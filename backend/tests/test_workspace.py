from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from suenmeow.api import create_app
from suenmeow.database import Account, KV, Record, Snapshot
from conftest import login


def register(client, **extra):
    return client.post('/api/auth/register', headers={'Origin': 'http://testserver'},
                       json={'username': 'new-editor', 'password': 'new-editor-test-password', **extra})


def draft(module, **changes):
    return {k: deepcopy(module[k]) for k in ('id', 'title', 'data', 'grants', 'version')} | changes


def test_registration_immediate_and_restricted(client, env):
    _, db, _, ids = env
    assert client.get('/api/auth/registration').json()['enabled']
    assert register(client, role='admin').status_code == 422
    assert register(client, forum_username='human').status_code == 422
    response = register(client)
    assert response.status_code == 201
    account = response.json()
    assert account['role'] == 'editor' and account['forum_username'] == ''
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    client.headers['x-csrf-token'] = account['csrf']
    assert client.get('/api/auth/me').json()['id'] == account['id']
    workspace = client.get('/api/prompts/workspace').json()
    assert workspace['modules'] == [] and workspace['pipeline'] is None and 'accounts' not in workspace
    assert client.get('/api/accounts').status_code == 403
    assert client.get('/api/connections').status_code == 403
    assert client.get('/api/agent/sessions').status_code == 403
    assert client.post('/api/config/publish', json={}).status_code == 403
    assert client.put('/api/auth/registration', json={'enabled': False}).status_code == 403
    assert client.post('/api/records/nest', json={'title': 'Claim identity', 'data': {
        'topic_id': 123, 'forum_username': 'human'}}).status_code == 403
    new = {'id': 'new-a', 'title': 'My module', 'data': {'content': 'Private draft'}, 'version': 1}
    saved = client.post('/api/prompts/workspace/save', json={'modules': [new]})
    assert saved.status_code == 200, saved.text
    own = saved.json()['modules'][0]
    assert own['owner'] == account['id']
    assert client.post('/api/prompts/workspace/save', json={'modules': [draft(own, grants=[ids['other']])]}).status_code == 403
    assert client.post('/api/prompts/workspace/save', json={'pipeline': {}, 'pipeline_version': 1}).status_code == 403
    assert client.post('/api/records/memory', json={'title': 'x', 'data': {'scope': 'private', 'text': 'claim'}}).status_code == 403
    with db.transaction() as s:
        assert s.get(Account, account['id']).password_hash != 'new-editor-test-password'
        assert s.get(KV, 'control').data['active_snapshot'] == 0
    client.headers.pop('x-csrf-token')
    assert client.post('/api/prompts/workspace/save', json={'modules': []}).status_code == 403


def test_registration_origin_duplicates_and_disable(client):
    body = {'username': 'new-editor', 'password': 'new-editor-test-password'}
    assert client.post('/api/auth/register', json=body).status_code == 403
    assert client.post('/api/auth/register', headers={'Origin': 'https://evil.example'}, json=body).status_code == 403
    assert register(client, username='ADMIN').status_code == 409
    assert register(client).status_code == 201
    assert register(client, username='New-Editor').status_code == 409
    login(client)
    assert client.put('/api/auth/registration', json={'enabled': False}).status_code == 200
    assert register(client, username='next-editor').status_code == 403
    assert not client.get('/api/auth/registration').json()['enabled']
    assert client.get('/api/auth/me').json()['role'] == 'admin'


def test_registration_rate_and_no_secret_echo(client, env):
    _, db, _, _ = env
    secret = 'short-key'
    response = register(client, password=secret)
    assert response.status_code == 422 and secret not in response.text
    with db.transaction() as s:
        from suenmeow.database import now
        s.get(KV, 'registration_lock').data = {'rates': {'global': {'count': 120, 'start': now()}}}
    assert register(client).status_code == 429


def test_atomic_new_module_and_routing(client, env):
    login(client)
    base = client.get('/api/prompts/workspace').json()
    pipeline = deepcopy(base['pipeline'])
    pipeline['replyer'].append('new-intro')
    response = client.post('/api/prompts/workspace/save', json={
        'modules': [{'id': 'new-intro', 'title': 'Introduction', 'data': {'content': 'Hello'}}],
        'pipeline': pipeline, 'pipeline_version': base['pipeline_version']})
    assert response.status_code == 200, response.text
    updated = response.json()
    real_id = updated['id_mapping']['new-intro']
    assert updated['pipeline']['replyer'][-1] == real_id
    assert updated['pipeline_version'] == base['pipeline_version'] + 1
    assert client.post('/api/prompts/workspace/save', json={
        'pipeline': pipeline, 'pipeline_version': base['pipeline_version']}).status_code == 409
    with env[1].transaction() as s:
        assert list(s.scalars(select(Snapshot))) == []
        assert s.get(KV, 'control').data['active_snapshot'] == 0


def test_atomic_conflict_rolls_back_other_module(client):
    login(client)
    modules = client.get('/api/prompts/workspace').json()['modules']
    first, second = sorted(modules[:2], key=lambda m: m['id'])
    bad = draft(second, version=second['version'] + 1)
    updated = draft(first, title='Should not persist')
    response = client.post('/api/prompts/workspace/save', json={'modules': [updated, bad]})
    assert response.status_code == 409
    saved = {m['id']: m for m in client.get('/api/prompts/workspace').json()['modules']}
    assert saved[first['id']]['title'] == first['title']
    assert saved[first['id']]['version'] == first['version']


def test_invalid_routing_rolls_back_creation_and_old_endpoint_invalidates_version(client):
    login(client)
    base = client.get('/api/prompts/workspace').json()
    pipeline = deepcopy(base['pipeline']); pipeline['replyer'].append('missing')
    response = client.post('/api/prompts/workspace/save', json={
        'modules': [{'id': 'new-rollback', 'title': 'Should not persist', 'data': {'content': ''}}],
        'pipeline': pipeline, 'pipeline_version': base['pipeline_version']})
    assert response.status_code == 422
    assert len(client.get('/api/prompts/workspace').json()['modules']) == len(base['modules'])
    assert client.put('/api/config/pipeline', json=base['pipeline']).status_code == 200
    assert client.post('/api/prompts/workspace/save', json={
        'pipeline': base['pipeline'], 'pipeline_version': base['pipeline_version']}).status_code == 409


def test_editor_grants_private_boundary_and_own_delete(client, env):
    login(client)
    admin_module = client.get('/api/prompts/workspace').json()['modules'][0]
    assert client.post('/api/prompts/workspace/save', json={
        'modules': [draft(admin_module, grants=[env[3]['editor']])]}).status_code == 200
    login(client, 'editor', 'editor-test-password')
    modules = client.get('/api/prompts/workspace').json()['modules']
    assert len(modules) == 1
    granted = modules[0]
    assert client.post('/api/prompts/workspace/save', json={
        'modules': [draft(granted, title='Authorized draft')]}).status_code == 200
    assert client.post('/api/prompts/workspace/save', json={
        'deleted': [{'id': granted['id'], 'version': granted['version'] + 1}]}).status_code == 403
    saved = client.post('/api/prompts/workspace/save', json={
        'modules': [{'id': 'new-own', 'title': 'Owned', 'data': {'content': ''}}]}).json()
    own = next(m for m in saved['modules'] if m['owner'] == env[3]['editor'])
    assert client.post('/api/prompts/workspace/save', json={
        'deleted': [{'id': own['id'], 'version': own['version']}]}).status_code == 200
    login(client, 'other', 'another-test-password')
    assert client.get('/api/prompts/workspace').json()['modules'] == []
    assert client.post('/api/prompts/workspace/save', json={'modules': [draft(granted)]}).status_code == 403


def test_own_referenced_delete_rejected_and_snapshot_immutable(client, env):
    login(client, 'editor', 'editor-test-password')
    saved = client.post('/api/prompts/workspace/save', json={
        'modules': [{'id': 'new-own', 'title': 'Owned', 'data': {'content': 'Original'}}]}).json()
    own = saved['modules'][0]
    login(client)
    base = client.get('/api/prompts/workspace').json()
    pipeline = deepcopy(base['pipeline']); pipeline['replyer'].append(own['id'])
    assert client.post('/api/prompts/workspace/save', json={
        'pipeline': pipeline, 'pipeline_version': base['pipeline_version']}).status_code == 200
    published = client.post('/api/config/publish', json={}).json()['version']
    login(client, 'editor', 'editor-test-password')
    assert client.post('/api/prompts/workspace/save', json={
        'deleted': [{'id': own['id'], 'version': own['version']}]}).status_code == 422
    assert client.post('/api/prompts/workspace/save', json={
        'modules': [draft(own, data={**own['data'], 'content': 'Changed draft'})]}).status_code == 200
    with env[1].transaction() as s:
        assert s.get(Snapshot, published).data['modules'][own['id']]['content'] == 'Original'
        assert s.get(KV, 'control').data['active_snapshot'] == published


def test_module_quota_applies_to_both_write_paths(client, env):
    with env[1].transaction() as s:
        s.add_all(Record(kind='module', owner=env[3]['editor'], title=f'Module {i}', data={
            'content': '', 'description': '', 'persona': False}) for i in range(50))
    login(client, 'editor', 'editor-test-password')
    assert client.post('/api/records/module', json={'title': 'Extra', 'data': {'content': ''}}).status_code == 422
    assert client.post('/api/prompts/workspace/save', json={
        'modules': [{'id': 'new-extra', 'title': 'Extra', 'data': {'content': ''}}]}).status_code == 422


def test_multiple_grants_scope_and_revocation(client, env):
    login(client)
    module = client.get('/api/prompts/workspace').json()['modules'][0]
    body = draft(module, grants=[env[3]['editor'], env[3]['other']])
    saved = client.post('/api/prompts/workspace/save', json={'modules': [body]}).json()
    updated = next(m for m in saved['modules'] if m['id'] == module['id'])
    login(client, 'other', 'another-test-password')
    assert client.get('/api/records/module').json()[0]['id'] == module['id']
    assert client.get('/api/prompts/workspace').json()['modules'][0]['id'] == module['id']
    login(client)
    assert client.post('/api/prompts/workspace/save', json={
        'modules': [draft(updated, grants=[env[3]['editor']])]}).status_code == 200
    login(client, 'other', 'another-test-password')
    assert client.get('/api/prompts/workspace').json()['modules'] == []
    assert client.post('/api/prompts/workspace/save', json={'modules': [draft(updated)]}).status_code == 403


def test_global_module_quota_cannot_be_bypassed_by_registering_again(client, env):
    with env[1].transaction() as s:
        s.add_all(Record(kind='module', owner=env[3]['admin'], title=f'Module {i}', data={
            'content': '', 'description': '', 'persona': False}) for i in range(500))
    result = register(client)
    assert result.status_code == 201
    client.headers['x-csrf-token'] = result.json()['csrf']
    assert client.get('/api/records/module').json() == []
    assert client.post('/api/records/module', json={'title': 'Extra', 'data': {'content': ''}}).status_code == 422
    assert client.post('/api/prompts/workspace/save', json={
        'modules': [{'id': 'new-extra', 'title': 'Extra', 'data': {'content': ''}}]}).status_code == 422


def test_postgres_concurrent_workspace_conflict(env):
    settings, db, _, _ = env
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row locks required')
    with TestClient(create_app(settings, db)) as client:
        login(client)
        module = client.get('/api/prompts/workspace').json()['modules'][0]
    def attempt(index):
        with TestClient(create_app(settings, db)) as client:
            login(client)
            return client.post('/api/prompts/workspace/save', json={
                'modules': [draft(module, title=f'Concurrent {index}')]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, range(2)))
    assert sorted(statuses) == [200, 409]


def test_postgres_simultaneous_registration(env):
    settings, db, _, _ = env
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row locks required')
    def attempt():
        with TestClient(create_app(settings, db)) as client:
            return register(client).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(statuses) == [201, 409]
    with db.transaction() as s:
        assert len(list(s.scalars(select(Account).where(Account.username == 'new-editor')))) == 1
