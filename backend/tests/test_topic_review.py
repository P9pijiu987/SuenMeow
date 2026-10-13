import asyncio
import json

import httpx
import pytest
from sqlalchemy import func, select

from conftest import login
from suenmeow.adapters import Discourse, Models
from suenmeow.database import Account, ForumIdentity, KV, Record, TopicReview, TopicReviewPart, TopicReviewPost, Usage, now
from suenmeow.domain import Policy
from suenmeow.service import BudgetExceeded, publish, reserve
from suenmeow.topic_review import interrupt_reviews, process_review, purge_expired
from suenmeow.worker import Worker
from test_memory_import import Forum, post


@pytest.fixture
def source(client, env, monkeypatch):
    import suenmeow.topic_review as module
    class FullForum(Forum):
        posts = [post(1, text='首帖 **Markdown** [图片](upload://abc.png)'), post(2, 8), post(3, text='最后一楼')]
        hidden = set()
        async def full_posts(self, tid, ids): return [p for p in self.posts if p['id'] in ids and p['id'] not in self.hidden]
    monkeypatch.setattr(module, 'Discourse', FullForum)
    login(client)
    _, db, vault, ids = env
    with db.transaction() as s:
        s.add(KV(key='connection:forum', data={'cipher': vault.seal({'base_url': 'https://forum.example', 'username': 'cat'})}))
        for route in ('summary', 'replyer'):
            s.add(KV(key='connection:' + route, data={'cipher': vault.seal({'base_url': 'https://model.example', 'model': 'fake', 'api_key': 'fake', 'max_output': 6000, 'temperature': .2})}))
        s.add(ForumIdentity(account_id=ids['editor'], site='https://forum.example', user_id=7, profile={'username': 'alice'}))
        publish(s, ids['admin'], 'topic review')
    return FullForum


def create(client, mode='review', **kwargs):
    response = client.post('/api/topic-reviews', json={'topic': 'https://forum.example/t/thread/42', 'mode': mode, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


class ReadingModels:
    def __init__(self, *args): self.calls = 0; self.inputs = []; self.fail_at = 0; self.closed = False
    async def complete(self, route, messages, topic_id, policy, **kwargs):
        assert topic_id == 0 and kwargs['task_id'] and kwargs['compact_json'] and kwargs['task_limit'] >= 1000
        self.calls += 1
        if self.calls == self.fail_at: raise RuntimeError('do not echo private adapter data')
        data = json.loads(messages[1]['content']); self.inputs.append((route, messages[0]['content'], data))
        return json.dumps({'summary': '完整讨论概要', 'commentary': '猫的独立看法', 'references': data['source_post_ids'][:12]}, ensure_ascii=False)
    async def close(self): self.closed = True


async def run(env, source, job, models=None):
    with env[1].transaction() as s: s.get(TopicReview, job['id']).state = 'running'
    models = models or ReadingModels()
    await process_review(env[1], env[2], models, job['id'], source)
    with env[1].transaction() as s:
        row = s.get(TopicReview, job['id'])
        return row.state, row.reason, dict(row.config), models


@pytest.mark.asyncio
async def test_export_all_authors_full_markdown_json_no_models(client, env, source):
    source.posts = [post(i, 7 if i % 2 else 8, text=('长楼层\n' * 6000 + 'END_OF_LONG_POST') if i == 301 else f'楼层{i}') for i in range(1, 302)]
    job = create(client, 'export')
    assert client.get(f"/api/topic-reviews/{job['id']}/export").status_code == 409
    state, _, conf, models = await run(env, source, job)
    assert state == 'completed' and conf['posts'] == 301 and conf['offset'] == 301 and models.calls == 0
    result = client.get(f"/api/topic-reviews/{job['id']}/export?format=json")
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    data = result.json(); assert data['complete'] and len(data['posts']) == 301
    assert data['posts'][1]['user_id'] == 8 and data['posts'][-1]['text'].endswith('END_OF_LONG_POST')
    markdown = client.get(f"/api/topic-reviews/{job['id']}/export").text
    assert '## #301' in markdown and 'END_OF_LONG_POST' in markdown and markdown.endswith('导出结束，共 301 条普通发言。\n')
    with env[1].transaction() as s:
        encrypted = s.scalar(select(TopicReviewPost).where(TopicReviewPost.job_id == job['id'], TopicReviewPost.number == 301))
        assert 'END_OF_LONG_POST' not in encrypted.content_cipher
        assert s.scalar(select(func.count()).select_from(Usage)) == 0
        assert not list(s.scalars(select(Record).where(Record.kind == 'memory')))


@pytest.mark.asyncio
async def test_every_fragment_researched_hierarchical_merge_persona_and_sources(client, env, source):
    source.posts = [post(i, 7 if i % 2 else 8, text=('完整中文段落' * 2200) + f'尾部{i}') for i in range(1, 8)]
    job = create(client, focus='观察意见变化')
    state, _, conf, models = await run(env, source, job)
    assert state == 'completed' and conf['parts_done'] > 4
    researched = [p for _, _, data in models.inputs for p in data['data'].get('posts', [])]
    for p in source.posts:
        assert ''.join(x['text'] for x in researched if x['id'] == p['id']) == p['text']
    assert all(route == 'summary' for route, _, _ in models.inputs[:-1]) and models.inputs[-1][0] == 'replyer'
    assert '你是 SuenMeow' in models.inputs[-1][1] and '只总结当前资料' in models.inputs[0][1]
    result = client.get('/api/topic-reviews/' + job['id']).json()['result']
    assert result['summary'] and result['commentary'] and result['sources']
    assert all(s['url'] == f"https://forum.example/t/42/{s['number']}" for s in result['sources'])
    with env[1].transaction() as s: assert not s.scalar(select(Record.id).where(Record.kind == 'memory'))


@pytest.mark.asyncio
async def test_failure_manual_resume_reuses_completed_parts(client, env, source):
    source.posts = [post(i, text='正文' * 4000) for i in range(1, 5)]
    job = create(client); models = ReadingModels(); models.fail_at = 2
    state, reason, _, _ = await run(env, source, job, models)
    assert state == 'failed' and 'RuntimeError' in reason and 'private adapter data' not in reason
    with env[1].transaction() as s:
        assert s.scalar(select(func.count()).select_from(TopicReviewPart).where(TopicReviewPart.job_id == job['id'])) == 1
    assert client.get('/api/topic-reviews').json()[0]['state'] == 'failed' and models.calls == 2
    assert client.post(f"/api/topic-reviews/{job['id']}/resume").status_code == 200
    state, _, conf, resumed = await run(env, source, job)
    with env[1].transaction() as s:
        parts = s.scalar(select(func.count()).select_from(TopicReviewPart).where(TopicReviewPart.job_id == job['id']))
    assert state == 'completed' and resumed.calls == parts - 1  # the successful cached part is not called again


def test_owner_identity_csrf_scope_and_quota(client, env, source):
    login(client, 'other', 'another-test-password')
    assert client.post('/api/topic-reviews', json={'topic': '42'}).status_code == 403
    login(client, 'editor', 'editor-test-password')
    assert client.post('/api/topic-reviews', json={'topic': 'https://evil.example/t/42'}).status_code == 422
    assert client.post('/api/topic-reviews', json={'topic': '0'}).status_code == 422
    source.private = True
    assert client.post('/api/topic-reviews', json={'topic': '42'}).status_code == 422
    source.private = False; source.public = False
    assert client.post('/api/topic-reviews', json={'topic': '42'}).status_code == 422
    source.public = True
    csrf = client.headers.pop('x-csrf-token')
    assert client.post('/api/topic-reviews', json={'topic': '42'}).status_code == 403
    client.headers['x-csrf-token'] = csrf
    job = create(client)
    assert client.post('/api/topic-reviews', json={'topic': '42'}).status_code == 409
    login(client)
    assert client.get('/api/topic-reviews').json() == []
    for suffix in ('', '/export'):
        assert client.get('/api/topic-reviews/' + job['id'] + suffix).status_code == 404
    assert client.post('/api/topic-reviews/' + job['id'] + '/cancel').status_code == 404


@pytest.mark.asyncio
async def test_revoked_publicity_hidden_sources_and_identity_blocks_access(client, env, source):
    login(client, 'editor', 'editor-test-password'); job = create(client)
    assert (await run(env, source, job))[0] == 'completed'
    source.hidden = {1}
    assert client.get('/api/topic-reviews/' + job['id']).status_code == 409
    # Export fails closed with an incomplete stream; the GUI will not save this file.
    assert not client.get('/api/topic-reviews/' + job['id'] + '/export').text.endswith('导出结束，共 3 条普通发言。\n')
    source.hidden = set(); source.private = True
    assert client.get('/api/topic-reviews/' + job['id'] + '/export').status_code == 409
    source.private = False
    with env[1].transaction() as s:
        identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == env[3]['editor'])); identity.user_id = 77
    assert client.get('/api/topic-reviews/' + job['id']).status_code == 409


@pytest.mark.asyncio
async def test_unpublished_prompts_stay_drafts_and_persona_unchanged(client, env, source):
    with env[1].transaction() as s:
        setting = s.get(KV, 'topic_review_settings').data
        module = s.get(Record, setting['review_module']); old = module.data['content']
        module.data = {**module.data, 'content': 'UNPUBLISHED_NEW_WORK_RULE'}
    job = create(client); _, _, _, models = await run(env, source, job)
    assert all('UNPUBLISHED_NEW_WORK_RULE' not in prompt for _, prompt, _ in models.inputs)
    assert old in models.inputs[-1][1]


@pytest.mark.asyncio
async def test_invalid_model_refs_and_output_no_automatic_retry(client, env, source):
    class Invalid(ReadingModels):
        async def complete(self, *args, **kwargs): self.calls += 1; return json.dumps({'summary':'概述','commentary':'看法','references':['1']})
    job = create(client); state, reason, _, models = await run(env, source, job, Invalid())
    assert state == 'failed' and models.calls == 1 and 'ValidationError' in reason
    assert client.get('/api/topic-reviews/' + job['id']).json()['result'] is None


@pytest.mark.asyncio
async def test_cancel_inflight_restart_expiry_and_worker_dispatch(client, env, source):
    job = create(client)
    entered, release = asyncio.Event(), asyncio.Event()
    class Waiting(ReadingModels):
        async def complete(self, *args, **kwargs): entered.set(); await release.wait(); return await super().complete(*args, **kwargs)
    task = asyncio.create_task(run(env, source, job, Waiting())); await entered.wait()
    assert client.post('/api/topic-reviews/' + job['id'] + '/cancel').status_code == 200
    release.set(); assert (await task)[0] == 'cancelled'
    with env[1].transaction() as s: assert not s.get(TopicReview, job['id']).result_cipher
    assert client.post('/api/topic-reviews/' + job['id'] + '/resume').status_code == 200
    interrupt_reviews(env[1])
    assert client.get('/api/topic-reviews').json()[0]['state'] == 'interrupted'
    assert client.post('/api/topic-reviews/' + job['id'] + '/resume').status_code == 200
    worker = Worker(env[1], env[2], source, ReadingModels)
    await worker.dispatch_reviews(); await worker.review_job
    assert client.get('/api/topic-reviews/' + job['id']).json()['state'] == 'completed'
    with env[1].transaction() as s: s.get(TopicReview, job['id']).expires = now() - 1
    purge_expired(env[1])
    with env[1].transaction() as s:
        assert not s.get(TopicReview, job['id']) and not s.scalar(select(TopicReviewPost.id)) and not s.scalar(select(TopicReviewPart.id))


def test_full_topic_uses_task_and_daily_budget_without_reply_topic_cap(client, env, source):
    job = create(client)
    with env[1].transaction() as s: s.get(TopicReview, job['id']).state = 'running'
    policy = Policy(topic_tokens=1000, daily_tokens=10000)
    assert reserve(env[1], 'summary', 0, 5000, policy, job['id'], 6000)
    with pytest.raises(BudgetExceeded): reserve(env[1], 'summary', 0, 1001, policy, job['id'], 6000)
    with pytest.raises(BudgetExceeded): reserve(env[1], 'summary', 0, 5001, policy, job['id'], 20000)


@pytest.mark.asyncio
async def test_adapter_include_raw_full_body_filter_and_foreign_posts():
    raw = '**quote** [asset](upload://abc.png)\n' + '完整正文' * 5000 + 'END'
    def handle(request):
        assert request.url.params['include_raw'] == 'true' and request.url.params.get_list('post_ids[]') == ['1','2','3','4']
        return httpx.Response(200, json={'post_stream': {'posts': [
            {'id':1,'topic_id':42,'post_number':1,'post_type':1,'raw':raw,'username':'alice','user_id':7},
            {'id':2,'topic_id':42,'post_number':2,'post_type':1,'raw':'hidden','hidden':True},
            {'id':3,'topic_id':99,'post_number':3,'post_type':1,'raw':'foreign'},
            {'id':4,'topic_id':42,'post_number':4,'post_type':2,'raw':'system'},
        ]}})
    forum = Discourse({'base_url':'https://forum.example','username':'cat'}, httpx.MockTransport(handle))
    result = await forum.full_posts(42, [1,2,3,4])
    assert len(result) == 1 and result[0]['text'] == raw and result[0]['body_format'] == 'markdown'
    await forum.close()


@pytest.mark.asyncio
async def test_model_metering_actual_tokens_and_failed_calls_are_retained(client, env, source):
    def handle(request):
        payload = json.loads(request.content)
        data = json.loads(payload['messages'][1]['content'])
        content = json.dumps({'summary':'整帖概述','commentary':'猫的看法','references':data['source_post_ids'][:12]})
        return httpx.Response(200, json={'usage':{'total_tokens':17},'choices':[{'finish_reason':'stop','message':{'content':content}}]})
    with env[1].transaction() as s:
        routes = {r: env[2].open(s.get(KV, 'connection:' + r).data['cipher']) for r in ('summary','replyer')}
    models = Models(env[1], routes, httpx.MockTransport(handle))
    job = create(client); state, _, _, _ = await run(env, source, job, models)
    await models.close()
    assert state == 'completed'
    data = client.get('/api/topic-reviews/' + job['id']).json()
    assert data['calls'] == 2 and data['tokens'] == 34


@pytest.mark.asyncio
async def test_export_withdrawal_midstream_leaves_no_complete_footer(client, env, source):
    source.posts = [post(i) for i in range(1, 42)]
    job = create(client, 'export'); assert (await run(env, source, job))[0] == 'completed'
    source.hidden = {41}
    result = client.get('/api/topic-reviews/' + job['id'] + '/export?format=json')
    assert result.status_code == 200 and '"complete":true' not in result.text and '楼层41' not in result.text


@pytest.mark.asyncio
async def test_no_silent_truncation_and_identity_credentials_redacted(client, env, source, monkeypatch):
    import suenmeow.topic_review as module
    source.posts = [post(1, text='SM-' + 'a' * 32), post(2, text='正文' * 200)]
    job = create(client, 'export'); assert (await run(env, source, job))[0] == 'completed'
    text = client.get('/api/topic-reviews/' + job['id'] + '/export').text
    assert 'SM-' not in text and '已隔离' in text
    monkeypatch.setattr(module, 'MAX_BYTES', 100)
    job = create(client, 'export'); state, reason, conf, model = await run(env, source, job)
    assert state == 'failed' and '上限' in reason and not conf['read_complete'] and model.calls == 0
    assert client.get('/api/topic-reviews/' + job['id'] + '/export').status_code == 409


def test_connection_reconfiguration_blocks_resume_without_model_call(client, env, source):
    job = create(client)
    assert client.post('/api/topic-reviews/' + job['id'] + '/cancel').status_code == 200
    with env[1].transaction() as s: s.get(KV, 'connection:summary').version += 1
    response = client.post('/api/topic-reviews/' + job['id'] + '/resume')
    assert response.status_code == 409 and '连接' in response.json()['detail']


def test_concurrent_creation_has_one_winner(client, env, source):
    import os
    from concurrent.futures import ThreadPoolExecutor
    if os.getenv('SUENMEOW_TEST_POSTGRES') != '1': pytest.skip('Requires isolated PostgreSQL schemas')
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.post('/api/topic-reviews', json={'topic':'42','mode':'export'}).status_code, range(2)))
    assert sorted(statuses) == [201,409]


@pytest.mark.asyncio
async def test_cancel_during_source_recheck_prevents_new_model_call(client, env, source):
    job = create(client)
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowScope(source):
        async def public_visible(self, topic):
            self.checks = getattr(self, 'checks', 0) + 1
            if self.checks == 3: entered.set(); await release.wait()
            return True
    models = ReadingModels()
    task = asyncio.create_task(run(env, SlowScope, job, models)); await entered.wait()
    assert client.post('/api/topic-reviews/' + job['id'] + '/cancel').status_code == 200
    release.set(); assert (await task)[0] == 'cancelled' and models.calls == 0
    with pytest.raises(BudgetExceeded): reserve(env[1], 'summary', 0, 1000, Policy(), job['id'], 200000)


@pytest.mark.asyncio
async def test_result_checks_withdrawn_sources_even_when_not_cited(client, env, source):
    source.posts = [post(i) for i in range(1, 16)]
    job=create(client); assert (await run(env,source,job))[0]=='completed'
    source.hidden={15}
    assert client.get('/api/topic-reviews/'+job['id']).status_code==409


def test_parallel_full_review_reservations_share_hard_limit(client, env, source):
    import os
    from concurrent.futures import ThreadPoolExecutor
    if os.getenv('SUENMEOW_TEST_POSTGRES') != '1': pytest.skip('Requires isolated PostgreSQL schemas')
    job=create(client)
    with env[1].transaction() as s: s.get(TopicReview,job['id']).state='running'
    def attempt(i):
        try: return reserve(env[1],'summary',0,600,Policy(topic_tokens=1000),job['id'],1500)
        except BudgetExceeded: return None
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(attempt,range(8)))
    assert len([r for r in results if r])==2
    with env[1].transaction() as s: assert s.scalar(select(func.sum(Usage.tokens)).where(Usage.task_id==job['id']))==1200


@pytest.mark.asyncio
async def test_post_ids_are_not_confused_with_floor_numbers(client, env, source):
    source.posts=[post(1001,number=1),post(1002,8,number=2)]
    job=create(client); state,_,_,models=await run(env,source,job)
    assert state=='completed'
    assert models.inputs[-1][2]['source_posts']==[{'id':1001,'number':1},{'id':1002,'number':2}]
    result=client.get('/api/topic-reviews/'+job['id']).json()['result']
    assert result['sources'][1]['url']=='https://forum.example/t/42/2'


@pytest.mark.asyncio
async def test_worker_factory_failure_does_not_leave_running_job(client,env,source):
    job=create(client)
    def broken(*args): raise RuntimeError('do not echo secret')
    worker=Worker(env[1],env[2],source,broken)
    await worker.dispatch_reviews(); await worker.review_job
    row=client.get('/api/topic-reviews/'+job['id']).json()
    assert row['state']=='failed' and row['reason']=='处理初始化失败：RuntimeError'
    await worker.dispatch_reviews(); assert worker.review_job is None
