from db.schema import SCHEMA_STATEMENTS


def test_schema_contains_core_tables() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS)
    assert "topic_state" in schema
    assert "trigger_events" in schema
    assert "ban_rules" in schema
    assert "pending_replies" in schema
