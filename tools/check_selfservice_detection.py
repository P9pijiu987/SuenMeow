"""Read-only discovery/full-source probe using a real verified identity. No model or new session."""
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select

from suenmeow.adapters import Discourse, source_text
from suenmeow.database import Account, Database, ForumIdentity, KV
from suenmeow.memory_import import mount_memory_import, SENSITIVE, verified_topic
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        conf = vault.open(s.get(KV, 'connection:forum').data['cipher'])
        category_id = s.get(KV, 'memory_import_settings').data['category_id']
        identity = s.scalar(select(ForumIdentity).join(Account, ForumIdentity.account_id == Account.id).where(
            ForumIdentity.site == conf['base_url'], Account.role == 'editor', Account.active.is_(True)).order_by(ForumIdentity.updated.desc()))
        assert identity, 'Requires an actual user-verified forum identity'
        account = s.get(Account, identity.account_id)

    routers = []
    mount_memory_import(SimpleNamespace(include_router=routers.append), db, vault, lambda: account)
    route = next(route for route in routers[0].routes if getattr(route, 'path', '') == '/api/memory-imports/detect')
    result = await route.endpoint(account=account)
    assert result['message'] in ('找到你在「個人帖」分类创建的主题', '没有找到你在「個人帖」分类创建的公开主题，请粘贴链接')
    assert result['category_id'] == category_id and len(result['candidates']) <= 5
    output = {'existing_verified_identity': True, 'category_id': category_id, 'category_filtered_candidates': len(result['candidates']),
              'model_calls': 0, 'saved_memories': 0, 'forum_writes': 0, 'new_sessions_or_proofs': 0}
    if result['candidates']:
        forum = Discourse(conf)
        try:
            await forum.login()
            topic, first = await verified_topic(forum, result['candidates'][0]['topic_id'], category_id)
            assert first['user_id'] == identity.user_id
            scanned, authors, filtered, chars = 0, 0, 0, 0
            stream = topic['post_stream']['stream']
            for offset in range(0, len(stream), 20):
                ids = stream[offset:offset + 20]
                posts = await forum.selected_posts(topic['id'], ids)
                scanned += len(ids)
                for post in posts:
                    if post['user_id'] != identity.user_id:
                        continue
                    text = source_text(post)
                    if post.get('identity_message') or not text or SENSITIVE.search(text):
                        filtered += 1
                    else:
                        authors += 1
                        chars += len(text)
            await verified_topic(forum, topic['id'], category_id)
            output.update({'all_visible_posts': scanned, 'author_posts': authors, 'author_chars': chars,
                           'filtered_author_posts': filtered, 'cursor_updates': 0})
        finally:
            await forum.close()
    print(json.dumps(output))


if __name__ == '__main__':
    asyncio.run(check())
