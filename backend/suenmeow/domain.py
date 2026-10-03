from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(Strict):
    notification_interval: int = Field(5, ge=5, le=300)
    hot_interval: int = Field(60, ge=30, le=3600)
    hot_min_new_posts: int = Field(5, ge=1, le=1000)
    burst_window_minutes: int = Field(5, ge=1, le=60)
    hourly_new_reply_min: int = Field(2, ge=1, le=1000)
    hourly_hot_reply_min: int = Field(10, ge=1, le=1000)
    global_cooldown: int = Field(60, ge=30, le=3600)
    topic_cooldown: int = Field(600, ge=60, le=86400)
    event_ttl: int = Field(600, ge=60, le=1800)
    daily_tokens: int = Field(800000, ge=1000, le=10000000)
    topic_tokens: int = Field(30000, ge=1000, le=1000000)
    context_posts: int = Field(60, ge=5, le=120)
    max_reply_chars: int = Field(2000, ge=100, le=10000)
    muted_topics: list[int] = Field(default_factory=list, max_length=1000)
    muted_users: list[str] = Field(default_factory=list, max_length=1000)
    quiet_start: int = Field(0, ge=0, le=23)
    quiet_end: int = Field(0, ge=0, le=23)
    timezone: str = "Asia/Shanghai"
    notifications: bool = True
    hot_topics: bool = True
    playful: bool = False

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, v):
        ZoneInfo(v)
        return v


def validate_url(v: str) -> str:
    p = urlsplit(v)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("连接地址必须是无凭据的 HTTPS URL")
    return v.rstrip("/")


class ForumConnection(Strict):
    base_url: str
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    @field_validator("base_url")
    @classmethod
    def url(cls, v):
        return validate_url(v)


class Route(Strict):
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=1000)
    max_output: int = Field(1600, ge=100, le=8000)
    temperature: float = Field(0.7, ge=0, le=2)
    supports_tools: bool = False
    endpoint_mode: Literal["base", "complete"] = "base"
    @field_validator("base_url")
    @classmethod
    def url(cls, v):
        return validate_url(v)


class RecordInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    data: dict
    version: int = Field(1, ge=1)
    grants: list[str] = Field(default_factory=list, max_length=100)


class ModuleData(Strict):
    content: str = Field(max_length=50000)
    description: str = Field(default="", max_length=500)
    persona: bool = False


class NestData(Strict):
    topic_id: int = Field(gt=0)
    forum_username: str = Field(default="", max_length=100)
    private: bool = False
    diary: bool = False
    followup: bool = False
    opted_out: bool = False
    objects: list[dict] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=4000)
    activity: str = Field(default="", max_length=200)
    progress: int = Field(0, ge=0, le=100)
    mood: Literal["慵懒", "好奇", "轻快", "安静"] = "好奇"
    energy: int = Field(60, ge=0, le=100)
    mood_updated: float = Field(0, ge=0)

    @field_validator("objects")
    @classmethod
    def room_objects(cls, v):
        if any(set(x) - {"name", "note"} or not isinstance(x.get("name", ""), str) or not isinstance(x.get("note", ""), str)
               or len(x.get("name", "")) > 80 or len(x.get("note", "")) > 300 for x in v):
            raise ValueError("物件仅包含名称和说明")
        return v


class ModeInput(Strict):
    mode: Literal["paused", "read_only", "approval", "auto"]


class AgentPolicy(Strict):
    enabled: bool = True
    auto_research: bool = False
    allowed_tools: list[Literal["forum_search", "forum_read_topic", "forum_user_activity", "memory_lookup", "release_describe"]] = Field(
        default_factory=lambda: ["forum_search", "forum_read_topic", "forum_user_activity", "memory_lookup", "release_describe"])
    max_steps: int = Field(8, ge=1, le=16)
    max_topics: int = Field(3, ge=1, le=8)
    max_seconds: int = Field(120, ge=20, le=300)
    max_tokens: int = Field(16000, ge=1000, le=100000)
    max_chars: int = Field(8000, ge=500, le=15000)
    max_parallel: int = Field(2, ge=1, le=2)


class AgentMessageInput(Strict):
    text: str = Field(min_length=1, max_length=12000)
    target_topic: int = Field(0, ge=0)
    private: bool = False
    max_chars: int = Field(3000, ge=100, le=15000)
    kind: Literal["research", "reply"] = "research"
    allow_send: bool = False
    reply_to: int = Field(0, ge=0)


class AgentDraftInput(Strict):
    text: str = Field(min_length=1, max_length=15000)
    target_topic: int = Field(gt=0)
    version: int = Field(gt=0)


class AgentConfirmInput(Strict):
    digest: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
