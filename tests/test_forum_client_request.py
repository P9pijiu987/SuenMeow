from __future__ import annotations

import httpx
import pytest

from bot.forum_client import ForumClient
from bot.settings import CredentialsConfig
from bot.settings import ForumConfig


def _make_client(*, transport: httpx.MockTransport) -> ForumClient:
    client = ForumClient(
        ForumConfig(base_url="https://forum.example.com", retry=1, user_agent="ua", default_headers={}, reactions={}),
        CredentialsConfig(username="u", password="p"),
    )
    client.client = httpx.AsyncClient(transport=transport)
    return client


@pytest.mark.anyio
async def test_request_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("bot.forum_client.asyncio.sleep", fake_sleep)
    client = _make_client(transport=httpx.MockTransport(handler))

    try:
        response = await client.request("GET", "/latest.json", retry_on_auth=False)
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleep_calls == [0.0]


@pytest.mark.anyio
async def test_request_retries_once_after_bad_csrf_login(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    request_headers: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        request_headers.append(request.headers.get("x-csrf-token"))
        if len(calls) == 1:
            return httpx.Response(403, text="BAD CSRF", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    client = _make_client(transport=httpx.MockTransport(handler))
    login_calls: list[str] = []

    async def fake_login() -> None:
        login_calls.append("login")
        client.csrf_token = "fresh-token"

    monkeypatch.setattr(client, "login", fake_login)

    try:
        response = await client.request("GET", "/latest.json")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert login_calls == ["login"]
    assert len(calls) == 2
    assert request_headers == [None, "fresh-token"]


@pytest.mark.anyio
async def test_request_notifications_403_rechecks_session_and_relogins_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/notifications.json":
            if calls.count("GET /notifications.json") == 1:
                return httpx.Response(403, text="forbidden", request=request)
            return httpx.Response(200, json={"notifications": []}, request=request)
        if request.url.path == "/session/current.json":
            return httpx.Response(200, json={"current_user": None}, request=request)
        return httpx.Response(500, request=request)

    client = _make_client(transport=httpx.MockTransport(handler))
    login_calls: list[str] = []

    async def fake_login() -> None:
        login_calls.append("login")

    monkeypatch.setattr(client, "login", fake_login)

    try:
        response = await client.request("GET", "/notifications.json")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert login_calls == ["login"]
    assert calls == [
        "GET /notifications.json",
        "GET /session/current.json",
        "GET /notifications.json",
    ]


@pytest.mark.anyio
async def test_request_notifications_403_retry_uses_fresh_csrf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    request_headers: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/notifications.json":
            request_headers.append(request.headers.get("x-csrf-token"))
            if len(request_headers) == 1:
                return httpx.Response(403, text="forbidden", request=request)
            return httpx.Response(200, json={"notifications": []}, request=request)
        if request.url.path == "/session/current.json":
            return httpx.Response(404, json={"error": "not found"}, request=request)
        return httpx.Response(500, request=request)

    client = _make_client(transport=httpx.MockTransport(handler))
    client.csrf_token = "stale-token"

    async def fake_login() -> None:
        client.csrf_token = "fresh-token"

    monkeypatch.setattr(client, "login", fake_login)

    try:
        response = await client.request("GET", "/notifications.json")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert request_headers == ["stale-token", "fresh-token"]


@pytest.mark.anyio
async def test_request_notifications_403_relogins_when_session_probe_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/notifications.json":
            if calls.count("GET /notifications.json") == 1:
                return httpx.Response(403, text="forbidden", request=request)
            return httpx.Response(200, json={"notifications": []}, request=request)
        if request.url.path == "/session/current.json":
            return httpx.Response(404, json={"error": "not found"}, request=request)
        return httpx.Response(500, request=request)

    client = _make_client(transport=httpx.MockTransport(handler))
    login_calls: list[str] = []

    async def fake_login() -> None:
        login_calls.append("login")

    monkeypatch.setattr(client, "login", fake_login)

    try:
        response = await client.request("GET", "/notifications.json")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert login_calls == ["login"]
    assert calls == [
        "GET /notifications.json",
        "GET /session/current.json",
        "GET /notifications.json",
    ]
