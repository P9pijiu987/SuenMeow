# SuenMeow

## 推荐默认部署方式

推荐使用 Docker Compose 进行部署与升级：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

如需持久化并避免 pull/rebuild 覆盖自定义提示词与人格文件，请在宿主机维护以下目录并通过 compose 挂载：

- `prompts/`
- `personas/`
- `prompts_public/`
- `personas_public/`

## 运行模式切换

系统支持以下运行模式（可在 WebUI 中切换）：

- `read-only`: 只读观测，不发送回复
- `approval`: 生成回复并进入审核
- `direct-send`: 直接发送回复

## 非敏感配置编辑

WebUI 提供非敏感配置编辑能力，例如：

- 运行模式
- 阈值与调度相关配置
- 提示词模块编排

敏感配置（如 `config/credentials.toml`、`config/providers.toml`）建议仅在服务器本地维护。

## 什么时候才会触发 Planner

系统会基于触发条件与阈值策略决定是否进入 Planner 流程，常见影响因素包括：

- 新回复/热度达到阈值
- 预算与冷却时间限制
- 运行模式与静音/封禁状态

建议在管理端结合日志与运行记录观察触发行为，再迭代调整配置。
