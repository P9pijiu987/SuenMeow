from sqlalchemy import select

from suenmeow.database import Record
from suenmeow.worker import Worker


class PublicForum:
    connection = {'base_url': 'https://forum.example', 'username': 'cat'}
    checked = []
    async def topic(self, topic_id, limit):
        self.checked.append(topic_id)
        return {'id': topic_id}
    async def public_visible(self, topic): return topic['id'] != 41
    async def selected_posts(self, topic_id, ids):
        return [{'id': ids[0], 'user_id': 7, 'text': '我在养薄荷，也骑自行车。'}]


async def test_retrieval_prioritizes_older_relevant_fact_over_recent_irrelevant(env):
    _, db, vault, ids = env
    with db.transaction() as s:
        for i in range(20):
            s.add(Record(kind='memory', owner=ids['editor'], title='无关近期记忆', updated=100+i,
                         data={'cipher': vault.seal({'text': f'周末骑自行车 {i}', 'username': 'alice', 'topic_id': 42, 'scope': 'public', 'source_post_id': 10})}))
        s.add(Record(kind='memory', owner=ids['editor'], title='相关旧记忆', updated=1, data={'cipher': vault.seal({
            'text': '喜欢照料薄荷', 'username': 'alice', 'topic_id': 43, 'scope': 'public', 'source_post_id': 11})}))
        for source, scope in [(41, 'public'), (44, 'private')]:
            s.add(Record(kind='memory', owner=ids['editor'], title='不可提供', updated=999, data={'cipher': vault.seal({
                'text': '薄荷种植经验', 'username': 'alice', 'topic_id': source, 'scope': scope, 'source_post_id': 12})}))
    worker = Worker(db, vault); worker.forum = PublicForum(); worker.forum.checked = []
    facts = await worker.checked_memories(99, False, {'alice'}, {7}, query='薄荷怎么养')
    assert facts[0]['text'] == '喜欢照料薄荷'
    assert len(facts) == 3  # One relevant fact plus at most two background facts.
    assert 44 not in worker.forum.checked and all(fact['text'] != '薄荷种植经验' for fact in facts)


async def test_personal_fact_site_boundary_and_source_edit_are_rechecked(env):
    _, db, vault, ids = env
    with db.transaction() as s:
        for site, quote in [('https://other.example', '薄荷'), ('https://forum.example', '已删除的原句')]:
            s.add(Record(kind='memory', owner=ids['editor'], title='个人贴事实', data={'cipher': vault.seal({
                'text': '喜欢薄荷', 'username': 'alice', 'forum_user_id': 7, 'site': site, 'origin': 'personal_topic',
                'topic_id': 42, 'scope': 'public', 'source_post_id': 12, 'quote': quote})}))
    worker = Worker(db, vault); worker.forum = PublicForum()
    assert await worker.checked_memories(99, False, {'alice'}, {7}, query='薄荷') == []
