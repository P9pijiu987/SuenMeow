from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import select

from suenmeow.database import Event, KV, Record, Reply, now
from suenmeow.domain import Policy
from suenmeow.service import add_event, get_snapshot, publish
from suenmeow.worker import Worker
from test_safety import FakeForum, activate


class GoodModels:
    def __init__(self): self.routes = []
    async def complete(self, route, messages, topic_id, policy):
        self.routes.append(route)
        return {"planner": '{"reply":true,"reason":"回答问题"}', "replyer": "窗边晒太阳，也要记得喝水喵。",
                "memory": '{"facts":[{"username":"human","text":"喜欢照料盆栽"}]}', "summary": "讨论如何照料盆栽。"}[route]


class GoodForum(FakeForum):
    async def reply(self, *args): self.calls += 1; return 22


async def test_complete_notification_approval_send_memory_path(env):
    epoch, version = activate(env, "approval")
    eid = add_event(env[1], "n:1001", 42, {"source": "notification", "username": "human"}, epoch, version, 600)
    w = Worker(env[1], env[2]); w.forum = GoodForum(); w.models = GoodModels(); w.epoch = epoch
    await w.draft_one()
    with env[1].transaction() as s:
        r = s.scalar(select(Reply).where(Reply.event_id == eid)); rid = r.id
        assert r.state == "approval"
    await w.send_one(); assert w.forum.calls == 0
    with env[1].transaction() as s: s.get(Reply, rid).state = "ready"
    await w.send_one(); await w.remember_one(); await w.send_one()
    assert w.forum.calls == 1
    assert w.models.routes == ["planner", "replyer", "memory"]
    with env[1].transaction() as s:
        assert s.get(Reply, rid).state == "sent"
        assert s.get(Reply, rid).memory_state == "done"
        memory = s.scalar(select(Record).where(Record.kind == "memory"))
        assert memory.owner == env[3]["editor"]
        assert env[2].open(memory.data["cipher"])["source_post_id"] == 20


async def test_summary_route_used_for_long_context(env):
    epoch, version = activate(env)
    class LongForum(GoodForum):
        async def topic(self, tid, limit):
            d = await super().topic(tid, limit)
            d["context"][0]["text"] = "谈话" * 10000
            return d
    eid = add_event(env[1], "n:long", 42, {"source": "notification"}, epoch, version, 600)
    w = Worker(env[1], env[2]); w.forum = LongForum(); w.models = GoodModels(); w.epoch = epoch
    await w.draft_one()
    assert w.models.routes == ["summary", "planner", "replyer"]


async def test_restart_discards_existing_ready_draft(env):
    epoch, version = activate(env)
    eid = add_event(env[1], "n:old", 42, {}, epoch, version, 600)
    with env[1].transaction() as s:
        s.get(Event, eid).state = "drafted"
        r = Reply(event_id=eid, topic_id=42, text_cipher=env[2].seal("old"), state="ready")
        s.add(r); s.flush(); rid=r.id
    w = Worker(env[1], env[2]); w.forum = GoodForum()
    await w.baseline(epoch); await w.send_one()
    assert w.forum.calls == 0
    with env[1].transaction() as s: assert s.get(Reply, rid).state == "expired"


async def test_new_notification_cannot_revive_old_visible_posts(env):
    epoch, version = activate(env)
    class OldVisibleForum(GoodForum):
        async def topic(self, tid, limit):
            data = await super().topic(tid, limit)
            for post in data["context"]:
                post["created"] = datetime.fromtimestamp(now() - 3600, timezone.utc).isoformat()
            return data
    models = GoodModels()
    w = Worker(env[1], env[2]); w.forum = OldVisibleForum(); w.models = models
    await w.baseline(epoch)
    eid = add_event(env[1], "n:new-id-old-post", 42, {"source": "notification", "username": "human"}, epoch, version, 600)
    await w.draft_one()
    assert not models.routes and w.forum.calls == 0
    with env[1].transaction() as s:
        assert s.get(Event, eid).state == "skipped"
        assert not s.scalar(select(Reply))


async def test_small_hot_increments_accumulate(env):
    epoch, version = activate(env)
    w = Worker(env[1], env[2]); w.forum = FakeForum()
    await w.baseline(epoch)
    with env[1].transaction() as s: control, snapshot = get_snapshot(s)
    for n in [101,102,103,104]:
        w.forum.topics[0]["highest_post_number"] = n; w.last_hot = 0
        await w.collect(control,snapshot)
    with env[1].transaction() as s: assert not s.scalar(select(Event))
    w.forum.topics[0]["highest_post_number"] = 105; w.last_hot = 0
    await w.collect(control,snapshot)
    with env[1].transaction() as s: assert s.scalar(select(Event)).receipt == "hot:42:105"


async def test_catnest_optout_no_diary_and_one_daily_receipt(env, monkeypatch):
    epoch, version = activate(env)
    with env[1].transaction() as s:
        p = Policy(playful=True, timezone="UTC").model_dump()
        s.get(KV,"policy").data = p
        publish(s, env[3]["admin"], "play")
        control,snapshot = get_snapshot(s); epoch=control["epoch"]
        nest=Record(kind="nest",owner=env[3]["editor"],title="room",data={"topic_id":42,"forum_username":"human","diary":True,"private":False,"opted_out":True})
        s.add(nest); s.flush(); nid=nest.id
    import suenmeow.worker as module
    class Midday(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026,10,3,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr(module, "datetime", Midday)
    w=Worker(env[1],env[2]); w.epoch=epoch; w.forum=GoodForum()
    await w.playful(snapshot)
    with env[1].transaction() as s:
        assert not s.scalar(select(Event))
        s.get(Record,nid).data={**s.get(Record,nid).data,"opted_out":False}
    w.last_play=0; await w.playful(snapshot)
    w.last_play=0; await w.playful(snapshot)
    with env[1].transaction() as s:
        assert len(list(s.scalars(select(Event)))) == 1
        assert s.scalar(select(Event)).data["source"] == "diary"
