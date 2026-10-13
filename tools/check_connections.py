"""Read-only forum verification and small model probes; never sends forum replies."""
import asyncio
import json

from sqlalchemy import select

from suenmeow.adapters import Discourse, Models
from suenmeow.agent import tool_specs
from suenmeow.database import Database, KV, Usage, audit, uid
from suenmeow.domain import AgentPolicy, Policy
from suenmeow.security import Vault
from suenmeow.service import ROUTES
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        assert s.get(KV, "control").data["mode"] == "paused", "Probe requires paused mode"
        forum_config = vault.open(s.get(KV, "connection:forum").data["cipher"])
        routes = {k: vault.open(s.get(KV, "connection:" + k).data["cipher"]) for k in ROUTES}
        policy = Policy.model_validate(s.get(KV, "policy").data)
    forum, models = Discourse(forum_config), Models(db, routes)
    results = {}
    try:
        await forum.login()
        results["forum"] = {"authenticated": True}
        notifications, topics = await asyncio.gather(forum.notifications(), forum.latest())
        results["forum"] = {"authenticated": True, "baseline_notifications": len(notifications), "latest_topics": len(topics),
                            "max_post_length": await forum.reply_limit()}
        if topics:
            topic = await forum.topic(int(topics[0]["id"]), 1)
            results["forum"]["public_visibility_check"] = await forum.public_visible(topic)
            results["forum"]["visible_posts_read"] = len(topic["context"])
    except Exception as exc:
        results["forum"] = {**results.get("forum", {"authenticated": False}), "error_type": type(exc).__name__}
        if hasattr(exc, "response"):
            results["forum"]["http_status"] = exc.response.status_code
    for route in ROUTES:
        try:
            text = await models.complete(route, [{"role": "user", "content": "连通性检测：仅返回 OK。"}], 0, policy)
            results[route] = {"ok": bool(text.strip()), "characters": len(text)}
        except Exception as exc:
            results[route] = {"ok": False, "error_type": type(exc).__name__}
    conf = {**routes["planner"], "supports_tools": True, "max_output": 300, "temperature": 0}
    models.routes["agent"] = conf
    try:
        probe_id = uid()
        message, truncated = await models.tool_turn(
            [{"role": "user", "content": "连通性检测：必须调用 release_describe 工具，参数为 {}；不要输出正文。"}],
            tool_specs(AgentPolicy(allowed_tools=["release_describe"]), "research"), 0, policy, probe_id, 10000)
        calls = message.get("tool_calls", [])
        verified = not truncated and len(calls) == 1 and calls[0].get("function", {}).get("name") == "release_describe"
        if verified:
            final, cut = await models.tool_turn([
                {"role": "user", "content": "调用 release_describe，收到结果后只返回 OK。"}, message,
                {"role": "tool", "tool_call_id": calls[0]["id"], "content": '{"status":"connectivity_probe_complete"}'}],
                tool_specs(AgentPolicy(allowed_tools=["release_describe"]), "research"), 0, policy, probe_id, 10000)
            verified = not cut and bool(final.get("content")) and not final.get("tool_calls")
        results["agent"] = {"tools_verified": verified}
        if verified:
            with db.transaction() as s:
                if not s.get(KV, "connection:agent"):
                    s.add(KV(key="connection:agent", data={"cipher": vault.seal({**conf, "max_output": 6000}), "tools_verified": True}))
                    audit(s, "deployment", "agent_route_verified")
    except Exception as exc:
        results["agent"] = {"tools_verified": False, "error_type": type(exc).__name__}
    await forum.close()
    await models.close()
    with db.transaction() as s:
        results["usage"] = [{"route": u.route, "tokens": u.tokens, "state": u.state}
                            for u in s.scalars(select(Usage).order_by(Usage.created.desc()).limit(5))]
    print(json.dumps(results))


if __name__ == "__main__":
    asyncio.run(check())
