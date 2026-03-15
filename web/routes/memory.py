from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel


router = APIRouter(prefix="/memory", tags=["记忆"])


class SelfMemoryPayload(BaseModel):
    memory: str


class UserMemoryPayload(BaseModel):
    memory: str


@router.get("", summary="查看记忆数据")
def get_memory(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {
        "self_memory": database.get_self_memory(),
        "user_memories": database.list_user_memories(),
    }


@router.get("/self", summary="查看自我记忆")
def get_self_memory(request: Request) -> dict[str, str]:
    database = request.app.state.database
    return {"memory": database.get_self_memory()}


@router.put("/self", summary="更新自我记忆")
def update_self_memory(payload: SelfMemoryPayload, request: Request) -> dict[str, str]:
    database = request.app.state.database
    database.set_self_memory(payload.memory)
    return {"memory": database.get_self_memory()}


@router.put("/user/{username}", summary="更新用户记忆")
def update_user_memory(username: str, payload: UserMemoryPayload, request: Request) -> dict[str, str]:
    database = request.app.state.database
    database.upsert_user_memory(username, payload.memory)
    return {"username": username, "memory": payload.memory}

