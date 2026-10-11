import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from conftest import login
from suenmeow.adapters import safe_post
from suenmeow.database import Event, KV, Observation, Reaction, Reply, now
from suenmeow.domain import Policy
from suenmeow.interactions import claim_reaction
from suenmeow.service import BudgetExceeded, claim_send, mark_unknown, publish, set_mode
from suenmeow.worker import Worker


def post(pid, tid=10, **changes):
    return {'id': pid, 'topic_id': tid, 'post_number': pid, 'post_type': 1, 'user_id': 322,
            'username': 'alice', 'raw': '今天薄荷长高了，很开心。', 'current_user_reaction': None,
            'created_at': datetime.fromtimestamp(now() - 1, timezone.utc).isoformat(), **changes}


class Forum:
    connection = {'base_url': 'https://forum.example', 'username': 'cat'}
    bot_id = 17
    def __init__(self):
        self.posts = [post(1, created_at=datetime.fromtimestamp(now() - 120, timezone.utc).isoformat())]
        self.pm = set()
        self.notices = []
        self.groups = {}
        self.allowed = ['heart', 'hug']
        self.writes = []
        self.fail_toggle = False
        self.fail_read = False

    async def notifications(self): return self.notices
    async def latest(self): return [{'id': 10, 'highest_post_number': max(p['id'] for p in self.posts)}]
    async def latest_posts(self, before=None): return [p for p in reversed(self.posts) if p['topic_id'] not in self.pm and (not before or p['id'] < before)]
    async def topic(self, tid, limit):
        if self.fail_read: raise RuntimeError('read unavailable')
        rows = [p for p in self.posts if p['topic_id'] == tid and not p.get('hidden') and not p.get('deleted_at')]
        return {'id': tid, 'category_id': 22, 'title': '薄荷的个人贴', 'archetype': 'private_message' if tid in self.pm else 'regular',
                'allowed_users': [{'id': x} for x in self.groups.get(tid, [17, 322])] if tid in self.pm else [], 'allowed_groups': [],
                'valid_reactions': self.allowed, 'context': [safe_post(p) for p in rows[-limit:]],
                'post_stream': {'stream': [p['id'] for p in rows]}}
    async def selected_posts(self, tid, ids):
        return [safe_post(p) for p in self.posts if p['topic_id'] == tid and p['id'] in ids and p['post_type'] == 1 and not p.get('hidden') and not p.get('deleted_at')]
    async def public_visible(self, topic): return topic['id'] not in self.pm
    async def reaction_post(self, pid): return deepcopy(next(p for p in self.posts if p['id'] == pid))
    async def read(self, path):
        assert path == '/session/current.json'
        return {'current_user': {'id': self.bot_id, 'username': 'cat'}}
    async def react(self, pid, emoji):
        self.writes.append(('reaction', pid, emoji))
        item = next(p for p in self.posts if p['id'] == pid)
        item['current_user_reaction'] = {'id': emoji}
        if self.fail_toggle: raise TimeoutError('response lost')
        return deepcopy(item)
    async def reply(self, tid, text, reply_to): self.writes.append(('reply', tid, text)); return 999


class Models:
    def __init__(self): self.calls = []; self.change = None; self.fail = None
    async def complete(self, route, messages, tid, policy, **kwargs):
        self.calls.append((route, tid, deepcopy(messages)))
        if self.fail: raise self.fail
        if route == 'replyer': return '这是有必要展开的说明。' * 80
        data = json.loads(messages[1]['content'])
        result = {'posts': [{'post_id': p['id'], 'reaction': 'heart', 'score': .9, 'reason': '开心的生活分享'} for p in data['new_posts']],
                  'reply': {'post_id': data['new_posts'][-1]['id'] if data['need_reply'] else 0,
                            'text': '薄荷今天也努力长大了一点喵。' if data['need_reply'] else '', 'reason': '接住日常分享'}}
        if self.change: self.change(result)
        return json.dumps(result, ensure_ascii=False)


@pytest.fixture
def engine(env):
    _, db, vault, ids = env
    with db.transaction() as s:
        defaults = dict(s.get(KV, 'interaction_defaults').data)
        defaults['reaction_percent'] = 100
        defaults['unmentioned_percent'] = 100
        defaults['reply_topic_hourly'] = 100
        s.get(KV, 'policy').data = Policy(interaction=defaults).model_dump()
        s.add(KV(key='connection:forum', data={'cipher': vault.seal(Forum.connection)}))
        publish(s, ids['admin'], 'isolated protocol 2')
        set_mode(s, 'auto', ids['admin'])
    w = Worker(db, vault)
    w.forum, w.models = Forum(), Models()
    return w


async def collect(w, posts=None):
    if not w.epoch:
        with w.db.transaction() as s: epoch = s.get(KV, 'control').data['epoch']
        await w.baseline(epoch)
    if posts:
        for item in posts: item['created_at'] = datetime.fromtimestamp(now(), timezone.utc).isoformat()
        w.forum.posts.extend(posts)
    w.interactions.poll_at = 0
    with w.db.transaction() as s:
        c = s.get(KV, 'control').data
        from suenmeow.database import Snapshot
        snap = s.get(Snapshot, c['active_snapshot'])
    await w.interactions.collect(c, snap)
    with w.db.transaction() as s:
        for o in s.scalars(select(Observation)): o.created = now() - 15


@pytest.mark.asyncio
async def test_new_posts_batch_once_and_no_memory_call(engine):
    w = engine
    await collect(w, [post(2), post(3)])
    await w.interactions.prepare_one()
    assert len(w.models.calls) == 1 and w.models.calls[0][0:2] == ('planner', 10)
    with w.db.transaction() as s:
        assert len(list(s.scalars(select(Observation)))) == 2
        assert all(o.state == 'evaluated' for o in s.scalars(select(Observation)))
        assert len(list(s.scalars(select(Reaction)))) == 2
        assert s.scalar(select(Reply)).memory_state == 'done'
    await w.send_one()
    await w.remember_one()
    assert len(w.models.calls) == 1 and len(w.forum.writes) == 1
    # A reaction cannot burst immediately after a text write.
    await w.interactions.send_one()
    assert len(w.forum.writes) == 1


@pytest.mark.asyncio
async def test_explicit_complex_reply_uses_second_route_and_still_no_memory_call(engine):
    engine.models.change = lambda result: result['reply'].update(long=True)
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    assert [c[0] for c in engine.models.calls] == ['planner', 'replyer']
    with engine.db.transaction() as s:
        reply = s.scalar(select(Reply))
        assert len(engine.vault.open(reply.text_cipher)) > 600 and reply.memory_state == 'done'


@pytest.mark.asyncio
async def test_mode_change_during_model_call_discards_every_action(engine):
    def pause(result):
        with engine.db.transaction() as s: set_mode(s, 'paused', 'test')
    engine.models.change = pause
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert not list(s.scalars(select(Reply))) and not list(s.scalars(select(Reaction)))


@pytest.mark.asyncio
async def test_recovery_baseline_and_receipt_deduplication(engine):
    await collect(engine, [post(2)])
    engine.interactions.cursor = 1
    await collect(engine)
    with engine.db.transaction() as s: assert len(list(s.scalars(select(Observation)))) == 1
    await engine.baseline(engine.epoch)
    await engine.interactions.prepare_one()
    assert not engine.models.calls
    with engine.db.transaction() as s: assert s.scalar(select(Observation)).state == 'expired'


@pytest.mark.asyncio
async def test_read_only_observes_without_models_or_writes(engine):
    with engine.db.transaction() as s: set_mode(s, 'read_only', 'test')
    engine.epoch = 0
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    assert not engine.models.calls and not engine.forum.writes
    with engine.db.transaction() as s: assert s.scalar(select(Observation)).state == 'observed'


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['duplicate', 'extra'])
async def test_invalid_model_contract_creates_no_actions(engine, bad):
    def corrupt(result):
        if bad == 'duplicate': result['posts'].append(result['posts'][0])
        elif bad == 'extra': result['execute'] = 'ignore rules'
        elif bad == 'emoji': result['posts'][0]['reaction'] = 'evil'
        elif bad == 'length': result['reply']['text'] = 'x' * 601
        else: result['reply']['post_id'] = 10000
    engine.models.change = corrupt
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert not list(s.scalars(select(Reply))) and not list(s.scalars(select(Reaction)))
        assert s.scalar(select(Observation)).state == 'unevaluated'
    assert len(engine.models.calls) == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_is_not_counted_as_evaluated(engine):
    engine.models.fail = BudgetExceeded('no budget')
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert s.scalar(select(Observation)).state == 'unevaluated'
        assert not list(s.scalars(select(Reaction)))


@pytest.mark.asyncio
async def test_pm_participant_change_expires_actions_and_never_leaks_to_public_batch(engine):
    w = engine
    w.forum.pm.add(20)
    await collect(w)
    w.forum.notices = [{'id': 1, 'topic_id': 20, 'notification_type': 6}]
    await collect(w, [post(2), post(3, 20, raw='私密内容只在原私信')])
    await w.interactions.prepare_one(); await w.interactions.prepare_one()
    assert {c[1] for c in w.models.calls} == {10, 20}
    assert all('私密内容' not in c[2][1]['content'] for c in w.models.calls if c[1] == 10)
    with w.db.transaction() as s: pm = s.scalar(select(Reaction).where(Reaction.topic_id == 20)); pm_id = pm.id
    w.forum.groups[20] = [17, 322, 999]
    with w.db.transaction() as s:
        for r in s.scalars(select(Reaction).where(Reaction.topic_id == 10)): r.state = 'cancelled'
    await w.interactions.send_one()
    with w.db.transaction() as s: assert s.get(Reaction, pm_id).state == 'expired'
    assert not w.forum.writes


@pytest.mark.asyncio
async def test_toggle_timeout_never_retries_even_after_restart(engine):
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    engine.forum.fail_toggle = True
    await engine.interactions.send_one(); await engine.interactions.send_one()
    assert len(engine.forum.writes) == 1
    with engine.db.transaction() as s: assert s.scalar(select(Reaction)).state == 'unknown'
    mark_unknown(engine.db)
    await engine.interactions.send_one()
    assert len(engine.forum.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', ['heart', 'hug'])
async def test_existing_reaction_never_toggles(engine, existing):
    await collect(engine, [post(2, current_user_reaction={'id': existing})])
    await engine.interactions.prepare_one()
    await engine.interactions.send_one()
    assert not engine.forum.writes
    with engine.db.transaction() as s: assert s.scalar(select(Reaction)).state == ('sent' if existing == 'heart' else 'cancelled')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['visibility', 'source', 'whitelist', 'connection', 'new_post'])
async def test_final_source_and_configuration_changes_reject_writes(engine, change):
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    if change == 'visibility': engine.forum.pm.add(10)
    elif change == 'source': engine.forum.posts[-1]['raw'] = '已经编辑'
    elif change == 'whitelist': engine.forum.allowed = ['hug']
    elif change == 'connection':
        with engine.db.transaction() as s: s.get(KV, 'connection:forum').version += 1
    elif change == 'new_post': engine.forum.posts.append(post(3))
    await engine.send_one()
    if change != 'new_post': await engine.interactions.send_one()
    if change != 'whitelist':
        assert not engine.forum.writes
        with engine.db.transaction() as s: assert s.scalar(select(Reply)).state == 'expired'
    else:
        assert all(write[0] != 'reaction' for write in engine.forum.writes)


@pytest.mark.asyncio
async def test_reaction_and_reply_share_atomic_gate_and_reaction_has_own_quota(engine):
    await collect(engine, [post(2), post(3)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        reactions = list(s.scalars(select(Reaction))); rid = s.scalar(select(Reply.id))
    t = now()
    assert claim_reaction(engine.db, reactions[0].id, t)
    assert not claim_send(engine.db, rid, t)
    assert claim_send(engine.db, rid, t + 16)
    assert not claim_reaction(engine.db, reactions[1].id, t + 17)


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['reaction_daily', 'reaction_topic_hourly', 'reaction_user_hourly'])
async def test_reaction_quota_counts_uncertain_inflight_attempts(engine, limit):
    with engine.db.transaction() as s:
        from suenmeow.database import Snapshot
        snap = s.get(Snapshot, s.get(KV, 'control').data['active_snapshot'])
        data = deepcopy(snap.data); data['policy']['interaction'][limit] = 1; snap.data = data
    await collect(engine, [post(2), post(3)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s: ids = list(s.scalars(select(Reaction.id)))
    t = now()
    assert claim_reaction(engine.db, ids[0], t)
    with engine.db.transaction() as s: s.get(Reaction, ids[0]).state = 'unknown'
    assert not claim_reaction(engine.db, ids[1], t + 16)


@pytest.mark.asyncio
async def test_sensitive_identity_and_system_posts_do_not_reach_model(engine):
    await collect(engine, [post(2, raw='登录验证码 SM-' + 'a' * 32), post(3, post_type=4)])
    await engine.interactions.prepare_one()
    assert not engine.models.calls
    with engine.db.transaction() as s:
        assert len(list(s.scalars(select(Observation)))) == 2
        assert all(o.state == 'skipped' for o in s.scalars(select(Observation)))


@pytest.mark.asyncio
async def test_no_suitable_suggestions_never_forces_reactions(engine):
    def none(result):
        for p in result['posts']: p['reaction'], p['score'], p['reason'] = '', 0, '严肃求助，不适合表情'
    engine.models.change = none
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s: assert not list(s.scalars(select(Reaction)))


@pytest.mark.asyncio
async def test_crisis_reaction_is_blocked_even_if_model_suggests_one(engine):
    await collect(engine, [post(2, raw='虚构安全验收：我有轻生的念头，正在寻求帮助。')])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert not list(s.scalars(select(Reaction)))
        assert s.scalar(select(Observation)).data['emoji'] == ''


@pytest.mark.asyncio
async def test_read_only_reconciliation_confirms_without_a_second_toggle(engine, client, monkeypatch):
    import suenmeow.interactions as module
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    engine.forum.fail_toggle = True
    await engine.interactions.send_one()
    async def no_op(): pass
    engine.forum.login, engine.forum.close = no_op, no_op
    monkeypatch.setattr(module, 'Discourse', lambda _: engine.forum)
    login(client)
    with engine.db.transaction() as s:
        rid = s.scalar(select(Reaction.id)); set_mode(s, 'paused', 'test')
    response = client.post(f'/api/reactions/{rid}/check', json={})
    assert response.status_code == 200 and response.json()['state'] == 'sent'
    assert len(engine.forum.writes) == 1


@pytest.mark.asyncio
async def test_reaction_api_admin_csrf_and_mode_bound_approval(engine, client):
    with engine.db.transaction() as s: set_mode(s, 'approval', 'test')
    engine.epoch = 0
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    login(client, 'editor', 'editor-test-password')
    assert client.get('/api/interactions').status_code == 403
    login(client)
    data = client.get('/api/interactions').json()
    assert data['eligible_recent'] == 1 and data['reactions'][0]['text']
    rid = data['reactions'][0]['id']
    csrf = client.headers.pop('x-csrf-token')
    assert client.post(f'/api/reactions/{rid}/decision', json={'action': 'approve'}).status_code == 403
    client.headers['x-csrf-token'] = csrf
    with engine.db.transaction() as s: set_mode(s, 'paused', 'test')
    assert client.post(f'/api/reactions/{rid}/decision', json={'action': 'approve'}).status_code == 409


@pytest.mark.asyncio
async def test_rolling_ratio_only_selects_suitable_proposals(engine):
    with engine.db.transaction() as s:
        from suenmeow.database import Snapshot
        c = s.get(KV, 'control').data
        snapshot = s.get(Snapshot, c['active_snapshot'])
        p = deepcopy(snapshot.data); p['policy']['interaction']['reaction_percent'] = 33; snapshot.data = p
    # Six batches, all suitable, with simulated confirmations for selection accounting.
    await collect(engine)
    for offset in range(0, 120, 20):
        await collect(engine, [post(i) for i in range(offset + 2, offset + 22)])
        await engine.interactions.prepare_one()
        with engine.db.transaction() as s:
            for r in s.scalars(select(Reaction).where(Reaction.state == 'ready')): r.state, r.claimed = 'sent', now()
            for r in s.scalars(select(Reply).where(Reply.state == 'ready')): r.state = 'cancelled'
    with engine.db.transaction() as s:
        total = len(list(s.scalars(select(Reaction).where(Reaction.state == 'sent'))))
    assert 30 <= total <= 40
    assert len(engine.models.calls) == 6


@pytest.mark.skipif(os.getenv('SUENMEOW_TEST_POSTGRES') != '1', reason='PostgreSQL concurrency')
@pytest.mark.asyncio
async def test_postgres_concurrent_claim_once(engine):
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s: rid = s.scalar(select(Reaction.id))
    t = now()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim_reaction(engine.db, rid, t), range(2)))
    assert sorted(results) == [False, True]


@pytest.mark.parametrize('bad', ['length', 'wrong_target', 'schema'])
async def test_bad_reply_preserves_valid_reaction(engine, bad):
    def corrupt(result):
        if bad == 'length': result['reply']['text'] = 'x' * 601
        elif bad == 'wrong_target': result['reply']['post_id'] = 10000
        else: result['reply']['post_id'] = 'not-an-integer'
    engine.models.change = corrupt
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert not list(s.scalars(select(Reply)))
        assert s.scalar(select(Reaction)).emoji == 'heart'
        assert s.scalar(select(Observation)).state == 'evaluated'


async def test_unknown_emoji_does_not_discard_valid_reply(engine):
    engine.models.change = lambda result: result['posts'][0].update(reaction='unsupported')
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert s.scalar(select(Reply)) is not None
        assert s.scalar(select(Reaction)) is None


async def test_extended_model_failure_preserves_independent_reaction(engine):
    engine.models.change = lambda result: result['reply'].update(long=True)
    complete = engine.models.complete
    async def failing(route, *args, **kwargs):
        if route == 'replyer': raise RuntimeError('provider unavailable')
        return await complete(route, *args, **kwargs)
    engine.models.complete = failing
    await collect(engine, [post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert not list(s.scalars(select(Reply)))
        assert s.scalar(select(Reaction)) is not None


async def test_explicit_all_memory_request_uses_agent_when_unsolicited_research_is_off(engine, env):
    from suenmeow.database import AgentTask, Record
    with engine.db.transaction() as s:
        assert s.get(KV, 'agent_policy').data.get('auto_research', False) is False
        for index in range(4):
            data = {'text': f'记忆事实 {index}', 'username': 'alice', 'forum_user_id': 322,
                    'scope': 'public', 'site': engine.forum.connection['base_url'], 'topic_id': 10, 'source_post_id': 1}
            s.add(Record(kind='memory', owner=env[3]['editor'], title='fixture', data={'cipher': engine.vault.seal(data)}))
    async def tool_turn(messages, tools, *args, **kwargs):
        provided = next(json.loads(m['content'].split('：', 1)[1]) for m in messages
                        if m['role'] == 'user' and m['content'].startswith('服务端已执行完整记忆读取'))
        assert provided['complete'] and provided['total'] == 4
        assert {t['function']['name'] for t in tools} <= {'memory_lookup', 'memory_read_all', 'forum_read_topic'}
        return {'role': 'assistant', 'content': '已核验并完整读取 alice 的四条可用记忆。'}, False
    engine.models.tool_turn = tool_turn
    await collect(engine, [post(2, raw='@cat，读取我的全部记忆，再告诉我。')])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        task = s.scalar(select(AgentTask))
        assert task.state == 'completed' and task.constraints['explicit_memory_request']
        assert task.constraints['memory_coverage']['alice']['total'] == 4
        assert s.scalar(select(Reply)) is not None
    assert engine.forum.writes == []


def test_reply_quotas_count_new_policy_across_versions_without_retrospective_shutdown(engine):
    from copy import deepcopy
    from suenmeow.database import Snapshot
    from suenmeow.interactions import reply_allowance
    with engine.db.transaction() as s:
        control = s.get(KV, 'control').data
        current = s.get(Snapshot, control['active_snapshot'])
        ip = Policy.model_validate(current.data['policy']).interaction
        ip.reply_daily = 1
        legacy = deepcopy(current.data)
        legacy['policy']['interaction'].pop('reply_daily')
        old = Snapshot(actor='fixture', data=legacy)
        s.add(old); s.flush()
        event = Event(receipt='interaction:legacy-quota', topic_id=10, snapshot_id=old.id,
                      epoch=control['epoch'], state='sent', data={}, expires=now()+600)
        s.add(event); s.flush()
        s.add(Reply(event_id=event.id, topic_id=10, text_cipher=engine.vault.seal('old'), state='sent'))
        s.flush()
        assert reply_allowance(s, 10, 322, ip) == ''
        newer = Snapshot(actor='fixture', data=deepcopy(current.data))
        s.add(newer); s.flush()
        event2 = Event(receipt='interaction:new-quota', topic_id=10, snapshot_id=newer.id,
                       epoch=control['epoch'], state='drafted', data={'reply_user_id':322}, expires=now()+600)
        s.add(event2); s.flush()
        reply = Reply(event_id=event2.id, topic_id=10, text_cipher=engine.vault.seal('new'), state='unknown')
        s.add(reply); s.flush()
        assert reply_allowance(s, 10, 322, ip) == '每日文字参与上限'
        assert reply_allowance(s, 10, 322, ip, exclude_id=reply.id) == ''
