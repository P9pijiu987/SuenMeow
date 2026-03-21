from pathlib import Path

import pytest

from bot.persona_loader import PersonaLoader
from bot.prompt_loader import PromptLoader


def test_prompt_loader_compose_raises_for_missing_files(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    _ = prompt_dir.mkdir()
    _ = (prompt_dir / "planner.md").write_text("planner body", encoding="utf-8")
    loader = PromptLoader(prompt_dir)

    with pytest.raises(ValueError, match=r"Prompt files not found: missing\.md"):
        _ = loader.compose(["planner.md", "missing.md"])


def test_persona_loader_compose_raises_for_missing_files(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    _ = prompt_dir.mkdir()
    _ = (prompt_dir / "core.md").write_text("core persona", encoding="utf-8")
    loader = PersonaLoader(prompt_dir)

    with pytest.raises(ValueError, match=r"Persona files not found: missing\.md"):
        _ = loader.compose(["missing"], always_include_core=True)


def test_persona_loader_compose_keeps_existing_core_and_enabled_personas(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    _ = prompt_dir.mkdir()
    _ = (prompt_dir / "core.md").write_text("core persona", encoding="utf-8")
    _ = (prompt_dir / "helper.md").write_text("helper persona", encoding="utf-8")
    loader = PersonaLoader(prompt_dir)

    composed = loader.compose(["helper"], always_include_core=True)

    assert composed == "core persona\n\nhelper persona"
