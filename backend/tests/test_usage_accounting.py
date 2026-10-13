import json
from sqlalchemy import select, text
import httpx
import pytest
from conftest import login
from suenmeow.adapters import Models
from suenmeow.database import KV, Usage, day
from suenmeow.domain import Policy
from suenmeow.token_counting import estimate, usage_metrics

ROUTE = {'base_url':'https://api.deepseek.com','api_key':'fictional-key','model':'deepseek-flash','max_output':1600,'temperature':0,'supports_tools':True}


def test_official_tokenizer_does_not_confuse_utf8_bytes_with_tokens():
    messages=[{'role':'system','content':'认真阅读对话，只回答需要补充的内容。'*400},{'role':'user','content':'请解释黑匣子。'}]
    old,_=estimate(messages)
    new,details=estimate(messages,ROUTE)
    assert new < old/3 and details['estimator']=='deepseek_v4_local_with_margin'
    image=[{'role':'user','content':[{'type':'text','text':'附图'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*1000000}}]}]
    count,m=estimate(image,ROUTE)
    assert count < 1500 and m['image_count']==1


@pytest.mark.parametrize('tool', [False,True])
async def test_remaining_topic_budget_admits_realistic_request_and_keeps_cache(env,tool):
    _,db,_,_=env
    with db.transaction() as s:
        s.add(Usage(day=day(),route='planner',topic_id=5502,tokens=93308,reserved=28000,state='actual'))
    def provider(request):
        assert json.loads(request.content)['max_tokens']==1600
        return httpx.Response(200,json={'usage':{'prompt_tokens':5500,'completion_tokens':300,'total_tokens':5800,
                'prompt_cache_hit_tokens':4096,'prompt_cache_miss_tokens':1404},'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'isolated output'}}]})
    models=Models(db,{'planner':ROUTE,'agent':ROUTE},httpx.MockTransport(provider))
    messages=[{'role':'system','content':'认真阅读对话，只回答需要补充的内容。'*400},{'role':'user','content':'请解释黑匣子。'}]
    if tool: await models.tool_turn(messages,[],5502,Policy(topic_tokens=120000),'',0)
    else: await models.complete('planner',messages,5502,Policy(topic_tokens=120000))
    with db.transaction() as s:
        row=s.scalar(select(Usage).where(Usage.reserved!=28000))
        assert row.state=='actual' and row.tokens==5800 and row.reserved<120000-93308
        assert row.metrics['prompt_tokens']==5500 and row.metrics['completion_tokens']==300
        assert row.metrics['cache_hit_tokens']==4096 and row.metrics['cache_miss_tokens']==1404
    await models.close()


async def test_truncated_output_keeps_actual_usage_and_cache(env):
    _,db,_,_=env
    def provider(request):
        return httpx.Response(200,json={'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130,'prompt_tokens_details':{'cached_tokens':80}},
            'choices':[{'finish_reason':'length','message':{'content':'partial'}}]})
    model=Models(db,{'planner':ROUTE},httpx.MockTransport(provider))
    with pytest.raises(RuntimeError): await model.complete('planner',[{'role':'user','content':'test'}],7,Policy())
    with db.transaction() as s:
        row=s.scalar(select(Usage)); assert row.tokens==130 and row.metrics['cache_hit_tokens']==80 and row.metrics['cache_miss_tokens']==20
    await model.close()


@pytest.mark.parametrize('status,state',[(429,'rejected'),(500,'failed_reserved')])
async def test_definite_rejections_release_hold_but_uncertain_failures_keep_it(env,status,state):
    _,db,_,_=env
    model=Models(db,{'planner':ROUTE},httpx.MockTransport(lambda r:httpx.Response(status,json={'error':'isolated'})))
    with pytest.raises(httpx.HTTPStatusError): await model.complete('planner',[{'role':'user','content':'test'}],7,Policy())
    with db.transaction() as s:
        row=s.scalar(select(Usage)); assert row.state==state and row.tokens==(0 if status==429 else row.reserved)
    await model.close()


def test_usage_api_preserves_unknown_history_and_has_auth(client,env):
    assert client.get('/api/usage/summary').status_code==401
    login(client)
    with env[1].transaction() as s:
        s.add(Usage(day=day(),route='planner',topic_id=7,tokens=50,reserved=200,state='actual'))
        s.add(Usage(day=day(),route='planner',topic_id=7,tokens=120,reserved=300,state='actual',metrics={'prompt_tokens':100,'completion_tokens':20,'cache_hit_tokens':80,'cache_miss_tokens':20}))
    summary=client.get('/api/usage/summary').json()
    assert summary['cache_measured_calls']==1 and summary['cache_unknown_calls']==1 and summary['cache_hit_percent']==80
    rows=client.get('/api/usage').json(); assert {r['reserved'] for r in rows}=={200,300}
    assert any(r['metrics']=={} for r in rows)
    login(client,'editor','editor-test-password')
    assert client.get('/api/usage/summary').status_code==403


def test_metrics_migration_is_additive_and_does_not_invent_old_cache(env):
    _,db,_,_=env
    with db.transaction() as s:
        original=dict(s.get(KV,'control').data)
        s.add(Usage(day=day(),route='planner',topic_id=7,tokens=50,reserved=200,state='actual'))
    with db.engine.begin() as conn: conn.execute(text('ALTER TABLE usage DROP COLUMN metrics'))
    db.migrate(); db.migrate()
    with db.transaction() as s:
        row=s.scalar(select(Usage)); assert row.tokens==50 and row.reserved==200 and row.metrics=={}
        assert s.get(KV,'control').data==original


def test_invalid_cache_counters_are_not_reported_as_hits():
    assert 'cache_hit_tokens' not in usage_metrics({'prompt_tokens':100,'prompt_cache_hit_tokens':True})
    assert 'cache_hit_tokens' not in usage_metrics({'prompt_tokens':100,'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':40})
    assert usage_metrics({'total_tokens':10,'arbitrary':'secret'})=={'total_tokens':10}


def test_manual_research_does_not_exhaust_later_automatic_topic_allowance(env):
    from test_agent_workflow import prepare
    from suenmeow.service import reserve, settle
    db,_,_,task=prepare(env,target_topic=5502)
    policy=Policy(daily_tokens=20000,topic_tokens=1000)
    hold=reserve(db,'agent',5502,5000,policy,task,10000);settle(db,hold,4500)
    hold=reserve(db,'planner',5502,900,policy);settle(db,hold,800)
    with pytest.raises(Exception) as exc: reserve(db,'planner',5502,300,policy)
    assert exc.value.details['used']==800 and exc.value.details['scope']=='topic'


def test_full_history_import_does_not_exhaust_automatic_topic_allowance(env):
    from suenmeow.database import MemoryImport,now
    from suenmeow.service import reserve,settle
    _,db,_,ids=env
    with db.transaction() as s:
        job=MemoryImport(owner=ids['admin'],topic_id=5502,expires=now()+3600,config={'full':True},input_cipher='isolated')
        s.add(job);s.flush();task=job.id
    policy=Policy(daily_tokens=20000,topic_tokens=1000)
    hold=reserve(db,'memory',5502,5000,policy,task);settle(db,hold,4500)
    assert reserve(db,'planner',5502,900,policy)


def test_only_one_unmatched_array_close_is_normalized_without_touching_strings():
    from suenmeow.adapters import json_output
    raw='{"posts":[{"post_id":5}],"reply":{"post_id":5,"text":"literal ] and escaped \\"quote\\"","long":false}],"memories":[]}'
    parsed=json_output(raw,tolerate_extra_array_close=True)
    assert parsed['reply']['post_id']==5 and parsed['reply']['text']=='literal ] and escaped "quote"'
    with pytest.raises(json.JSONDecodeError): json_output(raw)
    with pytest.raises(json.JSONDecodeError): json_output('{"reply":{"text":"broken}',tolerate_extra_array_close=True)
    with pytest.raises(json.JSONDecodeError): json_output('{"x":{}]],"y":[]}',tolerate_extra_array_close=True)
