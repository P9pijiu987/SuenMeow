from __future__ import annotations

from pathlib import Path


class PersonaLoader:
    def __init__(self, persona_dir: Path, extra_persona_dirs: list[Path] | None = None) -> None:
        self.persona_dir: Path = persona_dir
        self.search_dirs: list[Path] = [persona_dir, *(extra_persona_dirs or [])]

    def _resolve_name(self, name: str) -> str:
        return Path(name).stem if name.endswith(".md") else name

    def _resolve_file(self, resolved_name: str) -> Path | None:
        filename = f"{resolved_name}.md"
        for directory in self.search_dirs:
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        return None

    def load(self, name: str) -> str:
        resolved_name = self._resolve_name(name)
        target = self._resolve_file(resolved_name)
        if target is None:
            raise ValueError(f"Persona file not found: {resolved_name}.md")
        return target.read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        resolved_name = self._resolve_name(name)
        return self._resolve_file(resolved_name) is not None

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
        names: set[str] = set()
        for directory in self.search_dirs:
            names.update(path.stem for path in directory.glob("*.md"))
        return sorted(names)

    def available_files(self) -> list[str]:
        names: set[str] = set()
        for directory in self.search_dirs:
            names.update(path.name for path in directory.glob("*.md"))
        return sorted(names)
