from pathlib import Path

from fastapi import APIRouter
from fastapi.testclient import TestClient

from web.main import create_app


def _write_config(
    root: Path,
    *,
    mark_notifications_read: bool = False,
    shadow_mode: bool = False,
    panic_switch: bool = False,
    topic_cooldown_minutes: int = 0,
    blackout_start_hour: int | None = None,
    blackout_end_hour: int | None = None,
    muted_topic_ids: list[int] | None = None,
    muted_usernames: list[str] | None = None,
) -> None:
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
        "host='127.0.0.1'\nport=8000\nenable_auth=false\nshow_aigc_logs=true\n",
        encoding="utf-8",
    )
    runtime_toml = (
        "read_only=true\n"
        + f"mark_notifications_read={'true' if mark_notifications_read else 'false'}\n"
        + f"shadow_mode={'true' if shadow_mode else 'false'}\n"
        + "allow_send_reply=false\nrequire_approval_before_send=true\n"
        + f"panic_switch={'true' if panic_switch else 'false'}\n"
        + f"topic_cooldown_minutes={topic_cooldown_minutes}\n"
    )
    if blackout_start_hour is not None:
        runtime_toml += f"blackout_start_hour={blackout_start_hour}\n"
    if blackout_end_hour is not None:
        runtime_toml += f"blackout_end_hour={blackout_end_hour}\n"
    runtime_toml += f"muted_topic_ids={muted_topic_ids or []}\n"
    runtime_toml += f"muted_usernames={muted_usernames or []}\n"
    _ = (config_dir / "runtime.toml").write_text(runtime_toml, encoding="utf-8")
    _ = (config_dir / "personas.toml").write_text("enabled=['core']\n[priority]\ncore=10\n", encoding="utf-8")

    _ = (prompts_dir / "planner.md").write_text("old planner", encoding="utf-8")
    _ = (prompts_dir / "replyer.md").write_text("old replyer", encoding="utf-8")
    _ = (prompts_dir / "style_rules.md").write_text("old style", encoding="utf-8")
    _ = (prompts_dir / "safety_rules.md").write_text("old safety", encoding="utf-8")
    _ = (prompts_dir / "custom_rules.md").write_text("custom rule", encoding="utf-8")
    _ = (prompts_dir / "memory_user_update.md").write_text("memory user rule", encoding="utf-8")
    _ = (prompts_dir / "memory_self_update.md").write_text("memory self rule", encoding="utf-8")
    _ = (personas_dir / "core.md").write_text("old core", encoding="utf-8")
    _ = (personas_dir / "catgirl.md").write_text("catgirl persona", encoding="utf-8")


def _runtime_badges_section(html: str) -> str:
    marker = '<div id="runtime-status-badges"'
    start = html.index(marker)
    section = html[start:]
    return section.split("</div>", 1)[0]


def test_prompt_routes_support_read_and_update(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/prompts/planner.md")
        assert response.status_code == 200
        assert response.json() == {"file": "planner.md", "content": "old planner"}

        updated = client.put("/prompts/planner.md", json={"content": "new planner"})
        assert updated.status_code == 200
        assert updated.json() == {"file": "planner.md", "content": "new planner"}

        invalid = client.get("/prompts/planner.txt")
        assert invalid.status_code == 400

    assert (tmp_path / "prompts" / "planner.md").read_text(encoding="utf-8") == "new planner"


def test_prompt_routes_support_create_new_file(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        created = client.put("/prompts/new_prompt.md", json={"content": "brand new prompt"})
        assert created.status_code == 200
        assert created.json() == {"file": "new_prompt.md", "content": "brand new prompt"}

        listed = client.get("/prompts")
        assert listed.status_code == 200
        assert "new_prompt.md" in listed.json()["files"]

    assert (tmp_path / "prompts" / "new_prompt.md").read_text(encoding="utf-8") == "brand new prompt"


def test_persona_and_self_memory_routes_support_update(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        persona = client.get("/personas/core.md")
        assert persona.status_code == 200
        assert persona.json() == {"file": "core.md", "content": "old core"}

        updated_persona = client.put("/personas/core.md", json={"content": "new core"})
        assert updated_persona.status_code == 200
        assert updated_persona.json() == {"file": "core.md", "content": "new core"}

        updated_memory = client.put("/memory/self", json={"memory": "remember this"})
        assert updated_memory.status_code == 200
        assert updated_memory.json() == {"memory": "remember this"}

        fetched_memory = client.get("/memory/self")
        assert fetched_memory.status_code == 200
        assert fetched_memory.json() == {"memory": "remember this"}

        # Test user memory update
        user_memory_res = client.put("/memory/user/test_user", json={"memory": "user likes cats"})
        assert user_memory_res.status_code == 200
        assert user_memory_res.json() == {"username": "test_user", "memory": "user likes cats"}
        
        # Verify user memory lists in /memory endpoint
        all_memories = client.get("/memory")
        assert all_memories.status_code == 200
        data = all_memories.json()
        assert data["self_memory"] == "remember this"
        assert len(data["user_memories"]) > 0
        assert data["user_memories"][0]["username"] == "test_user"
        assert data["user_memories"][0]["memory_text"] == "user likes cats"

    assert (tmp_path / "personas" / "core.md").read_text(encoding="utf-8") == "new core"


def test_persona_routes_support_create_new_file(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        created = client.put("/personas/helper.md", json={"content": "helpful persona"})
        assert created.status_code == 200
        assert created.json() == {"file": "helper.md", "content": "helpful persona"}

        listed = client.get("/personas")
        assert listed.status_code == 200
        assert "helper.md" in listed.json()["files"]

    assert (tmp_path / "personas" / "helper.md").read_text(encoding="utf-8") == "helpful persona"


def test_openapi_uses_chinese_visible_copy_but_keeps_main_title(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "正常"}

        invalid_prompt = client.get("/prompts/planner.txt")
        assert invalid_prompt.status_code == 400
        assert invalid_prompt.json()["detail"] == "Markdown 文件名不合法"

        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        spec = openapi.json()
        assert spec["info"]["title"] == "SuenMeow Admin"
        assert spec["info"]["description"] == "SuenMeow 的管理与调试接口。"
        assert spec["paths"]["/prompts"]["get"]["summary"] == "查看提示词文件列表"
        assert spec["paths"]["/memory/self"]["put"]["summary"] == "更新自我记忆"
        assert spec["paths"]["/config/prompt-modules"]["put"]["summary"] == "更新提示词模块编排"


def test_config_prompt_modules_support_read_and_update(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert data["available_prompt_files"] == [
            "custom_rules.md",
            "memory_self_update.md",
            "memory_user_update.md",
            "planner.md",
            "replyer.md",
            "safety_rules.md",
            "style_rules.md",
        ]
        assert data["available_persona_files"] == ["catgirl.md", "core.md"]
        assert data["available_module_files"] == [
            "catgirl.md",
            "core.md",
            "custom_rules.md",
            "memory_self_update.md",
            "memory_user_update.md",
            "planner.md",
            "replyer.md",
            "safety_rules.md",
            "style_rules.md",
        ]
        assert data["prompt_modules"]["planner"] == [
            {"name": "planner.md", "enabled": True},
            {"name": "safety_rules.md", "enabled": True},
        ]
        assert data["prompt_modules"]["replyer"] == [
            {"name": "replyer.md", "enabled": True},
            {"name": "style_rules.md", "enabled": True},
            {"name": "safety_rules.md", "enabled": True},
        ]
        assert data["prompt_modules"]["memory"] == [
            {"name": "memory_user_update.md", "enabled": True},
            {"name": "memory_self_update.md", "enabled": True},
        ]
        assert data["runtime"] == {
            "read_only": True,
            "mark_notifications_read": False,
            "shadow_mode": False,
            "allow_send_reply": False,
            "require_approval_before_send": True,
            "panic_switch": False,
            "topic_cooldown_minutes": 0,
            "blackout_start_hour": None,
            "blackout_end_hour": None,
            "muted_topic_ids": [],
            "muted_usernames": [],
        }

        updated = client.put(
            "/config/prompt-modules",
            json={
                "planner": {
                    "modules": [
                        {"name": "custom_rules.md", "enabled": True},
                        {"name": "planner.md", "enabled": True},
                    ]
                },
                "replyer": {
                    "modules": [
                        {"name": "catgirl.md", "enabled": True},
                        {"name": "replyer.md", "enabled": True},
                        {"name": "style_rules.md", "enabled": False},
                        {"name": "safety_rules.md", "enabled": True},
                    ]
                },
                "memory": {
                    "modules": [
                        {"name": "core.md", "enabled": True},
                        {"name": "memory_self_update.md", "enabled": True},
                    ]
                },
            },
        )
        assert updated.status_code == 200
        updated_data = updated.json()
        assert updated_data["prompt_modules"]["planner"] == [
            {"name": "custom_rules.md", "enabled": True},
            {"name": "planner.md", "enabled": True},
        ]
        assert updated_data["prompt_modules"]["replyer"] == [
            {"name": "catgirl.md", "enabled": True},
            {"name": "replyer.md", "enabled": True},
            {"name": "style_rules.md", "enabled": False},
            {"name": "safety_rules.md", "enabled": True},
        ]
        assert updated_data["prompt_modules"]["memory"] == [
            {"name": "core.md", "enabled": True},
            {"name": "memory_self_update.md", "enabled": True},
        ]

    prompt_modules_file = tmp_path / "config" / "prompt_modules.toml"
    assert prompt_modules_file.read_text(encoding="utf-8") == (
        "[planner]\n"
        "[[planner.modules]]\n"
        "name = \"custom_rules.md\"\n"
        "enabled = true\n"
        "[[planner.modules]]\n"
        "name = \"planner.md\"\n"
        "enabled = true\n\n"
        "[replyer]\n"
        "[[replyer.modules]]\n"
        "name = \"catgirl.md\"\n"
        "enabled = true\n"
        "[[replyer.modules]]\n"
        "name = \"replyer.md\"\n"
        "enabled = true\n"
        "[[replyer.modules]]\n"
        "name = \"style_rules.md\"\n"
        "enabled = false\n"
        "[[replyer.modules]]\n"
        "name = \"safety_rules.md\"\n"
        "enabled = true\n\n"
        "[memory]\n"
        "[[memory.modules]]\n"
        "name = \"core.md\"\n"
        "enabled = true\n"
        "[[memory.modules]]\n"
        "name = \"memory_self_update.md\"\n"
        "enabled = true\n"
    )


def test_config_prompt_modules_rejects_empty_route(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/config/prompt-modules",
            json={
                "planner": {"modules": []},
                "replyer": {"modules": [{"name": "replyer.md", "enabled": True}]},
                "memory": {"modules": [{"name": "memory_user_update.md", "enabled": True}]},
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "提示词模块链不能为空"


def test_config_prompt_modules_rejects_all_disabled_route(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/config/prompt-modules",
            json={
                "planner": {"modules": [{"name": "planner.md", "enabled": False}]},
                "replyer": {"modules": [{"name": "replyer.md", "enabled": True}]},
                "memory": {"modules": [{"name": "memory_user_update.md", "enabled": True}]},
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "每条提示词链路至少需要启用一个模块"


def test_homepage_renders_real_chinese_admin_page(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    _ = (tmp_path / "logs" / "latest.log").write_text("line 1\nline 2\n", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    text = response.text
    assert "<title>SuenMeow Admin</title>" in text
    assert "中文管理页：可直接查看和编辑提示词、人格设定与自我记忆。" in text
    assert "提示词编辑" in text
    assert "提示词模块编排" in text
    assert "Memory 模块链" in text
    assert "人格编辑" in text
    assert "自我记忆" in text
    assert "用户记忆" in text
    assert "运行日志" in text
    assert "流水线追踪" in text
    assert "待审核回复" in text
    assert "运行状态 (Runtime Status)" in text
    assert "runtime-status-badges" in text
    assert "hydrateRuntimeStatus" in text
    assert "只读模式" in text
    assert "可发送" in text
    assert "需审批" in text
    assert "loadPipelineRuns()" in text
    assert "reply_pending_approval" in text
    assert "reply_sent" in text
    assert "决策依据" in text
    assert "建议回复" in text
    assert "Swagger 文档" in text
    assert "planner.md" in text
    assert "catgirl.md" in text
    assert "/config/prompt-modules" in text
    assert "core.md" in text
    assert "/memory/user/" in text
    assert "/logs/latest?lines=200" in text
    assert "/topics/runs" in text


def test_logs_latest_endpoint_returns_latest_file_content(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)
    log_dir = tmp_path / "logs"
    _ = (log_dir / "older.log").write_text("old\n", encoding="utf-8")
    latest = log_dir / "latest.log"
    _ = latest.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/logs/latest?lines=2")

    assert response.status_code == 200
    assert response.json() == {"file": "latest.log", "lines": ["beta", "gamma"]}


def test_create_app_maps_value_error_to_json_detail(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)
    router = APIRouter()

    @router.get("/raise-value-error")
    def raise_value_error() -> None:
        raise ValueError("loader exploded")

    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/raise-value-error")

    assert response.status_code == 400
    assert response.json() == {"detail": "loader exploded"}


def test_homepage_includes_error_detail_helper_script(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    text = response.text
    assert "async function readErrorDetail(res, fallback)" in text
    assert "await readErrorDetail(res, '保存失败 ✗')" in text


def test_homepage_includes_pipeline_run_trace_panel_script(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    text = response.text
    assert "pipeline-runs-container" in text
    assert "async function loadPipelineRuns()" in text
    assert "fetch('/topics/runs')" in text
    assert "reply_pending_approval" in text
    assert "reply_sent" in text
    assert "决策依据" in text
    assert "建议回复" in text
    assert "href=\"/topics/runs/${escapeHtml(run.id)}\"" in text
    assert "查看详情" in text


def test_homepage_includes_pending_approvals_panel_script(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    text = response.text
    assert "pending-approvals-container" in text
    assert "async function loadPendingApprovals()" in text
    assert "fetch('/topics/pending-replies')" in text
    assert "async function approvePendingReply" in text
    assert "fetch(`/topics/pending-replies/${id}/approve`" in text
    assert "待审核回复" in text


def test_homepage_server_renders_runtime_status_badges(tmp_path: Path) -> None:
    _write_config(tmp_path)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    text = response.text
    badges = _runtime_badges_section(text)
    assert "runtime-status-badges" in text
    assert "🔒 只读模式" in badges
    assert "🚫 禁发送" in badges
    assert "🛡️ 需审批" in badges
    assert "📭 保留未读通知" in badges
    assert "☀️ 非影子模式" in badges
    assert "🟢 Panic 开关关闭" in badges
    assert "🌤️ 无黑窗" in badges
    assert "⏱️ 无主题冷却" in badges
    assert "🔔 无静音主题" in badges
    assert "👥 无静音用户" in badges
    assert "📬 标记通知已读" not in badges
    assert "🫥 影子模式" not in badges
    assert "🛑 Panic 开关开启" not in badges
    assert "加载中..." not in text


def test_homepage_server_renders_mark_notifications_read_badge_when_enabled(tmp_path: Path) -> None:
    _write_config(tmp_path, mark_notifications_read=True)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")
        config_response = client.get("/config")

    assert response.status_code == 200
    assert config_response.status_code == 200
    text = response.text
    badges = _runtime_badges_section(text)
    assert "📬 标记通知已读" in badges
    assert "📭 保留未读通知" not in badges
    assert config_response.json()["runtime"]["mark_notifications_read"] is True


def test_homepage_server_renders_shadow_mode_badge_when_enabled(tmp_path: Path) -> None:
    _write_config(tmp_path, shadow_mode=True)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")
        config_response = client.get("/config")

    assert response.status_code == 200
    assert config_response.status_code == 200
    text = response.text
    badges = _runtime_badges_section(text)
    assert "🫥 影子模式" in badges
    assert "☀️ 非影子模式" not in badges
    assert config_response.json()["runtime"]["shadow_mode"] is True


def test_homepage_server_renders_extended_runtime_safety_badges_when_enabled(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        panic_switch=True,
        topic_cooldown_minutes=15,
        blackout_start_hour=22,
        blackout_end_hour=6,
        muted_topic_ids=[101, 202],
        muted_usernames=["alice", "bob"],
    )
    app = create_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/")
        config_response = client.get("/config")

    assert response.status_code == 200
    assert config_response.status_code == 200
    badges = _runtime_badges_section(response.text)
    runtime = config_response.json()["runtime"]
    assert "🛑 Panic 开关开启" in badges
    assert "🟢 Panic 开关关闭" not in badges
    assert "🌙 黑窗 22:00-06:00 UTC" in badges
    assert "⏱️ 主题冷却 15m" in badges
    assert "🔕 静音主题 2" in badges
    assert "🙈 静音用户 2" in badges
    assert runtime["panic_switch"] is True
    assert runtime["topic_cooldown_minutes"] == 15
    assert runtime["blackout_start_hour"] == 22
    assert runtime["blackout_end_hour"] == 6
    assert runtime["muted_topic_ids"] == [101, 202]
    assert runtime["muted_usernames"] == ["alice", "bob"]
