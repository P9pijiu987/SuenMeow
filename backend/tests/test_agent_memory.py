"""Complete memory reads use real source checks and fake providers; no live writes."""
import json

import httpx
import pytest
from sqlalchemy import select

from suenmeow.agent import AgentEngine, AgentStopped, tool_specs
from suenmeow.database import AgentTask, KV, Record, now
from suenmeow.domain import AgentPolicy
from test_agent_workflow import Forum, Model, call, prepare


class MemoryForum(Forum):
    async def topic(self, tid, limit):
        data = await super().topic(tid, limit)
        data['context'][0].update(user_id=7, text='我喜欢薄荷，也骑自行车。')
        return data


def fact(db, vault, owner, text='喜欢薄荷', **changes):
    data = {'text': text, 'username': 'human', 'forum_user_id': 7, 'scope': 'public',
            'topic_id': 100, 'source_post_id': 101, 'site': 'https://forum.test', **changes}
    with db.transaction() as s:
        row = Record(kind='memory', owner=owner, title='fixture', data={'cipher': vault.seal(data)})
        s.add(row); s.flush()
        return row.id


def engine(env, **kwargs):
    db, vault, ids, tid = prepare(env, **kwargs)
    runner = AgentEngine(db, vault, MemoryForum(), None, tid)
    with db.transaction() as s:
        runner.constraints = dict(s.get(AgentTask, tid).constraints)
    runner.policy = AgentPolicy.model_validate(runner.constraints['policy'])
    runner.specs = tool_specs(runner.policy, runner.constraints['kind'])
    runner.started = now()
    runner.deadline = now() + 120
    return runner, ids


async def test_complete_read_does_not_drop_older_memories_after_global_500_limit(env):
    runner, ids = engine(env)
    with runner.db.transaction() as s:
        for index in range(520):
            data = {'text': f'另一人的记忆 {index}', 'username': 'other', 'scope': 'public',
                    'topic_id': 100, 'source_post_id': 101}
            s.add(Record(kind='memory', owner=ids['other'], title='other', updated=10000 + index,
                         data={'cipher': runner.vault.seal(data)}))
        for index in range(123):
            data = {'text': f'我喜欢薄荷 {index}', 'username': 'human', 'scope': 'public',
                    'topic_id': 100, 'source_post_id': 101, 'site': 'https://forum.test', 'forum_user_id': 7}
            s.add(Record(kind='memory', owner=ids['editor'], title='own', updated=index,
                         data={'cipher': runner.vault.seal(data)}))
    cursor, seen, reads = '', set(), []
    while True:
        page = await runner.execute('memory_read_all', {'username': 'human', 'cursor': cursor})
        assert not seen.intersection(row['memory_id'] for row in page['facts'])
        seen.update(row['memory_id'] for row in page['facts'])
        reads.append(page['read_count'])
        if page['complete']:
            assert page['total'] == 123 and page['remaining'] == 0 and not page['has_more']
            break
        assert page['total'] is None and page['remaining'] is None and page['has_more']
        cursor = page['next_cursor']
    assert reads == [50, 100, 123]
    assert len(runner.forum.reads) == 1  # Shared source checked once within this bounded task.
    with runner.db.transaction() as s:
        constraints = s.get(AgentTask, runner.task_id).constraints
        assert len(constraints['memory_ids']) == 123
        assert constraints['memory_coverage']['human']['complete']


async def test_public_read_does_not_reveal_private_other_site_or_restricted_counts(env):
    runner, ids = engine(env)
    fact(runner.db, runner.vault, ids['editor'])
    for changes in [{'scope': 'private', 'topic_id': 200, 'source_post_id': 201},
                    {'site': 'https://other.test'}, {'scope': 'staff'},
                    {'topic_id': 300, 'source_post_id': 301}, {'topic_id': 200, 'source_post_id': 201}]:
        fact(runner.db, runner.vault, ids['editor'], 'secret-must-not-be-disclosed', **changes)
    page = await runner.execute('memory_read_all', {'username': 'human'})
    assert page['complete'] and page['total'] == 1
    assert 'secret-must-not-be-disclosed' not in json.dumps(page)
    assert 200 in runner.denied


async def test_private_read_only_includes_original_private_topic_and_same_site(env):
    runner, ids = engine(env, private=True, target_topic=200)
    fact(runner.db, runner.vault, ids['editor'], 'public')
    fact(runner.db, runner.vault, ids['editor'], 'own-private', scope='private', topic_id=200, source_post_id=201)
    fact(runner.db, runner.vault, ids['editor'], 'other-private', scope='private', topic_id=400, source_post_id=401)
    fact(runner.db, runner.vault, ids['editor'], 'legacy-private', scope='private', site='', topic_id=200, source_post_id=201)
    page = await runner.execute('memory_read_all', {'username': 'human'})
    assert page['complete'] and page['total'] == 2
    assert {x['text'] for x in page['facts']} == {'public', 'own-private'}
    assert 400 not in runner.forum.reads


async def test_complete_read_validates_identity_quote_and_exact_post(env):
    runner, ids = engine(env)
    fact(runner.db, runner.vault, ids['editor'], 'valid', origin='personal_topic', quote='我喜欢薄荷')
    for changes in [{'quote': ''}, {'quote': '我从来不种薄荷'}, {'forum_user_id': 8}, {'source_post_id': 999}]:
        fact(runner.db, runner.vault, ids['editor'], 'invalid', origin='personal_topic', **({'quote': '我喜欢薄荷'} | changes))
    page = await runner.execute('memory_read_all', {'username': 'human', 'user_id': 7})
    assert page['total'] == 1 and page['facts'][0]['text'] == 'valid'


async def test_complete_read_uses_stable_identity_after_username_change(env):
    runner, ids = engine(env)
    fact(runner.db, runner.vault, ids['editor'], 'old-name', username='old-human')
    fact(runner.db, runner.vault, ids['editor'], 'another-user', forum_user_id=8)
    topic = runner.forum.topic
    async def renamed(tid, limit):
        data = await topic(tid, limit)
        data['context'][0]['username'] = 'new-human'
        return data
    runner.forum.topic = renamed
    page = await runner.execute('memory_read_all', {'username': 'new-human', 'user_id': 7})
    assert page['total'] == 1 and page['facts'][0]['text'] == 'old-name'


async def test_cursor_is_task_user_bound_and_catalog_never_restarts(env):
    runner, ids = engine(env)
    for index in range(3): fact(runner.db, runner.vault, ids['editor'], str(index))
    page = await runner.execute('memory_read_all', {'username': 'human', 'limit': 1})
    with pytest.raises(ValueError):
        await runner.execute('memory_read_all', {'username': 'other', 'cursor': page['next_cursor']})
    with pytest.raises(ValueError):
        await runner.execute('memory_read_all', {'username': 'human', 'cursor': 'a' * 32})
    with pytest.raises(ValueError):
        await runner.execute('memory_read_all', {'username': 'human', 'limit': 2})
    with runner.db.transaction() as s:
        original = s.get(AgentTask, runner.task_id)
        task = AgentTask(owner=original.owner, session_id='', instruction_cipher=original.instruction_cipher,
                         constraints=dict(original.constraints), snapshot_id=original.snapshot_id,
                         expires=now() + 120, state='running')
        s.add(task); s.flush(); other_task = task.id
    runner2 = AgentEngine(runner.db, runner.vault, MemoryForum(), None, other_task)
    runner2.constraints = dict(runner.constraints)
    runner2.policy = AgentPolicy.model_validate(runner2.constraints['policy'])
    runner2.specs = tool_specs(runner2.policy, 'research')
    runner2.started = now(); runner2.deadline = now() + 120
    with pytest.raises(ValueError):
        await runner2.execute('memory_read_all', {'username': 'human', 'cursor': page['next_cursor']})
    valid = await runner.execute('memory_read_all', {'username': 'human', 'cursor': page['next_cursor']})
    assert valid['total'] == 3 and valid['complete']


async def test_topic_limit_reports_incomplete_not_false_total(env):
    runner, ids = engine(env)
    runner.policy.max_topics = 1
    for topic in (100, 500):
        fact(runner.db, runner.vault, ids['editor'], f'topic-{topic}', topic_id=topic, source_post_id=topic + 1)
    page = await runner.execute('memory_read_all', {'username': 'human'})
    assert not page['complete'] and page['total'] is None and page['next_cursor']
    assert page['read_count'] == 1 and page['blocked_reason']


async def test_changed_or_deleted_rows_are_not_read_from_frozen_catalog(env):
    runner, ids = engine(env)
    older = fact(runner.db, runner.vault, ids['editor'], 'older')
    newest = fact(runner.db, runner.vault, ids['editor'], 'newest')
    page = await runner.execute('memory_read_all', {'username': 'human', 'limit': 1})
    assert page['facts'][0]['memory_id'] == newest
    with runner.db.transaction() as s:
        s.get(Record, older).version += 1
    last = await runner.execute('memory_read_all', {'username': 'human', 'cursor': page['next_cursor']})
    assert last['complete'] and last['total'] == 1 and not last['facts']


async def test_related_lookup_labels_results_as_partial_and_finds_old_rows(env):
    runner, ids = engine(env)
    for index in range(510): fact(runner.db, runner.vault, ids['other'], 'unrelated', username='other')
    fact(runner.db, runner.vault, ids['editor'], 'old relevant mint')
    result = await runner.execute('memory_lookup', {'username': 'human', 'keyword': 'mint'})
    assert len(result['facts']) == 1 and result['total'] is None and result['complete'] is False
    assert 'memory_read_all' in result['instruction']


async def test_failed_activity_tool_does_not_abort_other_research(env):
    db, vault, _, tid = prepare(env)
    class FailedActivity(MemoryForum):
        async def user_activity(self, username):
            raise httpx.HTTPStatusError('do-not-echo-private-response',
                                       request=httpx.Request('GET', 'https://forum.test/secret'),
                                       response=httpx.Response(404))
    model = Model([call('forum_user_activity', {'username': 'human'}),
                   call('memory_read_all', {'username': 'human'}),
                   {'role': 'assistant', 'content': '未取得活动，书架已核验。'}])
    await AgentEngine(db, vault, FailedActivity(), model, tid).run()
    assert 'do-not-echo-private-response' not in json.dumps(model.messages)
    with db.transaction() as s:
        assert s.get(AgentTask, tid).state == 'completed'


async def test_tool_revocation_during_page_stops_reading(env):
    runner, ids = engine(env)
    fact(runner.db, runner.vault, ids['editor'])
    async def revoke(tid, limit):
        data = await MemoryForum.topic(runner.forum, tid, limit)
        with runner.db.transaction() as s:
            current = s.get(KV, 'agent_policy')
            current.data = {**current.data, 'allowed_tools': ['memory_lookup']}
        return data
    runner.forum.topic = revoke
    with pytest.raises(AgentStopped):
        await runner.execute('memory_read_all', {'username': 'human'})


async def test_many_distinct_sources_are_verified_in_bounded_batches(env):
    runner, ids = engine(env)
    class ManySources(MemoryForum):
        batches = []
        async def topic(self, tid, limit):
            data = await super().topic(tid, limit)
            data['post_stream']['stream'] = list(range(101, 161))
            data['context'] = []
            return data
        async def selected_posts(self, tid, post_ids):
            self.batches.append(post_ids)
            assert 1 <= len(post_ids) <= 20
            return [{'id': pid, 'number': pid - 100, 'text': '我喜欢薄荷', 'username': 'human', 'user_id': 7}
                    for pid in post_ids]
    runner.forum = ManySources()
    for pid in range(101, 161): fact(runner.db, runner.vault, ids['editor'], f'fact-{pid}', source_post_id=pid)
    first = await runner.execute('memory_read_all', {'username': 'human'})
    last = await runner.execute('memory_read_all', {'username': 'human', 'cursor': first['next_cursor']})
    assert last['complete'] and last['total'] == 60
    assert len(runner.forum.batches) == 4  # 20 + 20 + 10, then 10 on page two.


async def test_edited_memory_invalidates_already_approved_agent_draft(env):
    from suenmeow.agent import confirm_draft
    from suenmeow.database import AgentDraft, Reply
    from suenmeow.service import claim_send, set_mode
    db, vault, ids, tid = prepare(env, kind='reply', target_topic=100)
    rid = fact(db, vault, ids['editor'])
    model = Model([call('memory_read_all', {'username': 'human'}), call('draft_reply', {'text': '依据已经核验的记忆生成的草稿。'})])
    await AgentEngine(db, vault, MemoryForum(), model, tid).run()
    with db.transaction() as s:
        set_mode(s, 'auto', ids['admin'])
        control = s.get(KV, 'control').data
        s.get(KV, 'worker').data = {'status': 'online', 'baseline_epoch': control['epoch'], 'heartbeat': now()}
        task = s.get(AgentTask, tid)
        draft = s.scalar(select(AgentDraft).where(AgentDraft.task_id == tid))
        reply_id = confirm_draft(s, vault, draft, task, draft.digest)
        s.get(Record, rid).version += 1
    assert claim_send(db, reply_id) is None
    with db.transaction() as s:
        assert '记忆已修改' in s.get(Reply, reply_id).reason


async def test_budget_exhaustion_preserves_partial_memory_progress_without_false_total(env):
    from suenmeow.service import BudgetExceeded
    db, vault, ids, tid = prepare(env)
    for index in range(3): fact(db, vault, ids['editor'], str(index))
    async def exhausted(messages): raise BudgetExceeded('task limit')
    model = Model([call('memory_read_all', {'username': 'human', 'limit': 1}), exhausted])
    await AgentEngine(db, vault, MemoryForum(), model, tid).run()
    with db.transaction() as s:
        task = s.get(AgentTask, tid)
        assert task.state == 'failed'
        result = vault.open(task.result_cipher)
        assert '已读取 1 条' in result and '尚未读完，总数未核实' in result
        assert task.constraints['memory_coverage']['human']['complete'] is False


async def test_arbitrary_name_cannot_relabel_another_users_stable_id(env):
    runner, ids = engine(env)
    fact(runner.db, runner.vault, ids['editor'], 'do-not-misattribute')
    result = await runner.execute('memory_read_all', {'username': 'another-person', 'user_id': 7})
    assert result['total'] == 0 and not result['facts']
