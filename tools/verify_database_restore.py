"""Read a restored prompt-refresh backup; never connect to the production database."""
import argparse
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from suenmeow.database import AgentDraft, AgentMessage, AgentSource, Database, Event, KV, Record, Reply, Snapshot
from suenmeow.security import Vault
from suenmeow.settings import Settings


def verify(database):
    settings = Settings.env()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql" or database == url.database or not database.startswith("suenmeow_restore_check_"):
        raise ValueError("Use an isolated PostgreSQL database named suenmeow_restore_check_...")
    db = Database(url.set(database=database).render_as_string(hide_password=False))
    with db.transaction() as session:
        marker = session.get(KV, "prompt_refresh:20261004").data
        records = {row.id: row for row in session.scalars(select(Record).where(Record.kind == "module"))}
        snapshot = session.get(Snapshot, marker["snapshot"]).data
        original = session.get(Snapshot, marker["original_snapshot"]).data
        for mid, expected in marker["after_hashes"].items():
            assert hashlib.sha256(records[mid].data["content"].encode()).hexdigest() == expected
            assert hashlib.sha256(snapshot["modules"][mid]["content"].encode()).hexdigest() == expected
        for mid in marker["persona_ids"]:
            assert original["modules"][mid] == snapshot["modules"][mid]
            assert marker["original_hashes"][mid] == marker["after_hashes"][mid]
        vault = Vault(settings.key_file)
        connections = list(session.scalars(select(KV).where(KV.key.like("connection:%"))))
        for row in connections:
            assert isinstance(vault.open(row.data["cipher"]), dict)
        encrypted = 0
        for cls, field in [(AgentMessage, "text_cipher"), (AgentDraft, "text_cipher"), (AgentSource, "content_cipher")]:
            for row in session.scalars(select(cls)):
                vault.open(getattr(row, field))
                encrypted += 1
        print(json.dumps({"isolated_restore": True, "snapshot": marker["snapshot"],
                          "module_hashes": len(marker["after_hashes"]), "personas_identical": len(marker["persona_ids"]),
                          "connections_decrypted": len(connections), "encrypted_chat_records_decrypted": encrypted,
                          "events": session.scalar(select(func.count()).select_from(Event)),
                          "replies": session.scalar(select(func.count()).select_from(Reply))}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    verify(parser.parse_args().database)
