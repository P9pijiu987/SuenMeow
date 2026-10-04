import hashlib
import hmac
import json
from ipaddress import ip_address
from pathlib import Path
import secrets
import socket
from threading import BoundedSemaphore

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

from .database import Record
from .settings import Settings

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
HASH_SLOTS = BoundedSemaphore(2)


def password_hash(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise HTTPException(422, "密码必须为 12–256 个字符")
    with HASH_SLOTS:
        return HASHER.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        with HASH_SLOTS:
            return HASHER.verify(encoded, password)
    except (VerificationError, ValueError):
        return False


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_address(request: Request, settings: Settings) -> str:
    """Accept a single sanitized address only from the configured gateway TCP peer."""
    peer = request.client.host if request.client else "unknown"
    header = request.headers.get("x-suenmeow-client-ip", "")
    if not settings.trusted_proxy_host or not header or len(header) > 45 or "%" in header:
        return peer
    try:
        address = ip_address(header).compressed
        proxies = {entry[4][0] for entry in socket.getaddrinfo(settings.trusted_proxy_host, None)}
        return address if peer in proxies else peer
    except (ValueError, OSError):
        return peer  # DNS/header failure keeps a bounded shared peer bucket, never an untrusted identity.


class Vault:
    def __init__(self, path: Path):
        if not path.exists():
            raise RuntimeError("Encryption key missing; run suenmeow init first")
        self.fernet = Fernet(path.read_bytes().strip())

    def seal(self, value) -> str:
        return self.fernet.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def open(self, value: str):
        return json.loads(self.fernet.decrypt(value.encode()))


def require_admin(user):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")


def can_edit(user, record: Record) -> bool:
    return user.role == "admin" or record.owner == user.id or (record.kind == "module" and user.id in record.grants)


def enforce_record(user, record):
    if not can_edit(user, record):
        raise HTTPException(403, "没有此内容的访问权限")


def same_token(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
