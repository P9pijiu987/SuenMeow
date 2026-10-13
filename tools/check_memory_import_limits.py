"""Two bounded DeepSeek calls with invented long input; never save facts or rerun a user's job."""
import asyncio
import json

from suenmeow.adapters import ModelOutputError, Models, json_output
from suenmeow.database import Database, KV, Snapshot, uid
from suenmeow.domain import Policy
from suenmeow.memory_import import Facts, PROMPT, RECENT_PROMPT
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def check():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        route = vault.open(s.get(KV, 'connection:memory').data['cipher'])
        snapshot = s.get(Snapshot, s.get(KV, 'control').data['active_snapshot']).data
    system = '\n\n'.join(snapshot['modules'][key]['content'] for key in snapshot['pipeline']['memory']) + '\n\n' + PROMPT + '\n\n' + RECENT_PROMPT
    # Explicitly invented, repetitive hobbies, similar input volume to a recent real failure.
    texts = ['我每天照料窗边的薄荷，喜欢研究盆栽。', '周末喜欢骑自行车，最近在练习摄影。',
             '现在喜欢绿色，过去喜欢蓝色。', '正在读科幻小说，喜欢写阅读笔记。']
    posts = [{'id': i, 'number': i, 'created': '2026-10-05T00:00:00Z', 'text': texts[i % 4] * 3}
             for i in range(34, 0, -1)]
    def messages():
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps({'author': 'synthetic_alice', 'order': 'newest_first', 'posts': posts}, ensure_ascii=False)}]
    while len(json.dumps(messages(), ensure_ascii=False).encode()) + 2000 + 512 > 12000:
        posts.pop()
    model = Models(db, {'memory': route})
    output = {'synthetic_input_only': True, 'input_posts': len(posts), 'saved_memories': 0, 'forum_writes': 0, 'user_job_reruns': 0}
    try:
        try:
            await model.complete('memory', messages(), 0, Policy.model_validate(snapshot['policy']),
                                 task_id=uid(), task_limit=12000, output_limit=1000)
            output['old_limit'] = 'completed_this_sample'
        except ModelOutputError as exc:
            output['old_limit'] = exc.code
            output['old_usage'] = exc.metrics
        result = await model.complete('memory', messages(), 0, Policy.model_validate(snapshot['policy']),
                                      task_id=uid(), task_limit=12000, output_limit=min(2000, route['max_output']), compact_json=True)
        facts = Facts.model_validate(json_output(result)).facts
        assert facts and all(any(p['id'] == fact.source_post_id and fact.quote in p['text'] for p in posts) for fact in facts)
        output.update({'new_limit': 'valid_quoted_facts', 'facts': len(facts), 'model_calls': 2})
        print(json.dumps(output))
    finally:
        await model.close()


if __name__ == '__main__':
    asyncio.run(check())
