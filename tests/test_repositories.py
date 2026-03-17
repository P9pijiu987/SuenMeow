from pathlib import Path
import sqlite3
from typing import cast

import pytest

from db.repositories import Database
from db.repositories import TriggerEventRecordResult


def test_topic_state_and_cursor_roundtrip(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()

    database.note_topic_seen(123, 9)
    state = database.get_topic_state(123)
    assert state is not None
    assert state.highest_seen_post_number == 9

    database.set_scan_cursor("latest_page", "2")
    assert database.get_scan_cursor("latest_page") == "2"


def test_trigger_event_dedup_and_processing(tmp_path: Path) -> None:
    database = Database(tmp_path / "events.sqlite3")
    database.initialize()

    event = cast(
        dict[str, object],
        {
            "topic_id": 7,
            "reason": "notification",
            "source": "notification_worker",
            "notification_id": 99,
        },
    )
    first = database.record_trigger_event(event)
    second = database.record_trigger_event(event)
    assert isinstance(first, TriggerEventRecordResult)
    assert first.status == "created"
    assert second.status == "duplicate"

    pending = database.list_unprocessed_events()
    assert len(pending) == 1
    assert pending[0]["payload"]["topic_id"] == 7

    recent = database.list_recent_trigger_events()
    assert len(recent) == 1
    assert recent[0]["reason"] == "notification"
    assert recent[0]["failure_count"] == 0
    assert recent[0]["last_error_text"] is None
    assert recent[0]["last_attempted_at"] is None

    database.mark_event_processed(int(pending[0]["id"]))
    assert database.list_unprocessed_events() == []


def test_record_event_failure_updates_failure_metadata(tmp_path: Path) -> None:
    database = Database(tmp_path / "events-failure.sqlite3")
    database.initialize()

    event = cast(
        dict[str, object],
        {
            "topic_id": 7,
            "reason": "notification",
            "source": "notification_worker",
            "notification_id": 99,
        },
    )
    assert database.record_trigger_event(event).status == "created"
    event_id = int(database.list_unprocessed_events()[0]["id"])

    failure_count = database.record_event_failure(event_id, "HTTP 422 response body: too short")

    assert failure_count == 1
    recent = database.list_recent_trigger_events(limit=1)
    assert recent[0]["failure_count"] == 1
    assert recent[0]["last_error_text"] == "HTTP 422 response body: too short"
    assert recent[0]["last_attempted_at"] is not None


def test_trigger_event_dedup_is_atomic_with_insert_or_ignore(tmp_path: Path) -> None:
    database = Database(tmp_path / "events-atomic.sqlite3")
    database.initialize()

    event = cast(
        dict[str, object],
        {
            "topic_id": 7,
            "reason": "notification",
            "source": "notification_worker",
            "notification_id": 99,
        },
    )

    assert database.record_trigger_event(event).status == "created"
    assert database.record_trigger_event(event).status == "duplicate"

    recent = database.list_recent_trigger_events()
    assert len(recent) == 1
    assert recent[0]["payload"]["notification_id"] == 99


def test_pending_reply_roundtrip(tmp_path: Path) -> None:
    database = Database(tmp_path / "pending.sqlite3")
    database.initialize()

    pending_reply_id = database.create_pending_reply(
        topic_id=7,
        topic_title="topic",
        trigger_reason="notification",
        target_post_number=3,
        draft_content="meow",
        decision={"should_reply": True, "reason": "test"},
    )

    pending = database.get_pending_reply(pending_reply_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.target_post_number == 3

    listed = database.list_pending_replies()
    assert len(listed) == 1
    assert listed[0]["decision"]["should_reply"] is True

    database.mark_pending_reply_sent(pending_reply_id, reply_post_id=99)
    sent = database.get_pending_reply(pending_reply_id)
    assert sent is not None
    assert sent.status == "sent"
    assert sent.reply_post_id == 99


def test_trigger_event_merges_same_topic_different_notifications(tmp_path: Path) -> None:
    database = Database(tmp_path / "merge.sqlite3")
    database.initialize()

    first = database.record_trigger_event(
        cast(
            dict[str, object],
            {"topic_id": 7, "reason": "notification", "source": "notification_worker", "notification_id": 99},
        )
    )
    second = database.record_trigger_event(
        cast(
            dict[str, object],
            {"topic_id": 7, "reason": "notification", "source": "notification_worker", "notification_id": 100},
        )
    )

    assert first.status == "created"
    assert second.status == "merged"
    pending = database.list_unprocessed_events()
    assert len(pending) == 1
    assert pending[0]["payload"]["merged_event_count"] == 2


def test_trigger_event_blocked_when_topic_has_pending_reply(tmp_path: Path) -> None:
    database = Database(tmp_path / "pending-block.sqlite3")
    database.initialize()
    _ = database.create_pending_reply(
        topic_id=7,
        topic_title="topic",
        trigger_reason="notification",
        target_post_number=3,
        draft_content="meow",
        decision={"should_reply": True, "reason": "test"},
    )

    result = database.record_trigger_event(
        cast(
            dict[str, object],
            {"topic_id": 7, "reason": "notification", "source": "notification_worker", "notification_id": 99},
        )
    )

    assert result.status == "blocked_pending"
    assert database.list_unprocessed_events() == []


def test_create_pending_reply_rejects_second_pending_for_same_topic(tmp_path: Path) -> None:
    database = Database(tmp_path / "pending-unique.sqlite3")
    database.initialize()
    _ = database.create_pending_reply(
        topic_id=7,
        topic_title="topic",
        trigger_reason="notification",
        target_post_number=3,
        draft_content="meow",
        decision={"should_reply": True, "reason": "test"},
    )

    with pytest.raises(RuntimeError, match="pending reply already exists"):
        _ = database.create_pending_reply(
            topic_id=7,
            topic_title="topic",
            trigger_reason="notification",
            target_post_number=4,
            draft_content="meow2",
            decision={"should_reply": True, "reason": "test2"},
        )


def test_initialize_retries_after_transient_sqlite_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "retry-init.sqlite3")
    original_connect = database.connect
    attempts = {"count": 0}

    def flaky_connect() -> sqlite3.Connection:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return original_connect()

    monkeypatch.setattr(database, "connect", flaky_connect)
    database.initialize()

    assert attempts["count"] == 3
    with original_connect() as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='topic_state'"
        ).fetchone()
    assert row is not None


def test_initialize_raises_clear_error_after_bounded_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "fail-init.sqlite3")

    def failing_connect() -> sqlite3.Connection:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "connect", failing_connect)

    with pytest.raises(RuntimeError, match="Failed to initialize SQLite database") as exc_info:
        database.initialize()
    assert "fail-init.sqlite3" in str(exc_info.value)


def test_initialize_retries_after_schema_lock_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "schema-lock.sqlite3")
    original_connect = database.connect
    attempts = {"count": 0}

    def flaky_connect() -> sqlite3.Connection:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("database schema is locked: main")
        return original_connect()

    monkeypatch.setattr(database, "connect", flaky_connect)

    database.initialize()

    assert attempts["count"] == 2
