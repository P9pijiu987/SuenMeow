import asyncio
import json

import httpx
import pytest
from sqlalchemy import select, func

from suenmeow.adapters import Discourse, Models
from suenmeow.database import Event, KV, Record, Reply, Usage, now
from suenmeow.domain import Policy
from suenmeow.service import BudgetExceeded, add_event, claim_send, mark_unknown, publish, reserve, set_mode, settle
from suenmeow.worker import Worker


def activate(env, mode="auto"):
    _, db, _, ids = env
    with db.transaction() as s:
        version = publish(s, ids["admin"], "test")
        control = set_mode(s, mode, ids["admin"])
        s.get(KV, "worker").data = {"status": "online", "heartbeat": now(), "baseline_epoch": control["epoch"]}
    return control["epoch"], version


def draft(env, receipt="n:1", topic=42):
    epoch, version = activate(env)
    eid = add_event(env[1], receipt, topic, {"source": "notification", "username": "human", "private": False}, epoch, version, 600)
    with env[1].transaction() as s:
        s.get(Event, eid).state = "drafted"
        r = Reply(event_id=eid, topic_id=topic, text_cipher=env[2].seal("hello"), state="ready")
        s.add(r)
        s.flush()
        return r.id, eid


def test_receipt_dedupe_and_mode_epoch(env):
    epoch, version = activate(env)
    assert add_event(env[1], "notification:1", 42, {}, epoch, version, 600)
    assert not add_event(env[1], "notification:1", 42, {}, epoch, version, 600)
    with env[1].transaction() as s:
        set_mode(s, "paused", env[3]["admin"])
    assert not add_event(env[1], "notification:2", 42, {}, epoch, version, 600)


def test_claim_once_and_cooldown_and_epoch(env):
    rid, eid = draft(env)
    assert claim_send(env[1], rid)
    assert claim_send(env[1], rid) is None
    mark_unknown(env[1])
    with env[1].transaction() as s:
        assert s.get(Reply, rid).state == "unknown"
    assert claim_send(env[1], rid) is None


def test_global_cooldown_across_topics(env):
    epoch, version = activate(env)
    replies = []
    for tid in [1, 2]:
        eid = add_event(env[1], f"n:{tid}", tid, {}, epoch, version, 600)
        with env[1].transaction() as s:
            s.get(Event, eid).state = "drafted"
            r = Reply(event_id=eid, topic_id=tid, text_cipher=env[2].seal("hi"), state="ready")
            s.add(r); s.flush(); replies.append(r.id)
    assert claim_send(env[1], replies[0])
    assert not claim_send(env[1], replies[1])
    with env[1].transaction() as s:
        s.get(KV, "worker").data = {**s.get(KV, "worker").data, "heartbeat": now() + 61}
    assert claim_send(env[1], replies[1], now() + 61)


def test_expired_or_wrong_topic_never_sent(env):
    rid, eid = draft(env)
    with env[1].transaction() as s:
        s.get(Reply, rid).topic_id = 0
    assert claim_send(env[1], rid) is None
    with env[1].transaction() as s:
        s.get(Reply, rid).topic_id = 42
        s.get(Event, eid).expires = now() - 1
    assert claim_send(env[1], rid) is None
    with env[1].transaction() as s:
        assert s.get(Reply, rid).state == "expired"


def test_catnest_optout_rechecked_at_send_time(env):
    rid, eid = draft(env)
    with env[1].transaction() as s:
        nest = Record(kind="nest", owner=env[3]["editor"], title="room", data={"topic_id":42,"diary":True,"opted_out":True})
        s.add(nest); s.flush()
        e=s.get(Event,eid); e.data={**e.data,"nest_id":nest.id,"source":"diary"}
    assert not claim_send(env[1],rid)


def test_budget_all_routes_reserve_and_settle(env):
    db = env[1]
    p = Policy(daily_tokens=2000, topic_tokens=2000)
    a = reserve(db, "planner", 42, 1000, p)
    with pytest.raises(BudgetExceeded):
        reserve(db, "replyer", 42, 1100, p)
    settle(db, a, 300)
    b = reserve(db, "memory", 42, 1000, p)
    settle(db, b, None, failed=True)
    c = reserve(db, "summary", 42, 600, p)
    settle(db, c, 100)
    with db.transaction() as s:
        assert s.scalar(select(func.sum(Usage.tokens))) == 1400


class FakeForum:
    connection = {"username": "cat"}
    def __init__(self):
        self.ns = [{"id": 100, "topic_id": 42, "notification_type": 1, "read": False, "data": {}}]
        self.topics = [{"id": 42, "highest_post_number": 100}]
        self.calls = 0
    async def notifications(self): return self.ns
    async def latest(self): return self.topics
    async def topic(self, tid, limit):
        return {"archetype": "regular", "title": "Hello", "context": [{"id": 20, "number": 3, "username": "human", "text": "hi"}]}
    async def reply(self, *args):
        self.calls += 1
        raise httpx.ReadTimeout("uncertain")


async def test_baseline_skips_unread_backlog_and_hot_can_retrigger(env):
    epoch, version = activate(env)
    worker = Worker(env[1], env[2]); worker.forum = FakeForum()
    await worker.baseline(epoch)
    with env[1].transaction() as s:
        from suenmeow.service import get_snapshot
        control, snapshot = get_snapshot(s)
    await worker.collect(control, snapshot)
    with env[1].transaction() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 0
    worker.forum.ns.append({"id": 101, "topic_id": 42, "notification_type": 1, "read": False, "data": {}})
    worker.forum.topics[0]["highest_post_number"] = 106
    worker.last_poll = worker.last_hot = 0
    await worker.collect(control, snapshot)
    worker.forum.topics[0]["highest_post_number"] = 112
    worker.last_poll = worker.last_hot = 0
    await worker.collect(control, snapshot)
    with env[1].transaction() as s:
        receipts = list(s.scalars(select(Event.receipt)))
        assert "notification:100" not in receipts
        assert sorted(receipts) == ["hot:42:106", "hot:42:112", "notification:101"]


async def test_send_timeout_never_replayed(env):
    rid, eid = draft(env)
    worker = Worker(env[1], env[2]); worker.forum = FakeForum()
    await worker.send_one(); await worker.send_one()
    assert worker.forum.calls == 1
    with env[1].transaction() as s:
        assert s.get(Reply, rid).state == "unknown"


async def test_memory_failure_preserves_sent_reply(env):
    rid, eid = draft(env)
    with env[1].transaction() as s:
        r = s.get(Reply, rid); r.state = "sent"; r.sent_post_id = 21
    worker = Worker(env[1], env[2]); worker.forum = FakeForum()
    class BrokenModels:
        async def complete(self, *a): raise RuntimeError("model failed")
    worker.models = BrokenModels()
    await worker.remember_one(); await worker.send_one()
    with env[1].transaction() as s:
        assert s.get(Reply, rid).state == "sent"
        assert s.get(Reply, rid).memory_state == "failed"
    assert worker.forum.calls == 0


def test_private_memory_not_used_publicly_or_in_other_pm(env):
    with env[1].transaction() as s:
        s.add(Record(kind="memory", owner=env[3]["editor"], title="secret", data={"cipher": env[2].seal({"text": "secret", "username": "human", "topic_id": 42, "scope": "private"})}))
    w = Worker(env[1], env[2])
    assert not w.memories(42, False, {"human"})
    assert not w.memories(43, True, {"human"})
    assert w.memories(42, True, {"human"})[0]["text"] == "secret"


async def test_discourse_reply_payload_never_creates_topic():
    requests = []
    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 123})
    forum = Discourse({"base_url": "https://example.com", "username": "cat", "api_key": "fake"}, httpx.MockTransport(handle))
    with pytest.raises(ValueError): await forum.reply(0, "hi")
    assert await forum.reply(42, "hi") == 123
    assert requests == [{"topic_id": 42, "raw": "hi"}]
    await forum.close()
