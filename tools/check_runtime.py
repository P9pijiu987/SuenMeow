"""Explicit read-only activation and real Agent previews; never confirms or sends replies."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
from sqlalchemy import select

from suenmeow.adapters import Discourse
from suenmeow.database import Database, KV, Record
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check(activate):
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        pipeline = s.get(KV, "pipeline").data
        modules = {r.id: r for r in s.scalars(select(Record).where(Record.kind == "module"))}
        prompt_bytes = {route: sum(len(modules[i].data["content"].encode()) for i in ids) for route, ids in pipeline.items()}
        conf = vault.open(s.get(KV, "connection:forum").data["cipher"])
    print(json.dumps({"pipeline_bytes": prompt_bytes}), flush=True)
    if not activate:
        return
    async with httpx.AsyncClient(base_url=settings.origin, timeout=30) as client:
        password = Path("/run/secrets/probe_admin_password").read_text().strip()
        login = await client.post("/api/auth/login", json={"username": "admin", "password": password}, headers={"Origin": settings.origin})
        login.raise_for_status()
        client.headers.update({"Origin": settings.origin, "x-csrf-token": login.json()["csrf"]})
        async def call(method, path, data=None):
            response = await client.request(method, path, json=data)
            response.raise_for_status()
            return response.json()
        status = await call("GET", "/api/dashboard")
        assert status["control"]["mode"] in ("paused", "read_only"), "Only paused/read-only environments can be probed"
        active = status["control"]["active_snapshot"]
        snapshot = await call("GET", f"/api/config/versions/{active}") if active else None
        if not snapshot or any("persona" not in module for module in snapshot["modules"].values()):
            # Saved user prompts stay intact; only raise reservation ceilings for long persona prompts.
            config = await call("GET", "/api/config")
            policy = config["policy"]
            policy["topic_tokens"] = max(policy["topic_tokens"], 120000)
            await call("PUT", "/api/config/policy", policy)
            await call("POST", "/api/config/publish", {"note": "迁移内容核对完成；发布只读联调快照"})
        agent_policy = await call("GET", "/api/agent/settings")
        agent_policy.update(max_tokens=64000, max_seconds=300, auto_research=False)
        await call("PUT", "/api/agent/settings", agent_policy)
        await call("PUT", "/api/control", {"mode": "read_only"})
        for _ in range(45):
            status = await call("GET", "/api/dashboard")
            if status["worker"].get("status") == "online" and status["worker"].get("baseline_epoch") == status["control"]["epoch"]:
                break
            await asyncio.sleep(2)
        assert status["worker"]["status"] == "online", "Forum baseline must succeed before Agent preview"
        print(json.dumps({"mode": status["control"]["mode"], "baseline_ready": True,
                          "watermark_present": "notification_watermark" in status["worker"]}), flush=True)
        forum = Discourse(conf)
        try:
            await forum.login()
            topics = await forum.latest()
            target = None
            for item in topics[:8]:
                topic = await forum.topic(int(item["id"]), 1)
                if not topic.get("closed") and not topic.get("archived") and await forum.public_visible(topic):
                    target = topic
                    break
            assert target, "Need an open public existing topic for preview"
            username = target["context"][-1]["username"]
        finally:
            await forum.close()
        for kind, text in [
            ("research", f"部署验收，只研究不发送。请读取主题 {target['id']}，主动论坛搜索 SuenMeow，并检索用户 {username} 的相关记忆。使用 release_describe 核实已验证版本信息，汇总找到的来源与限制，不编造空记忆。"),
            ("reply", "部署验收，只生成预览，不发送。阅读目标主题，并根据 release_describe 对 SuenMeow 2.0 做约 500 字介绍。说明哪些仍在验收，避免打断原讨论；使用 draft_reply 完成。")]:
            chat = await call("POST", "/api/agent/sessions", {})
            queued = await call("POST", f"/api/agent/sessions/{chat['id']}/messages",
                                {"text": text, "kind": kind, "target_topic": target["id"], "max_chars": 2000, "allow_send": False})
            for _ in range(160):
                task = await call("GET", f"/api/agent/tasks/{queued['task_id']}")
                if task["state"] not in ("queued", "running"):
                    break
                await asyncio.sleep(2)
            print(json.dumps({"kind": kind, "task_id": task["id"], "state": task["state"], "reason": task["reason"],
                              "tools": [step["tool"] for step in task["steps"] if step["state"] == "completed"],
                              "sources": len(task["sources"]), "tokens": task["tokens"],
                              "draft_chars": len(task["draft"]["text"]) if task["draft"] else 0,
                              "confirmed": task["draft"]["confirmed"] if task["draft"] else False}), flush=True)
            assert task["state"] == ("completed" if kind == "research" else "awaiting_confirmation"), "Agent preview must complete"
        await call("POST", "/api/auth/logout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate-read-only", action="store_true", help="Publish imported draft and enter read-only mode for bounded previews")
    asyncio.run(check(parser.parse_args().activate_read_only))
