from __future__ import annotations

from bot.forum_client import ForumClient
from bot.settings import ThresholdsConfig
from db.repositories import Database


class HourlyScanWorker:
    def __init__(self, forum_client: ForumClient, database: Database, thresholds: ThresholdsConfig) -> None:
        self.forum_client = forum_client
        self.database = database
        self.thresholds = thresholds

    async def scan(self) -> list[dict]:
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
            should_trigger = False
            if new_reply_count >= self.thresholds.triggers.hourly_hot_reply_min:
                should_trigger = True
            elif new_reply_count >= self.thresholds.triggers.hourly_new_reply_min and not self.database.has_replied_in_topic(topic_id):
                should_trigger = True
            if not should_trigger:
                self.database.note_topic_seen(topic_id, highest_post_number)
                continue
            event = {
                "topic_id": topic_id,
                "reason": "hourly_scan",
                "source": "hourly_scan_worker",
                "highest_seen_post_number": highest_post_number,
                "new_reply_count": new_reply_count,
            }
            if self.database.record_trigger_event(event):
                events.append(event)
            else:
                self.database.note_topic_seen(topic_id, highest_post_number)
        return events
