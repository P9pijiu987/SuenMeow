from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from bot.activity_worker import ActivityWorker
from bot.settings import AppPaths
from bot.settings import load_settings
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


def _write_config(root: Path, *, hot_reply_min: int = 10) -> None:
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
        + f"hourly_new_reply_min=1\n"
        + f"hourly_hot_reply_min={hot_reply_min}\n"
        + "burst_window_minutes=5\n"
        + "burst_reply_min=3\n\n"
        + "[context]\nplanner_max_posts=10\nreplyer_max_posts=5\nsummary_max_chars=1000\n\n"
        + "[budget]\ndaily_token_budget=100\ntopic_token_budget=50\n",
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
        "read_only=true\nmark_notifications_read=false\nshadow_mode=false\nallow_send_reply=false\nrequire_approval_before_send=true\npanic_switch=false\ntopic_cooldown_minutes=0\nmuted_topic_ids=[]\nmuted_usernames=[]\n",
        encoding="utf-8",
    )
    _ = (config_dir / "personas.toml").write_text("enabled=['core']\n[priority]\ncore=10\n", encoding="utf-8")


@dataclass
class _FakeForumClient:
    latest_topics_payload: list[dict[str, object]]
    topic_posts_payload: dict[int, list[dict[str, object]]]

    async def list_latest_topics(self) -> list[dict[str, object]]:
        return self.latest_topics_payload

    async def get_topic_selected_posts(
        self,
        topic_id: int,
        *,
        include_first_post: bool = True,
        recent_post_limit: int = 50,
    ) -> list[dict[str, object]]:
        _ = include_first_post
        _ = recent_post_limit
        return self.topic_posts_payload[topic_id]


def _make_worker(tmp_path: Path, forum_client: _FakeForumClient, *, hot_reply_min: int = 10) -> tuple[ActivityWorker, Database]:
    _write_project_files(tmp_path)
    _write_config(tmp_path, hot_reply_min=hot_reply_min)
    database = Database(tmp_path / "data" / "suenmeow.sqlite3")
    database.initialize()
    settings = load_settings(AppPaths.from_root(tmp_path))
    worker = ActivityWorker(cast(object, forum_client), database, settings.thresholds)
    return worker, database


def _posts_with_recent_replies(reply_count_1h: int) -> list[dict[str, object]]:
    now = datetime.now(timezone.utc)
    posts: list[dict[str, object]] = [
        {
            "post_number": 1,
            "created_at": (now - timedelta(hours=5)).isoformat(),
            "username": "alice",
            "raw_text": "first post",
        }
    ]
    for index in range(reply_count_1h):
        posts.append(
            {
                "post_number": index + 2,
                "created_at": (now - timedelta(minutes=30)).isoformat(),
                "username": f"user{index}",
                "raw_text": f"reply {index}",
            }
        )
    return posts


@pytest.mark.anyio
async def test_activity_worker_triggers_when_recent_replies_exceed_10(tmp_path: Path) -> None:
    forum_client = _FakeForumClient(
        latest_topics_payload=[{"id": 7, "highest_post_number": 12}],
        topic_posts_payload={7: _posts_with_recent_replies(11)},
    )
    worker, database = _make_worker(tmp_path, forum_client, hot_reply_min=10)

    events = await worker.scan()

    assert len(events) == 1
    assert events[0]["reason"] == "hot_topic"
    assert events[0]["reply_count_1h"] == 11
    assert database.list_unprocessed_events()[0]["payload"]["reply_count_1h"] == 11


@pytest.mark.anyio
async def test_activity_worker_does_not_trigger_when_recent_replies_equal_10(tmp_path: Path) -> None:
    forum_client = _FakeForumClient(
        latest_topics_payload=[{"id": 7, "highest_post_number": 11}],
        topic_posts_payload={7: _posts_with_recent_replies(10)},
    )
    worker, database = _make_worker(tmp_path, forum_client, hot_reply_min=10)

    events = await worker.scan()

    assert events == []
    assert database.list_unprocessed_events() == []
