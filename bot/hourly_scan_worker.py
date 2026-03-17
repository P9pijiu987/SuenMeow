from __future__ import annotations

import logging

from bot.forum_client import ForumClient
from bot.settings import ThresholdsConfig
from db.repositories import Database


logger = logging.getLogger(__name__)


class HourlyScanWorker:
    def __init__(self, forum_client: ForumClient, database: Database, thresholds: ThresholdsConfig) -> None:
        self.forum_client = forum_client
        self.database = database
        self.thresholds = thresholds

    async def scan(self) -> list[dict]:
        logger.info("hourly scan worker skipped: hot-topic triggering is handled by activity_worker")
        return []
