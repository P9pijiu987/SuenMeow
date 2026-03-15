from pathlib import Path
from typing import cast

from db.repositories import Database


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
    assert database.record_trigger_event(event) is True
    assert database.record_trigger_event(event) is False

    pending = database.list_unprocessed_events()
    assert len(pending) == 1
    assert pending[0]["payload"]["topic_id"] == 7

    recent = database.list_recent_trigger_events()
    assert len(recent) == 1
    assert recent[0]["reason"] == "notification"

    database.mark_event_processed(int(pending[0]["id"]))
    assert database.list_unprocessed_events() == []


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

    assert database.record_trigger_event(event) is True
    assert database.record_trigger_event(event) is False

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
