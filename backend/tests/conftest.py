from pathlib import Path
import os
from urllib.parse import quote
import uuid

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, create_engine, text

from suenmeow.api import create_app
from suenmeow.database import Account, Database
from suenmeow.security import Vault, password_hash
from suenmeow.service import seed
from suenmeow.settings import Settings


@pytest.fixture
def env(tmp_path):
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    schema = "test_" + uuid.uuid4().hex
    base_engine = None
    url = "sqlite:///:memory:"
    if os.getenv("SUENMEOW_TEST_POSTGRES") == "1":
        root_url = os.environ["DATABASE_URL"]
        base_engine = create_engine(root_url)
        with base_engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        url = root_url + ("&" if "?" in root_url else "?") + "options=" + quote("-csearch_path=" + schema)
    settings = Settings(url, key, "http://testserver", False)
    db = Database(settings.database_url)
    db.migrate()
    with db.transaction() as s:
        admin = Account(username="admin", password_hash=password_hash("strong-test-password"), role="admin")
        editor = Account(username="editor", password_hash=password_hash("editor-test-password"), role="editor", forum_username="human")
        other = Account(username="other", password_hash=password_hash("another-test-password"), role="editor")
        s.add_all([admin, editor, other])
        s.flush()
        ids = {"admin": admin.id, "editor": editor.id, "other": other.id}
    seed(db, ids["admin"])
    yield settings, db, Vault(key), ids
    db.engine.dispose()
    if base_engine:
        with base_engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        base_engine.dispose()


@pytest.fixture
def client(env):
    settings, db, _, _ = env
    with TestClient(create_app(settings, db)) as client:
        yield client


def login(client, username="admin", password="strong-test-password"):
    r = client.post("/api/auth/login", json={"username": username, "password": password, "code": ""})
    assert r.status_code == 200, r.text
    client.headers["x-csrf-token"] = r.json()["csrf"]
    return r.json()
