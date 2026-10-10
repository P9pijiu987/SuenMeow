from copy import deepcopy

import pytest
from sqlalchemy import select

from conftest import login
from suenmeow.database import Account, Event, ForumIdentity, KV, Record, Reply, TopicPipeline
from suenmeow.service import add_event, claim_send, publish
from suenmeow.topic_pipeline import overlay, pin_valid, resolve
from suenmeow.worker import Worker
from test_memory_import import Forum, post
from test_pipeline import GoodModels
from test_safety import activate


@pytest.fixture
def setup_topic(client, env, monkeypatch):
    import suenmeow.topic_pipeline as api
    class OwnForum(Forum):
        posts = [post(1), post(2, 8), post(3)]
    monkeypatch.setattr(api, 'Discourse', OwnForum)
    login(client)
    assert client.put('/api/connections/forum', json={'base_url': 'https://forum.example', 'username': 'cat', 'password': 'fake'}).status_code == 200
    with env[1].transaction() as s:
        s.add(ForumIdentity(account_id=env[3]['editor'], site='https://forum.example', user_id=7, profile={'username': 'alice'}))
        persona = Record(kind='module', owner=env[3]['admin'], title='独立的人格', data={'persona': True, 'content': '独立人格内容'})
        private = Record(kind='module', owner=env[3]['other'], title='私有草稿', data={'content': '不可公开的无引用模块'})
        s.add_all([persona, private]); s.flush()
        pid, private_id = persona.id, private.id
    assert client.put('/api/topic-pipeline-settings', json={'require_review': True, 'version': 1}).status_code == 200
    return OwnForum, pid, private_id


def payload(persona):
    return {'title': '我的猫', 'topic_id': 42, 'personas': {r: [persona] for r in ('planner','replyer','memory','summary','agent')}, 'version': 1}


def create(client, pid):
    login(client, 'editor', 'editor-test-password')
    response = client.post('/api/topic-pipelines', json=payload(pid))
    assert response.status_code == 201, response.text
    return response.json()


def publish_personal(client, row):
    login(client)
    response = client.post(f"/api/topic-pipelines/{row['id']}/publish", json={'version': row['version']})
    assert response.status_code == 200, response.text
    return response.json()


def test_shared_readonly_no_private_draft_or_write(client, env, setup_topic):
    _, persona, private = setup_topic
    login(client, 'editor', 'editor-test-password')
    data = client.get('/api/prompts/workspace').json()
    assert data['pipeline'] and not data['pipeline_editable'] and 'accounts' not in data
    modules = {m['id']: m for m in data['modules']}
    assert persona in modules and private not in modules and not modules[persona]['editable']
    body = {k: modules[persona][k] for k in ('id','title','data','grants','version')}
    assert client.post('/api/prompts/workspace/save', json={'modules': [body]}).status_code == 403
    assert client.put('/api/records/module/' + persona, json={'title':'Hack', 'data':{'content':'overwrite'}, 'version':1}).status_code == 403
    assert client.post('/api/prompts/workspace/save', json={'pipeline': data['pipeline'], 'pipeline_version':data['pipeline_version']}).status_code == 403


async def test_drafts_publication_scope_and_fixed_content(client, env, setup_topic):
    ForumType, pid, _ = setup_topic
    row = create(client, pid)
    forum = ForumType({'base_url':'https://forum.example','username':'cat'})
    assert await resolve(env[1], forum, 42, False) is None
    assert client.post(f"/api/topic-pipelines/{row['id']}/publish", json={'version':1}).status_code == 403
    row = publish_personal(client, row)
    pin = await resolve(env[1], forum, 42, False)
    assert pin and await resolve(env[1], forum, 43, False) is None
    assert await resolve(env[1], forum, 42, True) is None
    with env[1].transaction() as s:
        snapshot = s.get(KV, 'pipeline').data
        publish(s, env[3]['admin'], 'global')
        from suenmeow.database import Snapshot
        original = deepcopy(s.get(Snapshot, s.get(KV,'control').data['active_snapshot']).data)
        module = s.get(Record, pid); module.data = {**module.data,'content':'修改尚未个人发布'}
    effective = overlay(original, pin)
    assert effective['modules']['topic-persona:'+pid]['content'] == '独立人格内容'
    for route, ids in original['pipeline'].items():
        assert effective['pipeline'][route] == ['topic-persona:'+pid] + [mid for mid in ids if not original['modules'][mid]['persona']]
    assert 'topic-persona:'+pid not in original['modules']
    login(client, 'editor','editor-test-password')
    changed = payload(pid) | {'title':'新草稿', 'version':row['version']}
    saved = client.put('/api/topic-pipelines/'+row['id'], json=changed).json()
    assert saved['version'] == 2 and saved['published_version'] == 1
    assert (await resolve(env[1],forum,42,False))['revision'] == pin['revision']
    publish_personal(client, saved)
    with env[1].transaction() as s: assert not pin_valid(s, pin)
    assert (await resolve(env[1],forum,42,False))['modules'][pid]['content'] == '修改尚未个人发布'


def test_identity_topics_owner_isolation_conflicts_and_quotas(client, env, setup_topic):
    ForumType, pid, _ = setup_topic
    login(client, 'other','another-test-password')
    assert client.post('/api/topic-pipelines',json=payload(pid)).status_code == 403
    login(client, 'editor','editor-test-password')
    ForumType.posts = [post(1, 8)]
    assert client.post('/api/topic-pipelines',json=payload(pid)).status_code == 422
    ForumType.posts = [post(1)]; ForumType.category_id = 23
    assert client.post('/api/topic-pipelines',json=payload(pid)).status_code == 422
    ForumType.category_id = 22; ForumType.private = True
    assert client.post('/api/topic-pipelines',json=payload(pid)).status_code == 422
    ForumType.private = False
    body = payload(pid); body['personas']['replyer'] += [pid]
    assert client.post('/api/topic-pipelines',json=body).status_code == 422
    row = create(client, pid)
    assert client.post('/api/topic-pipelines',json=payload(pid)).status_code == 409
    assert client.put('/api/topic-pipelines/'+row['id'],json=payload(pid)|{'version':2}).status_code == 409
    assert client.put('/api/topic-pipelines/'+row['id'],json=payload(pid)|{'topic_id':99}).status_code == 422
    login(client, 'other','another-test-password')
    assert client.get('/api/topic-pipelines').json() == []
    assert client.put('/api/topic-pipelines/'+row['id'],json=payload(pid)).status_code == 404
    login(client)
    assert len(client.get('/api/topic-pipelines').json()) == 1
    assert client.post('/api/topic-pipelines/'+row['id']+'/publish',json={'version':2}).status_code == 409
    login(client,'editor','editor-test-password')
    with env[1].transaction() as s:
        conn = s.get(KV,'connection:forum'); cat = s.get(KV,'memory_import_settings')
        s.add_all(TopicPipeline(owner=env[3]['editor'],site='https://forum.example',user_id=7,topic_id=100+i,title='Extra',personas=payload(pid)['personas'],forum_version=conn.version,category_version=cat.version) for i in range(9))
    assert client.post('/api/topic-pipelines',json=payload(pid)|{'topic_id':222}).status_code == 422


async def test_worker_uses_owner_not_latest_speaker_and_revocation_blocks_send(client, env, setup_topic):
    ForumType, pid, _ = setup_topic
    row = publish_personal(client, create(client,pid))
    epoch, version = activate(env, 'auto')
    ForumType.posts = [post(1),post(2,8)]
    forum = ForumType({'base_url':'https://forum.example','username':'cat'})
    class CapturingModels(GoodModels):
        def __init__(self): super().__init__(); self.prompts={}
        async def complete(self,route,messages,*args):
            self.prompts[route] = messages[0]['content']
            return await super().complete(route,messages,*args)
    eid = add_event(env[1],'personal:1',42,{'source':'notification'},epoch,version,600)
    worker=Worker(env[1],env[2]);worker.forum=forum;worker.models=CapturingModels();worker.epoch=epoch
    await worker.draft_one()
    assert '独立人格内容' in worker.models.prompts['planner'] and '独立人格内容' in worker.models.prompts['replyer']
    with env[1].transaction() as s:
        reply = s.scalar(select(Reply).where(Reply.event_id==eid)); assert reply and s.get(Event,eid).data['topic_pipeline']
        rid = reply.id
    login(client,'editor','editor-test-password')
    assert client.post('/api/topic-pipelines/'+row['id']+'/disable',json={'version':row['version']}).status_code == 200
    assert claim_send(env[1],rid) is None
    with env[1].transaction() as s: assert s.get(Reply,rid).state == 'expired'
    assert await resolve(env[1],forum,42,False) is None


async def test_identity_rebinding_and_category_changes_revoke_pin(client, env, setup_topic):
    ForumType,pid,_=setup_topic
    publish_personal(client, create(client,pid))
    forum=ForumType({'base_url':'https://forum.example','username':'cat'})
    pin=await resolve(env[1],forum,42,False)
    with env[1].transaction() as s:
        account=s.get(Account,env[3]['editor']);account.active=False
    assert await resolve(env[1],forum,42,False) is None
    with env[1].transaction() as s:
        s.get(Account,env[3]['editor']).active=True
        s.get(KV,'memory_import_settings').version += 1
        assert not pin_valid(s,pin)
    assert await resolve(env[1],forum,42,False) is None


async def test_summary_memory_and_full_import_use_topic_personas(client, env, setup_topic, monkeypatch):
    from suenmeow.memory_import import process_import
    from suenmeow.database import MemoryImport
    import suenmeow.memory_import as imports
    ForumType,pid,_=setup_topic
    publish_personal(client,create(client,pid))
    epoch,version=activate(env,'auto')
    ForumType.posts=[post(1,text='照料盆栽'*1500),post(2)]
    class Capturing(GoodModels):
        def __init__(self): super().__init__(); self.prompts={}
        async def complete(self,route,messages,*args,**kwargs):
            self.prompts[route]=messages[0]['content']
            return await super().complete(route,messages,*args)
    worker=Worker(env[1],env[2]);worker.forum=ForumType({'base_url':'https://forum.example','username':'cat'});worker.models=Capturing();worker.epoch=epoch
    eid=add_event(env[1],'personal:summary',42,{'source':'notification'},epoch,version,600)
    await worker.draft_one()
    with env[1].transaction() as s:
        row=s.scalar(select(Reply).where(Reply.event_id==eid));assert row
        row.state='sent';row.sent_post_id=999
    await worker.remember_one()
    assert '独立人格内容' in worker.models.prompts['summary'] and '独立人格内容' in worker.models.prompts['memory']
    login(client)
    assert client.put('/api/connections/memory',json={'base_url':'https://model.example','model':'fake','api_key':'fake'}).status_code==200
    monkeypatch.setattr(imports,'Discourse',ForumType)
    login(client,'editor','editor-test-password')
    response=client.post('/api/memory-imports/start',json={'topic_id':42})
    assert response.status_code==200,response.text
    job=response.json()
    assert job['config']['topic_pipeline']['modules'][pid]['content']=='独立人格内容'
    with env[1].transaction() as s: s.get(MemoryImport,job['id']).state='running'
    class FullModels:
        async def complete(self,route,messages,*args,**kwargs):
            assert '独立人格内容' in messages[0]['content']
            return '{"facts":[]}'
    await process_import(env[1],env[2],FullModels(),job['id'])
    with env[1].transaction() as s: assert s.get(MemoryImport,job['id']).state=='empty'


def test_postgres_concurrent_topic_drafts(env, setup_topic):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from suenmeow.api import create_app
    settings,db,_,_=env
    if db.engine.dialect.name!='postgresql': pytest.skip('PostgreSQL locks required')
    with TestClient(create_app(settings,db)) as client:
        row=create(client,setup_topic[1])
    def attempt(i):
        with TestClient(create_app(settings,db)) as client:
            login(client,'editor','editor-test-password')
            return client.put('/api/topic-pipelines/'+row['id'],json=payload(setup_topic[1])|{'title':f'Concurrent {i}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: statuses=list(pool.map(attempt,range(2)))
    assert sorted(statuses)==[200,409]


async def test_memory_rechecks_topic_category_after_reply(client, env, setup_topic):
    ForumType,pid,_=setup_topic
    publish_personal(client,create(client,pid))
    epoch,version=activate(env,'auto')
    forum=ForumType({'base_url':'https://forum.example','username':'cat'})
    worker=Worker(env[1],env[2]);worker.forum=forum;worker.models=GoodModels();worker.epoch=epoch
    eid=add_event(env[1],'personal:changed-category',42,{'source':'notification'},epoch,version,600)
    await worker.draft_one()
    with env[1].transaction() as s:
        row=s.scalar(select(Reply).where(Reply.event_id==eid)); assert row
        rid=row.id;row.state='sent';row.sent_post_id=999
    ForumType.category_id=23
    await worker.remember_one()
    assert worker.models.routes==['planner','replyer']
    with env[1].transaction() as s:
        assert s.get(Reply,rid).state=='sent' and s.get(Reply,rid).memory_state=='failed'


async def test_legacy_unmarked_roles_are_selectable_without_metadata_changes(client, env, setup_topic):
    ForumType,_,_=setup_topic
    data={'content':'未标记的旧人格内容','description':'原始元数据','persona':False}
    with env[1].transaction() as s:
        role=Record(kind='module',owner=env[3]['admin'],title='Meow.md',data=data)
        private=Record(kind='module',owner=env[3]['other'],title='Meow.md',data={'persona':False,'content':'仅仅同名的私有草稿'})
        s.add_all([role, private]);s.flush();pid=role.id;private_id=private.id
        pipeline=s.get(KV,'pipeline');pipeline.data={**pipeline.data,'replyer':[*pipeline.data['replyer'],pid]}
    login(client,'editor','editor-test-password')
    catalog=client.get('/api/prompts/workspace').json()['modules']
    assert private_id not in {m['id'] for m in catalog}
    view=next(m for m in catalog if m['id']==pid)
    assert view['is_persona'] and view['legacy_persona'] and not view['editable'] and view['data']==data
    row=publish_personal(client,create(client,pid))
    pin=await resolve(env[1],ForumType({'base_url':'https://forum.example','username':'cat'}),42,False)
    with env[1].transaction() as s:
        assert s.get(Record,pid).data==data
        from suenmeow.database import Snapshot
        version=publish(s,env[3]['admin'],'check')
        original=s.get(Snapshot,version).data
    original['pipeline']['replyer'].append(private_id)
    effective=overlay(original,pin)
    assert 'topic-persona:'+pid in effective['pipeline']['replyer']
    assert pid not in effective['pipeline']['replyer']
    assert private_id in effective['pipeline']['replyer']
    assert effective['modules']['topic-persona:'+pid]['content']==data['content']
