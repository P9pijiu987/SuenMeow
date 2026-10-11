"""Real DeepSeek protocol checks over fictional posts; no real forum reads or writes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from suenmeow.adapters import Models, safe_post
from suenmeow.database import Database, KV, Observation, Reaction, Reply, Snapshot, Usage, now
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


async def check():
    settings = Settings.env()
    production, original_vault = Database(settings.database_url), Vault(settings.key_file)
    with production.transaction() as s:
        assert s.get(KV, 'control').data['mode'] in ('paused', 'read_only')
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
            'daily_tokens': 200000}).model_dump()
        with db.transaction() as s:
            snapshot = Snapshot(actor='fictional-check', note='isolated paid model check', data=data)
            s.add(snapshot); s.flush()
            s.get(KV, 'control').data = {'mode': 'approval', 'epoch': 1, 'active_snapshot': snapshot.id}
            s.add(KV(key='connection:forum', data={'cipher': vault.seal(FictionalForum.connection)}))
        w = Worker(db, vault)
        w.forum, w.models = FictionalForum(), Models(db, {'planner': route})
        outputs = []
        try:
            await w.baseline(1)
            for tid, texts in [(900001, ['虚构验收：今天薄荷新长了叶子，好开心。', '虚构验收：@SuenMeow，种薄荷要注意什么？']),
                               (900002, ['虚构验收：最近情绪很低落，今天终于鼓起勇气寻求帮助。', '虚构验收：这个问题上面已经详细回答，谢谢大家。'])]:
                first_id = len(w.forum.posts) + 1
                for offset, text in enumerate(texts):
                    w.forum.posts.append({'id': first_id + offset, 'topic_id': tid, 'post_number': offset + 1,
                        'user_id': 9100 + offset, 'username': 'fictional-person-' + str(offset), 'raw': text, 'post_type': 1,
                        'created_at': datetime.fromtimestamp(now(), timezone.utc).isoformat()})
                w.interactions.poll_at = 0
                with db.transaction() as s: snapshot = s.get(Snapshot, 1)
                await w.interactions.collect({'mode': 'approval', 'epoch': 1}, snapshot)
                with db.transaction() as s:
                    for o in s.scalars(select(Observation).where(Observation.state == 'pending')): o.created = now() - 15
                await w.interactions.prepare_one()
                with db.transaction() as s:
                    rows = list(s.scalars(select(Observation).where(Observation.topic_id == tid)))
                    assert len(rows) == 2 and all(o.state == 'evaluated' for o in rows), [(o.state, o.reason) for o in rows]
                    reply = s.scalar(select(Reply).where(Reply.topic_id == tid))
                    outputs.append({'fictional_topic': tid, 'judgments': [{'emoji': o.data.get('emoji'), 'reason': o.reason} for o in rows],
                                    'reply': vault.open(reply.text_cipher) if reply else '', 'memory_calls': 0})
            with db.transaction() as s:
                assert all(r.state == 'approval' for r in s.scalars(select(Reaction)))
                print(json.dumps({'provider': 'api.deepseek.com', 'fictional_batches': 2, 'calls': s.scalar(select(func.count()).select_from(Usage)),
                                  'tokens': s.scalar(select(func.sum(Usage.tokens))), 'outputs': outputs,
                                  'production_writes': 0, 'real_forum_reads': 0, 'forum_writes': 0}, ensure_ascii=False))
        finally: await w.models.close(); db.engine.dispose()
    production.engine.dispose()


if __name__ == '__main__': asyncio.run(check())
