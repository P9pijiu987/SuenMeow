from __future__ import annotations

import secrets

from fastapi import HTTPException
from fastapi import Request
from fastapi import status


AUTH_COOKIE_NAME = "suenmeow_admin_session"


def _load_admin_credentials(root) -> tuple[str, str]:
    auth_file = root / "config" / "webui_admin_auth.toml"
    if not auth_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Admin auth not configured. Create config/webui_admin_auth.toml locally and DO NOT commit it to GitHub."
            ),
        )
    import tomllib

    data = tomllib.loads(auth_file.read_text(encoding="utf-8"))
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invalid admin auth file. username/password must be non-empty.",
        )
    return username, password


def verify_admin_credentials(root, username: str, password: str) -> bool:
    expected_user, expected_password = _load_admin_credentials(root)
    user_ok = secrets.compare_digest(username, expected_user)
    pass_ok = secrets.compare_digest(password, expected_password)
    return user_ok and pass_ok


def is_admin_logged_in(request: Request) -> bool:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    expected = getattr(request.app.state, "admin_session_token", None)
    return bool(token and expected and secrets.compare_digest(token, expected))


def require_admin_session(request: Request) -> None:
    if is_admin_logged_in(request):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录管理后台")
