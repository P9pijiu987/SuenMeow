import json

import httpx
import pytest
from sqlalchemy import select

from conftest import login
from suenmeow.adapters import Discourse, Models
from suenmeow.database import ForumIdentity, KV, MemoryCursor, MemoryImport, Record, Usage, day, now
from suenmeow.domain import Policy
from suenmeow.memory_import import process_import
from suenmeow.service import BudgetExceeded, publish
from suenmeow.worker import Worker


class Forum:
    public = True
    private = False
    posts = []

    def __init__(self, connection): self.connection = connection
    async def login(self): pass
    async def close(self): pass
    async def public_visible(self, topic): return self.public
    async def user_topics(self, username): return [{'topic_id': 42, 'title': '个人贴'}]
    async def topic(self, topic_id, limit):
        return {'id': topic_id, 'title': '个人贴', 'archetype': 'private_message' if self.private else 'regular',
                'post_stream': {'stream': [p['id'] for p in self.posts]}, 'context': self.posts[:1] + self.posts[-limit:]}
    async def selected_posts(self, topic_id, ids): return [p for p in self.posts if p['id'] in ids]


def post(i, user_id=7, **changes):
    return {'id': i, 'number': i, 'user_id': user_id, 'username': 'alice' if user_id == 7 else 'visitor',
            'text': f'我喜欢照料盆栽{i}。', **changes}


@pytest.fixture
def configured(client, env, monkeypatch):
    import suenmeow.memory_import as module
    class Fake(Forum):
        posts = [post(1), post(2, 8), post(3, has_quotes=True), post(4, identity_message=True), post(5)]
    monkeypatch.setattr(module, 'Discourse', Fake)
    login(client)
    _, db, vault, ids = env
    with db.transaction() as s:
        s.add(KV(key='connection:forum', data={'cipher': vault.seal({'base_url': 'https://forum.example', 'username': 'cat'})}))
        s.add(KV(key='connection:memory', data={'cipher': vault.seal({'base_url': 'https://model.example', 'model': 'fake', 'api_key': 'fake', 'max_output': 1000, 'temperature': 0.2})}))
        publish(s, ids['admin'], 'memory import test')
    return Fake


def preview(client, **kwargs):
    response = client.post('/api/memory-imports', json={'topic_id': 42, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


class FakeModels:
    calls = 0
    async def complete(self, route, messages, topic_id, policy, **kwargs):
        self.calls += 1
        assert route == 'memory' and topic_id == 42 and kwargs['task_limit'] >= 3000
        posts = json.loads(messages[1]['content'])['posts']
        assert all(p['id'] not in (2, 3, 4) for p in posts)
        return json.dumps({'facts': [{'text': '喜欢盆栽', 'quote': posts[0]['text'][:100], 'source_post_id': posts[0]['id']},
                                    {'text': '伪造事实', 'quote': '模型编造的原句', 'source_post_id': 2}]}, ensure_ascii=False)
    async def close(self): pass


async def extract(client, env, job, models=None):
    assert client.post(f"/api/memory-imports/{job['id']}/extract").status_code == 200
    with env[1].transaction() as s: s.get(MemoryImport, job['id']).state = 'running'
    models = models or FakeModels()
    await process_import(env[1], env[2], models, job['id'])
    return client.get('/api/memory-imports').json()[0], models


@pytest.mark.asyncio
async def test_author_only_sources_confirm_once_and_incremental_empty(client, env, configured):
    job = preview(client)
    assert job['config']['scanned'] == 5 and job['config']['author_posts'] == 2
    assert 0 < job['config']['reservation'] <= 12000
    assert client.post('/api/memory-imports', json={'topic_id': 42}).status_code == 409
    job, models = await extract(client, env, job)
    assert job['state'] == 'awaiting_save' and len(job['result']['facts']) == 1 and models.calls == 1
    args = {'digest': job['result']['digest'], 'selected': [0]}
    assert client.post(f"/api/memory-imports/{job['id']}/save", json=args).json()['saved'] == 1
    assert client.post(f"/api/memory-imports/{job['id']}/save", json=args).status_code == 409
    memory = client.get('/api/records/memory').json()[0]
    assert memory['data']['forum_user_id'] == 7 and memory['data']['source_post_id'] == 1
    assert client.delete('/api/records/memory/' + memory['id']).status_code == 200
    next_job, no_calls = await extract(client, env, preview(client))
    assert next_job['state'] == 'empty' and no_calls.calls == 0
    assert client.get('/api/records/memory').json() == []


@pytest.mark.parametrize('change', ['private', 'restricted', 'bad_author', 'bot'])
def test_nonpublic_or_unverified_author_is_rejected(client, env, configured, change):
    if change == 'private': configured.private = True
    if change == 'restricted': configured.public = False
    if change == 'bad_author': configured.posts = [post(1, user_id=None)]
    if change == 'bot': configured.posts = [post(1, username='cat')]
    response = client.post('/api/memory-imports', json={'topic_id': 42})
    assert response.status_code == 409
    with env[1].transaction() as s: assert not list(s.scalars(select(Usage)))


def test_topic_link_restricts_origin_and_resolves_topic_not_reply(client, env, configured):
    for url in ['https://evil.example/t/42', 'http://forum.example/t/42', 'https://forum.example@evil.example/t/42', 'https://forum.example/u/alice']:
        assert client.post('/api/memory-imports', json={'topic_url': url}).status_code == 422
    response = client.post('/api/memory-imports', json={'topic_url': 'https://forum.example/t/alice/42/99'})
    assert response.status_code == 200 and response.json()['topic_id'] == 42
    client.post('/api/memory-imports/' + response.json()['id'] + '/cancel')
    response = client.post('/api/memory-imports', json={'topic_url': 'https://forum.example/t/42/99'})
    assert response.status_code == 200 and response.json()['topic_id'] == 42


@pytest.mark.asyncio
async def test_budget_split_keeps_unprocessed_author_post_and_caps_scanning(client, env, configured):
    configured.posts = [post(i, text='我喜欢种花。' * 400) for i in range(1, 152)]
    job = preview(client, max_tokens=6000)
    assert job['config']['reservation'] <= 6000 and job['config']['truncated'] > 0
    assert job['config']['scanned'] < 100 and job['config']['remaining'] > 50
    first_last = job['config']['last']
    job, _ = await extract(client, env, job)
    assert job['state'] == 'awaiting_save'
    client.post(f"/api/memory-imports/{job['id']}/cancel")
    second = preview(client, max_tokens=6000)
    with env[1].transaction() as s:
        payload = env[2].open(s.get(MemoryImport, second['id']).input_cipher)
    assert json.loads(payload[1]['content'])['posts'][0]['id'] == first_last + 1


@pytest.mark.asyncio
async def test_changed_source_and_connection_cannot_save_or_extract(client, env, configured):
    job, _ = await extract(client, env, preview(client))
    configured.posts = [post(1, text='原句已被作者删除'), post(5)]
    args = {'digest': job['result']['digest'], 'selected': [0]}
    assert client.post(f"/api/memory-imports/{job['id']}/save", json=args).status_code == 409
    with env[1].transaction() as s: s.get(KV, 'connection:forum').version += 1
    assert client.post(f"/api/memory-imports/{job['id']}/save", json=args).status_code == 409
    assert client.get('/api/records/memory').json() == []


@pytest.mark.asyncio
async def test_verified_identity_owns_facts_and_editors_cannot_import_other_jobs(client, env, configured):
    with env[1].transaction() as s:
        s.add(ForumIdentity(account_id=env[3]['editor'], site='https://forum.example', user_id=7, profile={'username': 'alice'}))
    job, _ = await extract(client, env, preview(client))
    client.post(f"/api/memory-imports/{job['id']}/save", json={'digest': job['result']['digest'], 'selected': [0]})
    login(client, 'editor', 'editor-test-password')
    assert len(client.get('/api/records/memory').json()) == 1
    assert client.get('/api/memory-imports').json() == []
    assert client.post(f"/api/memory-imports/{job['id']}/extract").status_code == 404
    assert client.post('/api/memory-imports', json={'topic_id': 42}).status_code == 200


@pytest.mark.asyncio
async def test_cancelled_import_does_not_call_model_and_failed_import_keeps_cursor(client, env, configured):
    job = preview(client)
    client.post(f"/api/memory-imports/{job['id']}/cancel")
    models = FakeModels()
    await process_import(env[1], env[2], models, job['id'])
    assert models.calls == 0
    class Broken(FakeModels):
        async def complete(self, *args, **kwargs): raise RuntimeError('do not retry')
    failed, _ = await extract(client, env, preview(client), Broken())
    assert failed['state'] == 'failed'
    with env[1].transaction() as s: assert not list(s.scalars(select(MemoryCursor)))


@pytest.mark.asyncio
async def test_real_adapter_reserves_task_and_topic_before_model_request(env):
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"facts":[]}'}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 123}})
    model = Models(env[1], {'memory': {'base_url': 'https://fake.example/v1', 'api_key': 'fake', 'model': 'fake', 'max_output': 8000, 'temperature': 0}}, transport=httpx.MockTransport(provider))
    messages = [{'role': 'user', 'content': '仅测试预算'}]
    with pytest.raises(BudgetExceeded):
        await model.complete('memory', messages, 42, Policy(), task_id='import-budget', task_limit=500, output_limit=800)
    assert calls == []
    await model.complete('memory', messages, 42, Policy(), task_id='import-budget', task_limit=2000, output_limit=800)
    assert calls[0]['max_tokens'] == 800
    with env[1].transaction() as s:
        usage = s.scalar(select(Usage).where(Usage.task_id == 'import-budget'))
        assert usage.topic_id == 42 and usage.tokens == 123 and usage.state == 'actual'
    await model.close()


@pytest.mark.asyncio
async def test_hidden_activity_falls_back_to_one_bounded_author_search():
    requests = []
    def forum(request):
        requests.append(request)
        if request.url.path == '/user_actions.json':
            assert request.url.params['filter'] == '4'
            return httpx.Response(404, json={})
        assert request.url.path == '/search.json'
        assert request.url.params['q'] == '@alice in:first order:latest'
        assert request.url.params['page'] == '1'
        return httpx.Response(200, json={'topics': [{'id': 42, 'title': '个人贴'}],
                                       'posts': [{'topic_id': 42, 'blurb': '正文不会进入候选'}] * 30})
    client = Discourse({'base_url': 'https://forum.example', 'username': 'cat'}, transport=httpx.MockTransport(forum))
    try:
        candidates = await client.user_topics('alice')
        assert len(candidates) == 20 and candidates[0] == {'topic_id': 42, 'title': '个人贴'}
        assert len(requests) == 2
        requests.clear()
        assert await client.user_topics('alice in:messages') == []
        assert len(requests) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_worker_runs_import_while_paused_without_forum_writes(client, env, configured):
    job = preview(client)
    client.post(f"/api/memory-imports/{job['id']}/extract")
    worker = Worker(env[1], env[2], models_factory=lambda *args: FakeModels())
    await worker.dispatch_import()
    await worker.import_job
    assert worker.forum is None and worker.models is None
    assert client.get('/api/memory-imports').json()[0]['state'] == 'awaiting_save'


@pytest.mark.asyncio
async def test_sensitive_posts_excluded_before_provider_and_forged_quote_dropped(client, env, configured):
    configured.posts = [post(1), post(2, text='我的 API key 是不应发送的内容'), post(3, text='密码为不应发送的内容')]
    job = preview(client)
    assert job['config']['author_posts'] == 1
    job, models = await extract(client, env, job)
    assert len(job['result']['facts']) == 1 and models.calls == 1


@pytest.mark.asyncio
async def test_first_forum_login_adopts_only_matching_stable_identity(client, env, configured):
    from test_forum_auth import Forum as LoginForum, start
    from suenmeow.forum_auth import verify_forum_logins
    job, _ = await extract(client, env, preview(client))
    client.post(f"/api/memory-imports/{job['id']}/save", json={'digest': job['result']['digest'], 'selected': [0]})
    with env[1].transaction() as s:
        s.add(Record(kind='memory', owner=env[3]['admin'], title='same name, different identity', data={'cipher': env[2].seal({
            'origin': 'personal_topic', 'site': 'https://forum.example', 'forum_user_id': 8,
            'username': 'alice', 'text': '同名不可归属', 'scope': 'public', 'topic_id': 99, 'source_post_id': 100})}))
    pending = start(client)
    await verify_forum_logins(env[1], LoginForum(pending['code']), 1)
    response = client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 200
    with env[1].transaction() as s:
        rows = list(s.scalars(select(Record).where(Record.kind == 'memory')))
        assert next(r for r in rows if env[2].open(r.data['cipher'])['forum_user_id'] == 7).owner == response.json()['id']
        assert next(r for r in rows if env[2].open(r.data['cipher'])['forum_user_id'] == 8).owner == env[3]['admin']


@pytest.mark.asyncio
async def test_postgres_concurrent_confirmation_saves_once(client, env, configured):
    if env[1].engine.dialect.name != 'postgresql': pytest.skip('requires PostgreSQL')
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from suenmeow.api import create_app
    job, _ = await extract(client, env, preview(client))
    cookies, csrf = dict(client.cookies), client.headers['x-csrf-token']
    def confirm():
        with TestClient(create_app(env[0], env[1])) as other:
            other.cookies.update(cookies)
            return other.post(f"/api/memory-imports/{job['id']}/save", json={'digest': job['result']['digest'], 'selected': [0]},
                              headers={'x-csrf-token': csrf}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: confirm(), range(2)))
    assert sorted(results) == [200, 409]
    with env[1].transaction() as s: assert len(list(s.scalars(select(Record).where(Record.kind == 'memory')))) == 1


def bind_editor(client, env):
    with env[1].transaction() as s:
        s.add(ForumIdentity(account_id=env[3]['editor'], site='https://forum.example', user_id=7, profile={'username': 'alice'}))
    login(client, 'editor', 'editor-test-password')


@pytest.mark.asyncio
async def test_editor_detects_own_topic_previews_extracts_and_saves(client, env, configured):
    bind_editor(client, env)
    detected = client.get('/api/memory-imports/detect').json()
    assert detected['candidates'][0]['topic_id'] == 42
    job, models = await extract(client, env, preview(client))
    assert models.calls == 1
    response = client.post(f"/api/memory-imports/{job['id']}/save", json={'digest': job['result']['digest'], 'selected': [0]})
    assert response.json()['saved'] == 1
    own = client.get('/api/records/memory').json()
    assert len(own) == 1 and own[0]['owner'] == env[3]['editor']


def test_editor_cannot_import_another_author_or_unverified_site(client, env, configured):
    login(client, 'editor', 'editor-test-password')
    assert client.post('/api/memory-imports', json={'topic_id': 42}).status_code == 403
    assert client.get('/api/memory-imports/detect').json()['candidates'] == []
    bind_editor(client, env)
    configured.posts = [post(1, user_id=8)]
    assert client.post('/api/memory-imports', json={'topic_id': 42}).status_code == 409
    assert client.post('/api/memory-imports', json={'topic_id': 42, 'max_tokens': 13000}).status_code == 422


def test_detection_cache_filters_private_topics_without_model(client, env, configured):
    bind_editor(client, env)
    calls = []
    async def topics(self, username): calls.append(username); return [{'topic_id': 42, 'title': '个人贴'}]
    configured.user_topics = topics
    configured.public = False
    assert client.get('/api/memory-imports/detect').json()['candidates'] == []
    assert client.get('/api/memory-imports/detect').json()['candidates'] == []
    assert calls == ['alice']
    with env[1].transaction() as s: assert not list(s.scalars(select(Usage)))


@pytest.mark.asyncio
async def test_user_daily_batch_limit_prevents_repeated_paid_requests(client, env, configured):
    bind_editor(client, env)
    for _ in range(3):
        job = preview(client)
        assert client.post(f"/api/memory-imports/{job['id']}/extract").status_code == 200
        client.post(f"/api/memory-imports/{job['id']}/cancel")
    job = preview(client)
    assert client.post(f"/api/memory-imports/{job['id']}/extract").status_code == 429


@pytest.mark.asyncio
async def test_personal_token_limit_is_checked_atomically_before_provider(client, env, configured):
    bind_editor(client, env)
    job = preview(client)
    with env[1].transaction() as s:
        previous = MemoryImport(owner=env[3]['editor'], topic_id=42, state='failed', config={}, expires=now()+300, input_cipher=env[2].seal({}))
        s.add(previous); s.flush()
        s.add(Usage(day=day(), route='memory', topic_id=42, tokens=19000, reserved=19000, state='failed_reserved', task_id=previous.id))
    calls = []
    def provider(request): calls.append(True); raise AssertionError('must not call')
    model = Models(env[1], {'memory': {'base_url': 'https://fake.example/v1', 'api_key': 'fake', 'model': 'fake', 'max_output': 1000, 'temperature': 0}}, transport=httpx.MockTransport(provider))
    with pytest.raises(BudgetExceeded):
        await model.complete('memory', [{'role': 'user', 'content': '费用上限'}], 42, Policy(), task_id=job['id'], task_limit=12000, output_limit=1000)
    assert calls == []
    assert client.post(f"/api/memory-imports/{job['id']}/extract").status_code == 429
    await model.close()
