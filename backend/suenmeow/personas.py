"""Recognize migrated role files without rewriting preserved legacy metadata."""
from copy import deepcopy

from sqlalchemy import select
from fastapi import HTTPException

from .database import Account, KV, Record, Snapshot, TopicPipeline, audit, locked
from .prompts import PROTECTED_TITLES


def legacy_persona(s, module_id: str, title: str) -> bool:
    marker = s.get(KV, 'prompt_refresh:20261004')
    if marker and module_id in marker.data.get('persona_ids', []):
        return True
    if title in PROTECTED_TITLES:
        record = s.get(Record, module_id)
        owner = s.get(Account, record.owner) if record else None
        return bool(owner and owner.role == 'admin')
    return False


def is_persona(s, record) -> bool:
    return bool(record.data.get('persona') or legacy_persona(s, record.id, record.title))


def persona_data(record):
    return {'title': record.title, 'content': record.data.get('content', ''), 'persona': True, 'version': record.version}


def require_review(s):
    return s.get(KV, 'topic_pipeline_settings').data['require_review']


def accepted_persona(s, record):
    if not require_review(s):
        return persona_data(record)
    return s.get(KV, 'persona_publications').data.get(record.id)


def baseline_personas(s):
    """Turning review on governs future edits, without unpublishing existing personas."""
    row = s.get(KV, 'persona_publications')
    row.data = {r.id: persona_data(r) for r in s.scalars(select(Record).where(Record.kind == 'module')) if is_persona(s, r)}


def publish_personas(s, records, actor, refresh_global=True):
    """Publish only the selected persona text; never publish unrelated work/config drafts."""
    if any(r is None for r in records):
        raise HTTPException(422, '人格已删除，请重新选择')
    records = [r for r in records if is_persona(s, r)]
    if not records:
        return
    locked(s, 'pipeline')
    control = locked(s, 'control')
    locked(s, 'topic_pipeline_lock')
    accepted = s.get(KV, 'persona_publications')
    changes = {r.id: persona_data(r) for r in records}
    accepted.data = {**accepted.data, **changes}
    for r in records:
        audit(s, actor, 'persona_published', r.id, version=r.version)
    # Explicit persona publication refreshes its use in already active personal arrangements.
    # Pending ordering drafts stay pending; disabled arrangements are not re-enabled.
    for row in s.scalars(select(TopicPipeline).where(TopicPipeline.enabled.is_(True))):
        updates = {mid: data for mid, data in changes.items() if mid in row.published.get('modules', {})}
        if updates and any(row.published['modules'][mid] != data for mid, data in updates.items()):
            modules = {**row.published['modules'], **updates}
            if any(sum(len(modules[mid]['content'].encode()) for mid in ids) > 200000
                   for ids in row.published['personas'].values()):
                raise HTTPException(422, '人格修改会使已生效编排超过单路200,000字节限制，请缩短内容')
            row.published = {**row.published, 'modules': modules}
            row.generation += 1
    if not refresh_global:
        return
    snapshot = s.get(Snapshot, control.data['active_snapshot'])
    if not snapshot:
        return
    source = deepcopy(snapshot.data)
    referenced = {mid for ids in source['pipeline'].values() for mid in ids}
    changed = False
    for mid, data in changes.items():
        original = source['modules'].get(mid)
        # A system draft cannot bypass global publication by merely setting its persona flag.
        if (mid in referenced and original and (original.get('persona') or legacy_persona(s, mid, original['title']))
                and any(original.get(key) != data[key] for key in ('title', 'content', 'version'))):
            source['modules'][mid] = {**original, **{key: data[key] for key in ('title', 'content', 'version')}}
            changed = True
    if changed:
        from .service import publish
        publish(s, actor, '仅发布人格修改；保留已发布全局规则与编排', source=source)


def saved_personas(s, records, actor):
    if not require_review(s):
        publish_personas(s, records, actor)
