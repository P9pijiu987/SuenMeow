from __future__ import annotations

from db.repositories import Database

class MemoryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_user_memory(self, usernames: list[str]) -> dict[str, str]:
        stored = self.database.get_user_memories(usernames)
        return {username: stored.get(username, "") for username in usernames}

    def set_user_memory(self, username: str, memory_text: str, confidence: float = 0.0) -> None:
        self.database.upsert_user_memory(username, memory_text, confidence)

    def list_user_memories(self, limit: int = 100) -> list[dict]:
        return self.database.list_user_memories(limit)

    def get_self_memory(self) -> str:
        return self.database.get_self_memory()

    def set_self_memory(self, memory_text: str) -> None:
        self.database.set_self_memory(memory_text)
