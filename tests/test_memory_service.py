from pathlib import Path

from bot.memory_policy import MemoryPersistResult
from bot.memory_policy import SelfMemoryUpdate
from bot.memory_policy import UserMemoryUpdate
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


def test_memory_service_apply_memory_updates_respects_thresholds_and_last_valid_wins(tmp_path: Path) -> None:
    database = Database(tmp_path / "memory_apply.sqlite3")
    database.initialize()
    service = MemoryService(database)
    service.set_user_memory("alice", "old memory", 0.5)
    service.set_self_memory("old self memory")

    result = service.apply_memory_updates(
        user_updates=[
            UserMemoryUpdate(username="alice", memory_text="first update", confidence=0.8),
            UserMemoryUpdate(username="alice", memory_text="second update", confidence=0.9),
            UserMemoryUpdate(username="bob", memory_text="low confidence", confidence=0.1),
        ],
        self_update=SelfMemoryUpdate(memory_text="updated self memory", confidence=0.7),
        user_confidence_threshold=0.2,
        self_confidence_threshold=0.6,
    )

    assert result == MemoryPersistResult(updated_user_count=1, self_memory_updated=True)
    assert service.get_user_memory(["alice", "bob"]) == {
        "alice": "second update",
        "bob": "",
    }
    assert service.get_self_memory() == "updated self memory"


def test_memory_service_admin_setters_keep_current_write_contract(tmp_path: Path) -> None:
    database = Database(tmp_path / "memory_admin.sqlite3")
    database.initialize()
    service = MemoryService(database)

    assert service.set_self_memory_from_admin("  keep   spacing  ") == "  keep   spacing  "
    assert service.get_self_memory() == "  keep   spacing  "

    assert service.set_user_memory_from_admin("alice", "  prefers   fish  ") == "  prefers   fish  "
    listed = service.list_user_memories(limit=10)
    assert len(listed) == 1
    assert listed[0]["username"] == "alice"
    assert listed[0]["memory_text"] == "  prefers   fish  "
    assert listed[0]["confidence"] == 0.0
