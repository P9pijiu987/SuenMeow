from __future__ import annotations

from typing import Any
from typing import Awaitable
from typing import Callable

from bot.forum_client import ForumClient
from bot.settings import Settings
from db.repositories import Database


ReplySender = Callable[[int, str, int | None], Awaitable[dict[str, Any]]]


class ApprovalService:
    def __init__(self, database: Database, settings: Settings, send_reply: ReplySender | None = None) -> None:
        self.database = database
        self.settings = settings
        self._send_reply = send_reply

    async def approve_pending_reply(self, pending_reply_id: int) -> dict[str, Any]:
        pending_reply = self.database.get_pending_reply(pending_reply_id)
        if pending_reply is None:
            raise KeyError(f"pending reply {pending_reply_id} not found")
        if pending_reply.status != "pending":
            raise RuntimeError(f"pending reply {pending_reply_id} is already {pending_reply.status}")
        if self.settings.runtime.read_only:
            raise RuntimeError("manual approval send is disabled in read-only mode")
        if self.settings.runtime.panic_switch:
            raise RuntimeError("manual approval send is disabled because panic_switch=true")
        if not self.settings.runtime.allow_send_reply:
            raise RuntimeError("manual approval send is disabled because allow_send_reply=false")

        try:
            response = await self._reply(
                pending_reply.topic_id,
                pending_reply.draft_content,
                pending_reply.target_post_number,
            )
        except Exception as exc:
            self.database.mark_pending_reply_error(pending_reply_id, str(exc))
            raise

        self.database.record_reply(
            pending_reply.topic_id,
            pending_reply.draft_content,
            response.get("id"),
            pending_reply.target_post_number,
        )
        self.database.mark_pending_reply_sent(pending_reply_id, response.get("id"))
        updated = self.database.get_pending_reply(pending_reply_id)
        assert updated is not None
        item = self.database.list_pending_replies(status=None, limit=100)
        for candidate in item:
            if int(candidate["id"]) == pending_reply_id:
                return candidate
        raise RuntimeError(f"pending reply {pending_reply_id} disappeared after send")

    async def _reply(self, topic_id: int, draft_content: str, target_post_number: int | None) -> dict[str, Any]:
        if self._send_reply is not None:
            return await self._send_reply(topic_id, draft_content, target_post_number)

        forum_client = ForumClient(
            self.settings.forum,
            self.settings.credentials,
            read_only=self.settings.runtime.read_only,
        )
        try:
            await forum_client.login()
            return await forum_client.reply(topic_id, draft_content, target_post_number)
        finally:
            await forum_client.aclose()
