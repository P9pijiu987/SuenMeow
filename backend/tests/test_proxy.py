from dataclasses import replace
import socket

from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from suenmeow.api import create_app
from suenmeow.database import KV, now
from suenmeow.security import client_address, digest


@pytest.fixture
def gateway_dns(monkeypatch):
    original = socket.getaddrinfo
    def lookup(host, *args, **kwargs):
        if host == 'gateway':
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('172.20.0.3', 0))]
        return original(host, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)


@pytest.mark.parametrize('peer,header,expected', [
    ('172.20.0.3', '198.51.100.1', '198.51.100.1'),
    ('172.20.0.3', '2001:db8:0:0::1', '2001:db8::1'),
    ('203.0.113.9', '198.51.100.1', '203.0.113.9'),
    ('172.20.0.3', '198.51.100.1, 203.0.113.9', '172.20.0.3'),
    ('172.20.0.3', 'fe80::1%fake', '172.20.0.3'),
    ('172.20.0.3', '', '172.20.0.3'),
])
def test_client_address_trust_boundary(env, gateway_dns, peer, header, expected):
    request = Request({'type': 'http', 'client': (peer, 5000), 'headers': [
        (b'x-suenmeow-client-ip', header.encode()), (b'x-forwarded-for', b'1.2.3.4')]})
    assert client_address(request, replace(env[0], trusted_proxy_host='gateway')) == expected


def test_no_proxy_config_or_failed_dns_never_trusts_header(env, monkeypatch):
    request = Request({'type': 'http', 'client': ('172.20.0.3', 5000), 'headers': [
        (b'x-suenmeow-client-ip', b'198.51.100.1')]})
    assert client_address(request, env[0]) == '172.20.0.3'
    def failure(*args):
        raise OSError('DNS unavailable')
    monkeypatch.setattr(socket, 'getaddrinfo', failure)
    assert client_address(request, replace(env[0], trusted_proxy_host='gateway')) == '172.20.0.3'


def test_distinct_users_behind_gateway_have_separate_registration_and_login_limits(env, gateway_dns):
    settings, db, _, _ = env
    settings = replace(settings, trusted_proxy_host='gateway')
    with db.transaction() as s:
        s.get(KV, 'registration_lock').data = {'rates': {
            digest('198.51.100.1'): {'count': 30, 'start': now()}, 'global': {'count': 30, 'start': now()}}}
        s.add(KV(key='login:' + digest('198.51.100.1')[:32], data={'count': 10, 'start': now()}))
    app = create_app(settings, db)
    with TestClient(app, client=('172.20.0.3', 5000)) as client:
        client.headers.update({'Origin': settings.origin, 'X-SuenMeow-Client-IP': '198.51.100.1'})
        body = {'username': 'new-editor', 'password': 'new-editor-test-password'}
        assert client.post('/api/auth/register', json=body).status_code == 429
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'strong-test-password'}).status_code == 429
        client.headers['X-SuenMeow-Client-IP'] = '198.51.100.2'
        assert client.post('/api/auth/register', json=body).status_code == 201
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'strong-test-password'}).status_code == 200
    # A direct caller cannot change its bucket by imitating the internal header.
    with db.transaction() as s:
        s.add(KV(key='login:' + digest('203.0.113.9')[:32], data={'count': 10, 'start': now()}))
    with TestClient(app, client=('203.0.113.9', 5000)) as client:
        client.headers['X-SuenMeow-Client-IP'] = '198.51.100.2'
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'strong-test-password'}).status_code == 429


def test_no_insecure_proxy_slash_redirect(client):
    response = client.post('/api/auth/login/', json={'username': 'admin', 'password': 'bad'})
    assert response.status_code == 404 and 'location' not in response.headers
