# SuenMeow 2

独立实现的 Discourse 猫咪伙伴。中文控制台配置人格、回复节奏、四条模型路由、记忆、人工审核和猫窝；所有论坛写入统一限速，仅回复既有主题或既有私信。

## 开发

需要 Python 3.12+、Node 22+；正式部署使用 PostgreSQL。SQLite 仅用于本地快速验证。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/suenmeow init
PUBLIC_ORIGIN=http://localhost:5173 .venv/bin/uvicorn suenmeow.api:create_app --factory --host 127.0.0.1 --port 8000
```

另一终端：

```sh
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:5173`，使用初始化时的管理员账户。生产密钥不能用于本地界面演示。编辑者账户由管理员在 GUI 创建。

## 检查

```sh
.venv/bin/python -m pytest -q
cd frontend
npm run build
```

测试不连接真实论坛或真实模型。数据库权限和并发测试的生产验证单独记录在 [验收文档](docs/ACCEPTANCE.md)。

## 部署

```sh
python3 tools/prepare_deploy.py --origin https://suenmeow.example.com
docker compose build
docker compose up -d database
docker compose --profile setup run --rm init
docker compose up -d api worker gateway
```

网关仅监听服务器 `127.0.0.1:8000`；通过已有 HTTPS 反向代理或隧道公开访问。数据库没有主机端口。首次运行默认暂停。

管理员用户名为 `admin`，初始密码保存在服务器 `secrets/admin.password`。`secrets/` 目录权限为 0700；Compose 仅把需要的单个文件挂载到相应容器。不要提交 `.env`、密钥、密码或数据库。备份数据库时也必须同时备份加密密钥，否则无法解密保存的连接与私信数据。

先设置连接、检查 prompts 编排并发布，开启只读模式检查新水位，再选择审核或自动模式。修改连接自动暂停；重启、重连、模式切换和发布后重新跳过积压。原草稿不能强制补发。发送超时进入“待核实”，管理员在论坛核实后标记结果，系统不重试原事件。

## 猫窝

先由用户在论坛创建个人主题，再在 GUI 绑定其 ID 与创建者。可放置小物件、便签和连续小活动。全局趣味互动与猫窝日记都开启时，最多每天一次主动回复；安静时段不发，不补发。私信只绑定已有对话，跟进同一未完理由最多一次，对方沉默就停止。个人私信记忆仅用于原对话。

## 设计与迁移

管理员可在“与猫交谈”页交代研究任务，查看进度、真实来源和草稿。写回复时填写唯一既有主题 ID、可选楼层与字符上限；默认预览，确认当前正文后才进入发送门。暂停时可以研究。改变目标需创建新任务；编辑草稿撤销原批准。自动研究默认关闭，可在研究策略中调整工具和预算。

在“连接与模型”配置支持原生工具调用的可选 Agent 路由；没有专用路由时使用明确支持工具调用的规划模型。模型地址可选择基础地址或完整请求端点；旧配置导入保留完整端点语义。实际模型和论坛的兼容性以验收记录为准。

- [实施规格](docs/SPECIFICATION.md)
- [Agentic 主动研究与管理员聊天设计](docs/AGENTIC.md)（实现与验收中）
- [验收清单](docs/ACCEPTANCE.md)
- [MacBook 迁移和回滚](docs/MIGRATION.md)

旧版代码不属于本实现。旧版 prompts 与连接由离线导入器读取；旧事件、待发队列和记忆不导入。导入后 prompts 保留为草稿，管理员确认发布后才生效。
