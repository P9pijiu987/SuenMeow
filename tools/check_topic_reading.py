"""Verify a real public-topic export via HTTPS: no models, memories, or forum writes."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
from suenmeow.settings import Settings


async def check(topic):
    origin = Settings.env().origin
    async with httpx.AsyncClient(base_url=origin, timeout=75, headers={'Origin': origin}) as client:
        assert (await client.get('/api/topic-reviews')).status_code == 401
        login = await client.post('/api/auth/login', json={'username':'admin', 'password':Path('/run/secrets/probe_admin_password').read_text().strip()})
        login.raise_for_status(); client.headers['x-csrf-token'] = login.json()['csrf']
        before = (await client.get('/api/dashboard')).json()['control']
        assert before['mode'] == 'read_only'
        created = await client.post('/api/topic-reviews', json={'topic':str(topic), 'mode':'export'})
        created.raise_for_status(); job = created.json(); jid = job['id']
        try:
            for _ in range(600):
                response = await client.get('/api/topic-reviews/' + jid); response.raise_for_status(); job = response.json()
                if job['state'] not in ('queued','running'): break
                await asyncio.sleep(2)
            assert job['state'] == 'completed', job['state'] + ': ' + job['reason']
            assert job['config']['read_complete'] and job['calls'] == 0 and job['tokens'] == 0
            response = await client.get('/api/topic-reviews/' + jid + '/export?format=json'); response.raise_for_status()
            data = response.json()
            assert data['complete'] and len(data['posts']) == job['config']['posts']
            assert len({post['id'] for post in data['posts']}) == len(data['posts'])
            assert all(post['url'].startswith(job['config']['site'] + f'/t/{topic}/') for post in data['posts'])
            formats = {post.get('body_format') for post in data['posts']}
            after = (await client.get('/api/dashboard')).json()['control']; assert after == before
            print(json.dumps({'https_export':True,'topic_id':topic,'stream_ids':job['config']['total'],
                              'visible_posts':len(data['posts']),'excluded':job['config']['excluded'],
                              'body_formats':sorted(formats),'complete':True,'model_calls':job['calls'],
                              'tokens':job['tokens'],'mode':after['mode'],'published_snapshot':after['active_snapshot'],
                              'forum_writes':0,'memory_writes':0}))
        finally:
            if job['state'] in ('queued','running'): await client.post('/api/topic-reviews/' + jid + '/cancel')
            await client.post('/api/auth/logout')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--topic', type=int, required=True)
    asyncio.run(check(parser.parse_args().topic))
