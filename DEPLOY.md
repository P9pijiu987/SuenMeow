# SuenMeow 部署指南

## 1. 部署形态

> 推荐主部署方式：**Docker Compose**。
>
> 直接使用 `python main.py ...` 仍然适合本地调试，但这个项目默认的运行形态是 `Web + Worker` 双服务 Compose 栈。

SuenMeow 以两个长期运行的进程工作：

- `suenmeow-web`：FastAPI 管理端与 `/health` 健康检查接口
- `suenmeow-worker`：触发引擎、轮询循环、Pipeline 执行

两个服务共用同一个镜像，并挂载同一组持久化目录：

- `config/` —— TOML 运行配置
- `suenmeow_data`（Docker named volume）—— SQLite 数据库（容器内 `/app/data/suenmeow.sqlite3`）
- `logs/` —— 运行日志（`logs/latest.log`）

基础 Compose 栈还额外启用了：

- `init: true`：改善信号处理与子进程回收
- `stop_grace_period: 30s`：给 Worker 更平滑的停止窗口

## 2. 环境布局

### 本地 / 预发布环境

直接使用基础 Compose 文件：

```bash
docker compose up --build
```

### 类生产环境

如果你希望容器内的配置目录以只读方式挂载，请使用生产覆盖文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

注意：该模式下 `./config:/app/config:ro` 为只读挂载，因此 WebUI 中涉及写配置的操作（如运行模式切换、非敏感 TOML 保存）会失败；生产环境应在宿主机修改配置后再重启服务。

## 3. 配置说明

- 默认 Web 绑定地址是 `0.0.0.0`，因此容器内的 Web 服务可以通过端口映射暴露出来。
- 对外访问端口可以通过 `.env` / Compose 变量 `SUENMEOW_WEB_PORT` 调整。
- 支持通过以下环境变量改写路径：
  - `SUENMEOW_CONFIG_DIR`
  - `SUENMEOW_DATA_DIR`
  - `SUENMEOW_LOG_DIR`
- 如果这些路径写成相对路径，会相对于 `main.py --root` 传入的项目根目录解析。

## 4. 健康检查

- 容器健康检查定义在 `suenmeow-web` 服务上。
- Compose 会在容器内探测 `http://127.0.0.1:8000/health`。
- 预期响应是 HTTP `200`，JSON 内容为 `{"status": "正常"}`。

## 5. 首次启动检查清单

1. 如果你需要不同的对外端口或路径覆盖，先把 `.env.example` 复制为 `.env`。
2. 检查 `config/` 下所有配置文件。
3. 第一次连接真实论坛前，确认 `config/runtime.toml` 仍处于安全模式：
   - `read_only = true`
   - `allow_send_reply = false`
   - `require_approval_before_send = true`
4. 启动整个栈。
5. 打开 `http://localhost:${SUENMEOW_WEB_PORT:-8000}/health`。
6. 确认 `logs/latest.log` 已生成。
7. 确认 `docker compose exec suenmeow-web ls /app/data` 可看到 `suenmeow.sqlite3`。

## 6. 重启 / 持久化验证

升级、迁移或主机重启后，建议至少做一轮最小验证：

1. `docker compose ps` 能看到两个服务都在运行。
2. `docker compose logs --tail=100 suenmeow-web suenmeow-worker` 看起来是正常启动，没有 crash loop。
3. `curl http://localhost:${SUENMEOW_WEB_PORT:-8000}/health` 返回 `{"status": "正常"}`。
4. 执行停止：`docker compose down`。
5. 再重新启动。
6. 启动后确认以下文件/目录仍然存在：
    - `config/`
    - `/app/data/suenmeow.sqlite3`（位于 Docker 卷 `suenmeow_data`）
    - `logs/latest.log`
7. 重新打开管理端，确认最近的流水、审批数据或管理状态仍然存在。

补充建议：

- 可用 `docker volume inspect suenmeow_data` 确认卷存在。
- 若怀疑启动竞态导致 SQLite 锁冲突，先看 worker/web 日志中是否仅短暂告警，随后恢复正常。

## 7. 滚动修改配置

- 在宿主机上直接修改 `config/` 下的 TOML 文件。
- 如果改动属于启动期配置，请重启受影响的服务。
- 运行时开关、部分 Planner / 阈值配置已经支持热重载，但部署层面的修改仍建议保守处理。

对于 Web 服务，现在管理端已经可以直接：

- 切换运行模式：`read-only`、`shadow`、`approval`、`direct-send`
- 编辑非敏感 TOML 文件，例如：`runtime.toml`、`scheduler.toml`、`thresholds.toml`、`webui.toml`、`forum.toml`、`models.toml`、`personas.toml`、`prompt_modules.toml`

以下敏感文件仍然只允许在宿主机维护，故意不开放给 WebUI 编辑：

- `config/credentials.toml`
- `config/providers.toml`

## 8. 回滚手册

如果部署后状态异常，可以按下面步骤回滚：

1. 先把系统切回安全姿态，在 `config/runtime.toml` 中设置：
   - `read_only = true`
   - `allow_send_reply = false`
   - 如需快速冻结触发处理，可设置 `panic_switch = true`
2. 切回上一个已知可用的镜像或工作树。
3. 再次启动整个栈：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

4. 检查 `/health` 与 `docker compose logs`。
5. 在重新开启任何发送能力前，先确认 `/app/data/suenmeow.sqlite3`（卷内）与 `logs/latest.log` 都完好无损。

### 回滚路径差异提醒（旧版 bind mount ↔ 新版 named volume）

- 新版使用 `suenmeow_data:/app/data`。
- 旧版使用 `./data:/app/data`。
- 在这两种部署之间来回切换时，必须显式迁移 SQLite 文件：
  1. 停机并备份当前数据库。
  2. 从当前存储位置导出 `suenmeow.sqlite3`。
  3. 导入到目标存储位置后再启动。
  4. 启动后通过 `/health` + 管理端流水记录进行一致性核验。

## 9. 当前限制

- Worker 还没有单独的 HTTP 健康检查端点，目前只能通过间接方式观察。
- 生产就绪性仍依赖最终收尾验证波次（`F1`–`F4`），所以现在更适合“先做一轮真实试部署”，而不是直接盲目长期上线。
