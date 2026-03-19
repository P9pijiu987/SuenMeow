from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request


router = APIRouter(prefix="/logs", tags=["日志"])


@router.get("", summary="查看日志文件列表")
def get_logs(request: Request) -> dict[str, object]:
    log_dir = request.app.state.paths.log_dir
    return {"files": sorted(path.name for path in log_dir.glob("*.log"))}


@router.get("/latest", summary="查看最新日志")
def get_latest_log(request: Request, lines: int = 200) -> dict[str, object]:
    log_dir = request.app.state.paths.log_dir
    log_files = sorted(log_dir.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not log_files:
        return {"file": None, "lines": []}
    latest = log_files[0]
    content = _tail_lines(latest, lines)
    return {"file": latest.name, "lines": content}


def _tail_lines(path: Path, lines: int) -> list[str]:
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return all_lines[-max(lines, 1) :]
