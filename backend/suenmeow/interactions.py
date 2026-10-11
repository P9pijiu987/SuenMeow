"""Versioned, single-conversation interaction batches; models never call forum writes."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy import func, select

from .adapters import Discourse, LoginRequired, json_output, safe_post, timestamp, source_text
from .database import Account, ForumIdentity, Event, KV, Observation, Reaction, Record, Reply, Snapshot, Usage, audit, day, locked, now
from .domain import AgentPolicy, Policy, Strict
from .service import BudgetExceeded, get_snapshot, quiet
from .topic_pipeline import overlay, pin_valid, resolve


class PostDecision(Strict):
    model_config = ConfigDict(extra='forbid', strict=True)
    post_id: int = Field(gt=0)
    reaction: str = Field(default='', max_length=100)
    score: float = Field(default=0, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=300)


class BatchReply(Strict):
    model_config = ConfigDict(extra='forbid', strict=True)
    post_id: int = Field(default=0, ge=0)
    text: str = Field(default='', max_length=2000)
    reason: str = Field(min_length=1, max_length=300)
    research: bool = False
    long: bool = False


class BatchDecision(Strict):
    posts: list[PostDecision] = Field(min_length=1, max_length=20)
    reply: BatchReply


class ReactionDecision(Strict):
    action: str = Field(pattern=r'^(approve|reject|resolve_sent|resolve_unsent)$')


def signature(topic):
    return hashlib.sha256(json.dumps({'private': topic.get('archetype') == 'private_message',
        'users': sorted(int(u['id']) for u in topic.get('allowed_users', [])),
        'groups': sorted(int(g['id']) for g in topic.get('allowed_groups', []))}, sort_keys=True).encode()).hexdigest()


def digest_post(post):
    return hashlib.sha256(json.dumps({k: post.get(k) for k in ('id', 'user_id', 'username', 'number', 'text', 'reply_to', 'reply_to_username')},
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reply_allowance(s, topic_id, user_id, ip, at=None, exclude_id=''):
    at = now() if at is None else at
    rows = list(s.execute(select(Reply, Event, Snapshot).join(Event, Reply.event_id == Event.id)
        .join(Snapshot, Event.snapshot_id == Snapshot.id).where(
        Reply.created > at - 86400, Reply.state.in_(['approval', 'ready', 'sending', 'sent', 'unknown']),
        Event.receipt.like('interaction:%'), Reply.id != exclude_id)))
    # Introduce quotas at the new policy's cutover. Historical replies from the
    # uncapped policy must not retrospectively exhaust the entire rollout window.
    # Every subsequent publication serializes these fields, so edits cannot reset quotas.
    rows = [(reply, event) for reply, event, snapshot in rows
            if 'reply_daily' in (snapshot.data.get('policy', {}).get('interaction') or {})]
    if len(rows) >= ip.reply_daily: return '每日文字参与上限'
    if sum(r.created > at - 3600 and e.topic_id == topic_id for r, e in rows) >= ip.reply_topic_hourly: return '同帖每小时文字上限'
    if sum(r.created > at - 3600 and e.data.get('reply_user_id') == user_id for r, e in rows) >= ip.reply_user_hourly: return '同用户每小时文字上限'
    return ''


def invited(post, context, bot_username, private=False):
    own = post.get('author_text', post.get('text', ''))
    mention = bool(re.search(r'(?<![\w@])@' + re.escape(bot_username) + r'(?![\w.-])', own, re.I))
    parent = next((p for p in context if p['number'] == post.get('reply_to')), None)
    addressed = post.get('reply_to_username', '').casefold() == bot_username.casefold()
    addressed = addressed or bool(parent and parent['username'].casefold() == bot_username.casefold())
    return private or mention or addressed


def whitelist(topic):
    values = topic.get('valid_reactions')
    return sorted(set(x for x in values if isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_+:-]{1,100}', x))) if isinstance(values, list) else []


def select_reactions(s, observations, decisions, policy):
    """Stable sampling plus a rolling success/reservation cap; never invent a suggestion."""
    window = list(s.scalars(select(Observation).where(Observation.created > now() - 86400)
                  .where(Observation.epoch == observations[0].epoch)
                  .order_by(Observation.created.desc(), Observation.id.desc()).limit(policy.reaction_window * 3)))
    window = [o for o in window if o.data.get('eligible')][:policy.reaction_window]
    ids = [o.id for o in window]
    reserved = s.scalar(select(func.count()).select_from(Reaction).where(
        Reaction.observation_id.in_(ids), Reaction.state.in_(['approval', 'ready', 'sending', 'sent', 'unknown'])))
    allowance = max(0, math.ceil(len(window) * policy.reaction_percent / 100) - reserved)
    suitable = sum(bool(o.data.get('suggested')) for o in window)
    probability = min(1, policy.reaction_percent / 100 * len(window) / max(1, suitable))
    proposals = []
    for o, d in zip(observations, decisions):
        seed = int(hashlib.sha256(o.receipt.encode()).hexdigest()[:16], 16) / 2**64
        if d.reaction and d.score >= .5 and seed < probability:
            proposals.append((o, d, seed))
    return {o.id for o, _, _ in sorted(proposals, key=lambda v: (-v[1].score, v[2]))[:allowance]}


def reaction_reason(s, reaction, timestamp):
    control, current = get_snapshot(s)
    worker = s.get(KV, 'worker').data
    event, observation = s.get(Event, reaction.event_id), s.get(Observation, reaction.observation_id)
    if control['mode'] not in ('approval', 'auto'):
        return '运行模式禁止发送'
    if worker.get('status') != 'online' or worker.get('baseline_epoch') != control['epoch'] or timestamp - worker.get('heartbeat', 0) > 60:
        return '等待有效的新水位或 worker 心跳'
    if reaction.state != 'ready' or event.epoch != control['epoch'] or event.expires <= timestamp:
        return '动作已过期或不可发送'
    if not pin_valid(s, event.data.get('topic_pipeline')):
        return '个人贴编排已变化'
    connection = s.get(KV, 'connection:forum')
    if not connection or connection.version != event.data.get('forum_version'):
        return '论坛连接已变化'
    for snapshot in (s.get(Snapshot, event.snapshot_id), current):
        p = Policy.model_validate(snapshot.data['policy'])
        ip = p.interaction
        if not ip or not ip.reactions or ip.reaction_percent == 0:
            return '表情互动已关闭'
        if reaction.topic_id in p.muted_topics or observation.username.casefold() in {x.casefold() for x in p.muted_users} or quiet(p, timestamp):
            return '静音或安静时段'
        gate = s.get(KV, 'gate').data
        if timestamp - gate.get('last_write', gate.get('last_send', 0)) < ip.write_interval:
            return '共享写入间隔中'
        counted = select(func.count()).select_from(Reaction).where(Reaction.claimed > 0,
                    Reaction.state.in_(['sending', 'sent', 'unknown']))
        if s.scalar(counted.where(Reaction.claimed > timestamp - 86400)) >= ip.reaction_daily:
            return '每日表情限额'
        if s.scalar(counted.where(Reaction.claimed > timestamp - 3600, Reaction.topic_id == reaction.topic_id)) >= ip.reaction_topic_hourly:
            return '主题每小时表情限额'
        if s.scalar(counted.where(Reaction.claimed > timestamp - 3600, Reaction.user_id == reaction.user_id)) >= ip.reaction_user_hourly:
            return '用户每小时表情限额'
    return ''


def claim_reaction(db, rid, timestamp=None):
    timestamp = now() if timestamp is None else timestamp
    with db.transaction() as s:
        locked(s, 'control')
        gate = locked(s, 'gate')
        r = s.scalar(select(Reaction).where(Reaction.id == rid).with_for_update())
        if not r: return False
        reason = reaction_reason(s, r, timestamp)
        if reason:
            r.reason = reason
            if '过期' in reason or '变化' in reason or '关闭' in reason: r.state = 'expired'
            return False
        r.state, r.claimed, r.reason = 'sending', timestamp, '已预留写入；中断后不重试'
        gate.data = {**gate.data, 'last_write': timestamp}
        audit(s, 'worker', 'reaction_claim', r.id, post_id=r.post_id, emoji=r.emoji)
        return True


class InteractionEngine:
    def __init__(self, worker):
        self.w = worker
        self.cursor = 0
        self.poll_at = 0
        self.pm_cursors = {}

    async def baseline(self):
        if type(getattr(self.w.forum, 'bot_id', None)) is not int or self.w.forum.bot_id <= 0:
            raise ValueError('新版互动需要已核验的机器人用户 ID')
        posts = await self.w.forum.latest_posts()
        self.cursor = max([int(p['id']) for p in posts] or [0])
        self.pm_cursors = {}
        for notice in await self.w.forum.notifications():
            if notice.get('notification_type') == 6 and notice.get('topic_id'):
                topic = await self.w.forum.topic(int(notice['topic_id']), 1)
                self.pm_cursors[int(notice['topic_id'])] = max(topic.get('post_stream', {}).get('stream', []) or [0])
        with self.w.db.transaction() as s:
            for r in s.scalars(select(Reaction).where(Reaction.state.in_(['ready', 'approval', 'sending']))):
                r.state = 'unknown' if r.state == 'sending' else 'expired'
                r.reason = '重启/重新建立水位；不会补发或重复 toggle'
            for o in s.scalars(select(Observation).where(Observation.state.in_(['pending', 'processing']))):
                o.state, o.reason = 'expired', '重新建立水位，跳过旧消息'
            audit(s, 'worker', 'interaction_baseline', str(self.w.epoch), post_watermark=self.cursor)

    async def collect(self, control, snapshot):
        w = self.w
        p = Policy.model_validate(snapshot.data['policy'])
        ip = p.interaction
        if now() - self.poll_at < p.notification_interval: return
        self.poll_at = now()
        first = await w.forum.latest_posts()
        posts = list(first)
        # Bounded descending pagination. A gap is reported, never disguised as complete coverage.
        for _ in range(4):
            if not posts or min(int(x['id']) for x in posts) <= self.cursor: break
            page = await w.forum.latest_posts(min(int(x['id']) for x in posts))
            if not page: break
            if min(int(x['id']) for x in page) >= min(int(x['id']) for x in posts): break
            posts.extend(page)
        if posts and min(int(x['id']) for x in posts) > self.cursor and self.cursor:
            with w.db.transaction() as s:
                audit(s, 'worker', 'interaction_gap', str(w.epoch), reason='超过五页读取上限，未声称覆盖缺失消息')
        newer = {int(x['id']): x for x in posts if int(x['id']) > self.cursor}
        self.cursor = max([self.cursor, *newer])
        notifications = await w.forum.notifications() if p.notifications else []
        notified = set()
        for n in notifications:
            nid = int(n['id'])
            if nid > w.notification_watermark and n.get('notification_type') in (1, 2, 3, 6, 12) and n.get('topic_id'):
                notified.add(int(n['topic_id']))
            w.notification_watermark = max(w.notification_watermark, nid)
        topics = {int(x['topic_id']) for x in newer.values() if x.get('topic_id')} | notified
        for tid in sorted(topics):
            try:
                topic = await w.forum.topic(tid, ip.context_posts)
                private = not await w.forum.public_visible(topic)
                raw_candidates = [x for x in newer.values() if int(x.get('topic_id', 0)) == tid]
                candidates = []
                for offset in range(0, len(raw_candidates), 20):
                    candidates.extend(await w.forum.selected_posts(tid, [x['id'] for x in raw_candidates[offset:offset + 20]]))
                fetched = {p['id'] for p in candidates}
                candidates.extend({**safe_post({**p, 'raw': '', 'cooked': ''}), 'unread_source': True}
                                  for p in raw_candidates if p['id'] not in fetched)
                if tid in notified:
                    if topic.get('archetype') == 'private_message':
                        stream = topic.get('post_stream', {}).get('stream', [])
                        unseen = [pid for pid in stream if pid > self.pm_cursors.get(tid, 0)]
                        if len(unseen) > 100:
                            with w.db.transaction() as s: audit(s, 'worker', 'interaction_pm_gap', str(tid), unexamined=len(unseen) - 100)
                        for offset in range(0, len(unseen[-100:]), 20):
                            candidates.extend(await w.forum.selected_posts(tid, unseen[-100:][offset:offset + 20]))
                        self.pm_cursors[tid] = max(stream or [0])
                    else:
                        candidates.extend(topic['context'])
                seen = set()
                for post in sorted(candidates, key=lambda x: x['id']):
                    if post['id'] in seen: continue
                    seen.add(post['id'])
                    created = timestamp(post.get('created'))
                    if not created or created <= w.baseline_time or now() - created > p.event_ttl: continue
                    raw = newer.get(post['id'], {})
                    reason = ''
                    if post.get('username', '').casefold() == w.forum.connection['username'].casefold(): reason = '自己的发言'
                    elif (raw.get('user') or {}).get('bot'): reason = '已标识的机器人发言'
                    elif raw and (raw.get('post_type') != 1 or raw.get('hidden') or raw.get('deleted_at')): reason = '系统、隐藏或删除消息'
                    elif post.get('unread_source'): reason = '无法核验来源，未评估'
                    elif any(x.get('identity_message') for x in topic['context']) or post.get('identity_message'): reason = '身份验证对话隔离'
                    elif topic.get('closed') or topic.get('archived'): reason = '主题关闭或归档'
                    elif not post.get('user_id') or not post.get('number'): reason = '作者或楼层未核验'
                    elif tid in p.muted_topics or post['username'].casefold() in {x.casefold() for x in p.muted_users} or quiet(p, now()): reason = '静音或安静时段'
                    elif ip.categories and topic.get('category_id') not in ip.categories: reason = '观察分类之外'
                    elif ip.topics and tid not in ip.topics: reason = '观察主题之外'
                    elif private and tid not in notified: reason = '私密对话只处理明确的新通知'
                    receipt = hashlib.sha256(w.forum.connection['base_url'].encode()).hexdigest()[:24] + ':' + str(post['id'])
                    with w.db.transaction() as s:
                        current = locked(s, 'control').data
                        if current['epoch'] != w.epoch: return
                        if s.scalar(select(Observation.id).where(Observation.receipt == receipt)): continue
                        connection = s.get(KV, 'connection:forum')
                        observed_only = current['mode'] in ('paused', 'read_only')
                        s.add(Observation(receipt=receipt, topic_id=tid, post_id=post['id'], user_id=post.get('user_id') or 0,
                            username=post['username'], number=post['number'], epoch=w.epoch, expires=created + p.event_ttl,
                            state='skipped' if reason else 'observed' if observed_only else 'pending',
                            reason=reason or ('只读观察，不调用模型' if observed_only else ''),
                            data={'eligible': not reason, 'private': private, 'participants': signature(topic),
                                  'forum_version': connection.version if connection else 0,
                                  'bot_id': getattr(w.forum, 'bot_id', 0),
                                  'source': w.vault.seal(post), 'prompt_source': tid in notified,
                                  'high': max([x['number'] for x in topic['context']] or [post['number']])}))
            except Exception as exc:
                with w.db.transaction() as s:
                    audit(s, 'worker', 'interaction_unread', str(tid), reason=type(exc).__name__)
                if isinstance(exc, LoginRequired): raise
        w.last_success = now()

    async def prepare_one(self):
        w = self.w
        with w.db.transaction() as s:
            locked(s, 'control')
            control, snap = get_snapshot(s)
            if control['mode'] not in ('approval', 'auto'): return
            p = Policy.model_validate(snap.data['policy']); ip = p.interaction
            first = s.scalar(select(Observation).where(Observation.state == 'pending', Observation.epoch == w.epoch)
                             .order_by(Observation.created, Observation.id))
            if not first: return
            batch = list(s.scalars(select(Observation).where(Observation.state == 'pending', Observation.epoch == w.epoch,
                         Observation.topic_id == first.topic_id).order_by(Observation.created, Observation.id).limit(ip.max_batch)))
            batch = [o for o in batch if o.data['private'] == first.data['private'] and o.data['participants'] == first.data['participants']]
            wait = min(8, ip.merge_seconds) if first.data['prompt_source'] else ip.merge_seconds
            if now() - first.created < wait and len(batch) < ip.max_batch: return
            if first.expires <= now():
                first.state, first.reason = 'expired', '消息等待超时，未评估'; return
            locked(s, 'control')
            eid = Event(receipt='interaction:' + first.id, topic_id=first.topic_id, epoch=w.epoch, snapshot_id=snap.id,
                expires=min(o.expires for o in batch), state='processing', data={'source': 'interaction', 'private': first.data['private'],
                'forum_version': first.data['forum_version'], 'participants': first.data['participants'],
                'bot_id': first.data['bot_id'],
                'post_ids': [o.post_id for o in batch], 'high': max(o.data['high'] for o in batch)})
            s.add(eid); s.flush()
            for o in batch: o.state, o.event_id = 'processing', eid.id
            event_id, tid, snapshot = eid.id, first.topic_id, snap.data
        try:
            topic = await w.forum.topic(tid, ip.context_posts)
            if topic.get('id') != tid: raise ValueError('目标主题 ID 不匹配')
            private = not await w.forum.public_visible(topic)
            if private != first.data['private'] or signature(topic) != first.data['participants']:
                raise ValueError('对话可见性或参与者已变化')
            if topic.get('closed') or topic.get('archived') or any(x.get('identity_message') for x in topic['context']):
                raise ValueError('主题关闭或身份流程隔离')
            pin = await resolve(w.db, w.forum, tid, private)
            snapshot = overlay(snapshot, pin)
            posts = await w.forum.selected_posts(tid, [o.post_id for o in batch])
            source = {x['id']: x for x in posts}
            if set(source) != {o.post_id for o in batch} or any(digest_post(source[o.post_id]) != digest_post(w.vault.open(o.data['source'])) for o in batch):
                raise ValueError('来源已删除或修改')
            target = max(posts, key=lambda x: x['number'])
            memories = await w.checked_memories(tid, private, {o.username for o in batch}, {o.user_id for o in batch},
                                               query=target.get('text', '')[:1000])
            with w.db.transaction() as s:
                gate = s.get(KV, 'gate').data
            text_ready = now() - gate.get('last_send', 0) >= p.global_cooldown and now() - gate.get('topics', {}).get(str(tid), 0) >= p.topic_cooldown
            text_ready = text_ready and bool(topic['context']) and topic['context'][-1]['username'].casefold() != w.forum.connection['username'].casefold()
            reply_ids = []
            with w.db.transaction() as s:
                for o in batch:
                    post = source[o.post_id]
                    direct = invited(post, topic['context'], w.forum.connection['username'], private)
                    probability = ip.unmentioned_percent * (3 if ip.reply_inclination == 'chatty' else 1)
                    sample = int(hashlib.sha256(('reply:' + o.receipt).encode()).hexdigest()[:16], 16) / 2**64 * 100
                    # Notifications describe a topic, not an invitation to every author in the batch.
                    if not direct and (not ip.unmentioned or ip.reply_inclination == 'quiet' or sample >= probability): continue
                    if reply_allowance(s, tid, o.user_id, ip): continue
                    reply_ids.append(o.post_id)
            text_ready = text_ready and bool(reply_ids)
            allowed = whitelist(topic) if ip.reactions else []
            def compact(post, size):
                value = post.get('text', '')
                body = value.encode()[:size].decode('utf-8', 'ignore')
                return {k: post.get(k) for k in ('id', 'number', 'username', 'user_id', 'reply_to', 'reply_to_username')} | {'text': body, 'truncated': body != value}
            context = {'topic_id': tid, 'title': topic.get('title', '')[:200], 'private': private,
                'new_posts': [compact(source[o.post_id], min(2400, ip.input_bytes // (len(batch) + ip.context_posts + 2))) for o in batch],
                'context': [compact(x, 500) for x in topic['context']], 'memory': memories,
                'allowed_reactions': allowed, 'need_reply': text_ready,
                'allowed_reply_post_ids': reply_ids if text_ready else [],
                'reply_inclination': ip.reply_inclination, 'max_reply_chars': min(ip.ordinary_chars, p.max_reply_chars)}
            context['allow_extended_reply'] = ip.extended_replies
            context['automatic_memory'] = ip.automatic_memory
            from .worker import system_prompt, SYSTEM_RULES
            planning_rules = '\n'.join(snapshot['modules'][mid]['content'] for mid in snapshot['pipeline']['planner']
                                      if not snapshot['modules'][mid].get('persona') and mid not in snapshot['pipeline']['replyer'] and mid != ip.rule_module)
            speaking_rules = '\n'.join(snapshot['modules'][mid]['content'] for mid in snapshot['pipeline']['replyer'] if mid != ip.rule_module)
            system = planning_rules + '\n' + speaking_rules + '\n\n' + snapshot['modules'][ip.rule_module]['content']
            # The fixed protocol remains authoritative even when administrators edit the work rules.
            from .interaction_prompts import PROTOCOL
            system += '\n' + SYSTEM_RULES + '\n' + PROTOCOL
            messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]
            while len(json.dumps(context, ensure_ascii=False).encode()) > ip.input_bytes and (context['context'] or context['memory']):
                if context['context']: context['context'].pop(0)
                else: context['memory'].pop()
            messages[1]['content'] = json.dumps(context, ensure_ascii=False)
            if len(messages[1]['content'].encode()) > ip.input_bytes: raise ValueError('上下文超出输入上限，未调用模型')
            if hasattr(w.models, 'supports_vision') and w.models.supports_vision('planner'):
                images = await w.forum.image_parts(posts, private)
                if images:
                    messages[1]['content'] = [{'type': 'text', 'text': messages[1]['content']}, *images]
            raw = json_output(await w.models.complete('planner', messages, tid, p, task_id=event_id, output_limit=4000, compact_json=True))
            if not isinstance(raw, dict) or not {'posts', 'reply'} <= set(raw) or set(raw) - {'posts', 'reply', 'memories'}:
                raise ValueError('互动协议字段不匹配')
            if not isinstance(raw['posts'], list) or len(raw['posts']) > 20:
                raise ValueError('逐帖判断未完整覆盖或存在重复 ID')
            parsed, repaired = [], 0
            for item in raw['posts']:
                if not isinstance(item, dict):
                    raise ValueError('逐帖判断未完整覆盖或存在重复 ID')
                value = dict(item)
                pid = value.get('post_id')
                if isinstance(pid, str) and pid.isascii() and pid.isdecimal():
                    value['post_id'] = int(pid)
                if type(value.get('post_id')) is not int or value['post_id'] not in source:
                    raise ValueError('逐帖判断未完整覆盖或存在重复 ID')
                value['reason'] = str(value.get('reason') or '模型未说明具体理由')[:300]
                if value.get('reaction') is None: value['reaction'] = ''
                if isinstance(value.get('score'), str):
                    try: value['score'] = float(value['score'])
                    except ValueError: pass
                try:
                    parsed.append(PostDecision.model_validate(value))
                except ValidationError:
                    repaired += 1
                    parsed.append(PostDecision(post_id=value['post_id'], reason='此楼层表情字段不合法，忽略该项', reaction='', score=0))
            if repaired:
                with w.db.transaction() as s: audit(s, 'worker', 'interaction_items_rejected', event_id, count=repaired)
            decisions = {d.post_id: d for d in parsed}
            if len(decisions) != len(parsed) or set(decisions) != set(source): raise ValueError('逐帖判断未完整覆盖或存在重复 ID')
            for decision in parsed:
                if decision.reaction and decision.reaction not in allowed:
                    decision.reaction, decision.score, decision.reason = '', 0, '模型建议了站点未提供的表情，忽略该建议'
                if re.search(r'自杀|自殺|轻生|輕生|自伤|自傷|性侵|强奸|強姦|去世|严重受伤|嚴重受傷', source[decision.post_id].get('text', '')):
                    decision.reaction, decision.score, decision.reason = '', 0, '敏感求助或经历，不使用表情'
            try:
                value = dict(raw['reply']) if isinstance(raw['reply'], dict) else {}
                value['reason'] = str(value.get('reason') or '模型未说明接话理由')[:300]
                if isinstance(value.get('post_id'), str) and value['post_id'].isascii() and value['post_id'].isdecimal(): value['post_id'] = int(value['post_id'])
                if value.get('text') is None: value['text'] = ''
                reply = BatchReply.model_validate(value)
                if bool(reply.text.strip()) != bool(reply.post_id) or reply.post_id and reply.post_id not in reply_ids: raise ValueError('模型选择了不允许的回复楼层')
                if reply.text and (not text_ready or len(reply.text) > min(ip.ordinary_chars, p.max_reply_chars)): raise ValueError('正文不符合允许节奏或长度')
            except (ValueError, ValidationError):
                reply = BatchReply(reason='正文协议、目标或节奏不符合要求；保留独立表情判断')
            research_id = None
            with w.db.transaction() as s: ap = AgentPolicy.model_validate(s.get(KV, 'agent_policy').data)
            research = None
            from .memory_access import wants_all_memories
            explicit_memory = bool(reply.post_id and wants_all_memories(source[reply.post_id].get('text', '')))
            from .forum_links import requested_topics
            explicit_link = bool(reply.post_id and requested_topics(source[reply.post_id].get('text', ''), w.forum.connection['base_url']))
            extended = False
            try:
                if reply.post_id and text_ready and ap.enabled and ((reply.research and ap.auto_research) or explicit_memory or explicit_link):
                    research_id, research = await w.research_event(event_id, tid, private, snapshot, p, ap,
                                                                [source[reply.post_id]], explicit_memory=explicit_memory)
                if explicit_memory and not research:
                    reply.text = '这次还没能读完你的全部记忆，我不能把检索到的几条当成全部。'
                    reply.long = False
                extended = bool(reply.long and ip.extended_replies and reply.post_id)
                if (research or extended) and reply.post_id:
                    revised = await w.models.complete('replyer', [
                        {'role': 'system', 'content': system_prompt(snapshot, 'replyer') + '\n' + snapshot['modules'][ip.rule_module]['content']
                         + '\n依据提供的新发言与已核验研究来源回答。只输出一条正文，不声称已发送，不输出互动协议 JSON。'},
                        {'role': 'user', 'content': json.dumps({**context, 'max_reply_chars': p.max_reply_chars,
                                                              'target_post': compact(source[reply.post_id], 6000),
                                                              'instruction': '仅回复 target_post 的作者及问题；context 和其他新帖仅是背景，不能把另一作者的问题当成目标。',
                                                              'research': research}, ensure_ascii=False)}], tid, p, task_id=event_id)
                    if not revised.strip() or len(revised) > p.max_reply_chars:
                        reply = BatchReply(reason='扩展正文不完整或超过回复限制；保留独立表情判断')
                    else:
                        reply.text = revised.strip()
            except LoginRequired:
                raise
            except Exception as exc:
                reply = BatchReply(reason='研究或扩展正文未完成；保留独立表情判断')
                research_id = None
                with w.db.transaction() as s:
                    audit(s, 'worker', 'interaction_body_failed', event_id, error=type(exc).__name__)
            facts = []
            if ip.automatic_memory and isinstance(raw.get('memories'), list):
                try:
                    refreshed = {x['id']: x for x in await w.forum.selected_posts(tid, list(source))}
                    current_topic = await w.forum.topic(tid, 1)
                    same_scope = private == (not await w.forum.public_visible(current_topic)) and signature(current_topic) == first.data['participants']
                    for item in raw['memories'][:3]:
                        if not isinstance(item, dict): continue
                        pid, text, quote = item.get('source_post_id'), item.get('text'), item.get('quote')
                        post = refreshed.get(pid) if type(pid) is int else None
                        if not same_scope or not post or pid not in source or digest_post(post) != digest_post(source[pid]): continue
                        if post.get('identity_message') or post['username'].casefold() == w.forum.connection['username'].casefold(): continue
                        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 or not isinstance(quote, str) or not quote.strip(): continue
                        if quote not in source_text(post): continue
                        if re.search(r'密码|密碼|验证码|驗證碼|口令|api.?key|token|邮箱|電話|电话|住址|自杀|自殺|性侵|診斷|诊断|病历|病歷|账户余额|銀行|银行|身份证|身分證', text + quote, re.I): continue
                        facts.append({'text': text.strip(), 'quote': quote, 'username': post['username'], 'forum_user_id': post.get('user_id') or 0,
                                      'scope': 'private' if private else 'public', 'site': w.forum.connection['base_url'],
                                      'topic_id': tid, 'source_post_id': pid, 'origin': 'interaction_fact'})
                except Exception as exc:
                    with w.db.transaction() as s: audit(s, 'worker', 'interaction_memory_unverified', event_id, error=type(exc).__name__)
            with w.db.transaction() as s:
                current = locked(s, 'control').data
                event = s.get(Event, event_id)
                if current['epoch'] != event.epoch or current['mode'] not in ('approval', 'auto') or event.expires <= now():
                    raise ValueError('配置、水位或有效期已变化')
                rows = [s.get(Observation, o.id) for o in batch]
                for o in rows:
                    d = decisions[o.post_id]
                    o.state, o.reason = 'evaluated', d.reason
                    o.data = {**o.data, 'suggested': bool(d.reaction and d.score >= .5), 'emoji': d.reaction, 'score': d.score}
                s.flush()
                selected = select_reactions(s, rows, [decisions[o.post_id] for o in rows], ip) if allowed else set()
                state = 'ready' if current['mode'] == 'auto' else 'approval'
                for o in rows:
                    if o.id in selected:
                        s.add(Reaction(observation_id=o.id, event_id=event.id, topic_id=tid, post_id=o.post_id,
                                       user_id=o.user_id, emoji=decisions[o.post_id].reaction, state=state, reason=decisions[o.post_id].reason))
                    elif o.data.get('suggested'):
                        o.reason = (o.reason + ' · 未入选当前表情比例或表情已关闭')[:300]
                event.data = {**event.data, 'topic_pipeline': pin, 'allowed_reactions': allowed, 'memory_ids': [x['record_id'] for x in memories if x.get('record_id')],
                              'research_task': research_id,
                              'post_number': source[reply.post_id]['number'] if reply.post_id else 0,
                              'reply_user_id': source[reply.post_id]['user_id'] if reply.post_id else 0,
                              'username': source[reply.post_id]['username'] if reply.post_id else '', 'reply_reason': reply.reason}
                event.state, event.reason = 'drafted' if reply.text else 'evaluated', reply.reason
                if reply.text:
                    s.add(Reply(event_id=event.id, topic_id=tid, text_cipher=w.vault.seal(reply.text), state=state, memory_state='done', reason=reply.reason))
                audit(s, 'worker', 'interaction_evaluated', event.id, posts=len(rows), reactions=len(selected), reply=bool(reply.text),
                      ordinary=not bool(research or extended), research=bool(research_id))
            if facts:
                try:
                    with w.db.transaction() as s:
                        current = locked(s, 'control').data
                        event = s.get(Event, event_id)
                        if current['epoch'] != event.epoch or current['mode'] not in ('approval', 'auto'):
                            return
                        locked(s, 'memory_import_lock')
                        for fact in facts:
                            identity = s.scalar(select(ForumIdentity).where(ForumIdentity.site == fact['site'], ForumIdentity.user_id == fact['forum_user_id']))
                            owner = s.get(Account, identity.account_id) if identity else s.scalar(select(Account).where(Account.role == 'admin', Account.active.is_(True)))
                            if not owner: continue
                            existing = next((r for r in s.scalars(select(Record).where(Record.kind == 'memory', Record.owner == owner.id))
                                             if (old := w.vault.open(r.data['cipher'])).get('text') == fact['text'] and old.get('site') == fact['site'] and old.get('scope') == fact['scope']
                                             and (not private or old.get('topic_id') == tid)), None)
                            if not existing:
                                row = Record(kind='memory', owner=owner.id, title=f"{fact['username']} · 主题 {tid}", data={'cipher': w.vault.seal(fact)})
                                s.add(row); s.flush()
                                audit(s, 'worker', 'interaction_memory_saved', row.id, topic_id=tid, post_id=fact['source_post_id'])
                except Exception as exc:
                    with w.db.transaction() as s:
                        audit(s, 'worker', 'interaction_memory_failed', event_id, error=type(exc).__name__)
        except Exception as exc:
            with w.db.transaction() as s:
                event = s.get(Event, event_id)
                known = {'目标主题 ID 不匹配', '对话可见性或参与者已变化', '主题关闭或身份流程隔离', '来源已删除或修改',
                         '上下文超出输入上限，未调用模型', '互动协议字段不匹配', '逐帖判断未完整覆盖或存在重复 ID',
                         '扩展正文超过回复限制', '配置、水位或有效期已变化'}
                detail = str(exc) if type(exc) is ValueError and str(exc) in known else '模型结构字段不符合协议' if isinstance(exc, ValidationError) else type(exc).__name__
                event.state, event.reason = 'skipped', str(exc)[:300] if isinstance(exc, BudgetExceeded) else '互动未完成：' + detail
                fields = [str(x['loc'][0])[:30] for x in exc.errors(include_input=False, include_url=False)] if isinstance(exc, ValidationError) else []
                audit(s, 'worker', 'interaction_failed', event_id, error=type(exc).__name__, fields=fields)
                for o in s.scalars(select(Observation).where(Observation.event_id == event_id)):
                    if o.state == 'processing': o.state, o.reason = 'unevaluated', event.reason

    async def validate(self, event, *, reply=False, reaction=None):
        w = self.w
        identity = (await w.forum.read('/session/current.json')).get('current_user', {})
        if not event.data.get('bot_id') or identity.get('id') != event.data['bot_id'] or identity.get('username', '').casefold() != w.forum.connection['username'].casefold():
            raise ValueError('机器人身份已变化')
        target = await w.forum.topic(event.topic_id, 20)
        if target.get('id') != event.topic_id: raise ValueError('目标主题 ID 不匹配')
        private = not await w.forum.public_visible(target)
        if private != event.data['private'] or signature(target) != event.data['participants']:
            raise ValueError('对话可见性或参与者变化')
        if target.get('closed') or target.get('archived') or any(x.get('identity_message') for x in target['context']):
            raise ValueError('主题关闭或身份对话')
        actual = await resolve(w.db, w.forum, event.topic_id, private)
        expected = event.data.get('topic_pipeline')
        if (actual or expected) and (not actual or not expected or actual['id'] != expected['id'] or actual['revision'] != expected['revision']):
            raise ValueError('个人贴编排变化')
        with w.db.transaction() as s:
            connection = s.get(KV, 'connection:forum')
            if not connection or connection.version != event.data['forum_version']: raise ValueError('论坛连接变化')
            originals = list(s.scalars(select(Observation).where(Observation.event_id == event.id)))
            _, active = get_snapshot(s)
            for snap in (s.get(Snapshot, event.snapshot_id), active):
                ip = Policy.model_validate(snap.data['policy']).interaction
                if not ip or (ip.categories and target.get('category_id') not in ip.categories) or (ip.topics and event.topic_id not in ip.topics):
                    raise ValueError('观察范围变化')
        sources = await w.forum.selected_posts(event.topic_id, event.data['post_ids'])
        current = {p['id']: p for p in sources}
        if set(current) != set(event.data['post_ids']) or any(digest_post(current[o.post_id]) != digest_post(w.vault.open(o.data['source'])) for o in originals):
            raise ValueError('来源删除或修改')
        if reply and (max([x['number'] for x in target['context']] or [0]) > event.data['high']
                      or target['context'][-1]['username'].casefold() == w.forum.connection['username'].casefold()):
            raise ValueError('已有新发言，旧正文作废')
        if reply:
            refreshed = await w.checked_memories(event.topic_id, private, {o.username for o in originals}, {o.user_id for o in originals},
                                                 query=' '.join(p.get('text', '')[:300] for p in sources))
            if set(event.data.get('memory_ids', [])) - {x.get('record_id') for x in refreshed}: raise ValueError('引用记忆已删除或访问权变化')
        if reaction:
            if whitelist(target) != event.data['allowed_reactions'] or reaction.emoji not in whitelist(target): raise ValueError('站点表情设置变化')
            post = await w.forum.reaction_post(reaction.post_id)
            if post.get('topic_id') != event.topic_id or post.get('post_type') != 1 or post.get('hidden') or post.get('deleted_at'):
                raise ValueError('表情目标不可用')
            if 'current_user_reaction' not in post: raise ValueError('无法核验已有表情')
            if post.get('can_react') is False: raise ValueError('论坛不允许此表情')
            return post.get('current_user_reaction')

    async def send_one(self):
        w = self.w
        with w.db.transaction() as s:
            reactions = list(s.scalars(select(Reaction).where(Reaction.state == 'ready').order_by(Reaction.created).limit(10)))
        for r in reactions:
            with w.db.transaction() as s: event = s.get(Event, r.event_id)
            try:
                existing = await self.validate(event, reaction=r)
                if existing:
                    with w.db.transaction() as s:
                        row = s.get(Reaction, r.id)
                        row.state = 'sent' if existing.get('id') == r.emoji else 'cancelled'
                        row.reason = '已有相同表情，未 toggle' if row.state == 'sent' else '保留已有其他表情，未 toggle'
                    continue
            except Exception as exc:
                with w.db.transaction() as s:
                    row = s.get(Reaction, r.id); row.state, row.reason = 'expired', '目标核验失败：' + type(exc).__name__
                continue
            if not claim_reaction(w.db, r.id): continue
            try:
                result = await w.forum.react(r.post_id, r.emoji)
                if result.get('current_user_reaction', {}).get('id') != r.emoji: raise ValueError('无法确认写入结果')
                with w.db.transaction() as s:
                    row = s.get(Reaction, r.id); row.state, row.reason = 'sent', '已确认表情'
                    audit(s, 'worker', 'reaction_sent', r.id, post_id=r.post_id, emoji=r.emoji)
            except Exception as exc:
                with w.db.transaction() as s:
                    row = s.get(Reaction, r.id); row.state, row.reason = 'unknown', '结果待核实，不重试：' + type(exc).__name__
            return

def mount_interactions(app, db, vault, admin):
    @app.get('/api/interactions')
    def directory(account=Depends(admin)):
        with db.transaction() as s:
            observations = list(s.scalars(select(Observation).order_by(Observation.created.desc()).limit(200)))
            reactions = list(s.scalars(select(Reaction).order_by(Reaction.created.desc()).limit(200)))
            control, snapshot = get_snapshot(s)
            p = Policy.model_validate(snapshot.data['policy']) if snapshot else Policy()
            recent = list(s.scalars(select(Observation).where(Observation.created > now() - 86400)
                                   .where(Observation.epoch == control['epoch'])
                                   .order_by(Observation.created.desc(), Observation.id.desc()).limit(1500)))
            recent = [o.id for o in recent if o.data.get('eligible')][:(p.interaction.reaction_window if p.interaction else 120)]
            connection = s.get(KV, 'connection:forum')
            site = vault.open(connection.data['cipher'])['base_url'] if connection else ''
            batch_usage = select(Usage).join(Event, Usage.task_id == Event.id).where(Usage.day == day(), Event.receipt.like('interaction:%'))
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            eligible_today = s.scalar(select(func.count()).select_from(Observation).where(Observation.created >= start, Observation.data['eligible'].as_boolean().is_(True)))
            ledger = list(s.scalars(batch_usage))
            return {'posts': [{'id': o.id, 'post_id': o.post_id, 'topic_id': o.topic_id, 'username': o.username, 'number': o.number,
                              'private': o.data['private'], 'state': o.state, 'reason': o.reason, 'created': o.created,
                              'emoji': o.data.get('emoji', ''), 'batch': o.event_id} for o in observations],
                    'reactions': [{'id': r.id, 'post_id': r.post_id, 'topic_id': r.topic_id, 'emoji': r.emoji,
                                  'state': r.state, 'reason': r.reason, 'created': r.created,
                                  'private': s.get(Event, r.event_id).data['private'], 'batch': r.event_id,
                                  'number': s.get(Observation, r.observation_id).number,
                                  'text': vault.open(s.get(Observation, r.observation_id).data['source'])['text'][:600]} for r in reactions],
                    'site': site, 'eligible_recent': len(recent),
                    'reacted_recent': s.scalar(select(func.count()).select_from(Reaction).where(Reaction.observation_id.in_(recent), Reaction.state == 'sent')),
                    'calls_today': s.scalar(select(func.count()).select_from(Usage).where(Usage.day == day())),
                    'batch_calls_today': len(ledger), 'batch_tokens_today': sum(u.tokens for u in ledger),
                    'batch_tokens_per_100': round(100 * sum(u.tokens for u in ledger) / eligible_today) if eligible_today else 0,
                    'counts': dict(s.execute(select(Observation.state, func.count()).group_by(Observation.state)).all()),
                    'reaction_counts': dict(s.execute(select(Reaction.state, func.count()).group_by(Reaction.state)).all())}

    @app.post('/api/reactions/{rid}/decision')
    def decide(rid: str, body: ReactionDecision, account=Depends(admin)):
        with db.transaction() as s:
            control = locked(s, 'control').data
            r = s.scalar(select(Reaction).where(Reaction.id == rid).with_for_update())
            if not r: raise HTTPException(404, '表情动作不存在')
            e = s.get(Event, r.event_id)
            if body.action.startswith('resolve_'):
                if r.state != 'unknown': raise HTTPException(409, '仅待核实动作可确认结果')
                r.state = 'sent' if body.action == 'resolve_sent' else 'cancelled'
                r.reason = '管理员核实，原动作永不重试'
            elif r.state != 'approval': raise HTTPException(409, '状态已变化')
            elif body.action == 'reject': r.state, r.reason = 'rejected', '管理员拒绝'
            else:
                if control['mode'] not in ('approval', 'auto') or e.epoch != control['epoch'] or e.expires <= now():
                    raise HTTPException(409, '水位、模式或有效期已变化')
                r.state, r.reason = 'ready', '等待共享写入门'
            audit(s, account.id, 'reaction_' + body.action, rid)
        return {'ok': True}

    @app.post('/api/reactions/{rid}/check')
    async def check_result(rid: str, account=Depends(admin)):
        with db.transaction() as s:
            r = s.get(Reaction, rid)
            if not r or r.state != 'unknown': raise HTTPException(409, '只核对待核实动作')
            e = s.get(Event, r.event_id)
            connection = s.get(KV, 'connection:forum')
            if not connection or connection.version != e.data['forum_version']: raise HTTPException(409, '论坛连接变化，需人工核实')
            conf, version = vault.open(connection.data['cipher']), connection.version
            pid, emoji, tid = r.post_id, r.emoji, r.topic_id
        forum = Discourse(conf)
        try:
            await forum.login()
            post = await forum.reaction_post(pid)
            confirmed = post.get('topic_id') == tid and isinstance(post.get('current_user_reaction'), dict) and post['current_user_reaction'].get('id') == emoji
            with db.transaction() as s:
                locked(s, 'control')
                r = s.scalar(select(Reaction).where(Reaction.id == rid).with_for_update())
                if r.state != 'unknown' or s.get(KV, 'connection:forum').version != version:
                    raise HTTPException(409, '核对期间状态或连接变化')
                if confirmed and forum.bot_id == e.data['bot_id']:
                    r.state, r.reason = 'sent', '只读查询确认表情已生效，未重新 toggle'
                else:
                    r.reason = '只读查询仍不能确认原动作，保持待核实且不重试'
                audit(s, account.id, 'reaction_checked', rid, confirmed=r.state == 'sent')
                return {'state': r.state, 'reason': r.reason}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(503, '只读核对失败：' + type(exc).__name__)
        finally: await forum.close()
