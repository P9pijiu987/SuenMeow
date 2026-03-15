from __future__ import annotations

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS topic_state (
        topic_id INTEGER PRIMARY KEY,
        highest_seen_post_number INTEGER NOT NULL DEFAULT 0,
        highest_replied_post_number INTEGER NOT NULL DEFAULT 0,
        last_triggered_at TEXT,
        last_replied_at TEXT,
        summary_cache TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trigger_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        source TEXT NOT NULL,
        dedupe_key TEXT NOT NULL UNIQUE,
        payload_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        processed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reply_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER NOT NULL,
        reply_post_id INTEGER,
        reply_to_post_number INTEGER,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ban_rules (
        topic_id INTEGER PRIMARY KEY,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_cursors (
        cursor_name TEXT PRIMARY KEY,
        cursor_value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_memories (
        username TEXT PRIMARY KEY,
        memory_text TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(username) REFERENCES users(username)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS self_memories (
        key TEXT PRIMARY KEY,
        memory_text TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER,
        topic_id INTEGER NOT NULL,
        topic_title TEXT NOT NULL DEFAULT '',
        trigger_reason TEXT NOT NULL,
        action TEXT NOT NULL,
        decision_json TEXT NOT NULL,
        draft_content TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER NOT NULL,
        topic_title TEXT NOT NULL DEFAULT '',
        trigger_reason TEXT NOT NULL,
        target_post_number INTEGER,
        draft_content TEXT NOT NULL,
        decision_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        reply_post_id INTEGER,
        error_text TEXT,
        approved_at TEXT,
        sent_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
]
