from pathlib import Path

from bot.persona_loader import PersonaLoader
from bot.prompt_loader import PromptLoader


def test_prompt_loader_searches_public_directory(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    legacy_dir = tmp_path / "prompts_public"
    _ = prompt_dir.mkdir()
    _ = legacy_dir.mkdir()
    _ = (prompt_dir / "base.md").write_text("base", encoding="utf-8")
    _ = (legacy_dir / "new.md").write_text("new", encoding="utf-8")

    loader = PromptLoader(prompt_dir, extra_prompt_dirs=[legacy_dir])
    assert loader.exists("base.md") is True
    assert loader.exists("new.md") is True
    assert loader.load("new.md") == "new"
    assert "new.md" in loader.available()


def test_persona_loader_searches_public_directory(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    legacy_dir = tmp_path / "personas_public"
    _ = prompt_dir.mkdir()
    _ = legacy_dir.mkdir()
    _ = (prompt_dir / "core.md").write_text("core", encoding="utf-8")
    _ = (legacy_dir / "helper.md").write_text("helper", encoding="utf-8")

    loader = PersonaLoader(prompt_dir, extra_persona_dirs=[legacy_dir])
    assert loader.exists("core") is True
    assert loader.exists("helper") is True
    assert loader.load("helper") == "helper"
    assert "helper" in loader.available()
