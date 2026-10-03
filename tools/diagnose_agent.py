"""One bounded read-only Agent probe with redacted HTTP failure classification."""
import asyncio
import json

from sqlalchemy import select

from suenmeow.adapters import Discourse, Models
from suenmeow.agent import AgentEngine, enqueue
from suenmeow.database import Account, AgentSession, AgentStep, AgentTask, Database, KV
from suenmeow.domain import AgentMessageInput
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def main():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        assert s.get(KV, "control").data["mode"] == "read_only"
        prior = s.scalar(select(AgentTask).order_by(AgentTask.created.desc()))
        owner = s.get(Account, prior.owner)
        assert owner.active and owner.role == "admin"
        chat = AgentSession(owner=owner.id)
        s.add(chat)
        s.flush()
        body = AgentMessageInput(text=vault.open(prior.instruction_cipher), **{key: prior.constraints[key] for key in
                                 ("kind", "target_topic", "private", "max_chars", "reply_to")}, allow_send=False)
        tid = enqueue(s, vault, owner.id, chat.id, body)
        s.get(AgentTask, tid).state = "running"
        routes = {key.removeprefix("connection:"): vault.open(row.data["cipher"]) for key in
                  ["connection:forum", "connection:planner", "connection:agent"] if (row := s.get(KV, key))}
    forum, models = Discourse(routes.pop("forum")), Models(db, routes)
    async def error(response):
        if response.status_code >= 400:
            await response.aread()
            body = response.text.lower()
            print(json.dumps({"http_failure": response.status_code, "component": "forum" if response.request.url.host == forum.client.base_url.host else "model",
                              "fields": [key for key in ("reasoning_content", "tool_choice", "tool_call", "max_tokens", "messages", "assistant", "last message", "unsupported", "missing", "invalid") if key in body]}), flush=True)
    forum.client.event_hooks["response"] = [error]
    models.client.event_hooks["response"] = [error]
    try:
        await forum.login()
        await AgentEngine(db, vault, forum, models, tid).run()
        with db.transaction() as s:
            task = s.get(AgentTask, tid)
            print(json.dumps({"task_id": tid, "state": task.state, "reason": task.reason,
                              "steps": [(x.tool, x.state) for x in s.scalars(select(AgentStep).where(AgentStep.task_id == tid).order_by(AgentStep.id))]}))
    finally:
        await forum.close()
        await models.close()


if __name__ == "__main__":
    asyncio.run(main())
