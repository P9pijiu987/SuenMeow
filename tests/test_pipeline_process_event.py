import json
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import override

import pytest

from bot.ban_service import BanService
from bot.context_builder import ContextBuilder
from bot.forum_client import ForumClient
from bot.llm_client import LlmResponse
from bot.llm_client import LlmClient
from bot.memory_service import MemoryService
from bot.persona_loader import PersonaLoader
from bot.pipeline import Pipeline
from bot.planner import PlannerDecision
from bot.prompt_loader import PromptLoader
from bot.replyer import Replyer
from bot.settings import CredentialsConfig, ForumConfig
from db.repositories import Database


class FakePlanner:
    def __init__(self, decision: PlannerDecision) -> None:
        self._decision: PlannerDecision = decision

    async def decide(
        self,
        planner_input: object,
        context: str,
        *,
        llm_client: object | None = None,
        system_prompt: str = "",
        user_prompt: str | None = None,
        route_name: str = "planner",
    ) -> PlannerDecision:
        _ = planner_input
        _ = context
        _ = llm_client
        _ = system_prompt
        _ = user_prompt
        _ = route_name
        return self._decision


class FakeForumClient(ForumClient):
    def __init__(self, *, read_only: bool = False) -> None:
        super().__init__(
            ForumConfig(base_url="https://forum.example.com", retry=1, user_agent="ua", default_headers={}, reactions={}),
            CredentialsConfig(username="u", password="p"),
            read_only=read_only,
        )
        self.reply_calls: list[dict[str, object]] = []

    @override
    async def get_topic(self, topic_id: int) -> dict[str, object]:
        return {"title": "topic title", "highest_post_number": 5}

    @override
    async def get_topic_selected_posts(
        self,
        topic_id: int,
        *,
        include_first_post: bool = True,
        recent_post_limit: int = 50,
    ) -> list[dict[str, object]]:
        return [
            {"post_number": 1, "username": "alice", "reply_to_post_number": 0, "raw_text": "first post"},
            {"post_number": 5, "username": "bob", "reply_to_post_number": 1, "raw_text": "latest reply"},
        ]

    @override
    async def reply(self, topic_id: int, raw: str, reply_to_post_number: int | None = None) -> dict[str, object]:
        self.reply_calls.append(
            {
                "topic_id": topic_id,
                "raw": raw,
                "reply_to_post_number": reply_to_post_number,
            }
        )
        return {"id": 9001}


class FakeLlmClient(LlmClient):
    def __init__(self, memory_payload: dict[str, object] | None = None) -> None:
        super().__init__({}, {})
        self.memory_payload: dict[str, object] = memory_payload or {"user_updates": [], "self_update": None}
        self.calls: list[str] = []

    @override
    def describe_route(self, route_name: str) -> dict[str, str]:
        return {"route": route_name, "provider": "fake", "model": f"fake-{route_name}"}

    @override
    def is_route_available(self, route_name: str) -> bool:
        return route_name == "memory"

    @override
    async def chat(self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> LlmResponse | None:
        _ = system_prompt
        _ = user_prompt
        _ = temperature
        self.calls.append(route_name)
        if route_name != "memory":
            return None
        return LlmResponse(content=json.dumps(self.memory_payload, ensure_ascii=False), model="fake-memory", provider="fake")

    @staticmethod
    @override
    def parse_json_object(content: str) -> dict[str, object] | None:
        parsed: object = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        normalized: dict[str, object] = {}
        for key, value in parsed.items():
            if not isinstance(key, str):
                return None
            normalized[key] = value
        return normalized


def _make_pipeline(
    tmp_path: Path,
    *,
    allow_send_reply: bool,
    decision: PlannerDecision,
    require_approval_before_send: bool = False,
    shadow_mode: bool = False,
    panic_switch: bool = False,
    topic_cooldown_minutes: int = 0,
    blackout_start_hour: int | None = None,
    blackout_end_hour: int | None = None,
    muted_topic_ids: list[int] | None = None,
    muted_usernames: list[str] | None = None,
    llm_client: LlmClient | None = None,
) -> Pipeline:
    prompt_dir = tmp_path / "prompts"
    persona_dir = tmp_path / "personas"
    _ = prompt_dir.mkdir()
    _ = persona_dir.mkdir()
    _ = (prompt_dir / "planner.md").write_text("# Planner\nplanner system", encoding="utf-8")
    _ = (prompt_dir / "replyer.md").write_text("# Replyer\nreplyer system", encoding="utf-8")
    _ = (prompt_dir / "style_rules.md").write_text("# Style\nstyle", encoding="utf-8")
    _ = (prompt_dir / "safety_rules.md").write_text("# Safety\nsafety", encoding="utf-8")
    _ = (prompt_dir / "memory_user_update.md").write_text("# Memory User\nmemory user", encoding="utf-8")
    _ = (prompt_dir / "memory_self_update.md").write_text("# Memory Self\nmemory self", encoding="utf-8")
    _ = (persona_dir / "core.md").write_text("# Core\ncore persona", encoding="utf-8")

    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    return Pipeline(
        context_builder=ContextBuilder(planner_max_posts=20, replyer_max_posts=10),
        planner=FakePlanner(decision),
        replyer=Replyer(),
        persona_loader=PersonaLoader(persona_dir),
        prompt_loader=PromptLoader(prompt_dir),
        llm_client=llm_client,
        ban_service=BanService("SuenMeow"),
        database=database,
        memory_service=MemoryService(database),
        enabled_personas=["core"],
        allow_send_reply=allow_send_reply,
        require_approval_before_send=require_approval_before_send,
        shadow_mode=shadow_mode,
        panic_switch=panic_switch,
        topic_cooldown_minutes=topic_cooldown_minutes,
        blackout_start_hour=blackout_start_hour,
        blackout_end_hour=blackout_end_hour,
        muted_topic_ids=muted_topic_ids,
        muted_usernames=muted_usernames,
    )


@pytest.mark.anyio
async def test_process_event_keeps_draft_only_when_send_disabled(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=False, decision=decision)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply"
    assert forum_client.reply_calls == []
    assert pipeline.database.has_replied_in_topic(123) is False
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "reply"


@pytest.mark.anyio
async def test_process_event_sends_and_records_reply_when_enabled(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert len(forum_client.reply_calls) == 1
    assert forum_client.reply_calls[0]["reply_to_post_number"] == 5
    assert pipeline.database.has_replied_in_topic(123) is True
    state = pipeline.database.get_topic_state(123)
    assert state is not None
    assert state.highest_replied_post_number == 5
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "reply_sent"


@pytest.mark.anyio
async def test_dry_run_includes_memory_context_and_memory_prompt_when_requested(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=False, decision=decision)
    pipeline.memory_service.set_user_memory("bob", "prefers short replies", 0.8)
    pipeline.memory_service.set_self_memory("remember to stay concise")

    result = await pipeline.dry_run(
        123,
        [
            {"post_number": 1, "username": "alice", "reply_to_post_number": 0, "raw_text": "first post"},
            {"post_number": 5, "username": "bob", "reply_to_post_number": 1, "raw_text": "latest reply"},
        ],
        "notification",
    )

    assert "Memory context:" in result["debug_prompts"]["replyer"]["user"]
    assert "remember to stay concise" in result["debug_prompts"]["replyer"]["user"]
    assert "prefers short replies" in result["debug_prompts"]["replyer"]["user"]
    assert '"user_updates": [{' in result["debug_prompts"]["memory"]["user"]
    assert "Draft reply:" in result["debug_prompts"]["memory"]["user"]


@pytest.mark.anyio
async def test_process_event_executes_memory_chain_and_persists_updates_after_send(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "prefers short replies", "confidence": 0.9},
            ],
            "self_update": {"memory": "keep replies concise", "confidence": 0.7},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert llm_client.calls == ["replyer", "memory"]
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "prefers short replies"
    assert pipeline.memory_service.get_self_memory() == "keep replies concise"


@pytest.mark.anyio
async def test_process_event_skips_low_confidence_memory_updates(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "new durable memory", "confidence": 0.1},
            ],
            "self_update": {"memory": "new self memory", "confidence": 0.1},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    pipeline.memory_service.set_user_memory("bob", "existing user memory", 0.8)
    pipeline.memory_service.set_self_memory("existing self memory")
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "existing user memory"
    assert pipeline.memory_service.get_self_memory() == "existing self memory"


@pytest.mark.anyio
async def test_process_event_skips_noop_memory_updates_after_normalization(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "  prefers   short   replies  ", "confidence": 0.9},
            ],
            "self_update": {"memory": "  keep   replies concise  ", "confidence": 0.9},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    pipeline.memory_service.set_user_memory("bob", "prefers short replies", 0.4)
    pipeline.memory_service.set_self_memory("keep replies concise")
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    user_memories = pipeline.memory_service.list_user_memories()
    assert len(user_memories) == 1
    assert user_memories[0]["memory_text"] == "prefers short replies"
    assert user_memories[0]["confidence"] == 0.4
    assert pipeline.memory_service.get_self_memory() == "keep replies concise"


@pytest.mark.anyio
async def test_process_event_dedupes_same_payload_user_updates_with_last_valid_winning(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "first update", "confidence": 0.8},
                {"username": "bob", "memory": "second update", "confidence": 0.9},
            ],
            "self_update": None,
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "second update"
    user_memories = pipeline.memory_service.list_user_memories()
    assert len(user_memories) == 1
    assert user_memories[0]["username"] == "bob"
    assert user_memories[0]["memory_text"] == "second update"
    assert user_memories[0]["confidence"] == 0.9


@pytest.mark.anyio
async def test_process_event_low_confidence_duplicate_does_not_override_valid_update(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "valid update", "confidence": 0.9},
                {"username": "bob", "memory": "low confidence override", "confidence": 0.1},
            ],
            "self_update": None,
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "valid update"
    user_memories = pipeline.memory_service.list_user_memories()
    assert len(user_memories) == 1
    assert user_memories[0]["username"] == "bob"
    assert user_memories[0]["memory_text"] == "valid update"
    assert user_memories[0]["confidence"] == 0.9


@pytest.mark.anyio
async def test_process_event_keeps_valid_updates_when_payload_has_malformed_entries(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                "not-an-object",
                {"username": "", "memory": "bad username", "confidence": 0.9},
                {"username": "bob", "memory": "valid memory", "confidence": 0.9},
                {"username": "bob", "memory": "   ", "confidence": 0.9},
            ],
            "self_update": ["bad-shape"],
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "valid memory"
    assert pipeline.memory_service.get_self_memory() == ""
    user_memories = pipeline.memory_service.list_user_memories()
    assert len(user_memories) == 1
    assert user_memories[0]["username"] == "bob"
    assert user_memories[0]["memory_text"] == "valid memory"


@pytest.mark.anyio
async def test_process_event_treats_non_finite_confidence_as_invalid_and_keeps_valid_updates(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "stable memory", "confidence": "0.9"},
                {"username": "bob", "memory": "nan override", "confidence": "NaN"},
            ],
            "self_update": {"memory": "bad self", "confidence": "inf"},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "stable memory"
    assert pipeline.memory_service.get_self_memory() == ""
    user_memories = pipeline.memory_service.list_user_memories()
    assert len(user_memories) == 1
    assert user_memories[0]["username"] == "bob"
    assert user_memories[0]["memory_text"] == "stable memory"
    assert user_memories[0]["confidence"] == 0.9


@pytest.mark.anyio
async def test_process_event_self_memory_uses_stricter_confidence_threshold_than_user_memory(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "persisted user memory", "confidence": 0.4},
            ],
            "self_update": {"memory": "should not persist self memory", "confidence": 0.5},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "persisted user memory"
    assert pipeline.memory_service.get_self_memory() == ""


@pytest.mark.anyio
async def test_process_event_persists_self_memory_when_confidence_meets_stricter_threshold(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="update_memory",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "bob", "memory": "user memory", "confidence": 0.4},
            ],
            "self_update": {"memory": "persisted self memory", "confidence": 0.6},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, llm_client=llm_client)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_sent"
    assert pipeline.memory_service.get_user_memory(["bob"])["bob"] == "user memory"
    assert pipeline.memory_service.get_self_memory() == "persisted self memory"


@pytest.mark.anyio
async def test_process_event_does_not_send_when_forum_client_is_read_only(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision)
    forum_client = FakeForumClient(read_only=True)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply"
    assert forum_client.reply_calls == []
    assert pipeline.database.has_replied_in_topic(123) is False


@pytest.mark.anyio
async def test_process_event_skips_without_sending_when_planner_declines(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=False,
        priority="skip",
        target_username=None,
        target_post_number=None,
        reason="not worth replying",
        style_notes="none",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "skip"
    assert forum_client.reply_calls == []
    assert pipeline.database.has_replied_in_topic(123) is False


@pytest.mark.anyio
async def test_process_event_queues_pending_reply_when_approval_required(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(
        tmp_path,
        allow_send_reply=True,
        decision=decision,
        require_approval_before_send=True,
    )
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "reply_pending_approval"
    assert result["pending_reply_id"] is not None
    assert forum_client.reply_calls == []
    pending = pipeline.database.list_pending_replies()
    assert len(pending) == 1
    assert pending[0]["topic_id"] == 123
    assert pending[0]["target_post_number"] == 5
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "reply_pending_approval"


@pytest.mark.anyio
async def test_process_event_skips_when_topic_already_has_pending_reply(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(
        tmp_path,
        allow_send_reply=True,
        decision=decision,
        require_approval_before_send=True,
    )
    _ = pipeline.database.create_pending_reply(
        topic_id=123,
        topic_title="topic title",
        trigger_reason="notification",
        target_post_number=5,
        draft_content="existing draft",
        decision={"should_reply": True, "reason": "existing"},
    )
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "pending_approval_skip"
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "pending_approval_skip"
    assert runs[0]["decision"]["reason"] == "topic already has pending approval"


@pytest.mark.anyio
async def test_process_event_marks_ban_as_structured_short_circuit(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(
        tmp_path,
        allow_send_reply=True,
        decision=decision,
        require_approval_before_send=True,
    )

    class _BanForumClient(FakeForumClient):
        @override
        async def get_topic_selected_posts(
            self,
            topic_id: int,
            *,
            include_first_post: bool = True,
            recent_post_limit: int = 50,
        ) -> list[dict[str, object]]:
            _ = topic_id
            _ = include_first_post
            _ = recent_post_limit
            return [
                {"post_number": 1, "username": "alice", "reply_to_post_number": 0, "raw_text": "first post"},
                {
                    "post_number": 2,
                    "username": "bob",
                    "reply_to_post_number": 1,
                    "raw_text": "/ban @SuenMeow",
                },
            ]

    forum_client = _BanForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "banned"
    assert result["decision"]["should_reply"] is False
    assert result["decision"]["reason"] == "ban command detected"
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "banned"


@pytest.mark.anyio
async def test_process_event_short_circuits_when_topic_already_banned(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision)
    pipeline.database.add_topic_ban(123, "manual ban")
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "banned"
    assert result["decision"]["reason"] == "topic is banned"
    assert forum_client.reply_calls == []
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "banned"


@pytest.mark.anyio
async def test_process_event_memory_command_routes_to_memory_llm_and_persists(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=False,
        priority="skip",
        target_username=None,
        target_post_number=None,
        reason="no reply",
        style_notes="none",
        memory_action="none",
    )
    llm_client = FakeLlmClient(
        {
            "user_updates": [
                {"username": "alice", "memory": "likes fish", "confidence": 0.9},
            ],
            "self_update": {"memory": "be concise", "confidence": 0.8},
        }
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=False, decision=decision, llm_client=llm_client)

    class _MemoryCommandForumClient(FakeForumClient):
        @override
        async def get_topic_selected_posts(
            self,
            topic_id: int,
            *,
            include_first_post: bool = True,
            recent_post_limit: int = 50,
        ) -> list[dict[str, object]]:
            _ = topic_id
            _ = include_first_post
            _ = recent_post_limit
            return [
                {
                    "post_number": 1,
                    "username": "alice",
                    "reply_to_post_number": 0,
                    "raw_text": "/memory+记住我喜欢简洁回复",
                },
            ]

    forum_client = _MemoryCommandForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "memory_command"
    assert result["decision"]["reason"] == "memory command processed"
    assert llm_client.calls == ["memory"]
    assert pipeline.memory_service.get_user_memory(["alice"])["alice"] == "likes fish"
    assert pipeline.memory_service.get_self_memory() == "be concise"
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "memory_command"


@pytest.mark.anyio
async def test_process_event_uses_shadow_reply_without_sending_or_queueing(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(
        tmp_path,
        allow_send_reply=True,
        decision=decision,
        require_approval_before_send=True,
        shadow_mode=True,
    )
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "shadow_reply"
    assert result["pending_reply_id"] is None
    assert forum_client.reply_calls == []
    assert pipeline.database.list_pending_replies() == []
    assert pipeline.database.has_replied_in_topic(123) is False
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "shadow_reply"


@pytest.mark.anyio
async def test_process_event_short_circuits_when_panic_switch_enabled(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, panic_switch=True)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "panic_skip"
    assert result["decision"]["should_reply"] is False
    assert result["decision"]["reason"] == "panic switch enabled"
    assert forum_client.reply_calls == []
    assert pipeline.database.list_pending_replies() == []
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "panic_skip"


@pytest.mark.anyio
async def test_process_event_short_circuits_during_blackout_window(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(
        tmp_path,
        allow_send_reply=True,
        decision=decision,
        blackout_start_hour=22,
        blackout_end_hour=6,
    )
    pipeline._utcnow = lambda: datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)  # type: ignore[method-assign]
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "blackout_skip"
    assert result["decision"]["reason"] == "blackout window active"
    assert forum_client.reply_calls == []
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "blackout_skip"


@pytest.mark.anyio
async def test_process_event_short_circuits_for_muted_topic(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, muted_topic_ids=[123])
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "muted_topic"
    assert result["decision"]["reason"] == "topic is muted"
    assert forum_client.reply_calls == []
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "muted_topic"


@pytest.mark.anyio
async def test_process_event_short_circuits_for_topic_cooldown(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, topic_cooldown_minutes=30)
    pipeline.database.record_reply(topic_id=123, content="old reply", reply_post_id=999, reply_to_post_number=5)
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "cooldown_skip"
    assert result["decision"]["reason"] == "topic cooldown active"
    assert forum_client.reply_calls == []
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "cooldown_skip"


@pytest.mark.anyio
async def test_process_event_short_circuits_for_muted_target_user(tmp_path: Path) -> None:
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="Bob",
        target_post_number=5,
        reason="reply due to notification",
        style_notes="concise",
        memory_action="none",
    )
    pipeline = _make_pipeline(tmp_path, allow_send_reply=True, decision=decision, muted_usernames=[" bob "])
    forum_client = FakeForumClient(read_only=False)

    result = await pipeline.process_event(forum_client, {"topic_id": 123, "reason": "notification"}, event_id=7)

    assert result is not None
    assert result["action"] == "muted_user"
    assert result["pending_reply_id"] is None
    assert forum_client.reply_calls == []
    assert pipeline.database.list_pending_replies() == []
    runs = pipeline.database.list_recent_pipeline_runs()
    assert runs[0]["action"] == "muted_user"
