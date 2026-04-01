from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from bot.settings import available_module_files
from bot.settings import ensure_prompt_storage
from bot.settings import protected_prompt_modules_for_route
from bot.settings import prompt_modules_to_dict
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


def _read_markdown_with_fallback(path: Path) -> str:
    data = path.read_bytes()
    gb18030_text = data.decode("gb18030", errors="replace")
    try:
        utf8_text = data.decode("utf-8")
    except UnicodeDecodeError:
        return gb18030_text
    utf8_cjk = sum(1 for ch in utf8_text if "\u4e00" <= ch <= "\u9fff")
    gb18030_cjk = sum(1 for ch in gb18030_text if "\u4e00" <= ch <= "\u9fff")
    if gb18030_cjk > utf8_cjk:
        return gb18030_text
    return utf8_text


def _read_prompt_content(request: Request, filename: str) -> tuple[str, bool]:
    prompt_file = _prompt_dir(request) / filename
    if prompt_file.is_file():
        return _read_markdown_with_fallback(prompt_file), False
    raise HTTPException(status_code=404, detail="未找到提示词文件")


def _read_persona_content(request: Request, filename: str) -> tuple[str, bool]:
    prompt_file = _prompt_dir(request) / filename
    if prompt_file.is_file():
        return _read_markdown_with_fallback(prompt_file), False
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


def _enforce_protected_modules(route_name: str, route: PromptRouteConfig, available_files: set[str]) -> PromptRouteConfig:
    protected_order = protected_prompt_modules_for_route(route_name, available_files=available_files)
    if not protected_order:
        return route
    protected_set = set(protected_order)
    existing_names = {module.name for module in route.modules}
    missing = [name for name in protected_order if name not in existing_names]
    if missing:
        missing_display = ", ".join(missing)
        raise HTTPException(status_code=400, detail=f"{route_name} 链路不可移出受保护模块: {missing_display}")
    current_order = [module.name for module in route.modules if module.name in protected_set]
    if current_order != list(protected_order):
        protected_display = ", ".join(protected_order)
        raise HTTPException(status_code=400, detail=f"{route_name} 链路受保护模块顺序必须为: {protected_display}")
    return route


def _public_editor_config(request: Request) -> dict[str, object]:
    prompts = sorted(path.name for path in _prompt_dir(request).glob("*.md"))
    settings = request.app.state.settings
    return {
        "prompts": prompts,
        "available_module_files": sorted(available_module_files(request.app.state.paths)),
        "prompt_modules": prompt_modules_to_dict(settings.prompt_modules),
    }


def _tail_lines(path: Path, lines: int) -> list[str]:
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return all_lines[-max(lines, 1) :]


@router.get("", response_class=HTMLResponse, summary="公网编辑端首页")
def public_editor_index() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SuenMeow Public Editor</title>
  <style>
    :root { color-scheme: light dark; --bg: #f3f4f6; --text: #1f2937; --card-bg: #ffffff; --border: #e5e7eb; --primary: #3b82f6; --primary-hover: #2563eb; --success: #059669; --error: #dc2626; --muted: #6b7280; --log-bg: #1e1e1e; --log-text: #d4d4d4; }
    @media (prefers-color-scheme: dark) { :root { --bg: #111827; --text: #f9fafb; --card-bg: #1f2937; --border: #374151; --log-bg: #000000; } }
    body { font-family: 'Microsoft YaHei', system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.5; }
    .header { background: var(--card-bg); border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 10; }
    .header h1 { margin: 0; font-size: 24px; font-weight: 600; letter-spacing: -0.5px; }
    .header .desc { margin: 4px 0 0; font-size: 13px; color: var(--muted); }
    .container { max-width: 1440px; margin: 32px auto; padding: 0 32px; display: grid; grid-template-columns: repeat(12, 1fr); gap: 24px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03); display: flex; flex-direction: column; }
    .col-span-4 { grid-column: span 4; }
    .col-span-8 { grid-column: span 8; }
    .col-span-12 { grid-column: span 12; }
    @media (max-width: 1024px) { .col-span-4, .col-span-8 { grid-column: span 12; } }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .card-header h2 { margin: 0; font-size: 18px; font-weight: 600; }
    .section-note { margin: 0 0 12px; color: var(--muted); font-size: 13px; line-height: 1.7; }
    label { display: block; font-size: 14px; font-weight: 500; margin: 12px 0 6px; color: var(--text); }
    select, textarea, input { width: 100%; box-sizing: border-box; font-family: inherit; font-size: 14px; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; transition: border-color 0.2s, box-shadow 0.2s; }
    select:focus, textarea:focus, input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1); }
    textarea { min-height: 200px; resize: vertical; }
    textarea.readonly { opacity: 0.95; }
    button { border: 0; border-radius: 8px; padding: 10px 16px; background: var(--primary); color: white; font-weight: 500; cursor: pointer; transition: background 0.2s; font-size: 14px; display: inline-flex; justify-content: center; align-items: center; }
    button:hover { background: var(--primary-hover); }
    button.secondary { background: var(--bg); color: var(--text); border: 1px solid var(--border); }
    button.secondary:hover { background: var(--border); }
    .btn-group { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; gap: 12px; }
    .status { font-size: 13px; color: var(--success); text-align: right; flex-grow: 1; }
    .status.error { color: var(--error); }
    .log-viewer { background: var(--log-bg); color: var(--log-text); font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; padding: 16px; border-radius: 8px; overflow-y: auto; height: 300px; white-space: pre-wrap; word-wrap: break-word; flex-grow: 1; margin: 0; }
    .log-meta { font-size: 13px; color: var(--muted); margin-bottom: 8px; }
    .flex-row { display: flex; gap: 12px; align-items: flex-end; }
    .flex-row > * { flex: 1; }
    .module-list { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
    .module-item { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg); }
    .module-item.empty { color: var(--muted); justify-content: center; font-size: 13px; }
    .module-toggle { display: flex; align-items: center; gap: 8px; margin: 0; font-weight: 500; flex: 1; }
    .module-toggle input { width: auto; }
    .module-actions { display: flex; gap: 8px; }
    .module-actions button { padding: 6px 10px; font-size: 12px; }
    .warn { background:#fff7ed; border:1px solid #fdba74; border-radius:10px; padding:10px 12px; color:#9a3412; font-size:13px; line-height:1.7; }
    .hint-pill { padding: 4px 8px; border-radius: 999px; background: rgba(245, 158, 11, 0.12); color: #b45309; font-size: 12px; font-weight: 600; }
  </style>
</head>
<body>
  <header class="header">
    <div>
      <h1>SuenMeow Public Editor</h1>
      <p class="desc">与 8000 管理端同款风格（精简版）：仅保留 Prompt Editor / Prompt Modules / User Memories / Self Memory / Latest Logs。</p>
    </div>
    <div class="hint-pill">受限公网端</div>
  </header>

  <div class="container">
    <section class="card col-span-8">
      <div class="card-header"><h2>📝 提示词编辑 (Prompt Editor)</h2></div>
      <p class="section-note">仅编辑 <code>prompts/</code> 下 Markdown 文件，保存时自动备份到 <code>prompts_backup/</code>。</p>
      <label for="prompt-file">选择文件</label>
      <select id="prompt-file"></select>
      <label for="prompt-new-file">或新建文件名</label>
      <input id="prompt-new-file" type="text" placeholder="例如: custom_public_prompt.md" />
      <label for="prompt-content">内容</label>
      <textarea id="prompt-content" placeholder="载入中..."></textarea>
      <div class="btn-group">
        <button class="secondary" type="button" onclick="loadPrompt()">🔄 重新载入</button>
        <button type="button" onclick="savePrompt()">💾 保存 / 创建提示词</button>
        <div id="prompt-status" class="status"></div>
      </div>
      <div class="warn" style="margin-top:12px;">
        使用提醒：先复制已有模块再修改，变更后在“Prompt Modules”中确认链路是否启用。若效果异常，先禁用新增模块回滚。
      </div>
    </section>

    <section class="card col-span-4">
      <div class="card-header"><h2>🎛️ 模块编排 (Prompt Modules)</h2></div>
        <p class="section-note">仅操作 planner / replyer / memory 三条链路，支持启用开关、顺序调整与移出。受保护模块不可移出。</p>

      <label for="planner-add-module">Planner 模块链</label>
      <div id="planner-modules" class="module-list"></div>
      <div class="flex-row">
        <div>
          <label for="planner-add-module">添加模块到 Planner</label>
          <select id="planner-add-module"></select>
        </div>
        <button class="secondary" type="button" onclick="addPromptModule('planner')">➕ 添加</button>
      </div>

      <label for="replyer-add-module" style="margin-top:16px;">Replyer 模块链</label>
      <div id="replyer-modules" class="module-list"></div>
      <div class="flex-row">
        <div>
          <label for="replyer-add-module">添加模块到 Replyer</label>
          <select id="replyer-add-module"></select>
        </div>
        <button class="secondary" type="button" onclick="addPromptModule('replyer')">➕ 添加</button>
      </div>

      <label for="memory-add-module" style="margin-top:16px;">Memory 模块链</label>
      <div id="memory-modules" class="module-list"></div>
      <div class="flex-row">
        <div>
          <label for="memory-add-module">添加模块到 Memory</label>
          <select id="memory-add-module"></select>
        </div>
        <button class="secondary" type="button" onclick="addPromptModule('memory')">➕ 添加</button>
      </div>

      <div class="btn-group">
        <button type="button" onclick="savePromptModules()">💾 保存模块编排</button>
        <div id="prompt-modules-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-4">
      <div class="card-header"><h2>🧠 自我记忆 (Self Memory)</h2></div>
      <p class="section-note">公网端保持只读，不提供记忆写入。</p>
      <label for="self-memory">当前记忆</label>
      <textarea id="self-memory" class="readonly" readonly placeholder="暂无记忆..."></textarea>
      <div class="btn-group">
        <button class="secondary" type="button" onclick="loadMemoryView()">🔄 刷新</button>
        <div id="self-memory-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-8">
      <div class="card-header"><h2>👥 用户记忆 (User Memories)</h2></div>
      <p class="section-note">公网端仅浏览用户记忆，便于检查上下文，禁止直接写入。</p>
      <div class="flex-row">
        <div>
          <label for="user-memory-select">选择已有用户</label>
          <select id="user-memory-select">
            <option value="">-- 选择用户 --</option>
          </select>
        </div>
        <div>
          <label for="user-memory-name">用户名</label>
          <input type="text" id="user-memory-name" readonly />
        </div>
      </div>
      <label for="user-memory-content">记忆内容</label>
      <textarea id="user-memory-content" class="readonly" readonly placeholder="该用户暂无记忆"></textarea>
      <div class="btn-group">
        <button class="secondary" type="button" onclick="loadMemoryView()">🔄 刷新用户列表</button>
        <div id="user-memory-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-12">
      <div class="card-header">
        <h2>📋 运行日志 (Latest Logs)</h2>
        <button class="secondary" type="button" onclick="loadLogs()" style="padding: 6px 12px; margin: 0;">🔄 刷新</button>
      </div>
      <div class="log-meta"><span id="log-filename">未加载日志文件</span></div>
      <pre id="log-viewer" class="log-viewer">等待获取日志...</pre>
    </section>
  </div>

  <script>
    function setStatus(id, msg, error = false) {
      const el = document.getElementById(id);
      el.textContent = msg;
      el.className = 'status' + (error ? ' error' : '');
      setTimeout(() => {
        if (el.textContent === msg) {
          el.textContent = '';
        }
      }, 3000);
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    async function readErrorDetail(res, fallback) {
      try {
        const data = await res.json();
        return data.detail || fallback;
      } catch (_error) {
        return fallback;
      }
    }

    function ensureMarkdownFilename(name) {
      const trimmed = String(name || '').trim();
      if (!trimmed) return '';
      return trimmed.endsWith('.md') ? trimmed : `${trimmed}.md`;
    }

    function setSelectOptions(selectId, files, preferred = '') {
      const select = document.getElementById(selectId);
      if (!files.length) {
        select.innerHTML = '<option value="">-- 暂无文件 --</option>';
        select.disabled = true;
        return;
      }
      select.disabled = false;
      select.innerHTML = files.map(file => `<option value="${escapeHtml(file)}">${escapeHtml(file)}</option>`).join('');
      select.value = files.includes(preferred) ? preferred : files[0];
    }

    function resolveEditorFilename(selectId, inputId) {
      const fromInput = ensureMarkdownFilename(document.getElementById(inputId).value);
      if (fromInput) return fromInput;
      return document.getElementById(selectId).value;
    }

    async function refreshPromptFiles(preferred = '') {
      const res = await fetch('/public/prompts');
      if (!res.ok) throw new Error(await readErrorDetail(res, '加载提示词列表失败'));
      const data = await res.json();
      setSelectOptions('prompt-file', data.files || [], preferred);
    }

    async function loadPrompt() {
      const file = document.getElementById('prompt-file').value;
      if (!file) return;
      try {
        const res = await fetch(`/public/prompts/${encodeURIComponent(file)}`);
        if (!res.ok) {
          setStatus('prompt-status', await readErrorDetail(res, '加载失败'), true);
          return;
        }
        const data = await res.json();
        document.getElementById('prompt-content').value = data.content ?? '';
      } catch (_error) {
        setStatus('prompt-status', '加载失败', true);
      }
    }

    async function savePrompt() {
      const file = resolveEditorFilename('prompt-file', 'prompt-new-file');
      const content = document.getElementById('prompt-content').value;
      if (!file) {
        setStatus('prompt-status', '请输入文件名', true);
        return;
      }
      try {
        const res = await fetch(`/public/prompts/${encodeURIComponent(file)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        });
        if (!res.ok) {
          setStatus('prompt-status', await readErrorDetail(res, '保存失败 ✗'), true);
          return;
        }
        await refreshPromptFiles(file);
        await loadPromptModules();
        document.getElementById('prompt-new-file').value = '';
        setStatus('prompt-status', '保存成功 ✓');
      } catch (_error) {
        setStatus('prompt-status', '网络错误', true);
      }
    }

    let promptModuleState = { planner: [], replyer: [], memory: [] };
    let availableModuleFiles = [];

    function renderPromptModuleRoute(route) {
      const container = document.getElementById(`${route}-modules`);
      const modules = promptModuleState[route] || [];
      if (!modules.length) {
        container.innerHTML = '<div class="module-item empty">当前没有模块，可从 prompts 中添加。</div>';
      } else {
        container.innerHTML = modules.map((module, index) => `
          <div class="module-item">
            <label class="module-toggle">
              <input type="checkbox" ${module.enabled ? 'checked' : ''} onchange="togglePromptModule('${route}', ${index}, this.checked)">
              <span>${escapeHtml(module.name)}</span>
            </label>
            <div class="module-actions">
              <button class="secondary" type="button" onclick="movePromptModule('${route}', ${index}, -1)" ${index === 0 ? 'disabled' : ''}>↑</button>
              <button class="secondary" type="button" onclick="movePromptModule('${route}', ${index}, 1)" ${index === modules.length - 1 ? 'disabled' : ''}>↓</button>
              <button class="secondary" type="button" onclick="removePromptModule('${route}', ${index})" ${(module.removable === false) ? 'disabled title="受保护模块不可移出"' : ''}>移出</button>
            </div>
          </div>
        `).join('');
      }
      refreshPromptModuleOptions(route);
    }

    function refreshPromptModuleOptions(route) {
      const select = document.getElementById(`${route}-add-module`);
      const used = new Set((promptModuleState[route] || []).map(module => module.name));
      const candidates = availableModuleFiles.filter(name => !used.has(name));
      if (!candidates.length) {
        select.innerHTML = '<option value="">没有可添加的模块</option>';
        select.disabled = true;
        return;
      }
      select.disabled = false;
      select.innerHTML = candidates.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    }

    function togglePromptModule(route, index, enabled) {
      promptModuleState[route][index].enabled = enabled;
    }

    function movePromptModule(route, index, delta) {
      const modules = promptModuleState[route];
      const nextIndex = index + delta;
      if (nextIndex < 0 || nextIndex >= modules.length) return;
      [modules[index], modules[nextIndex]] = [modules[nextIndex], modules[index]];
      renderPromptModuleRoute(route);
    }

    function addPromptModule(route) {
      const select = document.getElementById(`${route}-add-module`);
      const name = select.value;
      if (!name) return;
      promptModuleState[route].push({ name, enabled: true, removable: true, protected: false });
      renderPromptModuleRoute(route);
    }

    function removePromptModule(route, index) {
      const modules = promptModuleState[route];
      const target = modules[index];
      if (!target || target.removable === false) return;
      modules.splice(index, 1);
      renderPromptModuleRoute(route);
    }

    function hydratePromptModules(data) {
      availableModuleFiles = data.available_module_files || [];
      const promptModules = data.prompt_modules || { planner: [], replyer: [], memory: [] };
      promptModuleState = {
        planner: (promptModules.planner || []).map(item => ({
          name: item.name,
          enabled: Boolean(item.enabled),
          removable: item.removable !== false,
          protected: Boolean(item.protected),
        })),
        replyer: (promptModules.replyer || []).map(item => ({
          name: item.name,
          enabled: Boolean(item.enabled),
          removable: item.removable !== false,
          protected: Boolean(item.protected),
        })),
        memory: (promptModules.memory || []).map(item => ({
          name: item.name,
          enabled: Boolean(item.enabled),
          removable: item.removable !== false,
          protected: Boolean(item.protected),
        })),
      };
      renderPromptModuleRoute('planner');
      renderPromptModuleRoute('replyer');
      renderPromptModuleRoute('memory');
    }

    async function loadPromptModules() {
      const res = await fetch('/public/config');
      if (!res.ok) {
        setStatus('prompt-modules-status', await readErrorDetail(res, '加载模块编排失败'), true);
        return;
      }
      const data = await res.json();
      hydratePromptModules(data);
    }

    async function savePromptModules() {
      try {
        const res = await fetch('/public/config/prompt-modules', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            planner: { modules: promptModuleState.planner },
            replyer: { modules: promptModuleState.replyer },
            memory: { modules: promptModuleState.memory },
          }),
        });
        if (!res.ok) {
          setStatus('prompt-modules-status', await readErrorDetail(res, '保存失败 ✗'), true);
          return;
        }
        const data = await res.json();
        hydratePromptModules(data);
        setStatus('prompt-modules-status', '保存成功 ✓');
      } catch (_error) {
        setStatus('prompt-modules-status', '网络错误', true);
      }
    }

    let userMemoriesData = [];

    function renderSelectedUserMemory() {
      const selected = document.getElementById('user-memory-select').value;
      const input = document.getElementById('user-memory-name');
      const textarea = document.getElementById('user-memory-content');
      input.value = selected;
      if (!selected) {
        textarea.value = '';
        return;
      }
      const matched = userMemoriesData.find(item => item.username === selected);
      textarea.value = matched ? matched.memory_text : '';
    }

    async function loadMemoryView() {
      try {
        const res = await fetch('/public/memory');
        if (!res.ok) {
          setStatus('self-memory-status', await readErrorDetail(res, '加载记忆失败'), true);
          return;
        }
        const data = await res.json();
        document.getElementById('self-memory').value = data.self_memory ?? '';
        userMemoriesData = data.user_memories || [];

        const select = document.getElementById('user-memory-select');
        const previous = select.value;
        select.innerHTML = '<option value="">-- 选择用户 --</option>';
        userMemoriesData.forEach(item => {
          const option = document.createElement('option');
          option.value = item.username;
          option.textContent = item.username;
          select.appendChild(option);
        });

        if (previous && userMemoriesData.some(item => item.username === previous)) {
          select.value = previous;
        } else if (userMemoriesData.length > 0) {
          select.value = userMemoriesData[0].username;
        }

        renderSelectedUserMemory();
        setStatus('self-memory-status', '记忆已刷新 ✓');
        setStatus('user-memory-status', `已加载 ${userMemoriesData.length} 条用户记忆`);
      } catch (_error) {
        setStatus('self-memory-status', '网络错误', true);
      }
    }

    async function loadLogs() {
      try {
        const res = await fetch('/public/logs/latest?lines=200');
        if (!res.ok) {
          setStatus('prompt-modules-status', await readErrorDetail(res, '加载日志失败'), true);
          return;
        }
        const data = await res.json();
        const viewer = document.getElementById('log-viewer');
        if (data.file) {
          document.getElementById('log-filename').textContent = `当前文件: ${data.file}`;
          viewer.textContent = (data.lines || []).join('\n');
          viewer.scrollTop = viewer.scrollHeight;
        } else {
          document.getElementById('log-filename').textContent = '暂无日志文件';
          viewer.textContent = '';
        }
      } catch (_error) {
        document.getElementById('log-viewer').textContent = '获取日志失败...';
      }
    }

    document.getElementById('prompt-file').addEventListener('change', loadPrompt);
    document.getElementById('user-memory-select').addEventListener('change', renderSelectedUserMemory);

    refreshPromptFiles().then(() => {
      if (document.getElementById('prompt-file').value) {
        loadPrompt();
      }
    });
    loadPromptModules();
    loadMemoryView();
    loadLogs();

    setInterval(() => {
      loadLogs();
    }, 10000);
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


@router.get("/logs/latest", summary="查看最新日志（只读）")
def get_public_latest_log(request: Request, lines: int = 200) -> dict[str, object]:
    bounded_lines = min(max(lines, 1), 1000)
    log_dir = request.app.state.paths.log_dir
    log_files = sorted(log_dir.glob("*.log*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not log_files:
        return {"file": None, "lines": []}
    latest = log_files[0]
    content = _tail_lines(latest, bounded_lines)
    return {"file": latest.name, "lines": content}


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
        planner=_enforce_protected_modules(
            "planner",
            _build_route_config(payload.planner.modules, available_files),
            available_files,
        ),
        replyer=_enforce_protected_modules(
            "replyer",
            _build_route_config(payload.replyer.modules, available_files),
            available_files,
        ),
        memory=_enforce_protected_modules(
            "memory",
            _build_route_config(payload.memory.modules, available_files),
            available_files,
        ),
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
