from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from conftest import login
from suenmeow.database import Event, KV, Record, Reply, now
from suenmeow.domain import Policy
from suenmeow.service import add_event, claim_send, get_snapshot, publish
from suenmeow.worker import Worker
from test_pipeline import GoodModels
from test_safety import activate


class BotForum:
    connection = {'username': 'cat'}
    public = True
    author = 17
    closed = False
    private = False
    calls = 0

    def __init__(self, connection=None):
        if connection: self.connection = connection
    async def login(self): pass
    async def close(self): pass
    async def read(self, path): return {'current_user': {'id': 17, 'username': 'cat'}}
    async def public_visible(self, topic): return self.public
    async def topic(self, topic_id, limit):
        return {'id': topic_id, 'title': '猫自己的个人贴', 'closed': self.closed,
                'archetype': 'private_message' if self.private else 'regular',
                'context': [{'id': 20, 'number': 1, 'user_id': self.author, 'username': 'cat', 'text': '虚拟猫窝'},
                            {'id': 21, 'number': 2, 'user_id': 17, 'username': 'cat', 'text': '昨天的日常'}]}
    async def reply(self, *args): self.calls += 1; return 23


def setup_home(env, enabled=True):
    with env[1].transaction() as s:
        s.add(KV(key='connection:forum', data={'cipher': env[2].seal({'base_url': 'https://forum.example', 'username': 'cat', 'password': 'fake-only'})}))
        row = s.get(KV, 'cat_nest')
        row.data = {'topic_id': 42, 'title': '猫窝', 'enabled': enabled, 'owner': env[3]['admin'],
                    'bot_id': 17, 'forum_version': 1, 'site': 'https://forum.example',
                    'saved_at': now() - 10000, 'idle_minutes': 120, 'notes': '', 'objects': '', 'activity': ''}
        s.get(KV, 'policy').data = Policy(playful=True, timezone='UTC').model_dump()
    epoch, _ = activate(env)
    with env[1].transaction() as s:
        _, snapshot = get_snapshot(s)
    return epoch, snapshot


def test_editor_readonly_and_no_legacy_mutations(client, env):
    setup_home(env)
    with env[1].transaction() as s:
        r = Record(kind='nest', owner=env[3]['editor'], title='old', data={'topic_id': 10})
        s.add(r); s.flush(); rid = r.id
    login(client, 'editor', 'editor-test-password')
    assert client.get('/api/cat-nest').json()['topic_id'] == 42
    assert client.get('/api/records/nest').status_code == 403
    body = {'version': 1, 'topic_id': 42}
    assert client.put('/api/cat-nest', json=body).status_code == 403
    assert client.post('/api/records/nest', json={'title': 'a', 'data': {}}).status_code == 403
    assert client.put('/api/records/nest/' + rid, json={'title': 'a', 'data': {}, 'version': 1}).status_code == 403
    assert client.delete('/api/records/nest/' + rid).status_code == 403


@pytest.mark.parametrize('failure', ['author', 'public', 'closed', 'private'])
def test_admin_cannot_bind_human_private_or_closed_topic(client, env, monkeypatch, failure):
    setup_home(env)
    monkeypatch.setattr('suenmeow.cat_nest.Discourse', BotForum)
    monkeypatch.setattr(BotForum, failure, {'author': 18, 'public': False, 'closed': True, 'private': True}[failure])
    login(client)
    assert client.put('/api/cat-nest', json={'version': 1, 'topic_id': 42, 'enabled': True}).status_code == 422
    assert client.get('/api/cat-nest').json()['version'] == 1


def test_singleton_version_csrf_and_admin_binding(client, env, monkeypatch):
    setup_home(env)
    monkeypatch.setattr('suenmeow.cat_nest.Discourse', BotForum)
    login(client)
    body = {'version': 1, 'topic_id': 43, 'enabled': False, 'notes': '虚拟设定'}
    assert client.put('/api/cat-nest', json=body, headers={'x-csrf-token': 'wrong'}).status_code == 403
    assert client.put('/api/cat-nest', json=body).status_code == 200
    assert client.put('/api/cat-nest', json=body).status_code == 409
    assert client.post('/api/records/nest', json={'title': 'old', 'data': {'topic_id': 99}}).status_code == 410
    assert client.get('/api/cat-nest').json()['topic_id'] == 43


async def test_idle_daily_receipt_and_restart_no_catchup(env, monkeypatch):
    epoch, snapshot = setup_home(env)
    class Midday(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026,10,10,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr('suenmeow.worker.datetime', Midday)
    w = Worker(env[1], env[2]); w.epoch = epoch; w.baseline_time = now()
    await w.playful(snapshot)
    with env[1].transaction() as s: assert not s.scalar(select(Event))
    w.baseline_time = now() - 10000; w.last_play = 0
    await w.playful(snapshot)
    with env[1].transaction() as s:
        e = s.scalar(select(Event)); assert e.data['cat_nest'] and e.topic_id == 42
        e.state = 'skipped'
    w.last_play = 0; await w.playful(snapshot)
    with env[1].transaction() as s: assert len(list(s.scalars(select(Event)))) == 1


async def test_diary_without_visitors_no_human_memory_or_research(env):
    epoch, snapshot = setup_home(env)
    eid = add_event(env[1], 'cat-nest:today', 42, {'source': 'diary', 'cat_nest': True, 'nest_version': 1}, epoch, snapshot.id, 600)
    w = Worker(env[1], env[2]); w.epoch = epoch; w.forum = BotForum(); w.models = GoodModels()
    await w.draft_one()
    with env[1].transaction() as s:
        r = s.scalar(select(Reply)); assert r.state == 'ready'
        assert s.get(Event, eid).data['post_number'] == 0
    await w.send_one(); await w.remember_one()
    assert w.forum.calls == 1 and w.models.routes == ['planner', 'replyer']
    with env[1].transaction() as s:
        assert not s.scalar(select(Record).where(Record.kind == 'memory'))
        assert s.scalar(select(Reply)).memory_state == 'done'


@pytest.mark.parametrize('change', ['version', 'enabled', 'connection', 'private', 'legacy'])
def test_shared_gate_rechecks_nest_changes(env, change):
    epoch, snapshot = setup_home(env)
    eid = add_event(env[1], 'cat-nest:today', 42, {'source': 'diary', 'cat_nest': True, 'nest_version': 1}, epoch, snapshot.id, 600)
    with env[1].transaction() as s:
        e = s.get(Event, eid); e.state = 'drafted'
        r = Reply(event_id=eid, topic_id=42, state='ready', text_cipher=env[2].seal('虚拟日常'))
        s.add(r); s.flush(); rid = r.id
        row = s.get(KV, 'cat_nest')
        if change == 'version': row.version += 1
        if change == 'enabled': row.data = {**row.data, 'enabled': False}
        if change == 'connection': s.get(KV, 'connection:forum').version += 1
        if change == 'private': e.data = {**e.data, 'private': True}
        if change == 'legacy': e.data = {'source': 'diary', 'nest_id': 'old'}
    assert claim_send(env[1], rid) is None


async def test_final_send_checks_topic_visibility_again(env):
    epoch, snapshot = setup_home(env)
    add_event(env[1], 'cat-nest:today', 42, {'source': 'diary', 'cat_nest': True, 'nest_version': 1}, epoch, snapshot.id, 600)
    w = Worker(env[1], env[2]); w.epoch = epoch; w.forum = BotForum(); w.models = GoodModels()
    await w.draft_one()
    w.forum.public = False
    await w.send_one()
    assert w.forum.calls == 0
    with env[1].transaction() as s: assert s.scalar(select(Reply)).state == 'expired'


def test_disable_same_nest_without_forum_access(client, env, monkeypatch):
    setup_home(env)
    def offline(*args): raise AssertionError('must not contact the forum when disabling')
    monkeypatch.setattr('suenmeow.cat_nest.Discourse', offline)
    login(client)
    r = client.put('/api/cat-nest', json={'version': 1, 'topic_id': 42, 'enabled': False})
    assert r.status_code == 200 and not r.json()['available']
    assert r.json()['version'] == 2


@pytest.mark.parametrize('blocker', ['disabled', 'busy', 'recent_send'])
async def test_idle_attempt_waits_for_enable_and_free_time(env, monkeypatch, blocker):
    epoch, snapshot = setup_home(env, enabled=blocker != 'disabled')
    class Midday(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026,10,10,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr('suenmeow.worker.datetime', Midday)
    if blocker == 'busy': add_event(env[1], 'notification:busy', 43, {}, epoch, snapshot.id, 600)
    if blocker == 'recent_send':
        with env[1].transaction() as s: s.get(KV, 'gate').data = {'last_send': now()}
    w = Worker(env[1], env[2]); w.epoch = epoch; w.baseline_time = now() - 10000
    await w.playful(snapshot)
    with env[1].transaction() as s:
        assert not any(e.data.get('cat_nest') for e in s.scalars(select(Event)))
