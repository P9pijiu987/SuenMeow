"""Two bounded failed logins verify public-edge IP sanitation without exposing addresses."""
import json
import secrets
import socket

import httpx
from sqlalchemy import select

from suenmeow.database import Database, KV
from suenmeow.security import digest
from suenmeow.settings import Settings


def check():
    settings = Settings.env()
    assert settings.origin.startswith('https://') and settings.trusted_proxy_host
    db = Database(settings.database_url)
    with db.transaction() as s:
        assert s.get(KV, 'control').data['mode'] in ('paused', 'read_only')
        before = {r.key: r.data['count'] for r in s.scalars(select(KV).where(KV.key.like('login:%')))}
    spoofed = ('198.51.100.71', '198.51.100.72')
    with httpx.Client(base_url=settings.origin, headers={'Origin': settings.origin}, timeout=30) as client:
        for address in spoofed:
            response = client.post('/api/auth/login', json={
                'username': 'proxy-probe-' + secrets.token_hex(5), 'password': 'not-an-existing-password'},
                headers={'X-Forwarded-For': address, 'X-SuenMeow-Client-IP': address})
            assert response.status_code == 401, f'Unexpected login status {response.status_code}'
    gateway = {entry[4][0] for entry in socket.getaddrinfo(settings.trusted_proxy_host, None)}
    with db.transaction() as s:
        after = {r.key: r.data['count'] for r in s.scalars(select(KV).where(KV.key.like('login:%')))}
    changed = {key for key, count in after.items() if count != before.get(key, 0)}
    assert len(changed) == 1, 'Different spoofed headers must share the actual visitor bucket'
    key = next(iter(changed))
    assert after[key] - before.get(key, 0) == 2, 'Run the bounded check without concurrent login attempts'
    assert key not in {'login:' + digest(ip)[:32] for ip in (*spoofed, *gateway)}, 'Gateway or spoofed identity used'
    print(json.dumps({'public_edge_sanitized': True, 'spoofed_headers_ignored': True,
                      'visitor_bucket_separate_from_gateway': True, 'bounded_failed_attempts': 2}))


if __name__ == '__main__':
    check()
