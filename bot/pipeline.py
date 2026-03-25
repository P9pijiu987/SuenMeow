from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import TypedDict
from typing import Protocol

from bot.ban_service import BanService
from bot.context_builder import ContextBuilder
from bot.forum_client import ForumClient
from bot.llm_client import LlmClient
from bot.memory_policy import extract_self_memory_update
from bot.memory_policy import extract_user_memory_updates
from bot.memory_policy import normalize_memory_text
from bot.memory_service import MemoryService
from bot.persona_loader import PersonaLoader
from bot.planner import PlannerDecision
from bot.planner import PlannerInput
from bot.prompt_loader import PromptLoader
from bot.replyer import ReplyDraft
from bot.replyer import Replyer
from bot.settings import PromptModulesConfig
from bot.settings import default_prompt_modules_config
from bot.settings import enabled_prompt_module_names
from db.repositories import Database


ForumPost = dict[str, object]
RouteDescription = dict[str, str]


logger = logging.getLogger(__name__)


class PromptBundle(TypedDict):
    planner_input: PlannerInput
    planner_system_prompt: str
    planner_user_prompt: str
    replyer_system_prompt: str
    memory_system_prompt: str
    replyer_context: str
    core_persona: str
    reply_persona: str
    reply_extra_persona: str
    self_memory: str
    user_memories: dict[str, str]
    planner_context: str


class DryRunResult(TypedDict):
    decision: PlannerDecision
    draft: ReplyDraft
    user_memories: dict[str, str]
    model_routes: dict[str, RouteDescription | None]
    planner_prompt_preview: str
    replyer_prompt_preview: str
    memory_prompt_preview: str
    persona_modules: list[str]
    debug_prompts: dict[str, dict[str, str]]


class DebugTopicResult(TypedDict):
    topic_id: int
    topic_title: str
    highest_post_number: int
    post_count: int
    decision: dict[str, object]
    draft: dict[str, object]
    debug_prompts: dict[str, dict[str, str]]
    model_routes: dict[str, RouteDescription | None]
    persona_modules: list[str]
    memory_hits: dict[str, str]


class ProcessEventResult(TypedDict):
    action: str
    pending_reply_id: int | None
    topic_id: int
    topic_title: str
    post_count: int
    decision: dict[str, object]
    draft: dict[str, object]
    memory_hits: dict[str, str]
    persona_modules: list[str]
    planner_prompt_preview: str
    replyer_prompt_preview: str


class PlannerLike(Protocol):
    async def decide(
        self,
        planner_input: PlannerInput,
        context: str,
        *,
        llm_client: LlmClient | None = None,
        system_prompt: str = "",
        user_prompt: str | None = None,
        route_name: str = "planner",
    ) -> PlannerDecision: ...


class Pipeline:
    _MEMORY_UPDATE_CONFIDENCE_THRESHOLD = 0.2
    _SELF_MEMORY_UPDATE_CONFIDENCE_THRESHOLD = 0.6
    _MEMORY_COMMAND_PATTERN = re.compile(r"^\s*/memory\+(?P<content>.+)$", re.IGNORECASE | re.DOTALL)

    def __init__(
        self,
        context_builder: ContextBuilder,
        planner: PlannerLike,
        replyer: Replyer,
        persona_loader: PersonaLoader,
        prompt_loader: PromptLoader,
        llm_client: LlmClient | None,
        ban_service: BanService,
        database: Database,
        memory_service: MemoryService,
        enabled_personas: list[str],
        prompt_modules: PromptModulesConfig | None = None,
        allow_send_reply: bool = False,
        require_approval_before_send: bool = False,
        shadow_mode: bool = False,
        panic_switch: bool = False,
        topic_cooldown_minutes: int = 0,
        blackout_start_hour: int | None = None,
        blackout_end_hour: int | None = None,
        muted_topic_ids: list[int] | None = None,
        muted_usernames: list[str] | None = None,
    ) -> None:
        self.context_builder = context_builder
        self.planner = planner
        self.replyer = replyer
        self.persona_loader = persona_loader
        self.prompt_loader = prompt_loader
        self.llm_client = llm_client
        self.ban_service = ban_service
        self.database = database
        self.memory_service = memory_service
        self.enabled_personas = enabled_personas
        self.prompt_modules = prompt_modules or default_prompt_modules_config()
        self.allow_send_reply = allow_send_reply
        self.require_approval_before_send = require_approval_before_send
        self.shadow_mode = shadow_mode
        self.panic_switch = panic_switch
        self.topic_cooldown_minutes = topic_cooldown_minutes
        self.blackout_start_hour = blackout_start_hour
        self.blackout_end_hour = blackout_end_hour
        self.muted_topic_ids = muted_topic_ids or []
        self.muted_usernames = muted_usernames or []

    @staticmethod
    def _build_skip_decision(reason: str) -> PlannerDecision:
        return PlannerDecision(
            should_reply=False,
            priority="skip",
            target_username=None,
            target_post_number=None,
            reason=reason,
            style_notes="none",
            memory_action="none",
        )

    @staticmethod
    def _build_skipped_draft() -> ReplyDraft:
        return ReplyDraft(content="", target_post_number=None, target_username=None, skipped=True)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_hour(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            logger.warning("invalid blackout hour value: bool is not allowed; value=%s", value)
            return None
        if isinstance(value, int):
            hour = value
        elif isinstance(value, str):
            try:
                hour = int(value)
            except ValueError:
                logger.warning("invalid blackout hour value: not numeric; value=%s", value)
                return None
        else:
            logger.warning("invalid blackout hour value type; value=%r", value)
            return None
        if 0 <= hour <= 23:
            return hour
        logger.warning("invalid blackout hour value range; value=%s", hour)
        return None

    @staticmethod
    def _normalize_non_negative_minutes(value: object) -> int:
        if isinstance(value, bool):
            logger.warning("invalid cooldown minutes value: bool is not allowed; value=%s", value)
            return 0
        if isinstance(value, int):
            minutes = value
        elif isinstance(value, str):
            try:
                minutes = int(value)
            except ValueError:
                logger.warning("invalid cooldown minutes value: not numeric; value=%s", value)
                return 0
        else:
            logger.warning("invalid cooldown minutes value type; value=%r", value)
            return 0
        return max(0, minutes)

    def _normalized_muted_topic_ids(self) -> set[int]:
        normalized: set[int] = set()
        for value in self.muted_topic_ids:
            try:
                normalized.add(int(value))
            except (TypeError, ValueError):
                continue
        return normalized

    def _normalized_muted_usernames(self) -> set[str]:
        normalized: set[str] = set()
        for value in self.muted_usernames:
            if not isinstance(value, str):
                continue
            candidate = value.strip().lower()
            if candidate:
                normalized.add(candidate)
        return normalized

    def _is_blackout_active(self, now: datetime | None = None) -> bool:
        start_hour = self._normalize_hour(self.blackout_start_hour)
        end_hour = self._normalize_hour(self.blackout_end_hour)
        if start_hour is None or end_hour is None or start_hour == end_hour:
            return False
        current = now or self._utcnow()
        hour = current.astimezone(timezone.utc).hour
        if start_hour < end_hour:
            return start_hour <= hour < end_hour
        return hour >= start_hour or hour < end_hour

    def _is_topic_in_cooldown(self, topic_id: int, now: datetime | None = None) -> bool:
        cooldown_minutes = self._normalize_non_negative_minutes(self.topic_cooldown_minutes)
        if cooldown_minutes <= 0:
            return False
        state = self.database.get_topic_state(topic_id)
        if state is None or not state.last_replied_at:
            return False
        try:
            last_replied_at = datetime.fromisoformat(state.last_replied_at)
        except ValueError:
            return False
        if last_replied_at.tzinfo is None:
            last_replied_at = last_replied_at.replace(tzinfo=timezone.utc)
        current = now or self._utcnow()
        return current - last_replied_at < timedelta(minutes=cooldown_minutes)

    def _is_target_user_muted(self, target_username: str | None) -> bool:
        if target_username is None:
            return False
        return target_username.strip().lower() in self._normalized_muted_usernames()

    def _record_short_circuit_run(
        self,
        *,
        event_id: int | None,
        topic_id: int,
        topic_title: str,
        trigger_reason: str,
        highest_post_number: int,
        action: str,
        reason: str,
    ) -> ProcessEventResult:
        self.database.note_topic_seen(topic_id, highest_post_number)
        decision = self._build_skip_decision(reason)
        draft = self._build_skipped_draft()
        self.database.record_pipeline_run(
            event_id=event_id,
            topic_id=topic_id,
            topic_title=topic_title,
            trigger_reason=trigger_reason,
            action=action,
            decision=decision.to_dict(),
            draft_content=draft.content,
        )
        return {
            "action": action,
            "pending_reply_id": None,
            "topic_id": topic_id,
            "topic_title": topic_title,
            "post_count": 0,
            "decision": decision.to_dict(),
            "draft": draft.to_dict(),
            "memory_hits": {},
            "persona_modules": list(self.enabled_personas),
            "planner_prompt_preview": "",
            "replyer_prompt_preview": "",
        }

    def _should_send_reply(self, forum_client: ForumClient, result: DryRunResult) -> bool:
        decision = result["decision"]
        draft = result["draft"]
        if not self.allow_send_reply:
            return False
        if self.require_approval_before_send:
            return False
        if getattr(forum_client, "read_only", False):
            return False
        if not decision.should_reply or draft.skipped:
            return False
        return bool(draft.content.strip())

    def _should_queue_for_approval(self, forum_client: ForumClient, result: DryRunResult) -> bool:
        decision = result["decision"]
        draft = result["draft"]
        if not self.allow_send_reply:
            return False
        if not self.require_approval_before_send:
            return False
        if getattr(forum_client, "read_only", False):
            return False
        if not decision.should_reply or draft.skipped:
            return False
        return bool(draft.content.strip())

    def _should_shadow_reply(self, result: DryRunResult) -> bool:
        decision = result["decision"]
        draft = result["draft"]
        if not self.shadow_mode:
            return False
        if not decision.should_reply or draft.skipped:
            return False
        return bool(draft.content.strip())

    @staticmethod
    def _should_run_memory_chain(decision: PlannerDecision) -> bool:
        normalized = decision.memory_action.strip().lower()
        return bool(normalized) and normalized not in {"none", "skip", "false", "no", "noop"}

    @staticmethod
    def _json_block(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def _normalize_memory_text(value: str) -> str:
        return normalize_memory_text(value)

    @classmethod
    def _extract_memory_command_content(cls, text: str) -> str | None:
        match = cls._MEMORY_COMMAND_PATTERN.match(text)
        if match is None:
            return None
        return cls._normalize_memory_text(match.group("content"))

    @classmethod
    def _extract_user_memory_updates(cls, payload: dict[str, object]) -> list[tuple[str, str, float]]:
        updates = extract_user_memory_updates(payload)
        return [(update.username, update.memory_text, update.confidence) for update in updates]

    @classmethod
    def _extract_self_memory_update(cls, payload: dict[str, object]) -> tuple[str, float] | None:
        self_update = extract_self_memory_update(payload)
        if self_update is None:
            return None
        return self_update.memory_text, self_update.confidence

    @staticmethod
    def _build_reply_memory_block(prompt_bundle: PromptBundle) -> str:
        memory_hits = {username: memory for username, memory in prompt_bundle["user_memories"].items() if memory.strip()}
        if not prompt_bundle["self_memory"].strip() and not memory_hits:
            return ""
        return (
            f"Self memory:\n{prompt_bundle['self_memory'] or '(empty)'}\n\n"
            + f"Relevant user memories:\n{json.dumps(memory_hits, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        )

    @staticmethod
    def _decision_prompt_summary(decision: PlannerDecision) -> str:
        return (
            f"should_reply: {decision.should_reply}\n"
            f"priority: {decision.priority}\n"
            f"target_username: {decision.target_username or '(none)'}\n"
            f"target_post_number: {decision.target_post_number if decision.target_post_number is not None else '(none)'}\n"
            f"reason: {decision.reason}\n"
            f"style_notes: {decision.style_notes}\n"
            f"memory_action: {decision.memory_action}"
        )

    def _build_memory_command_user_prompt(
        self,
        prompt_bundle: PromptBundle,
        *,
        command_content: str,
        command_author: str,
        topic_id: int,
    ) -> str:
        author = command_author.strip() or "(unknown)"
        return (
            "Return exactly one JSON object and nothing else.\n"
            + "Schema:\n"
            + '{"user_updates": [{"username": "...", "memory": "...", "confidence": 0.0}], "self_update": {"memory": "...", "confidence": 0.0} | null}\n'
            + "You are processing a direct memory command from a forum thread.\n"
            + "If the command asks to remember personal user preference/fact, use user_updates.\n"
            + "If the command asks to update bot behavior/persona/global rules, use self_update.\n"
            + "Only store durable, high-signal updates. If no change is needed, return {\"user_updates\": [], \"self_update\": null}.\n\n"
            + f"Topic id: {topic_id}\n"
            + f"Command author: {author}\n"
            + f"Command content after /memory+:\n{command_content}\n\n"
            + f"Current self memory:\n{prompt_bundle['self_memory'] or '(empty)'}\n\n"
            + f"Current user memories:\n{self._json_block(prompt_bundle['user_memories'])}\n\n"
            + f"Thread context:\n{prompt_bundle['planner_context']}"
        )

    def _persist_memory_payload(self, payload: dict[str, object]) -> tuple[int, bool]:
        result = self.memory_service.apply_memory_updates(
            user_updates=extract_user_memory_updates(payload),
            self_update=extract_self_memory_update(payload),
            user_confidence_threshold=self._MEMORY_UPDATE_CONFIDENCE_THRESHOLD,
            self_confidence_threshold=self._SELF_MEMORY_UPDATE_CONFIDENCE_THRESHOLD,
        )
        return result.updated_user_count, result.self_memory_updated

    def _build_memory_user_prompt(
        self,
        prompt_bundle: PromptBundle,
        decision: PlannerDecision,
        draft: ReplyDraft,
    ) -> str:
        if not self._should_run_memory_chain(decision):
            return ""
        return (
            "Return exactly one JSON object and nothing else.\n"
            + "Schema:\n"
            + '{"user_updates": [{"username": "...", "memory": "...", "confidence": 0.0}], "self_update": {"memory": "...", "confidence": 0.0} | null}\n'
            + "Only store durable, high-signal memory updates supported by the thread. If nothing should change, return {\"user_updates\": [], \"self_update\": null}.\n\n"
            + f"Planner decision:\n{self._json_block(decision.to_dict())}\n\n"
            + f"Draft reply:\n{draft.content or '(empty)'}\n\n"
            + f"Current self memory:\n{prompt_bundle['self_memory'] or '(empty)'}\n\n"
            + f"Current user memories:\n{self._json_block(prompt_bundle['user_memories'])}\n\n"
            + f"Thread context:\n{prompt_bundle['planner_context']}"
        )

    async def _execute_memory_chain(self, result: DryRunResult) -> None:
        if self.llm_client is None or not self.llm_client.is_route_available("memory"):
            return
        memory_prompts = result["debug_prompts"]["memory"]
        if not memory_prompts["user"].strip():
            return
        response = await self.llm_client.chat(
            "memory",
            memory_prompts["system"],
            memory_prompts["user"],
            temperature=0.2,
        )
        if response is None:
            return
        payload = self.llm_client.parse_json_object(response.content)
        if not isinstance(payload, dict):
            return
        _ = self._persist_memory_payload(payload)

    async def _execute_memory_command(
        self,
        *,
        topic_id: int,
        posts: list[ForumPost],
        trigger_reason: str,
        command_content: str,
        command_author: str,
    ) -> str:
        if self.llm_client is None or not self.llm_client.is_route_available("memory"):
            return "memory route unavailable"
        prompt_bundle = self._build_prompt_bundle(topic_id, posts, trigger_reason)
        memory_user_prompt = self._build_memory_command_user_prompt(
            prompt_bundle,
            command_content=command_content,
            command_author=command_author,
            topic_id=topic_id,
        )
        response = await self.llm_client.chat(
            "memory",
            prompt_bundle["memory_system_prompt"],
            memory_user_prompt,
            temperature=0.2,
        )
        if response is None:
            return "memory command llm call failed"
        payload = self.llm_client.parse_json_object(response.content)
        if not isinstance(payload, dict):
            return "memory command returned invalid payload"
        user_updates, self_updated = self._persist_memory_payload(payload)
        if user_updates == 0 and not self_updated:
            return "memory command processed with no durable updates"
        return "memory command processed"

    def _build_prompt_bundle(self, topic_id: int, posts: list[ForumPost], trigger_reason: str) -> PromptBundle:
        def unique_names(names: list[str]) -> list[str]:
            seen_names: set[str] = set()
            ordered_names: list[str] = []
            for name in names:
                if name in seen_names:
                    continue
                seen_names.add(name)
                ordered_names.append(name)
            return ordered_names

        def unique_text(parts: list[str]) -> str:
            seen_parts: set[str] = set()
            ordered_parts: list[str] = []
            for part in parts:
                normalized = part.strip()
                if not normalized or normalized in seen_parts:
                    continue
                seen_parts.add(normalized)
                ordered_parts.append(normalized)
            return "\n\n".join(ordered_parts)

        def configured_module_names(route_name: str) -> list[str]:
            if route_name == "planner":
                route = self.prompt_modules.planner
            elif route_name == "replyer":
                route = self.prompt_modules.replyer
            elif route_name == "memory":
                route = self.prompt_modules.memory
            else:
                raise ValueError(f"Unknown prompt route: {route_name}")
            return enabled_prompt_module_names(route)

        def compose_modules(route_name: str) -> str:
            parts: list[str] = []
            missing_names: list[str] = []
            for name in configured_module_names(route_name):
                if self.prompt_loader.exists(name):
                    parts.append(self.prompt_loader.load(name).strip())
                    continue
                if self.persona_loader.exists(name):
                    parts.append(self.persona_loader.load(name).strip())
                    continue
                missing_names.append(name)
            if missing_names:
                missing_display = ", ".join(missing_names)
                raise ValueError(f"Prompt route '{route_name}' references missing modules: {missing_display}")
            return unique_text(parts)

        def configured_personas(route_name: str) -> list[str]:
            personas: list[str] = []
            for name in configured_module_names(route_name):
                if self.persona_loader.exists(name):
                    personas.append(name.removesuffix(".md"))
            return unique_names(personas)

        usernames = sorted(
            username
            for post in posts
            if isinstance((username := post.get("username")), str) and username
        )
        user_memories = self.memory_service.get_user_memory(usernames)
        self_memory = self.memory_service.get_self_memory()
        planner_context = self.context_builder.build_for_planner(topic_id, posts)
        planner_personas = configured_personas("planner")
        replyer_personas = configured_personas("replyer")
        memory_personas = configured_personas("memory")
        include_core_in_planner = "core" not in planner_personas
        include_core_in_replyer = "core" not in replyer_personas
        include_core_in_memory = "core" not in memory_personas
        core_persona = self.persona_loader.compose(["core"], always_include_core=True)
        planner_prompt = compose_modules("planner")
        planner_system_prompt = unique_text(
            [self.persona_loader.compose(["core"]) if include_core_in_planner else "", planner_prompt]
        )
        reply_persona_names = unique_names(self.enabled_personas + replyer_personas)
        reply_persona = self.persona_loader.compose(reply_persona_names, always_include_core=True)
        reply_extras = [name for name in reply_persona_names if name != "core"]
        reply_extra_persona = self.persona_loader.compose(reply_extras) if reply_extras else ""
        replyer_context = self.context_builder.build_for_replyer(topic_id, posts)
        replyer_prompt = compose_modules("replyer")
        replyer_system_prompt = unique_text(
            [self.persona_loader.compose(["core"]) if include_core_in_replyer else "", replyer_prompt]
        )
        memory_prompt = compose_modules("memory")
        memory_system_prompt = unique_text(
            [self.persona_loader.compose(["core"]) if include_core_in_memory else "", memory_prompt]
        )
        planner_input = PlannerInput(
            trigger_reason=trigger_reason,
            topic_id=topic_id,
            posts=posts,
            user_memories=user_memories,
            self_memory=self_memory,
        )
        planner_user_prompt = (
            "Return one JSON object with keys: should_reply, priority, target_username, "
            "target_post_number, reason, style_notes, memory_action.\n\n"
            f"Trigger reason: {planner_input.trigger_reason}\n"
            f"Topic id: {planner_input.topic_id}\n"
            f"Self memory: {planner_input.self_memory or '(empty)'}\n"
            f"User memories: {planner_input.user_memories or {}}\n"
            f"Context:\n{planner_context.content}"
        )
        return {
            "planner_input": planner_input,
            "planner_system_prompt": planner_system_prompt,
            "planner_user_prompt": planner_user_prompt,
            "replyer_system_prompt": replyer_system_prompt,
            "memory_system_prompt": memory_system_prompt,
            "replyer_context": replyer_context.content,
            "core_persona": core_persona,
            "reply_persona": reply_persona,
            "reply_extra_persona": reply_extra_persona,
            "self_memory": self_memory,
            "user_memories": user_memories,
            "planner_context": planner_context.content,
        }

    async def dry_run(self, topic_id: int, posts: list[ForumPost], trigger_reason: str) -> DryRunResult:
        prompt_bundle = self._build_prompt_bundle(topic_id, posts, trigger_reason)
        model_routes: dict[str, RouteDescription | None] = {
            "planner": self.llm_client.describe_route("planner") if self.llm_client else None,
            "replyer": self.llm_client.describe_route("replyer") if self.llm_client else None,
            "memory": self.llm_client.describe_route("memory") if self.llm_client else None,
        }
        decision = await self.planner.decide(
            prompt_bundle["planner_input"],
            prompt_bundle["planner_context"],
            llm_client=self.llm_client,
            system_prompt=prompt_bundle["planner_system_prompt"],
            user_prompt=prompt_bundle["planner_user_prompt"],
            route_name="planner",
        )
        reply_memory_block = self._build_reply_memory_block(prompt_bundle)
        replyer_user_prompt = (
            (
                f"Additional persona modules:\n{prompt_bundle['reply_extra_persona']}\n\n"
                if prompt_bundle["reply_extra_persona"]
                else ""
            )
            + f"Decision:\n{self._decision_prompt_summary(decision)}\n\n"
            + (f"Memory context:\n{reply_memory_block}" if reply_memory_block else "")
            + f"Thread context:\n{prompt_bundle['replyer_context']}\n\n"
            + "Write a single forum reply only."
        )
        draft = await self.replyer.generate(
            decision,
            f"{prompt_bundle['replyer_system_prompt']}\n\n{prompt_bundle['replyer_context']}".strip(),
            prompt_bundle["reply_persona"],
            llm_client=self.llm_client,
            system_prompt=prompt_bundle["replyer_system_prompt"],
            user_prompt=replyer_user_prompt,
            route_name="replyer",
        )
        memory_user_prompt = self._build_memory_user_prompt(prompt_bundle, decision, draft)
        return {
            "decision": decision,
            "draft": draft,
            "user_memories": prompt_bundle["user_memories"],
            "model_routes": model_routes,
            "planner_prompt_preview": prompt_bundle["planner_system_prompt"][:200],
            "replyer_prompt_preview": prompt_bundle["replyer_system_prompt"][:200],
            "memory_prompt_preview": prompt_bundle["memory_system_prompt"][:200],
            "persona_modules": list(self.enabled_personas),
            "debug_prompts": {
                "planner": {
                    "system": prompt_bundle["planner_system_prompt"],
                    "user": prompt_bundle["planner_user_prompt"],
                },
                "replyer": {
                    "system": prompt_bundle["replyer_system_prompt"],
                    "user": replyer_user_prompt,
                },
                "memory": {
                    "system": prompt_bundle["memory_system_prompt"],
                    "user": memory_user_prompt,
                },
            },
        }

    async def debug_topic(
        self, forum_client: ForumClient, topic_id: int, trigger_reason: str = "manual_debug"
    ) -> DebugTopicResult:
        topic = await forum_client.get_topic(topic_id)
        highest_post_number = int(topic.get("highest_post_number") or 0)
        posts = await forum_client.get_topic_selected_posts(
            topic_id,
            recent_post_limit=self.context_builder.forum_recent_post_limit(),
        )
        result = await self.dry_run(topic_id, posts, trigger_reason)
        return {
            "topic_id": topic_id,
            "topic_title": topic.get("title", ""),
            "highest_post_number": highest_post_number,
            "post_count": len(posts),
            "decision": result["decision"].to_dict(),
            "draft": result["draft"].to_dict(),
            "debug_prompts": result["debug_prompts"],
            "model_routes": result["model_routes"],
            "persona_modules": result["persona_modules"],
            "memory_hits": {key: value for key, value in result["user_memories"].items() if value},
        }

    async def process_event(
        self, forum_client: ForumClient, event: dict[str, object], event_id: int | None = None
    ) -> ProcessEventResult:
        topic_id_value = event["topic_id"]
        topic_id = topic_id_value if isinstance(topic_id_value, int) else int(str(topic_id_value))
        trigger_reason_value = event["reason"]
        trigger_reason = trigger_reason_value if isinstance(trigger_reason_value, str) else str(trigger_reason_value)
        topic = await forum_client.get_topic(topic_id)
        topic_title = str(topic.get("title", ""))
        highest_post_number = int(topic.get("highest_post_number") or 0)
        if self.database.is_topic_banned(topic_id):
            return self._record_short_circuit_run(
                event_id=event_id,
                topic_id=topic_id,
                topic_title=topic_title,
                trigger_reason=trigger_reason,
                highest_post_number=highest_post_number,
                action="banned",
                reason="topic is banned",
            )
        if self.panic_switch:
            return self._record_short_circuit_run(
                event_id=event_id,
                topic_id=topic_id,
                topic_title=topic_title,
                trigger_reason=trigger_reason,
                highest_post_number=highest_post_number,
                action="panic_skip",
                reason="panic switch enabled",
            )
        if self._is_blackout_active():
            return self._record_short_circuit_run(
                event_id=event_id,
                topic_id=topic_id,
                topic_title=topic_title,
                trigger_reason=trigger_reason,
                highest_post_number=highest_post_number,
                action="blackout_skip",
                reason="blackout window active",
            )
        if topic_id in self._normalized_muted_topic_ids():
            return self._record_short_circuit_run(
                event_id=event_id,
                topic_id=topic_id,
                topic_title=topic_title,
                trigger_reason=trigger_reason,
                highest_post_number=highest_post_number,
                action="muted_topic",
                reason="topic is muted",
            )
        if self._is_topic_in_cooldown(topic_id):
            return self._record_short_circuit_run(
                event_id=event_id,
                topic_id=topic_id,
                topic_title=topic_title,
                trigger_reason=trigger_reason,
                highest_post_number=highest_post_number,
                action="cooldown_skip",
                reason="topic cooldown active",
            )
        if self.database.has_pending_reply_for_topic(topic_id):
            return self._record_short_circuit_run(
                event_id=event_id,
                topic_id=topic_id,
                topic_title=topic_title,
                trigger_reason=trigger_reason,
                highest_post_number=highest_post_number,
                action="pending_approval_skip",
                reason="topic already has pending approval",
            )
        posts = await forum_client.get_topic_selected_posts(
            topic_id,
            recent_post_limit=self.context_builder.forum_recent_post_limit(),
        )
        for post in posts:
            raw_text = post.get("raw_text", "")
            raw_text_value = raw_text if isinstance(raw_text, str) else ""
            if self.ban_service.contains_ban_command(raw_text_value):
                self.database.add_topic_ban(topic_id, "ban command detected")
                return self._record_short_circuit_run(
                    event_id=event_id,
                    topic_id=topic_id,
                    topic_title=topic_title,
                    trigger_reason=trigger_reason,
                    highest_post_number=highest_post_number,
                    action="banned",
                    reason="ban command detected",
                )
            memory_command_content = self._extract_memory_command_content(raw_text_value)
            if memory_command_content is not None:
                command_author_value = post.get("username")
                command_author = command_author_value if isinstance(command_author_value, str) else ""
                reason = await self._execute_memory_command(
                    topic_id=topic_id,
                    posts=posts,
                    trigger_reason=trigger_reason,
                    command_content=memory_command_content,
                    command_author=command_author,
                )
                return self._record_short_circuit_run(
                    event_id=event_id,
                    topic_id=topic_id,
                    topic_title=topic_title,
                    trigger_reason=trigger_reason,
                    highest_post_number=highest_post_number,
                    action="memory_command",
                    reason=reason,
                )
        result = await self.dry_run(topic_id, posts, trigger_reason)
        self.database.note_topic_seen(topic_id, highest_post_number)
        action = "reply" if result["decision"].should_reply else "skip"
        pending_reply_id: int | None = None
        if self._is_target_user_muted(result["decision"].target_username):
            action = "muted_user"
        elif self._should_shadow_reply(result):
            action = "shadow_reply"
        elif self._should_queue_for_approval(forum_client, result):
            try:
                pending_reply_id = self.database.create_pending_reply(
                    topic_id=topic_id,
                    topic_title=topic_title,
                    trigger_reason=trigger_reason,
                    target_post_number=result["decision"].target_post_number,
                    draft_content=result["draft"].content,
                    decision=result["decision"].to_dict(),
                )
                action = "reply_pending_approval"
            except RuntimeError:
                action = "pending_approval_skip"
        elif self._should_send_reply(forum_client, result):
            outbound_content = Replyer.sanitize_reply_text(result["draft"].content)
            if not outbound_content:
                action = "reply_error"
                self.database.record_pipeline_run(
                    event_id=event_id,
                    topic_id=topic_id,
                    topic_title=topic_title,
                    trigger_reason=trigger_reason,
                    action=action,
                    decision=result["decision"].to_dict(),
                    draft_content=result["draft"].content,
                )
                return {
                    "action": action,
                    "pending_reply_id": pending_reply_id,
                    "topic_id": topic_id,
                    "topic_title": topic_title,
                    "post_count": len(posts),
                    "decision": result["decision"].to_dict(),
                    "draft": result["draft"].to_dict(),
                    "memory_hits": {key: value for key, value in result["user_memories"].items() if value},
                    "persona_modules": result["persona_modules"],
                    "planner_prompt_preview": result["planner_prompt_preview"],
                    "replyer_prompt_preview": result["replyer_prompt_preview"],
                }
            try:
                reply_response = await forum_client.reply(
                    topic_id,
                    outbound_content,
                    result["decision"].target_post_number,
                )
            except Exception:
                self.database.record_pipeline_run(
                    event_id=event_id,
                    topic_id=topic_id,
                    topic_title=topic_title,
                    trigger_reason=trigger_reason,
                    action="reply_error",
                    decision=result["decision"].to_dict(),
                    draft_content=result["draft"].content,
                )
                raise
            reply_post_id_raw = reply_response.get("id")
            reply_post_id = reply_post_id_raw if isinstance(reply_post_id_raw, int) else None
            self.database.record_reply(
                topic_id,
                outbound_content,
                reply_post_id,
                result["decision"].target_post_number,
            )
            await self._execute_memory_chain(result)
            action = "reply_sent"
        self.database.record_pipeline_run(
            event_id=event_id,
            topic_id=topic_id,
            topic_title=topic_title,
            trigger_reason=trigger_reason,
            action=action,
            decision=result["decision"].to_dict(),
            draft_content=result["draft"].content,
        )
        return {
            "action": action,
            "pending_reply_id": pending_reply_id,
            "topic_id": topic_id,
            "topic_title": topic_title,
            "post_count": len(posts),
            "decision": result["decision"].to_dict(),
            "draft": result["draft"].to_dict(),
            "memory_hits": {key: value for key, value in result["user_memories"].items() if value},
            "persona_modules": result["persona_modules"],
            "planner_prompt_preview": result["planner_prompt_preview"],
            "replyer_prompt_preview": result["replyer_prompt_preview"],
        }
