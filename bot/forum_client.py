from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
import logging
import re
from typing import Any

import httpx

from bot.settings import CredentialsConfig, ForumConfig


HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_BREAK_RE = re.compile(r"<(?:br|/p|/div|/li|/blockquote|/h[1-6])[^>]*>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UnreadNotification:
    notification_id: int
    topic_id: int | None
    notification_type: str
    is_direct_trigger: bool
    raw: dict[str, Any]


class ForumClient:
    NOTIFICATION_TYPE_MAP = {
        1: "mentioned",
        2: "replied",
        3: "quoted",
        5: "liked",
        6: "private_message",
        9: "linked",
        12: "group_mentioned",
    }

    def __init__(self, forum: ForumConfig, credentials: CredentialsConfig, *, read_only: bool = False) -> None:
        self.forum = forum
        self.credentials = credentials
        self.read_only = read_only
        self.csrf_token = ""
        self.client = httpx.AsyncClient(
            headers={
                "user-agent": forum.user_agent,
                **forum.default_headers,
            },
            follow_redirects=True,
            timeout=30.0,
        )

    @staticmethod
    def cooked_to_text(cooked: str) -> str:
        text = HTML_BREAK_RE.sub("\n", cooked)
        text = HTML_TAG_RE.sub(" ", text)
        text = WHITESPACE_RE.sub(" ", text)
        return text.replace(" \n", "\n").strip()

    @staticmethod
    def normalize_post(post: dict[str, Any]) -> dict[str, Any]:
        raw_text = ForumClient.cooked_to_text(post.get("cooked", ""))
        return {
            "id": post.get("id"),
            "topic_id": post.get("topic_id"),
            "post_number": post.get("post_number"),
            "reply_to_post_number": post.get("reply_to_post_number") or 0,
            "username": post.get("username", ""),
            "raw_text": raw_text,
            "cooked": post.get("cooked", ""),
            "created_at": post.get("created_at"),
        }

    @classmethod
    def classify_notification(cls, item: dict[str, Any]) -> tuple[str, bool]:
        notification_code_raw = item.get("notification_type")
        notification_code: int = notification_code_raw if isinstance(notification_code_raw, int) else -1
        notification_type = cls.NOTIFICATION_TYPE_MAP.get(notification_code, "generic")
        is_direct_trigger = notification_type in {"mentioned", "replied", "quoted"}
        return notification_type, is_direct_trigger

    async def aclose(self) -> None:
        await self.client.aclose()

    async def login(self) -> None:
        await self.client.get(f"{self.forum.base_url}/session/passkey/challenge.json")
        csrf_response = await self.client.get(f"{self.forum.base_url}/session/csrf")
        csrf_response.raise_for_status()
        self.csrf_token = csrf_response.json()["csrf"]
        response = await self.client.post(
            f"{self.forum.base_url}/session",
            headers={"x-csrf-token": self.csrf_token},
            data={
                "login": self.credentials.username,
                "password": self.credentials.password,
                "second_factor_method": "1",
                "timezone": "Asia/Shanghai",
            },
        )
        response.raise_for_status()

    @staticmethod
    def _parse_retry_after_seconds(retry_after: str | None) -> float:
        if retry_after is None:
            return 2.0
        stripped = retry_after.strip()
        if not stripped:
            return 2.0
        try:
            return max(0.0, float(stripped))
        except ValueError:
            pass
        try:
            parsed = parsedate_to_datetime(stripped)
        except (TypeError, ValueError):
            return 2.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (parsed.astimezone(timezone.utc) - now).total_seconds())

    async def request(self, method: str, path: str, *, retry_on_auth: bool = True, **kwargs: Any) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.forum.base_url}{path}"
        headers = dict(kwargs.pop("headers", {}))
        auth_retries_remaining = 1 if retry_on_auth else 0
        rate_retries_remaining = 1
        attempt = 1

        while True:
            request_headers = dict(headers)
            if self.csrf_token and "x-csrf-token" not in request_headers:
                request_headers["x-csrf-token"] = self.csrf_token
            response = await self.client.request(method, url, headers=request_headers, **kwargs)

            if response.status_code == 429 and rate_retries_remaining > 0:
                retry_after = response.headers.get("Retry-After")
                wait_seconds = self._parse_retry_after_seconds(retry_after)
                logger.warning(
                    "forum request rate limited; method=%s path=%s attempt=%s wait_seconds=%s",
                    method,
                    path,
                    attempt,
                    wait_seconds,
                )
                rate_retries_remaining -= 1
                attempt += 1
                await response.aclose()
                await asyncio.sleep(wait_seconds)
                continue

            if retry_on_auth and response.status_code in {401, 403} and auth_retries_remaining > 0:
                logger.warning(
                    "forum request unauthorized; relogin and retry; method=%s path=%s attempt=%s status_code=%s",
                    method,
                    path,
                    attempt,
                    response.status_code,
                )
                auth_retries_remaining -= 1
                attempt += 1
                await response.aclose()
                await self.login()
                headers.pop("x-csrf-token", None)
                continue

            break

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body_preview = self._response_body_preview(response)
            detail = f"response body: {body_preview}" if body_preview else "response body: (empty)"
            raise httpx.HTTPStatusError(
                f"{exc}. {detail}",
                request=exc.request,
                response=exc.response,
            ) from exc
        return response

    @staticmethod
    def _response_body_preview(response: httpx.Response, limit: int = 300) -> str:
        text = response.text.strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[:limit]}…"

    async def list_latest_topics(self, page: int = 0) -> list[dict[str, Any]]:
        suffix = f"?page={page}" if page else ""
        response = await self.request("GET", f"/latest.json{suffix}")
        payload = response.json()
        return payload.get("topic_list", {}).get("topics", [])

    async def get_unread_notifications(self) -> list[UnreadNotification]:
        # The HTML notifications page may return a login page even with status 200,
        # so the worker must use the JSON endpoint explicitly.
        response = await self.request(
            "GET",
            "/notifications.json?limit=50&recent=false&bump_last_seen_reviewable=true",
            headers={"accept": "application/json"},
        )
        payload = response.json()
        notifications = []
        for item in payload.get("notifications", []):
            if item.get("read") is True:
                continue
            notification_type, is_direct_trigger = self.classify_notification(item)
            notifications.append(
                UnreadNotification(
                    notification_id=int(item["id"]),
                    topic_id=item.get("topic_id"),
                    notification_type=notification_type,
                    is_direct_trigger=is_direct_trigger,
                    raw=item,
                )
            )
        return notifications

    async def mark_notification_read(self, notification_id: int) -> None:
        if self.read_only:
            return
        await self.request("PUT", "/notifications/mark-read", data={"id": str(notification_id)})

    async def get_topic(self, topic_id: int) -> dict[str, Any]:
        response = await self.request("GET", f"/t/{topic_id}.json?track_visit=true&forceLoad=true")
        return response.json()

    async def get_topic_selected_posts(
        self,
        topic_id: int,
        *,
        include_first_post: bool = True,
        recent_post_limit: int = 50,
    ) -> list[dict[str, Any]]:
        topic = await self.get_topic(topic_id)
        stream_ids = list(topic.get("post_stream", {}).get("stream", []))
        if not stream_ids:
            return []

        selected_ids: list[int] = []
        if include_first_post:
            selected_ids.append(int(stream_ids[0]))

        tail_ids = [int(post_id) for post_id in stream_ids[-recent_post_limit:]]
        for post_id in tail_ids:
            if post_id not in selected_ids:
                selected_ids.append(post_id)

        selected_posts = await self.get_posts(topic_id, selected_ids)
        return sorted(selected_posts, key=lambda post: int(post.get("post_number") or 0))

    async def get_posts(self, topic_id: int, post_ids: list[int]) -> list[dict[str, Any]]:
        if not post_ids:
            return []
        query = "&".join(f"post_ids[]={post_id}" for post_id in post_ids)
        response = await self.request("GET", f"/t/{topic_id}/posts.json?{query}&include_suggested=false")
        posts = response.json().get("post_stream", {}).get("posts", [])
        return [self.normalize_post(post) for post in posts]

    async def get_topic_posts_around(self, topic_id: int, post_number: int) -> list[dict[str, Any]]:
        response = await self.request("GET", f"/t/{topic_id}/{post_number}.json?include_suggested=false")
        posts = response.json().get("post_stream", {}).get("posts", [])
        return [self.normalize_post(post) for post in posts]

    async def reply(self, topic_id: int, raw: str, reply_to_post_number: int | None = None) -> dict[str, Any]:
        if self.read_only:
            raise RuntimeError("reply is disabled in read-only mode")
        payload: dict[str, Any] = {"raw": raw, "topic_id": topic_id}
        if reply_to_post_number:
            payload["reply_to_post_number"] = reply_to_post_number
        response = await self.request("POST", "/posts.json", json=payload)
        return response.json()
