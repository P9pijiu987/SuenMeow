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

## 近期一键导入升级（2026-10-05）

显式 init 新增 `memory_import_settings`，默认分类 ID 22；不修改论坛身份、账户权限、21 个模块、发布 v3 或只读模式。新增配置无表结构变化。旧导入任务保留原预览/选择保存兼容流程，新建任务强制分类核验；未完成付费任务在 worker 重启后失效，不自动重试。新一键流程保存事实与游标同事务，水位推进到最新，近期窗口以外旧历史有意省略。

升级前保存 `v2-before-recent-import-20261005.dump` 和匹配上一版源码的 `v2-code-before-recent-import-20261005.tar.gz`，均在受限备份目录、权限 0600。数据库归档已包含新增默认分类设置，应用仍是切换前版本。对应源码由前次已完成的自助版本归档复制，避免误把已上传新代码作为回滚代码。重建并验收后依次更新 API/worker/gateway；生产保持 read_only，重启重新建立论坛水位，不补发积压。

完整 PostgreSQL 测试曾触发 test 容器 256 MiB 限额 OOM；仅提升测试容器至 512 MiB，API/worker 各 256 MiB、网关 64 MiB 保持原限额。回滚仍使用匹配代码和数据库重建，不依赖旧镜像标签；先停止新 worker，保持论坛发送关闭。

部署后另存 `v2-after-recent-import-20261005.dump` 与 `v2-code-after-recent-import-20261005.tar.gz`（0600）。数据库归档的 `pg_restore --list` 检查通过；配套源码不包含 secrets、runtime 或依赖目录。新归档尚未重新执行完整隔离恢复，历史演练证据保持独立。

真实用户反馈导入失败后，另保留 `v2-before-memory-output-fix-20261005.dump` 与 `v2-code-before-memory-output-fix-20261005.tar.gz`（0600），后者从上一版匹配源码归档复制。输出保护升级无 schema、提示词发布或人格变更；新任务采用 compact_json，旧失败任务保持失败及原用量，不能自动重跑或清空额度。生产发送模式继续只读。

修复后匹配归档为 `v2-after-memory-output-fix-20261005.dump` 与 `v2-code-after-memory-output-fix-20261005.tar.gz`（0600）。新数据库归档目录可读，不替代完整恢复演练。仅输出安全元数据的诊断为 `tools/diagnose_memory_import.py --topic 主题ID`；禁止为了诊断自动重跑用户的付费任务。

## 全量研究升级

用户明确要求所有发言都研究。init 增量建立 `memory_full_coverage` 和 `memory_tombstones`，无表结构变化；保留旧近期游标作并发检查，首次全量读取全部历史，成功后才记录新覆盖标记。原预览任务维持兼容；新任务不受旧个人 token/三次额度限制，仍遵守管理员全站日额度。worker 的全量任务超时延长至24小时，重启后仍中断，用户可以手动继续加密断点，不自动重新调用模型。

升级前归档 `v2-before-full-memory-20261005.dump` 与 `v2-code-before-full-memory-20261005.tar.gz`（0600），源码从上一版配套归档复制。依序 init、重建 API/worker/gateway，保留只读、发布v3、21个模块和七个人格。旧失败任务与原收费记录不重跑、不清空。新增删除来源指纹不能追溯恢复升级前已经删除的来源信息。

部署后匹配归档为 `v2-after-full-memory-20261005.dump` 与 `v2-code-after-full-memory-20261005.tar.gz`，均0600、受限目录0700。数据库归档254,381字节，`pg_restore --list` 通过；此轮不宣称新归档完成完整恢复演练。源码归档排除依赖、缓存、secrets 与 runtime。

## 个人贴编排升级（2026-10-05）

显式 init 新增 `topic_pipelines` 表和 `topic_pipeline_lock`，schema 2、账号身份、原模块、发布v3和只读模式保持。备份 `v2-before-topic-pipelines-20261005.dump` 及 `v2-code-before-topic-pipelines-20261005.tar.gz`（0600）；配套源码从上一版完整历史研究归档复制。切换前确认没有进行中的记忆导入，再更新 API/worker/gateway，重启仍建立新水位、跳过积压。个人编排草稿不启用发送，回滚同时恢复匹配代码与数据库。

部署后配套归档为 `v2-after-topic-pipelines-20261005.dump` 和 `v2-code-after-topic-pipelines-20261005.tar.gz`（0600、受限目录0700）；数据库归档272,199字节，`pg_restore --list`可读。源代码排除secrets、runtime、依赖与缓存。临时HTTPS权限验收账户已停用；系统继续read_only，没有替真实用户创建或发布个人编排。

## 人格与个人编排可选审核（2026-10-05）

用户要求审核可选且默认关闭，范围包含persona。显式init新增`topic_pipeline_settings.require_review=false`与`persona_publications`，不改原人格、账号、运行模式或既有发布快照。既有个人编排和待审核草稿保持原状态，用户可显式保存或启用；不在迁移时批量发布。管理员在两处提示词/个人编排界面调整同一开关。

人格后续保存/审核发布时才更新已生效使用；仅已发布全局编排引用的人格会生成选择性新快照，保留系统工作规则、全局顺序及策略，新epoch仍重新建立论坛水位。旧发送和个人研究断点的专属运行代次失效，不能自动重试收费。全局系统提示词和编排继续管理员发布。开启审核为当时现有人格建立基线，未来修改待审核；关闭不批量启用旧草稿。

切换前备份`v2-before-optional-review-20261005.dump`及匹配上一版源码`v2-code-before-optional-review-20261005.tar.gz`，切换后使用`v2-after-optional-review-20261005.dump`及`v2-code-after-optional-review-20261005.tar.gz`；目录0700、文件0600。先确认没有运行中的记忆导入，再init、切换服务和复核21个模块哈希。回滚恢复匹配代码/数据库并保留密钥，始终先关闭发送；新归档只验证目录可读，不代替完整隔离恢复演练。
