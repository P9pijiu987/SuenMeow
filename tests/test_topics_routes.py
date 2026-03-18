from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from bot.approval_service import ApprovalService
from db.repositories import Database
from web.main import create_app


def _write_config(root: Path) -> None:
    config_dir = root / "config"
    data_dir = root / "data"
    log_dir = root / "logs"
    prompts_dir = root / "prompts"
    personas_dir = root / "personas"
    _ = config_dir.mkdir()
    _ = data_dir.mkdir()
    _ = log_dir.mkdir()
    _ = prompts_dir.mkdir()
    _ = personas_dir.mkdir()

    _ = (config_dir / "credentials.toml").write_text("[forum]\nusername='u'\npassword='p'\n", encoding="utf-8")
    _ = (config_dir / "forum.toml").write_text("base_url='https://forum.example.com'\nretry=3\nuser_agent='ua'\n", encoding="utf-8")
    _ = (config_dir / "providers.toml").write_text("[default]\nbase_url='https://x'\napi_key='k'\ntimeout_seconds=10\n", encoding="utf-8")
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
    _ = (config_dir / "runtime.toml").write_text(
        "read_only=false\nmark_notifications_read=false\nallow_send_reply=true\nrequire_approval_before_send=true\n",
        encoding="utf-8",
    )
    _ = (config_dir / "personas.toml").write_text("enabled=['core']\n[priority]\ncore=10\n", encoding="utf-8")
    _ = (prompts_dir / "planner.md").write_text("planner", encoding="utf-8")
    _ = (prompts_dir / "replyer.md").write_text("replyer", encoding="utf-8")
    _ = (prompts_dir / "style_rules.md").write_text("style", encoding="utf-8")
    _ = (prompts_dir / "safety_rules.md").write_text("safety", encoding="utf-8")
    _ = (prompts_dir / "memory_user_update.md").write_text("memory user", encoding="utf-8")
    _ = (prompts_dir / "memory_self_update.md").write_text("memory self", encoding="utf-8")
    _ = (personas_dir / "core.md").write_text("core", encoding="utf-8")


def test_topics_pending_reply_routes(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)
    database: Database = app.state.database
    pending_reply_id = database.create_pending_reply(
        topic_id=7,
        topic_title="topic",
        trigger_reason="notification",
        target_post_number=3,
        draft_content="meow",
        decision={"should_reply": True, "reason": "test"},
    )

    calls: list[dict[str, object]] = []

    async def fake_send(topic_id: int, raw: str, reply_to_post_number: int | None) -> dict[str, object]:
        calls.append(
            {
                "topic_id": topic_id,
                "raw": raw,
                "reply_to_post_number": reply_to_post_number,
            }
        )
        return {"id": 88}

    app.state.approval_service = ApprovalService(app.state.database, app.state.settings, send_reply=fake_send)

    with TestClient(app) as client:
        listed = client.get("/topics/pending-replies")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == pending_reply_id

        approved = client.post(f"/topics/pending-replies/{pending_reply_id}/approve")
        assert approved.status_code == 200
        body = approved.json()
        assert body["status"] == "sent"
        assert body["reply_post_id"] == 88

    assert len(calls) == 1
    assert calls[0]["topic_id"] == 7
    assert database.has_replied_in_topic(7) is True


def test_topics_pending_reply_reject_route(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)
    database: Database = app.state.database
    pending_reply_id = database.create_pending_reply(
        topic_id=8,
        topic_title="topic reject",
        trigger_reason="notification",
        target_post_number=4,
        draft_content="mrrp",
        decision={"should_reply": True, "reason": "reject me"},
    )

    with TestClient(app) as client:
        rejected = client.post(f"/topics/pending-replies/{pending_reply_id}/reject")
        assert rejected.status_code == 200
        body = rejected.json()
        assert body["status"] == "rejected"

    pending = database.get_pending_reply(pending_reply_id)
    assert pending is not None
    assert pending.status == "rejected"


def test_config_route_exposes_approval_flag(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/config")

    assert response.status_code == 200
    assert response.json()["runtime"]["require_approval_before_send"] is True


def test_pipeline_run_detail_route_returns_item(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)
    database = cast(Database, app.state.database)
    database.record_pipeline_run(
        event_id=None,
        topic_id=11,
        topic_title="detail topic",
        trigger_reason="manual_debug",
        action="reply_sent",
        decision={"should_reply": True, "reason": "looks good"},
        draft_content="hello world",
    )
    run_id = int(database.list_recent_pipeline_runs(limit=1)[0]["id"])

    with TestClient(app) as client:
        response = client.get(f"/topics/runs/{run_id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": run_id,
        "event_id": None,
        "topic_id": 11,
        "topic_title": "detail topic",
        "trigger_reason": "manual_debug",
        "action": "reply_sent",
        "decision": {"should_reply": True, "reason": "looks good"},
        "draft_content": "hello world",
        "created_at": response.json()["created_at"],
    }


def test_pipeline_run_detail_route_returns_404_when_missing(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/topics/runs/99999")

    assert response.status_code == 404
    assert response.json()["detail"] == "pipeline run 99999 not found"


def test_topic_debug_route_returns_backend_payload(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    class FakeTriggerEngine:
        def __init__(self, settings: object, database: object) -> None:
            _ = settings
            _ = database

        async def debug_topics(self, topic_ids: list[int] | None = None, count: int = 2) -> list[dict[str, object]]:
            _ = count
            return [
                {
                    "topic_id": int((topic_ids or [0])[0]),
                    "topic_title": "debug topic",
                    "highest_post_number": 9,
                    "post_count": 3,
                    "decision": {"should_reply": True, "reason": "ok"},
                    "draft": {"content": "meow"},
                    "debug_prompts": {
                        "planner": {"system": "planner-s", "user": "planner-u"},
                        "replyer": {"system": "replyer-s", "user": "replyer-u"},
                        "memory": {"system": "memory-s", "user": "memory-u"},
                    },
                    "model_routes": {"planner": None, "replyer": None, "memory": None},
                    "persona_modules": ["core"],
                    "memory_hits": {},
                }
            ]

    monkeypatch.setattr("web.routes.topics.TriggerEngine", FakeTriggerEngine)

    with TestClient(app) as client:
        response = client.get("/topics/123/debug")

    assert response.status_code == 200
    payload = response.json()
    assert payload["topic_id"] == 123
    assert payload["topic_title"] == "debug topic"
    assert payload["debug_prompts"]["planner"]["system"] == "planner-s"
    assert payload["decision"]["should_reply"] is True


def test_topic_debug_route_returns_502_when_backend_debug_fails(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    class FakeTriggerEngine:
        def __init__(self, settings: object, database: object) -> None:
            _ = settings
            _ = database

        async def debug_topics(self, topic_ids: list[int] | None = None, count: int = 2) -> list[dict[str, object]]:
            _ = topic_ids
            _ = count
            raise RuntimeError("login failed")

    monkeypatch.setattr("web.routes.topics.TriggerEngine", FakeTriggerEngine)

    with TestClient(app) as client:
        response = client.get("/topics/123/debug")

    assert response.status_code == 502
    assert response.json()["detail"] == "topic debug failed: login failed"
