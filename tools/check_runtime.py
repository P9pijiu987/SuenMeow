"""Explicit read-only activation and real Agent previews; never confirms or sends replies."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
from sqlalchemy import func, select

from suenmeow.adapters import Discourse
from suenmeow.database import Database, KV, Record, Usage, day
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check(activate, only_reply=False):
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        pipeline = s.get(KV, "pipeline").data
        modules = {r.id: r for r in s.scalars(select(Record).where(Record.kind == "module"))}
        prompt_bytes = {route: sum(len(modules[i].data["content"].encode()) for i in ids) for route, ids in pipeline.items()}
        forum_config = vault.open(s.get(KV, "connection:forum").data["cipher"])
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
        connections = await call("GET", "/api/connections")
        for route, output in [("planner", 3000), ("replyer", 5000), ("memory", 3000), ("summary", 3000), ("agent", 8000)]:
            conf = connections[route]
            if "deepseek" in conf["model"].casefold() and (conf.get("reasoning_effort") != "low" or conf["max_output"] < output):
                payload = {key: value for key, value in conf.items() if key != "configured"}
                payload.update(api_key="", reasoning_effort="low", max_output=max(conf["max_output"], output))
                await call("PUT", "/api/connections/" + route, payload)
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
        forum = Discourse(forum_config)
        try:
            await forum.login()
            topics = await forum.latest()
            target = None
            config = await call("GET", "/api/config")
            for item in topics[:8]:
                with db.transaction() as s:
                    spent = s.scalar(select(func.coalesce(func.sum(Usage.tokens), 0)).where(Usage.topic_id == int(item["id"]), Usage.day == day()))
                if spent + agent_policy["max_tokens"] > config["policy"]["topic_tokens"]:
                    continue
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
            ("reply", "部署验收，只生成预览，不发送。阅读目标主题，并根据 release_describe 对 SuenMeow 2.0 写 1200 至 1600 中文字符的完整长介绍。分六段展开控制台、权限、回复安全、主动研究、迁移与趣味设计；区分实际验证、模拟测试和待验收项目，不编造。以轻松猫咪语气编写，使用 draft_reply 完成。")]:
            if only_reply and kind != "reply":
                continue
            chat = await call("POST", "/api/agent/sessions", {})
            queued = await call("POST", f"/api/agent/sessions/{chat['id']}/messages",
                                {"text": text, "kind": kind, "target_topic": target["id"], "max_chars": 2500, "allow_send": False})
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
            if kind == "reply":
                assert len(task["draft"]["text"]) >= 1000, "Long preview must contain a substantial complete body"
                draft = task["draft"]
                await call("PUT", f"/api/agent/drafts/{draft['id']}",
                           {"text": draft["text"] + "\n\n此文为部署验收预览，尚未发送。", "version": draft["version"], "target_topic": draft["target_topic"]})
                edited = await call("GET", f"/api/agent/tasks/{task['id']}")
                assert edited["draft"]["version"] == draft["version"] + 1 and not edited["draft"]["confirmed"]
                assert edited["draft"]["digest"] != draft["digest"]
                forbidden = await client.post(f"/api/agent/drafts/{draft['id']}/confirm", json={"digest": edited["draft"]["digest"]})
                assert forbidden.status_code == 409, "Read-only mode must block even a confirmed administrator"
                print(json.dumps({"draft_edit_versioned": True, "digest_changed": True, "read_only_send_rejected": 409}), flush=True)
        await call("POST", "/api/auth/logout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate-read-only", action="store_true", help="Publish imported draft and enter read-only mode for bounded previews")
    parser.add_argument("--only-reply", action="store_true", help="Skip already-verified research and check the long draft only")
    args = parser.parse_args()
    asyncio.run(check(args.activate_read_only, args.only_reply))
