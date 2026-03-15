from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from bot.llm_client import LlmClient
from bot.planner import PlannerDecision


@dataclass(slots=True)
class ReplyDraft:
    content: str
    target_post_number: int | None
    target_username: str | None
    skipped: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Replyer:
    def _fallback_generate(self, decision: PlannerDecision, context: str, persona_text: str) -> ReplyDraft:
        if not decision.should_reply:
            return ReplyDraft(
                content="",
                target_post_number=decision.target_post_number,
                target_username=decision.target_username,
                skipped=True,
            )
        persona_lines = [line.strip() for line in persona_text.splitlines() if line.strip() and not line.strip().startswith("#")]
        prefix = persona_lines[0] if persona_lines else "SuenMeow"
        opener = f"@{decision.target_username} " if decision.target_username else ""
        body = f"{opener}{decision.reason}."
        if "memory-aware" in decision.style_notes:
            body += " 我会结合你一贯的表达方式来回应。"
        return ReplyDraft(
            content=f"{prefix}\n\n{body}".strip(),
            target_post_number=decision.target_post_number,
            target_username=decision.target_username,
            skipped=False,
        )

    async def generate(
        self,
        decision: PlannerDecision,
        context: str,
        persona_text: str,
        *,
        llm_client: LlmClient | None = None,
        system_prompt: str = "",
        user_prompt: str | None = None,
        route_name: str = "replyer",
    ) -> ReplyDraft:
        fallback = self._fallback_generate(decision, context, persona_text)
        if not decision.should_reply or llm_client is None:
            return fallback

        if user_prompt is None:
            user_prompt = (
                f"Persona:\n{persona_text}\n\n"
                f"Decision:\n{decision.to_dict()}\n\n"
                f"Thread context:\n{context}\n\n"
                "Write a single forum reply only."
            )
        response = await llm_client.chat(route_name, system_prompt, user_prompt, temperature=0.7)
        if response is None or not response.content.strip():
            return fallback
        return ReplyDraft(
            content=response.content.strip(),
            target_post_number=decision.target_post_number,
            target_username=decision.target_username,
            skipped=False,
        )
