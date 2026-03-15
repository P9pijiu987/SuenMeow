from pathlib import Path

from db.repositories import Database


def test_pipeline_runs_are_recorded(tmp_path: Path) -> None:
    database = Database(tmp_path / "runs.sqlite3")
    database.initialize()

    database.record_pipeline_run(
        event_id=1,
        topic_id=42,
        topic_title="Hello",
        trigger_reason="notification",
        action="reply",
        decision={"should_reply": True},
        draft_content="draft",
    )

    runs = database.list_recent_pipeline_runs()
    assert len(runs) == 1
    assert runs[0]["topic_id"] == 42
    assert runs[0]["decision"]["should_reply"] is True
