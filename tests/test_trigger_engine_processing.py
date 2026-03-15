from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import cast

import pytest

from bot.pipeline import ProcessEventResult
from bot.settings import AppPaths
from bot.settings import load_settings
from bot.trigger_engine import TriggerEngine
from db.repositories import Database


def _write_project_files(root: Path) -> None:
    prompts_dir = root / "prompts"
    personas_dir = root / "personas"
    _ = prompts_dir.mkdir()
    _ = personas_dir.mkdir()

    _ = (prompts_dir / "planner.md").write_text("planner", encoding="utf-8")
    _ = (prompts_dir / "replyer.md").write_text("replyer", encoding="utf-8")
    _ = (prompts_dir / "style_rules.md").write_text("style", encoding="utf-8")
    _ = (prompts_dir / "safety_rules.md").write_text("safety", encoding="utf-8")
    _ = (prompts_dir / "memory_user_update.md").write_text("memory user", encoding="utf-8")
    _ = (prompts_dir / "memory_self_update.md").write_text("memory self", encoding="utf-8")
    _ = (personas_dir / "core.md").write_text("core persona", encoding="utf-8")


def _write_config(root: Path, *, daily_token_budget: int = 100, topic_token_budget: int = 50) -> None:
    config_dir = root / "config"
    data_dir = root / "data"
    log_dir = root / "logs"
    _ = config_dir.mkdir(exist_ok=True)
    _ = data_dir.mkdir(exist_ok=True)
    _ = log_dir.mkdir(exist_ok=True)

    _ = (config_dir / "credentials.toml").write_text("[forum]\nusername='u'\npassword='p'\n", encoding="utf-8")
    _ = (config_dir / "forum.toml").write_text(
        "base_url='https://forum.example.com'\nretry=3\nuser_agent='ua'\n",
        encoding="utf-8",
    )
    _ = (config_dir / "providers.toml").write_text(
        "[default]\nbase_url='https://provider.example.com/v1/chat/completions'\napi_key='k'\ntimeout_seconds=10\n",
        encoding="utf-8",
    )
    _ = (config_dir / "models.toml").write_text(
        "[planner]\nprovider='default'\nmodel='planner-v1'\n"
        + "[replyer]\nprovider='default'\nmodel='replyer-v1'\n"
        + "[memory]\nprovider='default'\nmodel='memory-v1'\n",
        encoding="utf-8",
    )
    _ = (config_dir / "thresholds.toml").write_text(
        "[triggers]\n"
        + "hourly_new_reply_min=1\n"
        + "hourly_hot_reply_min=2\n"
        + "burst_window_minutes=5\n"
        + "burst_reply_min=3\n\n"
        + "[context]\n"
        + "planner_max_posts=10\n"
        + "replyer_max_posts=5\n"
        + "summary_max_chars=1000\n\n"
        + "[budget]\n"
        + f"daily_token_budget={daily_token_budget}\n"
        + f"topic_token_budget={topic_token_budget}\n",
        encoding="utf-8",
    )
    _ = (config_dir / "scheduler.toml").write_text(
        "[polling]\nnotification_interval_seconds=5\nburst_scan_interval_seconds=60\nhourly_scan_interval_seconds=3600\nnightly_memory_hour=0\n",
        encoding="utf-8",
    )
    _ = (config_dir / "webui.toml").write_text(
        "host='127.0.0.1'\nport=5000\nenable_auth=false\nshow_aigc_logs=true\n",
        encoding="utf-8",
    )
    _ = (config_dir / "runtime.toml").write_text(
        "read_only=true\n"
        + "mark_notifications_read=false\n"
        + "shadow_mode=false\n"
        + "allow_send_reply=false\n"
        + "require_approval_before_send=true\n"
        + "panic_switch=false\n"
        + "topic_cooldown_minutes=0\n"
        + "muted_topic_ids=[]\n"
        + "muted_usernames=[]\n",
        encoding="utf-8",
    )
    _ = (config_dir / "personas.toml").write_text("enabled=['core']\n[priority]\ncore=10\n", encoding="utf-8")


class _StubPipeline:
    def __init__(self, *, fail_event_ids: set[int] | None = None) -> None:
        self.fail_event_ids = fail_event_ids or set()
        self.calls: list[int] = []

    async def process_event(
        self, forum_client: object, payload: dict[str, object], *, event_id: int | None = None
    ) -> ProcessEventResult | None:
        _ = forum_client
        _ = payload
        if event_id is None:
            raise AssertionError("event_id should be provided")
        self.calls.append(event_id)
        if event_id in self.fail_event_ids:
            raise RuntimeError(f"boom:{event_id}")
        return {
            "action": "reply",
            "pending_reply_id": None,
            "topic_id": 123,
            "topic_title": "topic",
            "post_count": 0,
            "decision": {},
            "draft": {},
            "memory_hits": {},
            "persona_modules": [],
            "planner_prompt_preview": "",
            "replyer_prompt_preview": "",
        }


class _StubForumClient:
    async def aclose(self) -> None:
        return None


def _make_engine(tmp_path: Path, *, daily_token_budget: int = 10000, topic_token_budget: int = 10000) -> TriggerEngine:
    _write_project_files(tmp_path)
    _write_config(tmp_path, daily_token_budget=daily_token_budget, topic_token_budget=topic_token_budget)
    database = Database(tmp_path / "data" / "suenmeow.sqlite3")
    database.initialize()
    return TriggerEngine(load_settings(AppPaths.from_root(tmp_path)), database)


@pytest.mark.anyio
async def test_process_pending_events_marks_over_budget_event_processed(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, daily_token_budget=1, topic_token_budget=1)
    _ = engine.database.record_trigger_event(
        cast(
            dict[str, object],
            {"topic_id": 123, "reason": "notification", "source": "notification_worker", "notification_id": 1},
        )
    )

    processed = await engine._process_pending_events()

    assert processed == []
    assert engine.database.list_unprocessed_events() == []
    recent = engine.database.list_recent_trigger_events(limit=1)
    assert recent[0]["processed_at"] is not None
    await engine.forum_client.aclose()


@pytest.mark.anyio
async def test_process_pending_events_continues_after_single_event_failure(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    first_added = engine.database.record_trigger_event(
        cast(
            dict[str, object],
            {"topic_id": 123, "reason": "notification", "source": "notification_worker", "notification_id": 1},
        )
    )
    second_added = engine.database.record_trigger_event(
        cast(
            dict[str, object],
            {"topic_id": 124, "reason": "notification", "source": "notification_worker", "notification_id": 2},
        )
    )
    assert first_added is True
    assert second_added is True

    pending_before = engine.database.list_unprocessed_events()
    failing_event_id = int(pending_before[0]["id"])
    succeeding_event_id = int(pending_before[1]["id"])
    cast(Any, engine).pipeline = _StubPipeline(fail_event_ids={failing_event_id})
    cast(Any, engine).forum_client = _StubForumClient()

    processed = await engine._process_pending_events()

    assert len(processed) == 1
    assert processed[0]["action"] == "reply"
    pending_after = engine.database.list_unprocessed_events()
    assert len(pending_after) == 1
    assert int(pending_after[0]["id"]) == failing_event_id

    recent = engine.database.list_recent_trigger_events(limit=2)
    processed_map = {int(item["id"]): item["processed_at"] for item in recent}
    assert processed_map[succeeding_event_id] is not None
    assert processed_map[failing_event_id] is None
    await engine.forum_client.aclose()
