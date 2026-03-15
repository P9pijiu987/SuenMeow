from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from bot.llm_client import LlmClient


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


class Planner:
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
        llm_client: LlmClient | None = None,
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
            parsed_reason = str(parsed.get("reason") or "").strip()
            return PlannerDecision(
                should_reply=self._parse_should_reply(parsed.get("should_reply"), fallback.should_reply),
                priority=str(parsed.get("priority") or fallback.priority),
                target_username=parsed.get("target_username") or None,
                target_post_number=int(parsed["target_post_number"]) if parsed.get("target_post_number") is not None else None,
                reason=parsed_reason or fallback.reason,
                style_notes=str(parsed.get("style_notes") or fallback.style_notes),
                memory_action=str(parsed.get("memory_action") or fallback.memory_action),
            )
        except (TypeError, ValueError):
            return fallback
