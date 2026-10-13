"""Explicit full-topic exports and reviews; no memory writes or forum send capability."""
import asyncio
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import delete, func, select

from .adapters import Discourse, ModelOutputError, json_output
from .database import (Account, ForumIdentity, KV, LoginSession, Snapshot, TopicReview, TopicReviewPart,
                       TopicReviewPost, Usage, audit, locked, now)
from .domain import Policy, Strict
from .full_memory import split_text
from .security import identity_message, private_identity_text
from .service import BudgetExceeded
from .topic_pipeline import overlay, pin_valid, verified_pin
from .topic_review_prompts import SUMMARY, REVIEW, PROTOCOL

class ReviewError(ValueError):
    """Safe, user-facing error; never forward arbitrary adapter/model exceptions."""


ACTIVE = ('queued', 'running')
MAX_POSTS, MAX_BYTES = 100000, 16 * 1024 * 1024


class StartReview(Strict):
    topic: str = Field(min_length=1, max_length=1000)
    mode: Literal['export', 'review'] = 'review'
    focus: str = Field(default='', max_length=1000)
    max_tokens: int = Field(default=200000, ge=1000, le=2000000)


class ReviewResult(Strict):
    summary: str = Field(min_length=1, max_length=6000)
    commentary: str = Field(min_length=1, max_length=6000)
    references: list[Annotated[int, Field(strict=True)]] = Field(min_length=1, max_length=12)


def topic_number(value, site):
    text = value.strip()
    if text.isascii() and text.isdigit():
        number = int(text)
    else:
        address, base = urlsplit(text), urlsplit(site)
        prefix = base.path.rstrip('/') + '/t/'
        if (address.scheme != base.scheme or address.netloc != base.netloc or address.username or address.password
                or not address.path.startswith(prefix)):
            raise HTTPException(422, '请输入已配置论坛的主题网址或主题 ID')
        parts = address.path[len(prefix):].strip('/').split('/')
        candidate = parts[0] if parts[0].isascii() and parts[0].isdigit() else parts[1] if len(parts) > 1 else ''
        if not candidate.isascii() or not candidate.isdigit():
            raise HTTPException(422, '无法识别主题 ID')
        number = int(candidate)
    if not 0 < number <= 2147483647:
        raise HTTPException(422, '主题 ID 超出范围')
    return number


def owned(s, job_id, owner):
    row = s.scalar(select(TopicReview).where(TopicReview.id == job_id, TopicReview.owner == owner))
    if not row:
        raise HTTPException(404, '整帖任务不存在')
    return row


def identity(s, account, site):
    if account.role == 'admin':
        return 0
    row = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == account.id, ForumIdentity.site == site))
    if not row:
        raise HTTPException(403, '请先使用论坛私信登录，绑定本人身份')
    return row.user_id


def valid_job(s, job, models=True):
    conf = job.config
    account = s.get(Account, job.owner)
    if not account or not account.active or job.expires <= now():
        raise ReviewError('账户或任务已失效')
    if identity(s, account, conf['site']) != conf['user_id']:
        raise ReviewError('论坛身份已变化')
    for route, version in conf['versions'].items():
        if not models and route != 'forum':
            continue
        current = s.get(KV, 'connection:' + route)
        if not current or current.version != version:
            raise ReviewError('连接配置已变化，请创建新任务')
    if models and not pin_valid(s, conf.get('topic_pipeline')):
        raise ReviewError('专属人格编排已变化，请创建新任务')


async def public_topic(forum, tid):
    topic = await forum.topic(tid, 1)
    if (topic.get('id') != tid or topic.get('archetype') == 'private_message' or topic.get('visible') is False
            or topic.get('deleted_at') or not await forum.public_visible(topic)):
        raise ReviewError('只能处理公开可见主题，私信和受限主题不可导出或点评')
    return topic


def view(s, vault, row, result=False):
    conf = row.config
    return {'id': row.id, 'topic_id': row.topic_id, 'state': row.state, 'reason': row.reason,
            'created': row.created, 'expires': row.expires,
            'config': {key: conf.get(key) for key in ('mode', 'focus', 'title', 'site', 'phase', 'total', 'offset', 'posts',
                      'excluded', 'bytes', 'read_complete', 'parts_done', 'merge_level', 'max_tokens', 'plain_text_posts')},
            'tokens': s.scalar(select(func.coalesce(func.sum(Usage.tokens), 0)).where(Usage.task_id == row.id)),
            'calls': s.scalar(select(func.count()).select_from(Usage).where(Usage.task_id == row.id)),
            'result': vault.open(row.result_cipher) if result and row.result_cipher else None}


def post_pages(db, vault, job_id):
    after = 0
    while True:
        with db.transaction() as s:
            rows = list(s.scalars(select(TopicReviewPost).where(TopicReviewPost.job_id == job_id, TopicReviewPost.number > after)
                                  .order_by(TopicReviewPost.number).limit(20)))
            posts = [vault.open(row.content_cipher) for row in rows]
        if not posts:
            return
        yield posts
        after = rows[-1].number


def chunks(db, vault, job_id):
    buffer, size = [], 0
    for posts in post_pages(db, vault, job_id):
        for post in posts:
            for fragment in split_text(post['text'], 10000):
                piece = {key: post.get(key) for key in ('id', 'number', 'username', 'user_id', 'created', 'reply_to')}
                piece['text'] = fragment
                length = len(json.dumps(piece, ensure_ascii=False).encode())
                if buffer and size + length > 16000:
                    yield buffer
                    buffer, size = [], 0
                buffer.append(piece); size += length
    if buffer:
        yield buffer


async def process_review(db, vault, models, job_id, forum_factory=None):
    forum = None
    try:
        with db.transaction() as s:
            job = s.get(TopicReview, job_id)
            valid_job(s, job)
            conf = dict(job.config)
            plan = vault.open(job.checkpoint_cipher)
            connection = vault.open(s.get(KV, 'connection:forum').data['cipher'])
            snapshot = s.get(Snapshot, conf['snapshot_id']).data if conf['snapshot_id'] else None
            routes = {key: vault.open(s.get(KV, 'connection:' + key).data['cipher']) for key in conf['versions'] if key != 'forum'}
        forum = (forum_factory or Discourse)(connection)
        await forum.login()

        def checkpoint():
            with db.transaction() as s:
                locked(s, 'topic_review_lock')
                row = s.get(TopicReview, job_id)
                if row.state != 'running':
                    return False
                valid_job(s, row)
                row.config, row.checkpoint_cipher = dict(conf), vault.seal(plan)
            return True

        async def ensure_public():
            topic = await public_topic(forum, conf['topic_id'])
            if not set(plan['ids']) <= set(topic.get('post_stream', {}).get('stream', [])):
                raise ReviewError('主题楼层清单发生删除或权限变化，请重新读取')
            pin = conf.get('topic_pipeline')
            if pin:
                first = next((p for p in topic.get('context', []) if p.get('number') == 1), {})
                current = verified_pin(db, topic, first, conf['site'])
                if not current or current['id'] != pin['id'] or current['revision'] != pin['revision']:
                    raise ReviewError('个人贴作者或专属编排已变化')

        async def ensure_sources():
            await ensure_public()
            for page in post_pages(db, vault, job_id):
                ids = [p['id'] for p in page]
                current = await forum.full_posts(conf['topic_id'], ids)
                if not set(ids) <= {p['id'] for p in current}:
                    raise ReviewError('已读取楼层被隐藏或撤回，请重新读取主题')
                if not checkpoint(): return False
            return True

        await ensure_public()
        if conf['read_complete'] and not await ensure_sources(): return
        if not conf['read_complete']:
            conf['phase'] = 'reading'
            while conf['offset'] < len(plan['ids']):
                if not checkpoint(): return
                await ensure_public()
                selected = plan['ids'][conf['offset']:conf['offset'] + 20]
                posts = await forum.full_posts(conf['topic_id'], selected)
                ids = set()
                with db.transaction() as s:
                    locked(s, 'topic_review_lock')
                    row = s.get(TopicReview, job_id)
                    if row.state != 'running': return
                    valid_job(s, row)
                    for post in posts:
                        if (post.get('id') not in selected or post['id'] in ids or type(post.get('number')) is not int
                                or post['number'] <= 0 or not isinstance(post.get('text'), str)):
                            raise ReviewError('论坛返回的楼层不属于本次读取范围')
                        ids.add(post['id'])
                        # Keep complete quotes and Markdown, but never export verification credentials.
                        post = {**post, 'text': private_identity_text(post['text'])}
                        conf['bytes'] += len(post['text'].encode())
                        if conf['bytes'] > MAX_BYTES:
                            raise ReviewError('主题超过16 MiB处理上限，本次未截断为完整导出')
                        conf['plain_text_posts'] += post.get('body_format') == 'plain_text'
                        s.add(TopicReviewPost(job_id=job_id, post_id=post['id'], number=post['number'], content_cipher=vault.seal(post)))
                    conf['offset'] += len(selected)
                    conf['posts'] += len(posts)
                    conf['excluded'] += len(selected) - len(posts)
                    row.config = dict(conf)
            conf['read_complete'] = True
            if not checkpoint(): return
        if conf['mode'] == 'export':
            await ensure_public()
            with db.transaction() as s:
                locked(s, 'topic_review_lock')
                row = s.get(TopicReview, job_id)
                if row.state != 'running': return
                valid_job(s, row)
                row.state, row.config = 'completed', {**conf, 'phase': 'done'}
            return
        if not conf['posts']:
            raise ReviewError('没有可读取的普通发言')
        snapshot = overlay(snapshot, conf.get('topic_pipeline'))
        work = conf['work']
        persona = '\n\n'.join(snapshot['modules'][mid]['content'] for mid in snapshot['pipeline']['replyer']
                              if snapshot['modules'][mid].get('persona') or mid in conf.get('persona_ids', []))

        async def analyse(input_data, allowed, final=False):
            if not checkpoint(): return None
            await ensure_public()
            if final and not await ensure_sources(): return None
            if 'posts' in input_data:
                current_ids = set()
                selected = sorted(allowed)
                for offset in range(0, len(selected), 20):
                    current_ids.update(p['id'] for p in await forum.full_posts(conf['topic_id'], selected[offset:offset + 20]))
                if not allowed <= current_ids:
                    raise ReviewError('待总结楼层被隐藏或撤回，请重新读取主题')
            with db.transaction() as s:
                valid_job(s, s.get(TopicReview, job_id))
                current = s.get(Snapshot, s.get(KV, 'control').data['active_snapshot'])
                policy = Policy.model_validate(snapshot['policy'])
                if current:
                    policy.daily_tokens = min(policy.daily_tokens, Policy.model_validate(current.data['policy']).daily_tokens)
            route = 'replyer' if final else 'summary'
            brief = ('根据全部分段结果写整帖总结并点评，summary建议2000字以内、commentary建议1000字以内。' if final else
                     '这是整帖的片段或中间笔记，只总结当前资料，不宣称已看完全文。summary最多800字，commentary最多300字。')
            with db.transaction() as s:
                sources = [{'id': p.post_id, 'number': p.number} for p in s.scalars(select(TopicReviewPost)
                           .where(TopicReviewPost.job_id == job_id, TopicReviewPost.post_id.in_(allowed)))]
            messages = [{'role': 'system', 'content': (persona if final else '') + '\n\n' + work['summary'] + '\n\n' + work['review'] + '\n\n' + PROTOCOL + '\n' + brief},
                        {'role': 'user', 'content': json.dumps({'title': conf['title'], 'focus': conf['focus'], 'data': input_data,
                                                               'source_post_ids': sorted(allowed), 'source_posts': sources}, ensure_ascii=False)}]
            if not checkpoint(): return None
            result = ReviewResult.model_validate(json_output(await models.complete(route, messages, 0, policy,
                         task_id=job_id, task_limit=conf['max_tokens'], output_limit=min(routes[route]['max_output'], 6000 if final else 2000), compact_json=True)))
            if any(type(i) is not int or i not in allowed for i in result.references):
                raise ReviewError('模型引用不属于实际读取来源')
            return result.model_dump()

        conf['phase'] = 'analysing'
        for index, buffer in enumerate(chunks(db, vault, job_id)):
            with db.transaction() as s:
                exists = s.scalar(select(TopicReviewPart.id).where(TopicReviewPart.job_id == job_id, TopicReviewPart.level == 0,
                                                                 TopicReviewPart.sequence == index))
            if exists:
                continue
            output = await analyse({'posts': buffer}, {p['id'] for p in buffer})
            if output is None: return
            with db.transaction() as s:
                locked(s, 'topic_review_lock')
                row = s.get(TopicReview, job_id)
                if row.state != 'running': return
                valid_job(s, row)
                s.add(TopicReviewPart(job_id=job_id, level=0, sequence=index, content_cipher=vault.seal(output)))
                conf['parts_done'] += 1; row.config = dict(conf)
        level = 0
        while True:
            with db.transaction() as s:
                count = s.scalar(select(func.count()).select_from(TopicReviewPart).where(TopicReviewPart.job_id == job_id,
                                                                                       TopicReviewPart.level == level))
            if not count:
                raise ReviewError('没有可整理的正文片段')
            conf['phase'], conf['merge_level'] = 'merging', level
            final = count <= 4
            for offset in range(0, count, 4):
                with db.transaction() as s:
                    exists = s.scalar(select(TopicReviewPart).where(TopicReviewPart.job_id == job_id, TopicReviewPart.level == level + 1,
                                                                    TopicReviewPart.sequence == offset // 4))
                    rows = list(s.scalars(select(TopicReviewPart).where(TopicReviewPart.job_id == job_id, TopicReviewPart.level == level)
                                          .order_by(TopicReviewPart.sequence).offset(offset).limit(4)))
                    notes = [vault.open(part.content_cipher) for part in rows]
                output = vault.open(exists.content_cipher) if exists else await analyse({'notes': notes}, {i for note in notes for i in note['references']}, final)
                if output is None: return
                with db.transaction() as s:
                    locked(s, 'topic_review_lock')
                    row = s.get(TopicReview, job_id)
                    if row.state != 'running': return
                    valid_job(s, row)
                    if not exists:
                        s.add(TopicReviewPart(job_id=job_id, level=level + 1, sequence=offset // 4, content_cipher=vault.seal(output)))
                    row.config = dict(conf)
                    if final:
                        sources = []
                        for post in s.scalars(select(TopicReviewPost).where(TopicReviewPost.job_id == job_id, TopicReviewPost.post_id.in_(output['references']))
                                              .order_by(TopicReviewPost.number)):
                            data = vault.open(post.content_cipher)
                            sources.append({'post_id': post.post_id, 'number': post.number, 'username': data.get('username', ''),
                                            'url': conf['site'] + f'/t/{conf["topic_id"]}/{post.number}'})
                        row.result_cipher = vault.seal({**output, 'sources': sources})
                        row.state, row.config = 'completed', {**conf, 'phase': 'done'}
            if final: return
            level += 1
    except asyncio.CancelledError:
        with db.transaction() as s:
            row = s.get(TopicReview, job_id)
            if row.state == 'running': row.state, row.reason = 'interrupted', '处理已中断；不会自动重试模型调用'
        raise
    except Exception as exc:
        with db.transaction() as s:
            row = s.get(TopicReview, job_id)
            if row.state == 'running':
                reason = ('模型输出截断或为空；已计用量，未保存不完整结果' if isinstance(exc, ModelOutputError) else
                          '模型预算不足；已完成片段保留，可手动继续' if isinstance(exc, BudgetExceeded) else
                          str(exc) if isinstance(exc, ReviewError) else
                          '读取或处理失败：' + type(exc).__name__)
                row.state, row.reason = 'failed', reason[:300]
    finally:
        if forum: await forum.close()


def mount_topic_reviews(app, db, vault, user):
    async def live_scope(job_id, account):
        with db.transaction() as s:
            row = owned(s, job_id, account.id)
            try: valid_job(s, row, models=False)
            except ReviewError as exc: raise HTTPException(409, str(exc))
            conf = dict(row.config)
            plan = vault.open(row.checkpoint_cipher)
            connection = vault.open(s.get(KV, 'connection:forum').data['cipher'])
        forum = Discourse(connection)
        try:
            async with asyncio.timeout(60):
                await forum.login()
                topic = await public_topic(forum, row.topic_id)
                if not set(plan['ids']) <= set(topic.get('post_stream', {}).get('stream', [])):
                    raise ReviewError('主题来源已发生删除或权限变化，请重新读取')
                if row.result_cipher:
                    for page in post_pages(db, vault, job_id):
                        refs = [p['id'] for p in page]
                        if not set(refs) <= {p['id'] for p in await forum.full_posts(row.topic_id, refs)}:
                            raise ReviewError('总结来源楼层被隐藏或撤回，请重新读取')
            return conf
        except ReviewError as exc: raise HTTPException(409, str(exc))
        except Exception: raise HTTPException(502, '暂时无法复核主题公开性，请稍后重试')
        finally: await forum.close()

    @app.get('/api/topic-reviews')
    def listing(account=Depends(user)):
        with db.transaction() as s:
            return [view(s, vault, row) for row in s.scalars(select(TopicReview).where(TopicReview.owner == account.id,
                         TopicReview.expires > now()).order_by(TopicReview.created.desc()).limit(30))]

    @app.post('/api/topic-reviews', status_code=201)
    async def create(body: StartReview, account=Depends(user)):
        if identity_message(body.focus): raise HTTPException(422, '重点要求不能包含账户验证资料')
        with db.transaction() as s:
            row = s.get(KV, 'connection:forum')
            if not row: raise HTTPException(409, '论坛尚未配置')
            connection = vault.open(row.data['cipher'])
            uid = identity(s, account, connection['base_url'])
            versions = {'forum': row.version}
            snapshot_id = s.get(KV, 'control').data['active_snapshot']
            if body.mode == 'review':
                if not snapshot_id: raise HTTPException(409, '请先由管理员发布工作配置')
                for route in ('summary', 'replyer'):
                    route_row = s.get(KV, 'connection:' + route)
                    if not route_row: raise HTTPException(409, '请先配置摘要与回复模型')
                    versions[route] = route_row.version
            snapshot = s.get(Snapshot, snapshot_id).data if snapshot_id else {}
            prompts = s.get(KV, 'topic_review_settings').data
            work = {key: snapshot.get('modules', {}).get(prompts[key + '_module'], {}).get('content', fallback)
                    for key, fallback in [('summary', SUMMARY), ('review', REVIEW)]}
            persona_ids = s.get(KV, 'prompt_refresh:20261004')
            persona_ids = persona_ids.data.get('persona_ids', []) if persona_ids else []
        tid = topic_number(body.topic, connection['base_url'])
        forum = Discourse(connection)
        try:
            async with asyncio.timeout(60):
                await forum.login()
                topic = await public_topic(forum, tid)
            ids = list(dict.fromkeys(topic.get('post_stream', {}).get('stream', [])))
            if not ids or len(ids) > MAX_POSTS or any(type(i) is not int or i <= 0 for i in ids):
                raise ReviewError('楼层清单为空或超过100,000楼处理上限')
            first = next((p for p in topic.get('context', []) if p.get('number') == 1), {})
            pin = verified_pin(db, topic, first, connection['base_url']) if body.mode == 'review' else None
        except ReviewError as exc: raise HTTPException(422, str(exc))
        except Exception: raise HTTPException(502, '暂时无法读取主题，请稍后重试')
        finally: await forum.close()
        with db.transaction() as s:
            locked(s, 'topic_review_lock')
            active = list(s.scalars(select(TopicReview).where(TopicReview.state.in_(ACTIVE), TopicReview.expires > now())))
            if any(row.owner == account.id for row in active) or len(active) >= 2:
                raise HTTPException(409, '已有整帖任务处理中，请等待或停止它')
            if s.scalar(select(func.count()).select_from(TopicReview).where(TopicReview.owner == account.id, TopicReview.expires > now())) >= 30:
                raise HTTPException(422, '七天内最多保留30个整帖任务，请稍后再试')
            conf = dict(site=connection['base_url'], user_id=uid, versions=versions, snapshot_id=snapshot_id,
                        mode=body.mode, focus=body.focus, max_tokens=body.max_tokens, topic_id=tid,
                        title=private_identity_text(topic.get('title', ''))[:500], phase='reading', total=len(ids),
                        offset=0, posts=0, excluded=0, bytes=0, plain_text_posts=0, read_complete=False,
                        parts_done=0, merge_level=0, work=work, persona_ids=persona_ids, topic_pipeline=pin)
            job = TopicReview(owner=account.id, topic_id=tid, config=conf, expires=now() + 7 * 86400,
                              checkpoint_cipher=vault.seal({'ids': ids}))
            valid_job(s, job)
            s.add(job); s.flush()
            audit(s, account.id, 'topic_review_created', job.id, topic_id=tid, mode=body.mode)
            return view(s, vault, job)

    @app.get('/api/topic-reviews/{job_id}')
    async def detail(job_id: str, account=Depends(user)):
        with db.transaction() as s:
            row = owned(s, job_id, account.id)
            has_result = bool(row.result_cipher)
        if has_result: await live_scope(job_id, account)
        with db.transaction() as s:
            return view(s, vault, owned(s, job_id, account.id), result=True)

    @app.post('/api/topic-reviews/{job_id}/cancel')
    def cancel(job_id: str, account=Depends(user)):
        with db.transaction() as s:
            locked(s, 'topic_review_lock')
            row = owned(s, job_id, account.id)
            if row.state in ACTIVE: row.state, row.reason = 'cancelled', '已停止；在途调用仍按实际用量计费'
            return view(s, vault, row)

    @app.post('/api/topic-reviews/{job_id}/resume')
    async def resume(job_id: str, account=Depends(user)):
        await live_scope(job_id, account)
        with db.transaction() as s:
            locked(s, 'topic_review_lock')
            row = owned(s, job_id, account.id)
            if row.state not in ('failed', 'cancelled', 'interrupted'): raise HTTPException(409, '任务无需继续')
            try: valid_job(s, row)
            except ReviewError as exc: raise HTTPException(409, str(exc))
            active = list(s.scalars(select(TopicReview).where(TopicReview.state.in_(ACTIVE), TopicReview.expires > now())))
            if len(active) >= 2 or any(other.owner == account.id for other in active): raise HTTPException(409, '已有任务处理中')
            row.state, row.reason = 'queued', ''
            audit(s, account.id, 'topic_review_resumed', row.id)
            return view(s, vault, row)

    @app.get('/api/topic-reviews/{job_id}/export')
    async def export(job_id: str, request: Request, format: Literal['markdown', 'json'] = Query('markdown'), account=Depends(user)):
        conf = await live_scope(job_id, account)
        if not conf['read_complete']: raise HTTPException(409, '全文读取尚未完成，不能导出为完整主题')
        async def content():
            with db.transaction() as s:
                connection = vault.open(s.get(KV, 'connection:forum').data['cipher'])
            forum = Discourse(connection)
            try:
                await forum.login()
                async for fragment in stream(forum): yield fragment
            finally:
                await forum.close()

        async def stream(forum):
            if format == 'json': yield '{"topic":' + json.dumps({k: conf[k] for k in ('title', 'site', 'topic_id', 'posts', 'excluded')}, ensure_ascii=False) + ',"posts":['
            else:
                yield f'# {conf["title"]}\n\n来源：{conf["site"]}/t/{conf["topic_id"]}\n\n'
                yield f'可读取普通发言 {conf["posts"]} 条；隐藏、删除或系统楼层等未返回内容 {conf["excluded"]} 条。附件保留引用，不下载二进制。\n\n'
            first = True
            for posts in post_pages(db, vault, job_id):
                with db.transaction() as s:
                    live = s.get(LoginSession, request.state.session_id)
                    owner = s.get(Account, account.id)
                    if not live or live.expires <= now() or not owner or not owner.active: return
                    try: valid_job(s, owned(s, job_id, account.id), models=False)
                    except (ReviewError, HTTPException): return
                try:
                    await public_topic(forum, conf['topic_id'])
                    ids = {p['id'] for p in posts}
                    current = await forum.full_posts(conf['topic_id'], sorted(ids))
                    if not ids <= {p['id'] for p in current}: return
                except Exception:
                    return
                for post in posts:
                    url = conf['site'] + f'/t/{conf["topic_id"]}/{post["number"]}'
                    if format == 'json': yield ('' if first else ',') + json.dumps({**post, 'url': url}, ensure_ascii=False)
                    else: yield f'## #{post["number"]} · @{post.get("username", "")}\n\n{post.get("created") or "时间未知"} · {url}\n\n{post["text"]}\n\n---\n\n'
                    first = False
                await asyncio.sleep(0)
            yield '],"complete":true}' if format == 'json' else f'导出结束，共 {conf["posts"]} 条普通发言。\n'
        return StreamingResponse(content(), media_type='application/json' if format == 'json' else 'text/markdown; charset=utf-8',
                   headers={'Content-Disposition': f'attachment; filename="topic-{conf["topic_id"]}.{ "json" if format == "json" else "md"}"',
                            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Accel-Buffering': 'no'})


def interrupt_reviews(db):
    with db.transaction() as s:
        locked(s, 'topic_review_lock')
        for row in s.scalars(select(TopicReview).where(TopicReview.state.in_(ACTIVE))):
            row.state, row.reason = 'interrupted', 'worker重启；请手动继续，不自动重新调用模型'


def purge_expired(db):
    with db.transaction() as s:
        locked(s, 'topic_review_lock')
        expired = list(s.scalars(select(TopicReview.id).where(TopicReview.expires <= now())))
        if expired:
            s.execute(delete(TopicReviewPost).where(TopicReviewPost.job_id.in_(expired)))
            s.execute(delete(TopicReviewPart).where(TopicReviewPart.job_id.in_(expired)))
            s.execute(delete(TopicReview).where(TopicReview.id.in_(expired)))
