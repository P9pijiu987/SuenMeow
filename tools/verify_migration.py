"""Check imported user data without printing credentials or prompt contents."""
import hashlib
import json
from pathlib import Path
import sys
import tomllib

from sqlalchemy import func, select

from suenmeow.database import Database, Event, KV, Record, Reply
from suenmeow.security import Vault
from suenmeow.service import ROUTES
from suenmeow.settings import Settings


def verify(source: Path):
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        migration = s.get(KV, "migration:legacy")
        assert migration, "No migration manifest"
        manifest = migration.data["manifest"]
        files = list((source / "prompts").rglob("*.md"))
        assert len(files) == len(manifest), "Prompt count mismatch"
        for entry in manifest:
            original = (source / entry["file"]).read_bytes()
            assert hashlib.sha256(original).hexdigest() == entry["sha256"], "Backup input changed"
            imported = s.get(Record, entry["module_id"])
            assert imported and imported.data["content"] == original.decode(), "Prompt content mismatch"
        credentials = tomllib.loads((source / "config/credentials.toml").read_text())
        credentials = credentials.get("forum", credentials)
        forum = vault.open(s.get(KV, "connection:forum").data["cipher"])
        assert forum["username"] == credentials["username"] and forum["password"] == credentials["password"], "Forum credentials mismatch"
        for route in ROUTES:
            conf = vault.open(s.get(KV, "connection:" + route).data["cipher"])
            assert conf["api_key"] and conf["model"] and conf["base_url"].startswith("https://"), "Invalid model route"
        events = s.scalar(select(func.count()).select_from(Event))
        replies = s.scalar(select(func.count()).select_from(Reply))
        memories = s.scalar(select(func.count()).select_from(Record).where(Record.kind == "memory"))
        assert (events, replies, memories) == (0, 0, 0), "Migration must not import historic queues or memories"
        assert s.get(KV, "control").data["mode"] == "paused", "Migration validation requires paused mode"
        print(json.dumps({"prompts_verified": len(manifest), "forum_credentials_match": True,
                          "model_routes_verified": len(ROUTES), "events": events, "replies": replies,
                          "memories": memories, "mode": "paused"}))


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
