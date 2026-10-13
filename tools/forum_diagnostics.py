"""Locate forum handshake failures; redact credentials and never print response documents."""
import asyncio
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import tomllib

from suenmeow.adapters import Discourse
from suenmeow.database import Database, KV
from suenmeow.security import Vault
from suenmeow.settings import Settings


async def main():
    settings = Settings.env()
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    with db.transaction() as s:
        conf = vault.open(s.get(KV, "connection:forum").data["cipher"])
    forum = Discourse(conf)
    result = {}
    def safe(value):
        value = str(value)
        for secret in [conf.get("password"), conf.get("api_key"), conf.get("username"), forum.csrf]:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"[\w.+-]+@[\w.-]+", "[redacted-email]", value)
        return value[:200]
    try:
        await forum.login()
        result["login"] = True
        for name, operation in [("notifications", forum.notifications), ("latest", forum.latest), ("length_settings", forum.reply_limit)]:
            try:
                data = await operation()
                result[name] = {"ok": True, "count_or_limit": len(data) if isinstance(data, list) else data}
            except Exception as exc:
                result[name] = {"ok": False, "type": type(exc).__name__, "path": exc.request.url.path if hasattr(exc, "request") else ""}
                if hasattr(exc, "response"):
                    result[name]["status"] = exc.response.status_code
                    try:
                        data = exc.response.json()
                        if isinstance(data, dict):
                            result[name]["errors"] = safe(data.get("errors") or data.get("error") or "")
                    except ValueError:
                        pass
        class Bootstrap(HTMLParser):
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if "preload" in values.get("id", ""):
                    result.setdefault("bootstrap", []).append({"tag": tag, "id": values["id"], "attributes": list(values)})
                    encoded = values.get("data-preloaded")
                    if encoded:
                        try:
                            payload = json.loads(encoded)
                            result["bootstrap_keys"] = list(payload)
                        except ValueError:
                            result["bootstrap_json"] = False
        response = await forum.client.get("/latest", headers={"Accept": "text/html", "X-Requested-With": "", "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
        result["html"] = {"status": response.status_code, "content_type": response.headers.get("content-type"), "bytes": len(response.content)}
        Bootstrap().feed(response.text)
    except Exception as exc:
        result["login"] = False
        result["failure"] = {"type": type(exc).__name__}
        if hasattr(exc, "request"):
            result["failure"]["path"] = exc.request.url.path
        if hasattr(exc, "response"):
            result["failure"]["status"] = exc.response.status_code
            try:
                data = exc.response.json()
                if isinstance(data, dict):
                    result["failure"]["errors"] = safe(data.get("errors") or data.get("error") or "")
            except ValueError:
                result["failure"]["json_error"] = False
    source = Path("/legacy/config/forum.toml")
    if source.exists():
        old = tomllib.loads(source.read_text())
        result["legacy_header_names"] = list(old.get("default_headers", {}))
    await forum.close()
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
