from copy import deepcopy

import pytest
from sqlalchemy import select

from conftest import login
from suenmeow.database import KV, Record, Snapshot, TopicPipeline
from suenmeow.service import publish
from suenmeow.topic_pipeline import pin_valid, resolve
from test_topic_pipeline import create, payload, setup_topic
from test_workspace import draft


def review(client, required):
    login(client)
    config = client.get('/api/topic-pipeline-settings').json()
    result = client.put('/api/topic-pipeline-settings', json={**config, 'require_review': required})
    assert result.status_code == 200, result.text
    return result.json()


def test_review_default_permissions_version_and_csrf(client):
    login(client, 'editor', 'editor-test-password')
    config = client.get('/api/topic-pipeline-settings').json()
    assert config == {'require_review': False, 'version': 1}
    assert client.put('/api/topic-pipeline-settings', json={**config, 'require_review': True}).status_code == 403
    login(client)
    assert client.put('/api/topic-pipeline-settings', json={**config, 'require_review': 'false'}).status_code == 422
    assert client.put('/api/topic-pipeline-settings', json={**config, 'require_review': True}).status_code == 200
    assert client.put('/api/topic-pipeline-settings', json=config).status_code == 409
    client.headers.pop('x-csrf-token')
    assert client.put('/api/topic-pipeline-settings', json={'require_review': False, 'version': 2}).status_code == 403


async def test_direct_save_review_toggle_and_reverification(client, env, setup_topic):
    ForumType, pid, _ = setup_topic
    review(client, False)
    row = create(client, pid)
    assert row['enabled'] and row['published_version'] == row['version'] == 1
    forum = ForumType({'base_url': 'https://forum.example', 'username': 'cat'})
    pin = await resolve(env[1], forum, 42, False)
    response = client.put('/api/topic-pipelines/' + row['id'], json=payload(pid) | {'title': '直接生效'})
    assert response.status_code == 200, response.text
    row = response.json()
    assert row['published_version'] == row['version'] == 2
    with env[1].transaction() as s:
        assert not pin_valid(s, pin)
        assert s.get(KV, 'control').data['mode'] == 'paused'
    review(client, True)
    login(client, 'editor', 'editor-test-password')
    response = client.put('/api/topic-pipelines/' + row['id'], json=payload(pid) | {'title': '等待审核', 'version': 2})
    row = response.json()
    assert row['version'] == 3 and row['published_version'] == 2 and row['published']['title'] == '直接生效'
    review(client, False)
    login(client, 'editor', 'editor-test-password')
    assert client.get('/api/topic-pipelines').json()[0]['published_version'] == 2
    activated = client.post('/api/topic-pipelines/' + row['id'] + '/publish', json={'version': 3})
    assert activated.status_code == 200, activated.text
    assert activated.json()['published_version'] == 3
    login(client, 'other', 'another-test-password')
    assert client.post('/api/topic-pipelines/' + row['id'] + '/publish', json={'version': 3}).status_code == 404
    login(client, 'editor', 'editor-test-password')
    ForumType.posts[0]['user_id'] = 8
    response = client.put('/api/topic-pipelines/' + row['id'], json=payload(pid) | {'version': 3})
    assert response.status_code == 422
    assert client.get('/api/topic-pipelines').json()[0]['version'] == 3


async def test_persona_auto_publication_review_and_active_uses(client, env, setup_topic):
    ForumType, _, _ = setup_topic
    review(client, False)
    login(client, 'editor', 'editor-test-password')
    response = client.post('/api/prompts/workspace/save', json={'modules': [
        {'id': 'new-persona', 'title': '自己的猫', 'data': {'persona': True, 'content': '初始人格'}}]})
    assert response.status_code == 200, response.text
    own = next(m for m in response.json()['modules'] if m['title'] == '自己的猫')
    pid = own['id']
    row = create(client, pid)
    forum = ForumType({'base_url': 'https://forum.example', 'username': 'cat'})
    pin = await resolve(env[1], forum, 42, False)
    response = client.post('/api/prompts/workspace/save', json={'modules': [draft(own, data={**own['data'], 'content': '直接更新人格'})]})
    assert response.status_code == 200, response.text
    own = next(m for m in response.json()['modules'] if m['id'] == pid)
    assert own['persona_published_version'] == 2
    assert (await resolve(env[1], forum, 42, False))['modules'][pid]['content'] == '直接更新人格'
    with env[1].transaction() as s: assert not pin_valid(s, pin)
    review(client, True)
    login(client, 'editor', 'editor-test-password')
    response = client.put('/api/records/module/' + pid, json={'title': own['title'], 'version': 2,
        'data': {**own['data'], 'content': '待审核人格'}})
    assert response.status_code == 200, response.text
    assert (await resolve(env[1], forum, 42, False))['modules'][pid]['content'] == '直接更新人格'
    assert client.post('/api/personas/' + pid + '/publish', json={'id': pid, 'version': 3}).status_code == 403
    workspace = client.get('/api/prompts/workspace').json()
    own = next(m for m in workspace['modules'] if m['id'] == pid)
    assert own['version'] == 3 and own['persona_published_version'] == 2
    login(client)
    assert client.post('/api/personas/' + pid + '/publish', json={'id': pid, 'version': 2}).status_code == 409
    assert client.post('/api/personas/' + pid + '/publish', json={'id': pid, 'version': 3}).status_code == 200
    assert (await resolve(env[1], forum, 42, False))['modules'][pid]['content'] == '待审核人格'
    with env[1].transaction() as s:
        current = s.get(TopicPipeline, row['id'])
        assert current.version == current.published_version == 1  # Persona publication preserves ordering.


def test_persona_publication_preserves_unpublished_global_work(client, env):
    login(client)
    with env[1].transaction() as s:
        version = publish(s, env[3]['admin'], 'baseline')
        old = deepcopy(s.get(Snapshot, version).data)
    workspace = client.get('/api/prompts/workspace').json()
    persona = next(m for m in workspace['modules'] if m['is_persona'])
    work = next(m for m in workspace['modules'] if not m['is_persona'])
    order = deepcopy(workspace['pipeline']); order['replyer'] = list(reversed(order['replyer']))
    response = client.post('/api/prompts/workspace/save', json={'modules': [
        draft(persona, data={**persona['data'], 'content': '新人格'}),
        draft(work, data={**work['data'], 'content': '不能偷偷发布的系统草稿'})],
        'pipeline': order, 'pipeline_version': workspace['pipeline_version']})
    assert response.status_code == 200, response.text
    with env[1].transaction() as s:
        current = s.get(Snapshot, s.get(KV, 'control').data['active_snapshot']).data
        assert current['modules'][persona['id']]['content'] == '新人格'
        assert current['modules'][work['id']] == old['modules'][work['id']]
        assert current['pipeline'] == old['pipeline'] and current['policy'] == old['policy']
        assert s.get(Record, work['id']).data['content'] == '不能偷偷发布的系统草稿'


def test_system_cannot_auto_publish_by_changing_persona_flag(client, env):
    login(client)
    with env[1].transaction() as s:
        version = publish(s, env[3]['admin'], 'baseline')
        old = deepcopy(s.get(Snapshot, version).data)
    workspace = client.get('/api/prompts/workspace').json()
    work = next(m for m in workspace['modules'] if not m['is_persona'])
    result = client.post('/api/prompts/workspace/save', json={'modules': [draft(work,
        data={**work['data'], 'persona': True, 'content': '原系统草稿'})]})
    assert result.status_code == 200, result.text
    with env[1].transaction() as s:
        assert s.get(KV, 'control').data['active_snapshot'] == version
        assert s.get(Snapshot, version).data == old


def test_active_arrangement_size_limit_rolls_back_persona_save(client, env, setup_topic):
    review(client, False)
    login(client, 'editor', 'editor-test-password')
    response = client.post('/api/prompts/workspace/save', json={'modules': [
        {'id': f'new-{i}', 'title': f'大型人格{i}', 'data': {'persona': True, 'content': 'a' * 40000}}
        for i in range(4)]})
    assert response.status_code == 200, response.text
    own = [m for m in response.json()['modules'] if m['title'].startswith('大型人格')]
    body = payload(own[0]['id']); body['personas'] = {key: [] for key in body['personas']}
    body['personas']['replyer'] = [m['id'] for m in own]
    row = client.post('/api/topic-pipelines', json=body).json()
    assert row['enabled']
    result = client.post('/api/prompts/workspace/save', json={'modules': [draft(own[0],
        data={**own[0]['data'], 'content': '猫' * 50000})]})
    assert result.status_code == 422
    with env[1].transaction() as s:
        assert s.get(Record, own[0]['id']).version == 1
        assert s.get(TopicPipeline, row['id']).generation == 1


def test_postgres_concurrent_review_settings(env):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from suenmeow.api import create_app
    settings, db, _, _ = env
    if db.engine.dialect.name != 'postgresql': pytest.skip('PostgreSQL locks required')
    def attempt(value):
        with TestClient(create_app(settings, db)) as client:
            login(client)
            return client.put('/api/topic-pipeline-settings', json={'require_review': value, 'version': 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: statuses = list(pool.map(attempt, (True, False)))
    assert sorted(statuses) == [200, 409]
