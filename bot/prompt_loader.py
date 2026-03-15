from __future__ import annotations

from pathlib import Path


class PromptLoader:
    def __init__(self, prompt_dir: Path) -> None:
        self.prompt_dir: Path = prompt_dir

    def load(self, name: str) -> str:
        return (self.prompt_dir / name).read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        return (self.prompt_dir / name).is_file()

    def available(self) -> list[str]:
        return sorted(path.name for path in self.prompt_dir.glob("*.md"))

    def compose(self, names: list[str]) -> str:
        missing_names = [name for name in names if not self.exists(name)]
        if missing_names:
            missing_display = ", ".join(missing_names)
            raise ValueError(f"Prompt files not found: {missing_display}")
        return "\n\n".join(self.load(name).strip() for name in names)
