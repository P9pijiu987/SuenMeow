"""Passwordless login proven by a fresh one-to-one forum message; never sends messages."""
import secrets
from urllib.parse import quote, urljoin, urlsplit

import httpx
import pyotp
from fastapi import Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import delete, func, select

from .adapters import plain, timestamp
from .database import Account, ForumIdentity, ForumLogin, KV, LoginSession, Record, audit, locked, now
from .domain import Strict
from .security import LOGIN_CODE, client_address, digest, password_hash, same_token
from .workspace_api import unique_username


class ForumAuthSettings(Strict):
    enabled: bool
    allow_signup: bool


class ForumFinish(Strict):
    code: str = Field(default="", max_length=8)


def identity_view(s, account):
    identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == account.id))
    if not identity:
        return {}
    return {"display_name": identity.profile.get("name") or identity.profile["username"],
            "forum_user_id": identity.user_id, "avatar_url": "/api/auth/avatar/" + account.id}


def mount_forum_auth(app, db, vault, settings, user, admin):
    def connection(s):
        row = s.get(KV, "connection:forum")
        if not row:
            raise HTTPException(503, "论坛连接尚未配置")
        conf = vault.open(row.data["cipher"])
        return row.version, conf

    def attempt(s, request, attempt_id):
        row = s.scalar(select(ForumLogin).where(ForumLogin.id == attempt_id).with_for_update())
        cookie = request.cookies.get("sm_forum_login", "")
        if not row or not cookie or not same_token(row.browser_hash, digest(cookie)):
            raise HTTPException(404, "登录请求不存在，请在原浏览器重新发起")
        version, conf = connection(s)
        if row.expires <= now() or row.forum_version != version or not s.get(KV, "forum_auth").data["enabled"]:
            raise HTTPException(410, "登录请求已失效，请重新发起")
        return row, conf

    @app.get("/api/auth/forum")
    def available():
        with db.transaction() as s:
            config, row = s.get(KV, "forum_auth"), s.get(KV, "connection:forum")
            conf = vault.open(row.data["cipher"]) if row else {}
            return {"enabled": bool(config and config.data["enabled"] and row),
                    "allow_signup": bool(config and config.data["allow_signup"]),
                    "forum_url": conf.get("base_url", ""), "bot_username": conf.get("username", "")}

    @app.put("/api/auth/forum", dependencies=[Depends(admin)])
    def configure(body: ForumAuthSettings):
        with db.transaction() as s:
            locked(s, "forum_auth_lock")
            locked(s, "registration_lock")
            row = s.get(KV, "forum_auth")
            row.data, row.version = body.model_dump(), row.version + 1
            if not body.enabled:
                for item in s.scalars(select(ForumLogin).where(ForumLogin.state.in_(["pending", "verified"]))):
                    item.state = "expired"
            audit(s, "administrator", "forum_auth_changed", enabled=body.enabled, allow_signup=body.allow_signup)
        return body.model_dump()

    @app.post("/api/auth/forum/start", status_code=201)
    def start(request: Request, response: Response):
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "请从本站登录页面发起")
        with db.transaction() as s:
            gate = locked(s, "forum_auth_lock")
            if not s.get(KV, "forum_auth").data["enabled"]:
                raise HTTPException(403, "论坛登录已关闭")
            version, conf = connection(s)
            rates = {key: value for key, value in gate.data.get("rates", {}).items() if now() - value["start"] < 3600}
            address = digest(client_address(request, settings))
            for key, limit in [(address, 10), ("global", 120)]:
                entry = rates.get(key, {"start": now(), "count": 0})
                if entry["count"] >= limit:
                    raise HTTPException(429, "登录请求过多，请稍后再试")
                rates[key] = {**entry, "count": entry["count"] + 1}
            gate.data = {"rates": rates}
            s.execute(delete(ForumLogin).where(ForumLogin.expires < now() - 86400))
            if s.scalar(select(func.count()).select_from(ForumLogin).where(ForumLogin.expires > now(), ForumLogin.state == "pending")) >= 100:
                raise HTTPException(429, "当前登录请求较多，请稍后再试")
            # A replacement from this browser invalidates its previous code.
            old = digest(request.cookies.get("sm_forum_login", ""))
            for row in s.scalars(select(ForumLogin).where(ForumLogin.browser_hash == old, ForumLogin.state.in_(["pending", "verified"]))):
                row.state = "expired"
            code, browser = "SM-" + secrets.token_hex(16), secrets.token_urlsafe(40)
            row = ForumLogin(code_hash=digest(code.lower()), browser_hash=digest(browser), forum_version=version, expires=now() + 300)
            s.add(row)
            s.flush()
            result = {"id": row.id, "code": code, "expires": row.expires,
                      "forum_url": conf["base_url"], "bot_username": conf["username"]}
        response.set_cookie("sm_forum_login", browser, httponly=True, secure=settings.secure_cookie,
                            samesite="strict", max_age=300, path="/api/auth/forum")
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/api/auth/forum/status/{attempt_id}")
    def status(attempt_id: str, request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        with db.transaction() as s:
            row, conf = attempt(s, request, attempt_id)
            identity = s.scalar(select(ForumIdentity).where(ForumIdentity.site == conf["base_url"], ForumIdentity.user_id == row.profile.get("id", 0)))
            account = s.get(Account, identity.account_id) if identity else None
            return {"state": row.state, "profile": row.profile if row.state == "verified" else None,
                    "totp_required": bool(account and account.totp_cipher)}

    @app.post("/api/auth/forum/finish/{attempt_id}")
    def finish(attempt_id: str, body: ForumFinish, request: Request, response: Response):
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "请从本站登录页面确认")
        with db.transaction() as s:
            row, _ = attempt(s, request, attempt_id)
            if row.state != "verified":
                raise HTTPException(409, "尚未收到有效私信，或该请求已使用")
            if row.failures >= 5:
                raise HTTPException(429, "确认尝试过多，请重新验证论坛身份")
            row.failures += 1
        with db.transaction() as s:
            locked(s, "registration_lock")
            row, conf = attempt(s, request, attempt_id)
            if row.state != "verified":
                raise HTTPException(409, "尚未收到有效私信，或该请求已使用")
            profile = row.profile
            identity = s.scalar(select(ForumIdentity).where(ForumIdentity.site == conf["base_url"], ForumIdentity.user_id == profile["id"]))
            if identity:
                account = s.scalar(select(Account).where(Account.id == identity.account_id).with_for_update())
                if not account or not account.active:
                    raise HTTPException(403, "账户不可用，请联系管理员")
                if account.totp_cipher:
                    counter = int(now()) // 30
                    if not pyotp.TOTP(vault.open(account.totp_cipher)).verify(body.code) or counter <= account.totp_last:
                        raise HTTPException(403, "请输入有效的两步验证码")
                    account.totp_last = counter
            else:
                if not s.get(KV, "forum_auth").data["allow_signup"]:
                    raise HTTPException(403, "新用户注册已关闭，已有绑定用户仍可登录")
                if s.scalar(select(func.count()).select_from(Account)) >= 500:
                    raise HTTPException(403, "账户名额已满，请联系管理员")
                if any(name.casefold() == profile["username"].casefold() for name in s.scalars(select(Account.forum_username)) if name):
                    raise HTTPException(409, "已有旧账户绑定此论坛用户名，请联系管理员核验迁移")
                username = "forum_" + str(profile["id"])
                if s.scalar(select(Account.id).where(Account.username == username)):
                    username += "_" + secrets.token_hex(4)
                unique_username(s, username)
                account = Account(username=username, password_hash=password_hash(secrets.token_urlsafe(40)), role="editor", active=True,
                                  forum_username=profile["username"])
                s.add(account)
                s.flush()
                identity = ForumIdentity(account_id=account.id, site=conf["base_url"], user_id=profile["id"], profile=profile)
                s.add(identity)
                audit(s, account.id, "forum_self_registered", forum_user_id=profile["id"])
            identity.profile, identity.updated = profile, now()
            account.forum_username = profile["username"]
            locked(s, "memory_import_lock")
            for memory in s.scalars(select(Record).where(Record.kind == "memory")):
                fact = vault.open(memory.data["cipher"])
                if fact.get("origin") == "personal_topic" and fact.get("site") == conf["base_url"] and fact.get("forum_user_id") == profile["id"]:
                    memory.owner = account.id
                    memory.data = {"cipher": vault.seal({**fact, "username": profile["username"]})}
                    memory.version += 1
                    memory.updated = now()
            row.state = "consumed"
            token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
            s.add(LoginSession(id=digest(token), user_id=account.id, csrf=csrf, expires=now() + settings.session_hours * 3600))
            # Rotate any old local session, including one belonging to another account.
            s.execute(delete(LoginSession).where(LoginSession.id == digest(request.cookies.get("sm_session", ""))))
            audit(s, account.id, "forum_login", forum_user_id=profile["id"])
            result = {"id": account.id, "username": account.username, "role": account.role,
                      "forum_username": profile["username"], "forum_user_id": profile["id"],
                      "display_name": profile.get("name") or profile["username"],
                      "avatar_url": "/api/auth/avatar/" + account.id, "csrf": csrf}
        response.set_cookie("sm_session", token, httponly=True, secure=settings.secure_cookie,
                            samesite="strict", max_age=settings.session_hours * 3600, path="/")
        response.delete_cookie("sm_forum_login", path="/api/auth/forum")
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/api/auth/avatar/{account_id}")
    async def avatar(account_id: str, account=Depends(user)):
        if account.id != account_id and account.role != "admin":
            raise HTTPException(403, "不能读取他人的登录资料")
        with db.transaction() as s:
            identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == account_id))
            if not identity:
                raise HTTPException(404, "头像不可用")
            url = urljoin(identity.site + "/", identity.profile.get("avatar_template", "").replace("{size}", "64"))
            parsed, origin = urlsplit(url), urlsplit(identity.site)
            if parsed.scheme != "https" or parsed.netloc != origin.netloc or parsed.username or parsed.password or parsed.fragment:
                raise HTTPException(404, "头像地址不可用")
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            async with client.stream("GET", url) as upstream:
                mime = upstream.headers.get("content-type", "").split(";")[0]
                if upstream.status_code != 200 or mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
                    raise HTTPException(404, "头像暂时不可用")
                content = bytearray()
                async for chunk in upstream.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 524288:
                        raise HTTPException(404, "头像不可用")
        return Response(bytes(content), media_type=mime, headers={"Cache-Control": "private, max-age=300"})


async def verify_forum_logins(db, forum, version: int):
    """Only raw, fresh, two-participant PM posts can prove a browser's challenge."""
    with db.transaction() as s:
        pending = list(s.scalars(select(ForumLogin).where(ForumLogin.state == "pending", ForumLogin.expires > now(), ForumLogin.forum_version == version)))
    if not pending:
        return
    codes = {row.code_hash: row for row in pending}
    earliest = min(row.created for row in pending)
    bot = (await forum.read("/session/current.json"))["current_user"]
    notifications = await forum.notifications()
    tids = list(dict.fromkeys(int(n.get("topic_id") or 0) for n in reversed(notifications)
                             if int(n.get("topic_id") or 0) > 0 and timestamp(n.get("created_at")) >= earliest))[:10]
    for tid in tids:
        topic = await forum.read(f"/t/{tid}.json")
        details = topic.get("details") or {}
        allowed = {person["id"] for person in details.get("allowed_users", topic.get("allowed_users", []))}
        groups = details.get("allowed_groups", topic.get("allowed_groups", []))
        if topic.get("archetype") != "private_message" or topic.get("closed") or topic.get("archived") or groups or len(allowed) != 2 or bot["id"] not in allowed:
            continue
        ids = topic.get("post_stream", {}).get("stream", [])[-10:]
        if not ids:
            continue
        posts = (await forum.read(f"/t/{tid}/posts.json", [("post_ids[]", pid) for pid in ids])).get("post_stream", {}).get("posts", [])
        for post in posts:
            author = post.get("user_id")
            if author == bot["id"] or author not in allowed or post.get("topic_id") != tid or post.get("post_type") != 1 or post.get("hidden") or post.get("deleted_at"):
                continue
            body = post.get("raw") or plain(post.get("cooked", ""))
            # Quoted/copied codes are not an intentional authentication message.
            cooked = post.get("cooked", "").lower()
            if "[quote" in body.lower() or "<blockquote" in cooked or 'class="quote"' in cooked or any(line.lstrip().startswith(">") for line in body.splitlines()):
                continue
            matches = LOGIN_CODE.findall(body)
            if len(matches) != 1:
                continue
            row = codes.get(digest(matches[0].lower()))
            created = timestamp(post.get("created_at"))
            if not row or not row.created <= created <= min(row.expires, now() + 30):
                continue
            profile = (await forum.read("/u/" + quote(post["username"], safe="") + ".json")).get("user", {})
            if profile.get("id") != author or profile.get("username", "").casefold() != post["username"].casefold():
                continue
            with db.transaction() as s:
                current = s.scalar(select(ForumLogin).where(ForumLogin.id == row.id).with_for_update())
                conf = s.get(KV, "connection:forum")
                if current.state == "pending" and current.expires > now() and conf and conf.version == version and s.get(KV, "forum_auth").data["enabled"]:
                    current.profile = {"id": author, "username": profile["username"], "name": str(profile.get("name") or "")[:200],
                                       "avatar_template": str(profile.get("avatar_template") or "")[:1000]}
                    current.state = "verified"
                    audit(s, "forum_verifier", "forum_identity_verified", current.id, forum_user_id=author)
