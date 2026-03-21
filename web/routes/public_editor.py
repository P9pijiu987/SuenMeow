from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from bot.settings import available_module_files
from bot.settings import ensure_prompt_storage
from bot.settings import PromptModuleEntry
from bot.settings import PromptModulesConfig
from bot.settings import PromptRouteConfig
from bot.settings import load_settings
from bot.settings import save_prompt_modules
from bot.settings import validate_prompt_modules_config
from bot.settings import write_prompt_file_with_backup


router = APIRouter(prefix="/public", tags=["公网编辑端"])


class MarkdownContentPayload(BaseModel):
    content: str


class PromptModuleItemPayload(BaseModel):
    name: str
    enabled: bool = True


class PromptRoutePayload(BaseModel):
    modules: list[PromptModuleItemPayload]


class PromptModulesPayload(BaseModel):
    planner: PromptRoutePayload
    replyer: PromptRoutePayload
    memory: PromptRoutePayload


def _normalize_markdown_filename(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix != ".md":
        raise HTTPException(status_code=400, detail="Markdown 文件名不合法")
    return filename


def _prompt_dir(request: Request) -> Path:
    paths = request.app.state.paths
    ensure_prompt_storage(paths)
    return paths.prompt_dir


def _read_prompt_content(request: Request, filename: str) -> tuple[str, bool]:
    prompt_file = _prompt_dir(request) / filename
    if prompt_file.is_file():
        return prompt_file.read_text(encoding="utf-8"), False
    raise HTTPException(status_code=404, detail="未找到提示词文件")


def _read_persona_content(request: Request, filename: str) -> tuple[str, bool]:
    prompt_file = _prompt_dir(request) / filename
    if prompt_file.is_file():
        return prompt_file.read_text(encoding="utf-8"), False
    raise HTTPException(status_code=404, detail="未找到人格文件")


def _build_route_config(items: list[PromptModuleItemPayload], available_files: set[str]) -> PromptRouteConfig:
    if not items:
        raise HTTPException(status_code=400, detail="提示词模块链不能为空")
    seen: set[str] = set()
    modules: list[PromptModuleEntry] = []
    for item in items:
        name = _normalize_markdown_filename(item.name)
        if name not in available_files:
            raise HTTPException(status_code=400, detail=f"未找到提示词模块: {name}")
        if name in seen:
            raise HTTPException(status_code=400, detail=f"提示词模块重复: {name}")
        seen.add(name)
        modules.append(PromptModuleEntry(name=name, enabled=item.enabled))
    if not any(module.enabled for module in modules):
        raise HTTPException(status_code=400, detail="每条提示词链路至少需要启用一个模块")
    return PromptRouteConfig(modules=modules)


def _public_editor_config(request: Request) -> dict[str, object]:
    prompts = sorted(path.name for path in _prompt_dir(request).glob("*.md"))
    settings = request.app.state.settings
    return {
        "prompts": prompts,
        "available_module_files": sorted(available_module_files(request.app.state.paths)),
        "prompt_modules": {
            "planner": [{"name": module.name, "enabled": module.enabled} for module in settings.prompt_modules.planner.modules],
            "replyer": [{"name": module.name, "enabled": module.enabled} for module in settings.prompt_modules.replyer.modules],
            "memory": [{"name": module.name, "enabled": module.enabled} for module in settings.prompt_modules.memory.modules],
        },
    }


@router.get("", response_class=HTMLResponse, summary="公网编辑端首页")
def public_editor_index() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SuenMeow Public Editor</title>
  <style>
    body { font-family: 'Microsoft YaHei', sans-serif; margin: 0; background: #f6f7fb; color: #1f2937; }
    .wrap { max-width: 1200px; margin: 24px auto; padding: 0 16px; }
    .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    h1,h2 { margin: 0 0 10px; }
    .hint { color:#6b7280; font-size:13px; line-height:1.7; }
    .warn { background:#fff7ed; border:1px solid #fdba74; border-radius:8px; padding:10px; margin-top:10px; }
    textarea, select, input { width: 100%; box-sizing: border-box; padding: 10px; border:1px solid #d1d5db; border-radius:8px; font-size:14px; }
    textarea { min-height: 180px; }
    button { border:0; background:#2563eb; color:#fff; border-radius:8px; padding:8px 14px; cursor:pointer; }
    button.secondary { background:#4b5563; }
    .grid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
    .status { font-size:13px; color:#059669; margin-left: 8px; }
    .status.error { color:#dc2626; }
    .readonly { color:#9ca3af; font-size:12px; margin-left:6px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>🌐 公网编辑端（受限）</h1>
      <div class="hint">
        本界面用于论坛用户协作编辑。提示词与人格文件已统一到 <code>prompts/</code>，所有变更会自动备份到 <code>prompts_backup/</code>。<br/>
        修改会影响后续模型链路，请先阅读下方教程。
      </div>
      <div class="warn">
        <b>使用提醒教程（必读）</b><br/>
        1) 先在列表中选择已有文件作为模板；<br/>
        2) 需要改内容时，可直接保存到同名文件或新建文件；<br/>
        3) 在“提示词模块编排”里把你的新文件加入对应链路并确认启用状态；<br/>
        4) 提交后观察运行效果，不要在高峰期频繁切换模块；<br/>
        5) 若结果异常，优先禁用新增模块回滚。
      </div>
    </div>

    <div class="card grid">
      <div>
        <h2>📝 提示词编辑</h2>
        <label>提示词文件（prompts/）</label>
        <select id="prompt-list"></select>
        <label>新建文件名（.md）</label>
        <input id="prompt-new" placeholder="例如: my_forum_style.md" />
        <label>内容</label>
        <textarea id="prompt-content"></textarea>
        <div style="margin-top:10px;">
          <button type="button" onclick="loadPrompt()">加载</button>
          <button type="button" onclick="savePrompt()">保存到 prompts</button>
          <span id="prompt-status" class="status"></span>
        </div>
      </div>
      <div>
        <h2>🎭 人格编辑</h2>
        <label>人格文件（与 prompts 合并）</label>
        <select id="persona-list"></select>
        <label>新建文件名（.md）</label>
        <input id="persona-new" placeholder="例如: helper_public.md" />
        <label>内容</label>
        <textarea id="persona-content"></textarea>
        <div style="margin-top:10px;">
          <button type="button" onclick="loadPersona()">加载</button>
          <button type="button" onclick="savePersona()">保存到 prompts</button>
          <span id="persona-status" class="status"></span>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>🧩 提示词模块编排（同步到主配置）</h2>
      <div class="hint">可把 prompts/ 中的任意文件编入 planner / replyer / memory。若引用不存在文件会被拒绝。</div>
      <label>planner (逗号分隔 md 文件名)</label>
      <input id="planner-modules" />
      <label>replyer (逗号分隔 md 文件名)</label>
      <input id="replyer-modules" />
      <label>memory (逗号分隔 md 文件名)</label>
      <input id="memory-modules" />
      <div style="margin-top:10px;">
        <button type="button" onclick="saveModules()">保存模块编排</button>
        <span id="modules-status" class="status"></span>
      </div>
    </div>

    <div class="card">
      <h2>🧠 记忆（只读）</h2>
      <div class="hint">公网端不提供记忆写入权限，仅可查看。</div>
      <textarea id="memory-view" readonly></textarea>
      <div style="margin-top:8px;"><button class="secondary" onclick="loadMemory()">刷新记忆</button></div>
    </div>
  </div>

  <script>
    let configCache = null;
    function setStatus(id, msg, error=false) {
      const el = document.getElementById(id);
      el.textContent = msg;
      el.className = 'status' + (error ? ' error' : '');
    }
    function asOptions(selectId, items) {
      const sel = document.getElementById(selectId);
      sel.innerHTML = (items || []).map(x => `<option value="${x}">${x}</option>`).join('');
    }
    function splitModules(value) {
      return String(value || '').split(',').map(x => x.trim()).filter(Boolean);
    }
    async function loadConfig() {
      const res = await fetch('/public/config');
      const data = await res.json();
      configCache = data;
      asOptions('prompt-list', data.prompts || []);
      asOptions('persona-list', data.prompts || []);
      document.getElementById('planner-modules').value = (data.prompt_modules.planner || []).map(x => x.name).join(', ');
      document.getElementById('replyer-modules').value = (data.prompt_modules.replyer || []).map(x => x.name).join(', ');
      document.getElementById('memory-modules').value = (data.prompt_modules.memory || []).map(x => x.name).join(', ');
    }
    async function loadPrompt() {
      const target = document.getElementById('prompt-list').value;
      const res = await fetch(`/public/prompts/${encodeURIComponent(target)}`);
      const data = await res.json();
      document.getElementById('prompt-content').value = data.content || '';
      setStatus('prompt-status', '已加载 prompts 文件');
    }
    async function savePrompt() {
      const newName = (document.getElementById('prompt-new').value || '').trim();
      const selected = document.getElementById('prompt-list').value;
      const filename = newName || selected;
      if (!filename) { setStatus('prompt-status', '请输入新文件名或选择文件', true); return; }
      const res = await fetch(`/public/prompts/${encodeURIComponent(filename)}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({content: document.getElementById('prompt-content').value}) });
      if (!res.ok) { const data = await res.json(); setStatus('prompt-status', data.detail || '保存失败', true); return; }
      setStatus('prompt-status', '保存成功');
      await loadConfig();
    }
    async function loadPersona() {
      const target = document.getElementById('persona-list').value;
      const res = await fetch(`/public/personas/${encodeURIComponent(target)}`);
      const data = await res.json();
      document.getElementById('persona-content').value = data.content || '';
      setStatus('persona-status', '已加载 prompts 文件');
    }
    async function savePersona() {
      const newName = (document.getElementById('persona-new').value || '').trim();
      const selected = document.getElementById('persona-list').value;
      const filename = newName || selected;
      if (!filename) { setStatus('persona-status', '请输入新文件名或选择文件', true); return; }
      const res = await fetch(`/public/personas/${encodeURIComponent(filename)}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({content: document.getElementById('persona-content').value}) });
      if (!res.ok) { const data = await res.json(); setStatus('persona-status', data.detail || '保存失败', true); return; }
      setStatus('persona-status', '保存成功');
      await loadConfig();
    }
    async function saveModules() {
      const body = {
        planner: { modules: splitModules(document.getElementById('planner-modules').value).map(name => ({name, enabled: true})) },
        replyer: { modules: splitModules(document.getElementById('replyer-modules').value).map(name => ({name, enabled: true})) },
        memory: { modules: splitModules(document.getElementById('memory-modules').value).map(name => ({name, enabled: true})) },
      };
      const res = await fetch('/public/config/prompt-modules', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
      if (!res.ok) { const data = await res.json(); setStatus('modules-status', data.detail || '保存失败', true); return; }
      setStatus('modules-status', '模块编排已同步');
      await loadConfig();
    }
    async function loadMemory() {
      const res = await fetch('/public/memory');
      const data = await res.json();
      const users = (data.user_memories || []).map(x => `${x.username}: ${x.memory_text}`).join('\n');
      document.getElementById('memory-view').value = `self_memory:\n${data.self_memory || ''}\n\nuser_memories:\n${users}`;
    }
    loadConfig().then(loadMemory);
  </script>
</body>
</html>
"""


@router.get("/config", summary="读取公网编辑端配置")
def get_public_config(request: Request) -> dict[str, object]:
    return _public_editor_config(request)


@router.get("/prompts", summary="读取提示词列表")
def list_public_prompts(request: Request) -> dict[str, object]:
    data = _public_editor_config(request)
    return {"files": data["prompts"]}


@router.get("/personas", summary="读取人格列表（与 prompts 合并）")
def list_public_personas(request: Request) -> dict[str, object]:
    data = _public_editor_config(request)
    return {"files": data["prompts"]}


@router.get("/memory", summary="只读记忆")
def get_public_memory(request: Request) -> dict[str, object]:
    database = request.app.state.database
    return {
        "self_memory": database.get_self_memory(),
        "user_memories": database.list_user_memories(),
    }


@router.get("/prompts/{filename}", summary="读取提示词")
def get_public_prompt(filename: str, request: Request) -> dict[str, object]:
    safe = _normalize_markdown_filename(filename)
    content, readonly = _read_prompt_content(request, safe)
    return {"file": safe, "content": content, "readonly": readonly}


@router.put("/prompts/{filename}", summary="写入提示词（写入 prompts 并自动备份）")
def save_public_prompt(filename: str, payload: MarkdownContentPayload, request: Request) -> dict[str, object]:
    safe = _normalize_markdown_filename(filename)
    target, backup = write_prompt_file_with_backup(request.app.state.paths, safe, payload.content)
    return {
        "file": safe,
        "content": payload.content,
        "stored_in": "prompts",
        "stored_path": str(target.parent),
        "backup_path": str(backup),
    }


@router.get("/personas/{filename}", summary="读取人格（与 prompts 合并）")
def get_public_persona(filename: str, request: Request) -> dict[str, object]:
    safe = _normalize_markdown_filename(filename)
    content, readonly = _read_persona_content(request, safe)
    return {"file": safe, "content": content, "readonly": readonly}


@router.put("/personas/{filename}", summary="写入人格（写入 prompts 并自动备份）")
def save_public_persona(filename: str, payload: MarkdownContentPayload, request: Request) -> dict[str, object]:
    safe = _normalize_markdown_filename(filename)
    target, backup = write_prompt_file_with_backup(request.app.state.paths, safe, payload.content)
    return {
        "file": safe,
        "content": payload.content,
        "stored_in": "prompts",
        "stored_path": str(target.parent),
        "backup_path": str(backup),
    }


@router.put("/config/prompt-modules", summary="更新提示词模块编排")
def update_public_prompt_modules(payload: PromptModulesPayload, request: Request) -> dict[str, object]:
    available_files = set(available_module_files(request.app.state.paths))
    prompt_modules = PromptModulesConfig(
        planner=_build_route_config(payload.planner.modules, available_files),
        replyer=_build_route_config(payload.replyer.modules, available_files),
        memory=_build_route_config(payload.memory.modules, available_files),
    )
    try:
        validate_prompt_modules_config(request.app.state.paths, prompt_modules)
        save_prompt_modules(request.app.state.paths, prompt_modules)
        reloaded = load_settings(request.app.state.paths)
        request.app.state.settings = reloaded
        request.app.state.approval_service.settings = reloaded
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public_editor_config(request)
