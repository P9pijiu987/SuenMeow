from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from db.repositories import Database
from web.main import create_app


def _write_config(root: Path, *, read_only: bool, allow_send_reply: bool, panic_switch: bool = False) -> None:
    config_dir = root / "config"
    data_dir = root / "data"
    log_dir = root / "logs"
    prompts_dir = root / "prompts"
    _ = config_dir.mkdir()
    _ = data_dir.mkdir()
    _ = log_dir.mkdir()
    _ = prompts_dir.mkdir()

    _ = (config_dir / "credentials.toml").write_text("[forum]\nusername='u'\npassword='p'\n", encoding="utf-8")
    _ = (config_dir / "forum.toml").write_text(
        "base_url='https://forum.example.com'\nretry=3\nuser_agent='ua'\n",
        encoding="utf-8",
    )
    _ = (config_dir / "providers.toml").write_text(
        "[default]\nbase_url='https://x'\napi_key='k'\ntimeout_seconds=10\n",
        encoding="utf-8",
    )
    _ = (config_dir / "models.toml").write_text(
        "[planner]\nprovider='default'\nmodel='m'\n[replyer]\nprovider='default'\nmodel='m'\n[memory]\nprovider='default'\nmodel='m'\n",
        encoding="utf-8",
    )
    thresholds_toml = (
        "[triggers]\nhourly_new_reply_min=1\nhourly_hot_reply_min=2\nburst_window_minutes=5\nburst_reply_min=3\n\n"
        + "[context]\nplanner_max_posts=10\nreplyer_max_posts=5\nsummary_max_chars=1000\n\n"
        + "[budget]\ndaily_token_budget=100\ntopic_token_budget=50\n"
    )
    _ = (config_dir / "thresholds.toml").write_text(thresholds_toml, encoding="utf-8")
    _ = (config_dir / "scheduler.toml").write_text(
        "[polling]\nnotification_interval_seconds=5\nburst_scan_interval_seconds=60\nhourly_scan_interval_seconds=3600\nnightly_memory_hour=0\n",
        encoding="utf-8",
    )
    _ = (config_dir / "webui.toml").write_text(
        "host='127.0.0.1'\nport=8000\nenable_auth=false\nshow_aigc_logs=true\npublic_host='127.0.0.1'\npublic_port=8001\n",
        encoding="utf-8",
    )
    runtime_toml = (
        f"read_only={'true' if read_only else 'false'}\n"
        + "mark_notifications_read=false\n"
        + "shadow_mode=false\n"
        + f"allow_send_reply={'true' if allow_send_reply else 'false'}\n"
        + "require_approval_before_send=true\n"
        + f"panic_switch={'true' if panic_switch else 'false'}\n"
        + "topic_cooldown_minutes=0\n"
        + "muted_topic_ids=[]\n"
        + "muted_usernames=[]\n"
    )
    _ = (config_dir / "runtime.toml").write_text(runtime_toml, encoding="utf-8")
    _ = (config_dir / "personas.toml").write_text("enabled=['core']\n[priority]\ncore=10\n", encoding="utf-8")
    _ = (prompts_dir / "planner.md").write_text("planner", encoding="utf-8")
    _ = (prompts_dir / "replyer.md").write_text("replyer", encoding="utf-8")
    _ = (prompts_dir / "style_rules.md").write_text("style", encoding="utf-8")
    _ = (prompts_dir / "safety_rules.md").write_text("safety", encoding="utf-8")
    _ = (prompts_dir / "memory_user_update.md").write_text("memory user", encoding="utf-8")
    _ = (prompts_dir / "memory_self_update.md").write_text("memory self", encoding="utf-8")
    _ = (prompts_dir / "core.md").write_text("core", encoding="utf-8")


def _create_pending_reply(database: Database) -> int:
    return database.create_pending_reply(
        topic_id=7,
        topic_title="topic",
        trigger_reason="notification",
        target_post_number=3,
        draft_content="meow",
        decision={"should_reply": True, "reason": "test"},
    )


def test_approve_fails_when_runtime_read_only_true(tmp_path: Path) -> None:
    _write_config(tmp_path, read_only=True, allow_send_reply=True)
    app = create_app(tmp_path)
    database = cast(Database, app.state.database)
    pending_reply_id = _create_pending_reply(database)

    with TestClient(app) as client:
        response = client.post(f"/topics/pending-replies/{pending_reply_id}/approve")

    assert response.status_code == 409
    assert response.json()["detail"] == "manual approval send is disabled in read-only mode"
    pending = database.get_pending_reply(pending_reply_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.reply_post_id is None


def test_approve_fails_when_allow_send_reply_false(tmp_path: Path) -> None:
    _write_config(tmp_path, read_only=False, allow_send_reply=False)
    app = create_app(tmp_path)
    database = cast(Database, app.state.database)
    pending_reply_id = _create_pending_reply(database)

    with TestClient(app) as client:
        response = client.post(f"/topics/pending-replies/{pending_reply_id}/approve")

    assert response.status_code == 409
    assert response.json()["detail"] == "manual approval send is disabled because allow_send_reply=false"
    pending = database.get_pending_reply(pending_reply_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.reply_post_id is None


def test_approve_fails_when_panic_switch_true(tmp_path: Path) -> None:
    _write_config(tmp_path, read_only=False, allow_send_reply=True, panic_switch=True)
    app = create_app(tmp_path)
    database = cast(Database, app.state.database)
    pending_reply_id = _create_pending_reply(database)

    with TestClient(app) as client:
        response = client.post(f"/topics/pending-replies/{pending_reply_id}/approve")

    assert response.status_code == 409
    assert response.json()["detail"] == "manual approval send is disabled because panic_switch=true"
    pending = database.get_pending_reply(pending_reply_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.reply_post_id is None


def test_reject_succeeds_even_when_runtime_read_only_true(tmp_path: Path) -> None:
    _write_config(tmp_path, read_only=True, allow_send_reply=True)
    app = create_app(tmp_path)
    database = cast(Database, app.state.database)
    pending_reply_id = _create_pending_reply(database)

    with TestClient(app) as client:
        response = client.post(f"/topics/pending-replies/{pending_reply_id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    pending = database.get_pending_reply(pending_reply_id)
    assert pending is not None
    assert pending.status == "rejected"
