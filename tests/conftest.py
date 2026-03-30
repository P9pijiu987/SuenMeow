from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_LIB_PATH = PROJECT_ROOT / "build" / "lib"


def _normalize(path: str) -> str:
    return str(Path(path).resolve())


def _ensure_project_root_first() -> None:
    project_root_str = str(PROJECT_ROOT)
    normalized_project_root = _normalize(project_root_str)
    normalized_build_lib = _normalize(str(BUILD_LIB_PATH))

    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in sys.path:
        try:
            normalized_entry = _normalize(entry)
        except (OSError, RuntimeError):
            normalized_entry = entry
        if normalized_entry == normalized_build_lib:
            continue
        if normalized_entry in seen:
            continue
        seen.add(normalized_entry)
        cleaned.append(entry)

    if normalized_project_root not in seen:
        cleaned.insert(0, project_root_str)
    else:
        cleaned.sort(key=lambda item: 0 if _normalize(item) == normalized_project_root else 1)

    sys.path[:] = cleaned


def _purge_shadowed_bot_modules() -> None:
    normalized_build_lib = _normalize(str(BUILD_LIB_PATH))
    for name, module in list(sys.modules.items()):
        if name != "bot" and not name.startswith("bot."):
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        try:
            module_path = _normalize(module_file)
        except (OSError, RuntimeError):
            continue
        if module_path.startswith(normalized_build_lib):
            del sys.modules[name]


_ensure_project_root_first()
_purge_shadowed_bot_modules()
importlib.invalidate_caches()
