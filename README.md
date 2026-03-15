# SuenMeow

SuenMeow 是一个面向 Discourse 论坛的事件驱动型人格 Bot 项目。它把“抓取论坛事件 → 让模型判断是否回复 → 生成回复草稿 → 按运行时安全策略决定发送、审批或仅演练 → 记录状态与流水”串成了一条可观测、可调试、可回滚的完整链路。

这个项目当前已经具备：

- 基于论坛通知、活跃度突增、定时扫描等来源触发处理
- 基于提示词模块 + 人格模块组合生成回复
- SQLite 持久化状态、事件、流水、待审批回复、记忆数据
- FastAPI 管理端，支持查看运行状态、日志、流水、调试单话题、管理提示词/人格/记忆，并直接切换运行模式、编辑非敏感 TOML 配置
- 多层运行时安全开关：只读、影子模式、审批前发送、panic 开关、黑窗、主题冷却、静音主题、静音用户
- Docker Compose 的 Web + Worker 双服务部署方式

如果你只是想先跑起来，直接看下面的 **「小白开箱即用教程」** 即可。

> 当前**推荐默认部署方式**：`Docker Compose`。
>
> 本地 Python 直跑更适合开发和排障；如果你是第一次真正部署，优先走本文的 Docker 教程。

---

## 1. 它能做什么

### 1.1 自动监听论坛事件

Worker 会持续轮询并处理多种事件来源：

- 通知触发
- 突发活跃话题触发
- 定时扫描热点 / 新话题触发
- 夜间记忆整理

这些事件会被写入数据库，避免重复处理，并能在管理端里看到最近触发记录与处理流水。

### 1.2 用提示词模块 + 人格模块控制回复风格

项目把模型输入拆成两层：

- **提示词模块**：如规划、回复风格、安全规则、记忆更新规则
- **人格模块**：如 `core.md`、`catgirl.md` 等人格设定

你可以在不改 Python 代码的前提下，通过配置文件或管理端接口调整启用的模块组合。

### 1.3 支持“直接发 / 待审批 / 只演练”三种工作模式

当前回复链路支持以下运行方式：

- **只读模式**：只观察，不实际发送
- **影子模式**：会完整生成草稿并记录流水，但不发送、也不进入审批队列
- **审批模式**：生成草稿后进入待审批列表，由人工批准后再发出
- **直接发送模式**：满足条件时自动发送

### 1.4 支持记忆系统

项目内置两类记忆：

- **自我记忆**：Bot 对自己行为/风格的长期约束
- **用户记忆**：针对特定用户名的偏好、历史信息

这些记忆会参与后续推理，也可以通过管理端直接查看与修改。

### 1.5 支持管理与调试

FastAPI 管理端已经提供了可用的管理/调试接口，包括：

- `/health`：健康检查
- `/config`：查看当前配置摘要、模型路由、运行时开关、提示词模块编排
- `/prompts`：查看/创建/更新提示词文件
- `/personas`：查看/创建/更新人格文件
- `/memory`：查看/更新自我记忆与用户记忆
- `/logs`：查看日志文件与最新日志
- `/topics/runs`：查看最近流水
- `/topics/events`：查看触发事件
- `/topics/states`：查看话题状态
- `/topics/pending-replies`：查看待审批回复
- `/topics/pending-replies/{id}/approve`：人工批准发送
- `/topics/{topic_id}/debug`：单话题调试

---

## 2. 项目结构说明

```text
SuenMeow/
├─ bot/                 # 核心业务：论坛客户端、调度、Pipeline、记忆、审批、预算等
├─ db/                  # SQLite 仓储层与 schema
├─ web/                 # FastAPI 管理端与页面/接口
├─ config/              # 所有 TOML 配置
├─ prompts/             # 提示词模块
├─ personas/            # 人格模块
├─ data/                # 运行时持久化数据（默认包含 sqlite）
├─ logs/                # 运行日志
├─ main.py              # CLI 统一入口
├─ Dockerfile
├─ docker-compose.yml
├─ docker-compose.prod.yml
└─ DEPLOY.md            # 部署/重启/回滚说明
```

几个最关键的目录：

- `config/`：你最常改的地方
- `prompts/`：控制模型“怎么想”
- `personas/`：控制模型“像谁说话”
- `data/`：数据库和运行状态
- `logs/`：出问题先看这里

---

## 3. 核心运行流程

SuenMeow 的典型流程如下：

1. Worker 轮询论坛通知和各类扫描器
2. 生成触发事件并写入数据库
3. Pipeline 取出事件，拉取话题内容
4. Planner 判断：要不要回复、回复给谁、原因是什么
5. Replyer 生成草稿
6. 根据运行时安全开关决定：
   - 跳过
   - 仅影子演练
   - 进入审批
   - 直接发送
7. 记录流水、话题状态、回复历史、记忆更新结果

这也是为什么项目需要同时运行：

- **Web 服务**：提供管理端与健康检查
- **Worker 服务**：真正干活，持续轮询并处理事件

### 3.1 现在到底什么时候才会触发 Planner？

很多人会误以为“只要扫到话题就会调用 Planner”，但当前实现不是这样。

真实链路是：

1. `NotificationWorker` / `ActivityWorker` / `HourlyScanWorker` 先生成触发事件
2. 事件写入数据库时会先做去重
3. `TriggerEngine.run_once()` 先检查全局运行时安全门：
   - `panic_switch`
   - 黑窗时间
4. `TriggerEngine._process_pending_events()` 再做预算检查
5. `Pipeline.process_event()` 再做每个话题级别的前置检查：
   - panic / 黑窗
   - 静音主题
   - 主题冷却
   - ban command
6. **只有这些都通过后**，才会进入 `dry_run()`
7. `dry_run()` 里面才真正调用 `planner.decide(...)`

也就是说：

- **不是看到话题就触发 Planner**
- **而是事件创建、去重、全局门控、预算门控、Pipeline 前置安全检查全部通过后，才触发 Planner**

Planner 跑完后，系统才会继续决定：

- 跳过
- 影子模式只演练
- 进入审批
- 直接发送

---

## 4. 配置文件说明

项目采用 **分文件 TOML 配置**。

### 4.1 `config/credentials.toml`

论坛登录凭证。

用途：

- 让 Bot 能登录论坛账号
- Worker 和调试接口都依赖它

> 建议：不要把真实账号密码提交到公开仓库。
>
> 公开仓库里建议只保留 `config/credentials.example.toml`，然后在本地复制为 `config/credentials.toml` 再填写真实值。

### 4.2 `config/forum.toml`

论坛连接配置：

- `base_url`：论坛地址
- `retry`：请求重试次数
- `user_agent`：请求头 UA
- `default_headers`：默认请求头
- `reactions`：论坛交互映射

### 4.3 `config/providers.toml`

模型提供方配置。

包括：

- `base_url`
- `api_key`
- `timeout_seconds`

如果你换模型服务商，通常先改这个文件。

> 公开仓库里建议只保留 `config/providers.example.toml`，然后在本地复制为 `config/providers.toml` 再填写真实值。

### 4.4 `config/models.toml`

模型路由配置。当前至少包含：

- `planner`
- `replyer`
- `memory`
- `webui`

也就是说，不同环节可以走不同模型。

### 4.5 `config/thresholds.toml`

阈值配置，主要分三块：

- `triggers`：触发条件
- `context`：上下文长度控制
- `budget`：预算控制

### 4.6 `config/scheduler.toml`

轮询调度相关：

- 通知扫描频率
- burst 扫描频率
- hourly 扫描频率
- 夜间记忆整理时间

### 4.7 `config/webui.toml`

管理端配置：

- `host`
- `port`
- `enable_auth`
- `show_aigc_logs`

当前默认：

- `host = "0.0.0.0"`
- `port = 8000`

这意味着本地和 Docker 场景都更容易直接访问。

### 4.8 `config/runtime.toml`

这是最重要的安全开关文件。当前支持：

- `read_only`
- `mark_notifications_read`
- `shadow_mode`
- `allow_send_reply`
- `require_approval_before_send`
- `panic_switch`
- `topic_cooldown_minutes`
- `blackout_start_hour`
- `blackout_end_hour`
- `muted_topic_ids`
- `muted_usernames`

推荐你理解这几个关键组合：

#### 最安全观察模式

```toml
read_only = true
allow_send_reply = false
require_approval_before_send = true
```

适合第一次接真实论坛。

#### 影子模式

```toml
shadow_mode = true
allow_send_reply = true
require_approval_before_send = true
```

会生成草稿并记录流水，但不会真的发，也不会进审批队列。

#### 人工审批模式

```toml
read_only = false
shadow_mode = false
allow_send_reply = true
require_approval_before_send = true
```

会进入待审批列表，人工确认后再发送。

#### 紧急停机模式

```toml
panic_switch = true
```

会快速冻结触发处理，是最直接的“止血开关”。

### 4.9 `config/personas.toml`

控制启用哪些人格模块，以及优先级。

### 4.10 `config/prompt_modules.toml`

控制三条提示词链路：

- `planner`
- `replyer`
- `memory`

每条链路都可以配置多个 `.md` 模块，并为每个模块设置 `enabled = true/false`。

---

## 5. 命令行使用方法

统一入口是 `main.py`。

### 5.1 初始化数据库

```bash
python main.py init-db
```

作用：

- 初始化 SQLite 表结构
- 首次启动前建议先执行一次

### 5.2 单次运行一轮 Worker

```bash
python main.py run-once
```

适合：

- 本地测试
- 配置改完后手工验证
- 不想让 Worker 一直循环时

### 5.3 启动长期运行 Worker

```bash
python main.py worker
```

适合正式运行。

### 5.4 启动管理端 Web 服务

```bash
python main.py web
```

启动后可访问：

- `http://127.0.0.1:8000/`（本地）
- `http://<你的主机IP>:8000/`（按配置开放时）

### 5.5 调试指定话题

```bash
python main.py debug-topics --topic-id 123
```

也可以一次传多个：

```bash
python main.py debug-topics --topic-id 123 --topic-id 456
```

不传 `--topic-id` 时，也可以用 `--count` 让系统自动挑选若干话题调试。

---

## 6. 管理端能做什么

当前管理端已经不是空壳，已经具备一批真实可用能力。

### 6.1 看当前系统状态

你可以通过首页和 `/config` 看到：

- 当前论坛地址
- 当前模型路由
- 当前运行时安全开关
- 当前可用提示词文件 / 人格文件 / 模块文件
- 当前提示词编排链路

### 6.2 直接切换运行模式（运行模式切换）

现在首页已经支持直接切换四种核心运行模式：

- `read-only`
- `shadow`
- `approval`
- `direct-send`

它们对应的含义分别是：

- **read-only**：完全只读，禁止发送
- **shadow**：生成草稿并记录流水，但不发送、也不进入待审批
- **approval**：生成草稿并进入待审批列表，人工确认后发送
- **direct-send**：满足条件时直接自动发送

WebUI 切换模式时，会把对应状态写回 `config/runtime.toml`，并立即刷新当前 Web 进程内的配置引用。

### 6.3 直接编辑非敏感配置（非敏感配置编辑）

现在首页还支持直接编辑一部分**非敏感** TOML 配置文件，当前允许的范围包括：

- `forum.toml`
- `models.toml`
- `personas.toml`
- `prompt_modules.toml`
- `runtime.toml`
- `scheduler.toml`
- `thresholds.toml`
- `webui.toml`

注意：

- **账号密码不会出现在这里**
- **API Key 也不会出现在这里**
- `credentials.toml` / `providers.toml` 仍然需要你在宿主机本地手工维护

### 6.4 管理提示词

支持：

- 查看提示词文件列表
- 查看单个提示词内容
- 创建新的提示词文件
- 更新已有提示词文件

### 6.5 管理人格

支持：

- 查看人格文件列表
- 查看当前启用人格与优先级
- 创建新人格文件
- 更新已有人格文件

### 6.6 管理记忆

支持：

- 查看全部记忆
- 查看/更新自我记忆
- 更新某个用户的用户记忆

### 6.7 看运行日志

支持：

- 查看日志文件列表
- 查看最新日志尾部内容

默认日志文件是：`logs/latest.log`

### 6.8 看事件与流水

支持：

- 最近 Pipeline 流水
- 单条流水详情
- 最近触发事件
- 话题状态

### 6.9 审批待发送回复

当启用“审批后发送”时，系统会把草稿放进待审批列表。你可以：

- 查看待审批回复
- 人工批准发送

### 6.10 调试单个话题

这是很实用的功能。你可以查看某个话题在当前配置下：

- 会不会回复
- 为什么回复/为什么跳过
- 草稿是什么
- 使用了哪些人格模块
- Planner / Replyer / Memory 的调试提示信息

---

## 7. 小白开箱即用教程

如果你完全没接触过这个项目，按下面一步一步做。

### 第一步：准备环境

你需要：

- Python 3.11
- Git
- 一个可用的论坛账号
- 一个可用的大模型 API

建议先在一个全新的虚拟环境里操作。

### 第二步：安装依赖

在项目根目录执行：

```bash
pip install -e .[dev]
```

### 第三步：检查并修改配置

重点看 `config/` 目录下这些文件：

- `credentials.example.toml`：论坛账号密码示例，先复制为 `credentials.toml` 再填写
- `forum.toml`：论坛地址
- `providers.example.toml`：模型提供方示例，先复制为 `providers.toml` 再填写
- `models.toml`：各环节模型选择
- `runtime.toml`：运行安全开关

第一次使用时，可以这样准备：

```bash
cp config/credentials.example.toml config/credentials.toml
cp config/providers.example.toml config/providers.toml
```

如果你在 Windows PowerShell 中操作，也可以手动复制并重命名这两个文件。

第一次接真实论坛时，强烈建议你把 `runtime.toml` 保持在安全状态，例如：

```toml
read_only = true
allow_send_reply = false
require_approval_before_send = true
panic_switch = false
```

### 第四步：初始化数据库

```bash
python main.py init-db
```

执行后，默认会生成：

- `data/suenmeow.sqlite3`

### 第五步：启动管理端

```bash
python main.py web
```

然后在浏览器打开：

```text
http://127.0.0.1:8000
```

也可以访问健康检查：

```text
http://127.0.0.1:8000/health
```

正常时应返回：

```json
{"status": "正常"}
```

### 第六步：先跑一轮，不要长期运行

```bash
python main.py run-once
```

这一步的目标不是“马上自动发帖”，而是确认：

- 能登录论坛
- 配置能正常加载
- 日志能正常写入
- 没有明显报错

### 第七步：看日志

打开：

- `logs/latest.log`

如果这里已经有运行日志，说明主流程基本通了。

### 第八步：再决定进入哪种运行模式

你有三种常见选择：

#### 方案 A：继续只观察（最推荐新手）

保持：

```toml
read_only = true
allow_send_reply = false
```

然后反复用：

```bash
python main.py run-once
```

#### 方案 B：先用影子模式演练

```toml
read_only = false
shadow_mode = true
allow_send_reply = true
require_approval_before_send = true
```

这样系统会完整生成草稿，但不会真的发出去。

#### 方案 C：进入人工审批模式

```toml
read_only = false
shadow_mode = false
allow_send_reply = true
require_approval_before_send = true
```

这样会进入待审批列表，由你手工批准发送。

### 第九步：需要长期运行时，启动 Worker

```bash
python main.py worker
```

到这里，系统才会持续轮询并处理事件。

---

## 8. Docker 开箱即用教程

这是当前**最推荐**的部署方式。如果你不是在做本地开发，而是想稳定跑起来，优先用 Docker。

### 8.1 先准备 `.env`

复制一份：

```bash
cp .env.example .env
```

Windows 用户可以手动复制文件。

`.env` 里常用项：

- `SUENMEOW_WEB_PORT=8000`
- `SUENMEOW_CONFIG_DIR=./config`
- `SUENMEOW_DATA_DIR=./data`
- `SUENMEOW_LOG_DIR=./logs`

### 8.2 启动整个栈

```bash
docker compose up --build
```

现在会启动两个服务：

- `suenmeow-web`
- `suenmeow-worker`

它们会共享：

- `config/`
- `data/`
- `logs/`

因此你通过 WebUI 修改的**非敏感配置**、运行产生的数据库、日志文件，都会保留在宿主机目录中。

### 8.3 验证启动成功

检查：

- 浏览器打开 `http://localhost:8000/health`
- 返回 `{"status": "正常"}`
- `logs/latest.log` 已生成
- `data/suenmeow.sqlite3` 已生成

### 8.4 更严格的生产启动方式

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

这个模式下：

- 配置目录会只读挂载
- 环境标记为 production
- 更接近正式部署

另外，Compose 当前还启用了：

- `init: true`，减少容器内僵尸进程/信号处理问题
- `stop_grace_period: 30s`，给 Worker 留出更稳妥的退出时间

更详细的部署、重启验证、回滚步骤请看：

- [`DEPLOY.md`](DEPLOY.md)

---

## 9. 环境变量说明

目前已经实际接入代码的环境变量有：

- `SUENMEOW_CONFIG_DIR`
- `SUENMEOW_DATA_DIR`
- `SUENMEOW_LOG_DIR`
- `SUENMEOW_WEB_PORT`（Compose 层使用）

其中前三个由 `AppPaths.from_root()` 解析，规则如下：

- 不填：默认使用 `root/config`、`root/data`、`root/logs`
- 填绝对路径：直接使用绝对路径
- 填相对路径：相对于 `main.py --root` 传入的项目根目录解析

这意味着你可以把：

- 配置文件
- 数据库
- 日志

放到项目目录外部的专用位置。

---

## 10. 常见操作示例

### 10.1 查看健康状态

```bash
curl http://127.0.0.1:8000/health
```

### 10.2 初始化数据库

```bash
python main.py init-db
```

### 10.3 只跑一轮

```bash
python main.py run-once
```

### 10.4 长期运行

```bash
python main.py worker
```

### 10.5 调试指定话题

```bash
python main.py debug-topics --topic-id 123
```

### 10.6 启动管理端

```bash
python main.py web
```

---

## 11. 运行安全建议

强烈建议按下面顺序上线：

1. **只读模式**
2. **影子模式**
3. **审批模式**
4. **自动发送**

不要一上来就直接自动发。

推荐你在真实环境里始终保留这些习惯：

- 先看 `/health`
- 先看 `logs/latest.log`
- 先确认 `runtime.toml` 当前是否安全
- 出现异常先打开 `panic_switch = true`

---

## 12. 当前已知限制

当前仓库已经能跑，但仍有一些明确边界：

- Worker 还没有独立 HTTP 健康检查端点
- 部署层面已经有最小可用方案，但最终全量验收仍取决于后续 `F1`–`F4` 收尾验证
- 文档里只描述已实现能力，不代表所有未来规划都已完成

---

## 13. 推荐阅读顺序

如果你第一次接手这个仓库，建议按这个顺序看：

1. 本文档 `README.md`
2. 部署文档 [`DEPLOY.md`](DEPLOY.md)
3. `config/runtime.toml`
4. `main.py`
5. `bot/settings.py`
6. `web/routes/`

这样最快建立整体认知。
