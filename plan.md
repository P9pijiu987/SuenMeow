## Plan: Python 论坛人格 Bot

推荐方案：做一个“事件驱动 + 分层状态机 + 两阶段 LLM（planner/replyer）+ 可组合人格 + 双记忆系统 + 完整后台”的 Python 项目，而不是复刻 WindWhisper 的“AI通过工具自助读帖/回帖”架构。原因是你强调可读性、低 token、每帖都顾及、配置拆分、可控触发和完整后台，这些都更适合确定性流水线，而不是开放式工具调用代理。WindWhisper 可复用的主要是 Discourse 登录/通知/发帖接口，以及 5 秒通知轮询思路；核心架构建议完全重做。

**Steps**
1. Phase 1: 定义系统边界与数据流。明确五个核心子系统：forum adapter、trigger engine、planner、replyer、memory engine、admin webui。所有触发先写入事件队列，再由 planner 决策，最后由 replyer 输出，避免轮询逻辑直接调模型。
2. Phase 1: 设计统一配置模型。拆分为 credentials、forum、providers、models、prompts、personas、thresholds、scheduler、memory、moderation、webui、logging。要求支持文件热更新或后台修改后落盘。*这是后续所有模块的前置依赖。*
3. Phase 1: 设计论坛状态缓存与幂等层。建立 topics、posts、topic_state、trigger_events、reply_history、ban_rules、scan_cursors。目标是保证“不重复回复”“短轮询不重复触发”“多种 trigger 不互相打架”。推荐 SQLite 起步，后续可迁移 PostgreSQL。*依赖步骤 1-2。*
4. Phase 2: 实现 forum adapter。封装 Discourse 登录、csrf 刷新、notifications 拉取、topic 元数据拉取、posts 拉取、reply 发帖、mark-read。优先复用 forum.rdfzer.com API 备忘中的接口路径和 crawler 中的限流重试策略。*依赖步骤 2。*
5. Phase 2: 实现触发引擎，拆成三个独立 worker。worker A: 5-15 秒轮询 notifications，处理 @你、回复你。worker B: 每 1 分钟扫描最近活跃话题元数据，检测“5 分钟内新增 >= 5 回复且 5 分钟内未因 @/回复触发”。worker C: 每小时扫描最新/活跃话题列表，检测“新增回复 > 2 且未回复过”“新增回复 > 10”。三者都只生成 TriggerEvent，不直接调模型。*A/B/C 可并行开发，依赖步骤 3-4。*
6. Phase 2: 实现 ban 规则。分两层：topic denylist 和 command detector。已在 denylist 中的话题，在拿到 topic_id 后立即跳过，不再拉正文。对于“/ban @bot”命令，需要最小限度读取新增帖子正文一次才能识别，识别后永久加入 denylist，之后该帖完全不处理。需要在设计中明确：绝对零读取不现实，但可做到“识别后零读取”。
7. Phase 3: 实现上下文裁剪器。不要把整帖无脑塞给模型。先取全帖元数据与最近窗口，再基于触发原因确定读取范围：@/回复你时优先读取目标楼层附近上下文；小时级触发时读取楼主、最近 N 层、关键被回复链、你的历史发言、少量摘要。planner 与 replyer 各自有独立阈值。必要时增加 thread summarizer，将长帖压缩成结构化摘要缓存。*依赖步骤 3-5。*
8. Phase 3: 实现 planner。输入包括触发原因、结构化帖子上下文、相关用户记忆、你的自我记忆、人格开关、风控状态。输出严格结构化 JSON：should_reply、priority、target_username、target_post_number、reason、style_notes、memory_actions、cooldown_hint。planner 默认偏向回复，但允许拒绝。*依赖步骤 7。*
9. Phase 3: 实现 replyer。只在 planner.should_reply 为 true 时运行，输入为回复目标、裁剪后的帖子上下文、组合式 system prompt、人设模块、记忆片段。输出必须满足长度、语气、对象、禁词、Markdown 安全限制。replyer 不再重新做“大决策”，只负责生成。*依赖步骤 8。*
10. Phase 3: 设计 prompt 组合系统。将 prompt 拆成 core persona、style rules、safety rules、planner prompt、replyer prompt、memory update prompt、tools/skills prompt。主人设、附加人设 1、猫娘人设都做成可启用模块，按顺序拼接，并记录启用状态与优先级。*可与步骤 8-9 并行。*
11. Phase 4: 实现他人记忆。SQLite 表建议含 users、user_memories、memory_evidence、memory_revisions。平时只检索触发帖中高相关用户的短记忆片段注入 planner/replyer。夜间 24:00 执行 memory consolidate job：选取当日新增回复量最大的 N 个帖子，保证处理总消息量在 50-200 之间，结合旧记忆做“辩证更新”，输出新记忆与置信度。无关用户不更新。*依赖步骤 3、7。*
12. Phase 4: 实现自我记忆。分为 short_term_self_memory 与 long_term_self_memory。planner 可产生 save_memory 动作，但只有在该次流程最终回复或满足明确用户指令时才真正落库。24:00 做遗忘/合并，删去低价值或过时内容。*依赖步骤 8。*
13. Phase 4: 实现 token 与成本控制。为每个模块配置模型：trigger 不用模型；planner 用便宜、快、结构化能力强的模型；replyer 可用稍强模型；夜间记忆整理可单独模型。增加 topic budget、daily budget、per-trigger budget、max context chars、max quoted posts、max retries。*可与步骤 8-12 并行。*
14. Phase 5: 实现 WebUI 后台。分为 Console 和 Terminal 两部分。Console 包括：凭据与论坛配置、模型供应商配置、模型路由、prompt 编辑、人格模块开关、阈值编辑、topic ban 列表、记忆检索与编辑、手动重跑某 topic、dry-run 开关。Terminal 包括：系统日志、触发日志、planner 输入输出、replyer 输入输出、AIGC 全记录、失败重试记录。推荐 FastAPI + SQLModel + Jinja2/HTMX 或 React 前端；如果要控制复杂度，第一版用 FastAPI + 服务端模板更稳。
15. Phase 5: 实现观测与人工兜底。加入 dry-run、approve-before-send、topic mute、user mute、cooldown、blackout hours、panic switch。重要的是让你能在后台直接看到“为什么它回了/没回”。
16. Phase 6: 实现 Docker 化与多环境。以 Docker 长驻 Mac 为生产，Windows 本机为开发测试。拆分 .env 与 mounted config directory，确保登录信息单独文件存放，prompts/providers/personas 单独目录管理。加入健康检查、日志卷、数据卷、时区配置。*依赖步骤 2-15。*
17. Phase 6: 测试与上线策略。先做 read-only 模式，仅扫描和 planner，不发帖；再做 shadow mode，生成 reply 但不发送；最后小流量发送。夜间作业、ban、幂等、断线重登、429 限流都要单测和集成测试覆盖。*依赖前述全部核心模块。*

**Relevant files**
- e:/program/SuenMeow/forum_rdfzer_api_from_windwhisper.md — 复用登录、csrf、notifications、posts、reply 接口定义
- e:/program/crawler/scrape_bdfz_traditional_posts.py — 复用 rate limit/backoff、论坛拉取与文本清洗经验
- e:/program/SuenMeow/prompt.md — 可作为未来 prompt 目录拆分时的迁移入口
- e:/program/SuenMeow/config/credentials.toml — 登录信息单独文件
- e:/program/SuenMeow/config/providers.toml — AI 供应商、地址、密钥、超时
- e:/program/SuenMeow/config/models.toml — planner/replyer/memory job 各模块模型路由
- e:/program/SuenMeow/prompts/planner.md — planner system prompt
- e:/program/SuenMeow/prompts/replyer.md — replyer system prompt
- e:/program/SuenMeow/prompts/memory_user_update.md — 他人记忆更新 prompt
- e:/program/SuenMeow/prompts/memory_self_update.md — 自我记忆更新 prompt
- e:/program/SuenMeow/personas/core.md — 主人设
- e:/program/SuenMeow/personas/extra_1.md — 附加人设 1
- e:/program/SuenMeow/personas/catgirl.md — 猫娘人设
- e:/program/SuenMeow/bot/forum_client.py — Discourse API 封装
- e:/program/SuenMeow/bot/trigger_engine.py — 三类 trigger 的调度与入队
- e:/program/SuenMeow/bot/planner.py — 结构化决策
- e:/program/SuenMeow/bot/replyer.py — 回复生成
- e:/program/SuenMeow/bot/context_builder.py — 上下文裁剪与摘要
- e:/program/SuenMeow/bot/memory_service.py — 记忆读写与夜间整理
- e:/program/SuenMeow/bot/ban_service.py — /ban 识别与 denylist
- e:/program/SuenMeow/db/schema.py — SQLite 模型与迁移定义
- e:/program/SuenMeow/web/main.py — WebUI 入口
- e:/program/SuenMeow/web/templates/... — 后台模板
- e:/program/SuenMeow/docker-compose.yml — 长驻部署


**Phase 1 scaffold**
- e:/program/SuenMeow/main.py — 本地开发入口，启动调度器、WebUI 或单模块调试
- e:/program/SuenMeow/pyproject.toml — Python 项目依赖与工具配置
- e:/program/SuenMeow/.env.example — 非敏感环境变量示例，真实密钥不入库
- e:/program/SuenMeow/config/credentials.toml — 论坛账号、密码、Cookie 相关敏感配置
- e:/program/SuenMeow/config/forum.toml — forum 地址、请求头、retry、reaction 名称映射
- e:/program/SuenMeow/config/providers.toml — 各 AI 供应商 base URL、key、超时、限流
- e:/program/SuenMeow/config/models.toml — planner/replyer/memory/webui 各模块模型路由
- e:/program/SuenMeow/config/thresholds.toml — 新回复阈值、上下文阈值、budget、cooldown
- e:/program/SuenMeow/config/scheduler.toml — 5 秒通知轮询、1 分钟活跃扫描、1 小时全量扫描、24 点记忆任务
- e:/program/SuenMeow/config/webui.toml — WebUI 监听地址、端口、认证与日志展示配置
- e:/program/SuenMeow/prompts/planner.md — planner system prompt
- e:/program/SuenMeow/prompts/replyer.md — replyer system prompt
- e:/program/SuenMeow/prompts/memory_user_update.md — 他人记忆更新 prompt
- e:/program/SuenMeow/prompts/memory_self_update.md — 自我记忆整理与遗忘 prompt
- e:/program/SuenMeow/prompts/style_rules.md — 统一风格规则
- e:/program/SuenMeow/prompts/safety_rules.md — 不回复、降级、敏感内容等规则
- e:/program/SuenMeow/personas/core.md — 主人设
- e:/program/SuenMeow/personas/extra_1.md — 附加人设 1
- e:/program/SuenMeow/personas/catgirl.md — 猫娘人设
- e:/program/SuenMeow/bot/forum_client.py — Discourse 登录、csrf、通知、帖子、发帖、mark-read
- e:/program/SuenMeow/bot/trigger_engine.py — 触发总控，协调 3 类 worker 与事件队列
- e:/program/SuenMeow/bot/notification_worker.py — @你、回复你 的短轮询触发
- e:/program/SuenMeow/bot/activity_worker.py — 1 分钟爆帖扫描触发
- e:/program/SuenMeow/bot/hourly_scan_worker.py — 1 小时常规扫描触发
- e:/program/SuenMeow/bot/context_builder.py — 上下文裁剪、摘要缓存、引用链提取
- e:/program/SuenMeow/bot/planner.py — 结构化决策输出
- e:/program/SuenMeow/bot/replyer.py — 最终回复生成
- e:/program/SuenMeow/bot/persona_loader.py — 人设模块组合与启停
- e:/program/SuenMeow/bot/prompt_loader.py — prompt 组合与热加载
- e:/program/SuenMeow/bot/memory_service.py — 他人记忆、自我记忆、夜间 consolidation
- e:/program/SuenMeow/bot/ban_service.py — /ban 命令识别与 denylist
- e:/program/SuenMeow/bot/budget_service.py — token/成本预算控制
- e:/program/SuenMeow/bot/pipeline.py — TriggerEvent -> planner -> replyer 主链路编排
- e:/program/SuenMeow/db/schema.py — SQLite 表结构
- e:/program/SuenMeow/db/repositories.py — topic_state、reply_history、trigger_events 等仓储层
- e:/program/SuenMeow/db/migrations/ — 后续 schema 迁移
- e:/program/SuenMeow/web/main.py — FastAPI 入口
- e:/program/SuenMeow/web/routes/config.py — 配置编辑页
- e:/program/SuenMeow/web/routes/prompts.py — prompt 编辑页
- e:/program/SuenMeow/web/routes/personas.py — 人设开关页
- e:/program/SuenMeow/web/routes/memory.py — 记忆查看/编辑页
- e:/program/SuenMeow/web/routes/topics.py — ban topic、手动重跑、topic 状态页
- e:/program/SuenMeow/web/routes/logs.py — 日志和 AIGC 终端页
- e:/program/SuenMeow/web/templates/ — 服务端模板
- e:/program/SuenMeow/data/ — SQLite、缓存摘要、运行时产物
- e:/program/SuenMeow/logs/ — 文件日志
- e:/program/SuenMeow/tests/test_forum_client.py — 接口与登录逻辑测试
- e:/program/SuenMeow/tests/test_trigger_engine.py — 触发幂等与事件生成测试
- e:/program/SuenMeow/tests/test_context_builder.py — 长帖裁剪与摘要缓存测试
- e:/program/SuenMeow/tests/test_pipeline.py — planner/replyer 主链路测试
- e:/program/SuenMeow/docker-compose.yml — Mac 长驻部署
- e:/program/SuenMeow/Dockerfile — 生产镜像定义
- e:/program/SuenMeow/README.md — 启动、配置、部署说明

**Verification**
1. 用测试账号验证登录、csrf 刷新、notifications 拉取、reply 发帖、429 重试。
2. 在 read-only 模式下运行至少 24 小时，确认三类 trigger 都会触发且不会重复入队。
3. 构造含 @你、回复你、快速爆帖、普通活跃帖、ban 贴的测试数据，验证 planner 输入与输出是否符合预期。
4. 对超长帖子验证 context builder 是否遵守 planner/replyer 的不同阈值，并显著降低 token 消耗。
5. 验证 denylist：一旦识别 /ban 命令，后续该 topic 不再读取正文、不再触发、不再出现在夜间记忆任务。
6. 验证他人记忆与自我记忆的新增、检索、夜间 consolidation、遗忘是否符合规则。
7. 验证 WebUI 可编辑 prompts、providers、thresholds、memory、ban list，并能实时看到日志与 AIGC 记录。
8. 在 Docker for Mac 环境完成持久化重启测试，确保 SQLite/日志/config 挂载后不丢状态。

**Decisions**
- 已确认：全盘采用该方案，后续以此为冻结版执行蓝图。
- 包含：Python 实现、完全不同于 WindWhisper 的核心交互架构、完整后台、低 token 优化、双记忆系统、Docker 部署。
- 不包含：论坛插件开发、真正的 server push；即时触发采用短轮询 notifications，参考 WindWhisper 的 5 秒循环。
- 决策：生产环境为 Mac 上的 Docker 长驻部署；开发测试环境为 Windows 本机直跑。
- 决策：WebUI 第一版就做完整后台，但前端技术栈优先选择简单稳妥方案，不先追求复杂 SPA。
- 决策：数据库先用 SQLite，因为它足够支撑单实例长期运行，且便于你理解和维护。
- 决策：默认启用回复前人工审核开关，但仅在开发期和 shadow mode 打开；正式小流量上线后默认自动发送，保留一键切回审核模式。
- 决策：默认实现用户级黑名单与静音名单，不做用户级白名单优先策略，避免额外复杂度。
- 决策：默认实现话题摘要缓存，并将其视为长帖低 token 控制的核心能力，而非可选优化。
- 决策：ban 的“完全不读取”解释为“识别 ban 后永久不读取”；在 ban 首次出现前，系统至少要对新增帖子做最小读取才能发现该命令。

## 当前进度补充（2026-03-14）

### 本轮已完成
- 中文 WebUI 已经可直接作为主入口使用，首页 `/` 是真实管理面板，不再只是 JSON。
- 可在 WebUI 中编辑 prompts、personas、自我记忆、用户记忆，并查看最新日志。
- 新增“提示词模块编排”功能，支持分别配置 `planner`、`replyer`、`memory` 在 LLM 调用前的 module 链。
- 新增持久化配置文件：`config/prompt_modules.toml`。
- `bot/settings.py` 已支持加载 prompt module 配置，并在缺省时回退到旧的硬编码默认值，确保未修改配置时行为不变。
- `bot/trigger_engine.py` 已将 prompt module 配置注入 `Pipeline`。
- `bot/pipeline.py` 已改为按配置拼接 `planner` / `replyer` / `memory` modules，不再写死列表。
- module 来源已扩展为 `prompts/*.md` + `personas/*.md`，可把 `core.md`、`catgirl.md` 等 persona 文件直接编排进链路。
- `bot/prompt_loader.py` / `bot/persona_loader.py` 已补充单文件 `exists/load/available` 能力，persona 名既支持 `core` 也支持 `core.md`。
- `web/routes/config.py` 已暴露：
  - `GET /config`：返回 `available_prompt_files`、`available_persona_files`、`available_module_files` 和 `prompt_modules`
  - `PUT /config/prompt-modules`：保存 planner/replyer/memory 的模块顺序与启用状态
- `web/main.py` 已加入中文“提示词模块编排”面板，支持启用/停用、上下移动、添加模块、保存。
- prompt 编辑 / persona 编辑都已支持直接“创建或更新” Markdown 文件，不必先手工建文件。
- `web/routes/prompts.py` 与 `web/routes/personas.py` 的 PUT 语义已改为 create-or-update。

### 当前默认 prompt module 行为
- planner 默认链：`planner.md -> safety_rules.md`
- replyer 默认链：`replyer.md -> style_rules.md -> safety_rules.md`
- memory 默认链：`memory_user_update.md -> memory_self_update.md`
- 若 `config/prompt_modules.toml` 不存在或缺字段，系统会自动回退到上述默认值。

### 本轮已验证
- 目标测试已通过：
  - `tests/test_settings.py`
  - `tests/test_pipeline_debug.py`
  - `tests/test_pipeline_process_event.py`
  - `tests/test_web_admin_routes.py`
- 目标测试总结果：`18 passed in 5.42s`
- 改动文件的 error 级别 LSP diagnostics 已清空。
- 端口 `5000` 的 WebUI 已重新启动并验证：
  - `/health` 返回 200
  - `/config` 返回 200，且已包含 `available_prompt_files`、`available_persona_files`、`available_module_files` 与 `prompt_modules.memory`
  - `/` 返回 200，且已显示 `Memory 模块链`、`保存 / 创建提示词`、`保存 / 创建人格`

### 重要限制 / 已知事项
- 现在 WebUI 保存 `prompt_modules.toml` 后，Web 进程内的 `app.state.settings` 会刷新；但若独立 worker 进程已启动，它不会自动热更新，仍需重启 worker 才会吃到新配置。
- `PromptLoader.compose()` 目前仍会静默跳过不存在的 prompt 文件；这保证运行不中断，但也意味着配置错误不一定会立即显性报错。
- 当前 UI 允许把某一路由的模块链保存为空；技术上可行，但可能让 planner/replyer 提示词强度下降，后续最好补更强的校验或保护。
- prompt/persona 同名文件如果同时存在于两个目录，当前 runtime 解析优先级是 `prompts/` 先于 `personas/`。
- 当前 `memory` 仅实现“可编排的 system prompt / debug 展示 / 配置落盘”，并未额外发明新的 memory LLM 执行流程；现有架构里真实 memory 执行链仍未落地。

### 下一任建议优先级
1. 给 worker 进程补“配置热重载”或明确的重启提示机制，避免 WebUI 改完后 bot 侧仍吃旧配置。
2. 为 prompt module 配置增加更严格的后端校验：
   - 禁止重复模块
   - 对关键模块给出保护策略（至少提示风险）
   - 对空链保存给出更明确提示
3. 如果继续推进“像真实后台”的方向，可继续增强创建文件后的前端交互，例如自动填充模板、分类筛选 prompt/persona candidates、冲突提示。
4. 继续补 memory / debug / topic rerun 方向的后台能力，但“回复审核”目前不是最高优先级。

### 本轮核心变更文件
- `E:\program\SuenMeow\config\prompt_modules.toml`
- `E:\program\SuenMeow\bot\prompt_loader.py`
- `E:\program\SuenMeow\bot\persona_loader.py`
- `E:\program\SuenMeow\bot\settings.py`
- `E:\program\SuenMeow\bot\pipeline.py`
- `E:\program\SuenMeow\bot\trigger_engine.py`
- `E:\program\SuenMeow\web\routes\config.py`
- `E:\program\SuenMeow\web\routes\prompts.py`
- `E:\program\SuenMeow\web\routes\personas.py`
- `E:\program\SuenMeow\web\main.py`
- `E:\program\SuenMeow\tests\test_settings.py`
- `E:\program\SuenMeow\tests\test_pipeline_debug.py`
- `E:\program\SuenMeow\tests\test_pipeline_process_event.py`
- `E:\program\SuenMeow\tests\test_web_admin_routes.py`

### 交接提醒
- 当前正在运行的新 WebUI PTY：`pty_253451fd`
- 如果下一任要继续做 live 验证，直接先检查 `http://127.0.0.1:5000/config` 是否仍返回 `available_module_files`、`available_persona_files`、`prompt_modules.memory`，以及首页是否出现 `Memory 模块链`。
