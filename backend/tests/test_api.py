import pyotp
from sqlalchemy import select

from conftest import login
from suenmeow.database import Account, KV, LoginSession, Record, Snapshot


def test_session_csrf_and_origin(client):
    assert client.get("/api/dashboard").status_code == 401
    login(client)
    assert client.get("/api/dashboard").status_code == 200
    csrf = client.headers.pop("x-csrf-token")
    assert client.put("/api/control", json={"mode": "paused"}).status_code == 403
    client.headers["x-csrf-token"] = csrf
    assert client.put("/api/control", json={"mode": "paused"}, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/dashboard").status_code == 401


def test_editor_permissions_and_module_grants(client, env):
    _, db, _, ids = env
    login(client, "editor", "editor-test-password")
    for path in ["/api/accounts", "/api/config", "/api/connections", "/api/replies", "/api/audit"]:
        assert client.get(path).status_code == 403
    assert client.post("/api/config/publish", json={"note": "unauthorized"}).status_code == 403
    with db.transaction() as s:
        module = s.scalar(select(Record).where(Record.kind == "module"))
        mid = module.id
    body = {"title": "test", "data": {"content": "new"}, "version": 1, "grants": []}
    assert client.put("/api/records/module/" + mid, json=body).status_code == 403
    with db.transaction() as s:
        s.get(Record, mid).grants = [ids["editor"]]
    body["grants"] = [ids["editor"]]
    assert client.put("/api/records/module/" + mid, json=body).status_code == 200
    # A stale write cannot replace a concurrent editor's newer version.
    assert client.put("/api/records/module/" + mid, json=body).status_code == 409
    body["version"], body["grants"] = 2, [ids["other"]]
    assert client.put("/api/records/module/" + mid, json=body).status_code == 403


def test_secrets_masked_and_encrypted(client, env):
    login(client)
    body = {"base_url": "https://forum.example.com", "username": "cat", "password": "not-a-real-password"}
    assert client.put("/api/connections/forum", json=body).status_code == 200
    data = client.get("/api/connections").json()["forum"]
    assert data["configured"] and "password" not in data and "api_key" not in data
    with env[1].transaction() as s:
        assert "not-a-real-password" not in str(s.get(KV, "connection:forum").data)
    body["password"] = ""
    assert client.put("/api/connections/forum", json=body).status_code == 200
    with env[1].transaction() as s:
        assert env[2].open(s.get(KV, "connection:forum").data["cipher"])["password"] == "not-a-real-password"


def test_validation_errors_do_not_echo_credentials(client):
    password = "private-password-fixture-" * 20
    response = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 422
    assert password not in response.text
    login(client)
    key = "private-api-key-fixture"
    response = client.put("/api/connections/planner", json={
        "base_url": "https://model.example.com/v1", "model": "example",
        "api_key": key, "max_output": "invalid", "unexpected_secret": key,
    })
    assert response.status_code == 422
    assert key not in response.text


def test_publish_immutable_and_restore(client, env):
    login(client)
    first = client.post("/api/config/publish", json={"note": "first"}).json()["version"]
    original = client.get(f"/api/config/versions/{first}").json()
    policy = client.get("/api/config").json()["policy"]
    policy["global_cooldown"] = 180
    assert client.put("/api/config/policy", json=policy).status_code == 200
    assert client.get(f"/api/config/versions/{first}").json() == original
    client.post("/api/config/publish", json={"note": "second"})
    assert client.post(f"/api/config/versions/{first}/restore").status_code == 200
    assert client.get("/api/config").json()["policy"]["global_cooldown"] == original["policy"]["global_cooldown"]


def test_unknown_registration_path_and_login_rate_limit(client):
    assert client.post("/api/register", json={}).status_code == 404
    for i in range(10):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 429


def test_totp_enrollment_revokes_sessions(client, env):
    login(client)
    r = client.post("/api/auth/totp/setup", json={"password": "strong-test-password"})
    assert r.status_code == 200
    code = pyotp.TOTP(r.json()["secret"]).now()
    assert client.post("/api/auth/totp/confirm", json={"password": "strong-test-password", "code": code}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "strong-test-password"}).status_code == 401


def test_legacy_user_nest_disabled_and_memory_isolation(client, env):
    _, db, vault, ids = env
    with db.transaction() as s:
        s.add(Record(kind="memory", owner=ids["other"], title="private", data={"cipher": vault.seal({"text": "secret", "scope": "private"})}))
    login(client, "editor", "editor-test-password")
    assert client.get("/api/records/memory").json() == []
    body = {"title": "room", "data": {"topic_id": 123, "forum_username": "other-person"}}
    assert client.post("/api/records/nest", json=body).status_code == 403
    body["data"]["forum_username"] = "human"
    assert client.post("/api/records/nest", json=body).status_code == 403
    body["data"]["private"] = True
    assert client.post("/api/records/nest", json=body).status_code == 403
