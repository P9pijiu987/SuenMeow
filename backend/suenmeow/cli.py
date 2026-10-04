import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import tomllib

from cryptography.fernet import Fernet
from sqlalchemy import select
import uvicorn

from .database import Account, Database, KV, Record, audit
from .domain import ForumConnection, Policy, Route
from .security import Vault, password_hash
from .service import ROUTES, publish, seed
from .settings import Settings


def initialize(settings, username, password_file=None):
    settings.key_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings.key_file.exists():
        fd = os.open(settings.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(Fernet.generate_key())
    db = Database(settings.database_url)
    db.migrate()
    with db.transaction() as s:
        existing = s.scalar(select(Account).where(Account.role == "admin"))
        if existing:
            aid = existing.id
        else:
            password = Path(password_file).read_text().strip() if password_file else getpass.getpass("New admin password (12+ characters): ")
            a = Account(username=username, password_hash=password_hash(password), role="admin")
            s.add(a)
            s.flush()
            aid = a.id
            audit(s, a.id, "initialized")
    seed(db, aid)
    normalize_legacy_endpoints(db, Vault(settings.key_file))
    print("Database initialized; worker remains paused. No credentials printed.")


def normalize_legacy_endpoints(db, vault):
    # Early v2 imports omitted the fact that legacy provider URLs were complete endpoints.
    with db.transaction() as s:
        if not s.get(KV, "migration:legacy"):
            return
        for route in ROUTES:
            row = s.get(KV, "connection:" + route)
            if not row or row.version != 1:
                continue
            data = vault.open(row.data["cipher"])
            if "endpoint_mode" not in data:
                if s.get(KV, "control").data["mode"] != "paused":
                    raise RuntimeError("Pause before normalizing imported endpoints")
                row.data, row.version = {"cipher": vault.seal({**data, "endpoint_mode": "complete"})}, row.version + 1
                audit(s, "migration", "legacy_endpoint_normalized", route)


def import_legacy(settings, source: Path):
    db, vault = Database(settings.database_url), Vault(settings.key_file)
    def config(name):
        p = source / "config" / (name + ".toml")
        return tomllib.loads(p.read_text()) if p.exists() else {}
    creds, forum, providers = config("credentials"), config("forum"), config("providers")
    creds = creds.get("forum", creds)
    models, modules = config("models"), config("prompt_modules")
    prompt_files = sorted((source / "prompts").rglob("*.md"))
    manifest = []
    with db.transaction() as s:
        if s.get(KV, "migration:legacy"):
            raise RuntimeError("Legacy import already completed; refusing duplicate import")
        control = s.get(KV, "control")
        if control.data["mode"] != "paused":
            raise RuntimeError("Pause the worker before importing")
        admin = s.scalar(select(Account).where(Account.role == "admin", Account.active.is_(True)))
        connection = ForumConnection(base_url=forum["base_url"], username=creds["username"], password=creds["password"]).model_dump()
        s.add(KV(key="connection:forum", data={"cipher": vault.seal(connection)}))
        for route in ROUTES:
            legacy_key = "webui" if route == "summary" and "summary" not in models else route
            model = models.get(legacy_key, models.get("replyer", {}))
            provider = providers.get(model.get("provider", "default"), {})
            route_data = Route(base_url=provider["base_url"], model=model["model"], api_key=provider["api_key"], endpoint_mode="complete").model_dump()
            s.add(KV(key="connection:" + route, data={"cipher": vault.seal(route_data)}))
        lookup = {}
        for p in prompt_files:
            content = p.read_text()
            r = Record(kind="module", owner=admin.id, title=p.name,
                       data={"content": content, "description": "从旧版保存的 prompt 导入", "persona": p.stem in config("personas").get("priority", {})})
            s.add(r)
            s.flush()
            lookup[p.name] = r.id
            manifest.append({"file": str(p.relative_to(source)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "module_id": r.id})
        pipeline = {}
        for route in ROUTES:
            entries = modules.get(route, {}).get("modules", [])
            if route == "summary" and not entries:
                entries = [{"name": "summary_prompt.md", "enabled": True}]
            names = [x["name"] for x in entries if x.get("enabled", True)]
            if not names or any(n not in lookup for n in names):
                raise RuntimeError(f"Unmapped prompt pipeline: {route}")
            pipeline[route] = [lookup[n] for n in names]
        s.get(KV, "pipeline").data = pipeline
        old_policy = config("thresholds")
        budget = old_policy.get("budget", {})
        triggers = old_policy.get("triggers", {})
        policy = Policy(daily_tokens=budget.get("daily_token_budget", 800000), topic_tokens=budget.get("topic_token_budget", 30000),
                        hot_min_new_posts=triggers.get("burst_reply_min", 5), burst_window_minutes=triggers.get("burst_window_minutes", 5),
                        hourly_new_reply_min=triggers.get("hourly_new_reply_min", 2), hourly_hot_reply_min=triggers.get("hourly_hot_reply_min", 10))
        s.get(KV, "policy").data = policy.model_dump()
        # Do not publish imported instructions automatically. Administrator reviews them in the new GUI.
        s.add(KV(key="migration:legacy", data={"manifest": manifest, "unmapped_configs": ["runtime", "scheduler", "personas", "webui"],
                                              "note": "Old runtime flags intentionally replaced with paused defaults; routes preserved."}))
        audit(s, admin.id, "legacy_import", str(len(manifest)), files=len(manifest))
    print(json.dumps({"imported_prompts": len(manifest), "routes": list(ROUTES), "state": "paused", "published": False}))


def main():
    parser = argparse.ArgumentParser(prog="suenmeow")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--username", default="admin")
    init.add_argument("--password-file")
    sub.add_parser("api")
    sub.add_parser("worker")
    imp = sub.add_parser("import-legacy")
    imp.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = Settings.env()
    if args.command == "init":
        initialize(settings, args.username, args.password_file)
    elif args.command == "api":
        from .api import create_app
        uvicorn.run(create_app(settings), host="0.0.0.0", port=8000, proxy_headers=False)
    elif args.command == "worker":
        from .worker import serve_worker
        asyncio.run(serve_worker(settings))
    elif args.command == "import-legacy":
        import_legacy(settings, args.source)


if __name__ == "__main__":
    main()
