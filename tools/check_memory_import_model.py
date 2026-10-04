"""Validate the configured memory model with synthetic personal posts; never save forum facts."""
import asyncio
import json

from sqlalchemy import select

from suenmeow.adapters import Models, json_output
from suenmeow.database import Database, KV, Snapshot, Usage, uid
from suenmeow.domain import Policy
from suenmeow.memory_import import Facts, PROMPT
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        route = vault.open(s.get(KV, 'connection:memory').data['cipher'])
        snapshot = s.get(Snapshot, s.get(KV, 'control').data['active_snapshot']).data
        system = '\n\n'.join(snapshot['modules'][key]['content'] for key in snapshot['pipeline']['memory']) + '\n\n' + PROMPT
    # All author and post content below is invented, with no real personal data.
    posts = [{'id': 1, 'number': 1, 'text': '我喜欢养盆栽，最近在种薄荷。'},
             {'id': 3, 'number': 3, 'text': '我周末喜欢骑自行车，最喜欢蓝色。'}]
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps({'author': 'synthetic_alice', 'posts': posts}, ensure_ascii=False)}]
    task_id = uid()
    model = Models(db, {'memory': route})
    try:
        response = await model.complete('memory', messages, 11957, Policy.model_validate(snapshot['policy']),
                                        task_id=task_id, task_limit=12000, output_limit=min(1000, route['max_output']))
        result = Facts.model_validate(json_output(response))
        assert result.facts and all(any(post['id'] == fact.source_post_id and fact.quote in post['text'] for post in posts) for fact in result.facts)
        with db.transaction() as s:
            usage = s.scalar(select(Usage).where(Usage.task_id == task_id))
            print(json.dumps({'real_model_protocol': True, 'synthetic_input_only': True, 'facts': len(result.facts),
                              'model_calls': 1, 'tokens': usage.tokens, 'accounting': usage.state,
                              'saved_memories': 0, 'forum_writes': 0}))
    finally:
        await model.close()


if __name__ == '__main__':
    asyncio.run(check())
