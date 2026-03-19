from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone
import json
import logging
from pathlib import Path

from bot.activity_worker import ActivityWorker
from bot.ban_service import BanService
from bot.budget_service import BudgetService
from bot.context_builder import ContextBuilder
from bot.forum_client import ForumClient
from bot.llm_client import LlmClient
from bot.memory_service import MemoryService
from bot.notification_worker import NotificationWorker
from bot.persona_loader import PersonaLoader
from bot.pipeline import DebugTopicResult
from bot.pipeline import Pipeline
from bot.pipeline import ProcessEventResult
from bot.planner import Planner
from bot.prompt_loader import PromptLoader
from bot.replyer import Replyer
from bot.settings import load_settings
from bot.settings import PUBLIC_PERSONAS_DIRNAME
from bot.settings import PUBLIC_PROMPTS_DIRNAME
from bot.settings import Settings
from db.repositories import Database


logger = logging.getLogger(__name__)


class TriggerEngine:
    MAX_API_EVENT_FAILURES = 2

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.forum_client = ForumClient(
            settings.forum,
            settings.credentials,
            read_only=settings.runtime.read_only,
        )
        self.notification_worker = NotificationWorker(
            self.forum_client,
            self.database,
            mark_notifications_read=settings.runtime.mark_notifications_read,
        )
        self.activity_worker = ActivityWorker(self.forum_client, self.database, settings.thresholds)
        self.budget_service = BudgetService(
            daily_token_budget=settings.thresholds.budget.daily_token_budget,
            topic_token_budget=settings.thresholds.budget.topic_token_budget,
        )
        self.memory_service = MemoryService(self.database)
        self.llm_client = LlmClient(settings.providers, settings.models)
        self.pipeline = Pipeline(
            context_builder=ContextBuilder(
                planner_max_posts=settings.thresholds.context.planner_max_posts,
                replyer_max_posts=settings.thresholds.context.replyer_max_posts,
            ),
            planner=Planner(),
            replyer=Replyer(),
            persona_loader=PersonaLoader(
                settings.paths.root / "personas",
                extra_persona_dirs=[settings.paths.root / PUBLIC_PERSONAS_DIRNAME],
            ),
            prompt_loader=PromptLoader(
                settings.paths.root / "prompts",
                extra_prompt_dirs=[settings.paths.root / PUBLIC_PROMPTS_DIRNAME],
            ),
            llm_client=self.llm_client,
            ban_service=BanService(settings.credentials.username),
            database=self.database,
            memory_service=self.memory_service,
            enabled_personas=settings.personas.enabled,
            prompt_modules=settings.prompt_modules,
            allow_send_reply=settings.runtime.allow_send_reply,
            require_approval_before_send=settings.runtime.require_approval_before_send,
            shadow_mode=settings.runtime.shadow_mode,
            panic_switch=settings.runtime.panic_switch,
            topic_cooldown_minutes=settings.runtime.topic_cooldown_minutes,
            blackout_start_hour=settings.runtime.blackout_start_hour,
            blackout_end_hour=settings.runtime.blackout_end_hour,
            muted_topic_ids=settings.runtime.muted_topic_ids,
            muted_usernames=settings.runtime.muted_usernames,
        )
        self._is_logged_in = False
        self._settings_snapshot = self._build_settings_snapshot(settings.paths.config_dir)

    @staticmethod
    def _build_settings_snapshot(config_dir: Path) -> dict[str, int]:
        return {
            path.name: path.stat().st_mtime_ns
            for path in sorted(config_dir.glob("*.toml"))
            if path.is_file()
        }

    async def _reload_settings_if_needed(self) -> bool:
        current_snapshot = self._build_settings_snapshot(self.settings.paths.config_dir)
        if current_snapshot == self._settings_snapshot:
            return False

        changed_files = sorted(
            name
            for name in set(self._settings_snapshot) | set(current_snapshot)
            if self._settings_snapshot.get(name) != current_snapshot.get(name)
        )
        try:
            new_settings = load_settings(self.settings.paths)
        except Exception:
            logger.exception(
                "worker settings reload failed; keeping previous settings; changed_files=%s",
                ", ".join(changed_files) or "(unknown)",
            )
            return False

        await self._apply_settings(new_settings)
        self._settings_snapshot = current_snapshot
        logger.info("worker settings reloaded from disk; changed_files=%s", ", ".join(changed_files) or "(unknown)")
        return True

    async def reload_settings_if_needed(self) -> bool:
        return await self._reload_settings_if_needed()

    async def _apply_settings(self, new_settings: Settings) -> None:
        old_settings = self.settings
        should_replace_forum_client = (
            old_settings.forum != new_settings.forum
            or old_settings.credentials != new_settings.credentials
        )

        self.settings = new_settings
        self.forum_client.read_only = new_settings.runtime.read_only
        self.notification_worker.mark_notifications_read = new_settings.runtime.mark_notifications_read
        self.activity_worker.thresholds = new_settings.thresholds
        self.budget_service.daily_token_budget = new_settings.thresholds.budget.daily_token_budget
        self.budget_service.topic_token_budget = new_settings.thresholds.budget.topic_token_budget
        self.llm_client.providers = new_settings.providers
        self.llm_client.models = new_settings.models
        self.pipeline.prompt_modules = new_settings.prompt_modules
        self.pipeline.enabled_personas = new_settings.personas.enabled
        self.pipeline.allow_send_reply = new_settings.runtime.allow_send_reply
        self.pipeline.require_approval_before_send = new_settings.runtime.require_approval_before_send
        self.pipeline.shadow_mode = new_settings.runtime.shadow_mode
        self.pipeline.panic_switch = new_settings.runtime.panic_switch
        self.pipeline.topic_cooldown_minutes = new_settings.runtime.topic_cooldown_minutes
        self.pipeline.blackout_start_hour = new_settings.runtime.blackout_start_hour
        self.pipeline.blackout_end_hour = new_settings.runtime.blackout_end_hour
        self.pipeline.muted_topic_ids = new_settings.runtime.muted_topic_ids
        self.pipeline.muted_usernames = new_settings.runtime.muted_usernames
        self.pipeline.context_builder.planner_max_posts = new_settings.thresholds.context.planner_max_posts
        self.pipeline.context_builder.replyer_max_posts = new_settings.thresholds.context.replyer_max_posts

        if not should_replace_forum_client:
            return

        await self.forum_client.aclose()
        self.forum_client = ForumClient(
            new_settings.forum,
            new_settings.credentials,
            read_only=new_settings.runtime.read_only,
        )
        self.notification_worker.forum_client = self.forum_client
        self.activity_worker.forum_client = self.forum_client
        self._is_logged_in = False

    async def _ensure_logged_in(self) -> None:
        if self._is_logged_in:
            return
        await self.forum_client.login()
        self._is_logged_in = True

    async def _process_pending_events(self) -> list[ProcessEventResult]:
        processed: list[ProcessEventResult] = []
        for event in self.database.list_unprocessed_events():
            # Budgeting happens before any expensive topic reads beyond the current event scope.
            budget_decision = self.budget_service.allow(estimated_topic_tokens=4000, estimated_daily_tokens=4000)
            if not budget_decision.allowed:
                self.database.mark_event_processed(int(event["id"]))
                logger.warning(
                    "trigger event skipped by budget guard; event_id=%s reason=%s",
                    event["id"],
                    budget_decision.reason,
                )
                continue
            try:
                result = await self.pipeline.process_event(self.forum_client, event["payload"], event_id=int(event["id"]))
            except Exception as exc:
                failure_count = self.database.record_event_failure(int(event["id"]), str(exc))
                logger.exception(
                    "trigger event processing failed; event_id=%s failure_count=%s",
                    event["id"],
                    failure_count,
                )
                if failure_count >= self.MAX_API_EVENT_FAILURES:
                    self.database.mark_event_processed(int(event["id"]))
                    logger.warning(
                        "trigger event reached retry limit and will not be retried again; event_id=%s failure_count=%s",
                        event["id"],
                        failure_count,
                    )
                continue
            self.database.mark_event_processed(int(event["id"]))
            if result is not None:
                processed.append(result)
        return processed

    def _is_blackout_active(self, now: datetime | None = None) -> bool:
        start_hour = self.settings.runtime.blackout_start_hour
        end_hour = self.settings.runtime.blackout_end_hour
        if start_hour is None or end_hour is None or start_hour == end_hour:
            return False
        current = now or datetime.now(timezone.utc)
        hour = current.astimezone(timezone.utc).hour
        if start_hour < end_hour:
            return start_hour <= hour < end_hour
        return hour >= start_hour or hour < end_hour

    async def run_once(self) -> None:
        _ = await self._reload_settings_if_needed()
        if self.settings.runtime.panic_switch:
            logger.warning("trigger pass skipped: panic_switch=true")
            return
        if self._is_blackout_active():
            logger.info(
                "trigger pass skipped: blackout window active; start_hour=%s end_hour=%s",
                self.settings.runtime.blackout_start_hour,
                self.settings.runtime.blackout_end_hour,
            )
            return
        await self._ensure_logged_in()
        notification_events = await self.notification_worker.scan()
        activity_events = await self.activity_worker.scan()
        processed_events = await self._process_pending_events()
        logger.info(
            "trigger pass complete: read_only=%s notifications=%s activity=%s processed=%s",
            self.settings.runtime.read_only,
            len(notification_events),
            len(activity_events),
            len(processed_events),
        )

    async def run_forever(self) -> None:
        await self._ensure_logged_in()
        try:
            while True:
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("trigger loop iteration failed")
                await asyncio.sleep(self.settings.polling.notification_interval_seconds)
        finally:
            await self.forum_client.aclose()

    async def debug_topics(self, topic_ids: list[int] | None = None, count: int = 2) -> list[DebugTopicResult]:
        await self._ensure_logged_in()
        try:
            selected_topic_ids = list(topic_ids or [])
            if not selected_topic_ids:
                latest = await self.forum_client.list_latest_topics()
                selected_topic_ids = [int(topic["id"]) for topic in latest[:count]]

            results: list[DebugTopicResult] = []
            for topic_id in selected_topic_ids[:count]:
                debug_result = await self.pipeline.debug_topic(self.forum_client, topic_id)
                logger.info("=== TOPIC DEBUG START ===")
                logger.info(
                    "topic_debug_meta=%s",
                    json.dumps(
                        {
                            "topic_id": debug_result["topic_id"],
                            "topic_title": debug_result["topic_title"],
                            "post_count": debug_result["post_count"],
                            "model_routes": debug_result["model_routes"],
                            "persona_modules": debug_result["persona_modules"],
                        },
                        ensure_ascii=False,
                    ),
                )
                logger.info("planner_system_prompt:\n%s", debug_result["debug_prompts"]["planner"]["system"])
                logger.info("planner_user_prompt:\n%s", debug_result["debug_prompts"]["planner"]["user"])
                logger.info("planner_decision=%s", json.dumps(debug_result["decision"], ensure_ascii=False, indent=2))
                logger.info("replyer_system_prompt:\n%s", debug_result["debug_prompts"]["replyer"]["system"])
                logger.info("replyer_user_prompt:\n%s", debug_result["debug_prompts"]["replyer"]["user"])
                logger.info("reply_draft=%s", json.dumps(debug_result["draft"], ensure_ascii=False, indent=2))
                logger.info("=== TOPIC DEBUG END ===")
                results.append(debug_result)
            return results
        finally:
            await self.forum_client.aclose()
