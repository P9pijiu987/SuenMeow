import pytest

from bot.notification_worker import NotificationWorker
from bot.forum_client import UnreadNotification


class FakeForumClient:
    def __init__(self, notifications):
        self.notifications = notifications
        self.marked = []

    async def get_unread_notifications(self):
        return self.notifications

    async def mark_notification_read(self, notification_id: int):
        self.marked.append(notification_id)


class FakeDatabase:
    def __init__(self):
        self.events = []

    def is_topic_banned(self, topic_id: int) -> bool:
        return False

    def record_trigger_event(self, event: dict) -> bool:
        self.events.append(event)
        return True


@pytest.mark.anyio
async def test_notification_worker_respects_mark_read_flag() -> None:
    forum_client = FakeForumClient(
        [
            UnreadNotification(
                notification_id=1,
                topic_id=2,
                notification_type="mentioned",
                is_direct_trigger=True,
                raw={},
            )
        ]
    )
    database = FakeDatabase()
    worker = NotificationWorker(forum_client, database, mark_notifications_read=False)

    events = await worker.scan()

    assert len(events) == 1
    assert forum_client.marked == []
    assert events[0]["reason"] == "notification"
