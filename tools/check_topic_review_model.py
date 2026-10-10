"""Two real DeepSeek calls over fictional posts in an isolated database; never reads/sends forum content."""
import asyncio
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from suenmeow.adapters import Models
from suenmeow.database import Account, Database, KV, Snapshot, TopicReview, Usage, now
from suenmeow.security import Vault
from suenmeow.settings import Settings
from suenmeow.topic_review import process_review
from suenmeow.topic_review_prompts import SUMMARY, REVIEW


class FictionalForum:
    posts = [{'id':i,'number':i,'username':'fictional-'+str(i % 2),'user_id':900+i % 2,'text':text}
             for i,text in enumerate(['虚构验收：我想在窗边养薄荷，计划每天浇水。','虚构验收：先看土壤是否干，太多水可能积水。',
                                     '虚构验收：我改成土干了再浇水，最近也在准备考试。','虚构验收：周末骑车回来，发现薄荷新长了叶子。'], 1)]
    def __init__(self, connection): pass
    async def login(self): pass
    async def close(self): pass
    async def public_visible(self, topic): return True
    async def topic(self, tid, limit): return {'id':tid,'title':'虚构薄荷讨论','archetype':'regular','post_stream':{'stream':[1,2,3,4]},'context':self.posts[:1]}
    async def full_posts(self, tid, ids): return [p for p in self.posts if p['id'] in ids]


async def check():
    settings = Settings.env(); production = Database(settings.database_url); original_vault = Vault(settings.key_file)
    with production.transaction() as s:
        assert s.get(KV,'control').data['mode'] == 'read_only'
        snapshot = s.get(Snapshot,s.get(KV,'control').data['active_snapshot']).data
        routes = {r:original_vault.open(s.get(KV,'connection:'+r).data['cipher']) for r in ('summary','replyer')}
    assert all(urlsplit(route['base_url']).hostname == 'api.deepseek.com' for route in routes.values()), 'Only the explicitly authorized DeepSeek endpoint may receive validation inputs'
    with tempfile.TemporaryDirectory(prefix='topic-review-validation-') as folder:
        key = Path(folder)/'key'; key.write_bytes(Fernet.generate_key()); vault = Vault(key)
        db = Database('sqlite:///' + folder + '/isolated.sqlite3'); db.migrate()
        with db.transaction() as s:
            account=Account(username='fictional-validator',role='admin',password_hash='unused-disabled-login');s.add(account);s.flush()
            published=Snapshot(actor=account.id,note='isolated real-model validation',data={**snapshot,'policy':{**snapshot['policy'],'daily_tokens':200000}})
            s.add(published);s.flush();s.get(KV,'control').data={**s.get(KV,'control').data,'active_snapshot':published.id}
            for name,value in [('forum',{'base_url':'https://fictional.example','username':'fictional'}),*routes.items()]:
                s.add(KV(key='connection:'+name,data={'cipher':vault.seal(value)}))
            marker = None
            with production.transaction() as original:
                marker=original.get(KV,'prompt_refresh:20261004').data['persona_ids']
            config={'site':'https://fictional.example','user_id':0,'versions':{'forum':1,'summary':1,'replyer':1},'snapshot_id':published.id,
                    'topic_id':900001,'title':'虚构薄荷讨论','mode':'review','focus':'梳理浇水计划的变化，并给出自然的猫咪点评。',
                    'max_tokens':120000,'total':4,'offset':0,'posts':0,'excluded':0,'bytes':0,'plain_text_posts':0,'read_complete':False,
                    'parts_done':0,'merge_level':0,'work':{'summary':SUMMARY,'review':REVIEW},'persona_ids':marker,'topic_pipeline':None}
            job=TopicReview(owner=account.id,topic_id=900001,state='running',config=config,expires=now()+3600,checkpoint_cipher=vault.seal({'ids':[1,2,3,4]}));s.add(job);s.flush();jid=job.id
        models=Models(db,routes)
        try: await process_review(db,vault,models,jid,FictionalForum)
        finally: await models.close()
        with db.transaction() as s:
            job=s.get(TopicReview,jid); assert job.state=='completed',job.reason
            result=vault.open(job.result_cipher)
            print(json.dumps({'fictional_real_model_check':True,'provider':'api.deepseek.com','calls':s.scalar(select(func.count()).select_from(Usage)),
                              'tokens':s.scalar(select(func.sum(Usage.tokens))),'summary_chars':len(result['summary']),'commentary_chars':len(result['commentary']),
                              'source_refs':len(result['sources']),'production_writes':0,'forum_reads':0,'forum_writes':0}))
        db.engine.dispose()
    production.engine.dispose()


if __name__=='__main__': asyncio.run(check())
