from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(prefix="/personas", tags=["人格"])


class MarkdownContentPayload(BaseModel):
    content: str


def _normalize_markdown_file(base_dir: Path, filename: str) -> Path:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".md":
        raise HTTPException(status_code=400, detail="Markdown 文件名不合法")
    return base_dir / filename


def _resolve_existing_markdown_file(base_dir: Path, filename: str) -> Path:
    path = _normalize_markdown_file(base_dir, filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="未找到人格文件")
    return path


@router.get("", summary="查看人格文件列表")
def list_personas(request: Request) -> dict[str, object]:
    persona_dir = request.app.state.paths.root / "personas"
    settings = request.app.state.settings
    files = sorted(path.name for path in persona_dir.glob("*.md"))
    return {
        "files": files,
        "enabled": settings.personas.enabled,
        "priority": settings.personas.priority,
    }


@router.get("/{filename}", summary="查看人格文件")
def get_persona(filename: str, request: Request) -> dict[str, object]:
    persona_dir = request.app.state.paths.root / "personas"
    path = _resolve_existing_markdown_file(persona_dir, filename)
    return {
        "file": filename,
        "content": path.read_text(encoding="utf-8"),
    }


@router.put("/{filename}", summary="创建或更新人格文件")
def update_persona(filename: str, payload: MarkdownContentPayload, request: Request) -> dict[str, object]:
    persona_dir = request.app.state.paths.root / "personas"
    path = _normalize_markdown_file(persona_dir, filename)
    path.write_text(payload.content, encoding="utf-8")
    return {
        "file": filename,
        "content": payload.content,
    }
