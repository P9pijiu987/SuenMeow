from __future__ import annotations

from bot.memory_policy import MemoryPersistResult
from bot.memory_policy import SelfMemoryUpdate
from bot.memory_policy import UserMemoryUpdate
from bot.memory_policy import select_user_memory_updates
from bot.memory_policy import should_update_self_memory
from db.repositories import Database


class MemoryService:
    def __init__(self, database: Database) -> None:
        self.database: Database = database

    def get_user_memory(self, usernames: list[str]) -> dict[str, str]:
        stored = self.database.get_user_memories(usernames)
        return {username: stored.get(username, "") for username in usernames}

    def set_user_memory(self, username: str, memory_text: str, confidence: float = 0.0) -> None:
        self.database.upsert_user_memory(username, memory_text, confidence)

    def list_user_memories(self, limit: int = 100) -> list[dict[str, object]]:
        return self.database.list_user_memories(limit)

    def get_self_memory(self) -> str:
        return self.database.get_self_memory()

    def set_self_memory(self, memory_text: str) -> None:
        self.database.set_self_memory(memory_text)

    def apply_memory_updates(
        self,
        *,
        user_updates: list[UserMemoryUpdate],
        self_update: SelfMemoryUpdate | None,
        user_confidence_threshold: float,
        self_confidence_threshold: float,
    ) -> MemoryPersistResult:
        current_user_memories = self.get_user_memory([update.username for update in user_updates])
        selected_user_updates = select_user_memory_updates(
            user_updates,
            current_user_memories,
            confidence_threshold=user_confidence_threshold,
        )

        updated_user_count = 0
        for update in selected_user_updates.values():
            self.set_user_memory(update.username, update.memory_text, update.confidence)
            updated_user_count += 1

        current_self_memory = self.get_self_memory()
        self_memory_updated = should_update_self_memory(
            self_update,
            current_self_memory=current_self_memory,
            confidence_threshold=self_confidence_threshold,
        )
        if self_memory_updated and self_update is not None:
            self.set_self_memory(self_update.memory_text)

        return MemoryPersistResult(
            updated_user_count=updated_user_count,
            self_memory_updated=self_memory_updated,
        )

    def set_self_memory_from_admin(self, memory_text: str) -> str:
        self.set_self_memory(memory_text)
        return self.get_self_memory()

    def set_user_memory_from_admin(self, username: str, memory_text: str) -> str:
        self.set_user_memory(username, memory_text)
        return memory_text
