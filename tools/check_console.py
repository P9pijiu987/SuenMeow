"""Verify the public console's real cookie-auth flow without printing credentials or cookies."""
import asyncio
import json
from pathlib import Path

import httpx

from suenmeow.settings import Settings


async def check():
    origin = Settings.env().origin
    async with httpx.AsyncClient(base_url=origin, timeout=30, follow_redirects=False) as client:
        health = await client.get("/api/health")
        health.raise_for_status()
        unauthenticated = await client.get("/api/dashboard")
        assert unauthenticated.status_code == 401
        password = Path("/run/secrets/probe_admin_password").read_text().strip()
        response = await client.post("/api/auth/login", json={"username": "admin", "password": password, "code": ""},
                                     headers={"Origin": origin})
        response.raise_for_status()
        csrf = response.json()["csrf"]
        cookie = response.headers.get("set-cookie", "").lower()
        assert all(word in cookie for word in ["secure", "httponly", "samesite=strict"])
        dashboard = await client.get("/api/dashboard")
        dashboard.raise_for_status()
        config = await client.get("/api/config")
        config.raise_for_status()
        no_csrf = await client.post("/api/agent/sessions", json={}, headers={"Origin": origin})
        assert no_csrf.status_code == 403
        wrong_origin = await client.post("/api/agent/sessions", json={}, headers={"Origin": "https://untrusted.test", "x-csrf-token": csrf})
        assert wrong_origin.status_code == 403
        sessions = await client.get("/api/agent/sessions")
        sessions.raise_for_status()
        await client.post("/api/auth/logout", headers={"Origin": origin, "x-csrf-token": csrf})
        after_logout = await client.get("/api/dashboard")
        assert after_logout.status_code == 401
        print(json.dumps({"https_health": health.json(), "unauthenticated_status": 401, "login_verified": True,
                          "secure_httponly_samesite_cookie": True, "csrf_rejection": 403, "origin_rejection": 403,
                          "logout_revoked": True, "mode": dashboard.json()["control"]["mode"],
                          "worker_status": dashboard.json()["worker"]["status"]}))


if __name__ == "__main__":
    asyncio.run(check())
