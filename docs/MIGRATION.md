# MacBook 迁移与回滚

## 实施前

目标为用户指定的 `zhangyichi@172.27.145.239`。重新检查实际工作目录和容器状态，不能将另一份旧 checkout 当作部署源。此前发现的实际部署路径为 `/Users/zhangyichi/Documents/program/SuenMeow`，仍须现场验证。

记录旧版本、挂载、容器名和端口。备份配置、prompts 与旧数据库到服务器上权限受限的目录；不将凭据提交 Git，不打印密钥、不读取无关服务的秘密。备份旧数据仅用于回滚。

## 导入

以离线导入器读取旧 TOML 配置和 Markdown prompts，生成新版加密连接设置与提示词草稿。分别核对文件数、摘要和路由映射。管理员检查兼容性后发布。旧格式不能直接映射时报告具体字段，禁止静默丢弃。

新库仅保留新账户、连接、策略与 prompts；不导入旧事件、待审核回复、记忆和通知水位。管理员密码和加密密钥只在服务器初始化；将首次登录信息安全交付用户。

## 切换

1. 构建独立镜像，验证数据库迁移和健康检查。
2. 停止旧 worker，确保只有一个论坛发送者。
3. 新系统以暂停模式启动，在本地验证登录、配置、权限和导入结果。
4. 保持公开域名与共享隧道结构，验证 HTTPS 和 Secure Cookie；禁止暴露数据库端口。
5. 开启只读模式并建立新水位，验证积压被跳过，再开启限速回复。
6. 记录镜像版本、检查结果、资源占用和实际地址。

## 回滚

暂停并停止新 worker，保留新库与审计记录。恢复旧镜像及其受限备份，在防补发策略确认前保持旧 worker 停止。回滚不能自动启动未经验证的旧发送者。

共享隧道曾有令牌暴露记录，应轮换该令牌；涉及其他服务的变更需要先确认影响并逐一验证。

## 已完成的恢复演练

2026-10-04，旧备份在独立临时目录恢复，33 个文件逐一校验，SQLite 完整性通过。新版 PostgreSQL 自定义格式备份恢复到独立数据库后，验证了 15 个 prompts、加密论坛凭据和四条模型连接；旧事件、回复、记忆仍为 0。演练库已删除，未启动任何旧发送者。

MacBook 的受限备份目录为 `/Users/zhangyichi/Documents/program/SuenMeow2-backups`。保留 `v2-before-activation-20261004.dump` 和配套 `v2-secrets-20261004.tar.gz`；二者权限为 0600。恢复时先恢复配套密钥和配置，随后导入隔离库验证，再决定是否切换。不要将秘密归档上传仓库。

首帖前系统 prompts 更新另外保留 `v2-before-prompt-refresh-20261004.dump` 和 `v2-after-prompt-refresh-20261004.dump`。后者已在独立库恢复并核验发布 v3、21 个模块哈希、7 个 persona、6 组加密连接以及 81 条加密聊天记录，事件和回复均为 0。演练库已清理，正式服务未切换到演练库。

新版归档的恢复核验命令如下，须在部署目录执行并使用与归档匹配的加密密钥。数据库名必须是未使用的隔离名称，不能覆盖正式 `suenmeow` 库：

```sh
docker compose exec -T database createdb -U suenmeow suenmeow_restore_check_example </dev/null
docker compose exec -T database pg_restore -U suenmeow -d suenmeow_restore_check_example < /path/to/backup.dump
docker compose run --rm -T --interactive=false -v "$PWD/tools:/checks:ro" api python /checks/verify_database_restore.py --database suenmeow_restore_check_example </dev/null
docker compose exec -T database dropdb -U suenmeow suenmeow_restore_check_example </dev/null
```

核验工具仅连接隔离库，只输出计数和通过状态，不输出解密内容。最后一条命令仅清理本次创建的演练库；失败时先检查诊断，保留原备份。

## 注册与工作区升级

保留 `v2-before-workspace-20261004.dump` 与 `v2-code-before-workspace-20261004.tar.gz`，位于上述受限备份目录，权限均为 0600。此次只有 settings 新增注册策略和锁记录，不改变既有账户权限、prompts、编排或发布快照。升级全程保持暂停或只读，显式执行 `Database(Settings.env().database_url).migrate()` 后重建 API/worker/gateway；既有模式继续只读，worker 重启后重新建立水位。

此前公开网页注册默认开启；2026-10-05 改为论坛私信登录后，生产已关闭无身份的网页自由注册。原管理员和旧账户仍可密码登录，不允许新用户自填论坛身份。

## 私信身份与个人贴导入升级

显式运行 init 增量创建 `forum_logins`、`forum_identities`、`memory_imports`、`memory_cursors` 和对应锁/特性版本标记，保留 schema 2、原账户、21 个模块和发布 v3。七个 persona 不改；不通过初始化重新导入旧事件或旧记忆。确认新登录入口正常后关闭兼容网页注册；禁止用生产模拟身份记录冒充普通用户。

升级前归档 `v2-before-memory-import-20261005.dump` 与 `v2-code-before-memory-import-20261005.tar.gz`，保存在原 MacBook 的受限备份目录。重建并依序启动 API/worker/gateway，仍保持只读。worker 重启先使未完成导入失效，不重新扣费；论坛水位重新建立，旧回复不能补发。回滚应同时恢复匹配的代码、数据库和受限密钥备份；新特性表不能被旧代码继续操作。

用户自助版本再次显式 init，新增 `memory_detect_cache`，不改变身份、发布快照或发送模式。保留 `v2-before-selfservice-20261005.dump` 和 `v2-code-before-selfservice-20261005.tar.gz`（0600）。配套上一版代码由已部署个人贴归档叠加两次私信登录修复重建；旧容器镜像 ID 已不能重新打标，因此回滚须从配套源码构建，不依赖旧镜像标签。自助导入仍在只读时可用；只读限制论坛写入，不妨碍用户主动保存本人记忆。

当前公开入口经过 Cloudflare Tunnel，网关仅绑定宿主机回环地址。Nginx 从 Cloudflare 的 [`CF-Connecting-IP`](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-connecting-ip) 生成并覆盖内部地址头；API 只接受 `TRUSTED_PROXY_HOST=gateway` 解析出的真实 TCP 对端，校验单一 IPv4/IPv6 地址，不信任外部 `X-Forwarded-For`。禁用 Uvicorn 自动代理头处理，API 错误尾斜线返回 404、网关重定向保持相对路径，避免错误 HTTP 重定向。直连/无头/DNS 失败回退为对端共享限速。切换代理供应商或公开绑定网关时，须同时调整并验证这一信任边界。

`tools/check_proxy.py` 在公开只读环境执行两次随机不存在账户的失败登录，核验伪造转发头被覆盖、两个请求落入同一真实来访地址桶，且不再使用网关共享桶。只输出布尔值，不输出原始 IP；不会重置其他人的限速计数。

`tools/check_workspace.py --prepare --fixture-file /probe/fixture.json` 创建一个受限账户和两个专用模块；省略阶段参数检查 HTTP 权限、CSRF 和冲突；`--cleanup` 核对原模块/编排/发布版本，再移除模块并禁用测试账户。须在暂停/只读环境运行，挂载临时清单目录与 `/run/secrets/probe_admin_password`，或用 `--admin-password-file` 指定忽略的本地密码文件。清单包含临时密码，保持 0600 并禁止提交；这些检查不调用模型或论坛发送。
