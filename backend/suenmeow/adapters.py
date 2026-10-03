from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from urllib.parse import quote

import httpx

from .domain import Policy
from .service import reserve, settle


class LoginRequired(Exception):
    pass


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "li", "div"):
            self.parts.append("\n")


class ClientSettingsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.settings = {}
        self.script_parts = None

    def load(self, encoded):
        data = json.loads(encoded)
        settings = data.get("siteSettings", {})
        self.settings = json.loads(settings) if isinstance(settings, str) else settings

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id") == "data-preloaded" and values.get("data-preloaded"):
            self.load(values["data-preloaded"])
        elif tag == "script" and values.get("id") == "data-preloaded" and values.get("type") == "application/json":
            self.script_parts = []

    def handle_data(self, data):
        if self.script_parts is not None:
            self.script_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script_parts is not None:
            self.load("".join(self.script_parts))
            self.script_parts = None


def plain(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return "".join(parser.parts).strip()


def timestamp(value) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0


class Discourse:
    """Session or API-key adapter. Writes are deliberately never retried here."""

    def __init__(self, connection: dict, transport=None):
        self.connection = connection
        self.csrf = ""
        self.client = httpx.AsyncClient(base_url=connection["base_url"], timeout=30,
                                       transport=transport, follow_redirects=False,
                                       headers={"Accept": "application/json", "User-Agent": "SuenMeow/2.0",
                                                "X-Requested-With": "XMLHttpRequest"})
        if connection.get("api_key"):
            self.client.headers.update({"Api-Key": connection["api_key"], "Api-Username": connection["username"]})
        self.public_client = httpx.AsyncClient(base_url=connection["base_url"], timeout=15, transport=transport,
                                               follow_redirects=False, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})

    async def close(self):
        await self.client.aclose()
        await self.public_client.aclose()

    async def login(self):
        if self.connection.get("api_key"):
            current = await self.read("/session/current.json")
            if current.get("current_user", {}).get("username", "").casefold() != self.connection["username"].casefold():
                raise LoginRequired("Forum API identity not verified")
            return
        # Discourse deployments can require establishing cookies before obtaining CSRF.
        await self.client.get("/session/passkey/challenge.json")
        r = await self.client.get("/session/csrf")
        r.raise_for_status()
        self.csrf = r.json()["csrf"]
        self.client.headers["X-CSRF-Token"] = self.csrf
        r = await self.client.post("/session", data={"login": self.connection["username"],
                                   "password": self.connection["password"], "timezone": "Asia/Shanghai"})
        r.raise_for_status()
        data = r.json()
        if data.get("error") or data.get("errors"):
            raise LoginRequired("Forum authentication failed")
        current = await self.read("/session/current.json")
        if current.get("current_user", {}).get("username", "").casefold() != self.connection["username"].casefold():
            raise LoginRequired("Forum identity not verified")

    async def read(self, path: str, params=None):
        r = await self.client.get(path, params=params)
        if r.status_code in (401, 403):
            raise LoginRequired("Forum session expired")
        r.raise_for_status()
        return r.json()

    async def notifications(self):
        # The chronological list is capped at 60; the recent list prioritizes unread items.
        data = await self.read("/notifications.json", {"limit": 60, "silent": "true"})
        return data.get("notifications", [])

    async def latest(self):
        return (await self.read("/latest.json")).get("topic_list", {}).get("topics", [])

    async def search(self, query: str, page=1):
        if not 1 <= page <= 3 or len(query) > 300:
            raise ValueError("Search limits exceeded")
        return await self.read("/search.json", {"q": query, "page": page})

    async def user_activity(self, username: str):
        return (await self.read("/user_actions.json", {"username": username, "filter": "5", "limit": 20})).get("user_actions", [])

    async def public_visible(self, topic: dict) -> bool:
        if topic.get("archetype") == "private_message":
            return False
        tid = int(topic["id"])
        response = await self.public_client.get(f"/t/{tid}.json")
        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("id") == tid and data.get("archetype") != "private_message":
                    return True
            except (ValueError, TypeError):
                pass
        # Member-only forums can explicitly expose unrestricted category metadata.
        categories = (await self.read("/categories.json")).get("category_list", {}).get("categories", [])
        category = next((c for c in categories if c["id"] == topic.get("category_id")), None)
        return bool(category and category.get("read_restricted") is False)

    async def topic(self, topic_id: int, limit: int):
        if topic_id <= 0:
            raise ValueError("Existing topic ID required")
        data = await self.read(f"/t/{topic_id}.json")
        stream = data.get("post_stream", {}).get("stream", [])
        ids = list(dict.fromkeys(stream[:1] + stream[-limit:]))
        if ids:
            result = await self.read(f"/t/{topic_id}/posts.json", [("post_ids[]", i) for i in ids])
            posts = result.get("post_stream", {}).get("posts", [])
        else:
            posts = data.get("post_stream", {}).get("posts", [])
        data["context"] = [{"id": p["id"], "number": p.get("post_number", 0), "username": p.get("username", ""),
                            "text": p.get("raw") or plain(p.get("cooked", "")), "created": p.get("created_at")}
                           for p in sorted(posts, key=lambda p: p.get("post_number", 0))
                           if p.get("post_type") == 1 and not p.get("hidden") and not p.get("deleted_at")]
        return data

    async def selected_posts(self, topic_id: int, post_ids: list[int]):
        if topic_id <= 0 or not 1 <= len(post_ids) <= 20 or any(i <= 0 for i in post_ids):
            raise ValueError("Invalid post selection")
        data = await self.read(f"/t/{topic_id}/posts.json", [("post_ids[]", i) for i in post_ids])
        return [{"id": p["id"], "number": p.get("post_number", 0), "username": p.get("username", ""),
                 "text": p.get("raw") or plain(p.get("cooked", "")), "created": p.get("created_at")}
                for p in data.get("post_stream", {}).get("posts", []) if p.get("id") in post_ids and p.get("topic_id", topic_id) == topic_id
                and p.get("post_type") == 1 and not p.get("hidden") and not p.get("deleted_at")]

    async def reply_limit(self):
        # Discourse preloads client settings into HTML; /site.json does not expose this field.
        response = await self.client.get("/latest", headers={"Accept": "text/html", "X-Requested-With": "",
                                                               "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
        response.raise_for_status()
        if len(response.content) > 2097152:
            raise ValueError("Forum settings response too large")
        parser = ClientSettingsParser()
        parser.feed(response.text)
        value = parser.settings.get("max_post_length")
        if not isinstance(value, int) or not 100 <= value <= 1000000:
            raise ValueError("Forum reply length could not be verified")
        return value

    async def reply(self, topic_id: int, text: str, reply_to=None):
        if topic_id <= 0 or not text.strip():
            raise ValueError("Only replies to existing topics are allowed")
        payload = {"topic_id": topic_id, "raw": text}
        if reply_to and int(reply_to) > 0:
            payload["reply_to_post_number"] = int(reply_to)
        r = await self.client.post("/posts.json", json=payload)
        # Even HTTP errors remain uncertain at the application boundary: never repost automatically.
        r.raise_for_status()
        return int(r.json()["id"])


class Models:
    def __init__(self, db, routes: dict, transport=None):
        self.db, self.routes = db, routes
        self.client = httpx.AsyncClient(timeout=90, transport=transport, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    def endpoint(self, conf):
        url = conf["base_url"].rstrip("/")
        return url if conf.get("endpoint_mode") == "complete" or url.endswith("/chat/completions") else url + "/chat/completions"

    async def tool_turn(self, messages, tools, topic_id, policy, task_id, task_limit, route="agent", tool_choice="auto"):
        conf = self.routes.get(route) or self.routes.get("planner")
        if not conf or not conf.get("supports_tools"):
            raise RuntimeError("请配置支持工具调用的 Agent 模型，并开启工具调用选项")
        reservation = len(json.dumps([messages, tools], ensure_ascii=False).encode()) + conf["max_output"] + 512
        usage_id = reserve(self.db, "agent", topic_id, reservation, policy, task_id, task_limit)
        actual = None
        try:
            response = await self.client.post(self.endpoint(conf),
                headers={"Authorization": "Bearer " + conf["api_key"]},
                json={"model": conf["model"], "messages": messages, "tools": tools, "tool_choice": tool_choice,
                      "max_tokens": conf["max_output"], "temperature": conf["temperature"]})
            response.raise_for_status()
            data = response.json()
            tokens = data.get("usage", {}).get("total_tokens")
            if isinstance(tokens, int) and tokens >= 0:
                actual = tokens
            choice = data["choices"][0]
            message = choice["message"]
            result = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("tool_calls"):
                result["tool_calls"] = message["tool_calls"]
                # Some compatible providers require this opaque payload on the next tool turn.
                # It remains in memory only; steps/results never store or expose it.
                if isinstance(message.get("reasoning_content"), str):
                    result["reasoning_content"] = message["reasoning_content"]
            settle(self.db, usage_id, actual)
            return result, choice.get("finish_reason") == "length"
        except Exception:
            settle(self.db, usage_id, actual, failed=True)
            raise

    async def complete(self, route: str, messages: list, topic_id: int, policy: Policy):
        conf = self.routes.get(route)
        if not conf:
            raise RuntimeError(f"Model route {route} not configured")
        # UTF-8 byte count bounds ordinary prompt tokenization more conservatively than chars/4.
        reservation = len(json.dumps(messages, ensure_ascii=False).encode()) + conf["max_output"] + 512
        usage_id = reserve(self.db, route, topic_id, reservation, policy)
        actual = None
        try:
            r = await self.client.post(self.endpoint(conf),
                                       headers={"Authorization": "Bearer " + conf["api_key"]},
                                       json={"model": conf["model"], "messages": messages,
                                             "max_tokens": conf["max_output"], "temperature": conf["temperature"]})
            r.raise_for_status()
            data = r.json()
            u = data.get("usage", {})
            if isinstance(u.get("total_tokens"), int) and u["total_tokens"] >= 0:
                actual = u["total_tokens"]
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("Model output truncated")
            text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError("Model returned empty content")
            settle(self.db, usage_id, actual)
            return text.strip()
        except Exception:
            settle(self.db, usage_id, actual, failed=True)
            raise


def json_output(text: str):
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1])
    return json.loads(text)
