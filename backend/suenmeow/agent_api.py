"""Administrator-owned research sessions; mutations use the application's auth/CSRF dependency."""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from .agent import TERMINAL, confirm_draft, draft_digest, enqueue, own_session, own_task
from .database import (Account, AgentDraft, AgentMessage, AgentSession, AgentSource, AgentStep,
                       AgentTask, KV, LoginSession, Reply, Usage, audit, locked, now)
from .domain import AgentConfirmInput, AgentDraftInput, AgentMessageInput, AgentPolicy


def mount_agent_api(app, db, vault, admin):
    router = APIRouter(prefix="/api/agent", dependencies=[Depends(admin)])

    def task_data(s, task):
        draft = s.scalar(select(AgentDraft).where(AgentDraft.task_id == task.id))
        reply = s.get(Reply, draft.reply_id) if draft and draft.reply_id else None
        control, worker = s.get(KV, "control").data, s.get(KV, "worker").data
        block = ""
        if task.cancelled or task.expires <= now():
            block = "任务已停止或过期，请重新研究"
        elif control["mode"] not in ("approval", "auto"):
            block = "当前为暂停或只读模式，草稿可以预览，不能发送"
        elif worker.get("status") != "online" or worker.get("baseline_epoch") != control["epoch"] or now() - worker.get("heartbeat", 0) > 60:
            block = "等待 worker 在线并建立新水位"
        return {"id": task.id, "session_id": task.session_id, "state": task.state, "reason": task.reason,
                "snapshot_id": task.snapshot_id, "send_block_reason": block,
                "created": task.created, "expires": task.expires, "cancelled": task.cancelled,
                "constraints": {k: v for k, v in task.constraints.items() if k != "policy"},
                "result": vault.open(task.result_cipher) if task.result_cipher else "",
                "tokens": s.scalar(select(func.coalesce(func.sum(Usage.tokens), 0)).where(Usage.task_id == task.id)),
                "steps": [{"id": x.id, "tool": x.tool, "state": x.state, "detail": vault.open(x.detail_cipher), "created": x.created}
                          for x in s.scalars(select(AgentStep).where(AgentStep.task_id == task.id).order_by(AgentStep.id))],
                "sources": [{"id": x.id, "topic_id": x.topic_id, "post_number": x.post_number, "url": x.url,
                             "public": x.public, **vault.open(x.content_cipher)}
                            for x in s.scalars(select(AgentSource).where(AgentSource.task_id == task.id))],
                "draft": {"id": draft.id, "target_topic": draft.target_topic, "text": vault.open(draft.text_cipher),
                          "digest": draft.digest, "version": draft.version, "confirmed": draft.confirmed,
                          "reply_id": draft.reply_id, "reply_state": reply.state if reply else "",
                          "send_reason": reply.reason if reply else "", "sent_post_id": reply.sent_post_id if reply else 0} if draft else None}

    @router.get("/settings")
    def settings(account=Depends(admin)):
        with db.transaction() as s:
            return AgentPolicy.model_validate(s.get(KV, "agent_policy").data).model_dump()

    @router.put("/settings")
    def save_settings(body: AgentPolicy, account=Depends(admin)):
        with db.transaction() as s:
            locked(s, "agent_lock").data = {}
            s.get(KV, "agent_policy").data = body.model_dump()
            audit(s, account.id, "agent_settings_changed")
        return {"ok": True}

    @router.post("/sessions")
    def create_session(account=Depends(admin)):
        with db.transaction() as s:
            row = AgentSession(owner=account.id)
            s.add(row)
            s.flush()
            return {"id": row.id, "title": row.title, "created": row.created}

    @router.get("/sessions")
    def sessions(account=Depends(admin)):
        with db.transaction() as s:
            return [{"id": x.id, "title": x.title, "created": x.created}
                    for x in s.scalars(select(AgentSession).where(AgentSession.owner == account.id).order_by(AgentSession.created.desc()).limit(100))]

    @router.get("/sessions/{session_id}")
    def session(session_id: str, account=Depends(admin)):
        with db.transaction() as s:
            row = own_session(s, session_id, account.id)
            return {"id": row.id, "title": row.title,
                    "messages": [{"id": m.id, "role": m.role, "text": vault.open(m.text_cipher),
                                  "created": m.created, "meta": m.meta}
                                 for m in s.scalars(select(AgentMessage).where(AgentMessage.session_id == row.id).order_by(AgentMessage.created).limit(200))],
                    "tasks": [{"id": t.id, "state": t.state, "reason": t.reason}
                              for t in s.scalars(select(AgentTask).where(AgentTask.session_id == row.id).order_by(AgentTask.created.desc()).limit(100))]}

    @router.post("/sessions/{session_id}/messages")
    def message(session_id: str, body: AgentMessageInput, account=Depends(admin)):
        with db.transaction() as s:
            return {"task_id": enqueue(s, vault, account.id, session_id, body)}

    @router.get("/tasks/{task_id}")
    def task(task_id: str, account=Depends(admin)):
        with db.transaction() as s:
            return task_data(s, own_task(s, task_id, account.id))

    @router.get("/tasks/{task_id}/stream")
    async def stream(task_id: str, request: Request, after: int = Query(0, ge=0), account=Depends(admin)):
        with db.transaction() as s:
            own_task(s, task_id, account.id)
        last_event = request.headers.get("last-event-id", "")
        after = max(after, int(last_event)) if last_event.isdigit() else after
        async def events():
            cursor = after
            # Recheck account and session on every batch; revocation also terminates open streams.
            for _ in range(180):
                if await request.is_disconnected():
                    return
                with db.transaction() as s:
                    live = s.get(LoginSession, request.state.session_id)
                    user = s.get(Account, account.id)
                    if not live or live.expires <= now() or not user or not user.active or user.role != "admin":
                        return
                    row = own_task(s, task_id, account.id)
                    steps = [{"id": x.id, "tool": x.tool, "state": x.state, "detail": vault.open(x.detail_cipher)}
                             for x in s.scalars(select(AgentStep).where(AgentStep.task_id == task_id, AgentStep.id > cursor).order_by(AgentStep.id))]
                    state, reason = row.state, row.reason
                for step in steps:
                    cursor = step["id"]
                    yield f"id: {cursor}\nevent: step\ndata: {json.dumps(step, ensure_ascii=False)}\n\n"
                yield "event: status\ndata: " + json.dumps({"state": state, "reason": reason}, ensure_ascii=False) + "\n\n"
                if state in TERMINAL:
                    yield "event: done\ndata: {}\n\n"
                    return
                await asyncio.sleep(1)
        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @router.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str, account=Depends(admin)):
        with db.transaction() as s:
            locked(s, "control")
            task = own_task(s, task_id, account.id, lock=True)
            draft = s.scalar(select(AgentDraft).where(AgentDraft.task_id == task_id).with_for_update())
            reply = s.get(Reply, draft.reply_id) if draft and draft.reply_id else None
            if reply and reply.state in ("sending", "sent", "unknown"):
                raise HTTPException(409, "请求已发出或结果需核实，停止不能撤回；请检查发送记录")
            if reply:
                reply.state, reply.reason = "cancelled", "管理员停止任务"
            task.cancelled, task.state, task.reason = True, "cancelled", "管理员停止任务"
            audit(s, account.id, "agent_cancelled", task_id)
        return {"ok": True}

    @router.put("/drafts/{draft_id}")
    def edit(draft_id: str, body: AgentDraftInput, account=Depends(admin)):
        with db.transaction() as s:
            locked(s, "control")
            draft = s.get(AgentDraft, draft_id)
            if not draft:
                raise HTTPException(404, "草稿不存在")
            task = own_task(s, draft.task_id, account.id, lock=True)
            draft = s.scalar(select(AgentDraft).where(AgentDraft.id == draft_id).with_for_update())
            if task.cancelled or task.expires <= now():
                raise HTTPException(409, "任务已过期或停止，请创建新任务")
            if draft.reply_id:
                reply = s.get(Reply, draft.reply_id)
                if reply.state in ("sending", "sent", "unknown"):
                    raise HTTPException(409, "草稿已进入发送流程，不能修改")
                reply.state, reply.reason = "cancelled", "草稿编辑撤销原批准"
                draft.confirmed, draft.reply_id = False, ""
                task.state, task.reason = "awaiting_confirmation", "草稿已修改，需要重新确认"
            if draft.version != body.version:
                raise HTTPException(409, "草稿已更新，请刷新")
            if body.target_topic != task.constraints["target_topic"]:
                raise HTTPException(422, "改变目标需要创建新任务，重新核验权限与来源")
            limit = min(task.constraints["max_chars"], task.constraints.get("forum_limit", 0),
                        AgentPolicy.model_validate(s.get(KV, "agent_policy").data).max_chars)
            if not 0 < len(body.text.strip()) <= limit:
                raise HTTPException(422, "草稿超过本次长度限制")
            draft.text_cipher, draft.digest = vault.seal(body.text.strip()), draft_digest(body.text.strip(), task.constraints)
            draft.version += 1
            audit(s, account.id, "agent_draft_edited", task.id, version=draft.version)
        return {"ok": True}

    @router.post("/drafts/{draft_id}/confirm")
    def confirm(draft_id: str, body: AgentConfirmInput, account=Depends(admin)):
        with db.transaction() as s:
            locked(s, "control")
            draft = s.get(AgentDraft, draft_id)
            if not draft:
                raise HTTPException(404, "草稿不存在")
            task = own_task(s, draft.task_id, account.id, lock=True)
            draft = s.scalar(select(AgentDraft).where(AgentDraft.id == draft_id).with_for_update())
            return {"reply_id": confirm_draft(s, vault, draft, task, body.digest)}

    app.include_router(router)
