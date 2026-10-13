"""Real DeepSeek protocol checks over fictional posts; no real forum reads or writes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import base64
import struct
import zlib
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from suenmeow.adapters import Models, safe_post
from suenmeow.database import Account, Database, KV, Observation, Reaction, Record, Reply, Snapshot, Usage, now
from suenmeow.domain import InteractionPolicy, Policy
from suenmeow.interaction_prompts import RULES
from suenmeow.security import Vault
from suenmeow.settings import Settings
from suenmeow.worker import Worker


class FictionalForum:
    connection = {'base_url': 'https://fictional.example', 'username': 'SuenMeow'}
    bot_id = 9001
    def __init__(self): self.posts = []
    async def latest_posts(self, before=None): return [p for p in reversed(self.posts) if not before or p['id'] < before]
    async def latest(self): return []
    async def notifications(self): return []
    async def public_visible(self, topic): return True
    async def topic(self, tid, limit):
        return {'id': tid, 'title': '明确虚构的模型验收讨论', 'category_id': 22, 'archetype': 'regular',
                'valid_reactions': ['heart', 'hug', 'cat'], 'context': [safe_post(p) for p in self.posts if p['topic_id'] == tid][-limit:]}
    async def selected_posts(self, tid, ids): return [safe_post(p) for p in self.posts if p['id'] in ids and p['topic_id'] == tid]
    async def image_parts(self, posts, private=False):
        if not any(p.get('images') for p in posts): return []
        def chunk(name, data): return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data))
        pixels = b''.join(b'\x00'+b'\xff\x00\x00'*128+b'\x00\x00\xff'*128 for _ in range(256))
        png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',256,256,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
        return [{'type':'text','text':'本次明确虚构的图片识别验收附件：'},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png).decode(),'detail':'low'}}]


async def check():
    settings = Settings.env()
    production, original_vault = Database(settings.database_url), Vault(settings.key_file)
    with production.transaction() as s:
        # Production is read only for this probe, including when the user has
        # enabled auto. All execution and usage belong to the isolated database.
        original = deepcopy(s.get(Snapshot, s.get(KV, 'control').data['active_snapshot']).data)
        route = original_vault.open(s.get(KV, 'connection:planner').data['cipher'])
        marker = s.get(KV, 'prompt_refresh:20261004')
        persona_ids = marker.data['persona_ids'] if marker else []
    assert urlsplit(route['base_url']).hostname == 'api.deepseek.com', 'Only the expressly authorized provider may receive these inputs'
    with tempfile.TemporaryDirectory(prefix='interaction-model-check-') as folder:
        key = Path(folder) / 'key'; key.write_bytes(Fernet.generate_key())
        vault, db = Vault(key), Database('sqlite:///' + folder + '/check.sqlite3')
        db.migrate()
        data = deepcopy(original)
        data['modules']['fictional-interaction-rule'] = {'title': '新版互动规则', 'content': RULES, 'persona': False, 'version': 1}
        for mid in persona_ids:
            if mid in data['modules']: data['modules'][mid]['persona'] = True
        data['policy'] = Policy.model_validate(original['policy']).model_copy(update={
            'interaction': InteractionPolicy(rule_module='fictional-interaction-rule', extended_replies=False),
            'daily_tokens': 200000, 'topic_tokens': 60000}).model_dump()
        with db.transaction() as s:
            snapshot = Snapshot(actor='fictional-check', note='isolated paid model check', data=data)
            s.add(snapshot); s.flush()
            s.add(Account(username='fictional-check',password_hash='not-a-login-hash',role='admin'))
            s.get(KV, 'control').data = {'mode': 'approval', 'epoch': 1, 'active_snapshot': snapshot.id}
            s.add(KV(key='connection:forum', data={'cipher': vault.seal(FictionalForum.connection)}))
        w = Worker(db, vault)
        w.forum, w.models = FictionalForum(), Models(db, {'planner': route})
        fictional_responses = []
        async def capture(response):
            await response.aread()
            data = response.json()
            if data.get('choices'):
                fictional_responses.append(data['choices'][0]['message'].get('content', ''))
        w.models.client.event_hooks['response'] = [capture]
        outputs = []
        passed = False
        try:
            await w.baseline(1)
            for tid, texts in [(900001, ['虚构验收：今天薄荷新长了叶子，好开心。', '虚构验收：@SuenMeow，种薄荷要注意什么？']),
                               (900002, ['虚构验收：最近情绪很低落，今天终于鼓起勇气寻求帮助。', '虚构验收：这个问题上面已经详细回答，谢谢大家。']),
                               (900003, ['虚构验收：我的长期饮品偏好是薄荷茶，我一直喜欢薄荷茶。@SuenMeow，请准确描述附件图片的左半边和右半边颜色，不猜测；顺便记住我的饮品偏好。'])]:
                first_id = len(w.forum.posts) + 1
                for offset, text in enumerate(texts):
                    w.forum.posts.append({'id': first_id + offset, 'topic_id': tid, 'post_number': offset + 1,
                        'user_id': 9100 + offset, 'username': 'fictional-person-' + str(offset), 'raw': text, 'post_type': 1,
                        'created_at': datetime.fromtimestamp(now(), timezone.utc).isoformat()})
                    if tid == 900003:
                        w.forum.posts[-1]['cooked'] = '<p>明确虚构图像 <img src="https://fictional.example/image.png"></p>'
                w.interactions.poll_at = 0
                with db.transaction() as s: snapshot = s.get(Snapshot, 1)
                await w.interactions.collect({'mode': 'approval', 'epoch': 1}, snapshot)
                with db.transaction() as s:
                    for o in s.scalars(select(Observation).where(Observation.state == 'pending')): o.created = now() - 15
                await w.interactions.prepare_one()
                with db.transaction() as s:
                    rows = list(s.scalars(select(Observation).where(Observation.topic_id == tid)))
                    assert len(rows) == len(texts) and all(o.state == 'evaluated' for o in rows), [(o.state, o.reason) for o in rows]
                    reply = s.scalar(select(Reply).where(Reply.topic_id == tid))
                    if tid == 900003:
                        text = vault.open(reply.text_cipher) if reply else ''
                        assert '红' in text or '紅' in text, 'Red half was not recognized'
                        assert '蓝' in text or '藍' in text, 'Blue half was not recognized'
                        facts = [vault.open(r.data['cipher']) for r in s.scalars(select(Record).where(Record.kind=='memory'))]
                        assert any(f.get('origin')=='interaction_fact' and f.get('source_post_id')==first_id and '薄荷' in f.get('text','') for f in facts), 'Stable stated preference was not saved with source'
                    outputs.append({'fictional_topic': tid, 'judgments': [{'emoji': o.data.get('emoji'), 'reason': o.reason} for o in rows],
                                    'reply': vault.open(reply.text_cipher) if reply else '', 'memory_calls': 0})
            with db.transaction() as s:
                assert all(r.state == 'approval' for r in s.scalars(select(Reaction)))
                print(json.dumps({'provider': 'api.deepseek.com', 'fictional_batches': 3, 'calls': s.scalar(select(func.count()).select_from(Usage)),
                                  'tokens': s.scalar(select(func.sum(Usage.tokens))), 'outputs': outputs,
                                  'usage_details': [{'reserved': u.reserved, 'actual': u.tokens, 'metrics': u.metrics}
                                                    for u in s.scalars(select(Usage).order_by(Usage.created))],
                                  'production_writes': 0, 'real_forum_reads': 0, 'forum_writes': 0}, ensure_ascii=False))
                passed = True
        finally:
            if not passed:
                with db.transaction() as s:
                    print(json.dumps({'fictional_failed_run': True, 'calls': s.scalar(select(func.count()).select_from(Usage)),
                        'tokens': s.scalar(select(func.sum(Usage.tokens))),
                        'usage_details': [{'reserved': u.reserved, 'actual': u.tokens, 'metrics': u.metrics} for u in s.scalars(select(Usage))],
                        'last_fictional_response': fictional_responses[-1][:3000] if fictional_responses else ''}, ensure_ascii=False))
            await w.models.close(); db.engine.dispose()
    production.engine.dispose()


if __name__ == '__main__': asyncio.run(check())
