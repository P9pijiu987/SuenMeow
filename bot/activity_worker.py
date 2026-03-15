from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bot.forum_client import ForumClient
from bot.settings import ThresholdsConfig
from db.repositories import Database


class ActivityWorker:
    def __init__(self, forum_client: ForumClient, database: Database, thresholds: ThresholdsConfig) -> None:
        self.forum_client = forum_client
        self.database = database
        self.thresholds = thresholds

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)

    async def scan(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        topics = await self.forum_client.list_latest_topics()
        events: list[dict] = []
        for topic in topics:
            topic_id = int(topic["id"])
            if self.database.is_topic_banned(topic_id):
                continue
            highest_post_number = int(topic.get("highest_post_number") or topic.get("posts_count") or 0)
            state = self.database.get_topic_state(topic_id)
            previous_highest = 0 if state is None else state.highest_seen_post_number
            new_reply_count = max(0, highest_post_number - previous_highest)
            bumped_at = self._parse_datetime(topic.get("bumped_at"))
            if bumped_at is None:
                self.database.note_topic_seen(topic_id, highest_post_number)
                continue
            if now - bumped_at > timedelta(minutes=self.thresholds.triggers.burst_window_minutes):
                self.database.note_topic_seen(topic_id, highest_post_number)
                continue
            if new_reply_count < self.thresholds.triggers.burst_reply_min:
                self.database.note_topic_seen(topic_id, highest_post_number)
                continue
            event = {
                "topic_id": topic_id,
                "reason": "burst_activity",
                "source": "activity_worker",
                "highest_seen_post_number": highest_post_number,
                "new_reply_count": new_reply_count,
            }
            if self.database.record_trigger_event(event):
                events.append(event)
            else:
                self.database.note_topic_seen(topic_id, highest_post_number)
        return events
