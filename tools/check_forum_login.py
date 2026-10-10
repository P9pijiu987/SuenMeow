"""Probe passwordless login over HTTPS; never send a forum message or consume a proof."""
import asyncio
import json
from pathlib import Path

import httpx

from suenmeow.settings import Settings


async def check():
    origin = Settings.env().origin
    async with httpx.AsyncClient(base_url=origin, timeout=30) as browser:
        entry = await browser.get("/api/auth/forum")
        entry.raise_for_status()
        assert entry.json()["enabled"] and entry.json()["allow_signup"]
        denied = await browser.post("/api/auth/forum/start")
        assert denied.status_code == 403
        request = await browser.post("/api/auth/forum/start", headers={"Origin": origin})
        request.raise_for_status()
        data = request.json()
        cookie = request.headers["set-cookie"].lower()
        assert all(x in cookie for x in ("secure", "httponly", "samesite=strict"))
        status = await browser.get("/api/auth/forum/status/" + data["id"])
        assert status.json()["state"] == "pending"
        async with httpx.AsyncClient(base_url=origin, timeout=30) as outsider:
            assert (await outsider.get("/api/auth/forum/status/" + data["id"])).status_code == 404
        finish = await browser.post("/api/auth/forum/finish/" + data["id"], json={}, headers={"Origin": origin})
        assert finish.status_code == 409
        replaced = await browser.post("/api/auth/forum/start", headers={"Origin": origin})
        replaced.raise_for_status()
        assert (await browser.get("/api/auth/forum/status/" + data["id"])).status_code == 404
        # Disable the older, unbound public registration after verifying the new entry.
        password = Path("/run/secrets/probe_admin_password").read_text().strip()
        login = await browser.post("/api/auth/login", json={"username": "admin", "password": password}, headers={"Origin": origin})
        login.raise_for_status()
        csrf = {"Origin": origin, "x-csrf-token": login.json()["csrf"]}
        closed = await browser.put("/api/auth/registration", json={"enabled": False}, headers=csrf)
        closed.raise_for_status()
        assert not (await browser.get("/api/auth/registration")).json()["enabled"]
        await browser.post("/api/auth/logout", headers=csrf)
        print(json.dumps({"entry_available": True, "browser_bound": True, "origin_required": True,
                          "secure_cookie": True, "unverified_finish_rejected": True,
                          "public_password_signup_closed": True, "real_pm_verified": False,
                          "forum_writes": 0, "model_calls": 0}))


if __name__ == "__main__":
    asyncio.run(check())
