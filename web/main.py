from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse

from bot.logging_utils import configure_logging
from bot.approval_service import ApprovalService
from bot.settings import AppPaths, load_settings
from db.repositories import Database
from web.routes.config import router as config_router
from web.routes.logs import router as logs_router
from web.routes.memory import router as memory_router
from web.routes.personas import router as personas_router
from web.routes.prompts import router as prompts_router
from web.routes.topics import router as topics_router


def create_app(root: Path) -> FastAPI:
    paths = AppPaths.from_root(root)
    configure_logging(paths.log_dir)
    settings = load_settings(paths)
    database = Database(paths.database_path)
    database.initialize()

    app = FastAPI(
        title="SuenMeow Admin",
        description="SuenMeow 的管理与调试接口。",
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: object, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.state.paths = paths
    app.state.settings = settings
    app.state.database = database
    app.state.approval_service = ApprovalService(database, settings)

    @app.get("/", summary="首页", response_class=HTMLResponse)
    def index() -> str:
        prompt_dir = paths.root / "prompts"
        persona_dir = paths.root / "personas"
        prompt_modules_hint = "支持把 prompts 与 personas（如 core.md、catgirl.md）一起编排到 planner / replyer / memory 链路中。"
        editable_config_options = "".join(
            f'<option value="{name}">{name}</option>'
            for name in (
                "forum.toml",
                "models.toml",
                "personas.toml",
                "prompt_modules.toml",
                "runtime.toml",
                "scheduler.toml",
                "thresholds.toml",
                "webui.toml",
            )
        )
        runtime_badges: list[str] = []
        if settings.runtime.read_only:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">🔒 只读模式</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(5, 150, 105, 0.1); color: var(--success); font-size: 12px; font-weight: 500;">🔓 读写模式</span>'
            )
        if settings.runtime.allow_send_reply:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(5, 150, 105, 0.1); color: var(--success); font-size: 12px; font-weight: 500;">✉️ 可发送</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(220, 38, 38, 0.1); color: var(--error); font-size: 12px; font-weight: 500;">🚫 禁发送</span>'
            )
        if settings.runtime.require_approval_before_send:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(59, 130, 246, 0.1); color: var(--primary); font-size: 12px; font-weight: 500;">🛡️ 需审批</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">⚡ 免审批</span>'
            )
        if settings.runtime.mark_notifications_read:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(59, 130, 246, 0.1); color: var(--primary); font-size: 12px; font-weight: 500;">📬 标记通知已读</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">📭 保留未读通知</span>'
            )
        if settings.runtime.shadow_mode:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(245, 158, 11, 0.15); color: #b45309; font-size: 12px; font-weight: 500;">🫥 影子模式</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">☀️ 非影子模式</span>'
            )
        if settings.runtime.panic_switch:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(220, 38, 38, 0.12); color: var(--error); font-size: 12px; font-weight: 500;">🛑 Panic 开关开启</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(5, 150, 105, 0.1); color: var(--success); font-size: 12px; font-weight: 500;">🟢 Panic 开关关闭</span>'
            )
        if (
            settings.runtime.blackout_start_hour is not None
            and settings.runtime.blackout_end_hour is not None
            and settings.runtime.blackout_start_hour != settings.runtime.blackout_end_hour
        ):
            runtime_badges.append(
                f'<span style="padding: 4px 8px; border-radius: 4px; background: rgba(99, 102, 241, 0.12); color: #4338ca; font-size: 12px; font-weight: 500;">🌙 黑窗 {settings.runtime.blackout_start_hour:02d}:00-{settings.runtime.blackout_end_hour:02d}:00 UTC</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">🌤️ 无黑窗</span>'
            )
        if settings.runtime.topic_cooldown_minutes > 0:
            runtime_badges.append(
                f'<span style="padding: 4px 8px; border-radius: 4px; background: rgba(14, 165, 233, 0.12); color: #0369a1; font-size: 12px; font-weight: 500;">⏱️ 主题冷却 {settings.runtime.topic_cooldown_minutes}m</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">⏱️ 无主题冷却</span>'
            )
        if settings.runtime.muted_topic_ids:
            runtime_badges.append(
                f'<span style="padding: 4px 8px; border-radius: 4px; background: rgba(168, 85, 247, 0.12); color: #7e22ce; font-size: 12px; font-weight: 500;">🔕 静音主题 {len(settings.runtime.muted_topic_ids)}</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">🔔 无静音主题</span>'
            )
        if settings.runtime.muted_usernames:
            runtime_badges.append(
                f'<span style="padding: 4px 8px; border-radius: 4px; background: rgba(236, 72, 153, 0.12); color: #be185d; font-size: 12px; font-weight: 500;">🙈 静音用户 {len(settings.runtime.muted_usernames)}</span>'
            )
        else:
            runtime_badges.append(
                '<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">👥 无静音用户</span>'
            )
        runtime_badges_html = "".join(runtime_badges)
        prompt_options = "".join(
            f'<option value="{path.name}">{path.name}</option>' for path in sorted(prompt_dir.glob("*.md"))
        )
        persona_options = "".join(
            f'<option value="{path.name}">{path.name}</option>' for path in sorted(persona_dir.glob("*.md"))
        )
        return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SuenMeow Admin</title>
  <style>
    :root {{ color-scheme: light dark; --bg: #f3f4f6; --text: #1f2937; --card-bg: #ffffff; --border: #e5e7eb; --primary: #3b82f6; --primary-hover: #2563eb; --success: #059669; --error: #dc2626; --muted: #6b7280; --log-bg: #1e1e1e; --log-text: #d4d4d4; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg: #111827; --text: #f9fafb; --card-bg: #1f2937; --border: #374151; --log-bg: #000000; }} }}
    body {{ font-family: 'Microsoft YaHei', system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.5; }}
    .header {{ background: var(--card-bg); border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 10; }}
    .header h1 {{ margin: 0; font-size: 24px; font-weight: 600; letter-spacing: -0.5px; }}
    .links a {{ color: var(--primary); text-decoration: none; margin-left: 16px; font-size: 14px; font-weight: 500; }}
    .links a:hover {{ text-decoration: underline; }}
    .container {{ max-width: 1400px; margin: 32px auto; padding: 0 32px; display: grid; grid-template-columns: repeat(12, 1fr); gap: 24px; }}
    .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03); display: flex; flex-direction: column; }}
    .col-span-4 {{ grid-column: span 4; }}
    .col-span-6 {{ grid-column: span 6; }}
    .col-span-8 {{ grid-column: span 8; }}
    .col-span-12 {{ grid-column: span 12; }}
    @media (max-width: 1024px) {{ .col-span-4, .col-span-6, .col-span-8 {{ grid-column: span 12; }} }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
    .card-header h2 {{ margin: 0; font-size: 18px; font-weight: 600; }}
    label {{ display: block; font-size: 14px; font-weight: 500; margin: 12px 0 6px; color: var(--text); }}
    select, textarea, input {{ width: 100%; box-sizing: border-box; font-family: inherit; font-size: 14px; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; transition: border-color 0.2s, box-shadow 0.2s; }}
    select:focus, textarea:focus, input:focus {{ outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1); }}
    textarea {{ min-height: 200px; resize: vertical; flex-grow: 1; }}
    .btn-group {{ display: flex; justify-content: space-between; align-items: center; margin-top: 16px; gap: 12px; }}
    button {{ border: 0; border-radius: 8px; padding: 10px 16px; background: var(--primary); color: white; font-weight: 500; cursor: pointer; transition: background 0.2s; font-size: 14px; display: inline-flex; justify-content: center; align-items: center; }}
    button:hover {{ background: var(--primary-hover); }}
    button.secondary {{ background: var(--bg); color: var(--text); border: 1px solid var(--border); }}
    button.secondary:hover {{ background: var(--border); }}
    .status {{ font-size: 13px; color: var(--success); text-align: right; flex-grow: 1; }}
    .status.error {{ color: var(--error); }}
    .log-viewer {{ background: var(--log-bg); color: var(--log-text); font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; padding: 16px; border-radius: 8px; overflow-y: auto; height: 300px; white-space: pre-wrap; word-wrap: break-word; flex-grow: 1; margin: 0; }}
    .log-meta {{ font-size: 13px; color: var(--muted); margin-bottom: 8px; display: flex; justify-content: space-between; }}
    .flex-row {{ display: flex; gap: 12px; align-items: flex-end; }}
    .flex-row > * {{ flex: 1; }}
    .flex-row > .auto-width {{ flex: 0 0 auto; margin-bottom: 2px; }}
    .module-route-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
    .module-list {{ display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }}
    .module-item {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg); }}
    .module-item.empty {{ color: var(--muted); justify-content: center; font-size: 13px; }}
    .module-toggle {{ display: flex; align-items: center; gap: 8px; margin: 0; font-weight: 500; flex: 1; }}
    .module-toggle input {{ width: auto; }}
    .module-actions {{ display: flex; gap: 8px; }}
    .module-actions button {{ padding: 6px 10px; font-size: 12px; }}
    .section-note {{ margin: 0 0 12px; color: var(--muted); font-size: 13px; }}
    @media (max-width: 1024px) {{ .module-route-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header class="header">
    <div>
      <h1>SuenMeow Admin</h1>
      <p style="margin: 4px 0 0; font-size: 13px; color: var(--muted);">中文管理页：可直接查看和编辑提示词、人格设定与自我记忆。</p>
    </div>
    <div class="links">
      <a href="/docs" target="_blank">Swagger 文档</a>
      <a href="/openapi.json" target="_blank">OpenAPI</a>
    </div>
  </header>

  <div class="container">
    <!-- Runtime Status -->
    <section class="card col-span-12" style="flex-direction: row; align-items: center; justify-content: space-between; padding: 16px 24px;">
      <div style="display: flex; align-items: center; gap: 16px;">
        <h2 style="margin: 0; font-size: 16px; font-weight: 600;">⚙️ 运行状态 (Runtime Status)</h2>
        <div id="runtime-status-badges" style="display: flex; gap: 8px;">
          {runtime_badges_html}
        </div>
      </div>
      <div>
        <a href="/config" target="_blank" style="font-size: 13px; color: var(--primary); text-decoration: none;">查看完整配置</a>
      </div>
    </section>

    <section class="card col-span-6">
      <div class="card-header"><h2>🎛️ 运行模式切换 (Runtime Modes)</h2></div>
      <p class="section-note">安全模式优先：只允许切换非敏感运行时策略，不在 WebUI 明文编辑密钥和账密。</p>
      <label for="runtime-mode-select">选择运行模式</label>
      <select id="runtime-mode-select">
        <option value="read-only">只读模式</option>
        <option value="shadow">影子模式</option>
        <option value="approval">审批模式</option>
        <option value="direct-send">直接发送模式</option>
      </select>
      <div style="margin-top: 12px; color: var(--muted); font-size: 13px; line-height: 1.7;">
        <div>只读：不发送，只观察</div>
        <div>影子：生成草稿并记流水，但不发送、不进审批</div>
        <div>审批：生成草稿并进入待审批列表</div>
        <div>直接发送：满足条件时直接发送</div>
      </div>
      <div class="btn-group">
        <button type="button" onclick="saveRuntimeMode()">💾 保存运行模式</button>
        <div id="runtime-mode-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-6">
      <div class="card-header"><h2>🧾 非敏感配置编辑 (Config Editor)</h2></div>
      <p class="section-note">这里只开放非敏感 TOML：runtime / webui / scheduler / thresholds / forum / models / personas / prompt modules。账号密码与 API key 不可在这里直接编辑。</p>
      <label for="editable-config-file">选择配置文件</label>
      <select id="editable-config-file">{editable_config_options}</select>
      <label for="editable-config-content">配置内容</label>
      <textarea id="editable-config-content" placeholder="载入中..."></textarea>
      <div class="btn-group">
        <button class="secondary" type="button" onclick="loadEditableConfig()">🔄 重新载入</button>
        <button type="button" onclick="saveEditableConfig()">💾 保存配置</button>
        <div id="editable-config-status" class="status"></div>
      </div>
    </section>

    <!-- Row 1: Configurations -->
    <section class="card col-span-4">
      <div class="card-header"><h2>📝 提示词编辑 (Prompts)</h2></div>
      <label for="prompt-file">选择文件</label>
      <select id="prompt-file">{prompt_options}</select>
      <label for="prompt-new-file">或新建文件名</label>
      <input id="prompt-new-file" type="text" placeholder="例如: custom_prompt.md" />
      <label for="prompt-content">内容</label>
      <textarea id="prompt-content" placeholder="载入中..."></textarea>
      <div class="btn-group">
        <button type="button" onclick="savePrompt()">💾 保存 / 创建提示词</button>
        <div id="prompt-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-4">
      <div class="card-header"><h2>🎭 人格编辑 (Personas)</h2></div>
      <label for="persona-file">选择文件</label>
      <select id="persona-file">{persona_options}</select>
      <label for="persona-new-file">或新建文件名</label>
      <input id="persona-new-file" type="text" placeholder="例如: helper.md" />
      <label for="persona-content">内容</label>
      <textarea id="persona-content" placeholder="载入中..."></textarea>
      <div class="btn-group">
        <button type="button" onclick="savePersona()">💾 保存 / 创建人格</button>
        <div id="persona-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-4">
      <div class="card-header"><h2>🧠 自我记忆 (Self Memory)</h2></div>
      <label for="self-memory">当前记忆状态</label>
      <textarea id="self-memory" placeholder="暂无记忆..."></textarea>
      <div class="btn-group">
        <button type="button" onclick="saveSelfMemory()">💾 保存记忆</button>
        <div id="self-memory-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-12">
      <div class="card-header"><h2>🧩 提示词模块编排 (Prompt Modules)</h2></div>
      <p class="section-note">{prompt_modules_hint}</p>
      <div class="module-route-grid">
        <div>
          <label for="planner-add-module">Planner 模块链</label>
          <div id="planner-modules" class="module-list"></div>
          <div class="flex-row">
            <div>
              <label for="planner-add-module">添加模块到 Planner</label>
              <select id="planner-add-module"></select>
            </div>
            <button class="secondary auto-width" type="button" onclick="addPromptModule('planner')">➕ 添加</button>
          </div>
        </div>
        <div>
          <label for="replyer-add-module">Replyer 模块链</label>
          <div id="replyer-modules" class="module-list"></div>
          <div class="flex-row">
            <div>
              <label for="replyer-add-module">添加模块到 Replyer</label>
              <select id="replyer-add-module"></select>
            </div>
            <button class="secondary auto-width" type="button" onclick="addPromptModule('replyer')">➕ 添加</button>
          </div>
        </div>
        <div>
          <label for="memory-add-module">Memory 模块链</label>
          <div id="memory-modules" class="module-list"></div>
          <div class="flex-row">
            <div>
              <label for="memory-add-module">添加模块到 Memory</label>
              <select id="memory-add-module"></select>
            </div>
            <button class="secondary auto-width" type="button" onclick="addPromptModule('memory')">➕ 添加</button>
          </div>
        </div>
      </div>
      <div class="btn-group">
        <button type="button" onclick="savePromptModules()">💾 保存模块编排</button>
        <div id="prompt-modules-status" class="status"></div>
      </div>
    </section>

    <!-- Row 2: User Memory & Logs -->
    <section class="card col-span-6">
      <div class="card-header"><h2>👥 用户记忆 (User Memories)</h2></div>
      <div class="flex-row">
        <div>
          <label for="user-memory-select">选择已有用户</label>
          <select id="user-memory-select">
            <option value="">-- 选择用户 --</option>
          </select>
        </div>
        <div>
          <label for="user-memory-name">或输入用户名</label>
          <input type="text" id="user-memory-name" placeholder="例如: user123" />
        </div>
      </div>
      <label for="user-memory-content">记忆内容</label>
      <textarea id="user-memory-content" placeholder="该用户的专属记忆..."></textarea>
      <div class="btn-group">
        <button type="button" onclick="saveUserMemory()">💾 保存用户记忆</button>
        <div id="user-memory-status" class="status"></div>
      </div>
    </section>

    <section class="card col-span-6">
      <div class="card-header">
        <h2>📋 运行日志 (Latest Logs)</h2>
        <button class="secondary" type="button" onclick="loadLogs()" style="padding: 6px 12px; margin: 0;">🔄 刷新</button>
      </div>
      <div class="log-meta">
        <span id="log-filename">未加载日志文件</span>
      </div>
      <pre id="log-viewer" class="log-viewer">等待获取日志...</pre>
    </section>

    <!-- Row 3: Pending Approvals -->
    <section class="card col-span-12">
      <div class="card-header">
        <h2>⏳ 待审核回复 (Pending Approvals)</h2>
        <button class="secondary" type="button" onclick="loadPendingApprovals()" style="padding: 6px 12px; margin: 0;">🔄 刷新</button>
      </div>
      <div id="pending-approvals-container" style="display: flex; flex-direction: column; gap: 12px; max-height: 400px; overflow-y: auto; padding-right: 4px;">
        <div class="module-item empty">等待获取待审核数据...</div>
      </div>
    </section>

    <!-- Row 4: Pipeline Runs -->
    <section class="card col-span-12">
      <div class="card-header">
        <h2>🚀 流水线追踪 (Pipeline Runs)</h2>
        <button class="secondary" type="button" onclick="loadPipelineRuns()" style="padding: 6px 12px; margin: 0;">🔄 刷新</button>
      </div>
      <div id="pipeline-runs-container" style="display: flex; flex-direction: column; gap: 12px; max-height: 400px; overflow-y: auto; padding-right: 4px;">
        <div class="module-item empty">等待获取流水线记录...</div>
      </div>
    </section>
  </div>

  <script>
    // Utils
    function setStatus(id, msg, isError = false) {{
      const el = document.getElementById(id);
      el.textContent = msg;
      el.className = 'status' + (isError ? ' error' : '');
      setTimeout(() => el.textContent = '', 3000);
    }}

    function ensureMarkdownFilename(name) {{
      const trimmed = String(name || '').trim();
      if (!trimmed) return '';
      return trimmed.endsWith('.md') ? trimmed : `${{trimmed}}.md`;
    }}

    function setSelectOptions(selectId, files, preferred = '') {{
      const select = document.getElementById(selectId);
      if (!files.length) {{
        select.innerHTML = '<option value="">-- 暂无文件 --</option>';
        select.disabled = true;
        return;
      }}
      select.disabled = false;
      select.innerHTML = files.map(file => `<option value="${{escapeHtml(file)}}">${{escapeHtml(file)}}</option>`).join('');
      select.value = files.includes(preferred) ? preferred : files[0];
    }}

    async function readErrorDetail(res, fallback) {{
      try {{
        const data = await res.json();
        return data.detail || fallback;
      }} catch (_error) {{
        return fallback;
      }}
    }}

    async function refreshPromptFiles(preferred = '') {{
      const res = await fetch('/prompts');
      if (!res.ok) throw new Error(await readErrorDetail(res, '加载提示词列表失败'));
      const data = await res.json();
      setSelectOptions('prompt-file', data.files || [], preferred);
    }}

    async function refreshPersonaFiles(preferred = '') {{
      const res = await fetch('/personas');
      if (!res.ok) throw new Error(await readErrorDetail(res, '加载人格列表失败'));
      const data = await res.json();
      setSelectOptions('persona-file', data.files || [], preferred);
    }}

    function resolveEditorFilename(selectId, inputId) {{
      const fromInput = ensureMarkdownFilename(document.getElementById(inputId).value);
      if (fromInput) return fromInput;
      return document.getElementById(selectId).value;
    }}

    // Prompts
    async function loadPrompt() {{
      const file = document.getElementById('prompt-file').value;
      if (!file) return;
      try {{
        const res = await fetch(`/prompts/${{encodeURIComponent(file)}}`);
        if (!res.ok) {{
          setStatus('prompt-status', await readErrorDetail(res, '加载失败'), true);
          return;
        }}
        const data = await res.json();
        document.getElementById('prompt-content').value = data.content ?? '';
      }} catch (_error) {{ setStatus('prompt-status', '加载失败', true); }}
    }}
    async function savePrompt() {{
      const file = resolveEditorFilename('prompt-file', 'prompt-new-file');
      const content = document.getElementById('prompt-content').value;
      if (!file) {{
        setStatus('prompt-status', '请输入文件名', true);
        return;
      }}
      try {{
        const res = await fetch(`/prompts/${{encodeURIComponent(file)}}`, {{
          method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ content }})
        }});
        if (!res.ok) {{
          setStatus('prompt-status', await readErrorDetail(res, '保存失败 ✗'), true);
          return;
        }}
        await refreshPromptFiles(file);
        await loadPromptModules();
        document.getElementById('prompt-new-file').value = '';
        setStatus('prompt-status', '保存成功 ✓');
      }} catch (_error) {{ setStatus('prompt-status', '网络错误', true); }}
    }}

    // Personas
    async function loadPersona() {{
      const file = document.getElementById('persona-file').value;
      if (!file) return;
      try {{
        const res = await fetch(`/personas/${{encodeURIComponent(file)}}`);
        if (!res.ok) {{
          setStatus('persona-status', await readErrorDetail(res, '加载失败'), true);
          return;
        }}
        const data = await res.json();
        document.getElementById('persona-content').value = data.content ?? '';
      }} catch (_error) {{ setStatus('persona-status', '加载失败', true); }}
    }}
    async function savePersona() {{
      const file = resolveEditorFilename('persona-file', 'persona-new-file');
      const content = document.getElementById('persona-content').value;
      if (!file) {{
        setStatus('persona-status', '请输入文件名', true);
        return;
      }}
      try {{
        const res = await fetch(`/personas/${{encodeURIComponent(file)}}`, {{
          method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ content }})
        }});
        if (!res.ok) {{
          setStatus('persona-status', await readErrorDetail(res, '保存失败 ✗'), true);
          return;
        }}
        await refreshPersonaFiles(file);
        await loadPromptModules();
        document.getElementById('persona-new-file').value = '';
        setStatus('persona-status', '保存成功 ✓');
      }} catch (_error) {{ setStatus('persona-status', '网络错误', true); }}
    }}

    // Memories
    let userMemoriesData = [];
    let promptModuleState = {{ planner: [], replyer: [], memory: [] }};
    let availableModuleFiles = [];

    async function loadAllMemories() {{
      try {{
        const res = await fetch('/memory');
        if (!res.ok) {{
          setStatus('self-memory-status', await readErrorDetail(res, '加载记忆数据失败'), true);
          return;
        }}
        const data = await res.json();
        // Self
        document.getElementById('self-memory').value = data.self_memory ?? '';
        
        // Users
        userMemoriesData = data.user_memories || [];
        const select = document.getElementById('user-memory-select');
        select.innerHTML = '<option value="">-- 选择用户 --</option>';
        userMemoriesData.forEach(um => {{
          const opt = document.createElement('option');
          opt.value = um.username;
          opt.textContent = um.username;
          select.appendChild(opt);
        }});
      }} catch (_error) {{
        setStatus('self-memory-status', '加载记忆数据失败', true);
      }}
    }}

    async function saveSelfMemory() {{
      const memory = document.getElementById('self-memory').value;
      try {{
        const res = await fetch('/memory/self', {{
          method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ memory }})
        }});
        if (!res.ok) {{
          setStatus('self-memory-status', await readErrorDetail(res, '保存失败 ✗'), true);
          return;
        }}
        setStatus('self-memory-status', '保存成功 ✓');
      }} catch (_error) {{ setStatus('self-memory-status', '网络错误', true); }}
    }}

    document.getElementById('user-memory-select').addEventListener('change', (e) => {{
      const username = e.target.value;
      const input = document.getElementById('user-memory-name');
      const textarea = document.getElementById('user-memory-content');
      input.value = username;
      if (username) {{
        const um = userMemoriesData.find(x => x.username === username);
        textarea.value = um ? um.memory_text : '';
      }} else {{
        textarea.value = '';
      }}
    }});

    async function saveUserMemory() {{
      const username = document.getElementById('user-memory-name').value.trim();
      const memory = document.getElementById('user-memory-content').value;
      if (!username) {{
        setStatus('user-memory-status', '请输入用户名', true);
        return;
      }}
      try {{
        const res = await fetch(`/memory/user/${{encodeURIComponent(username)}}`, {{
          method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ memory }})
        }});
        if (res.ok) {{
          setStatus('user-memory-status', '保存成功 ✓');
          await loadAllMemories(); // Refresh list
          document.getElementById('user-memory-select').value = username;
        }} else {{
          setStatus('user-memory-status', await readErrorDetail(res, '保存失败 ✗'), true);
        }}
      }} catch (_error) {{ setStatus('user-memory-status', '网络错误', true); }}
    }}

    // Prompt module orchestration
    function escapeHtml(value) {{
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    function renderPromptModuleRoute(route) {{
      const container = document.getElementById(`${{route}}-modules`);
      const modules = promptModuleState[route] || [];
      if (!modules.length) {{
        container.innerHTML = '<div class="module-item empty">当前没有模块，可从 prompts 或 personas 中添加。</div>';
      }} else {{
        container.innerHTML = modules.map((module, index) => `
          <div class="module-item">
            <label class="module-toggle">
              <input type="checkbox" ${{module.enabled ? 'checked' : ''}} onchange="togglePromptModule('${{route}}', ${{index}}, this.checked)">
              <span>${{escapeHtml(module.name)}}</span>
            </label>
            <div class="module-actions">
              <button class="secondary" type="button" onclick="movePromptModule('${{route}}', ${{index}}, -1)" ${{index === 0 ? 'disabled' : ''}}>↑</button>
              <button class="secondary" type="button" onclick="movePromptModule('${{route}}', ${{index}}, 1)" ${{index === modules.length - 1 ? 'disabled' : ''}}>↓</button>
            </div>
          </div>
        `).join('');
      }}
      refreshPromptModuleOptions(route);
    }}

    function refreshPromptModuleOptions(route) {{
      const select = document.getElementById(`${{route}}-add-module`);
      const used = new Set((promptModuleState[route] || []).map(module => module.name));
      const candidates = availableModuleFiles.filter(name => !used.has(name));
      if (!candidates.length) {{
        select.innerHTML = '<option value="">没有可添加的模块</option>';
        select.disabled = true;
        return;
      }}
      select.disabled = false;
      select.innerHTML = candidates.map(name => `<option value="${{escapeHtml(name)}}">${{escapeHtml(name)}}</option>`).join('');
    }}

    function togglePromptModule(route, index, enabled) {{
      promptModuleState[route][index].enabled = enabled;
    }}

    function movePromptModule(route, index, delta) {{
      const modules = promptModuleState[route];
      const nextIndex = index + delta;
      if (nextIndex < 0 || nextIndex >= modules.length) return;
      [modules[index], modules[nextIndex]] = [modules[nextIndex], modules[index]];
      renderPromptModuleRoute(route);
    }}

    function addPromptModule(route) {{
      const select = document.getElementById(`${{route}}-add-module`);
      const name = select.value;
      if (!name) return;
      promptModuleState[route].push({{ name, enabled: true }});
      renderPromptModuleRoute(route);
    }}

    function hydratePromptModules(data) {{
      availableModuleFiles = data.available_module_files || [];
      const promptModules = data.prompt_modules || {{ planner: [], replyer: [], memory: [] }};
      promptModuleState = {{
        planner: (promptModules.planner || []).map(item => ({{ name: item.name, enabled: Boolean(item.enabled) }})),
        replyer: (promptModules.replyer || []).map(item => ({{ name: item.name, enabled: Boolean(item.enabled) }})),
        memory: (promptModules.memory || []).map(item => ({{ name: item.name, enabled: Boolean(item.enabled) }})),
      }};
      renderPromptModuleRoute('planner');
      renderPromptModuleRoute('replyer');
      renderPromptModuleRoute('memory');
    }}

    function hydrateRuntimeStatus(runtime) {{
      if (!runtime) return;
      const badges = document.getElementById('runtime-status-badges');
      const modeSelect = document.getElementById('runtime-mode-select');
      if (modeSelect && runtime.mode) {{
        modeSelect.value = runtime.mode;
      }}
      let html = '';
      
      if (runtime.read_only) {{
        html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">🔒 只读模式</span>`;
      }} else {{
        html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(5, 150, 105, 0.1); color: var(--success); font-size: 12px; font-weight: 500;">🔓 读写模式</span>`;
      }}
      
      if (runtime.allow_send_reply) {{
        html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(5, 150, 105, 0.1); color: var(--success); font-size: 12px; font-weight: 500;">✉️ 可发送</span>`;
      }} else {{
        html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(220, 38, 38, 0.1); color: var(--error); font-size: 12px; font-weight: 500;">🚫 禁发送</span>`;
      }}
      
       if (runtime.require_approval_before_send) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(59, 130, 246, 0.1); color: var(--primary); font-size: 12px; font-weight: 500;">🛡️ 需审批</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">⚡ 免审批</span>`;
       }}

       if (runtime.mark_notifications_read) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(59, 130, 246, 0.1); color: var(--primary); font-size: 12px; font-weight: 500;">📬 标记通知已读</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">📭 保留未读通知</span>`;
       }}

        if (runtime.shadow_mode) {{
          html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(245, 158, 11, 0.15); color: #b45309; font-size: 12px; font-weight: 500;">🫥 影子模式</span>`;
        }} else {{
          html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">☀️ 非影子模式</span>`;
        }}

       if (runtime.panic_switch) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(220, 38, 38, 0.12); color: var(--error); font-size: 12px; font-weight: 500;">🛑 Panic 开关开启</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(5, 150, 105, 0.1); color: var(--success); font-size: 12px; font-weight: 500;">🟢 Panic 开关关闭</span>`;
       }}

       if (runtime.blackout_start_hour !== null && runtime.blackout_start_hour !== undefined && runtime.blackout_end_hour !== null && runtime.blackout_end_hour !== undefined && runtime.blackout_start_hour !== runtime.blackout_end_hour) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(99, 102, 241, 0.12); color: #4338ca; font-size: 12px; font-weight: 500;">🌙 黑窗 ${{String(runtime.blackout_start_hour).padStart(2, '0')}}:00-${{String(runtime.blackout_end_hour).padStart(2, '0')}}:00 UTC</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">🌤️ 无黑窗</span>`;
       }}

       if ((runtime.topic_cooldown_minutes || 0) > 0) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(14, 165, 233, 0.12); color: #0369a1; font-size: 12px; font-weight: 500;">⏱️ 主题冷却 ${{runtime.topic_cooldown_minutes}}m</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">⏱️ 无主题冷却</span>`;
       }}

       if ((runtime.muted_topic_ids || []).length > 0) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(168, 85, 247, 0.12); color: #7e22ce; font-size: 12px; font-weight: 500;">🔕 静音主题 ${{runtime.muted_topic_ids.length}}</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">🔔 无静音主题</span>`;
       }}

       if ((runtime.muted_usernames || []).length > 0) {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: rgba(236, 72, 153, 0.12); color: #be185d; font-size: 12px; font-weight: 500;">🙈 静音用户 ${{runtime.muted_usernames.length}}</span>`;
       }} else {{
         html += `<span style="padding: 4px 8px; border-radius: 4px; background: var(--border); color: var(--text); font-size: 12px; font-weight: 500;">👥 无静音用户</span>`;
       }}

        badges.innerHTML = html;
       }}

    async function loadPromptModules() {{
      try {{
        const res = await fetch('/config');
        if (!res.ok) {{
          setStatus('prompt-modules-status', await readErrorDetail(res, '加载模块编排失败'), true);
          return;
        }}
        const data = await res.json();
        hydratePromptModules(data);
        hydrateRuntimeStatus(data.runtime);
        hydrateEditableConfigOptions(data.editable_configs || []);
      }} catch (_error) {{
        setStatus('prompt-modules-status', '加载模块编排失败', true);
      }}
    }}

    function hydrateEditableConfigOptions(files) {{
      const select = document.getElementById('editable-config-file');
      if (!select || !Array.isArray(files) || !files.length) return;
      const current = select.value;
      select.innerHTML = files.map(file => `<option value="${{escapeHtml(file)}}">${{escapeHtml(file)}}</option>`).join('');
      select.value = files.includes(current) ? current : files[0];
    }}

    async function loadEditableConfig() {{
      const file = document.getElementById('editable-config-file').value;
      if (!file) return;
      try {{
        const res = await fetch(`/config/editable/${{encodeURIComponent(file)}}`);
        if (!res.ok) {{
          setStatus('editable-config-status', await readErrorDetail(res, '加载配置失败'), true);
          return;
        }}
        const data = await res.json();
        document.getElementById('editable-config-content').value = data.content || '';
        setStatus('editable-config-status', '配置已载入');
      }} catch (_error) {{
        setStatus('editable-config-status', '网络错误', true);
      }}
    }}

    async function saveEditableConfig() {{
      const file = document.getElementById('editable-config-file').value;
      const content = document.getElementById('editable-config-content').value;
      if (!file) {{
        setStatus('editable-config-status', '请选择配置文件', true);
        return;
      }}
      try {{
        const res = await fetch(`/config/editable/${{encodeURIComponent(file)}}`, {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ content }}),
        }});
        if (!res.ok) {{
          setStatus('editable-config-status', await readErrorDetail(res, '保存配置失败'), true);
          return;
        }}
        const data = await res.json();
        document.getElementById('editable-config-content').value = data.content || '';
        await loadPromptModules();
        setStatus('editable-config-status', '配置保存成功 ✓');
      }} catch (_error) {{
        setStatus('editable-config-status', '网络错误', true);
      }}
    }}

    async function saveRuntimeMode() {{
      const mode = document.getElementById('runtime-mode-select').value;
      try {{
        const res = await fetch('/config/runtime-mode', {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ mode }}),
        }});
        if (!res.ok) {{
          setStatus('runtime-mode-status', await readErrorDetail(res, '保存运行模式失败'), true);
          return;
        }}
        const data = await res.json();
        hydrateRuntimeStatus(data.runtime);
        if (document.getElementById('editable-config-file').value === 'runtime.toml') {{
          await loadEditableConfig();
        }}
        setStatus('runtime-mode-status', '运行模式已更新 ✓');
      }} catch (_error) {{
        setStatus('runtime-mode-status', '网络错误', true);
      }}
    }}

    async function savePromptModules() {{
      try {{
        const res = await fetch('/config/prompt-modules', {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            planner: {{ modules: promptModuleState.planner }},
            replyer: {{ modules: promptModuleState.replyer }},
            memory: {{ modules: promptModuleState.memory }},
          }}),
        }});
        if (!res.ok) {{
          setStatus('prompt-modules-status', await readErrorDetail(res, '保存失败 ✗'), true);
          return;
        }}
        const data = await res.json();
        hydratePromptModules(data);
        setStatus('prompt-modules-status', '保存成功 ✓');
      }} catch (_error) {{
        setStatus('prompt-modules-status', '网络错误', true);
      }}
    }}

    // Logs
    async function loadLogs() {{
      try {{
        const res = await fetch('/logs/latest?lines=200');
        const data = await res.json();
        const viewer = document.getElementById('log-viewer');
        if (data.file) {{
          document.getElementById('log-filename').textContent = `当前文件: ${{data.file}}`;
          viewer.textContent = data.lines.join('\\n');
          viewer.scrollTop = viewer.scrollHeight;
        }} else {{
          document.getElementById('log-filename').textContent = '暂无日志文件';
          viewer.textContent = '';
        }}
      }} catch (e) {{
        document.getElementById('log-viewer').textContent = '获取日志失败...';
      }}
    }}

    // Pipeline Runs
    async function loadPipelineRuns() {{
      const container = document.getElementById('pipeline-runs-container');
      try {{
        const res = await fetch('/topics/runs');
        if (!res.ok) {{
          container.innerHTML = `<div class="module-item empty" style="color: var(--error);">加载失败: ${{escapeHtml(await readErrorDetail(res, '未知错误'))}}</div>`;
          return;
        }}
        const data = await res.json();
        const runs = data.items || [];
        if (!runs.length) {{
          container.innerHTML = '<div class="module-item empty">暂无流水线记录</div>';
          return;
        }}
        
        container.innerHTML = runs.map(run => {{
          const actionMap = {{
            'reply_sent': '✅ 已回复',
            'reply_pending_approval': '⏳ 待审核',
            'skip': '⏭️ 已跳过',
            'reply_error': '❌ 回复失败',
            'banned': '🔨 已封禁',
            'reply': '✉️ 计划回复'
          }};
          const actionText = actionMap[run.action] || run.action;
          let actionColor = 'var(--text)';
          if (run.action === 'skip') actionColor = 'var(--muted)';
          if (run.action === 'reply' || run.action === 'reply_sent') actionColor = 'var(--success)';
          if (run.action === 'reply_pending_approval') actionColor = 'var(--primary)';
          if (run.action === 'reply_error' || run.action === 'banned') actionColor = 'var(--error)';
          
          const decision = run.decision || {{}};
          const reason = decision.reason || '无明确原因';
          const shouldReply = decision.should_reply === true
            ? '是'
            : decision.should_reply === false
              ? '否'
              : '未知';
          
          let draftHtml = '';
          if (run.draft_content && run.draft_content.trim() !== '') {{
            draftHtml = `<div style="margin-top: 8px; padding: 8px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; font-size: 13px; white-space: pre-wrap; word-break: break-all; color: var(--text);"><b>📝 草稿:</b>\\n${{escapeHtml(run.draft_content)}}</div>`;
          }}

          return `
            <div class="module-item" style="flex-direction: column; align-items: stretch; padding: 12px;">
              <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <strong style="font-size: 14px;">[${{escapeHtml(run.topic_id)}}] ${{escapeHtml(run.topic_title)}}</strong>
                <div style="display: flex; gap: 8px; align-items: center;">
                  <span style="font-size: 13px; font-weight: bold; color: ${{actionColor}};">${{escapeHtml(actionText)}}</span>
                  <a href="/topics/runs/${{escapeHtml(run.id)}}" target="_blank" style="font-size: 12px; color: var(--primary); text-decoration: none;">查看详情</a>
                </div>
              </div>
              <div style="font-size: 13px; color: var(--text); line-height: 1.6;">
                <div><b>触发原因:</b> ${{escapeHtml(run.trigger_reason)}} | <b>建议回复:</b> ${{escapeHtml(shouldReply)}}</div>
                <div style="color: var(--muted);"><b>决策依据:</b> ${{escapeHtml(reason)}}</div>
                <div style="color: var(--muted); font-size: 12px;"><b>时间:</b> ${{new Date(run.created_at).toLocaleString()}}</div>
              </div>
              ${{draftHtml}}
            </div>
          `;
        }}).join('');
      }} catch (e) {{
        container.innerHTML = '<div class="module-item empty" style="color: var(--error);">获取流水线记录失败...</div>';
      }}
    }}

    // Pending Approvals
    async function loadPendingApprovals() {{
      const container = document.getElementById('pending-approvals-container');
      try {{
        const res = await fetch('/topics/pending-replies');
        if (!res.ok) {{
          container.innerHTML = `<div class="module-item empty" style="color: var(--error);">加载失败: ${{escapeHtml(await readErrorDetail(res, '未知错误'))}}</div>`;
          return;
        }}
        const data = await res.json();
        const items = data.items || [];
        if (!items.length) {{
          container.innerHTML = '<div class="module-item empty">暂无待审核回复</div>';
          return;
        }}
        
        container.innerHTML = items.map(item => {{
          const decision = item.decision || {{}};
          const reason = decision.reason || '无明确原因';
          const trigger = item.trigger_reason || '未知';
          const targetPost = item.target_post_number ? ` #p${{item.target_post_number}}` : '';
          
          let draftHtml = '';
          if (item.draft_content && item.draft_content.trim() !== '') {{
            draftHtml = `<div style="margin-top: 8px; padding: 8px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; font-size: 13px; white-space: pre-wrap; word-break: break-all; color: var(--text);"><b>📝 草稿:</b>\\n${{escapeHtml(item.draft_content)}}</div>`;
          }}

          let extraInfo = '';
          if (item.error_text) {{
             extraInfo += ` <span style="color: var(--error);">[错误: ${{escapeHtml(item.error_text)}}]</span>`;
          }}
          if (item.reply_post_id) {{
             extraInfo += ` <span style="color: var(--success);">[已发送 ID: ${{escapeHtml(item.reply_post_id)}}]</span>`;
          }}
          
          let actionBtn = '';
          if (item.status === 'pending') {{
            actionBtn = `<button type="button" onclick="approvePendingReply('${{item.id}}', this)" style="padding: 6px 12px; font-size: 12px;">✅ 批准发送</button>`;
          }}

          return `
            <div class="module-item" style="flex-direction: column; align-items: stretch; padding: 12px;">
              <div style="display: flex; justify-content: space-between; margin-bottom: 4px; align-items: center;">
                <strong style="font-size: 14px;">[${{escapeHtml(item.topic_id)}}] ${{escapeHtml(item.topic_title)}}${{targetPost}}</strong>
                <div style="display: flex; gap: 8px; align-items: center;">
                  <span style="font-size: 13px; font-weight: bold; color: ${{item.status === 'pending' ? 'var(--primary)' : 'var(--text)'}};">状态: ${{escapeHtml(item.status)}}${{extraInfo}}</span>
                  ${{actionBtn}}
                </div>
              </div>
              <div style="font-size: 13px; color: var(--text); line-height: 1.6;">
                <div><b>触发原因:</b> ${{escapeHtml(trigger)}}</div>
                <div style="color: var(--muted);"><b>决策依据:</b> ${{escapeHtml(reason)}}</div>
                <div style="color: var(--muted); font-size: 12px;"><b>时间:</b> ${{new Date(item.created_at).toLocaleString()}}</div>
              </div>
              ${{draftHtml}}
            </div>
          `;
        }}).join('');
      }} catch (e) {{
        container.innerHTML = '<div class="module-item empty" style="color: var(--error);">获取待审核数据失败...</div>';
      }}
    }}

    async function approvePendingReply(id, btnEl) {{
      if (!confirm('确定要批准并发送该回复吗？')) return;
      
      const originalText = btnEl.textContent;
      btnEl.disabled = true;
      btnEl.textContent = '发送中...';
      
      try {{
        const res = await fetch(`/topics/pending-replies/${{id}}/approve`, {{
          method: 'POST'
        }});
        
        if (!res.ok) {{
          const err = await readErrorDetail(res, '审批失败');
          alert('审批失败: ' + err);
          btnEl.disabled = false;
          btnEl.textContent = originalText;
          return;
        }}
        
        alert('批准成功');
        await loadPendingApprovals();
        await loadPipelineRuns();
      }} catch (e) {{
        alert('网络错误: ' + e.message);
        btnEl.disabled = false;
        btnEl.textContent = originalText;
      }}
    }}

    // Init
    document.getElementById('prompt-file').addEventListener('change', loadPrompt);
    document.getElementById('persona-file').addEventListener('change', loadPersona);
    document.getElementById('editable-config-file').addEventListener('change', loadEditableConfig);
    
    // Initial loads
    refreshPromptFiles().then(() => {{ if (document.getElementById('prompt-file').value) loadPrompt(); }});
    refreshPersonaFiles().then(() => {{ if (document.getElementById('persona-file').value) loadPersona(); }});
    loadAllMemories();
    loadPromptModules();
    loadEditableConfig();
    loadLogs();
    loadPipelineRuns();
    loadPendingApprovals();
    
    // Auto-refresh logs and pipeline runs every 10s
    setInterval(() => {{
      loadLogs();
      loadPipelineRuns();
      loadPendingApprovals();
    }}, 10000);
  </script>
</body>
</html>
"""

    @app.get("/health", summary="健康检查")
    def health() -> dict[str, str]:
        return {"status": "正常"}

    app.include_router(config_router)
    app.include_router(prompts_router)
    app.include_router(personas_router)
    app.include_router(memory_router)
    app.include_router(topics_router)
    app.include_router(logs_router)
    return app
