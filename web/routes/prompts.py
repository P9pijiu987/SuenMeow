from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bot.settings import ensure_prompt_storage
from bot.settings import write_prompt_file_with_backup


router = APIRouter(prefix="/prompts", tags=["提示词"])


def _read_markdown_with_fallback(path: Path) -> str:
    data = path.read_bytes()
    gb18030_text = data.decode("gb18030", errors="replace")
    try:
        utf8_text = data.decode("utf-8")
    except UnicodeDecodeError:
        return gb18030_text
    utf8_cjk = sum(1 for ch in utf8_text if "\u4e00" <= ch <= "\u9fff")
    gb18030_cjk = sum(1 for ch in gb18030_text if "\u4e00" <= ch <= "\u9fff")
    if gb18030_cjk > utf8_cjk:
        return gb18030_text
    return utf8_text


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
        raise HTTPException(status_code=404, detail="未找到提示词文件")
    return path


@router.get("", summary="查看提示词文件列表")
def list_prompts(request: Request) -> dict[str, object]:
    paths = request.app.state.paths
    ensure_prompt_storage(paths)
    prompt_dir = paths.prompt_dir
    return {"files": sorted(path.name for path in prompt_dir.glob("*.md"))}


@router.get("/{filename}", summary="查看提示词文件")
def get_prompt(filename: str, request: Request) -> dict[str, object]:
    paths = request.app.state.paths
    ensure_prompt_storage(paths)
    prompt_dir = paths.prompt_dir
    path = _resolve_existing_markdown_file(prompt_dir, filename)
    return {
        "file": filename,
        "content": _read_markdown_with_fallback(path),
    }


@router.put("/{filename}", summary="创建或更新提示词文件")
def update_prompt(filename: str, payload: MarkdownContentPayload, request: Request) -> dict[str, object]:
    paths = request.app.state.paths
    ensure_prompt_storage(paths)
    prompt_dir = paths.prompt_dir
    path = _normalize_markdown_file(prompt_dir, filename)
    _, _ = write_prompt_file_with_backup(paths, path.name, payload.content)
    return {
        "file": filename,
        "content": payload.content,
    }
