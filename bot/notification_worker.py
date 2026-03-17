from __future__ import annotations

import logging

from bot.forum_client import ForumClient
from db.repositories import Database


logger = logging.getLogger(__name__)


class NotificationWorker:
    def __init__(self, forum_client: ForumClient, database: Database, *, mark_notifications_read: bool) -> None:
        self.forum_client: ForumClient = forum_client
        self.database: Database = database
        self.mark_notifications_read: bool = mark_notifications_read

    async def _maybe_mark_read(self, notification_id: int) -> None:
        if not self.mark_notifications_read:
            return
        await self.forum_client.mark_notification_read(notification_id)

    async def scan(self) -> list[dict[str, object]]:
        try:
            notifications = await self.forum_client.get_unread_notifications()
        except Exception:
            logger.exception("Failed to fetch unread notifications; skipping this scan pass")
            return []
        events: list[dict[str, object]] = []
        for notification in notifications:
            if notification.topic_id is None:
                logger.info(
                    "notification skipped: no topic_id; notification_id=%s type=%s",
                    notification.notification_id,
                    notification.notification_type,
                )
                await self._maybe_mark_read(notification.notification_id)
                continue
            if self.database.is_topic_banned(notification.topic_id):
                logger.info(
                    "notification skipped: topic banned; topic_id=%s notification_id=%s type=%s",
                    notification.topic_id,
                    notification.notification_id,
                    notification.notification_type,
                )
                await self._maybe_mark_read(notification.notification_id)
                continue
            if not notification.is_direct_trigger:
                logger.info(
                    "notification skipped: low-value notification; topic_id=%s notification_id=%s type=%s",
                    notification.topic_id,
                    notification.notification_id,
                    notification.notification_type,
                )
                await self._maybe_mark_read(notification.notification_id)
                continue
            event: dict[str, object] = {
                "topic_id": notification.topic_id,
                "reason": "notification",
                "source": "notification_worker",
                "notification_id": notification.notification_id,
                "notification_type": notification.notification_type,
                "is_direct_trigger": notification.is_direct_trigger,
            }
            record_result = self.database.record_trigger_event(event)
            if getattr(record_result, "status", None) in {"created", "merged"}:
                events.append(event)
            logger.info(
                "notification evaluated; topic_id=%s notification_id=%s type=%s source=notification_worker result=%s",
                notification.topic_id,
                notification.notification_id,
                notification.notification_type,
                getattr(record_result, "status", record_result),
            )
            await self._maybe_mark_read(notification.notification_id)
        return events
