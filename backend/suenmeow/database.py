from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, create_engine, select, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def now() -> float:
    return time.time()


def uid() -> str:
    return uuid.uuid4().hex


def day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="editor")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_cipher: Mapped[str] = mapped_column(Text, default="")
    totp_pending: Mapped[str] = mapped_column(Text, default="")
    totp_last: Mapped[int] = mapped_column(Integer, default=0)
    forum_username: Mapped[str] = mapped_column(String(100), default="")


class LoginSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[float] = mapped_column(Float)


class KV(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    owner: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    grants: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated: Mapped[float] = mapped_column(Float, default=now)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(32))
    created: Mapped[float] = mapped_column(Float, default=now)
    note: Mapped[str] = mapped_column(String(300), default="")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    receipt: Mapped[str] = mapped_column(String(200), unique=True)
    topic_id: Mapped[int] = mapped_column(Integer, index=True)
    epoch: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[int] = mapped_column(Integer)
    created: Mapped[float] = mapped_column(Float, default=now)
    expires: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    data: Mapped[dict] = mapped_column(JSON)


class Reply(Base):
    __tablename__ = "replies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(String(32), unique=True)
    topic_id: Mapped[int] = mapped_column(Integer, index=True)
    text_cipher: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), index=True)
    created: Mapped[float] = mapped_column(Float, default=now)
    updated: Mapped[float] = mapped_column(Float, default=now)
    sent_post_id: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(300), default="")
    memory_state: Mapped[str] = mapped_column(String(32), default="pending")


class Usage(Base):
    __tablename__ = "usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    day: Mapped[str] = mapped_column(String(10), index=True)
    route: Mapped[str] = mapped_column(String(20))
    topic_id: Mapped[int] = mapped_column(Integer)
    tokens: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), default="reserved")
    created: Mapped[float] = mapped_column(Float, default=now)
    task_id: Mapped[str] = mapped_column(String(32), default="", index=True)


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新的管理会话")
    created: Mapped[float] = mapped_column(Float, default=now)


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(20))
    text_cipher: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float, default=now)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    owner: Mapped[str] = mapped_column(String(32), index=True)
    instruction_cipher: Mapped[str] = mapped_column(Text)
    constraints: Mapped[dict] = mapped_column(JSON)
    snapshot_id: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    created: Mapped[float] = mapped_column(Float, default=now)
    expires: Mapped[float] = mapped_column(Float)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    result_cipher: Mapped[str] = mapped_column(Text, default="")


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    tool: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(20))
    detail_cipher: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float, default=now)


class AgentSource(Base):
    __tablename__ = "agent_sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    topic_id: Mapped[int] = mapped_column(Integer)
    post_id: Mapped[int] = mapped_column(Integer, default=0)
    post_number: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str] = mapped_column(Text)
    public: Mapped[bool] = mapped_column(Boolean)
    content_cipher: Mapped[str] = mapped_column(Text)


class AgentDraft(Base):
    __tablename__ = "agent_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(String(32), unique=True)
    target_topic: Mapped[int] = mapped_column(Integer)
    text_cipher: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_id: Mapped[str] = mapped_column(String(32), default="")


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(Float, default=now)


class Database:
    def __init__(self, url: str):
        kw = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kw["poolclass"] = StaticPool
            elif url.startswith("sqlite:///"):
                Path(url[10:]).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **kw)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        # Additive migrations keep legacy runtime state intact and require explicit CLI execution.
        Base.metadata.create_all(self.engine)
        columns = {c["name"] for c in inspect(self.engine).get_columns("usage")}
        if "task_id" not in columns:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE usage ADD COLUMN task_id VARCHAR(32) NOT NULL DEFAULT ''"))
            next(index for index in Usage.__table__.indexes if index.name == "ix_usage_task_id").create(self.engine, checkfirst=True)
        with self.transaction() as s:
            if not s.get(KV, "schema"):
                s.add(KV(key="schema", data={"version": 2}))
            elif s.get(KV, "schema").data["version"] not in (1, 2):
                raise RuntimeError("Unsupported schema version")
            else:
                s.get(KV, "schema").data = {"version": 2}
            for key, data in {
                "control": {"mode": "paused", "epoch": 1, "active_snapshot": 0},
                "gate": {"last_send": 0, "topics": {}},
                "budget_lock": {},
                "worker": {"status": "stopped", "heartbeat": 0},
                "policy": {}, "routes": {}, "pipeline": {},
                "agent_policy": {}, "agent_lock": {},
            }.items():
                if not s.get(KV, key):
                    s.add(KV(key=key, data=data))

    @contextmanager
    def transaction(self):
        with self.sessions.begin() as s:
            yield s


def locked(s, key: str) -> KV:
    return s.scalars(select(KV).where(KV.key == key).with_for_update()).one()


def audit(s, actor: str, action: str, target: str = "", **detail):
    s.add(Audit(actor=actor, action=action, target=target, detail=detail))
