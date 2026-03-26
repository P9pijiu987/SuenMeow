import pytest

from bot.llm_client import LlmClient
from bot.llm_client import LlmProviderHttpError
from bot.llm_client import LlmResponse
from bot.llm_client import LlmResponseFormatError
from bot.llm_client import LlmRouteUnavailableError
from bot.planner import Planner, PlannerInput
from bot.planner import PlannerDecision
from bot.replyer import Replyer


class FakeLlmClient:
    class _Resp:
        def __init__(self, content: str) -> None:
            self.content = content

    async def chat(self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7):
        return self._Resp(
            '{"should_reply": false, "priority": "skip", "target_username": null, '
            '"target_post_number": null, "reason": "content is not worth a reply", '
            '"style_notes": "none", "memory_action": "none"}'
        )

    @staticmethod
    def parse_json_object(content: str):
        import json

        return json.loads(content)


@pytest.mark.anyio
async def test_planner_raises_when_llm_is_unavailable() -> None:
    planner = Planner()
    with pytest.raises(LlmRouteUnavailableError):
        _ = await planner.decide(
            PlannerInput(
                trigger_reason="notification",
                topic_id=1,
                posts=[{"username": "alice", "post_number": 3, "raw_text": "hello world"}],
                user_memories={"alice": "likes concise replies"},
                self_memory="stay concise",
            ),
            "hello world context",
            llm_client=None,
        )


@pytest.mark.anyio
async def test_planner_uses_model_reason_and_should_reply_flag() -> None:
    planner = Planner()
    decision = await planner.decide(
        PlannerInput(
            trigger_reason="notification",
            topic_id=1,
            posts=[{"username": "alice", "post_number": 3, "raw_text": "hello world"}],
            user_memories={},
            self_memory="",
        ),
        "hello world context",
        llm_client=FakeLlmClient(),
        system_prompt="planner",
        user_prompt="ctx",
    )
    assert decision.should_reply is False
    assert decision.reason == "content is not worth a reply"


@pytest.mark.anyio
async def test_replyer_raises_when_llm_is_unavailable() -> None:
    replyer = Replyer()
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="alice",
        target_post_number=3,
        reason="reply",
        style_notes="concise",
        memory_action="none",
    )
    with pytest.raises(LlmRouteUnavailableError):
        _ = await replyer.generate(
            decision=decision,
            context="ctx",
            persona_text="# Core Persona\n\nRole: Suen (admin)",
            llm_client=None,
        )


class QuotaErrorLlmClient(LlmClient):
    def __init__(self) -> None:
        super().__init__({}, {})

    async def chat(
        self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7
    ) -> LlmResponse:
        _ = route_name
        _ = system_prompt
        _ = user_prompt
        _ = temperature
        raise LlmProviderHttpError("simulated provider failure")


class EmptyReplyLlmClient(LlmClient):
    def __init__(self) -> None:
        super().__init__({}, {})

    async def chat(
        self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7
    ) -> LlmResponse:
        _ = route_name
        _ = system_prompt
        _ = user_prompt
        _ = temperature
        return LlmResponse(content="   ", model="fake-replyer", provider="fake")


@pytest.mark.anyio
async def test_replyer_propagates_provider_failure() -> None:
    replyer = Replyer()
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="alice",
        target_post_number=3,
        reason="reply",
        style_notes="concise",
        memory_action="none",
    )
    with pytest.raises(LlmProviderHttpError):
        _ = await replyer.generate(
            decision=decision,
            context="ctx",
            persona_text="# Core Persona\n\nRole: Suen (admin)",
            llm_client=QuotaErrorLlmClient(),
        )


@pytest.mark.anyio
async def test_replyer_raises_on_empty_llm_content() -> None:
    replyer = Replyer()
    decision = PlannerDecision(
        should_reply=True,
        priority="normal",
        target_username="alice",
        target_post_number=3,
        reason="reply",
        style_notes="concise",
        memory_action="none",
    )
    with pytest.raises(LlmResponseFormatError):
        _ = await replyer.generate(
            decision=decision,
            context="ctx",
            persona_text="# Core Persona\n\nRole: Suen (admin)",
            llm_client=EmptyReplyLlmClient(),
        )


def test_replyer_sanitize_reply_text_filters_internal_prefixes() -> None:
    content = "Role: Suen\n\n@alice hi\nPlanner: hidden\nfinal"
    assert Replyer.sanitize_reply_text(content) == "@alice hi\nfinal"
