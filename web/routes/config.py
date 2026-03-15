from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi import HTTPException
from pydantic import BaseModel

from bot.settings import editable_config_filenames
from bot.settings import runtime_mode_name
from bot.settings import Settings
from bot.settings import PromptModuleEntry
from bot.settings import PromptModulesConfig
from bot.settings import PromptRouteConfig
from bot.settings import load_settings
from bot.settings import prompt_modules_to_dict
from bot.settings import save_editable_config_text
from bot.settings import save_prompt_modules
from bot.settings import validate_editable_config_filename
from bot.settings import validate_prompt_modules_config


router = APIRouter(prefix="/config", tags=["配置"])


class PromptModuleItemPayload(BaseModel):
    name: str
    enabled: bool = True


class PromptRoutePayload(BaseModel):
    modules: list[PromptModuleItemPayload]


class PromptModulesPayload(BaseModel):
    planner: PromptRoutePayload
    replyer: PromptRoutePayload
    memory: PromptRoutePayload


class RuntimeModePayload(BaseModel):
    mode: str


class ConfigTextPayload(BaseModel):
    content: str


def _available_prompt_files(request: Request) -> list[str]:
    prompt_dir = request.app.state.paths.root / "prompts"
    return sorted(path.name for path in prompt_dir.glob("*.md"))


def _available_persona_files(request: Request) -> list[str]:
    persona_dir = request.app.state.paths.root / "personas"
    return sorted(path.name for path in persona_dir.glob("*.md"))


def _available_module_files(request: Request) -> list[str]:
    return sorted({*_available_prompt_files(request), *_available_persona_files(request)})


def _normalize_markdown_filename(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".md":
        raise HTTPException(status_code=400, detail=f"提示词模块名不合法: {filename}")
    return filename


def _build_route_config(items: list[PromptModuleItemPayload], available_files: set[str]) -> PromptRouteConfig:
    if not items:
        raise HTTPException(status_code=400, detail="提示词模块链不能为空")
    seen: set[str] = set()
    modules: list[PromptModuleEntry] = []
    for item in items:
        name = _normalize_markdown_filename(item.name)
        if name not in available_files:
            raise HTTPException(status_code=400, detail=f"未找到提示词模块: {name}")
        if name in seen:
            raise HTTPException(status_code=400, detail=f"提示词模块重复: {name}")
        seen.add(name)
        modules.append(PromptModuleEntry(name=name, enabled=item.enabled))
    if not any(module.enabled for module in modules):
        raise HTTPException(status_code=400, detail="每条提示词链路至少需要启用一个模块")
    return PromptRouteConfig(modules=modules)


def _config_response(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "forum_base_url": settings.forum.base_url,
        "models": {name: route.model for name, route in settings.models.items()},
        "editable_configs": editable_config_filenames(),
        "runtime": {
            "mode": runtime_mode_name(settings.runtime),
            "read_only": settings.runtime.read_only,
            "mark_notifications_read": settings.runtime.mark_notifications_read,
            "shadow_mode": settings.runtime.shadow_mode,
            "allow_send_reply": settings.runtime.allow_send_reply,
            "require_approval_before_send": settings.runtime.require_approval_before_send,
            "panic_switch": settings.runtime.panic_switch,
            "topic_cooldown_minutes": settings.runtime.topic_cooldown_minutes,
            "blackout_start_hour": settings.runtime.blackout_start_hour,
            "blackout_end_hour": settings.runtime.blackout_end_hour,
            "muted_topic_ids": settings.runtime.muted_topic_ids,
            "muted_usernames": settings.runtime.muted_usernames,
        },
        "providers": {
            name: {
                "base_url": provider.base_url,
                "timeout_seconds": provider.timeout_seconds,
                "has_api_key": provider.api_key != "replace_me" and bool(provider.api_key),
            }
            for name, provider in settings.providers.items()
        },
        "available_prompt_files": _available_prompt_files(request),
        "available_persona_files": _available_persona_files(request),
        "available_module_files": _available_module_files(request),
        "prompt_modules": prompt_modules_to_dict(settings.prompt_modules),
    }


def _apply_web_settings(request: Request, settings: Settings) -> None:
    request.app.state.settings = settings
    request.app.state.approval_service.settings = settings


def _reload_web_settings(request: Request) -> None:
    _apply_web_settings(request, load_settings(request.app.state.paths))


def _runtime_mode_state(mode: str) -> dict[str, bool]:
    normalized = mode.strip().lower()
    mapping = {
        "read-only": {
            "read_only": True,
            "shadow_mode": False,
            "allow_send_reply": False,
            "require_approval_before_send": True,
        },
        "shadow": {
            "read_only": False,
            "shadow_mode": True,
            "allow_send_reply": True,
            "require_approval_before_send": True,
        },
        "approval": {
            "read_only": False,
            "shadow_mode": False,
            "allow_send_reply": True,
            "require_approval_before_send": True,
        },
        "direct-send": {
            "read_only": False,
            "shadow_mode": False,
            "allow_send_reply": True,
            "require_approval_before_send": False,
        },
    }
    if normalized not in mapping:
        raise HTTPException(status_code=400, detail=f"不支持的运行模式: {mode}")
    return mapping[normalized]


@router.get("", summary="查看当前配置")
def get_config(request: Request) -> dict[str, object]:
    return _config_response(request)


@router.put("/prompt-modules", summary="更新提示词模块编排")
def update_prompt_modules(payload: PromptModulesPayload, request: Request) -> dict[str, object]:
    available_files = set(_available_module_files(request))
    prompt_modules = PromptModulesConfig(
        planner=_build_route_config(payload.planner.modules, available_files),
        replyer=_build_route_config(payload.replyer.modules, available_files),
        memory=_build_route_config(payload.memory.modules, available_files),
    )
    try:
        validate_prompt_modules_config(request.app.state.paths, prompt_modules)
        save_prompt_modules(request.app.state.paths, prompt_modules)
        _reload_web_settings(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _config_response(request)


@router.get("/editable/{filename}", summary="读取可编辑配置文件")
def get_editable_config(filename: str, request: Request) -> dict[str, object]:
    try:
        safe_name = validate_editable_config_filename(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = request.app.state.paths.config_dir / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"配置文件不存在: {safe_name}")
    return {"file": safe_name, "content": target.read_text(encoding="utf-8")}


@router.put("/editable/{filename}", summary="更新非敏感配置文件")
def update_editable_config(filename: str, payload: ConfigTextPayload, request: Request) -> dict[str, object]:
    try:
        safe_name = validate_editable_config_filename(filename)
        updated_settings = save_editable_config_text(request.app.state.paths, safe_name, payload.content)
        _apply_web_settings(request, updated_settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = request.app.state.paths.config_dir / safe_name
    return {"file": safe_name, "content": target.read_text(encoding="utf-8")}


@router.put("/runtime-mode", summary="切换运行模式")
def update_runtime_mode(payload: RuntimeModePayload, request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    runtime_lines = [
        f"read_only = {'true' if settings.runtime.read_only else 'false'}",
        f"mark_notifications_read = {'true' if settings.runtime.mark_notifications_read else 'false'}",
        f"shadow_mode = {'true' if settings.runtime.shadow_mode else 'false'}",
        f"allow_send_reply = {'true' if settings.runtime.allow_send_reply else 'false'}",
        f"require_approval_before_send = {'true' if settings.runtime.require_approval_before_send else 'false'}",
        f"panic_switch = {'true' if settings.runtime.panic_switch else 'false'}",
        f"topic_cooldown_minutes = {settings.runtime.topic_cooldown_minutes}",
    ]
    if settings.runtime.blackout_start_hour is not None:
        runtime_lines.append(f"blackout_start_hour = {settings.runtime.blackout_start_hour}")
    if settings.runtime.blackout_end_hour is not None:
        runtime_lines.append(f"blackout_end_hour = {settings.runtime.blackout_end_hour}")
    runtime_lines.append(f"muted_topic_ids = {settings.runtime.muted_topic_ids}")
    runtime_lines.append(f"muted_usernames = {settings.runtime.muted_usernames}")

    overrides = _runtime_mode_state(payload.mode)
    rewritten: list[str] = []
    for line in runtime_lines:
        key = line.split("=", 1)[0].strip()
        if key in overrides:
            rewritten.append(f"{key} = {'true' if overrides[key] else 'false'}")
        else:
            rewritten.append(line)
    try:
        updated_settings = save_editable_config_text(request.app.state.paths, "runtime.toml", "\n".join(rewritten) + "\n")
        _apply_web_settings(request, updated_settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _config_response(request)
