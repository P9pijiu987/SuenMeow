from sqlalchemy import select, text

from suenmeow.database import Base, KV, Usage


def test_schema_one_upgrade_preserves_usage_and_control(env):
    _, db, _, _ = env
    with db.transaction() as s:
        s.get(KV, "schema").data = {"version": 1}
        s.add(Usage(day="2026-10-01", route="replyer", topic_id=42, tokens=123, reserved=200, state="actual"))
        original_control = dict(s.get(KV, "control").data)
    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_usage_task_id"))
        conn.execute(text("ALTER TABLE usage DROP COLUMN task_id"))
        for table in list(Base.metadata.sorted_tables):
            if table.name.startswith("agent_"):
                table.drop(conn)
    db.migrate()
    db.migrate()  # Explicit reruns must not reset preserved state or duplicate tables.
    with db.transaction() as s:
        assert s.get(KV, "schema").data["version"] == 2
        assert s.get(KV, "control").data == original_control
        usage = s.scalar(select(Usage))
        assert usage.tokens == 123 and usage.task_id == ""


def test_import_endpoint_fix_does_not_overwrite_admin_edits(env):
    from suenmeow.cli import normalize_legacy_endpoints
    _, db, vault, _ = env
    with db.transaction() as s:
        s.add(KV(key="migration:legacy", data={"manifest": []}))
        for route, version in [("planner", 1), ("replyer", 2)]:
            s.add(KV(key="connection:" + route, version=version, data={"cipher": vault.seal({"base_url": "https://model.test/completions"})}))
    normalize_legacy_endpoints(db, vault)
    with db.transaction() as s:
        assert vault.open(s.get(KV, "connection:planner").data["cipher"])["endpoint_mode"] == "complete"
        assert "endpoint_mode" not in vault.open(s.get(KV, "connection:replyer").data["cipher"])
