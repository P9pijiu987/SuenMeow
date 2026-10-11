"""Real-provider complete-memory check over fictional facts in an isolated database."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from suenmeow.adapters import Models
from suenmeow.agent import AgentEngine
from suenmeow.database import Account, AgentTask, Database, KV, Record, Snapshot, Usage, now
from suenmeow.domain import AgentPolicy, Policy
from suenmeow.security import Vault
from suenmeow.settings import Settings


class FictionalForum:
    connection = {'base_url': 'https://fictional.example', 'username': 'SuenMeow'}
    writes = []
    async def topic(self, tid, limit):
        assert tid == 900001
        return {'id': tid, 'title': '虚构记忆验收', 'post_stream': {'stream': [900002]},
                'context': [{'id': 900002, 'number': 1, 'username': 'fictional-user', 'user_id': 42,
                             'text': '这是公开虚构验收资料，我喜欢种薄荷，也喜欢读书。'}]}
    async def public_visible(self, topic): return True
    async def selected_posts(self, tid, ids): return (await self.topic(tid, 1))['context']


async def check():
    settings = Settings.env()
    production, original_vault = Database(settings.database_url), Vault(settings.key_file)
    with production.transaction() as s:
        control = s.get(KV, 'control').data
        original = deepcopy(s.get(Snapshot, control['active_snapshot']).data)
        route = original_vault.open(s.get(KV, 'connection:agent').data['cipher'])
    assert urlsplit(route['base_url']).hostname == 'api.deepseek.com'
    with tempfile.TemporaryDirectory(prefix='agent-memory-model-check-') as folder:
        key = Path(folder) / 'key'; key.write_bytes(Fernet.generate_key())
        vault, db = Vault(key), Database('sqlite:///' + folder + '/check.sqlite3')
        db.migrate()
        agent_policy = AgentPolicy(allowed_tools=['memory_lookup', 'memory_read_all'], max_seconds=300, max_tokens=100000)
        original['policy'] = Policy.model_validate(original['policy']).model_copy(update={'daily_tokens': 200000}).model_dump()
        with db.transaction() as s:
            account = Account(username='fictional-admin', password_hash='no-login', role='admin')
            s.add(account); s.flush()
            snap = Snapshot(actor=account.id, note='isolated fictional check', data=original)
            s.add(snap); s.flush()
            s.get(KV, 'control').data = {'mode': 'read_only', 'epoch': 1, 'active_snapshot': snap.id}
            s.get(KV, 'agent_policy').data = agent_policy.model_dump()
            s.add(KV(key='connection:forum', data={'cipher': vault.seal(FictionalForum.connection)}))
            for index in range(110):
                data = {'text': f'虚构验收事实 #{index + 1}：我喜欢薄荷，也喜欢读书；这是独立的虚构事实编号。',
                        'username': 'fictional-user', 'forum_user_id': 42, 'scope': 'public',
                        'topic_id': 900001, 'source_post_id': 900002, 'site': FictionalForum.connection['base_url']}
                s.add(Record(kind='memory', owner=account.id, title='fictional fact', data={'cipher': vault.seal(data)}))
            constraints = {'kind': 'research', 'private': False, 'target_topic': 0, 'reply_to': 0,
                           'max_chars': 3000, 'allow_send': False, 'epoch': 1, 'forum_version': 1,
                           'policy': agent_policy.model_dump()}
            instruction = ('这是明确虚构的模型验收。请读取 fictional-user（稳定 user_id=42）的全部记忆。'
                           '必须使用 memory_read_all；跟随 next_cursor 续读至 complete=true，再报告核验后的总数与主要偏好，'
                           '不能把第一页或 memory_lookup 的少量命中当作全部。结果简短，不发帖。')
            task = AgentTask(session_id='', owner=account.id, instruction_cipher=vault.seal(instruction), constraints=constraints,
                             snapshot_id=snap.id, state='running', expires=now() + 300)
            s.add(task); s.flush(); tid = task.id
        models = Models(db, {'agent': route})
        try:
            await AgentEngine(db, vault, FictionalForum(), models, tid).run()
            with db.transaction() as s:
                task = s.get(AgentTask, tid)
                assert task.state == 'completed', task.reason
                coverage = task.constraints['memory_coverage']['fictional-user']
                assert coverage['complete'] and coverage['total'] == 110, coverage
                assert len(task.constraints['memory_ids']) == 110
                text = vault.open(task.result_cipher)
                assert '110' in text, 'Result must accurately report the verified count'
                print(json.dumps({'provider': 'api.deepseek.com', 'fictional_facts': 110,
                                  'coverage': coverage, 'result': text,
                                  'calls': s.scalar(select(func.count()).select_from(Usage)),
                                  'tokens': s.scalar(select(func.sum(Usage.tokens))),
                                  'production_mutations': 0, 'real_forum_reads': 0, 'forum_writes': 0}, ensure_ascii=False))
        finally:
            await models.close()
            db.engine.dispose()
    production.engine.dispose()


if __name__ == '__main__':
    asyncio.run(check())
