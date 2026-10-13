"""Exercise imported prompts with real models in a disposable DB and an in-memory forum."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from cryptography.fernet import Fernet
from sqlalchemy import select

from suenmeow.adapters import Models
from suenmeow.database import Account, Database, Event, KV, Record, Reply, Snapshot
from suenmeow.security import Vault, password_hash
from suenmeow.service import ROUTES, add_event, publish, seed, set_mode
from suenmeow.settings import Settings
from suenmeow.worker import Worker


class Forum:
    connection = {"username": "cat"}

    def __init__(self):
        self.sends = 0
        self.posts = [{"id": 1001, "number": 1, "username": "human", "created": datetime.now(timezone.utc).isoformat(),
                       "text": "这是隔离验收的虚构园艺笔记。我喜欢种盆栽，也喜欢早晨喝茶。阳台朝南，薄荷和罗勒每天有六小时日照，午后会拉遮阳帘。我记录每次浇水前的土壤湿度，尝试观察叶子变化。" * 20},
                      {"id": 1002, "number": 2, "username": "human", "created": datetime.now(timezone.utc).isoformat(),
                       "text": "@SuenMeow 请帮我回答：夏天该怎么判断薄荷盆栽需要浇水？不要用固定日历来浇，给一个简单的方法，谢谢喵！"}]

    async def notifications(self): return []
    async def latest(self): return []
    async def public_visible(self, topic): return True
    async def topic(self, tid, limit):
        return {"id": tid, "title": "隔离验收：照料盆栽", "archetype": "regular", "context": self.posts,
                "post_stream": {"stream": [p["id"] for p in self.posts]}}
    async def reply(self, tid, text, reply_to=None):
        self.sends += 1
        self.posts.append({"id": 1003, "number": 3, "username": "cat", "text": text,
                           "created": datetime.now(timezone.utc).isoformat()})
        return 1003


class ProbeModels(Models):
    async def complete(self, route, messages, topic_id, policy):
        try:
            result = await super().complete(route, messages, 0, policy)
            print(json.dumps({"route": route, "completed": True}), flush=True)
            return result
        except Exception as exc:
            print(json.dumps({"route": route, "completed": False, "error_type": type(exc).__name__,
                              "truncated": isinstance(exc, RuntimeError) and str(exc) == "Model output truncated"}), flush=True)
            raise


async def check():
    settings = Settings.env()
    production, vault = Database(settings.database_url), Vault(settings.key_file)
    with production.transaction() as s:
        assert s.get(KV, "control").data["mode"] == "read_only"
        snapshot = s.get(Snapshot, s.get(KV, "control").data["active_snapshot"]).data
        routes = {name: vault.open(s.get(KV, "connection:" + name).data["cipher"]) for name in ROUTES}
    with tempfile.TemporaryDirectory(prefix="suenmeow-pipeline-") as directory:
        key = Path(directory) / "key"
        key.write_bytes(Fernet.generate_key())
        local_vault = Vault(key)
        db = Database("sqlite:///" + str(Path(directory) / "check.sqlite3"))
        db.migrate()
        with db.transaction() as s:
            admin = Account(username="verification", role="admin", password_hash=password_hash("verification-only-password"))
            s.add(admin)
            s.flush()
            aid = admin.id
        seed(db, aid)
        with db.transaction() as s:
            version = publish(s, aid, "isolated imported-prompt check", snapshot)
            control = set_mode(s, "approval", aid)
            epoch = control["epoch"]
        # Real calls share production budget reservations; event/reply/memory data stays isolated.
        forum, models = Forum(), ProbeModels(production, routes)
        worker = Worker(db, local_vault)
        worker.forum, worker.models = forum, models
        await worker.baseline(epoch)
        # The fixture represents activity arriving after startup's backlog baseline.
        for post in forum.posts:
            post["created"] = datetime.now(timezone.utc).isoformat()
        eid = add_event(db, "isolated:new-event", 42, {"source": "notification", "username": "human"}, epoch, version, 1800)
        try:
            await worker.draft_one()
            worker.state("online", "isolated verification", baseline_epoch=epoch)
            with db.transaction() as s:
                event = s.get(Event, eid)
                reply = s.scalar(select(Reply).where(Reply.event_id == eid))
                print(json.dumps({"draft_state": event.state, "reason": event.reason, "reply_present": bool(reply)}), flush=True)
                assert reply and reply.state == "approval", "Imported prompt protocol must produce a reviewable reply"
                text = local_vault.open(reply.text_cipher)
                print(json.dumps({"reply_characters": len(text), "json_envelope": text.lstrip().startswith(("{", "```json"))}), flush=True)
                assert not text.lstrip().startswith(("{", "```json")), "Forum reply must be prose, not a legacy JSON envelope"
                rid = reply.id
                reply.state = "ready"
            await worker.send_one()
            await worker.remember_one()
            await worker.send_one()
            with db.transaction() as s:
                reply = s.get(Reply, rid)
                assert reply.state == "sent" and reply.memory_state == "done" and forum.sends == 1
                memories = list(s.scalars(select(Record).where(Record.kind == "memory")))
                print(json.dumps({"fake_forum_sends": forum.sends, "memory_done": True, "memory_records": len(memories),
                                  "real_forum_writes": 0, "production_event_queue_unchanged": True}), flush=True)
        finally:
            await models.close()
            db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())
