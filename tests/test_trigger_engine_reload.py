from pathlib import Path

import pytest

from bot.settings import AppPaths
from bot.settings import load_settings
from bot.trigger_engine import TriggerEngine
from db.repositories import Database


def _write_project_files(root: Path) -> None:
    prompts_dir = root / "prompts"
    _ = prompts_dir.mkdir()

    _ = (prompts_dir / "planner.md").write_text("planner", encoding="utf-8")
    _ = (prompts_dir / "replyer.md").write_text("replyer", encoding="utf-8")
    _ = (prompts_dir / "style_rules.md").write_text("style", encoding="utf-8")
    _ = (prompts_dir / "safety_rules.md").write_text("safety", encoding="utf-8")
    _ = (prompts_dir / "custom_rules.md").write_text("custom", encoding="utf-8")
    _ = (prompts_dir / "memory_user_update.md").write_text("memory user", encoding="utf-8")
    _ = (prompts_dir / "memory_self_update.md").write_text("memory self", encoding="utf-8")
    _ = (prompts_dir / "core.md").write_text("core persona", encoding="utf-8")
    _ = (prompts_dir / "catgirl.md").write_text("catgirl persona", encoding="utf-8")


def _write_config(
    root: Path,
    *,
    forum_base_url: str = "https://forum.example.com",
    planner_model: str = "planner-v1",
    mark_notifications_read: bool = False,
    shadow_mode: bool = False,
    panic_switch: bool = False,
    allow_send_reply: bool = False,
    require_approval_before_send: bool = True,
    topic_cooldown_minutes: int = 0,
    blackout_start_hour: int | None = None,
    blackout_end_hour: int | None = None,
    muted_topic_ids: list[int] | None = None,
    muted_usernames: list[str] | None = None,
    read_only: bool = True,
    planner_max_posts: int = 10,
    replyer_max_posts: int = 5,
    burst_window_minutes: int = 5,
    daily_token_budget: int = 100,
    topic_token_budget: int = 50,
    enabled_personas: list[str] | None = None,
    planner_modules: list[tuple[str, bool]] | None = None,
) -> None:
    config_dir = root / "config"
    data_dir = root / "data"
    log_dir = root / "logs"
    _ = config_dir.mkdir(exist_ok=True)
    _ = data_dir.mkdir(exist_ok=True)
    _ = log_dir.mkdir(exist_ok=True)

    _ = (config_dir / "credentials.toml").write_text("[forum]\nusername='u'\npassword='p'\n", encoding="utf-8")
    _ = (config_dir / "forum.toml").write_text(
        f"base_url='{forum_base_url}'\nretry=3\nuser_agent='ua'\n",
        encoding="utf-8",
    )
    _ = (config_dir / "providers.toml").write_text(
        "[default]\nbase_url='https://provider.example.com/v1/chat/completions'\napi_key='k'\ntimeout_seconds=10\n",
        encoding="utf-8",
    )
    models_toml = (
        f"[planner]\nprovider='default'\nmodel='{planner_model}'\n"
        + "[replyer]\nprovider='default'\nmodel='replyer-v1'\n"
        + "[memory]\nprovider='default'\nmodel='memory-v1'\n"
    )
    _ = (config_dir / "models.toml").write_text(
        models_toml,
        encoding="utf-8",
    )
    thresholds_toml = (
        "[triggers]\n"
        + "hourly_new_reply_min=1\n"
        + "hourly_hot_reply_min=2\n"
        + f"burst_window_minutes={burst_window_minutes}\n"
        + "burst_reply_min=3\n\n"
        + "[context]\n"
        + f"planner_max_posts={planner_max_posts}\n"
        + f"replyer_max_posts={replyer_max_posts}\n"
        + "summary_max_chars=1000\n\n"
        + "[budget]\n"
        + f"daily_token_budget={daily_token_budget}\n"
        + f"topic_token_budget={topic_token_budget}\n"
    )
    _ = (config_dir / "thresholds.toml").write_text(
        thresholds_toml,
        encoding="utf-8",
    )
    _ = (config_dir / "scheduler.toml").write_text(
        "[polling]\nnotification_interval_seconds=5\nburst_scan_interval_seconds=60\nhourly_scan_interval_seconds=3600\nnightly_memory_hour=0\n",
        encoding="utf-8",
    )
    _ = (config_dir / "webui.toml").write_text(
        "host='127.0.0.1'\nport=5000\nenable_auth=false\nshow_aigc_logs=true\npublic_host='127.0.0.1'\npublic_port=8001\n",
        encoding="utf-8",
    )
    runtime_toml = (
        f"read_only={'true' if read_only else 'false'}\n"
        + f"mark_notifications_read={'true' if mark_notifications_read else 'false'}\n"
        + f"shadow_mode={'true' if shadow_mode else 'false'}\n"
        + f"allow_send_reply={'true' if allow_send_reply else 'false'}\n"
        + f"require_approval_before_send={'true' if require_approval_before_send else 'false'}\n"
        + f"panic_switch={'true' if panic_switch else 'false'}\n"
        + f"topic_cooldown_minutes={topic_cooldown_minutes}\n"
    )
    if blackout_start_hour is not None:
        runtime_toml += f"blackout_start_hour={blackout_start_hour}\n"
    if blackout_end_hour is not None:
        runtime_toml += f"blackout_end_hour={blackout_end_hour}\n"
    runtime_toml += f"muted_topic_ids={muted_topic_ids or []}\n"
    runtime_toml += f"muted_usernames={muted_usernames or []}\n"
    _ = (config_dir / "runtime.toml").write_text(
        runtime_toml,
        encoding="utf-8",
    )
    persona_names = enabled_personas or ["core"]
    enabled_persona_toml = ", ".join(f"'{name}'" for name in persona_names)
    _ = (config_dir / "personas.toml").write_text(
        f"enabled=[{enabled_persona_toml}]\n[priority]\ncore=10\ncatgirl=5\n",
        encoding="utf-8",
    )
    modules = planner_modules or [("planner.md", True), ("safety_rules.md", True)]
    planner_module_lines: list[str] = []
    for name, enabled in modules:
        planner_module_lines.extend(
            [
                "[[planner.modules]]",
                f"name='{name}'",
                f"enabled={'true' if enabled else 'false'}",
            ]
        )
    prompt_modules_toml = (
        "[planner]\n"
        + "\n".join(planner_module_lines)
        + "\n\n[replyer]\n"
        + "[[replyer.modules]]\nname='replyer.md'\nenabled=true\n"
        + "[[replyer.modules]]\nname='style_rules.md'\nenabled=true\n"
        + "[[replyer.modules]]\nname='safety_rules.md'\nenabled=true\n\n"
        + "[memory]\n"
        + "[[memory.modules]]\nname='memory_user_update.md'\nenabled=true\n"
        + "[[memory.modules]]\nname='memory_self_update.md'\nenabled=true\n"
    )
    _ = (config_dir / "prompt_modules.toml").write_text(
        prompt_modules_toml,
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_trigger_engine_reload_updates_runtime_settings_without_recreating_forum_client_for_prompt_only_change(
    tmp_path: Path,
) -> None:
    _write_project_files(tmp_path)
    _write_config(tmp_path)
    database = Database(tmp_path / "data" / "suenmeow.sqlite3")
    database.initialize()
    engine = TriggerEngine(load_settings(AppPaths.from_root(tmp_path)), database)
    original_forum_client = engine.forum_client

    try:
        _write_config(
            tmp_path,
            planner_modules=[("custom_rules.md", True), ("planner.md", True)],
            enabled_personas=["core", "catgirl"],
            allow_send_reply=True,
            require_approval_before_send=False,
            planner_max_posts=25,
            replyer_max_posts=12,
            daily_token_budget=999,
            topic_token_budget=222,
            planner_model="planner-v2",
            mark_notifications_read=True,
            shadow_mode=True,
            panic_switch=True,
            topic_cooldown_minutes=45,
            blackout_start_hour=22,
            blackout_end_hour=6,
            muted_topic_ids=[123, 456],
            muted_usernames=["alice", "bob"],
        )

        changed = await engine.reload_settings_if_needed()

        assert changed is True
        assert engine.forum_client is original_forum_client
        assert engine.notification_worker.mark_notifications_read is True
        assert engine.pipeline.shadow_mode is True
        assert engine.pipeline.panic_switch is True
        assert engine.pipeline.topic_cooldown_minutes == 45
        assert engine.pipeline.blackout_start_hour == 22
        assert engine.pipeline.blackout_end_hour == 6
        assert engine.pipeline.muted_topic_ids == [123, 456]
        assert engine.pipeline.muted_usernames == ["alice", "bob"]
        assert engine.pipeline.allow_send_reply is True
        assert engine.pipeline.require_approval_before_send is False
        assert engine.pipeline.enabled_personas == ["core", "catgirl"]
        assert [module.name for module in engine.pipeline.prompt_modules.planner.modules] == [
            "core.md",
            "custom_rules.md",
            "planner.md",
        ]
        assert engine.pipeline.context_builder.planner_max_posts == 25
        assert engine.pipeline.context_builder.replyer_max_posts == 12
        assert engine.budget_service.daily_token_budget == 999
        assert engine.budget_service.topic_token_budget == 222
        assert engine.activity_worker.thresholds.context.planner_max_posts == 25
        assert engine.llm_client.models["planner"].model == "planner-v2"
    finally:
        await engine.forum_client.aclose()


@pytest.mark.anyio
async def test_trigger_engine_reload_recreates_forum_client_when_forum_settings_change(tmp_path: Path) -> None:
    _write_project_files(tmp_path)
    _write_config(tmp_path, forum_base_url="https://forum-one.example.com")
    database = Database(tmp_path / "data" / "suenmeow.sqlite3")
    database.initialize()
    engine = TriggerEngine(load_settings(AppPaths.from_root(tmp_path)), database)
    original_forum_client = engine.forum_client
    try:
        _write_config(tmp_path, forum_base_url="https://forum-two.example.com")

        changed = await engine.reload_settings_if_needed()

        assert changed is True
        assert engine.forum_client is not original_forum_client
        assert engine.forum_client.forum.base_url == "https://forum-two.example.com"
        assert engine.notification_worker.forum_client is engine.forum_client
        assert engine.activity_worker.forum_client is engine.forum_client
    finally:
        await engine.forum_client.aclose()
