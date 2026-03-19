from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.schema import SCHEMA_STATEMENTS


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TopicState:
    topic_id: int
    highest_seen_post_number: int
    highest_replied_post_number: int
    last_triggered_at: str | None
    last_replied_at: str | None
    summary_cache: str | None
    updated_at: str


@dataclass(slots=True)
class PipelineRun:
    id: int
    event_id: int | None
    topic_id: int
    topic_title: str
    trigger_reason: str
    action: str
    decision_json: str
    draft_content: str
    created_at: str


@dataclass(slots=True)
class PendingReply:
    id: int
    topic_id: int
    topic_title: str
    trigger_reason: str
    target_post_number: int | None
    draft_content: str
    decision_json: str
    status: str
    reply_post_id: int | None
    error_text: str | None
    approved_at: str | None
    sent_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class TriggerEventRecordResult:
    status: str
    event_id: int | None = None
    merged_into_event_id: int | None = None


class Database:
    CONNECT_TIMEOUT_SECONDS = 5.0
    BUSY_TIMEOUT_MILLISECONDS = 5000
    INIT_MAX_ATTEMPTS = 3
    INIT_RETRY_BACKOFF_SECONDS = 0.2

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.CONNECT_TIMEOUT_SECONDS)
        connection.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MILLISECONDS}")
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        for attempt in range(1, self.INIT_MAX_ATTEMPTS + 1):
            try:
                with self.connect() as connection:
                    for statement in SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    self._apply_runtime_migrations(connection)
                    connection.commit()
                return
            except sqlite3.OperationalError as exc:
                if not self._is_transient_init_error(exc) or attempt >= self.INIT_MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Failed to initialize SQLite database at {self.path} "
                        f"after {attempt} attempt(s): {exc}"
                    ) from exc
                time.sleep(self.INIT_RETRY_BACKOFF_SECONDS * attempt)

    @staticmethod
    def _is_transient_init_error(error: sqlite3.OperationalError) -> bool:
        message = str(error).lower()
        transient_markers = (
            "database is locked",
            "database schema is locked",
            "database table is locked",
            "database is busy",
        )
        return any(marker in message for marker in transient_markers)

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _apply_runtime_migrations(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(trigger_events)").fetchall()
        }
        if "failure_count" not in columns:
            connection.execute(
                "ALTER TABLE trigger_events ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"
            )
        if "last_error_text" not in columns:
            connection.execute("ALTER TABLE trigger_events ADD COLUMN last_error_text TEXT")
        if "last_attempted_at" not in columns:
            connection.execute("ALTER TABLE trigger_events ADD COLUMN last_attempted_at TEXT")

    def is_topic_banned(self, topic_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM ban_rules WHERE topic_id = ?", (topic_id,)).fetchone()
        return row is not None

    def add_topic_ban(self, topic_id: int, reason: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO ban_rules(topic_id, reason) VALUES(?, ?)",
                (topic_id, reason),
            )
            connection.commit()

    def list_banned_topics(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT topic_id, reason, created_at FROM ban_rules ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_topic_state(self, topic_id: int) -> TopicState | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT topic_id, highest_seen_post_number, highest_replied_post_number,
                       last_triggered_at, last_replied_at, summary_cache, updated_at
                FROM topic_state
                WHERE topic_id = ?
                """,
                (topic_id,),
            ).fetchone()
        if row is None:
            return None
        return TopicState(**dict(row))

    def upsert_topic_state(
        self,
        topic_id: int,
        *,
        highest_seen_post_number: int | None = None,
        highest_replied_post_number: int | None = None,
        last_triggered_at: str | None = None,
        last_replied_at: str | None = None,
        summary_cache: str | None = None,
    ) -> None:
        current = self.get_topic_state(topic_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO topic_state(
                    topic_id,
                    highest_seen_post_number,
                    highest_replied_post_number,
                    last_triggered_at,
                    last_replied_at,
                    summary_cache,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    highest_seen_post_number = excluded.highest_seen_post_number,
                    highest_replied_post_number = excluded.highest_replied_post_number,
                    last_triggered_at = excluded.last_triggered_at,
                    last_replied_at = excluded.last_replied_at,
                    summary_cache = excluded.summary_cache,
                    updated_at = excluded.updated_at
                """,
                (
                    topic_id,
                    highest_seen_post_number if highest_seen_post_number is not None else (current.highest_seen_post_number if current else 0),
                    highest_replied_post_number if highest_replied_post_number is not None else (current.highest_replied_post_number if current else 0),
                    last_triggered_at if last_triggered_at is not None else (current.last_triggered_at if current else None),
                    last_replied_at if last_replied_at is not None else (current.last_replied_at if current else None),
                    summary_cache if summary_cache is not None else (current.summary_cache if current else None),
                    self._utcnow(),
                ),
            )
            connection.commit()

    def note_topic_seen(self, topic_id: int, highest_seen_post_number: int) -> None:
        current = self.get_topic_state(topic_id)
        if current is not None and current.highest_seen_post_number >= highest_seen_post_number:
            return
        self.upsert_topic_state(topic_id, highest_seen_post_number=highest_seen_post_number)

    def note_topic_triggered(self, topic_id: int, highest_seen_post_number: int | None = None) -> None:
        self.upsert_topic_state(
            topic_id,
            highest_seen_post_number=highest_seen_post_number,
            last_triggered_at=self._utcnow(),
        )

    def record_reply(self, topic_id: int, content: str, reply_post_id: int | None, reply_to_post_number: int | None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reply_history(topic_id, reply_post_id, reply_to_post_number, content)
                VALUES(?, ?, ?, ?)
                """,
                (topic_id, reply_post_id, reply_to_post_number, content),
            )
            connection.commit()
        highest_replied_post_number = reply_to_post_number or 0
        self.upsert_topic_state(
            topic_id,
            highest_replied_post_number=highest_replied_post_number,
            last_replied_at=self._utcnow(),
        )

    def create_pending_reply(
        self,
        *,
        topic_id: int,
        topic_title: str,
        trigger_reason: str,
        target_post_number: int | None,
        draft_content: str,
        decision: dict[str, Any],
    ) -> int:
        if self.has_pending_reply_for_topic(topic_id):
            raise RuntimeError(f"pending reply already exists for topic {topic_id}")
        now = self._utcnow()
        with self.connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO pending_replies(
                        topic_id,
                        topic_title,
                        trigger_reason,
                        target_post_number,
                        draft_content,
                        decision_json,
                        status,
                        updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        topic_id,
                        topic_title,
                        trigger_reason,
                        target_post_number,
                        draft_content,
                        json.dumps(decision, ensure_ascii=False),
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError(f"pending reply already exists for topic {topic_id}") from exc
            connection.commit()
        pending_reply_id = cursor.lastrowid
        if pending_reply_id is None:
            raise RuntimeError("failed to create pending reply")
        return int(pending_reply_id)

    def get_pending_reply(self, pending_reply_id: int) -> PendingReply | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, topic_id, topic_title, trigger_reason, target_post_number,
                       draft_content, decision_json, status, reply_post_id, error_text,
                       approved_at, sent_at, created_at, updated_at
                FROM pending_replies
                WHERE id = ?
                """,
                (pending_reply_id,),
            ).fetchone()
        if row is None:
            return None
        return PendingReply(**dict(row))

    def has_pending_reply_for_topic(self, topic_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM pending_replies WHERE topic_id = ? AND status = 'pending' LIMIT 1",
                (topic_id,),
            ).fetchone()
        return row is not None

    def list_pending_replies(self, *, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        where_clause = "WHERE status = ?" if status is not None else ""
        params: tuple[Any, ...] = (status, limit) if status is not None else (limit,)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, topic_id, topic_title, trigger_reason, target_post_number,
                       draft_content, decision_json, status, reply_post_id, error_text,
                       approved_at, sent_at, created_at, updated_at
                FROM pending_replies
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["decision"] = json.loads(item.pop("decision_json"))
            items.append(item)
        return items

    def mark_pending_reply_sent(self, pending_reply_id: int, reply_post_id: int | None) -> None:
        now = self._utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pending_replies
                SET status = 'sent',
                    reply_post_id = ?,
                    error_text = NULL,
                    approved_at = ?,
                    sent_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (reply_post_id, now, now, now, pending_reply_id),
            )
            connection.commit()

    def mark_pending_reply_error(self, pending_reply_id: int, error_text: str) -> None:
        now = self._utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pending_replies
                SET status = 'error',
                    error_text = ?,
                    approved_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_text, now, now, pending_reply_id),
            )
            connection.commit()

    def mark_pending_reply_rejected(self, pending_reply_id: int) -> None:
        now = self._utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pending_replies
                SET status = 'rejected',
                    error_text = NULL,
                    approved_at = NULL,
                    sent_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, pending_reply_id),
            )
            connection.commit()

    def has_replied_in_topic(self, topic_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM reply_history WHERE topic_id = ? LIMIT 1",
                (topic_id,),
            ).fetchone()
        return row is not None

    def set_scan_cursor(self, cursor_name: str, cursor_value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_cursors(cursor_name, cursor_value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(cursor_name) DO UPDATE SET
                    cursor_value = excluded.cursor_value,
                    updated_at = excluded.updated_at
                """,
                (cursor_name, cursor_value, self._utcnow()),
            )
            connection.commit()

    def get_scan_cursor(self, cursor_name: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cursor_value FROM scan_cursors WHERE cursor_name = ?",
                (cursor_name,),
            ).fetchone()
        return None if row is None else str(row["cursor_value"])

    def list_unprocessed_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, topic_id, reason, source, dedupe_key, payload_json,
                       failure_count, last_error_text, last_attempted_at, created_at, processed_at
                FROM trigger_events
                WHERE processed_at IS NULL
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            events.append(item)
        return events

    def mark_event_processed(self, event_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE trigger_events SET processed_at = ? WHERE id = ?",
                (self._utcnow(), event_id),
            )
            connection.commit()

    def record_event_failure(self, event_id: int, error_text: str) -> int:
        now = self._utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE trigger_events
                SET failure_count = failure_count + 1,
                    last_error_text = ?,
                    last_attempted_at = ?
                WHERE id = ?
                """,
                (error_text, now, event_id),
            )
            row = connection.execute(
                "SELECT failure_count FROM trigger_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            connection.commit()
        if row is None:
            raise KeyError(f"trigger event not found: {event_id}")
        return int(row["failure_count"])

    def get_user_memories(self, usernames: list[str]) -> dict[str, str]:
        if not usernames:
            return {}
        placeholders = ", ".join("?" for _ in usernames)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT username, memory_text FROM user_memories WHERE username IN ({placeholders})",
                tuple(usernames),
            ).fetchall()
        return {str(row["username"]): str(row["memory_text"]) for row in rows}

    def upsert_user_memory(self, username: str, memory_text: str, confidence: float = 0.0) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO users(username) VALUES(?)",
                (username,),
            )
            connection.execute(
                """
                INSERT INTO user_memories(username, memory_text, confidence, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    memory_text = excluded.memory_text,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (username, memory_text, confidence, self._utcnow()),
            )
            connection.commit()

    def list_user_memories(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT username, memory_text, confidence, updated_at FROM user_memories ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_self_memory(self, key: str = "long_term") -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT memory_text FROM self_memories WHERE key = ?",
                (key,),
            ).fetchone()
        return "" if row is None else str(row["memory_text"])

    def set_self_memory(self, memory_text: str, key: str = "long_term") -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO self_memories(key, memory_text, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    memory_text = excluded.memory_text,
                    updated_at = excluded.updated_at
                """,
                (key, memory_text, self._utcnow()),
            )
            connection.commit()

    def record_pipeline_run(
        self,
        *,
        event_id: int | None,
        topic_id: int,
        topic_title: str,
        trigger_reason: str,
        action: str,
        decision: dict[str, Any],
        draft_content: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO pipeline_runs(event_id, topic_id, topic_title, trigger_reason, action, decision_json, draft_content)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    topic_id,
                    topic_title,
                    trigger_reason,
                    action,
                    json.dumps(decision, ensure_ascii=False),
                    draft_content,
                ),
            )
            connection.commit()

    def list_recent_pipeline_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, event_id, topic_id, topic_title, trigger_reason, action, decision_json, draft_content, created_at
                FROM pipeline_runs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["decision"] = json.loads(item.pop("decision_json"))
            items.append(item)
        return items

    def get_pipeline_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, event_id, topic_id, topic_title, trigger_reason, action, decision_json, draft_content, created_at
                FROM pipeline_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["decision"] = json.loads(item.pop("decision_json"))
        return item

    def list_recent_trigger_events(self, limit: int = 50, include_processed: bool = True) -> list[dict[str, Any]]:
        where_clause = "" if include_processed else "WHERE processed_at IS NULL"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, topic_id, reason, source, dedupe_key, payload_json,
                       failure_count, last_error_text, last_attempted_at, created_at, processed_at
                FROM trigger_events
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            items.append(item)
        return items

    def list_topic_states(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT topic_id, highest_seen_post_number, highest_replied_post_number,
                       last_triggered_at, last_replied_at, summary_cache, updated_at
                FROM topic_state
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _build_trigger_dedupe_key(event: dict[str, object]) -> str:
        source_value = event["source"]
        source = source_value if isinstance(source_value, str) else str(source_value)
        reason_value = event["reason"]
        reason = reason_value if isinstance(reason_value, str) else str(reason_value)
        topic_id_value = event["topic_id"]
        topic_id = topic_id_value if isinstance(topic_id_value, int) else int(str(topic_id_value))
        return f"{source}:{reason}:{topic_id}:{event.get('notification_id', '')}"

    @staticmethod
    def _trigger_summary(payload: dict[str, object]) -> dict[str, object]:
        summary: dict[str, object] = {
            "source": payload.get("source"),
            "reason": payload.get("reason"),
            "notification_id": payload.get("notification_id"),
            "notification_type": payload.get("notification_type"),
            "target_post_number": payload.get("target_post_number"),
            "reply_count_1h": payload.get("reply_count_1h"),
        }
        return {key: value for key, value in summary.items() if value is not None}

    def _normalize_trigger_payload(self, event: dict[str, object]) -> dict[str, object]:
        payload = dict(event)
        payload.setdefault("merged_event_count", 1)
        payload.setdefault("merged_events", [self._trigger_summary(payload)])
        return payload

    def _merge_trigger_payload(
        self,
        existing_payload: dict[str, object],
        incoming_event: dict[str, object],
    ) -> dict[str, object]:
        merged_payload = dict(existing_payload)
        merged_payload.update(incoming_event)
        merged_event_count_raw = existing_payload.get("merged_event_count", 1)
        merged_event_count = merged_event_count_raw if isinstance(merged_event_count_raw, int) else 1
        merged_history_raw = existing_payload.get("merged_events", [])
        merged_history = list(merged_history_raw) if isinstance(merged_history_raw, list) else []
        merged_history.append(self._trigger_summary(incoming_event))
        merged_payload["merged_event_count"] = merged_event_count + 1
        merged_payload["merged_events"] = merged_history[-20:]
        return merged_payload

    def _get_active_trigger_event_row(self, connection: sqlite3.Connection, topic_id: int) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT id, topic_id, reason, source, dedupe_key, payload_json
            FROM trigger_events
            WHERE topic_id = ? AND processed_at IS NULL
            LIMIT 1
            """,
            (topic_id,),
        ).fetchone()

    def has_active_trigger_event_for_topic(self, topic_id: int) -> bool:
        with self.connect() as connection:
            return self._get_active_trigger_event_row(connection, topic_id) is not None

    def record_trigger_event(self, event: dict[str, object]) -> TriggerEventRecordResult:
        source_value = event["source"]
        source = source_value if isinstance(source_value, str) else str(source_value)
        reason_value = event["reason"]
        reason = reason_value if isinstance(reason_value, str) else str(reason_value)
        topic_id_value = event["topic_id"]
        topic_id = topic_id_value if isinstance(topic_id_value, int) else int(str(topic_id_value))
        highest_seen_value = event.get("highest_seen_post_number")
        highest_seen_post_number = highest_seen_value if isinstance(highest_seen_value, int) else None
        dedupe_key = self._build_trigger_dedupe_key(event)
        normalized_payload = self._normalize_trigger_payload(event)
        with self.connect() as connection:
            if self.has_pending_reply_for_topic(topic_id):
                logger.info(
                    "trigger event blocked by pending approval; topic_id=%s source=%s reason=%s",
                    topic_id,
                    source,
                    reason,
                )
                return TriggerEventRecordResult(status="blocked_pending")

            active_row = self._get_active_trigger_event_row(connection, topic_id)
            if active_row is not None:
                if str(active_row["dedupe_key"]) == dedupe_key:
                    logger.info(
                        "trigger event duplicate skipped; topic_id=%s event_id=%s source=%s reason=%s",
                        topic_id,
                        active_row["id"],
                        source,
                        reason,
                    )
                    return TriggerEventRecordResult(status="duplicate", event_id=int(active_row["id"]))

                existing_payload_raw = active_row["payload_json"]
                existing_payload = json.loads(existing_payload_raw) if isinstance(existing_payload_raw, str) and existing_payload_raw else {}
                merged_payload = self._merge_trigger_payload(existing_payload, event)
                connection.execute(
                    """
                    UPDATE trigger_events
                    SET reason = ?,
                        source = ?,
                        payload_json = ?
                    WHERE id = ?
                    """,
                    (
                        reason,
                        source,
                        json.dumps(merged_payload, ensure_ascii=False),
                        int(active_row["id"]),
                    ),
                )
                connection.commit()
                self.note_topic_triggered(topic_id, highest_seen_post_number)
                logger.info(
                    "trigger event merged into active topic unit; topic_id=%s event_id=%s source=%s reason=%s",
                    topic_id,
                    active_row["id"],
                    source,
                    reason,
                )
                return TriggerEventRecordResult(
                    status="merged",
                    event_id=int(active_row["id"]),
                    merged_into_event_id=int(active_row["id"]),
                )

            try:
                cursor = connection.execute(
                    "INSERT INTO trigger_events(topic_id, reason, source, dedupe_key, payload_json) VALUES(?, ?, ?, ?, ?)",
                    (
                        topic_id,
                        reason,
                        source,
                        dedupe_key,
                        json.dumps(normalized_payload, ensure_ascii=False),
                    ),
                )
            except sqlite3.IntegrityError:
                active_row = self._get_active_trigger_event_row(connection, topic_id)
                if active_row is not None:
                    existing_payload_raw = active_row["payload_json"]
                    existing_payload = json.loads(existing_payload_raw) if isinstance(existing_payload_raw, str) and existing_payload_raw else {}
                    merged_payload = self._merge_trigger_payload(existing_payload, event)
                    connection.execute(
                        """
                        UPDATE trigger_events
                        SET reason = ?,
                            source = ?,
                            payload_json = ?
                        WHERE id = ?
                        """,
                        (
                            reason,
                            source,
                            json.dumps(merged_payload, ensure_ascii=False),
                            int(active_row["id"]),
                        ),
                    )
                    connection.commit()
                    self.note_topic_triggered(topic_id, highest_seen_post_number)
                    return TriggerEventRecordResult(
                        status="merged",
                        event_id=int(active_row["id"]),
                        merged_into_event_id=int(active_row["id"]),
                    )
                duplicate_row = connection.execute(
                    "SELECT id FROM trigger_events WHERE dedupe_key = ? LIMIT 1",
                    (dedupe_key,),
                ).fetchone()
                if duplicate_row is not None:
                    logger.info(
                        "trigger event duplicate skipped after integrity check; topic_id=%s event_id=%s source=%s reason=%s",
                        topic_id,
                        duplicate_row["id"],
                        source,
                        reason,
                    )
                    return TriggerEventRecordResult(status="duplicate", event_id=int(duplicate_row["id"]))
                raise

            connection.commit()
            event_id = cursor.lastrowid
        self.note_topic_triggered(topic_id, highest_seen_post_number)
        logger.info(
            "trigger event created; topic_id=%s event_id=%s source=%s reason=%s",
            topic_id,
            event_id,
            source,
            reason,
        )
        return TriggerEventRecordResult(status="created", event_id=int(event_id) if event_id is not None else None)
