from __future__ import annotations

from pathlib import Path


class PersonaLoader:
    def __init__(self, persona_dir: Path) -> None:
        self.persona_dir: Path = persona_dir

    def _resolve_name(self, name: str) -> str:
        return Path(name).stem if name.endswith(".md") else name

    def load(self, name: str) -> str:
        resolved_name = self._resolve_name(name)
        return (self.persona_dir / f"{resolved_name}.md").read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        resolved_name = self._resolve_name(name)
        return (self.persona_dir / f"{resolved_name}.md").is_file()

    def compose(self, enabled: list[str], *, always_include_core: bool = False) -> str:
        names = [self._resolve_name(name) for name in enabled]
        if always_include_core and "core" not in names:
            names.insert(0, "core")
        parts: list[str] = []
        seen: set[str] = set()
        missing_names: list[str] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            if not self.exists(name):
                missing_names.append(f"{name}.md")
                continue
            parts.append(self.load(name).strip())
        if missing_names:
            missing_display = ", ".join(missing_names)
            raise ValueError(f"Persona files not found: {missing_display}")
        return "\n\n".join(part for part in parts if part)

    def available(self) -> list[str]:
        return sorted(path.stem for path in self.persona_dir.glob("*.md"))

    def available_files(self) -> list[str]:
        return sorted(path.name for path in self.persona_dir.glob("*.md"))
