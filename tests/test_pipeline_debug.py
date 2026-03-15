from pathlib import Path

import pytest

from bot.context_builder import ContextBuilder
from bot.forum_client import ForumClient
from bot.memory_service import MemoryService
from bot.persona_loader import PersonaLoader
from bot.pipeline import Pipeline
from bot.planner import Planner
from bot.prompt_loader import PromptLoader
from bot.replyer import Replyer
from bot.ban_service import BanService
from bot.settings import CredentialsConfig, ForumConfig
from bot.llm_client import LlmClient
from bot.settings import ModelRoute, ProviderConfig
from bot.settings import PromptModuleEntry, PromptModulesConfig, PromptRouteConfig
from bot.persona_loader import PersonaLoader
from bot.prompt_loader import PromptLoader
from db.repositories import Database


class FakeForumClient(ForumClient):
    def __init__(self) -> None:
        super().__init__(
            ForumConfig(base_url="https://forum.example.com", retry=1, user_agent="ua", default_headers={}, reactions={}),
            CredentialsConfig(username="u", password="p"),
            read_only=True,
        )

    async def get_topic(self, topic_id: int) -> dict[str, object]:
        return {"title": "topic title", "highest_post_number": 3}

    async def get_topic_selected_posts(
        self,
        topic_id: int,
        *,
        include_first_post: bool = True,
        recent_post_limit: int = 50,
    ) -> list[dict[str, object]]:
        return [
            {"post_number": 1, "username": "alice", "reply_to_post_number": 0, "raw_text": "hello world"},
            {"post_number": 2, "username": "bob", "reply_to_post_number": 1, "raw_text": "reply here"},
        ]


@pytest.mark.anyio
async def test_debug_topic_returns_full_prompt_payload(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    persona_dir = tmp_path / "personas"
    _ = prompt_dir.mkdir()
    _ = persona_dir.mkdir()
    _ = (prompt_dir / "planner.md").write_text("# Planner\nplanner system", encoding="utf-8")
    _ = (prompt_dir / "replyer.md").write_text("# Replyer\nreplyer system", encoding="utf-8")
    _ = (prompt_dir / "style_rules.md").write_text("# Style\nstyle", encoding="utf-8")
    _ = (prompt_dir / "safety_rules.md").write_text("# Safety\nsafety", encoding="utf-8")
    _ = (prompt_dir / "memory_user_update.md").write_text("# Memory User\nuser memory rule", encoding="utf-8")
    _ = (prompt_dir / "memory_self_update.md").write_text("# Memory Self\nself memory rule", encoding="utf-8")
    _ = (persona_dir / "core.md").write_text("# Core\ncore persona", encoding="utf-8")
    _ = (persona_dir / "extra_1.md").write_text("# Extra\nextra persona", encoding="utf-8")

    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    pipeline = Pipeline(
        context_builder=ContextBuilder(planner_max_posts=20, replyer_max_posts=10),
        planner=Planner(),
        replyer=Replyer(),
        persona_loader=PersonaLoader(persona_dir),
        prompt_loader=PromptLoader(prompt_dir),
        llm_client=LlmClient(
            providers={"default": ProviderConfig(base_url="https://example.com", api_key="replace_me", timeout_seconds=10)},
            models={"planner": ModelRoute(provider="default", model="x"), "replyer": ModelRoute(provider="default", model="y")},
        ),
        ban_service=BanService("SuenMeow"),
        database=database,
        memory_service=MemoryService(database),
        enabled_personas=["core", "extra_1"],
        allow_send_reply=False,
    )

    result = await pipeline.debug_topic(FakeForumClient(), 123)
    planner_route = result["model_routes"]["planner"]
    replyer_route = result["model_routes"]["replyer"]

    assert result["topic_id"] == 123
    assert "planner" in result["debug_prompts"]
    assert "replyer" in result["debug_prompts"]
    assert "memory" in result["debug_prompts"]
    assert planner_route is not None
    assert replyer_route is not None
    assert planner_route["model"] == "x"
    assert replyer_route["model"] == "y"
    assert "core persona" in result["debug_prompts"]["planner"]["system"]
    assert "# Style" not in result["debug_prompts"]["planner"]["system"]
    assert "Core persona:" not in result["debug_prompts"]["planner"]["user"]
    assert "extra persona" not in result["debug_prompts"]["planner"]["user"]
    assert "Additional persona modules:" in result["debug_prompts"]["replyer"]["user"]
    assert "Trigger reason:" in result["debug_prompts"]["planner"]["user"]
    assert "Write a single forum reply only." in result["debug_prompts"]["replyer"]["user"]
    assert "reply_to=1" in result["debug_prompts"]["replyer"]["user"]
    assert "user memory rule" in result["debug_prompts"]["memory"]["system"]


@pytest.mark.anyio
async def test_debug_topic_uses_configured_prompt_module_order_and_enabled_flags(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    persona_dir = tmp_path / "personas"
    _ = prompt_dir.mkdir()
    _ = persona_dir.mkdir()
    _ = (prompt_dir / "planner.md").write_text("planner system", encoding="utf-8")
    _ = (prompt_dir / "replyer.md").write_text("replyer system", encoding="utf-8")
    _ = (prompt_dir / "style_rules.md").write_text("style rules", encoding="utf-8")
    _ = (prompt_dir / "safety_rules.md").write_text("safety rules", encoding="utf-8")
    _ = (prompt_dir / "memory_user_update.md").write_text("memory user rules", encoding="utf-8")
    _ = (persona_dir / "core.md").write_text("core persona", encoding="utf-8")
    _ = (persona_dir / "catgirl.md").write_text("catgirl persona", encoding="utf-8")

    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    pipeline = Pipeline(
        context_builder=ContextBuilder(planner_max_posts=20, replyer_max_posts=10),
        planner=Planner(),
        replyer=Replyer(),
        persona_loader=PersonaLoader(persona_dir),
        prompt_loader=PromptLoader(prompt_dir),
        llm_client=LlmClient(
            providers={"default": ProviderConfig(base_url="https://example.com", api_key="replace_me", timeout_seconds=10)},
            models={"planner": ModelRoute(provider="default", model="x"), "replyer": ModelRoute(provider="default", model="y")},
        ),
        ban_service=BanService("SuenMeow"),
        database=database,
        memory_service=MemoryService(database),
        enabled_personas=["core"],
        prompt_modules=PromptModulesConfig(
            planner=PromptRouteConfig(
                modules=[
                    PromptModuleEntry(name="safety_rules.md", enabled=True),
                    PromptModuleEntry(name="planner.md", enabled=True),
                ]
            ),
            replyer=PromptRouteConfig(
                modules=[
                    PromptModuleEntry(name="style_rules.md", enabled=False),
                    PromptModuleEntry(name="replyer.md", enabled=True),
                    PromptModuleEntry(name="safety_rules.md", enabled=True),
                ]
            ),
            memory=PromptRouteConfig(
                modules=[
                    PromptModuleEntry(name="catgirl.md", enabled=True),
                    PromptModuleEntry(name="memory_user_update.md", enabled=True),
                ]
            ),
        ),
        allow_send_reply=False,
    )

    result = await pipeline.debug_topic(FakeForumClient(), 123)

    planner_system = result["debug_prompts"]["planner"]["system"]
    replyer_system = result["debug_prompts"]["replyer"]["system"]
    memory_system = result["debug_prompts"]["memory"]["system"]
    assert planner_system.index("safety rules") < planner_system.index("planner system")
    assert "style rules" not in replyer_system
    assert "replyer system" in replyer_system
    assert "safety rules" in replyer_system
    assert memory_system.index("catgirl persona") < memory_system.index("memory user rules")


@pytest.mark.anyio
async def test_debug_topic_allows_persona_modules_in_replyer_chain_without_duplicate_core(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    persona_dir = tmp_path / "personas"
    _ = prompt_dir.mkdir()
    _ = persona_dir.mkdir()
    _ = (prompt_dir / "replyer.md").write_text("replyer system", encoding="utf-8")
    _ = (prompt_dir / "planner.md").write_text("planner system", encoding="utf-8")
    _ = (prompt_dir / "safety_rules.md").write_text("safety rules", encoding="utf-8")
    _ = (prompt_dir / "memory_user_update.md").write_text("memory user rules", encoding="utf-8")
    _ = (persona_dir / "core.md").write_text("core persona", encoding="utf-8")
    _ = (persona_dir / "catgirl.md").write_text("catgirl persona", encoding="utf-8")

    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    pipeline = Pipeline(
        context_builder=ContextBuilder(planner_max_posts=20, replyer_max_posts=10),
        planner=Planner(),
        replyer=Replyer(),
        persona_loader=PersonaLoader(persona_dir),
        prompt_loader=PromptLoader(prompt_dir),
        llm_client=LlmClient(
            providers={"default": ProviderConfig(base_url="https://example.com", api_key="replace_me", timeout_seconds=10)},
            models={"planner": ModelRoute(provider="default", model="x"), "replyer": ModelRoute(provider="default", model="y")},
        ),
        ban_service=BanService("SuenMeow"),
        database=database,
        memory_service=MemoryService(database),
        enabled_personas=["core"],
        prompt_modules=PromptModulesConfig(
            planner=PromptRouteConfig(modules=[PromptModuleEntry(name="planner.md", enabled=True)]),
            replyer=PromptRouteConfig(
                modules=[
                    PromptModuleEntry(name="core.md", enabled=True),
                    PromptModuleEntry(name="catgirl.md", enabled=True),
                    PromptModuleEntry(name="replyer.md", enabled=True),
                ]
            ),
            memory=PromptRouteConfig(modules=[PromptModuleEntry(name="memory_user_update.md", enabled=True)]),
        ),
        allow_send_reply=False,
    )

    result = await pipeline.debug_topic(FakeForumClient(), 123)

    replyer_system = result["debug_prompts"]["replyer"]["system"]
    assert replyer_system.count("core persona") == 1
    assert "catgirl persona" in replyer_system


@pytest.mark.anyio
async def test_debug_topic_raises_when_enabled_prompt_module_is_missing(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    persona_dir = tmp_path / "personas"
    _ = prompt_dir.mkdir()
    _ = persona_dir.mkdir()
    _ = (prompt_dir / "replyer.md").write_text("replyer system", encoding="utf-8")
    _ = (prompt_dir / "memory_user_update.md").write_text("memory user rules", encoding="utf-8")
    _ = (persona_dir / "core.md").write_text("core persona", encoding="utf-8")

    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    pipeline = Pipeline(
        context_builder=ContextBuilder(planner_max_posts=20, replyer_max_posts=10),
        planner=Planner(),
        replyer=Replyer(),
        persona_loader=PersonaLoader(persona_dir),
        prompt_loader=PromptLoader(prompt_dir),
        llm_client=LlmClient(
            providers={"default": ProviderConfig(base_url="https://example.com", api_key="replace_me", timeout_seconds=10)},
            models={"planner": ModelRoute(provider="default", model="x"), "replyer": ModelRoute(provider="default", model="y")},
        ),
        ban_service=BanService("SuenMeow"),
        database=database,
        memory_service=MemoryService(database),
        enabled_personas=["core"],
        prompt_modules=PromptModulesConfig(
            planner=PromptRouteConfig(modules=[PromptModuleEntry(name="planner.md", enabled=True)]),
            replyer=PromptRouteConfig(modules=[PromptModuleEntry(name="replyer.md", enabled=True)]),
            memory=PromptRouteConfig(modules=[PromptModuleEntry(name="memory_user_update.md", enabled=True)]),
        ),
        allow_send_reply=False,
    )

    with pytest.raises(ValueError, match="planner.*missing modules"):
        _ = await pipeline.debug_topic(FakeForumClient(), 123)


def test_prompt_loader_compose_raises_when_file_missing(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    _ = prompt_dir.mkdir()
    _ = (prompt_dir / "planner.md").write_text("planner system", encoding="utf-8")

    loader = PromptLoader(prompt_dir)

    with pytest.raises(ValueError, match="Prompt files not found: missing.md"):
        _ = loader.compose(["planner.md", "missing.md"])


def test_persona_loader_compose_raises_when_file_missing(tmp_path: Path) -> None:
    persona_dir = tmp_path / "personas"
    _ = persona_dir.mkdir()
    _ = (persona_dir / "core.md").write_text("core persona", encoding="utf-8")

    loader = PersonaLoader(persona_dir)

    with pytest.raises(ValueError, match="Persona files not found: helper.md"):
        _ = loader.compose(["core", "helper"], always_include_core=True)

