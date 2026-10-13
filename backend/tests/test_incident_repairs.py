import json

import httpx
import pytest
from sqlalchemy import select

from conftest import login
from test_agent_workflow import Forum, Model, call, prepare
from test_interactions import engine, collect, post
from suenmeow.adapters import Discourse, Models, safe_post
from suenmeow.agent import AgentEngine, claim_task
from suenmeow.database import AgentDraft, AgentMessage, AgentTask, KV, Record, Reply, Snapshot
from suenmeow.domain import Policy
from suenmeow.forum_links import topic_link
from suenmeow.service import BudgetExceeded, reserve, settle, publish
from suenmeow.web_search import search_public


def test_budget_bundle_live_without_publishing_or_changing_mode(client, env):
    login(client)
    with env[1].transaction() as s: original = publish(s, env[3]['admin'], 'baseline')
    policy = client.get('/api/agent/settings').json(); policy['max_tokens'] = 500000
    r = client.put('/api/agent/settings/bundle', json={'policy':policy, 'daily_tokens':5000, 'topic_tokens':3000})
    assert r.status_code == 200, r.text
    assert client.get('/api/dashboard').json()['daily_budget'] == 5000
    with env[1].transaction() as s:
        assert s.get(KV,'control').data['active_snapshot'] == original
        assert s.get(Snapshot,original).data['policy']['daily_tokens'] != 5000
    used = reserve(env[1], 'planner', 10, 2000, Policy())
    settle(env[1], used, 800)
    with pytest.raises(BudgetExceeded) as exc: reserve(env[1], 'planner', 10, 2500, Policy())
    assert exc.value.details == {'scope':'topic','limit':3000,'used':800,'requested':2500,'remaining':2200}
    assert client.get('/api/agent/budgets').json()['used_today'] == 800
    login(client,'editor','editor-test-password')
    assert client.get('/api/agent/budgets').status_code == 403
    assert client.put('/api/agent/settings/bundle',json={'policy':policy,'daily_tokens':100000,'topic_tokens':5000}).status_code == 403


def test_admin_research_does_not_share_topic_zero_budget(env):
    db, _, _, tid = prepare(env)
    p = Policy(daily_tokens=20000, topic_tokens=1000)
    uid = reserve(db, 'agent', 0, 5000, p, tid, 10000); settle(db, uid, 2000)
    with pytest.raises(BudgetExceeded) as exc: reserve(db, 'agent', 0, 9000, p, tid, 10000)
    assert exc.value.details['scope'] == 'task'
    with db.transaction() as s:
        task = s.get(AgentTask, tid); task.constraints = {**task.constraints,'origin':'forum'}
    with pytest.raises(BudgetExceeded) as exc: reserve(db, 'agent', 10, 2000, p, tid, 10000)
    assert exc.value.details['scope'] == 'topic'


async def test_revision_cancels_old_approval_and_requires_new_confirmation(client, env):
    db,vault,ids,tid = prepare(env,kind='reply',target_topic=100,allow_send=False)
    await AgentEngine(db,vault,Forum(),Model([call('draft_reply',{'text':'原草稿'})]),tid).run()
    login(client)
    original = client.get('/api/agent/tasks/'+tid).json()
    r = client.post('/api/agent/drafts/'+original['draft']['id']+'/revise',json={'text':'补读论坛后详细解释','version':1})
    assert r.status_code == 200, r.text
    new = r.json()['task_id']; assert claim_task(db) == new
    model = Model([call('draft_reply',{'text':'已经核对目标的修订草稿'})])
    await AgentEngine(db,vault,Forum(),model,new).run()
    data = client.get('/api/agent/tasks/'+new).json()
    assert data['state'] == 'awaiting_confirmation' and not data['draft']['confirmed']
    assert data['constraints']['allow_send'] is False and data['constraints']['target_topic'] == 100
    assert '原草稿' in json.dumps(model.messages,ensure_ascii=False)
    assert client.post('/api/agent/drafts/'+original['draft']['id']+'/revise',json={'text':'再次修订旧草稿','version':1}).status_code == 409
    assert client.post('/api/agent/drafts/'+original['draft']['id']+'/confirm',json={'digest':original['draft']['digest']}).status_code == 409
    with db.transaction() as s: assert not list(s.scalars(select(Reply)))


async def test_private_history_does_not_enter_public_followup(env):
    db,vault,_,tid = prepare(env)
    with db.transaction() as s:
        task=s.get(AgentTask,tid)
        s.add(AgentMessage(session_id=task.session_id,role='assistant',text_cipher=vault.seal('private-history-secret'),meta={'private':True,'target_topic':200}))
        s.add(AgentMessage(session_id=task.session_id,role='assistant',text_cipher=vault.seal('public-history'),meta={'private':False,'target_topic':100}))
    model=Model([{'role':'assistant','content':'后续研究'}])
    await AgentEngine(db,vault,Forum(),model,tid).run()
    text=json.dumps(model.messages)
    assert 'public-history' in text and 'private-history-secret' not in text


async def test_forum_read_accepts_same_site_url_and_preserves_privacy(env):
    db,vault,_,tid=prepare(env)
    model=Model([call('forum_read_topic',{'url':'https://forum.test/t/topic/100/'}),call('forum_read_topic',{'url':'https://other.test/t/200/'}),{'role':'assistant','content':'完成'}])
    await AgentEngine(db,vault,Forum(),model,tid).run()
    tools=[m for m in model.messages if m['role']=='tool']
    assert 'source_id' in tools[0]['content'] and 'error' in tools[1]['content']
    assert topic_link('https://forum.test/t/13340/2','https://forum.test') == (13340,2)
    assert topic_link('https://forum.test/t/topic/13340/','https://forum.test') == (13340,0)


async def test_vision_uses_real_image_parts_without_cookie_forwarding(env):
    seen=[]
    def transport(request):
        seen.append(request)
        assert request.url.host == 'forum.test'
        return httpx.Response(200,headers={'content-type':'image/png'},content=b'\x89PNG\r\n\x1a\nimage')
    forum=Discourse({'base_url':'https://forum.test','username':'cat','api_key':'secret'},transport=httpx.MockTransport(transport))
    value=safe_post({'id':1,'raw':'看看图片','cooked':'<p><img src="/uploads/photo.png"><img class="emoji" src="/emoji/cat.png"></p>'})
    parts=await forum.image_parts([value],private=True)
    assert len(parts)==2 and parts[1]['image_url']['url'].startswith('data:image/png;base64,')
    assert len(seen)==1
    assert Models.input_reservation([{'role':'user','content':parts}]) < 5000
    await forum.close()


async def test_web_search_has_no_forum_credentials_and_filters_internal_links():
    def handler(request):
        assert request.url.host=='www.bing.com' and 'api-key' not in request.headers and 'cookie' not in request.headers
        return httpx.Response(200,content=b'<rss><channel><item><title>Public</title><link>https://example.com/source</link><description>Evidence</description></item><item><link>http://127.0.0.1/secret</link></item></channel></rss>')
    result=await search_public('public query',httpx.MockTransport(handler))
    assert len(result['results'])==1 and result['complete'] is False


async def test_automatic_fact_is_sourced_without_an_extra_model_call(engine):
    def add(result):
        result['memories']=[{'source_post_id':2,'text':'human 喜欢薄荷茶','quote':'我喜欢薄荷茶'}]
    engine.models.change=add
    await collect(engine,[post(2,raw='我喜欢薄荷茶，平时一直喝这个。')])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        facts=list(s.scalars(select(Record).where(Record.kind=='memory')))
        assert len(facts)==1
        value=engine.vault.open(facts[0].data['cipher'])
        assert value['source_post_id']==2 and value['quote']=='我喜欢薄荷茶' and value['origin']=='interaction_fact'
    assert len(engine.models.calls)==1


async def test_invalid_auto_fact_does_not_break_reply_or_reactions(engine):
    engine.models.change=lambda result: result.update(memories=[{'source_post_id':2,'text':'编造的事实','quote':'原文没有这句'}])
    await collect(engine,[post(2)])
    await engine.interactions.prepare_one()
    with engine.db.transaction() as s:
        assert not list(s.scalars(select(Record).where(Record.kind=='memory')))
        assert s.scalar(select(Reply))


async def test_memory_storage_failure_does_not_cancel_committed_interactions(engine, monkeypatch):
    engine.models.change = lambda result: result.update(memories=[{'source_post_id':2,'text':'alice 喜欢薄荷茶','quote':'我喜欢薄荷茶'}])
    seal = engine.vault.seal
    def fail_fact(value):
        if isinstance(value, dict) and value.get('origin') == 'interaction_fact':
            raise RuntimeError('isolated memory storage failure')
        return seal(value)
    monkeypatch.setattr(engine.vault, 'seal', fail_fact)
    await collect(engine, [post(2, raw='我喜欢薄荷茶，平时一直喝这个。')])
    await engine.interactions.prepare_one()
    from suenmeow.database import Event, Reaction
    with engine.db.transaction() as s:
        assert s.scalar(select(Reply)).state == 'ready'
        assert s.scalar(select(Reaction)).state == 'ready'
        assert s.scalar(select(Event)).state == 'drafted'
        assert not list(s.scalars(select(Record).where(Record.kind == 'memory')))
    assert len(engine.models.calls) == 1 and not engine.forum.writes


@pytest.mark.parametrize('state', ['sending', 'sent', 'unknown'])
async def test_revision_cannot_restart_a_sent_or_uncertain_reply(client, env, state):
    db, vault, _, tid = prepare(env, kind='reply', target_topic=100, allow_send=False)
    await AgentEngine(db, vault, Forum(), Model([call('draft_reply', {'text':'原草稿'})]), tid).run()
    with db.transaction() as s:
        draft = s.scalar(select(AgentDraft).where(AgentDraft.task_id == tid))
        reply = Reply(event_id='isolated-source', topic_id=100, text_cipher=vault.seal('原草稿'), state=state)
        s.add(reply); s.flush(); draft.reply_id = reply.id
        draft_id = draft.id
    login(client)
    response = client.post('/api/agent/drafts/' + draft_id + '/revise', json={'text':'请修改', 'version':1})
    assert response.status_code == 409
    with db.transaction() as s:
        assert len(list(s.scalars(select(AgentTask)))) == 1
        assert not s.get(AgentTask, tid).cancelled



async def test_one_bad_reaction_field_does_not_destroy_whole_batch(engine):
    def change(result):
        result['posts'][0].update(score='not-a-number',reaction=None)
        result['posts'][1]['reason']='why'*200
    engine.models.change=change
    await collect(engine,[post(2),post(3)])
    await engine.interactions.prepare_one()
    from suenmeow.database import Observation, Reaction
    with engine.db.transaction() as s:
        assert all(o.state=='evaluated' for o in s.scalars(select(Observation)))
        assert len(list(s.scalars(select(Reaction))))==1 and s.scalar(select(Reply))


async def test_redundant_json_delimiter_preserves_actions_without_another_call(engine):
    original=engine.models.complete
    async def faulty(*args,**kwargs):
        output=await original(*args,**kwargs)
        return output[:-1]+']}'
    engine.models.complete=faulty
    await collect(engine,[post(2,raw='@cat，请解释一下这个问题。')])
    await engine.interactions.prepare_one()
    from suenmeow.database import Observation,Reaction
    with engine.db.transaction() as s:
        assert s.scalar(select(Observation)).state=='evaluated'
        assert s.scalar(select(Reply)).state=='ready' and s.scalar(select(Reaction)).state=='ready'
    assert len(engine.models.calls)==1 and not engine.forum.writes


async def test_recorded_deepseek_image_response_replays_with_sourced_fact(engine):
    # Captured fictional provider output had exactly one extra ] after reply.
    from suenmeow.adapters import Models as RealModels
    text='{"posts":[{"post_id":5,"reaction":"cat","score":0.6,"reason":"图片核对"}],"reply":{"post_id":5,"text":"左半邊，紅。右半邊，藍。","reason":"被点名要求描述图片并记录偏好","research":false,"long":false}],"memories":[{"source_post_id":5,"text":"长期饮品偏好是薄荷茶","quote":"我的长期饮品偏好是薄荷茶，我一直喜欢薄荷茶。"}]}'
    requests=[]
    def provider(request):
        requests.append(request)
        return httpx.Response(200,json={'usage':{'prompt_tokens':5064,'completion_tokens':187,'total_tokens':5251,'prompt_cache_hit_tokens':4864,'prompt_cache_miss_tokens':200},'choices':[{'finish_reason':'stop','message':{'content':text}}]})
    model=RealModels(engine.db,{'planner':{'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'fictional-key','max_output':4000,'temperature':0}},httpx.MockTransport(provider))
    async def image_parts(posts,private=False): return []
    engine.forum.image_parts=image_parts;engine.forum.allowed.append('cat');engine.models=model
    try:
        await collect(engine,[post(5,raw='我的长期饮品偏好是薄荷茶，我一直喜欢薄荷茶。 @cat 请准确描述图片颜色。')])
        await engine.interactions.prepare_one()
        with engine.db.transaction() as s:
            reply=s.scalar(select(Reply));assert reply and '紅' in engine.vault.open(reply.text_cipher)
            fact=s.scalar(select(Record).where(Record.kind=='memory'));assert fact and engine.vault.open(fact.data['cipher'])['source_post_id']==5
            from suenmeow.database import Usage
            usage=s.scalar(select(Usage));assert usage.tokens==5251 and usage.metrics['cache_hit_tokens']==4864
        assert len(requests)==1 and not engine.forum.writes
    finally: await model.close()
