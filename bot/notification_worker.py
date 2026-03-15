from __future__ import annotations

from bot.forum_client import ForumClient
from db.repositories import Database


class NotificationWorker:
    def __init__(self, forum_client: ForumClient, database: Database, *, mark_notifications_read: bool) -> None:
        self.forum_client = forum_client
        self.database = database
        self.mark_notifications_read = mark_notifications_read

    async def _maybe_mark_read(self, notification_id: int) -> None:
        if not self.mark_notifications_read:
            return
        await self.forum_client.mark_notification_read(notification_id)

    async def scan(self) -> list[dict]:
        notifications = await self.forum_client.get_unread_notifications()
        events: list[dict] = []
        for notification in notifications:
            if notification.topic_id is None:
                await self._maybe_mark_read(notification.notification_id)
                continue
            if self.database.is_topic_banned(notification.topic_id):
                await self._maybe_mark_read(notification.notification_id)
                continue
            trigger_reason = f"notification:{notification.notification_type}"
            if notification.is_direct_trigger:
                trigger_reason = "notification"
            event = {
                "topic_id": notification.topic_id,
                "reason": trigger_reason,
                "source": "notification_worker",
                "notification_id": notification.notification_id,
                "notification_type": notification.notification_type,
                "is_direct_trigger": notification.is_direct_trigger,
            }
            if self.database.record_trigger_event(event):
                events.append(event)
            await self._maybe_mark_read(notification.notification_id)
        return events
