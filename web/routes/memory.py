from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bot.memory_service import MemoryService
from db.repositories import Database


router = APIRouter(prefix="/memory", tags=["记忆"])


class SelfMemoryPayload(BaseModel):
    memory: str


class UserMemoryPayload(BaseModel):
    memory: str


def _memory_service(request: Request) -> MemoryService:
    database = cast(Database, request.app.state.database)
    return MemoryService(database)


@router.get("", summary="查看记忆数据")
def get_memory(request: Request) -> dict[str, object]:
    memory_service = _memory_service(request)
    return {
        "self_memory": memory_service.get_self_memory(),
        "user_memories": memory_service.list_user_memories(),
    }


@router.get("/self", summary="查看自我记忆")
def get_self_memory(request: Request) -> dict[str, str]:
    memory_service = _memory_service(request)
    return {"memory": memory_service.get_self_memory()}


@router.put("/self", summary="更新自我记忆")
def update_self_memory(payload: SelfMemoryPayload, request: Request) -> dict[str, str]:
    memory_service = _memory_service(request)
    updated_memory = memory_service.set_self_memory_from_admin(payload.memory)
    return {"memory": updated_memory}


@router.put("/user/{username}", summary="更新用户记忆")
def update_user_memory(username: str, payload: UserMemoryPayload, request: Request) -> dict[str, str]:
    memory_service = _memory_service(request)
    updated_memory = memory_service.set_user_memory_from_admin(username, payload.memory)
    return {"username": username, "memory": updated_memory}
