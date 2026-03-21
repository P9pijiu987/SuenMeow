# DEPLOY

## 推荐主部署方式

推荐使用 Docker Compose 作为主部署方式：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

默认会拉起：

- `suenmeow-web`（管理端）
- `suenmeow-public-web`（公网受限编辑端）
- `suenmeow-worker`

端口变量：

- `SUENMEOW_WEB_PORT`（默认 8000）
- `SUENMEOW_PUBLIC_WEB_PORT`（默认 8001）

## 运行模式

- `read-only`: 只读观测
- `approval`: 生成待审核
- `direct-send`: 直接发送

## 配置与凭据

敏感配置请仅保存在本机：

- `config/credentials.toml`
- `config/providers.toml`

## 重启 / 持久化验证

服务重启后请确认以下状态仍存在：

- `data/suenmeow.sqlite3`
- `logs/latest.log`
- `config/`
- `prompts/`（主提示词与人格目录）
- `prompts_backup/`（每次修改自动追加备份）

## 回滚手册

1. 停止当前容器
2. 回滚镜像/代码到上一个稳定版本
3. 使用同一份 data 与 config 挂载重启
4. 验证健康检查与关键链路
