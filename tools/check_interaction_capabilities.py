"""Read actual forum collection/reaction fields; never issue a toggle or model call."""
import asyncio
import json

from suenmeow.adapters import Discourse
from suenmeow.database import Database, KV
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        assert s.get(KV, 'control').data['mode'] in ('paused', 'read_only')
        conf = vault.open(s.get(KV, 'connection:forum').data['cipher'])
    forum = Discourse(conf)
    try:
        await forum.login()
        posts = await forum.latest_posts()
        assert forum.bot_id > 0 and posts
        latest = posts[0]
        topic = await forum.topic(latest['topic_id'], 1)
        post = await forum.reaction_post(latest['id'])
        assert topic['id'] == latest['topic_id'] and post['id'] == latest['id']
        assert isinstance(topic.get('valid_reactions'), list) and topic['valid_reactions']
        assert 'current_user_reaction' in post
        canonical = await forum.selected_posts(latest['topic_id'], [latest['id']])
        assert len(canonical) == 1 and canonical[0]['id'] == latest['id'] and canonical[0]['user_id']
        print(json.dumps({'stable_bot_identity': True, 'new_post_feed': True,
            'feed_records': len(posts), 'canonical_source': True, 'reaction_whitelist': True,
            'current_reaction_field': True, 'forum_writes': 0, 'model_calls': 0}))
    finally:
        await forum.close()
        db.engine.dispose()


if __name__ == '__main__':
    asyncio.run(check())
