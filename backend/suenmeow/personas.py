"""Recognize migrated role files without rewriting preserved legacy metadata."""
from .database import Account, KV, Record
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
