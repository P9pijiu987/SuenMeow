import pytest
from typing import cast

from db.repositories import Database
from bot.notification_worker import NotificationWorker
from bot.forum_client import ForumClient
from bot.forum_client import UnreadNotification


class FakeForumClient:
    def __init__(self, notifications: list[UnreadNotification]) -> None:
        self.notifications: list[UnreadNotification] = notifications
        self.marked: list[int] = []

    async def get_unread_notifications(self) -> list[UnreadNotification]:
        return self.notifications

    async def mark_notification_read(self, notification_id: int) -> None:
        self.marked.append(notification_id)


class FakeDatabase:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.result_statuses: list[str] = []

    def is_topic_banned(self, topic_id: int) -> bool:
        _ = topic_id
        return False

    def record_trigger_event(self, event: dict[str, object]) -> object:
        self.events.append(event)
        status = self.result_statuses.pop(0) if self.result_statuses else "created"

        class _Result:
            def __init__(self, current_status: str) -> None:
                self.status = current_status

        return _Result(status)


class FailingForumClient:
    def __init__(self) -> None:
        self.marked: list[int] = []

    async def get_unread_notifications(self) -> list[UnreadNotification]:
        raise RuntimeError("notifications fetch failed")

    async def mark_notification_read(self, notification_id: int) -> None:
        self.marked.append(notification_id)


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
    worker = NotificationWorker(
        cast(ForumClient, cast(object, forum_client)),
        cast(Database, cast(object, database)),
        mark_notifications_read=False,
    )

    events = await worker.scan()

    assert len(events) == 1
    assert forum_client.marked == []
    assert events[0]["reason"] == "notification"


@pytest.mark.anyio
async def test_notification_worker_skips_low_value_notifications() -> None:
    forum_client = FakeForumClient(
        [
            UnreadNotification(notification_id=1, topic_id=2, notification_type="liked", is_direct_trigger=False, raw={}),
            UnreadNotification(notification_id=2, topic_id=2, notification_type="group_mentioned", is_direct_trigger=False, raw={}),
        ]
    )
    database = FakeDatabase()
    worker = NotificationWorker(
        cast(ForumClient, cast(object, forum_client)),
        cast(Database, cast(object, database)),
        mark_notifications_read=False,
    )

    events = await worker.scan()

    assert events == []
    assert database.events == []


@pytest.mark.anyio
async def test_notification_worker_keeps_merged_direct_notifications() -> None:
    forum_client = FakeForumClient(
        [
            UnreadNotification(notification_id=1, topic_id=2, notification_type="mentioned", is_direct_trigger=True, raw={}),
            UnreadNotification(notification_id=2, topic_id=2, notification_type="replied", is_direct_trigger=True, raw={}),
        ]
    )
    database = FakeDatabase()
    database.result_statuses = ["created", "merged"]
    worker = NotificationWorker(
        cast(ForumClient, cast(object, forum_client)),
        cast(Database, cast(object, database)),
        mark_notifications_read=False,
    )

    events = await worker.scan()

    assert len(events) == 2
    assert [event["notification_type"] for event in events] == ["mentioned", "replied"]


@pytest.mark.anyio
async def test_notification_worker_scan_isolates_fetch_failure_and_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    forum_client = FailingForumClient()
    database = FakeDatabase()
    worker = NotificationWorker(
        cast(ForumClient, cast(object, forum_client)),
        cast(Database, cast(object, database)),
        mark_notifications_read=True,
    )

    with caplog.at_level("ERROR"):
        events = await worker.scan()

    assert events == []
    assert database.events == []
    assert "failed to fetch unread notifications" in caplog.text.lower()
