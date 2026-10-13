"""Validate full-research extraction and index-only merge with invented data. No forum/fact writes."""
import asyncio
import json

from sqlalchemy import select

from suenmeow.adapters import Models, json_output
from suenmeow.database import Database, KV, Snapshot, Usage, uid
from suenmeow.domain import Policy
from suenmeow.full_memory import FULL_PROMPT, MERGE_PROMPT, FullFacts, Selection
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        route = vault.open(s.get(KV, 'connection:memory').data['cipher'])
        snap = s.get(Snapshot, s.get(KV, 'control').data['active_snapshot']).data
    work = '\n\n'.join(snap['modules'][key]['content'] for key in snap['pipeline']['memory'])
    posts = [{'id': 1, 'number': 1, 'created': '2020-01-01T00:00:00Z', 'text': '以前我在学校的天文社团参加观测，持续了两年。'},
             {'id': 2, 'number': 2, 'created': '2023-01-01T00:00:00Z', 'text': '我喜欢照料盆栽，每天照料薄荷。'},
             {'id': 3, 'number': 3, 'created': '2026-10-05T00:00:00Z', 'text': '现在周末喜欢骑自行车，仍然喜欢天文观测。'}]
    tasks, model = [uid(), uid()], Models(db, {'memory': route})
    try:
        raw = await model.complete('memory', [{'role': 'system', 'content': work + '\n\n' + FULL_PROMPT},
            {'role': 'user', 'content': json.dumps({'author': 'synthetic_alice', 'order': 'oldest_first', 'posts': posts}, ensure_ascii=False)}],
            0, Policy.model_validate(snap['policy']), task_id=tasks[0], output_limit=route['max_output'], compact_json=True)
        facts = FullFacts.model_validate(json_output(raw)).facts
        assert facts and all(any(p['id'] == f.source_post_id and f.quote in p['text'] for p in posts) for f in facts)
        assert any(f.source_post_id == 1 for f in facts), 'Historical author experience must be researched too'
        raw = await model.complete('memory', [{'role': 'system', 'content': work + '\n\n' + MERGE_PROMPT},
            {'role': 'user', 'content': json.dumps({'facts': [f.model_dump() for f in facts]}, ensure_ascii=False)}],
            0, Policy.model_validate(snap['policy']), task_id=tasks[1], output_limit=route['max_output'], compact_json=True)
        keep = Selection.model_validate(json_output(raw)).keep
        assert keep and all(0 <= i < len(facts) for i in keep)
        with db.transaction() as s:
            usage = list(s.scalars(select(Usage).where(Usage.task_id.in_(tasks))))
            print(json.dumps({'synthetic_input_only': True, 'model_calls': len(usage), 'tokens': sum(u.tokens for u in usage),
                              'historical_source_included': True, 'facts': len(facts), 'selected': len(set(keep)),
                              'saved_memories': 0, 'forum_writes': 0, 'user_job_reruns': 0}))
    finally:
        await model.close()


if __name__ == '__main__':
    asyncio.run(check())
