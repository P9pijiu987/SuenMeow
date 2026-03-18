# SuenMeow

SuenMeow 是面向论坛自动化回复与管理的服务。

## 推荐默认部署方式

推荐使用 **Docker Compose** 进行部署与运维，便于统一管理 web / worker / data / logs。

## WebUI 功能概览

- 运行模式切换：支持 read-only / approval / direct-send 等模式切换。
- 非敏感配置编辑：可在 WebUI 编辑非敏感 TOML（不含密钥）。
- 提示词与人格管理：支持模块化编排与运行时调整。

## 触发机制说明

### 什么时候才会触发 Planner

Planner 仅在满足触发条件（通知、活跃度、规则阈值）后执行，不会对所有主题无差别运行。

## 公网编辑端说明

新增 public-web 端口用于公网受限编辑：

- 内置 prompts/personas 文件只读。
- 仅允许在 `prompts_public/` 与 `personas_public/` 新建/修改自定义 md。
- 仅开放受限编辑与只读记忆接口。

### Docker 双 Web 服务

默认 Compose 同时提供两个 Web 服务：

- `suenmeow-web`（管理端，默认 `8000`）
- `suenmeow-public-web`（公网受限端，默认 `8001`）

可通过环境变量调整端口：

- `SUENMEOW_WEB_PORT`
- `SUENMEOW_PUBLIC_WEB_PORT`

## 管理端登录提醒

8000 管理端可启用简单登录。账号密码请放在本地文件：

- `config/webui_admin_auth.toml`

该文件包含敏感信息，**不要上传到 GitHub**。
