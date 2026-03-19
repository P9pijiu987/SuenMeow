from __future__ import annotations

import httpx
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from bot import forum_client as forum_client_module
from bot.trigger_engine import TriggerEngine


router = APIRouter(prefix="/topics", tags=["话题"])


class BanTopicPayload(BaseModel):
    reason: str = "banned from webui"


def _format_topic_export_text(topic_id: int, topic_title: str, posts: list[dict[str, object]]) -> str:
    def _to_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return 0
        return 0

    ordered_posts = sorted(posts, key=lambda post: _to_int(post.get("post_number")))
    user_by_post_number: dict[int, str] = {}
    for post in ordered_posts:
        post_number = _to_int(post.get("post_number"))
        username = str(post.get("username") or "")
        if post_number > 0:
            user_by_post_number[post_number] = username

    lines = [
        f"topic_id: {topic_id}",
        f"topic_title: {topic_title}",
        f"post_count: {len(ordered_posts)}",
        "",
    ]

    for post in ordered_posts:
        post_number = _to_int(post.get("post_number"))
        reply_to_post_number = _to_int(post.get("reply_to_post_number"))
        reply_to_username = user_by_post_number.get(reply_to_post_number, "") if reply_to_post_number > 0 else ""
        username = str(post.get("username") or "")
        created_at = str(post.get("created_at") or "")
        raw_text = str(post.get("raw_text") or "")

        lines.append(f"--- post #{post_number} ---")
        lines.append(f"sender: {username}")
        lines.append(f"time: {created_at}")
        if reply_to_post_number > 0:
            lines.append(f"reply_to_post_number: {reply_to_post_number}")
            lines.append(f"reply_to_user: {reply_to_username}")
        else:
            lines.append("reply_to_post_number: topic")
            lines.append("reply_to_user: topic")
        lines.append("content:")
        lines.append(raw_text)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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


@router.get("/{topic_id}/export.txt", summary="导出话题全文 TXT")
async def export_topic_txt(topic_id: int, request: Request) -> PlainTextResponse:
    settings = request.app.state.settings
    forum_client = forum_client_module.ForumClient(settings.forum, settings.credentials, read_only=True)
    try:
        await forum_client.login()
        topic = await forum_client.get_topic(topic_id)
        topic_title = str(topic.get("title") or "")
        stream_ids = []
        for post_id in topic.get("post_stream", {}).get("stream", []):
            if isinstance(post_id, bool):
                stream_ids.append(int(post_id))
            elif isinstance(post_id, int):
                stream_ids.append(post_id)
            elif isinstance(post_id, float):
                stream_ids.append(int(post_id))
            elif isinstance(post_id, str):
                try:
                    stream_ids.append(int(post_id.strip()))
                except ValueError:
                    continue
        posts = await forum_client.get_posts(topic_id, stream_ids)
        content = _format_topic_export_text(topic_id=topic_id, topic_title=topic_title, posts=posts)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"topic {topic_id} not found") from exc
        raise HTTPException(status_code=502, detail=f"topic export failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"topic export failed: {exc}") from exc
    finally:
        await forum_client.aclose()

    return PlainTextResponse(
        content,
        headers={"Content-Disposition": f'attachment; filename="topic-{topic_id}.txt"'},
    )


@router.post("/{topic_id}/ban", summary="封禁话题")
def ban_topic(topic_id: int, payload: BanTopicPayload, request: Request) -> dict[str, object]:
    database = request.app.state.database
    reason = payload.reason.strip() or "banned from webui"
    database.add_topic_ban(topic_id, reason)
    return {"topic_id": topic_id, "reason": reason, "status": "banned"}


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


@router.post("/pending-replies/{pending_reply_id}/reject", summary="拒绝待处理回复")
def reject_pending_reply(pending_reply_id: int, request: Request) -> dict[str, object]:
    approval_service = request.app.state.approval_service
    try:
        item = approval_service.reject_pending_reply(pending_reply_id)
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
