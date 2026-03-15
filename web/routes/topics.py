from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from bot.trigger_engine import TriggerEngine


router = APIRouter(prefix="/topics", tags=["话题"])


@router.get("/banned", summary="查看已封禁话题")
def banned_topics(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {"items": database.list_banned_topics()}


@router.get("/runs", summary="查看流水记录")
def recent_runs(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {"items": database.list_recent_pipeline_runs()}


@router.get("/runs/{run_id}", summary="查看单条流水详情")
def pipeline_run_detail(run_id: int, request: Request) -> dict[str, object]:
    database = request.app.state.database
    item = database.get_pipeline_run(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"pipeline run {run_id} not found")
    return item


@router.get("/events", summary="查看触发事件")
def recent_events(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {"items": database.list_recent_trigger_events()}


@router.get("/states", summary="查看话题状态")
def topic_states(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {"items": database.list_topic_states()}


@router.get("/pending-replies", summary="查看待处理回复")
def pending_replies(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {"items": database.list_pending_replies()}


@router.post("/pending-replies/{pending_reply_id}/approve", summary="批准发送待处理回复")
async def approve_pending_reply(pending_reply_id: int, request: Request) -> dict[str, object]:
    approval_service = request.app.state.approval_service
    try:
        item = await approval_service.approve_pending_reply(pending_reply_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return item


@router.get("/{topic_id}/debug", summary="调试单个话题")
async def debug_topic(topic_id: int, request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    database = request.app.state.database
    engine = TriggerEngine(settings, database)
    try:
        results = await engine.debug_topics(topic_ids=[topic_id], count=1)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"topic debug failed: {exc}") from exc
    if not results:
        raise HTTPException(status_code=404, detail=f"topic {topic_id} debug result not found")
    return dict(results[0])
