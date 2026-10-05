"""Author-bound persona drafts, administrator publication and topic-local composition."""
from copy import deepcopy

from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from .adapters import Discourse
from .database import Account, ForumIdentity, KV, Record, TopicPipeline, audit, locked, now
from .domain import Strict
from .memory_import import verified_topic
from .personas import is_persona

ROUTES = ('planner', 'replyer', 'memory', 'summary', 'agent')


class PipelineDraft(Strict):
    title: str = Field(min_length=1, max_length=200)
    topic_id: int = Field(gt=0, le=2147483647)
    personas: dict[str, list[str]]
    version: int = Field(default=1, ge=1)


class PipelineVersion(Strict):
    version: int = Field(ge=1)


def validate_personas(s, order: dict) -> dict:
    if set(order) != set(ROUTES) or any(len(ids) > 40 or len(ids) != len(set(ids)) for ids in order.values()):
        raise HTTPException(422, '需要五条路由，每条最多40个不同人格')
    selected = set(mid for ids in order.values() for mid in ids)
    modules = {r.id: r for r in s.scalars(select(Record).where(Record.kind == 'module'))}
    if any(mid not in modules or not is_persona(s, modules[mid]) for mid in selected):
        raise HTTPException(422, '专属编排只能选择现有人格，系统工作规则由管理员维护')
    if any(sum(len(modules[mid].data.get('content', '').encode()) for mid in ids) > 200000 for ids in order.values()):
        raise HTTPException(422, '单条路由的人格内容过长')
    return {mid: {'title': modules[mid].title, 'content': modules[mid].data.get('content', ''),
                  'persona': True, 'version': modules[mid].version} for mid in selected}


def pipeline_view(s, row: TopicPipeline) -> dict:
    owner = s.get(Account, row.owner)
    identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == row.owner, ForumIdentity.site == row.site))
    return {'id': row.id, 'owner': row.owner, 'owner_name': identity.profile['username'] if identity else owner.username,
            'title': row.title, 'topic_id': row.topic_id, 'url': row.site + f'/t/{row.topic_id}',
            'personas': row.personas, 'version': row.version, 'published_version': row.published_version,
            'published': row.published, 'enabled': row.enabled, 'updated': row.updated}


def valid_binding(s, row: TopicPipeline) -> bool:
    account = s.get(Account, row.owner)
    conn = s.get(KV, 'connection:forum')
    category = s.get(KV, 'memory_import_settings')
    identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == row.owner, ForumIdentity.site == row.site))
    return bool(account and account.active and identity and identity.user_id == row.user_id and conn
                and conn.version == row.forum_version and category.version == row.category_version)


def pin_valid(s, pin: dict | None) -> bool:
    if not pin:
        return True
    row = s.get(TopicPipeline, pin['id'])
    return bool(row and row.enabled and row.generation == pin['revision'] and valid_binding(s, row))


def overlay(snapshot: dict, pin: dict | None) -> dict:
    if not pin:
        return snapshot
    result = deepcopy(snapshot)
    # Namespaced immutable persona content cannot collide with global work module IDs.
    for mid, data in pin['modules'].items():
        result['modules']['topic-persona:' + mid] = data
    for route in ROUTES:
        work = [mid for mid in snapshot['pipeline'].get(route, [])
                if not snapshot['modules'][mid].get('persona') and mid not in pin.get('persona_ids', [])]
        result['pipeline'][route] = ['topic-persona:' + mid for mid in pin['personas'][route]] + work
    return result


def verified_pin(db, topic: dict, first: dict, site: str) -> dict | None:
    """Caller has already checked public visibility and a genuine first post."""
    with db.transaction() as s:
        row = s.scalar(select(TopicPipeline).where(TopicPipeline.topic_id == topic['id'], TopicPipeline.site == site,
                                                   TopicPipeline.enabled.is_(True)))
        if (not row or not row.published or topic.get('archetype') == 'private_message'
                or topic.get('category_id') != s.get(KV, 'memory_import_settings').data['category_id']
                or first.get('user_id') != row.user_id or first.get('number') != 1 or not valid_binding(s, row)):
            return None
        return {'id': row.id, 'revision': row.generation, **deepcopy(row.published)}


async def resolve(db, forum, topic_id: int, private: bool) -> dict | None:
    if private or not forum.connection.get('base_url'):
        return None
    with db.transaction() as s:
        row = s.scalar(select(TopicPipeline).where(TopicPipeline.site == forum.connection['base_url'],
                                                   TopicPipeline.topic_id == topic_id, TopicPipeline.enabled.is_(True)))
        if not row or not valid_binding(s, row):
            return None
        category = s.get(KV, 'memory_import_settings').data['category_id']
    # Never infer topic ownership from the latest speaker or the context window.
    topic, first = await verified_topic(forum, topic_id, category)
    return verified_pin(db, topic, first, forum.connection['base_url'])


def mount_topic_pipelines(app, db, vault, user, admin):
    def owned(s, pid, account):
        row = s.get(TopicPipeline, pid)
        if not row or (row.owner != account.id and account.role != 'admin'):
            raise HTTPException(404, '编排不存在')
        return row

    async def verify_owner(account, topic_id):
        with db.transaction() as s:
            conn = s.get(KV, 'connection:forum')
            if not conn:
                raise HTTPException(409, '论坛尚未配置')
            config = vault.open(conn.data['cipher'])
            identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == account.id,
                                                            ForumIdentity.site == config['base_url']))
            if not identity:
                raise HTTPException(403, '请先使用论坛私信登录，绑定本人身份')
            category = s.get(KV, 'memory_import_settings')
            binding = dict(site=config['base_url'], user_id=identity.user_id, forum_version=conn.version,
                           category_version=category.version)
            category_id = category.data['category_id']
        forum = Discourse(config)
        try:
            import asyncio
            async with asyncio.timeout(60):
                await forum.login()
                _, first = await verified_topic(forum, topic_id, category_id)
                if first['user_id'] != binding['user_id']:
                    raise ValueError('只能绑定本人创建的公开个人贴')
            return binding
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except Exception:
            raise HTTPException(502, '暂时无法核验个人贴，请稍后重试')
        finally:
            await forum.close()

    @app.get('/api/topic-pipelines')
    def listing(account=Depends(user)):
        with db.transaction() as s:
            query = select(TopicPipeline).order_by(TopicPipeline.updated.desc())
            if account.role != 'admin':
                query = query.where(TopicPipeline.owner == account.id)
            return [pipeline_view(s, row) for row in s.scalars(query)]

    @app.post('/api/topic-pipelines', status_code=201)
    async def create(body: PipelineDraft, account=Depends(user)):
        binding = await verify_owner(account, body.topic_id)
        with db.transaction() as s:
            locked(s, 'editor_lock')
            locked(s, 'control')
            locked(s, 'topic_pipeline_lock')
            validate_personas(s, body.personas)
            if s.scalar(select(func.count()).select_from(TopicPipeline).where(TopicPipeline.owner == account.id)) >= 10:
                raise HTTPException(422, '每位用户最多10个个人贴编排')
            if s.scalar(select(TopicPipeline.id).where(TopicPipeline.site == binding['site'], TopicPipeline.topic_id == body.topic_id)):
                raise HTTPException(409, '该个人贴已有编排，请修改现有草稿')
            row = TopicPipeline(owner=account.id, title=body.title, topic_id=body.topic_id, personas=body.personas, **binding)
            if not valid_binding(s, row):
                raise HTTPException(409, '身份或论坛配置已变化，请重新核验')
            s.add(row); s.flush()
            audit(s, account.id, 'topic_pipeline_draft_created', row.id, topic_id=row.topic_id)
            return pipeline_view(s, row)

    @app.put('/api/topic-pipelines/{pid}')
    def save(pid: str, body: PipelineDraft, account=Depends(user)):
        with db.transaction() as s:
            locked(s, 'editor_lock')
            locked(s, 'control')
            locked(s, 'topic_pipeline_lock')
            row = owned(s, pid, account)
            if row.version != body.version:
                raise HTTPException(409, '编排已被修改，请重新读取后合并；本次未保存')
            if row.topic_id != body.topic_id:
                raise HTTPException(422, '绑定主题不能修改，请为另一篇本人个人贴新建编排')
            validate_personas(s, body.personas)
            row.title, row.personas, row.version, row.updated = body.title, body.personas, row.version + 1, now()
            audit(s, account.id, 'topic_pipeline_draft_saved', row.id)
            return pipeline_view(s, row)

    @app.post('/api/topic-pipelines/{pid}/publish')
    async def publish_personal(pid: str, body: PipelineVersion, account=Depends(admin)):
        with db.transaction() as s:
            row = owned(s, pid, account)
            owner = s.get(Account, row.owner)
            topic_id = row.topic_id
        binding = await verify_owner(owner, topic_id)
        with db.transaction() as s:
            locked(s, 'editor_lock')
            locked(s, 'control')
            locked(s, 'topic_pipeline_lock')
            row = owned(s, pid, account)
            if row.version != body.version:
                raise HTTPException(409, '草稿已更新，请重新审核')
            for key, value in binding.items():
                setattr(row, key, value)
            if not valid_binding(s, row):
                raise HTTPException(409, '身份或配置已变化')
            modules = validate_personas(s, row.personas)
            row.published = {'persona_ids': [r.id for r in s.scalars(select(Record).where(Record.kind == 'module')) if is_persona(s, r)],
                             'title': row.title, 'personas': deepcopy(row.personas), 'modules': modules, 'topic_id': row.topic_id,
                             'owner': row.owner, **binding}
            row.published_version, row.enabled = row.version, True
            row.generation += 1
            audit(s, account.id, 'topic_pipeline_published', row.id, version=row.version)
            return pipeline_view(s, row)

    @app.post('/api/topic-pipelines/{pid}/disable')
    def disable(pid: str, body: PipelineVersion, account=Depends(user)):
        with db.transaction() as s:
            locked(s, 'control')
            locked(s, 'topic_pipeline_lock')
            row = owned(s, pid, account)
            if row.version != body.version:
                raise HTTPException(409, '编排已变化，请重新读取')
            row.enabled, row.version, row.updated = False, row.version + 1, now()
            audit(s, account.id, 'topic_pipeline_disabled', row.id)
            return pipeline_view(s, row)
