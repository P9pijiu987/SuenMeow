"""Inspect a failed import's safe metadata; never print personal input or call a model."""
import argparse
import json

from sqlalchemy import select

from suenmeow.database import Database, KV, MemoryCursor, MemoryImport, Usage
from suenmeow.security import Vault
from suenmeow.settings import Settings


def check(topic_id):
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        job = s.scalar(select(MemoryImport).where(MemoryImport.topic_id == topic_id).order_by(MemoryImport.created.desc()))
        assert job, "No import found"
        model = vault.open(s.get(KV, "connection:memory").data["cipher"])
        messages = vault.open(job.input_cipher)
        previous = s.scalar(select(MemoryCursor).where(MemoryCursor.site == job.config["site"], MemoryCursor.topic_id == topic_id))
        print(json.dumps({"state": job.state, "reason": job.reason,
                          "config": {k: job.config.get(k) for k in ("output_limit", "max_tokens", "reservation", "scanned", "author_posts", "base", "last")},
                          "usage": [{"tokens": u.tokens, "state": u.state, "reserved": u.reserved} for u in s.scalars(select(Usage).where(Usage.task_id == job.id))],
                          "model": {k: model.get(k) for k in ("model", "max_output", "reasoning_effort")},
                          "input_bytes": len(json.dumps(messages, ensure_ascii=False).encode()),
                          "cursor_exists": bool(previous), "cursor_last_post_id": previous.last_post_id if previous else 0,
                          "model_calls": 0, "forum_writes": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", type=int, required=True)
    check(parser.parse_args().topic)
