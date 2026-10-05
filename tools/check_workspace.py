"""Public-console registration/editing probe; isolated fixtures only, no forum or model calls."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import secrets

import httpx

from suenmeow.settings import Settings


def call(client, method, path, body=None, expected=200):
    response = client.request(method, '/api' + path, json=body)
    assert response.status_code == expected, f'{path}: {response.status_code}'
    return response.json()


def login(client, username, password):
    result = call(client, 'POST', '/auth/login', {'username': username, 'password': password})
    client.headers['x-csrf-token'] = result['csrf']


def signature(workspace):
    return {m['id']: sha256(json.dumps({k:v for k,v in m.items() if k not in ('editable', 'is_persona', 'legacy_persona', 'persona_published_version')}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for m in workspace['modules']}


def check(args):
    origin = Settings.env().origin
    with httpx.Client(base_url=origin, headers={'Origin': origin}, timeout=30) as admin, \
            httpx.Client(base_url=origin, headers={'Origin': origin}, timeout=30) as editor:
        login(admin, 'admin', args.admin_password_file.read_text().strip())
        dashboard = call(admin, 'GET', '/dashboard')
        assert dashboard['control']['mode'] in ('paused', 'read_only'), 'Probe only before enabling sends'
        if args.prepare:
            assert not args.fixture_file.exists(), 'Do not overwrite an existing probe fixture'
            before = call(admin, 'GET', '/prompts/workspace')
            username, password = 'workspace-probe-' + secrets.token_hex(5), secrets.token_urlsafe(24)
            account = call(editor, 'POST', '/auth/register', {'username': username, 'password': password}, 201)
            assert account['role'] == 'editor' and not account['forum_username']
            editor.headers['x-csrf-token'] = account['csrf']
            assert all(not m['editable'] for m in call(editor, 'GET', '/prompts/workspace')['modules'])
            own = call(editor, 'POST', '/records/module', {'title': '验收 · 编辑者自己的模块',
                       'data': {'content': '以自然的猫咪语气回应；不虚构事实。', 'description': '仅用于注册与编辑验收'}})
            granted = call(admin, 'POST', '/records/module', {'title': '验收 · 授权协作模块',
                           'data': {'content': '遵守来源边界，保持讨论友善。', 'description': '用于验证逐模块授权'},
                           'grants': [account['id']]})
            fixture = {'id': account['id'], 'username': username, 'password': password,
                       'own': own['id'], 'granted': granted['id'], 'before': signature(before),
                       'pipeline': before['pipeline'], 'snapshot': before['active_snapshot']}
            args.fixture_file.parent.mkdir(parents=True, exist_ok=True)
            with args.fixture_file.open('x') as out:
                args.fixture_file.chmod(0o600)
                json.dump(fixture, out, ensure_ascii=False)
            call(editor, 'POST', '/auth/logout')
            call(admin, 'POST', '/auth/logout')
            print(json.dumps({'registered_editor_immediately': True, 'isolated_modules': 2,
                              'snapshot': fixture['snapshot'], 'mode': dashboard['control']['mode']}))
            return
        fixture = json.loads(args.fixture_file.read_text())
        assert fixture['username'].startswith('workspace-probe-')
        current = call(admin, 'GET', '/prompts/workspace')
        hashes = signature(current)
        assert all(hashes.get(mid) == value for mid, value in fixture['before'].items()), 'Existing prompts changed'
        assert current['pipeline'] == fixture['pipeline'] and current['active_snapshot'] == fixture['snapshot']
        if args.cleanup:
            for mid in (fixture['own'], fixture['granted']):
                module = next(m for m in current['modules'] if m['id'] == mid)
                assert module['title'].startswith('验收 · ')
                assert not any(mid in route for route in current['pipeline'].values())
                call(admin, 'DELETE', '/records/module/' + mid)
            call(admin, 'PUT', '/accounts/' + fixture['id'], {'username': fixture['username'], 'role': 'editor',
                'active': False, 'password': '', 'forum_username': ''})
            call(admin, 'POST', '/auth/logout')
            print(json.dumps({'fixtures_disabled_and_removed': True, 'existing_prompts_unchanged': True,
                              'snapshot': current['active_snapshot'], 'mode': dashboard['control']['mode']}))
            return
        login(editor, fixture['username'], fixture['password'])
        visible = call(editor, 'GET', '/prompts/workspace')
        assert {m['id'] for m in visible['modules'] if m['editable']} == {fixture['own'], fixture['granted']}
        assert visible['pipeline'] == current['pipeline'] and not visible['pipeline_editable'] and 'accounts' not in visible
        for path in ('/accounts', '/connections', '/config', '/agent/sessions', '/replies'):
            call(editor, 'GET', path, expected=403)
        call(editor, 'POST', '/config/publish', {}, 403)
        call(editor, 'PUT', '/auth/registration', {'enabled': False}, 403)
        call(editor, 'POST', '/prompts/workspace/save', {'pipeline': {}, 'pipeline_version': 1}, 403)
        module = next(m for m in visible['modules'] if m['editable'])
        change = {k: module[k] for k in ('id', 'title', 'data', 'version', 'grants')}
        call(editor, 'POST', '/prompts/workspace/save', {'modules': [{**change, 'grants': [] if module['grants'] else [fixture['id']]}]}, 403)
        change['data'] = {**change['data'], 'description': '真实 HTTPS 受限编辑 API 验收'}
        saved = call(editor, 'POST', '/prompts/workspace/save', {'modules': [change]})
        assert next(m for m in saved['modules'] if m['id'] == change['id'])['version'] == change['version'] + 1
        call(editor, 'POST', '/prompts/workspace/save', {'modules': [change]}, 409)
        csrf = editor.headers.pop('x-csrf-token')
        call(editor, 'POST', '/prompts/workspace/save', {'modules': []}, 403)
        after = call(admin, 'GET', '/prompts/workspace')
        assert after['pipeline'] == fixture['pipeline'] and after['active_snapshot'] == fixture['snapshot']
        editor.headers['x-csrf-token'] = csrf
        call(editor, 'POST', '/auth/logout')
        call(admin, 'POST', '/auth/logout')
        print(json.dumps({'restricted_visibility': True, 'server_permission_checks': 403,
                          'optimistic_conflict': 409, 'csrf_rejected': 403,
                          'existing_prompts_unchanged': True, 'snapshot': after['active_snapshot'],
                          'mode': dashboard['control']['mode']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument('--prepare', action='store_true')
    stage.add_argument('--cleanup', action='store_true')
    parser.add_argument('--fixture-file', type=Path, required=True)
    parser.add_argument('--admin-password-file', type=Path, default=Path('/run/secrets/probe_admin_password'))
    check(parser.parse_args())
