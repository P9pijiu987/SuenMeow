from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import re
from re import Pattern
from typing import Protocol

from bot.llm_client import LlmResponseFormatError
from bot.llm_client import LlmRouteUnavailableError


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
    ) -> PlannerChatResponse: ...

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
        if llm_client is None:
            raise LlmRouteUnavailableError("planner route requires an available llm_client")

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
        parsed = llm_client.parse_json_object(response.content)
        if not parsed:
            raise LlmResponseFormatError("planner route returned non-JSON or empty decision payload")
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
            parsed_reason = self._sanitize_short_field(parsed.get("reason"), "planner returned empty reason", max_chars=280)
            parsed_style_notes = self._sanitize_short_field(
                parsed.get("style_notes"), "concise, thread-aware", max_chars=200
            )
            parsed_memory_action = self._sanitize_short_field(
                parsed.get("memory_action"), "none", max_chars=80
            )
            return PlannerDecision(
                should_reply=self._parse_should_reply(parsed.get("should_reply"), False),
                priority=str(parsed.get("priority") or "normal"),
                target_username=target_username,
                target_post_number=target_post_number,
                reason=parsed_reason or "planner returned empty reason",
                style_notes=parsed_style_notes,
                memory_action=parsed_memory_action,
            )
        except (TypeError, ValueError) as exc:
            raise LlmResponseFormatError("planner decision payload schema is invalid") from exc
