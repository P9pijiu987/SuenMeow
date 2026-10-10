"""Exercise public-topic import preview only: no model call, no memory save, no forum write."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx

from suenmeow.settings import Settings


async def check(topic_id):
    origin = Settings.env().origin
    async with httpx.AsyncClient(base_url=origin, timeout=120) as client:
        assert (await client.get('/api/memory-imports')).status_code == 401
        password = Path('/run/secrets/probe_admin_password').read_text().strip()
        response = await client.post('/api/auth/login', json={'username': 'admin', 'password': password}, headers={'Origin': origin})
        response.raise_for_status()
        csrf = {'Origin': origin, 'x-csrf-token': response.json()['csrf']}
        before = await client.get('/api/usage')
        before.raise_for_status()
        preview = await client.post('/api/memory-imports', json={'topic_id': topic_id, 'max_tokens': 12000}, headers=csrf)
        preview.raise_for_status()
        job = preview.json()
        try:
            config = job['config']
            assert job['state'] == 'preview' and job['tokens'] == 0
            assert 0 <= config['author_posts'] <= config['scanned'] <= 100 and config['remaining'] >= 0
            assert config['reservation'] <= 12000 and config['user_id'] > 0
            after = await client.get('/api/usage')
            assert before.json() == after.json()
            print(json.dumps({'public_author_verified': True, 'scanned': config['scanned'],
                              'author_posts': config['author_posts'], 'remaining': config['remaining'],
                              'truncated_posts': config['truncated'], 'reserved_tokens': config['reservation'],
                              'actual_model_calls': 0, 'saved_memories': 0, 'forum_writes': 0}))
        finally:
            (await client.post('/api/memory-imports/' + job['id'] + '/cancel', headers=csrf)).raise_for_status()
            await client.post('/api/auth/logout', headers=csrf)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--topic', type=int, required=True)
    asyncio.run(check(parser.parse_args().topic))
