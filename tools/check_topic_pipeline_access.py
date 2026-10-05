"""HTTPS read-only catalog/permission check; temporary unbound editor cleaned up, no forum/model calls."""
import json
from pathlib import Path
import secrets

import httpx

from suenmeow.settings import Settings


def check():
    origin = Settings.env().origin
    with httpx.Client(base_url=origin,headers={'Origin':origin},timeout=30) as admin, httpx.Client(base_url=origin,headers={'Origin':origin},timeout=30) as editor:
        def call(client, method, path, body=None, status=200):
            r=client.request(method,'/api'+path,json=body)
            assert r.status_code==status, f'{path}: {r.status_code}'
            return r.json()
        login=call(admin,'POST','/auth/login',{'username':'admin','password':Path('/run/secrets/probe_admin_password').read_text().strip()})
        admin.headers['x-csrf-token']=login['csrf']
        before=call(admin,'GET','/prompts/workspace')
        dashboard=call(admin,'GET','/dashboard')
        assert dashboard['control']['mode']=='read_only'
        name='topic-access-probe-'+secrets.token_hex(5)
        password=secrets.token_urlsafe(24)
        account=call(admin,'POST','/accounts',{'username':name,'password':password,'role':'editor','active':True,'forum_username':''})
        try:
            login=call(editor,'POST','/auth/login',{'username':name,'password':password})
            editor.headers['x-csrf-token']=login['csrf']
            visible=call(editor,'GET','/prompts/workspace')
            expected={m['id'] for m in before['modules'] if m['is_persona'] or any(m['id'] in ids for ids in before['pipeline'].values())}
            assert {m['id'] for m in visible['modules']}==expected
            assert visible['pipeline']==before['pipeline'] and not visible['pipeline_editable'] and 'accounts' not in visible
            assert all(not m['editable'] and not m['owner'] and not m['grants'] for m in visible['modules'])
            persona=next(m for m in visible['modules'] if m['is_persona'])
            draft={k:persona[k] for k in ('id','title','data','version','grants')}
            call(editor,'POST','/prompts/workspace/save',{'modules':[draft]},403)
            call(editor,'POST','/prompts/workspace/save',{'pipeline':visible['pipeline'],'pipeline_version':visible['pipeline_version']},403)
            assert call(editor,'GET','/topic-pipelines')==[]
            call(editor,'POST','/topic-pipelines',{'title':'unbound probe','topic_id':11957,'personas':{r:[persona['id']] for r in visible['pipeline']}},403)
            csrf=editor.headers.pop('x-csrf-token')
            call(editor,'POST','/topic-pipelines',{'title':'CSRF probe','topic_id':11957,'personas':{r:[] for r in visible['pipeline']}},403)
            editor.headers['x-csrf-token']=csrf
            call(editor,'POST','/auth/logout')
            after=call(admin,'GET','/prompts/workspace')
            assert all(before[k]==after[k] for k in ('modules','pipeline','pipeline_version','active_snapshot'))
            assert call(admin,'GET','/dashboard')['control']==dashboard['control']
            print(json.dumps({'https_shared_catalog':True,'personas_readable':sum(m['is_persona'] for m in visible['modules']),
                              'global_pipeline_readable':True,'shared_writes_rejected':403,'unverified_binding_rejected':403,
                              'csrf_rejected':403,'published_prompts_unchanged':True,'mode':'read_only','model_calls':0,'forum_writes':0}))
        finally:
            call(admin,'PUT','/accounts/'+account['id'],{'username':name,'password':'','role':'editor','active':False,'forum_username':''})
            call(admin,'POST','/auth/logout')
            print(json.dumps({'temporary_account_disabled':True}))


if __name__=='__main__': check()
