"""Install reviewed release evidence for the read-only release_describe tool."""
import argparse
import json
from pathlib import Path

from suenmeow.database import Database, KV, audit
from suenmeow.settings import Settings


def record(path):
    data = json.loads(path.read_text())
    assert data["version"] == "2.0.0" and data["status"] and isinstance(data["verified_features"], list)
    db = Database(Settings.env().database_url)
    with db.transaction() as s:
        assert s.get(KV, "control").data["mode"] in ("paused", "read_only"), "Record deployment evidence before enabling sends"
        row = s.get(KV, "release_verified")
        if row:
            row.data, row.version = data, row.version + 1
        else:
            s.add(KV(key="release_verified", data=data))
        audit(s, "deployment", "release_evidence_recorded", data["version"], verified_at=data["verified_at"])
    print(json.dumps({"version": data["version"], "verified_features": len(data["verified_features"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    record(parser.parse_args().file)
