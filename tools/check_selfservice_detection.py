"""Read-only discovery using an existing verified identity; create no session or model request."""
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select

from suenmeow.adapters import Discourse
from suenmeow.database import Account, Database, ForumIdentity, KV
from suenmeow.memory_import import mount_memory_import
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        connection = s.get(KV, 'connection:forum')
        site = vault.open(connection.data['cipher'])['base_url']
        identity = s.scalar(select(ForumIdentity).join(Account, ForumIdentity.account_id == Account.id).where(
            ForumIdentity.site == site, Account.role == 'editor', Account.active.is_(True)).order_by(ForumIdentity.updated.desc()))
        assert identity, 'Requires an actual user-verified forum identity'
        account = s.get(Account, identity.account_id)
    # Invoke the read service with that existing identity; no fabricated session/proof is stored.
    routers = []
    mount_memory_import(SimpleNamespace(include_router=routers.append), db, vault, lambda: account)
    route = next(route for route in routers[0].routes if getattr(route, 'path', '') == '/api/memory-imports/detect')
    result = await route.endpoint(account=account)
    if result['message'] not in ('找到你创建的公开主题，请选择个人贴；也可以手动粘贴链接',
                                 '最近的创建记录中没有找到公开个人贴，请手动粘贴链接'):
        forum = Discourse(vault.open(connection.data['cipher']))
        stage = 'forum_login'
        try:
            await forum.login()
            stage = 'created_topics'
            actions = await forum.user_topics(identity.profile['username'])
            print(json.dumps({'diagnostic_login': True, 'created_records': len(actions),
                              'cached_pending': result['message'].startswith('正在查找')}))
        except Exception as exc:
            print(json.dumps({'diagnostic_stage': stage, 'diagnostic_error_type': type(exc).__name__,
                              'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)}))
        finally:
            await forum.close()
    assert result['message'] in ('找到你创建的公开主题，请选择个人贴；也可以手动粘贴链接',
                                  '最近的创建记录中没有找到公开个人贴，请手动粘贴链接'), 'Discovery did not complete normally'
    candidates = result['candidates']
    assert len(candidates) <= 5 and all(candidate['url'].startswith(site + '/t/') for candidate in candidates)
    print(json.dumps({'existing_verified_identity': True, 'discovery_completed': True, 'public_own_candidates': len(candidates),
                      'manual_link_fallback_available': True, 'model_calls': 0, 'saved_memories': 0, 'forum_writes': 0,
                      'new_sessions_or_proofs': 0}))


if __name__ == '__main__':
    asyncio.run(check())
