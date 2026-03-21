from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import re
from re import Pattern
from typing import Protocol


@dataclass(slots=True)
class PlannerInput:
    trigger_reason: str
    topic_id: int
    posts: list[dict[str, object]]
    user_memories: dict[str, str]
    self_memory: str


@dataclass(slots=True)
class PlannerDecision:
    should_reply: bool
    priority: str
    target_username: str | None
    target_post_number: int | None
    reason: str
    style_notes: str
    memory_action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PlannerLlmClient(Protocol):
    async def chat(
        self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.2
    ) -> PlannerChatResponse | None: ...

    def parse_json_object(self, content: str) -> dict[str, object] | None: ...


class PlannerChatResponse(Protocol):
    content: str


class Planner:
    _LEAKY_PREFIX_PATTERN: Pattern[str] = re.compile(r"^(role|planner|thought|analysis|system)\s*:", re.IGNORECASE)

    @classmethod
    def _sanitize_short_field(cls, raw_value: object, fallback: str, *, max_chars: int) -> str:
        text = str(raw_value or "").strip()
        if not text:
            return fallback
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        kept = [line for line in lines if not cls._LEAKY_PREFIX_PATTERN.match(line)]
        cleaned = " ".join(kept).strip()
        if not cleaned:
            return fallback
        return cleaned[:max_chars]

    @staticmethod
    def _parse_should_reply(raw_value: object, fallback: bool) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            lowered = raw_value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return fallback

    def _fallback_decide(self, planner_input: PlannerInput, context: str) -> PlannerDecision:
        posts = planner_input.posts
        if not posts:
            return PlannerDecision(
                should_reply=False,
                priority="skip",
                target_username=None,
                target_post_number=None,
                reason="no posts available",
                style_notes="none",
                memory_action="none",
            )

        last_post = posts[-1]
        raw_target_username = last_post.get("username")
        target_username = raw_target_username if isinstance(raw_target_username, str) and raw_target_username else None
        raw_target_post_number = last_post.get("post_number")
        target_post_number = raw_target_post_number if isinstance(raw_target_post_number, int) else None
        reason = f"reply due to {planner_input.trigger_reason}"
        style_notes = "concise, thread-aware"
        should_reply = True
        priority = "normal"
        memory_action = "none"

        if planner_input.trigger_reason == "notification":
            priority = "high"
            style_notes = "direct, responsive"
        elif planner_input.trigger_reason == "burst_activity":
            priority = "medium"
            style_notes = "short, selective"
        elif planner_input.trigger_reason == "hourly_scan":
            priority = "low"
            style_notes = "concise, context-light"

        if len(context.strip()) < 10:
            should_reply = False
            priority = "skip"
            reason = "context too thin"

        if target_username is not None and planner_input.user_memories.get(target_username):
            style_notes += ", memory-aware"

        return PlannerDecision(
            should_reply=should_reply,
            priority=priority,
            target_username=target_username,
            target_post_number=target_post_number,
            reason=reason,
            style_notes=style_notes,
            memory_action=memory_action,
        )

    async def decide(
        self,
        planner_input: PlannerInput,
        context: str,
        *,
        llm_client: PlannerLlmClient | None = None,
        system_prompt: str = "",
        user_prompt: str | None = None,
        route_name: str = "planner",
    ) -> PlannerDecision:
        fallback = self._fallback_decide(planner_input, context)
        if llm_client is None:
            return fallback

        if user_prompt is None:
            user_prompt = (
                "Return one JSON object with keys: should_reply, priority, target_username, "
                "target_post_number, reason, style_notes, memory_action.\n\n"
                f"Trigger reason: {planner_input.trigger_reason}\n"
                f"Topic id: {planner_input.topic_id}\n"
                f"Self memory: {planner_input.self_memory or '(empty)'}\n"
                f"User memories: {planner_input.user_memories or {}}\n"
                f"Context:\n{context}"
            )
        response = await llm_client.chat(route_name, system_prompt, user_prompt, temperature=0.2)
        if response is None:
            return fallback
        parsed = llm_client.parse_json_object(response.content)
        if not parsed:
            return fallback
        try:
            raw_target_username = parsed.get("target_username")
            target_username = raw_target_username if isinstance(raw_target_username, str) and raw_target_username else None
            raw_target_post_number = parsed.get("target_post_number")
            target_post_number: int | None
            if isinstance(raw_target_post_number, int) and not isinstance(raw_target_post_number, bool):
                target_post_number = raw_target_post_number
            elif isinstance(raw_target_post_number, str) and raw_target_post_number.strip():
                target_post_number = int(raw_target_post_number)
            else:
                target_post_number = None
            parsed_reason = self._sanitize_short_field(parsed.get("reason"), fallback.reason, max_chars=280)
            parsed_style_notes = self._sanitize_short_field(
                parsed.get("style_notes"), fallback.style_notes, max_chars=200
            )
            parsed_memory_action = self._sanitize_short_field(
                parsed.get("memory_action"), fallback.memory_action, max_chars=80
            )
            return PlannerDecision(
                should_reply=self._parse_should_reply(parsed.get("should_reply"), fallback.should_reply),
                priority=str(parsed.get("priority") or fallback.priority),
                target_username=target_username,
                target_post_number=target_post_number,
                reason=parsed_reason or fallback.reason,
                style_notes=parsed_style_notes,
                memory_action=parsed_memory_action,
            )
        except (TypeError, ValueError):
            return fallback
