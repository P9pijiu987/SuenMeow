from contextlib import asynccontextmanager
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
import pyotp
from sqlalchemy import delete, select

from .database import Account, Audit, Database, Event, KV, LoginSession, Record, Reply, Snapshot, Usage, audit, locked, now
from .domain import ForumConnection, ModeInput, ModuleData, NestData, Policy, RecordInput, Route, Strict
from .security import DUMMY_HASH, Vault, digest, enforce_record, can_edit, password_hash, require_admin, same_token, verify_password
from .service import ROUTES, dashboard, publish, set_mode
from .settings import Settings
from .agent_api import mount_agent_api


class LoginInput(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=8)


class AccountInput(Strict):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[\w.-]+$")
    password: str = Field(default="", max_length=256)
    role: str = Field(default="editor", pattern="^(admin|editor)$")
    active: bool = True
    forum_username: str = Field(default="", max_length=100)


class PasswordInput(Strict):
    current: str = Field(max_length=256)
    password: str = Field(max_length=256)


class TotpInput(Strict):
    password: str = Field(max_length=256)
    code: str = Field(default="", max_length=8)


class PublishInput(Strict):
    note: str = Field(default="", max_length=300)


class DecisionInput(Strict):
    action: str = Field(pattern="^(approve|reject|resolve_sent|resolve_unsent)$")
    text: str = Field(default="", max_length=10000)
    post_id: int = Field(default=0, ge=0)


def create_app(settings: Settings | None = None, database: Database | None = None):
    settings = settings or Settings.env()
    db = database or Database(settings.database_url)
    vault = Vault(settings.key_file)

    @asynccontextmanager
    async def lifespan(app):
        # Schema changes are performed explicitly by the CLI/deployment init service.
        with db.transaction() as s:
            if not s.get(KV, "schema") or s.get(KV, "schema").data.get("version") != 2:
                raise RuntimeError("Run suenmeow init before starting the API")
        yield

    app = FastAPI(title="SuenMeow", version="2.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.vault = db, vault

    @app.middleware("http")
    async def headers(request: Request, call_next):
        if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", 0)) > 262144:
            return JSONResponse({"detail": "请求过大"}, status_code=413)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != settings.origin:
                return JSONResponse({"detail": "来源不允许"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def user(request: Request):
        token = request.cookies.get("sm_session", "")
        with db.transaction() as s:
            session = s.get(LoginSession, digest(token)) if token else None
            account = s.get(Account, session.user_id) if session else None
            if not session or session.expires < now() or not account or not account.active:
                raise HTTPException(401, "请先登录")
            if request.method not in ("GET", "HEAD") and not same_token(request.headers.get("x-csrf-token", ""), session.csrf):
                raise HTTPException(403, "会话校验失败，请刷新后重试")
            request.state.session_id = session.id
            request.state.csrf = session.csrf
            return account

    def admin(account=Depends(user)):
        require_admin(account)
        return account

    def record_dict(r):
        data = vault.open(r.data["cipher"]) if "cipher" in r.data else r.data
        return {"id": r.id, "kind": r.kind, "owner": r.owner, "title": r.title,
                "data": data, "grants": r.grants, "version": r.version, "updated": r.updated}

    @app.get("/api/health")
    def health():
        with db.transaction() as s:
            schema = s.get(KV, "schema")
            if not schema or schema.data["version"] != 2:
                raise HTTPException(503, "数据库未就绪")
        return {"ok": True, "version": "2.0.0"}

    @app.post("/api/auth/login")
    def login(body: LoginInput, request: Request, response: Response):
        address = request.client.host if request.client else "unknown"
        keys = ["login:" + digest(address)[:32], "account:" + digest(body.username.casefold())[:32]]
        with db.transaction() as s:
            for key in keys:
                entry = s.get(KV, key)
                if not entry:
                    entry = KV(key=key, data={"count": 0, "start": now()})
                    s.add(entry)
                    s.flush()
                entry = locked(s, key)
                data = entry.data if now() - entry.data["start"] < 900 else {"count": 0, "start": now()}
                if data["count"] >= 10:
                    raise HTTPException(429, "登录尝试过多，请在 15 分钟后再试")
                entry.data = {**data, "count": data["count"] + 1}
        with db.transaction() as s:
            account = s.scalar(select(Account).where(Account.username == body.username).with_for_update())
            valid = verify_password(account.password_hash if account else DUMMY_HASH, body.password)
            if account and account.totp_cipher:
                totp = pyotp.TOTP(vault.open(account.totp_cipher))
                counter = int(time.time()) // 30
                valid = valid and totp.verify(body.code) and counter > account.totp_last
                if valid:
                    account.totp_last = counter
            if not valid or not account or not account.active:
                audit(s, "anonymous", "login_failed")
                # Commit the failure audit before raising below.
                result = None
            else:
                token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
                s.add(LoginSession(id=digest(token), user_id=account.id, csrf=csrf,
                                   expires=now() + settings.session_hours * 3600))
                audit(s, account.id, "login")
                for key in keys:
                    entry = locked(s, key)
                    entry.data = {**entry.data, "count": 0 if key.startswith("account:") else max(0, entry.data["count"] - 1)}
                result = {"id": account.id, "username": account.username, "role": account.role, "csrf": csrf}
        if result is None:
            raise HTTPException(401, "登录信息或验证码错误")
        response.set_cookie("sm_session", token, httponly=True, secure=settings.secure_cookie,
                            samesite="strict", path="/", max_age=settings.session_hours * 3600)
        return result

    @app.get("/api/auth/me")
    def me(request: Request, account=Depends(user)):
        return {"id": account.id, "username": account.username, "role": account.role,
                "forum_username": account.forum_username, "totp_enabled": bool(account.totp_cipher), "csrf": request.state.csrf}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, account=Depends(user)):
        with db.transaction() as s:
            s.execute(delete(LoginSession).where(LoginSession.id == request.state.session_id))
        response.delete_cookie("sm_session", path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(body: PasswordInput, account=Depends(user)):
        if not verify_password(account.password_hash, body.current):
            raise HTTPException(403, "当前密码错误")
        encoded = password_hash(body.password)
        with db.transaction() as s:
            s.get(Account, account.id).password_hash = encoded
            s.execute(delete(LoginSession).where(LoginSession.user_id == account.id))
            audit(s, account.id, "password_changed")
        return {"ok": True, "message": "密码已修改，请重新登录"}

    @app.post("/api/auth/totp/setup")
    def setup_totp(body: TotpInput, account=Depends(user)):
        if not verify_password(account.password_hash, body.password):
            raise HTTPException(403, "密码错误")
        if account.totp_cipher:
            raise HTTPException(409, "已启用两步验证")
        secret = pyotp.random_base32()
        with db.transaction() as s:
            s.get(Account, account.id).totp_pending = vault.seal(secret)
        return {"secret": secret, "uri": pyotp.TOTP(secret).provisioning_uri(account.username, issuer_name="SuenMeow")}

    @app.post("/api/auth/totp/confirm")
    def confirm_totp(body: TotpInput, account=Depends(user)):
        if not verify_password(account.password_hash, body.password):
            raise HTTPException(403, "密码错误")
        with db.transaction() as s:
            a = s.get(Account, account.id)
            if not a.totp_pending or not pyotp.TOTP(vault.open(a.totp_pending)).verify(body.code):
                raise HTTPException(422, "验证码错误")
            a.totp_cipher, a.totp_pending, a.totp_last = a.totp_pending, "", int(time.time()) // 30
            s.execute(delete(LoginSession).where(LoginSession.user_id == a.id))
            audit(s, a.id, "totp_enabled")
        return {"ok": True}

    @app.get("/api/auth/sessions")
    def sessions(account=Depends(user)):
        with db.transaction() as s:
            return [{"id": x.id, "expires": x.expires} for x in s.scalars(select(LoginSession).where(LoginSession.user_id == account.id))]

    @app.delete("/api/auth/sessions/{session_id}")
    def revoke_session(session_id: str, account=Depends(user)):
        with db.transaction() as s:
            s.execute(delete(LoginSession).where(LoginSession.user_id == account.id, LoginSession.id == session_id))
            audit(s, account.id, "session_revoked")
        return {"ok": True}

    @app.get("/api/dashboard")
    def status(account=Depends(user)):
        with db.transaction() as s:
            return dashboard(s, account.role == "admin")

    @app.put("/api/control")
    def control(body: ModeInput, account=Depends(admin)):
        with db.transaction() as s:
            return set_mode(s, body.mode, account.id)

    @app.get("/api/config")
    def config(account=Depends(admin)):
        with db.transaction() as s:
            return {"policy": Policy.model_validate(s.get(KV, "policy").data).model_dump(),
                    "pipeline": s.get(KV, "pipeline").data, "control": s.get(KV, "control").data}

    @app.put("/api/config/policy")
    def policy(body: Policy, account=Depends(admin)):
        with db.transaction() as s:
            s.get(KV, "policy").data = body.model_dump()
            audit(s, account.id, "policy_draft_saved")
        return {"ok": True, "message": "草稿已保存，发布后生效"}

    @app.put("/api/config/pipeline")
    def pipeline(body: dict[str, list[str]], account=Depends(admin)):
        if set(body) != set(ROUTES) or any(len(v) > 40 for v in body.values()):
            raise HTTPException(422, "需要四条路由，每条最多 40 个模块")
        with db.transaction() as s:
            modules = set(s.scalars(select(Record.id).where(Record.kind == "module")))
            if any(x not in modules for v in body.values() for x in v):
                raise HTTPException(422, "模块不存在")
            s.get(KV, "pipeline").data = body
            audit(s, account.id, "pipeline_draft_saved")
        return {"ok": True}

    @app.post("/api/config/publish")
    def publish_config(body: PublishInput, account=Depends(admin)):
        with db.transaction() as s:
            return {"version": publish(s, account.id, body.note)}

    @app.get("/api/config/versions")
    def versions(account=Depends(admin)):
        with db.transaction() as s:
            return [{"id": v.id, "created": v.created, "note": v.note, "actor": v.actor}
                    for v in s.scalars(select(Snapshot).order_by(Snapshot.id.desc()).limit(50))]

    @app.get("/api/config/versions/{version}")
    def version_data(version: int, account=Depends(admin)):
        with db.transaction() as s:
            v = s.get(Snapshot, version)
            if not v:
                raise HTTPException(404, "版本不存在")
            return v.data

    @app.post("/api/config/versions/{version}/restore")
    def restore(version: int, account=Depends(admin)):
        with db.transaction() as s:
            v = s.get(Snapshot, version)
            if not v:
                raise HTTPException(404, "版本不存在")
            s.get(KV, "policy").data = v.data["policy"]
            s.get(KV, "pipeline").data = v.data["pipeline"]
            for mid, data in v.data["modules"].items():
                r = s.get(Record, mid)
                if not r:
                    r = Record(id=mid, kind="module", owner=account.id, title=data["title"], data={})
                    s.add(r)
                r.data = {**r.data, "content": data["content"], "persona": data.get("persona", r.data.get("persona", False))}
                r.version += 1
            return {"version": publish(s, account.id, f"回滚至版本 {version}", v.data)}

    @app.get("/api/connections")
    def connections(account=Depends(admin)):
        with db.transaction() as s:
            result = {}
            for key in ["forum", *ROUTES, "agent"]:
                stored = s.get(KV, "connection:" + key)
                if stored:
                    data = vault.open(stored.data["cipher"])
                    result[key] = {k: v for k, v in data.items() if k not in ("password", "api_key")}
                    result[key]["configured"] = bool(data.get("password") or data.get("api_key"))
            return result

    @app.put("/api/connections/forum")
    def forum_connection(body: ForumConnection, account=Depends(admin)):
        return save_connection("forum", body.model_dump(), account)

    @app.put("/api/connections/{route}")
    def model_connection(route: str, body: Route, account=Depends(admin)):
        if route not in (*ROUTES, "agent"):
            raise HTTPException(404, "路由不存在")
        return save_connection(route, body.model_dump(), account)

    def save_connection(key, data, account):
        with db.transaction() as s:
            control = locked(s, "control")
            stored = s.get(KV, "connection:" + key)
            if stored:
                old = vault.open(stored.data["cipher"])
                for secret in ("password", "api_key"):
                    if secret in data and not data[secret]:
                        data[secret] = old.get(secret, "")
                stored.data, stored.version = {"cipher": vault.seal(data)}, stored.version + 1
            else:
                s.add(KV(key="connection:" + key, data={"cipher": vault.seal(data)}))
            if not data.get("password") and not data.get("api_key"):
                raise HTTPException(422, "首次配置需要密钥或密码")
            control.data = {**control.data, "mode": "paused", "epoch": control.data["epoch"] + 1}
            audit(s, account.id, "connection_changed", key)
        return {"ok": True, "message": "连接已保存，系统已暂停；重新开启时跳过积压"}

    @app.get("/api/records/{kind}")
    def records(kind: str, account=Depends(user)):
        if kind not in ("module", "memory", "nest"):
            raise HTTPException(404, "类型不存在")
        with db.transaction() as s:
            rows = s.scalars(select(Record).where(Record.kind == kind).order_by(Record.updated.desc()).limit(500))
            return [record_dict(r) for r in rows if can_edit(account, r)]

    def validated_data(kind, data, account):
        if kind == "module":
            try:
                return ModuleData.model_validate(data).model_dump()
            except ValidationError:
                raise HTTPException(422, "提示词格式或长度不正确")
        if kind == "nest":
            try:
                result = NestData.model_validate(data).model_dump()
            except ValidationError:
                raise HTTPException(422, "猫窝格式不正确；需要有效的既有主题 ID")
            if account.role != "admin":
                if not account.forum_username or result["forum_username"] != account.forum_username:
                    raise HTTPException(403, "管理员须先绑定你的论坛身份")
                if result["private"] or result["followup"]:
                    raise HTTPException(403, "私信绑定需要管理员核验")
            return result
        if kind == "memory":
            require_admin(account)  # Automatic personal facts can be viewed/deleted; behavior edits require admin.
            if len(str(data.get("text", ""))) > 10000 or data.get("scope") not in ("public", "private"):
                raise HTTPException(422, "记忆格式不正确")
            return {"cipher": vault.seal(data)}
        raise HTTPException(404, "类型不存在")

    @app.post("/api/records/{kind}")
    def add_record(kind: str, body: RecordInput, account=Depends(user)):
        if body.grants:
            require_admin(account)
        data = validated_data(kind, body.data, account)
        with db.transaction() as s:
            r = Record(kind=kind, owner=account.id, title=body.title, data=data, grants=body.grants)
            s.add(r)
            s.flush()
            audit(s, account.id, "record_created", r.id, kind=kind)
            return record_dict(r)

    @app.put("/api/records/{kind}/{record_id}")
    def edit_record(kind: str, record_id: str, body: RecordInput, account=Depends(user)):
        with db.transaction() as s:
            r = s.scalar(select(Record).where(Record.id == record_id, Record.kind == kind).with_for_update())
            if not r:
                raise HTTPException(404, "内容不存在")
            enforce_record(account, r)
            if body.version != r.version:
                raise HTTPException(409, "内容已被更新，请刷新后重试")
            if body.grants != r.grants:
                require_admin(account)
            data = validated_data(kind, body.data, account)
            r.title, r.data, r.grants, r.version, r.updated = body.title, data, body.grants, r.version + 1, now()
            audit(s, account.id, "record_updated", r.id, kind=kind)
            return record_dict(r)

    @app.delete("/api/records/{kind}/{record_id}")
    def remove_record(kind: str, record_id: str, account=Depends(user)):
        with db.transaction() as s:
            r = s.get(Record, record_id)
            if not r or r.kind != kind:
                raise HTTPException(404, "内容不存在")
            enforce_record(account, r)
            if kind == "module":
                require_admin(account)
            s.delete(r)
            audit(s, account.id, "record_deleted", record_id, kind=kind)
        return {"ok": True}

    @app.get("/api/accounts")
    def accounts(account=Depends(admin)):
        with db.transaction() as s:
            return [{"id": a.id, "username": a.username, "role": a.role, "active": a.active,
                     "forum_username": a.forum_username, "totp_enabled": bool(a.totp_cipher)} for a in s.scalars(select(Account))]

    @app.post("/api/accounts")
    def create_account(body: AccountInput, account=Depends(admin)):
        encoded = password_hash(body.password)
        with db.transaction() as s:
            if s.scalar(select(Account).where(Account.username == body.username)):
                raise HTTPException(409, "用户名已存在")
            if body.forum_username and s.scalar(select(Account).where(Account.forum_username == body.forum_username)):
                raise HTTPException(409, "论坛身份已绑定")
            a = Account(username=body.username, password_hash=encoded, role=body.role,
                        active=body.active, forum_username=body.forum_username)
            s.add(a)
            s.flush()
            audit(s, account.id, "account_created", a.id)
            return {"id": a.id}

    @app.put("/api/accounts/{account_id}")
    def update_account(account_id: str, body: AccountInput, account=Depends(admin)):
        with db.transaction() as s:
            a = s.get(Account, account_id)
            if not a:
                raise HTTPException(404, "用户不存在")
            if account.id == a.id and (not body.active or body.role != "admin"):
                raise HTTPException(409, "不能禁用或降级当前管理员")
            duplicate = s.scalar(select(Account).where(Account.username == body.username, Account.id != account_id))
            if duplicate:
                raise HTTPException(409, "用户名已存在")
            if body.forum_username and s.scalar(select(Account).where(Account.forum_username == body.forum_username, Account.id != account_id)):
                raise HTTPException(409, "论坛身份已绑定")
            a.username, a.role, a.active, a.forum_username = body.username, body.role, body.active, body.forum_username
            if body.password:
                a.password_hash = password_hash(body.password)
            s.execute(delete(LoginSession).where(LoginSession.user_id == a.id))
            audit(s, account.id, "account_updated", a.id)
        return {"ok": True}

    @app.get("/api/replies")
    def replies(account=Depends(admin)):
        with db.transaction() as s:
            result = []
            for r in s.scalars(select(Reply).order_by(Reply.created.desc()).limit(100)):
                e = s.get(Event, r.event_id)
                result.append({"id": r.id, "topic_id": r.topic_id, "text": vault.open(r.text_cipher),
                               "state": r.state, "reason": r.reason, "created": r.created, "expires": e.expires,
                               "private": e.data.get("private", False), "source": e.data.get("source"),
                               "snapshot_id": e.snapshot_id, "memory_state": r.memory_state, "sent_post_id": r.sent_post_id})
            return result

    @app.post("/api/replies/{reply_id}/decision")
    def decision(reply_id: str, body: DecisionInput, account=Depends(admin)):
        with db.transaction() as s:
            control = locked(s, "control").data
            r = s.scalar(select(Reply).where(Reply.id == reply_id).with_for_update())
            if not r:
                raise HTTPException(404, "回复不存在")
            e = s.get(Event, r.event_id)
            if body.action in ("resolve_sent", "resolve_unsent"):
                if r.state != "unknown":
                    raise HTTPException(409, "只有待核实回复能进行此操作")
                if body.action == "resolve_sent" and not body.post_id:
                    raise HTTPException(422, "请填写论坛上核实的帖子 ID")
                r.state = "sent" if body.action == "resolve_sent" else "cancelled"
                r.sent_post_id = body.post_id
                r.reason = "管理员已核实；原事件不会重发"
                e.state = r.state
            elif r.state != "approval":
                raise HTTPException(409, "回复状态已变化")
            elif body.action == "reject":
                r.state, e.state = "rejected", "rejected"
            else:
                if control["mode"] not in ("approval", "auto") or e.expires <= now() or e.epoch != control["epoch"]:
                    raise HTTPException(409, "事件已过期或模式已变化")
                if body.text:
                    policy = Policy.model_validate(s.get(Snapshot, e.snapshot_id).data["policy"])
                    if len(body.text) > policy.max_reply_chars:
                        raise HTTPException(422, "超过回复长度限制")
                    r.text_cipher = vault.seal(body.text)
                r.state, r.reason = "ready", "等待发送门校验"
            r.updated = now()
            audit(s, account.id, "reply_" + body.action, r.id)
        return {"ok": True}

    @app.get("/api/events")
    def events(account=Depends(admin)):
        with db.transaction() as s:
            return [{"id": e.id, "topic_id": e.topic_id, "state": e.state, "reason": e.reason,
                     "created": e.created, "source": e.data.get("source"), "snapshot_id": e.snapshot_id}
                    for e in s.scalars(select(Event).order_by(Event.created.desc()).limit(100))]

    @app.get("/api/audit")
    def logs(account=Depends(admin)):
        with db.transaction() as s:
            return [{"id": a.id, "actor": a.actor, "action": a.action, "target": a.target,
                     "detail": a.detail, "created": a.created} for a in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))]

    @app.get("/api/usage")
    def usage(account=Depends(admin)):
        with db.transaction() as s:
            return [{"id": u.id, "day": u.day, "route": u.route, "topic_id": u.topic_id,
                     "tokens": u.tokens, "state": u.state, "created": u.created}
                    for u in s.scalars(select(Usage).order_by(Usage.created.desc()).limit(200))]

    mount_agent_api(app, db, vault, admin)
    return app
