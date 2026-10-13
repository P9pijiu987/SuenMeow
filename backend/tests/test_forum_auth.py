from datetime import datetime, timezone

import pytest
import pyotp
from sqlalchemy import select

from suenmeow.adapters import safe_post
from suenmeow.database import Account, ForumIdentity, ForumLogin, KV, now
from suenmeow.forum_auth import verify_forum_logins
from conftest import login
from suenmeow.worker import Worker


def configure(env):
    _, db, vault, _ = env
    with db.transaction() as s:
        s.add(KV(key='connection:forum', data={'cipher': vault.seal({
            'base_url': 'https://forum.example', 'username': 'cat', 'password': 'fake-forum-password'})}))


def start(client):
    result = client.post('/api/auth/forum/start', json={}, headers={'Origin': 'http://testserver'})
    assert result.status_code == 201, result.text
    return result.json()


class Forum:
    def __init__(self, code, **changes):
        self.post = {'id': 20, 'topic_id': 99, 'user_id': 7, 'username': 'alice', 'post_type': 1,
                     'raw': '我想登录后台，验证码是 ' + code,
                     'created_at': datetime.now(timezone.utc).isoformat(), **changes}
        self.topic = {'archetype': 'private_message', 'allowed_users': [{'id': 1}, {'id': 7}],
                      'post_stream': {'stream': [20]}}
        self.profile = {'id': 7, 'username': 'alice', 'name': '小鱼', 'avatar_template': '/avatar/{size}.png'}

    async def notifications(self):
        return [{'topic_id': 99, 'created_at': self.post['created_at']}]

    async def read(self, path, params=None):
        if path == '/session/current.json': return {'current_user': {'id': 1}}
        if path == '/t/99.json': return self.topic
        if path == '/t/99/posts.json': return {'post_stream': {'posts': [self.post]}}
        if path == '/u/alice.json': return {'user': self.profile}
        raise AssertionError(path)


@pytest.mark.asyncio
async def test_private_message_login_binds_stable_identity_and_reuses_account(client, env):
    configure(env)
    pending = start(client)
    assert client.get('/api/auth/forum/status/' + pending['id']).json()['state'] == 'pending'
    assert client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).status_code == 409
    await verify_forum_logins(env[1], Forum(pending['code']), 1)
    response = client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 200
    account = response.json()
    assert account['role'] == 'editor' and account['forum_user_id'] == 7 and account['display_name'] == '小鱼'
    assert client.get('/api/auth/me').json()['forum_username'] == 'alice'
    client.headers['x-csrf-token'] = account['csrf']
    assert client.get('/api/config').status_code == 403
    assert client.put('/api/auth/forum', json={'enabled': False, 'allow_signup': False}).status_code == 403
    assert client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).status_code == 404
    client.cookies.clear()
    other = start(client)
    forum = Forum(other['code']); forum.profile['name'] = '新昵称'
    await verify_forum_logins(env[1], forum, 1)
    result = client.post('/api/auth/forum/finish/' + other['id'], json={}, headers={'Origin': 'http://testserver'}).json()
    assert result['id'] == account['id'] and result['display_name'] == '新昵称'
    with env[1].transaction() as s:
        assert len(list(s.scalars(select(ForumIdentity)))) == 1
        assert all(pending['code'] not in str(row.profile) for row in s.scalars(select(ForumLogin)))


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['public', 'group', 'author', 'profile', 'quote', 'old', 'hidden', 'wrong_topic'])
async def test_identity_proof_rejects_false_sources(client, env, invalid):
    configure(env); pending = start(client); forum = Forum(pending['code'])
    if invalid == 'public': forum.topic['archetype'] = 'regular'
    if invalid == 'group': forum.topic['allowed_users'].append({'id': 8})
    if invalid == 'author': forum.post['user_id'] = 8
    if invalid == 'profile': forum.profile['id'] = 9
    if invalid == 'quote': forum.post['raw'] = '> ' + forum.post['raw']
    if invalid == 'old': forum.post['created_at'] = '2000-01-01T00:00:00Z'
    if invalid == 'hidden': forum.post['hidden'] = True
    if invalid == 'wrong_topic': forum.post['topic_id'] = 98
    await verify_forum_logins(env[1], forum, 1)
    assert client.get('/api/auth/forum/status/' + pending['id']).json()['state'] == 'pending'


def test_browser_binding_expiry_origin_rotation_and_limits(client, env):
    configure(env)
    assert client.post('/api/auth/forum/start', json={}).status_code == 403
    pending = start(client); cookie = client.cookies.get('sm_forum_login')
    client.cookies.clear()
    assert client.get('/api/auth/forum/status/' + pending['id']).status_code == 404
    client.cookies.set('sm_forum_login', cookie, path='/api/auth/forum')
    replacement = start(client)
    assert client.get('/api/auth/forum/status/' + pending['id']).status_code == 404
    with env[1].transaction() as s: s.get(ForumLogin, replacement['id']).expires = now() - 1
    assert client.get('/api/auth/forum/status/' + replacement['id']).status_code == 410
    for _ in range(8): start(client)
    assert client.post('/api/auth/forum/start', json={}, headers={'Origin': 'http://testserver'}).status_code == 429


@pytest.mark.asyncio
async def test_disabled_signup_old_identity_and_disabled_account_are_not_bypassed(client, env):
    configure(env); pending = start(client)
    await verify_forum_logins(env[1], Forum(pending['code']), 1)
    login(client)
    assert client.put('/api/auth/forum', json={'enabled': True, 'allow_signup': False}).status_code == 200
    assert client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).status_code == 403
    client.put('/api/auth/forum', json={'enabled': True, 'allow_signup': True})
    with env[1].transaction() as s: s.get(Account, env[3]['editor']).forum_username = 'alice'
    assert client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).status_code == 409
    with env[1].transaction() as s: s.get(Account, env[3]['editor']).forum_username = 'human'
    response = client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 200
    aid = response.json()['id']
    with env[1].transaction() as s: s.get(Account, aid).active = False
    pending = start(client)
    await verify_forum_logins(env[1], Forum(pending['code']), 1)
    assert client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).status_code == 403


@pytest.mark.asyncio
async def test_totp_is_enforced_and_confirmation_is_bounded(client, env):
    configure(env); pending = start(client)
    await verify_forum_logins(env[1], Forum(pending['code']), 1)
    aid = client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).json()['id']
    secret = pyotp.random_base32()
    with env[1].transaction() as s: s.get(Account, aid).totp_cipher = env[2].seal(secret)
    pending = start(client); await verify_forum_logins(env[1], Forum(pending['code']), 1)
    assert client.get('/api/auth/forum/status/' + pending['id']).json()['totp_required']
    for _ in range(5):
        assert client.post('/api/auth/forum/finish/' + pending['id'], json={'code': 'wrong'}, headers={'Origin': 'http://testserver'}).status_code == 403
    assert client.post('/api/auth/forum/finish/' + pending['id'], json={'code': pyotp.TOTP(secret).now()}, headers={'Origin': 'http://testserver'}).status_code == 429


def test_account_verification_text_never_enters_model_context():
    code = 'SM-' + 'a' * 32
    for text in ['我要登录 ' + code, '注册账号 alice 密码 my-secret-password']:
        data = safe_post({'id': 1, 'raw': text})
        assert data['identity_message'] and text not in data['text']


@pytest.mark.asyncio
async def test_verifier_works_while_bot_paused_without_models_or_sends(client, env):
    configure(env); pending = start(client)
    forum = Forum(pending['code'])
    async def noop(): pass
    forum.login = forum.close = noop
    worker = Worker(env[1], env[2], forum_factory=lambda conf: forum)
    await worker.poll_forum_auth()
    assert worker.models is None and worker.forum is None
    assert client.get('/api/auth/forum/status/' + pending['id']).json()['state'] == 'verified'


@pytest.mark.asyncio
async def test_changed_connection_and_disabled_login_expire_proofs(client, env):
    configure(env); pending = start(client)
    await verify_forum_logins(env[1], Forum(pending['code']), 1)
    with env[1].transaction() as s: s.get(KV, 'connection:forum').version += 1
    assert client.get('/api/auth/forum/status/' + pending['id']).status_code == 410
    pending = start(client)
    login(client)
    client.put('/api/auth/forum', json={'enabled': False, 'allow_signup': True})
    assert client.get('/api/auth/forum/status/' + pending['id']).status_code == 410
    assert client.post('/api/auth/forum/start', json={}, headers={'Origin': 'http://testserver'}).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('group', [False, True])
async def test_real_discourse_nested_participants_shape(client, env, group):
    configure(env); pending = start(client); forum = Forum(pending['code'])
    forum.topic['details'] = {'allowed_users': forum.topic.pop('allowed_users'), 'allowed_groups': [{'id': 99}] if group else []}
    await verify_forum_logins(env[1], forum, 1)
    assert client.get('/api/auth/forum/status/' + pending['id']).json()['state'] == ('pending' if group else 'verified')


@pytest.mark.asyncio
async def test_avatar_rejects_external_or_private_origin(client, env):
    configure(env); pending = start(client)
    forum = Forum(pending['code']); forum.profile['avatar_template'] = 'http://127.0.0.1/secret'
    await verify_forum_logins(env[1], forum, 1)
    aid = client.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).json()['id']
    assert client.get('/api/auth/avatar/' + aid).status_code == 404
    assert client.get('/api/auth/avatar/' + env[3]['admin']).status_code == 403


@pytest.mark.asyncio
async def test_postgres_concurrent_finish_consumes_once(client, env):
    if env[1].engine.dialect.name != 'postgresql': pytest.skip('requires PostgreSQL')
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from suenmeow.api import create_app
    configure(env); pending = start(client)
    await verify_forum_logins(env[1], Forum(pending['code']), 1)
    cookie = client.cookies.get('sm_forum_login')
    app = create_app(env[0], env[1])
    def finish():
        with TestClient(app) as other:
            other.cookies.set('sm_forum_login', cookie, path='/api/auth/forum')
            return other.post('/api/auth/forum/finish/' + pending['id'], json={}, headers={'Origin': 'http://testserver'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: statuses = list(pool.map(lambda _: finish(), range(2)))
    assert sorted(statuses) == [200, 409]
    with env[1].transaction() as s: assert len(list(s.scalars(select(ForumIdentity)))) == 1
