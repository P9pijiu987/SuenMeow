"""Restricted self-registration and atomic prompt draft editing."""
import secrets

from fastapi import Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import Text, cast, func, or_, select

from .database import Account, KV, LoginSession, Record, audit, locked, now
from .domain import ModuleData, Strict
from .security import can_edit, client_address, digest, enforce_record, password_hash, require_admin
from .service import ROUTES


class RegistrationInput(Strict):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=12, max_length=256)


class RegistrationSettings(Strict):
    enabled: bool


class ModuleDraft(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    data: ModuleData
    grants: list[str] = Field(default_factory=list, max_length=200)
    version: int = Field(default=1, ge=1)


class WorkspaceSave(Strict):
    modules: list[ModuleDraft] = Field(default_factory=list, max_length=40)
    deleted: list["DeletedModule"] = Field(default_factory=list, max_length=40)
    pipeline: dict[str, list[str]] | None = None
    pipeline_version: int | None = Field(default=None, ge=1)


class DeletedModule(Strict):
    id: str = Field(min_length=1, max_length=32)
    version: int = Field(ge=1)


def unique_username(s, username, exclude=None):
    if any(name.casefold() == username.casefold() and aid != exclude
           for aid, name in s.execute(select(Account.id, Account.username))):
        raise HTTPException(409, "用户名已被使用")


def check_module_quota(s, account, additional=1):
    if account.role != "admin":
        count = s.scalar(select(func.count()).select_from(Record).where(
            Record.kind == "module", Record.owner == account.id))
        if count + additional > 50:
            raise HTTPException(422, "每位编辑者最多拥有 50 个模块，请联系管理员整理")
        total = s.scalar(select(func.count()).select_from(Record).where(Record.kind == "module"))
        if additional > 0 and total + additional > 500:
            raise HTTPException(422, "模块书架已满，请联系管理员整理")


def visible_records(account, kind):
    query = select(Record).where(Record.kind == kind)
    if account.role != "admin":
        scope = Record.owner == account.id
        if kind == "module":
            # UUID identifiers are matched as complete quoted JSON array elements on both databases.
            scope = or_(scope, cast(Record.grants, Text).contains('"' + account.id + '"'))
        query = query.where(scope)
    return query.order_by(Record.updated.desc(), Record.id)


def mount_workspace_api(app, db, settings, user, admin):
    def view(s, account):
        modules = [dict(id=r.id, owner=r.owner, title=r.title, data=r.data,
                        grants=r.grants, version=r.version, updated=r.updated)
                   for r in s.scalars(visible_records(account, "module")) if can_edit(account, r)]
        result = {"modules": modules, "pipeline": None, "pipeline_version": None}
        if account.role == "admin":
            pipeline = s.get(KV, "pipeline")
            result.update(pipeline={**pipeline.data, "agent": pipeline.data.get("agent", [])},
                          pipeline_version=pipeline.version,
                          active_snapshot=s.get(KV, "control").data["active_snapshot"],
                          accounts=[dict(id=a.id, username=a.username, active=a.active) for a in s.scalars(
                              select(Account).where(Account.role == "editor"))])
        return result

    @app.get("/api/auth/registration")
    def registration_status():
        with db.transaction() as s:
            config = s.get(KV, "registration")
            return {"enabled": bool(config and config.data.get("enabled")), "minimum_password_length": 12}

    @app.put("/api/auth/registration")
    def registration_settings(body: RegistrationSettings, account=Depends(admin)):
        with db.transaction() as s:
            locked(s, "registration_lock")
            config = s.get(KV, "registration")
            config.data, config.version = body.model_dump(), config.version + 1
            audit(s, account.id, "registration_changed", enabled=body.enabled)
        return body.model_dump()

    @app.post("/api/auth/register", status_code=201)
    def register(body: RegistrationInput, request: Request, response: Response):
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "请从本站注册页面提交")
        address = digest(client_address(request, settings))
        with db.transaction() as s:
            gate = locked(s, "registration_lock")
            if not s.get(KV, "registration").data.get("enabled"):
                raise HTTPException(403, "管理员已关闭注册")
            rates = {k: v for k, v in gate.data.get("rates", {}).items() if now() - v["start"] < 3600}
            rate = rates.get(address, {"start": now(), "count": 0})
            global_rate = rates.get("global", {"start": now(), "count": 0})
            if rate["count"] >= 30 or global_rate["count"] >= 120:
                raise HTTPException(429, "注册尝试过多，请在一小时后再试")
            rates[address] = {**rate, "count": rate["count"] + 1}
            rates["global"] = {**global_rate, "count": global_rate["count"] + 1}
            gate.data = {"rates": rates}
        hashed = password_hash(body.password)
        with db.transaction() as s:
            locked(s, "registration_lock")
            if not s.get(KV, "registration").data.get("enabled"):
                raise HTTPException(403, "管理员已关闭注册")
            if s.scalar(select(func.count()).select_from(Account)) >= 500:
                raise HTTPException(403, "注册名额已满，请联系管理员")
            unique_username(s, body.username)
            account = Account(username=body.username, password_hash=hashed, role="editor", active=True,
                              forum_username="")
            s.add(account)
            s.flush()
            token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
            s.add(LoginSession(id=digest(token), user_id=account.id, csrf=csrf,
                               expires=now() + settings.session_hours * 3600))
            audit(s, account.id, "self_registered")
            result = {"id": account.id, "username": account.username, "role": "editor",
                      "forum_username": "", "totp_enabled": False, "csrf": csrf}
        response.set_cookie("sm_session", token, httponly=True, secure=settings.secure_cookie,
                            samesite="strict", path="/", max_age=settings.session_hours * 3600)
        return result

    @app.get("/api/prompts/workspace")
    def workspace(account=Depends(user)):
        with db.transaction() as s:
            locked(s, "editor_lock")
            locked(s, "pipeline")
            return view(s, account)

    @app.post("/api/prompts/workspace/save")
    def save_workspace(body: WorkspaceSave, account=Depends(user)):
        if body.pipeline is not None:
            require_admin(account)
            if (body.pipeline_version is None or not set(ROUTES) <= set(body.pipeline)
                    or set(body.pipeline) - {*ROUTES, "agent"}
                    or any(len(v) > 40 or len(v) != len(set(v)) for v in body.pipeline.values())):
                raise HTTPException(422, "编排需要版本号、四条工作路由；每条最多 40 个不同模块")
        ids = [m.id for m in body.modules] + [m.id for m in body.deleted]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "不能重复提交同一模块")
        with db.transaction() as s:
            locked(s, "editor_lock")
            pipeline = locked(s, "pipeline")
            if body.pipeline is not None and pipeline.version != body.pipeline_version:
                raise HTTPException(409, "编排已被其他人修改；你的修改仍保留，请重新读取后合并")
            mapping = {}
            for draft in sorted(body.deleted, key=lambda m: m.id):
                r = s.scalar(select(Record).where(Record.id == draft.id, Record.kind == "module").with_for_update())
                if not r:
                    raise HTTPException(404, "模块不存在；修改仍保留")
                if account.role != "admin" and account.id != r.owner:
                    raise HTTPException(403, "只能删除自己的模块")
                if r.version != draft.version:
                    raise HTTPException(409, "待删除模块已被修改；请重新读取后确认")
                s.delete(r)
                audit(s, account.id, "record_deleted", r.id, kind="module")
            s.flush()
            check_module_quota(s, account, sum(mid.startswith("new-") for mid in ids))
            for draft in sorted(body.modules, key=lambda m: m.id):
                if draft.id.startswith("new-"):
                    if draft.grants:
                        require_admin(account)
                    r = Record(kind="module", owner=account.id, title=draft.title,
                               data=draft.data.model_dump(), grants=draft.grants)
                    s.add(r)
                    s.flush()
                    mapping[draft.id] = r.id
                    audit(s, account.id, "record_created", r.id, kind="module")
                else:
                    r = s.scalar(select(Record).where(Record.id == draft.id, Record.kind == "module").with_for_update())
                    if not r:
                        raise HTTPException(404, "模块不存在；修改仍保留")
                    enforce_record(account, r)
                    if r.version != draft.version:
                        raise HTTPException(409, "模块已被其他人修改；你的修改仍保留，请重新读取后合并")
                    if draft.grants != r.grants:
                        require_admin(account)
                    r.title, r.data, r.grants = draft.title, draft.data.model_dump(), draft.grants
                    r.version, r.updated = r.version + 1, now()
                    audit(s, account.id, "record_updated", r.id, kind="module")
                if draft.grants:
                    valid = set(s.scalars(select(Account.id).where(Account.active.is_(True))))
                    if set(draft.grants) - valid:
                        raise HTTPException(422, "授权账户不存在或已停用")
            order = ({k: [mapping.get(mid, mid) for mid in v] for k, v in body.pipeline.items()}
                     if body.pipeline is not None else pipeline.data)
            valid = set(s.scalars(select(Record.id).where(Record.kind == "module")))
            if any(mid not in valid for route in order.values() for mid in route):
                raise HTTPException(422, "模块被编排引用，需管理员先移除引用；整次修改均未保存")
            if body.pipeline is not None:
                pipeline.data, pipeline.version = order, pipeline.version + 1
                audit(s, account.id, "pipeline_draft_saved")
            s.flush()
            return {**view(s, account), "id_mapping": mapping}
