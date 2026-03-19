from __future__ import annotations

from pathlib import Path


class PromptLoader:
    def __init__(self, prompt_dir: Path, extra_prompt_dirs: list[Path] | None = None) -> None:
        self.prompt_dir: Path = prompt_dir
        self.search_dirs: list[Path] = [prompt_dir, *(extra_prompt_dirs or [])]

    def _resolve_file(self, name: str) -> Path | None:
        for directory in self.search_dirs:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return None

    def load(self, name: str) -> str:
        target = self._resolve_file(name)
        if target is None:
            raise ValueError(f"Prompt file not found: {name}")
        return target.read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        return self._resolve_file(name) is not None

    def available(self) -> list[str]:
        names: set[str] = set()
        for directory in self.search_dirs:
            names.update(path.name for path in directory.glob("*.md"))
        return sorted(names)

    def compose(self, names: list[str]) -> str:
        missing_names = [name for name in names if not self.exists(name)]
        if missing_names:
            missing_display = ", ".join(missing_names)
            raise ValueError(f"Prompt files not found: {missing_display}")
        return "\n\n".join(self.load(name).strip() for name in names)
