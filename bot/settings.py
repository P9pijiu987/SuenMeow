from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json
import os
from pathlib import Path
from typing import Any
import tomllib


DEFAULT_PLANNER_PROMPT_MODULES = ("planner.md", "safety_rules.md")
DEFAULT_REPLYER_PROMPT_MODULES = ("replyer.md", "style_rules.md", "safety_rules.md")
DEFAULT_MEMORY_PROMPT_MODULES = ("memory_user_update.md", "memory_self_update.md")
PROMPT_MODULES_CONFIG_FILENAME = "prompt_modules.toml"


@dataclass(slots=True)
class AppPaths:
    root: Path
    config_dir: Path
    data_dir: Path
    log_dir: Path
    database_path: Path

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        def resolve_dir(env_name: str, default_name: str) -> Path:
            raw_value = os.getenv(env_name)
            if not raw_value:
                return root / default_name
            candidate = Path(raw_value)
            return candidate if candidate.is_absolute() else root / candidate

        config_dir = resolve_dir("SUENMEOW_CONFIG_DIR", "config")
        data_dir = resolve_dir("SUENMEOW_DATA_DIR", "data")
        log_dir = resolve_dir("SUENMEOW_LOG_DIR", "logs")
        return cls(
            root=root,
            config_dir=config_dir,
            data_dir=data_dir,
            log_dir=log_dir,
            database_path=data_dir / "suenmeow.sqlite3",
        )


@dataclass(slots=True)
class CredentialsConfig:
    username: str
    password: str


@dataclass(slots=True)
class ForumConfig:
    base_url: str
    retry: int
    user_agent: str
    default_headers: dict[str, str]
    reactions: dict[str, str]


@dataclass(slots=True)
class ProviderConfig:
    base_url: str
    api_key: str
    timeout_seconds: int


@dataclass(slots=True)
class ModelRoute:
    provider: str
    model: str


@dataclass(slots=True)
class TriggersThresholds:
    hourly_new_reply_min: int
    hourly_hot_reply_min: int
    burst_window_minutes: int
    burst_reply_min: int


@dataclass(slots=True)
class ContextThresholds:
    planner_max_posts: int
    replyer_max_posts: int
    summary_max_chars: int


@dataclass(slots=True)
class BudgetThresholds:
    daily_token_budget: int
    topic_token_budget: int


@dataclass(slots=True)
class ThresholdsConfig:
    triggers: TriggersThresholds
    context: ContextThresholds
    budget: BudgetThresholds


@dataclass(slots=True)
class PollingConfig:
    notification_interval_seconds: int
    burst_scan_interval_seconds: int
    hourly_scan_interval_seconds: int
    nightly_memory_hour: int


@dataclass(slots=True)
class WebUiConfig:
    host: str
    port: int
    enable_auth: bool
    show_aigc_logs: bool


@dataclass(slots=True)
class RuntimeConfig:
    read_only: bool = True
    mark_notifications_read: bool = False
    shadow_mode: bool = False
    allow_send_reply: bool = False
    require_approval_before_send: bool = False
    panic_switch: bool = False
    topic_cooldown_minutes: int = 0
    blackout_start_hour: int | None = None
    blackout_end_hour: int | None = None
    muted_topic_ids: list[int] = field(default_factory=list)
    muted_usernames: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PersonaConfig:
    enabled: list[str]
    priority: dict[str, int]


@dataclass(slots=True)
class PromptModuleEntry:
    name: str
    enabled: bool = True


@dataclass(slots=True)
class PromptRouteConfig:
    modules: list[PromptModuleEntry]


@dataclass(slots=True)
class PromptModulesConfig:
    planner: PromptRouteConfig
    replyer: PromptRouteConfig
    memory: PromptRouteConfig


@dataclass(slots=True)
class Settings:
    paths: AppPaths
    credentials: CredentialsConfig
    forum: ForumConfig
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelRoute]
    thresholds: ThresholdsConfig
    polling: PollingConfig
    webui: WebUiConfig
    runtime: RuntimeConfig
    personas: PersonaConfig
    prompt_modules: PromptModulesConfig


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _normalize_markdown_filename(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".md":
        raise ValueError(f"Invalid markdown filename: {filename}")
    return filename


def _default_prompt_route_config(names: tuple[str, ...]) -> PromptRouteConfig:
    return PromptRouteConfig(modules=[PromptModuleEntry(name=name, enabled=True) for name in names])


def available_module_files(paths: AppPaths) -> set[str]:
    prompt_dir = paths.root / "prompts"
    persona_dir = paths.root / "personas"
    return {
        *(path.name for path in prompt_dir.glob("*.md")),
        *(path.name for path in persona_dir.glob("*.md")),
    }


def default_prompt_modules_config() -> PromptModulesConfig:
    return PromptModulesConfig(
        planner=_default_prompt_route_config(DEFAULT_PLANNER_PROMPT_MODULES),
        replyer=_default_prompt_route_config(DEFAULT_REPLYER_PROMPT_MODULES),
        memory=_default_prompt_route_config(DEFAULT_MEMORY_PROMPT_MODULES),
    )


def validate_prompt_route_config(route_name: str, route: PromptRouteConfig, *, available_files: set[str]) -> None:
    if not route.modules:
        raise ValueError(f"Prompt route '{route_name}' must contain at least one module")
    if not any(module.enabled for module in route.modules):
        raise ValueError(f"Prompt route '{route_name}' must enable at least one module")

    missing_modules = [module.name for module in route.modules if module.name not in available_files]
    if missing_modules:
        missing_list = ", ".join(sorted(missing_modules))
        raise ValueError(f"Prompt route '{route_name}' references missing modules: {missing_list}")


def validate_prompt_modules_config(paths: AppPaths, prompt_modules: PromptModulesConfig) -> None:
    modules = available_module_files(paths)
    validate_prompt_route_config("planner", prompt_modules.planner, available_files=modules)
    validate_prompt_route_config("replyer", prompt_modules.replyer, available_files=modules)
    validate_prompt_route_config("memory", prompt_modules.memory, available_files=modules)


def _load_prompt_route_config(raw_route: Any, default_names: tuple[str, ...], *, route_name: str, available_files: set[str]) -> PromptRouteConfig:
    if raw_route is None:
        route = _default_prompt_route_config(default_names)
        validate_prompt_route_config(route_name, route, available_files=available_files)
        return route

    modules = raw_route.get("modules", []) if isinstance(raw_route, dict) else raw_route if isinstance(raw_route, list) else None
    if not isinstance(modules, list):
        raise ValueError(f"Prompt route '{route_name}' modules must be a list")

    seen: set[str] = set()
    loaded_modules: list[PromptModuleEntry] = []
    for raw_module in modules:
        if isinstance(raw_module, str):
            name = _normalize_markdown_filename(raw_module)
            enabled = True
        elif isinstance(raw_module, dict):
            raw_name = raw_module.get("name")
            if not isinstance(raw_name, str):
                raise ValueError(f"Prompt route '{route_name}' contains a module without a valid name")
            name = _normalize_markdown_filename(raw_name)
            enabled = bool(raw_module.get("enabled", True))
        else:
            raise ValueError(f"Prompt route '{route_name}' contains an invalid module entry")
        if name in seen:
            raise ValueError(f"Prompt route '{route_name}' contains duplicate module '{name}'")
        seen.add(name)
        loaded_modules.append(PromptModuleEntry(name=name, enabled=enabled))
    route = PromptRouteConfig(modules=loaded_modules)
    validate_prompt_route_config(route_name, route, available_files=available_files)
    return route


def enabled_prompt_module_names(route: PromptRouteConfig) -> list[str]:
    return [module.name for module in route.modules if module.enabled]


def prompt_modules_to_dict(prompt_modules: PromptModulesConfig) -> dict[str, list[dict[str, object]]]:
    return {
        "planner": [
            {"name": module.name, "enabled": module.enabled} for module in prompt_modules.planner.modules
        ],
        "replyer": [
            {"name": module.name, "enabled": module.enabled} for module in prompt_modules.replyer.modules
        ],
        "memory": [{"name": module.name, "enabled": module.enabled} for module in prompt_modules.memory.modules],
    }


def _serialize_prompt_route(route_name: str, route: PromptRouteConfig) -> str:
    lines = [f"[{route_name}]"]
    for module in route.modules:
        name = _normalize_markdown_filename(module.name)
        lines.extend(
            [
                f"[[{route_name}.modules]]",
                f"name = {json.dumps(name, ensure_ascii=False)}",
                f"enabled = {'true' if module.enabled else 'false'}",
            ]
        )
    return "\n".join(lines)


def save_prompt_modules(paths: AppPaths, prompt_modules: PromptModulesConfig) -> None:
    validate_prompt_modules_config(paths, prompt_modules)
    content = (
        f"{_serialize_prompt_route('planner', prompt_modules.planner)}\n\n"
        f"{_serialize_prompt_route('replyer', prompt_modules.replyer)}\n\n"
        f"{_serialize_prompt_route('memory', prompt_modules.memory)}\n"
    )
    target = paths.config_dir / PROMPT_MODULES_CONFIG_FILENAME
    tmp_target = target.with_suffix(".tmp")
    _ = tmp_target.write_text(content, encoding="utf-8")
    _ = tmp_target.replace(target)


def load_settings(paths: AppPaths) -> Settings:
    credentials_raw = _load_toml(paths.config_dir / "credentials.toml")
    forum_raw = _load_toml(paths.config_dir / "forum.toml")
    providers_raw = _load_toml(paths.config_dir / "providers.toml")
    models_raw = _load_toml(paths.config_dir / "models.toml")
    thresholds_raw = _load_toml(paths.config_dir / "thresholds.toml")
    scheduler_raw = _load_toml(paths.config_dir / "scheduler.toml")
    webui_raw = _load_toml(paths.config_dir / "webui.toml")
    runtime_raw = _load_toml(paths.config_dir / "runtime.toml")
    personas_raw = _load_toml(paths.config_dir / "personas.toml")
    prompt_modules_path = paths.config_dir / PROMPT_MODULES_CONFIG_FILENAME
    prompt_modules_raw = _load_toml(prompt_modules_path) if prompt_modules_path.is_file() else {}
    module_files = available_module_files(paths)

    credentials = CredentialsConfig(
        username=credentials_raw["forum"]["username"],
        password=credentials_raw["forum"]["password"],
    )
    forum = ForumConfig(
        base_url=forum_raw["base_url"].rstrip("/"),
        retry=int(forum_raw["retry"]),
        user_agent=forum_raw["user_agent"],
        default_headers=dict(forum_raw.get("default_headers", {})),
        reactions=dict(forum_raw.get("reactions", {})),
    )
    providers = {
        name: ProviderConfig(
            base_url=item["base_url"],
            api_key=item["api_key"],
            timeout_seconds=int(item.get("timeout_seconds", 120)),
        )
        for name, item in providers_raw.items()
    }
    models = {
        name: ModelRoute(provider=item["provider"], model=item["model"])
        for name, item in models_raw.items()
    }
    thresholds = ThresholdsConfig(
        triggers=TriggersThresholds(**thresholds_raw["triggers"]),
        context=ContextThresholds(**thresholds_raw["context"]),
        budget=BudgetThresholds(**thresholds_raw["budget"]),
    )
    polling = PollingConfig(**scheduler_raw["polling"])
    webui = WebUiConfig(**webui_raw)
    runtime = RuntimeConfig(**runtime_raw)
    personas = PersonaConfig(
        enabled=list(personas_raw.get("enabled", [])),
        priority={key: int(value) for key, value in personas_raw.get("priority", {}).items()},
    )
    prompt_modules = PromptModulesConfig(
        planner=_load_prompt_route_config(
            prompt_modules_raw.get("planner"),
            DEFAULT_PLANNER_PROMPT_MODULES,
            route_name="planner",
            available_files=module_files,
        ),
        replyer=_load_prompt_route_config(
            prompt_modules_raw.get("replyer"),
            DEFAULT_REPLYER_PROMPT_MODULES,
            route_name="replyer",
            available_files=module_files,
        ),
        memory=_load_prompt_route_config(
            prompt_modules_raw.get("memory"),
            DEFAULT_MEMORY_PROMPT_MODULES,
            route_name="memory",
            available_files=module_files,
        ),
    )
    return Settings(
        paths=paths,
        credentials=credentials,
        forum=forum,
        providers=providers,
        models=models,
        thresholds=thresholds,
        polling=polling,
        webui=webui,
        runtime=runtime,
        personas=personas,
        prompt_modules=prompt_modules,
    )
