"""Restore a deployment archive into an isolated directory and verify every regular file."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def verify(archive):
    files, databases = 0, 0
    with tempfile.TemporaryDirectory(prefix="suenmeow-restore-") as directory:
        root = Path(directory)
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                relative = Path(member.name)
                if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk():
                    raise ValueError("Archive contains an unsafe path or link")
                if not member.isfile():
                    continue
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                original = source.extractfile(member).read()
                target.write_bytes(original)
                target.chmod(0o600)
                assert hashlib.sha256(original).digest() == hashlib.sha256(target.read_bytes()).digest()
                files += 1
                if original[:16] == b"SQLite format 3\x00":
                    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
                        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
                    databases += 1
        assert files > 0
        print(json.dumps({"restored_files_verified": files, "sqlite_integrity_verified": databases, "isolated_restore": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    verify(parser.parse_args().archive)
