"""Read-only login diagnostics. Print booleans/counts, never codes, credentials or PM bodies."""
import asyncio
import json

from sqlalchemy import select

from suenmeow.adapters import Discourse, plain, timestamp
from suenmeow.database import Database, ForumLogin, KV, now
from suenmeow.security import LOGIN_CODE, Vault, digest
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        attempts = list(s.scalars(select(ForumLogin).where(ForumLogin.created > now() - 900).order_by(ForumLogin.created)))
        connection = s.get(KV, 'connection:forum')
        conf = vault.open(connection.data['cipher'])
        print(json.dumps({'enabled': s.get(KV, 'forum_auth').data, 'worker': s.get(KV, 'worker').data,
                          'attempts': [{'state': r.state, 'age_seconds': round(now() - r.created),
                                        'expires_in': round(r.expires - now()), 'connection_matches': r.forum_version == connection.version} for r in attempts]}))
    forum = Discourse(conf)
    try:
        await forum.login()
        bot = (await forum.read('/session/current.json'))['current_user']['id']
        notifications = await forum.notifications()
        earliest = min((r.created for r in attempts), default=now() - 300)
        recent = [n for n in notifications if timestamp(n.get('created_at')) >= earliest]
        print(json.dumps({'notifications': len(notifications), 'recent': len(recent),
                          'timestamp_field_present': all('created_at' in n for n in notifications),
                          'recent_types': [n.get('notification_type') for n in recent]}))
        tids = list(dict.fromkeys(n.get('topic_id') for n in recent if n.get('topic_id')))[:10]
        for tid in tids:
            topic = await forum.read(f'/t/{tid}.json')
            ids = topic.get('post_stream', {}).get('stream', [])[-10:]
            details_data = topic.get('details') or {}
            participants = {p['id'] for p in details_data.get('allowed_users', topic.get('allowed_users', []))}
            posts = (await forum.read(f'/t/{tid}/posts.json', [('post_ids[]', i) for i in ids])).get('post_stream', {}).get('posts', []) if ids else []
            details = []
            for post in posts:
                body = post.get('raw') or plain(post.get('cooked', ''))
                codes = LOGIN_CODE.findall(body)
                matching = [r for r in attempts if any(digest(code.lower()) == r.code_hash for code in codes)]
                if codes:
                    details.append({'codes': len(codes), 'matches_request': bool(matching), 'author_is_bot': post.get('user_id') == bot,
                                    'author_is_participant': post.get('user_id') in participants, 'post_type': post.get('post_type'),
                                    'topic_matches': post.get('topic_id') == tid, 'has_raw': 'raw' in post,
                                    'fresh': any(r.created <= timestamp(post.get('created_at')) <= min(r.expires, now() + 30) for r in matching),
                                    'quote': '[quote' in body.lower() or '<blockquote' in post.get('cooked', '').lower(),
                                    'hidden_or_deleted': bool(post.get('hidden') or post.get('deleted_at'))})
            print(json.dumps({'private_message': topic.get('archetype') == 'private_message', 'participants': len(participants),
                              'bot_participant': bot in participants, 'groups': bool(details_data.get('allowed_groups', topic.get('allowed_groups'))), 'code_posts': details}))
    finally:
        await forum.close()


if __name__ == '__main__':
    asyncio.run(check())
