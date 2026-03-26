from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import re

from bot.llm_client import LlmClient
from bot.llm_client import LlmResponseFormatError
from bot.llm_client import LlmRouteUnavailableError
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
    _LEAKY_PREFIX_PATTERN = re.compile(r"^(role|planner|thought|analysis|system)\s*:", re.IGNORECASE)

    @classmethod
    def _sanitize_persona_prefix(cls, persona_text: str) -> str:
        persona_lines = [line.strip() for line in persona_text.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not persona_lines:
            return "SuenMeow"
        first = persona_lines[0]
        if cls._LEAKY_PREFIX_PATTERN.match(first):
            return "SuenMeow"
        return first

    @classmethod
    def _sanitize_reason(cls, reason: str) -> str:
        cleaned_lines = [line.strip() for line in reason.splitlines() if line.strip()]
        if not cleaned_lines:
            return ""
        kept: list[str] = []
        for line in cleaned_lines:
            if cls._LEAKY_PREFIX_PATTERN.match(line):
                continue
            kept.append(line)
        text = " ".join(kept).strip()
        if not text:
            return ""
        return text[:280]

    @classmethod
    def sanitize_reply_text(cls, content: str) -> str:
        cleaned_lines = [line.rstrip() for line in content.splitlines()]
        kept: list[str] = []
        for line in cleaned_lines:
            stripped = line.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            if cls._LEAKY_PREFIX_PATTERN.match(stripped):
                continue
            kept.append(stripped)
        sanitized = "\n".join(kept).strip()
        return sanitized[:4000]

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
        if not decision.should_reply:
            return ReplyDraft(
                content="",
                target_post_number=decision.target_post_number,
                target_username=decision.target_username,
                skipped=True,
            )
        if llm_client is None:
            raise LlmRouteUnavailableError("replyer route requires an available llm_client")

        if user_prompt is None:
            user_prompt = (
                f"Persona:\n{persona_text}\n\n"
                f"Decision:\n{decision.to_dict()}\n\n"
                f"Thread context:\n{context}\n\n"
                "Write a single forum reply only."
            )
        response = await llm_client.chat(route_name, system_prompt, user_prompt, temperature=0.7)
        if not response.content.strip():
            raise LlmResponseFormatError("replyer route returned empty content")
        safe_content = self.sanitize_reply_text(response.content)
        if not safe_content:
            raise LlmResponseFormatError("replyer route content became empty after sanitization")
        return ReplyDraft(
            content=safe_content,
            target_post_number=decision.target_post_number,
            target_username=decision.target_username,
            skipped=False,
        )
