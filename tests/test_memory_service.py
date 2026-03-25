from pathlib import Path

from bot.memory_service import MemoryService
from db.repositories import Database


def test_memory_service_roundtrip(tmp_path: Path) -> None:
    database = Database(tmp_path / "memory.sqlite3")
    database.initialize()
    service = MemoryService(database)

    service.set_user_memory("alice", "likes concise answers", 0.8)
    service.set_self_memory("remember to be concise")

    assert service.get_user_memory(["alice", "bob"]) == {
        "alice": "likes concise answers",
        "bob": "",
    }
    assert service.get_self_memory() == "remember to be concise"
