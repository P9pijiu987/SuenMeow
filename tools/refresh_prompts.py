"""Apply the authorized task-prompt refresh through admin APIs, preserving every persona."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
from suenmeow.database import Database, KV, Snapshot, audit
from suenmeow.prompts import AGENT, replacement_for
from suenmeow.settings import Settings


def sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


async def refresh(apply=False):
    settings = Settings.env()
    db = Database(settings.database_url)
    with db.transaction() as s:
        migration = s.get(KV, "migration:legacy")
        files = {item["module_id"]: item["file"] for item in migration.data["manifest"]} if migration else {}
        marker = s.get(KV, "prompt_refresh:20261004")
        previous = dict(marker.data) if marker else None
    async with httpx.AsyncClient(base_url=settings.origin, timeout=30) as client:
        response = await client.post("/api/auth/login", json={"username": "admin", "password": Path("/run/secrets/probe_admin_password").read_text().strip()}, headers={"Origin": settings.origin})
        response.raise_for_status()
        client.headers.update({"Origin": settings.origin, "x-csrf-token": response.json()["csrf"]})
        async def call(method, path, body=None):
            response = await client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()
        control = (await call("GET", "/api/dashboard"))["control"]
        assert control["mode"] in ("paused", "read_only"), "Never refresh prompts while sends are enabled"
        records = await call("GET", "/api/records/module")
        if previous:
            by_id = {r["id"]: r for r in records}
            for mid, expected in previous["after_hashes"].items():
                assert sha(by_id[mid]["data"]["content"]) == expected, "Prompt changed after recorded refresh"
            print(json.dumps({"already_applied": True, "snapshot": previous["snapshot"], "hashes_verified": len(previous["after_hashes"])}))
            await call("POST", "/api/auth/logout")
            return
        config = await call("GET", "/api/config")
        protected, changes = {}, []
        for record in records:
            content = replacement_for(record["title"], record["data"].get("persona", False), files.get(record["id"], ""))
            if content is None:
                protected[record["id"]] = record
            else:
                changes.append((record, content))
        print(json.dumps({"system_modules_to_refresh": len(changes), "personas_preserved": len(protected), "apply": apply}), flush=True)
        if not apply:
            await call("POST", "/api/auth/logout")
            return
        original_snapshot = control["active_snapshot"]
        original_hashes = {r["id"]: sha(r["data"]["content"]) for r in records}
        for record, content in changes:
            await call("PUT", "/api/records/module/" + record["id"], {
                "title": "信任与权限边界" if files.get(record["id"], "").endswith("/JailBreak.md") else record["title"],
                "data": {**record["data"], "content": content, "description": "SuenMeow 2 工作协议 · 2026-10-04；人格内容单独保留"},
                "version": record["version"], "grants": record["grants"],
            })
        agent = next((r for r in records if r["title"] == "主动研究"), None)
        if not agent:
            agent = await call("POST", "/api/records/module", {"title": "主动研究", "data": {"content": AGENT, "description": "有界研究、真实来源和完整草稿任务", "persona": False}})
        legacy = {Path(filename).name: mid for mid, filename in files.items()}
        if files:
            # Keep the same active persona order, replace only the working instructions.
            pipeline = {
                "planner": [mid for mid in config["pipeline"]["planner"] if mid in protected] + [legacy["planner.md"], legacy["planner_suen.md"], legacy["safety_rules.md"]],
                "replyer": [mid for mid in config["pipeline"]["replyer"] if mid in protected] + [legacy["replyer.md"], legacy["style_rules.md"], legacy["safety_rules.md"]],
                "memory": [legacy["memory_user_update.md"], legacy["memory_self_update.md"], legacy["safety_rules.md"]],
                "summary": [legacy["summary_prompt.md"], legacy["safety_rules.md"]],
                "agent": [agent["id"], legacy["style_rules.md"], legacy["safety_rules.md"]],
            }
        else:
            pipeline = {**config["pipeline"], "agent": [agent["id"]]}
        await call("PUT", "/api/config/pipeline", pipeline)
        current = await call("GET", "/api/records/module")
        by_id = {r["id"]: r for r in current}
        assert all(by_id[mid] == record for mid, record in protected.items()), "Persona metadata/content changed"
        assert (await call("GET", "/api/dashboard"))["control"]["active_snapshot"] == original_snapshot
        result = await call("POST", "/api/config/publish", {"note": "管理员要求首帖前更新全部工作 prompts；personas 原样保留；只读联调"})
        after_hashes = {r["id"]: sha(r["data"]["content"]) for r in current}
        with db.transaction() as s:
            snap = s.get(Snapshot, result["version"])
            assert all(sha(snap.data["modules"][mid]["content"]) == digest for mid, digest in after_hashes.items())
            s.add(KV(key="prompt_refresh:20261004", data={"original_snapshot": original_snapshot, "snapshot": result["version"], "original_hashes": original_hashes, "after_hashes": after_hashes, "persona_ids": list(protected)}))
            audit(s, "deployment", "task_prompts_refreshed", str(result["version"]), personas_preserved=len(protected), systems_updated=len(changes))
        await call("POST", "/api/auth/logout")
        print(json.dumps({"published_snapshot": result["version"], "systems_updated": len(changes), "personas_identical": len(protected), "mode": control["mode"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(refresh(parser.parse_args().apply))
