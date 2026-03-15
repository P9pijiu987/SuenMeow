# forum.rdfzer.com API 备忘

本文件基于以下两类信息整理：

1. WindWhisper 仓库中对 Discourse 论坛的实际调用实现。
2. 对 forum.rdfzer.com 的在线探测结果，用来确认这些接口风格确实适用于目标站点。

目标：为后续使用 Python 重写一个更贴近你设想的论坛拟人 AI bot 提供 API 依据。

## 结论先看

forum.rdfzer.com 确实是 Discourse 论坛，并且至少具备以下特征：

- `GET /about.json` 可访问，返回站点信息。
- `GET /session/csrf` 可访问，返回 `{"csrf": "..."}`。
- `GET /t/117.json?track_visit=true&forceLoad=true` 可访问，公开帖子结构里存在 `reactions` 字段。
- `GET /notifications?...` 在未登录时返回 `403`，说明通知接口需要登录态。

因此，WindWhisper 中那套论坛 API 路径基本可以直接迁移到 forum.rdfzer.com。

## WindWhisper 中的论坛配置结构

项目里论坛主配置对应的数据结构是：

```json
{
  "url": "https://forum.rdfzer.com",
  "username": "your_username",
  "password": "your_password",
  "retry": 3,
  "defaultHeaders": {},
  "reactions": {
    "heart": "爱心，同时也是默认的点赞行为"
  }
}
```

含义：

- `url`: 论坛根地址。
- `username`, `password`: 登录凭据。
- `retry`: 登录失败或请求失败时的重试次数。
- `defaultHeaders`: 额外请求头，某些站点有反代或风控时可补。
- `reactions`: 论坛可用 reaction 名称到中文说明的映射，供上层 bot 选择使用。

## 登录与会话维护

WindWhisper 的登录顺序不是直接 POST 用户名密码，而是先拿 cookie，再拿 csrf，再登录。

### 1. 预取挑战 cookie

```http
GET /session/passkey/challenge.json
```

作用：

- 不一定真的要做 passkey 登录。
- 更像是先拿一组初始 cookie，供后续 `/session/csrf` 和 `/session` 使用。

### 2. 获取 csrf token

```http
GET /session/csrf
Cookie: <上一步得到的 cookie>
```

返回体示例：

```json
{
  "csrf": "9ywq-s7Zzwkz0gZjsh0oU1psjuHm9O74WCXnOAxHNVGr-SgdlOCGqbKBJqkKL0pEz7Yz2IJ7SqnOlXkYBkwDSw"
}
```

### 3. 提交账号密码登录

```http
POST /session
Cookie: <当前 cookie>
X-Csrf-Token: <csrf>
Content-Type: multipart/form-data
```

表单字段：

- `login`: 用户名
- `password`: 密码
- `second_factor_method`: 固定写 `1`
- `timezone`: WindWhisper 里写的是 `Asia/Shanghai`

### 4. 后续请求的会话处理

WindWhisper 的做法：

- 所有登录后请求都携带 `Cookie` 和 `X-Csrf-Token`。
- 如果响应头里出现 `Set-Cookie`，就把新旧 cookie 合并覆盖。
- 如果返回 `401` 或 `403`，并且响应体包含 `BAD CSRF`，就重新执行登录流程，刷新 cookie 和 csrf。
- 如果返回 `429` 或 `5xx`，按 `retry` 重试。

这套逻辑很适合你直接用 Python 封装成一个 `ForumClient`。

## WindWhisper 默认附带的请求头

除 `Cookie` 和 `X-Csrf-Token` 外，WindWhisper 还会带这些头：

```http
discourse-logged-in: true
discourse-present: true
dnt: 1
priority: u=1, i
sec-ch-ua: "Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"
user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0
x-requested-with: XMLHttpRequest
x-silence-logger: true
```

实务建议：

- 真正关键的一般是 `Cookie`、`X-Csrf-Token`、`User-Agent`。
- `x-requested-with: XMLHttpRequest` 对某些 Discourse 接口有帮助。
- 其余头通常可先照抄，后面如果测试发现不必要，再逐步删。

## 已确认可用的核心接口

以下均来自 WindWhisper 的真实调用。

### 1. 获取站点信息

```http
GET /about.json
```

用途：

- 公开可读。
- 用来检查站点是否可达、是否是 Discourse、取类别和管理员信息。

在 forum.rdfzer.com 上已验证可用。

### 2. 获取话题信息

```http
GET /t/{topic_id}.json?track_visit=true&forceLoad=true
```

WindWhisper 从返回体中主要读取这些字段：

- `title`
- `category_id`
- `highest_post_number`
- `archetype`

典型用途：

- 判断话题标题。
- 判断话题类型。
- 获取最大楼层。
- 再根据 `category_id` 请求分类详情。

`archetype` 在项目里有两种特别处理：

- `regular`: 普通话题
- `private_message`: 私信话题

### 3. 获取分类信息

```http
GET /c/{category_id}/show.json
```

WindWhisper 读取字段：

- `category.name`
- `category.color`
- `category.text_color`

### 4. 按帖子 ID 批量获取帖子

```http
GET /t/{topic_id}/posts.json?post_ids%5B%5D={post_id_1}&post_ids%5B%5D={post_id_2}&include_suggested=false
```

等价于：

```http
GET /t/{topic_id}/posts.json?post_ids[]={post_id_1}&post_ids[]={post_id_2}&include_suggested=false
```

WindWhisper 实际上手工拼的是 URL 编码后的 `post_ids%5B%5D`。

读取字段：

- `post_stream.posts[].id`
- `post_stream.posts[].topic_id`
- `post_stream.posts[].username`
- `post_stream.posts[].cooked`
- `post_stream.posts[].reply_to_post_number`
- `post_stream.posts[].post_number`
- `post_stream.posts[].current_user_reaction.id`
- `post_stream.posts[].reactions[].id`
- `post_stream.posts[].reactions[].count`

注意：

- `cooked` 是 HTML，不是纯文本。
- `reply_to_post_number` 可能不存在，WindWhisper 默认按 `0` 处理。
- `current_user_reaction` 可能为空。

### 5. 按楼层附近读取帖子

```http
GET /t/{topic_id}/{post_number}.json?include_suggested=false
```

WindWhisper 用这个接口实现“查看某楼前后帖子上下文”。

它读取的字段和上一节基本一样，也是从：

- `post_stream.posts[]`

里提取帖子数据。

对拟人 bot 很实用，因为你往往不需要整帖全量拉取，只需要围绕被提及楼层读取上下文。

### 6. 发帖 / 回复

```http
POST /posts.json
Content-Type: application/json
```

请求体：

```json
{
  "raw": "回复内容",
  "topic_id": 117,
  "reply_to_post_number": 3
}
```

说明：

- `raw`: 帖子原始 Markdown 文本。
- `topic_id`: 目标话题。
- `reply_to_post_number`: 可选。若为 `0` 或不传，则更像是在话题中新增一层回复，而不是挂到某一楼下。

WindWhisper 的逻辑是：

- 如果 `replyTo != 0`，才写入 `reply_to_post_number`。

### 7. 点赞 / reaction 切换

```http
PUT /discourse-reactions/posts/{post_id}/custom-reactions/{action}/toggle.json
```

这是 WindWhisper 当前用于点赞的接口，明显依赖 discourse-reactions 插件。

已在 forum.rdfzer.com 的公开话题 JSON 中确认：帖子结构里存在 `reactions` 字段，因此这个站点大概率已启用对应机制。

`action` 的值不是固定只有 `heart`，取决于论坛配置。WindWhisper 通过本地 `config.json` 的 `reactions` 映射来约束上层 bot 可选动作。

建议你后续自行登录论坛网页抓一遍真实可用 reaction 名称，再填到你的 Python 配置里。

### 8. 获取未读通知

```http
GET /notifications?limit=50&recent=false&bump_last_seen_reviewable=true
```

未登录时，forum.rdfzer.com 上此接口返回 `403`，说明需要登录态。

WindWhisper 对返回体里每个通知只关心这些字段：

- `notifications[].read`
- `notifications[].topic_id`
- `notifications[].id`

并且只保留：

- `read != true`
- `id` 存在

形成内部结构：

```json
{
  "topicId": 123,
  "notificationId": 456789
}
```

### 9. 标记通知已读

```http
PUT /notifications/mark-read
Content-Type: application/x-www-form-urlencoded
```

表单字段：

- `id`: 通知 ID

WindWhisper 请求体等价于：

```http
id=456789
```

## WindWhisper 实际提取的帖子数据模型

为了方便你后续在 Python 里照着建模，这里把核心字段整理成一个简单结构：

```json
{
  "id": 340,
  "topicId": 117,
  "username": "suen",
  "cooked": "<p>...</p>",
  "replyTo": 0,
  "postNumber": 1,
  "myReaction": null,
  "reactions": {
    "heart": 3
  }
}
```

字段解释：

- `id`: 帖子 ID，用于 reaction 接口。
- `topicId`: 话题 ID。
- `username`: 作者用户名。
- `cooked`: Discourse 渲染后的 HTML。
- `replyTo`: 回复到哪一层，缺失则当 `0`。
- `postNumber`: 楼层号，给上下文抓取和回复定位用。
- `myReaction`: 当前登录用户对这帖是否已点某个 reaction。
- `reactions`: 每种 reaction 的计数。

## 推荐的 Python 封装方式

建议你先不要直接做全自动 bot，而是先做一个极薄的同步或异步 API 客户端层。

最小接口建议：

```python
class ForumClient:
    def login(self) -> None: ...
    def get_csrf(self) -> str: ...
    def get_topic(self, topic_id: int) -> dict: ...
    def get_category(self, category_id: int) -> dict | None: ...
    def get_posts_by_ids(self, topic_id: int, post_ids: list[int]) -> list[dict]: ...
    def get_posts_around(self, topic_id: int, post_number: int) -> list[dict]: ...
    def send_post(self, topic_id: int, raw: str, reply_to_post_number: int | None = None) -> dict: ...
    def toggle_reaction(self, post_id: int, action: str = "heart") -> dict: ...
    def get_unread_notifications(self) -> list[dict]: ...
    def mark_notification_read(self, notification_id: int) -> dict: ...
```

这样你之后无论做：

- 轮询通知驱动型 bot
- 指令触发型 bot
- 手动审核后发送
- 半自动拟人账号

都可以复用同一层。

## Python 请求顺序示例

下面是按 WindWhisper 逻辑改写的最小流程示意：

```python
import requests


class ForumClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.csrf_token = None

        self.session.headers.update(
            {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                "x-requested-with": "XMLHttpRequest",
                "discourse-logged-in": "true",
                "discourse-present": "true",
                "x-silence-logger": "true",
                "dnt": "1",
            }
        )

    def login(self):
        self.session.get(f"{self.base_url}/session/passkey/challenge.json", timeout=15)

        res = self.session.get(f"{self.base_url}/session/csrf", timeout=15)
        res.raise_for_status()
        self.csrf_token = res.json()["csrf"]
        self.session.headers["x-csrf-token"] = self.csrf_token

        res = self.session.post(
            f"{self.base_url}/session",
            files={},
            data={
                "login": self.username,
                "password": self.password,
                "second_factor_method": "1",
                "timezone": "Asia/Shanghai",
            },
            timeout=15,
        )
        res.raise_for_status()
        return res

    def get_topic(self, topic_id: int):
        res = self.session.get(
            f"{self.base_url}/t/{topic_id}.json",
            params={"track_visit": "true", "forceLoad": "true"},
            timeout=15,
        )
        res.raise_for_status()
        return res.json()
```

## 你接下来最容易踩的坑

### 1. `cooked` 是 HTML，不是纯文本

如果你直接把 `cooked` 喂给 LLM，建议先：

- 适度清洗 HTML
- 保留引用、代码块、链接、图片 alt
- 过滤 Discourse 自动插入的无关控件内容

### 2. reaction 用的是帖子 ID，不是楼层号

WindWhisper 的点赞接口入参是：

- `post_id`

不是：

- `post_number`

这两个必须分清。

### 3. 回复时如果要挂到某层，传的是 `reply_to_post_number`

也就是说：

- 点赞看 `post.id`
- 回复挂楼看 `post.post_number`

### 4. 通知流很容易造成重复处理

WindWhisper 的 README 里特别提醒：

- 如果论坛设置成“被点赞时也通知”，bot 可能因为自己的互动或别人的点赞重复进入同一话题。

所以你重写时最好加：

- 最近处理 topic 的短期去重缓存
- notification 去重
- 发送前复读检查

### 5. `BAD CSRF` 需要自动重登

这不是异常情况，而是你应该在客户端层面内建的恢复分支。

## 建议的最小重整路线

比起直接照搬 WindWhisper，我建议你拆成三层：

1. `forum_client.py`
   只做 HTTP、登录、重试、数据提取。
2. `topic_processor.py`
   决定读取哪些帖、如何构造上下文、何时回复或点赞。
3. `persona_bot.py`
   管人格设定、长期记忆、发言策略、限流和审核。

这样你就不需要懂 Kotlin 也能稳步替换原项目。

## 本次整理所依据的 WindWhisper 代码位置

关键文件：

- `src/main/kotlin/forum/Auth.kt`
- `src/main/kotlin/forum/Posts.kt`
- `src/main/kotlin/forum/Notification.kt`
- `src/main/kotlin/MainConfig.kt`
- `src/main/kotlin/Work.kt`
- `src/main/kotlin/ai/tools/Forum.kt`

## 在线验证结果

本次对 forum.rdfzer.com 的直接探测结果：

- `GET /about.json` 返回 `200`。
- `GET /session/csrf` 返回 `200`，且响应中有 `csrf` 字段。
- `GET /t/117.json?track_visit=true&forceLoad=true` 返回 `200`，并确认 `post_stream.posts[0].reactions` 存在。
- `GET /notifications?limit=1&recent=false&bump_last_seen_reviewable=true` 未登录返回 `403`。

这说明你可以放心先按 Discourse 客户端方式重构。