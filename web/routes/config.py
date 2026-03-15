from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi import HTTPException
from pydantic import BaseModel

from bot.settings import PromptModuleEntry
from bot.settings import PromptModulesConfig
from bot.settings import PromptRouteConfig
from bot.settings import load_settings
from bot.settings import prompt_modules_to_dict
from bot.settings import save_prompt_modules
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
        "runtime": {
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
        request.app.state.settings = load_settings(request.app.state.paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _config_response(request)
