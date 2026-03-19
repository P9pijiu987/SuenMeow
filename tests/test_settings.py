from pathlib import Path

import pytest

from bot.settings import AppPaths, load_settings
from bot.settings import available_module_files


def _write_prompt_and_persona_files(root: Path) -> None:
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
    _ = (personas_dir / "core.md").write_text("core", encoding="utf-8")
    _ = (personas_dir / "catgirl.md").write_text("catgirl", encoding="utf-8")


def _write_base_config(root: Path) -> Path:
    config_dir = root / "config"
    data_dir = root / "data"
    log_dir = root / "logs"
    _ = config_dir.mkdir()
    _ = data_dir.mkdir()
    _ = log_dir.mkdir()
    _write_prompt_and_persona_files(root)

    models_toml = (
        "[planner]\nprovider='default'\nmodel='m'\n"
        "[replyer]\nprovider='default'\nmodel='m'\n"
        "[memory]\nprovider='default'\nmodel='m'\n"
    )
    thresholds_toml = (
        "[triggers]\nhourly_new_reply_min=1\nhourly_hot_reply_min=2\nburst_window_minutes=5\nburst_reply_min=3\n\n"
        "[context]\nplanner_max_posts=10\nreplyer_max_posts=5\nsummary_max_chars=1000\n\n"
        "[budget]\ndaily_token_budget=100\ntopic_token_budget=50\n"
    )
    runtime_toml = (
        "read_only=true\nmark_notifications_read=false\nshadow_mode=false\nallow_send_reply=false\n"
        "require_approval_before_send=true\npanic_switch=false\ntopic_cooldown_minutes=15\n"
        "blackout_start_hour=22\nblackout_end_hour=6\nmuted_topic_ids=[123, 456]\nmuted_usernames=['alice','bob']\n"
    )
    _ = (config_dir / "credentials.toml").write_text("[forum]\nusername='u'\npassword='p'\n", encoding="utf-8")
    _ = (config_dir / "forum.toml").write_text("base_url='https://forum.rdfzer.com'\nretry=3\nuser_agent='ua'\n", encoding="utf-8")
    _ = (config_dir / "providers.toml").write_text("[default]\nbase_url='https://x'\napi_key='k'\ntimeout_seconds=10\n", encoding="utf-8")
    _ = (config_dir / "models.toml").write_text(models_toml, encoding="utf-8")
    _ = (config_dir / "thresholds.toml").write_text(thresholds_toml, encoding="utf-8")
    _ = (config_dir / "scheduler.toml").write_text(
        "[polling]\nnotification_interval_seconds=5\nburst_scan_interval_seconds=60\nhourly_scan_interval_seconds=3600\nnightly_memory_hour=0\n",
        encoding="utf-8",
    )
    _ = (config_dir / "webui.toml").write_text(
        "host='127.0.0.1'\nport=8000\nenable_auth=false\nshow_aigc_logs=true\npublic_host='127.0.0.1'\npublic_port=8001\n",
        encoding="utf-8",
    )
    _ = (config_dir / "runtime.toml").write_text(runtime_toml, encoding="utf-8")
    _ = (config_dir / "personas.toml").write_text("enabled=['core','catgirl']\n[priority]\ncore=10\ncatgirl=5\n", encoding="utf-8")
    return config_dir


def test_settings_load_personas(tmp_path: Path) -> None:
    root = tmp_path
    _ = _write_base_config(root)

    settings = load_settings(AppPaths.from_root(root))
    assert settings.personas.enabled == ["core", "catgirl"]
    assert settings.personas.priority["core"] == 10
    assert settings.runtime.read_only is True
    assert settings.runtime.shadow_mode is False
    assert settings.runtime.require_approval_before_send is True
    assert settings.runtime.panic_switch is False
    assert settings.runtime.topic_cooldown_minutes == 15
    assert settings.runtime.blackout_start_hour == 22
    assert settings.runtime.blackout_end_hour == 6
    assert settings.runtime.muted_topic_ids == [123, 456]
    assert settings.runtime.muted_usernames == ["alice", "bob"]
    assert settings.webui.public_host == "127.0.0.1"
    assert settings.webui.public_port == 8001
    assert [module.name for module in settings.prompt_modules.planner.modules] == ["planner.md", "safety_rules.md"]
    assert [module.name for module in settings.prompt_modules.replyer.modules] == [
        "replyer.md",
        "style_rules.md",
        "safety_rules.md",
    ]
    assert [module.name for module in settings.prompt_modules.memory.modules] == [
        "memory_user_update.md",
        "memory_self_update.md",
    ]


def test_settings_load_custom_prompt_modules(tmp_path: Path) -> None:
    root = tmp_path
    config_dir = _write_base_config(root)
    prompt_modules_toml = (
        "[planner]\n[[planner.modules]]\nname='planner.md'\nenabled=true\n[[planner.modules]]\nname='safety_rules.md'\nenabled=false\n\n"
        "[replyer]\n[[replyer.modules]]\nname='safety_rules.md'\nenabled=true\n[[replyer.modules]]\nname='replyer.md'\nenabled=true\n\n"
        "[memory]\n[[memory.modules]]\nname='core.md'\nenabled=true\n[[memory.modules]]\nname='memory_self_update.md'\nenabled=false\n"
    )
    _ = (config_dir / "prompt_modules.toml").write_text(prompt_modules_toml, encoding="utf-8")

    settings = load_settings(AppPaths.from_root(root))

    assert [(module.name, module.enabled) for module in settings.prompt_modules.planner.modules] == [
        ("planner.md", True),
        ("safety_rules.md", False),
    ]
    assert [(module.name, module.enabled) for module in settings.prompt_modules.replyer.modules] == [
        ("safety_rules.md", True),
        ("replyer.md", True),
    ]
    assert [(module.name, module.enabled) for module in settings.prompt_modules.memory.modules] == [
        ("core.md", True),
        ("memory_self_update.md", False),
    ]


def test_settings_missing_prompt_module_file_is_ignored_with_fallback(tmp_path: Path) -> None:
    root = tmp_path
    config_dir = _write_base_config(root)
    prompt_modules_toml = (
        "[planner]\n[[planner.modules]]\nname='planner.md'\nenabled=true\n\n"
        "[replyer]\n[[replyer.modules]]\nname='TsundereCatgirlMaid.md'\nenabled=true\n\n"
        "[memory]\n[[memory.modules]]\nname='missing_memory.md'\nenabled=true\n"
    )
    _ = (config_dir / "prompt_modules.toml").write_text(prompt_modules_toml, encoding="utf-8")

    settings = load_settings(AppPaths.from_root(root))

    assert [module.name for module in settings.prompt_modules.planner.modules] == ["planner.md"]
    assert [module.name for module in settings.prompt_modules.replyer.modules] == [
        "replyer.md",
        "style_rules.md",
        "safety_rules.md",
    ]
    assert [module.name for module in settings.prompt_modules.memory.modules] == [
        "memory_user_update.md",
        "memory_self_update.md",
    ]


def test_settings_still_rejects_missing_modules_on_explicit_validation(tmp_path: Path) -> None:
    root = tmp_path
    config_dir = _write_base_config(root)
    prompt_modules_toml = (
        "[planner]\n[[planner.modules]]\nname='planner.md'\nenabled=true\n\n"
        "[replyer]\n[[replyer.modules]]\nname='replyer.md'\nenabled=true\n\n"
        "[memory]\n[[memory.modules]]\nname='missing_memory.md'\nenabled=true\n"
    )
    _ = (config_dir / "prompt_modules.toml").write_text(prompt_modules_toml, encoding="utf-8")

    from bot.settings import PromptModuleEntry
    from bot.settings import PromptRouteConfig
    from bot.settings import validate_prompt_route_config

    route = PromptRouteConfig(modules=[PromptModuleEntry(name="missing_memory.md", enabled=True)])
    with pytest.raises(ValueError, match="references missing modules"):
        validate_prompt_route_config("memory", route, available_files=available_module_files(AppPaths.from_root(root)))


def test_settings_reject_prompt_route_without_enabled_modules(tmp_path: Path) -> None:
    root = tmp_path
    config_dir = _write_base_config(root)
    prompt_modules_toml = (
        "[planner]\n[[planner.modules]]\nname='planner.md'\nenabled=false\n\n"
        "[replyer]\n[[replyer.modules]]\nname='replyer.md'\nenabled=true\n\n"
        "[memory]\n[[memory.modules]]\nname='memory_user_update.md'\nenabled=true\n"
    )
    _ = (config_dir / "prompt_modules.toml").write_text(prompt_modules_toml, encoding="utf-8")

    with pytest.raises(ValueError, match="must enable at least one module"):
        _ = load_settings(AppPaths.from_root(root))


def test_app_paths_support_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    monkeypatch.setenv("SUENMEOW_CONFIG_DIR", "deploy-config")
    monkeypatch.setenv("SUENMEOW_DATA_DIR", str(root / "shared-data"))
    monkeypatch.setenv("SUENMEOW_LOG_DIR", "runtime-logs")

    paths = AppPaths.from_root(root)

    assert paths.config_dir == root / "deploy-config"
    assert paths.data_dir == root / "shared-data"
    assert paths.log_dir == root / "runtime-logs"
    assert paths.database_path == root / "shared-data" / "suenmeow.sqlite3"


def test_app_paths_default_to_root_directories_when_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    monkeypatch.delenv("SUENMEOW_CONFIG_DIR", raising=False)
    monkeypatch.delenv("SUENMEOW_DATA_DIR", raising=False)
    monkeypatch.delenv("SUENMEOW_LOG_DIR", raising=False)

    paths = AppPaths.from_root(root)

    assert paths.config_dir == root / "config"
    assert paths.data_dir == root / "data"
    assert paths.log_dir == root / "logs"
    assert paths.database_path == root / "data" / "suenmeow.sqlite3"


def test_available_module_files_includes_public_directories(tmp_path: Path) -> None:
    root = tmp_path
    _ = _write_base_config(root)
    _ = (root / "prompts_public").mkdir()
    _ = (root / "personas_public").mkdir()
    _ = (root / "prompts_public" / "public_prompt.md").write_text("p", encoding="utf-8")
    _ = (root / "personas_public" / "public_persona.md").write_text("x", encoding="utf-8")

    files = available_module_files(AppPaths.from_root(root))
    assert "public_prompt.md" in files
    assert "public_persona.md" in files
