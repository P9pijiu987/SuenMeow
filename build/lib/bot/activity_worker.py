from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from bot.forum_client import ForumClient
from bot.settings import ThresholdsConfig
from db.repositories import Database


logger = logging.getLogger(__name__)


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

    async def scan(self) -> list[dict[str, object]]:
        now = datetime.now(timezone.utc)
        topics = await self.forum_client.list_latest_topics()
        events: list[dict[str, object]] = []
        for topic in topics:
            topic_id = int(topic["id"])
            if self.database.is_topic_banned(topic_id):
                continue
            highest_post_number = int(topic.get("highest_post_number") or topic.get("posts_count") or 0)
            state = self.database.get_topic_state(topic_id)
            previous_highest = 0 if state is None else state.highest_seen_post_number
            new_reply_count = max(0, highest_post_number - previous_highest)
            if highest_post_number <= previous_highest:
                logger.info(
                    "hot topic skipped: no new posts since last scan; topic_id=%s highest_seen=%s",
                    topic_id,
                    highest_post_number,
                )
                continue

            posts = await self.forum_client.get_topic_selected_posts(topic_id, recent_post_limit=50)
            recent_reply_count = 0
            for post in posts:
                post_number_raw = post.get("post_number")
                post_number = post_number_raw if isinstance(post_number_raw, int) else int(post_number_raw or 0)
                if post_number <= 1:
                    continue
                created_at = self._parse_datetime(post.get("created_at") if isinstance(post, dict) else None)
                if created_at is None:
                    continue
                if now - created_at <= timedelta(hours=1):
                    recent_reply_count += 1

            if recent_reply_count <= self.thresholds.triggers.hourly_hot_reply_min:
                self.database.note_topic_seen(topic_id, highest_post_number)
                logger.info(
                    "hot topic skipped: threshold not met; topic_id=%s reply_count_1h=%s threshold=%s source=activity_worker",
                    topic_id,
                    recent_reply_count,
                    self.thresholds.triggers.hourly_hot_reply_min,
                )
                continue

            event: dict[str, object] = {
                "topic_id": topic_id,
                "reason": "hot_topic",
                "source": "activity_worker",
                "highest_seen_post_number": highest_post_number,
                "new_reply_count": new_reply_count,
                "reply_count_1h": recent_reply_count,
            }
            record_result = self.database.record_trigger_event(event)
            status = getattr(record_result, "status", record_result)
            if status in {"created", "merged"}:
                events.append(event)
            else:
                self.database.note_topic_seen(topic_id, highest_post_number)
            logger.info(
                "hot topic evaluated; topic_id=%s source=activity_worker result=%s reply_count_1h=%s new_reply_count=%s",
                topic_id,
                status,
                recent_reply_count,
                new_reply_count,
            )
        return events
