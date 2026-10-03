from concurrent.futures import ThreadPoolExecutor
import os

import pytest
from sqlalchemy import func, select

from suenmeow.database import Event, KV, Reply, Usage, now
from suenmeow.domain import Policy
from suenmeow.service import BudgetExceeded, add_event, claim_send, reserve, settle
from test_safety import activate

pytestmark = pytest.mark.skipif(os.getenv("SUENMEOW_TEST_POSTGRES") != "1", reason="Requires isolated PostgreSQL schemas")


def test_parallel_budget_reservation_cannot_overspend(env):
    p = Policy(daily_tokens=2000, topic_tokens=2000)
    def attempt(i):
        try:
            return reserve(env[1], "planner", 42, 600, p)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert len([r for r in results if r]) == 3
    with env[1].transaction() as s:
        assert s.scalar(select(func.sum(Usage.tokens))) == 1800


def test_parallel_send_claim_has_one_winner(env):
    epoch, version = activate(env)
    eid = add_event(env[1], "n:1", 42, {}, epoch, version, 600)
    with env[1].transaction() as s:
        s.get(Event, eid).state = "drafted"
        r = Reply(event_id=eid, topic_id=42, text_cipher=env[2].seal("hi"), state="ready")
        s.add(r); s.flush(); rid = r.id
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: claim_send(env[1], rid), range(8)))
    assert len([c for c in claims if c]) == 1


def test_parallel_duplicate_notifications_are_one_event(env):
    epoch, version = activate(env)
    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(lambda _: add_event(env[1], "notification:777", 42, {}, epoch, version, 600), range(8)))
    assert len([e for e in events if e]) == 1


def test_parallel_agent_instructions_enforce_one_task_per_admin(env):
    from fastapi import HTTPException
    from suenmeow.agent import claim_task, enqueue
    from suenmeow.database import AgentSession
    from suenmeow.domain import AgentMessageInput
    from suenmeow.service import publish
    with env[1].transaction() as s:
        publish(s, env[3]["admin"], "agent")
        chat = AgentSession(owner=env[3]["admin"])
        s.add(chat)
        s.flush()
        sid = chat.id
    def attempt(i):
        try:
            with env[1].transaction() as s:
                return enqueue(s, env[2], env[3]["admin"], sid, AgentMessageInput(text="研究公开讨论"))
        except HTTPException as exc:
            assert exc.status_code == 409
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert len([x for x in results if x]) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: claim_task(env[1]), range(8)))
    assert len([x for x in claims if x]) == 1


def test_parallel_agent_task_reservations_share_a_hard_limit(env):
    p = Policy(daily_tokens=10000, topic_tokens=10000)
    def attempt(i):
        try:
            return reserve(env[1], "agent", 0, 600, p, "shared-agent-task", 2000)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert len([x for x in results if x]) == 3
