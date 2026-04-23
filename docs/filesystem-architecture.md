# JellyfishBot 文件系统架构

> 本文档完整描述 JellyfishBot 在 **Local 模式** 下的物理目录结构、各文件
> 的 schema、读写路径，以及 Admin / Service / WeChat / Scheduler / Inbox
> 五种执行域之间的消息流。S3 模式只换底层后端，Schema 与逻辑路径都不变。
>
> 所有路径都相对项目根（PowerShell 中 `$PWD`）描述，绝对路径示例统一用
> `D:\semi-deep-agent\` 演示。代码引用：`app/...` 是 Python 包路径。

---

## 0. TL;DR — 三个核心维度

| 维度 | 含义 | 物理实体 |
|------|------|----------|
| **执行域**（who） | Admin / Service Consumer / WeChat / Scheduler / Inbox 五种 agent 触发主体 | 各自独立的 agent factory + 独立的对话目录 |
| **数据域**（what） | 用户数据、Service 配置、对话记录、生成物、附件、Soul、定时任务 | `users/{uid}/...` 下严格分层 |
| **后端**（where） | Local 文件系统 vs S3 兼容对象存储 | `STORAGE_BACKEND=local\|s3` |

整个项目 **所有持久化数据都在 `users/` 之下**（除全局配置 `config/registration_keys.json`、checkpoint DB、加密 master key 之外），删一个用户目录 = 彻底注销该用户所有产物。

---

## 1. 项目根目录全景

```
D:\semi-deep-agent\
├── app/                          ← FastAPI 后端源码
│   ├── core/                     ← settings / security / encryption / api_config / path_security
│   ├── storage/                  ← 抽象存储层（local + s3 + s3_backend）
│   ├── routes/                   ← REST 路由（auth, chat, conversations, files, services,
│   │                                 consumer, scheduler, settings_routes, batch, scripts, models, inbox, ...）
│   ├── schemas/                  ← Pydantic 请求/响应模型
│   ├── services/                 ← 业务层（agent / consumer_agent / scheduler / inbox /
│   │                                 published / conversations / tools / memory_tools / ...）
│   ├── channels/wechat/          ← WeChat iLink 协议层（client / bridge / admin_bridge /
│   │                                 router / admin_router / session_manager / delivery / media /
│   │                                 rate_limiter）
│   ├── voice/                    ← S2S WebSocket 代理
│   └── main.py                   ← FastAPI 装配
├── frontend/                     ← React 19 + Vite + AntD 5
│   ├── src/                      ← TypeScript 源码（pages, components, stores, ...）
│   ├── public/                   ← Vite 静态资源（含 wechat-scan.html 占位模板）
│   ├── dist/                     ← `npm run build` 产物（生产用，含 service-chat.html）
│   └── server.js                 ← 生产环境 Express 静态 + 反向代理
├── data/                         ← 进程/全局运行时数据（非用户）
│   ├── checkpoints.db            ← AsyncSqliteSaver（LangGraph thread state，WAL 模式）
│   └── encryption.key            ← AES-256-GCM master key（保护 api_keys.json）
├── config/
│   └── registration_keys.json    ← 全局注册码池（超管在 Tauri 启动器中管理）
├── users/                        ← ★ 所有用户数据在此 ★
│   ├── users.json                ← 全局账号索引（uid → username/password_hash/token/reg_key）
│   └── {user_id}/                ← 每个 Admin 一个目录（uid 是 8 位 uuid hex）
├── docs/                         ← 项目文档（本文件、wechat-integration-guide.md 等）
├── wechat-bot/                   ← 早期独立的 iLink PoC bot（已迁入 app/channels/wechat/）
├── tauri-launcher/               ← 桌面启动器源码（生成 .dmg / .exe）
├── launcher.py                   ← 跨平台 Python 启动器（端口检测 + 子进程管理 + 日志 tee）
├── venv/                         ← 项目主 Python 虚拟环境（**不要混淆** 用户的 venv）
└── .env / .cursorrules / requirements.txt / start_local.{sh,bat} / Dockerfile
```

> 注意：`venv/`（项目级）和 `users/{uid}/venv/`（每个 Admin 私有的脚本执行
> 环境）是**两套独立**的 Python 环境，互不影响。

---

## 2. 单个 Admin 用户目录详解

每个 Admin 注册成功后，`_create_user_dirs(user_id)`（`app/core/security.py`）
+ `storage.ensure_user_dirs(user_id)`（`app/storage/local.py`）+ `ensure_soul_dir(user_id)`
（`app/services/memory_tools.py`）会构造下述目录骨架，并写入示例
`docs/README.md` 与 `scripts/hello.py`：

```
users/{user_id}/
├── filesystem/                   ← Admin 主文件系统（agent 可见的「项目根」）
│   ├── docs/                     ← 文档库（默认 README.md；Service consumer 只读共享）
│   │   └── README.md
│   ├── scripts/                  ← Python 脚本（执行受沙箱限制）
│   │   └── hello.py              ← 注册时自动写入的示例
│   ├── generated/                ← AI 生成物 (admin 侧)
│   │   ├── images/               ← generate_image 的输出
│   │   ├── audio/                ← generate_speech 的输出
│   │   └── videos/               ← generate_video 的输出
│   └── soul/                     ← Memory Subagent 可读写的笔记/人格文件（按需创建）
│
├── conversations/                ← Admin 自己的聊天历史
│   ├── {conv_id}.json            ← ★ 单个对话主体（schema 见 §3.1）★
│   └── {conv_id}/                ← 同名目录（**与 .json 文件并列**）
│       └── query_appendix/       ← 用户上传的附件（图片/文件）
│           └── images/
│               └── wx_xxxxxxxx.jpg
│
├── services/                     ← Admin 发布的所有 Service
│   └── {service_id}/             ← 单个 Service 的完整空间（详见 §4）
│
├── tasks/                        ← Admin 定时任务
│   └── task_{8hex}.json          ← 任务 + runs[] 步骤日志（schema 见 §6.1）
│
├── inbox/                        ← Admin 收件箱（来自 Service Agent 的通知）
│   └── inbox_{8hex}.json         ← 单条收件箱消息（schema 见 §7.1）
│
├── soul/                         ← Soul 应用层配置（agent 不可访问）
│   └── config.json               ← {memory_enabled, memory_subagent_enabled,
│                                       soul_edit_enabled, max_recent_messages, ...}
│
├── voice_transcripts/            ← S2S 语音通话的转录（按需创建）
│
├── venv/                         ← 用户私有 Python 虚拟环境（venv_manager 管理）
│   ├── Scripts/python.exe        ← Windows
│   ├── bin/python                ← Unix
│   └── requirements.txt          ← 用户已安装的包列表（启动时自动还原）
│
├── api_keys.json                 ← AES-256-GCM 加密的用户级 API 密钥
├── subagents.json                ← 用户自定义 Subagent 列表
├── admin_wechat_session.json     ← Admin 自接入微信的持久化会话（详见 §10.4）
├── capability_prompts.json       ← 用户对 CAPABILITY_PROMPTS 的覆写（仅存差异）
├── system_prompts/               ← 用户主 system prompt 的版本历史（按需创建）
└── user_profile.json             ← 用户画像（投资偏好/自定义备注）
```

### 关键不变量

1. **`conversations/{conv_id}.json` 与 `conversations/{conv_id}/` 同时存在**：
   前者放对话消息，后者放该对话的附件（query_appendix）。**绝对不要**把消息
   塞进同名目录里，`save_message` 永远写 `.json` 文件。
2. **`filesystem/` 的根才是 deepagents 看到的根**——agent 通过 `read_file("/docs/foo")`
   访问的实际路径是 `users/{uid}/filesystem/docs/foo`。
3. **`soul/` 出现在两个位置**：
   - `users/{uid}/soul/config.json` —— 应用层开关，**agent 不可见**
   - `users/{uid}/filesystem/soul/` —— 笔记/人格内容，**agent 可读写**
   早期版本曾用 symlink 把 `filesystem/soul → ../soul` 指过去，但
   `Path.resolve()` 跟随符号链接破坏路径逃逸检查，已废弃。`sync_soul_symlink()`
   现在只负责删除旧 symlink 并把内容迁到 `filesystem/soul/`。

---

## 3. 聊天记录 Schema

### 3.1 Admin 对话 — `users/{uid}/conversations/{conv_id}.json`

```jsonc
{
  "id": "ef485e98",                      // 8 位 uuid hex
  "title": "如何创建和管理 Subagent...",  // 自动取首条 user 消息前 30 字
  "created_at": "2026-04-02T17:49:25.684348",
  "updated_at": "2026-04-02T17:50:01.386854",
  "messages": [
    {
      "role": "user",
      "content": "如何创建和管理 Subagent？...",
      "timestamp": "2026-04-02T17:49:25.697347",
      "attachments": [                   // 可选：query_appendix/ 中的附件
        {"type": "image", "filename": "wx_abc123.jpg", "path": "images/wx_abc123.jpg"}
      ]
    },
    {
      "role": "assistant",
      "content": "根据我的系统配置，我来详细介绍...",
      "timestamp": "2026-04-02T17:50:01.386854",
      "tool_calls": [...],               // 可选：兼容旧格式
      "blocks": [                        // ★ 新格式：交错渲染序列 ★
        {"type": "thinking", "content": "..."},
        {"type": "text", "content": "根据我的..."},
        {"type": "tool", "name": "read_file", "args": "...", "result": "...", "done": true},
        {"type": "subagent", "name": "general-purpose",
         "task": "...", "status": "done",
         "timeline": [{"kind": "text", "content": "..."}, ...], "done": true}
      ]
    }
  ]
}
```

**写入路径**：所有 SSE 流（`chat.py` / `consumer.py` / WeChat bridge）
都通过 `app.services.conversations.save_message()` 原子写入
（`atomic_json_save` → tmp + `os.replace`）。

**`blocks` 字段** 是 2026-04 引入的统一交错渲染格式，前端 `MessageBubble`
检测到 `blocks` 就走 `BlocksRenderer`，否则 fallback 旧格式
（`tool_calls` 在上、`content` 在下）。

### 3.2 Consumer 对话 — `users/{admin}/services/{svc}/conversations/{conv_id}/messages.json`

```jsonc
{
  "id": "017f99af00",                    // 10 位 uuid hex
  "title": "微信用户",                   // WeChat 默认；web 取首条 user 消息前 30 字
  "created_at": "2026-04-02T10:08:10.514515",
  "updated_at": "2026-04-02T10:08:20.032342",
  "messages": [ /* 同 admin 格式，含 blocks/attachments/tool_calls */ ]
}
```

**目录结构**（每个 conversation 是个目录而非单文件，因为要带媒体）：

```
conversations/{conv_id}/
├── messages.json                  ← 上述 schema
├── generated/                     ← Consumer 侧 AI 生成物（与会话绑定）
│   ├── images/  audio/  videos/
└── query_appendix/                ← 用户上传的附件（同 admin）
    └── images/
```

> Consumer 的 `query_appendix/` 与 admin 不同：admin 是 `conversations/{cid}/query_appendix/`
> （JSON 同级），consumer 是 `conversations/{cid}/query_appendix/`（与 messages.json 同级）。
> 都通过 `save_consumer_attachment()` / `get_consumer_attachment_dir()` 访问。

### 3.3 标题自动生成

`save_message` / `save_consumer_message` 在第一次写入 user 消息时，若标题
仍为「新对话」/ 空，会取 content 前 30 字 + `...` 作为标题，避免列表全是「新对话」。

---

## 4. Service 目录详解

```
users/{admin_id}/services/{service_id}/
├── config.json                    ← Service 配置（schema 见 §4.1）
├── keys.json                      ← API 密钥列表（sha256 哈希存储）
├── wechat_sessions.json           ← 该 Service 的 WeChat 会话快照（持久化）
├── tasks/                         ← Service 级定时任务（agent only）
│   └── stask_{8hex}.json          ← 任务 + runs[]
└── conversations/                 ← Consumer 对话集合
    └── {conv_id}/                 ← 单个 consumer 对话（10 位 hex）
        ├── messages.json
        ├── generated/{images,audio,videos}/
        └── query_appendix/images/
```

### 4.1 `config.json` schema

```jsonc
{
  "id": "svc_3d10bfff",
  "admin_id": "f14af5eb",
  "name": "shinan",
  "description": "",
  "model": "anthropic:claude-opus-4-6-thinking",
  "system_prompt_version_id": null,        // 可选：指向 admin 的 prompt 历史版本
  "user_profile_version_id": null,         // 可选：注入用户画像版本
  "allowed_docs": ["*"],                   // 路径白名单或 ["*"] 全允许
  "allowed_scripts": ["*"],                // 同上；空数组 = 禁脚本
  "capabilities": ["web", "scheduler", "image", "speech", "video"],
                                           // 可选 humanchat（启微信时自动加）
  "research_tools": false,
  "published": true,
  "max_conversations": 1000,
  "welcome_message": "...",                // 可选：consumer 聊天首屏欢迎语
  "quick_questions": ["问题A", "问题B"],   // 可选：首屏快速问题 chips
  "wechat_channel": {                      // 可选：微信渠道配置
    "enabled": true,
    "expires_at": "2026-04-18T02:35:00.000Z",
    "max_sessions": 100,
    "updated_at": "2026-04-09T16:03:29.023893"
  },
  "created_at": "2026-04-02T10:07:58.053255",
  "updated_at": "2026-04-09T16:03:29.023893"
}
```

### 4.2 `keys.json` schema

```jsonc
{
  "keys": [
    {
      "id": "key_a2a212",                  // 内部 ID
      "prefix": "sk-svc-0890d",            // 展示前缀（脱敏）
      "key_hash": "sha256:0030490f...",    // 真实 key 永不存储
      "name": "default",
      "created_at": "2026-04-02T10:08:03.306049",
      "last_used_at": "2026-04-02T10:08:12.419107"
    }
  ]
}
```

> Consumer 访问 `/api/v1/*` 时带 `Authorization: Bearer sk-svc-...`，
> `verify_service_key()` 会**遍历所有用户的所有 services**比对哈希。这在
> 当前规模（admin ≤ 20）足够，未来可换成全局索引。

---

## 5. 用户管理与全局账号

### 5.1 `users/users.json` — 账号索引

```jsonc
{
  "f14af5eb": {
    "username": "shinan",
    "password_hash": "$2b$12$...",          // bcrypt（fallback: sha256:salt:hash）
    "token": "f01402f603...48 hex",         // 64 字符 hex，登录时 rotate
    "created_at": "2026-03-23T14:58:46.041792",
    "reg_key": "DA-160BA226-B406C04F",
    "last_login": "2026-04-13T13:51:26.739196"
  }
}
```

### 5.2 `config/registration_keys.json` — 注册码池（全局）

```jsonc
{
  "keys": [
    {"key": "JFBOT-XXXX-XXXX-XXXX",
     "used": true, "used_by": "shinan",
     "used_at": "2026-03-23T14:58:46.041792"}
  ]
}
```

由 Tauri 启动器（超管功能）或手工编辑生成；`register()` 只接受未使用的码。

### 5.3 `users/{uid}/api_keys.json` — 加密的 LLM/外部 Key

加密器：`app/core/encryption.py` 的 `encrypt()` / `decrypt()`，AES-256-GCM，
master key 在 `data/encryption.key`（自动生成；可被 `ENCRYPTION_KEY` 环境变量覆盖）。

字段全集（密文）：

| Provider | API Key 字段 | Base URL 字段 |
|----------|--------------|---------------|
| OpenAI | `openai_api_key` | `openai_base_url` |
| Anthropic | `anthropic_api_key` | `anthropic_base_url` |
| Tavily | `tavily_api_key` | — |
| CloudsWay | `cloudsway_search_key` | — |
| Image | `image_api_key` | `image_base_url` |
| TTS | `tts_api_key` | `tts_base_url` |
| Video | `video_api_key` | `video_base_url` |
| S2S（Realtime） | `s2s_api_key` | `s2s_base_url` |
| STT（Whisper） | `stt_api_key` | `stt_base_url` |

优先级链：**用户配置 > 环境变量 > 未配置**。任何 `agent.py` /
`ai_tools.py` / `web_tools.py` 调用 `get_api_config(cap, user_id=...)`
都会自动走这条链，更新 key 后 `clear_agent_cache(user_id)` +
`clear_consumer_cache(admin_id=user_id)` 强制重建 agent。

### 5.4 其它每用户配置文件

| 文件 | 作用 |
|------|------|
| `subagents.json` | 用户自定义 Subagent（默认 `[]`） |
| `capability_prompts.json` | 覆盖 `CAPABILITY_PROMPTS` 的差异项（不存默认） |
| `user_profile.json` | 投资画像 + 自定义画像备注（可被 Service 引用版本） |
| `soul/config.json` | Memory & Soul 开关 |

---

## 6. 定时任务（Scheduler）

### 6.1 Admin 任务 — `users/{uid}/tasks/task_*.json`

```jsonc
{
  "id": "task_e2a9a525",
  "user_id": "f14af5eb",
  "name": "每日问候",
  "description": "每日向用户问候",
  "schedule_type": "once",                  // once | cron | interval
  "schedule": "2026-04-01T09:00:00",        // ISO 字符串 / cron 表达式 / 秒数
  "task_type": "script",                    // script | agent
  "task_config": {
    // === script 类型 ===
    "script_path": "hello.py",
    "script_args": [],
    "input_data": null,
    "timeout": 60,
    "permissions": {
      "read_dirs": ["*"],                   // 相对 filesystem/ 根；"*" = 全允许
      "write_dirs": ["*"]
    }
    // === agent 类型 ===
    // "prompt": "...",
    // "doc_path": ["docs/skill.md"],       // 可选：注入文档作为 system 上下文
    // "model": "anthropic:claude-...",
    // "capabilities": ["web", "image"],
    // "permissions": { "read_dirs": [...], "write_dirs": [...] }
  },
  "reply_to": null,                         // 见 §8.2
  "enabled": true,
  "tz_offset_hours": 8,                     // 关键：cron/once 都按这个时区解释
  "created_at": "2026-04-02T02:09:59.324904+00:00",
  "last_run_at": "2026-04-02T04:04:27.421731+00:00",
  "next_run_at": null,
  "runs": [                                 // 最近 20 次运行记录
    {
      "run_id": "run_838cfc",
      "started_at": "...", "finished_at": "...",
      "status": "success",                  // success | error | timeout
      "output": "Hello, World!\n\n退出码: 0",
      "steps": [                            // 详细步骤日志（前端可视化用）
        {"type": "start", "content": "启动脚本: hello.py", "ts": "...",
         "args": [], "read_dirs": ["*"], "write_dirs": ["*"],
         "resolved_write_dirs": ["...", "..."],
         "scripts_dir": "...", "fs_dir": "..."},
        {"type": "stdout", "content": "Hello, World!", "ts": "..."},
        {"type": "exit", "content": "退出码: 0", "exit_code": 0, "ts": "..."}
        // 其它 type: stderr | error | loop | tool_call | tool_result |
        //          ai_message | docs_loaded | auto_approve | wechat_connected |
        //          wechat_warning | wechat_send | wechat_error | finish
      ]
    }
  ]
}
```

### 6.2 Service 任务 — `users/{admin}/services/{svc}/tasks/stask_*.json`

与 Admin 任务结构基本相同，但：

| 区别 | Admin Task | Service Task |
|------|------------|--------------|
| 文件前缀 | `task_` | `stask_` |
| 顶层字段 | `user_id` | `admin_id` + `service_id` |
| `task_type` | `script` 或 `agent` | 仅 `agent`（无脚本） |
| Agent 工厂 | `create_user_agent` | `create_consumer_agent(channel="scheduler")` |
| 工具栏 | 完整 admin 工具集 | Consumer 工具集（受 `allowed_docs/scripts` 过滤） |
| 权限隔离 | 整个 user filesystem | 该 Service 的 conversation 范围 |

### 6.3 调度循环

`TaskScheduler` 单例（`app/services/scheduler.py`）：

```
main.py startup
  └─→ TaskScheduler.start()
        └─→ asyncio loop（每 30 秒）
              ├─ scan users/*/tasks/*.json          (admin)
              ├─ scan users/*/services/*/tasks/*.json (service)
              ├─ if next_run_at <= now → asyncio.create_task(_execute_task(...))
              └─ 单任务超时 _TASK_TIMEOUT_S = 300s
```

`_compute_next_run` 按 `tz_offset_hours` 处理：cron 把 UTC now 转为
用户本地时间送 croniter，结果再转回 UTC；once 自动补时区后缀。

### 6.4 沙箱权限解析

`_resolve_permission_dirs(user_id, dir_names)`：

| 输入 | 解析为 |
|------|--------|
| `"*"` | `users/{uid}/filesystem/`（整根） |
| `"docs"` | `users/{uid}/filesystem/docs/`（自动 makedirs） |
| `"data/output"` | `users/{uid}/filesystem/data/output/` |
| 空字符串 | 跳过 |

`scripts_dir` **永远** 强制写入 `read_dirs` + `write_dirs`（脚本 cwd 必须可读写）。

---

## 7. Inbox（管理员收件箱）

> 来自 Service Agent 的反馈通道。Service Agent 调用 `contact_admin` 工具
> → `post_to_inbox()` → 落盘 + 触发 inbox agent → 评估后转发到 admin 微信。

### 7.1 `users/{admin}/inbox/inbox_{8hex}.json`

```jsonc
{
  "id": "inbox_a3f12b88",
  "service_id": "svc_3d10bfff",
  "service_name": "shinan",
  "conversation_id": "017f99af00",
  "wechat_session_id": "ws_b826a2ba",       // 可选：来源 WeChat 会话
  "wechat_user_id": "o9cq80-_4ac2...",      // 可选：来源微信用户
  "message": "用户反馈：这个产品的退款政策是什么？我找不到相关说明。",
  "timestamp": "2026-04-15T14:23:09.123456+00:00",
  "status": "handled",                      // unread | read | handled
  "handled_by": "agent",                    // agent | manual | null
  "agent_response": "已通知管理员..."       // inbox agent 的输出（可选）
}
```

### 7.2 Inbox Agent 触发逻辑（`_trigger_inbox_agent`）

```
post_to_inbox()
  ├─ 写 inbox_*.json (status=unread)
  ├─ 检查 admin 当前是否有微信连接（_get_admin_wechat_session）
  └─ 若有 → asyncio 调度 _trigger_inbox_agent(...)
        ├─ 注入最近 3 条 inbox 历史 + 当前消息
        ├─ thread_id = f"inbox-{admin_id}"  ← 稳定 ID，跨次累积上下文
        ├─ 启动 admin agent（capabilities=[humanchat]）
        ├─ Agent 自主判断重要性，决定是否调用 send_message
        └─ 完成后更新 inbox 文件: status=handled, handled_by=agent
```

### 7.3 sync tool → asyncio 桥接（关键修复）

`contact_admin` 是 sync 工具，LangChain 通过 `BaseTool._arun` →
`run_in_executor(None, self._run)` 在线程池执行。线程池里
`asyncio.get_running_loop()` 失败，因此 `inbox.py` 必须缓存主事件循环：

```python
# main.py startup 时调用一次
inbox.set_main_loop(asyncio.get_running_loop())

# post_to_inbox() 内部
try:
    loop = asyncio.get_running_loop()         # async 上下文优先
    loop.create_task(coro)
except RuntimeError:
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _main_loop)  # sync 上下文兜底
```

---

## 8. 消息互发实现（Scheduler ↔ Service ↔ Admin ↔ User）

JellyfishBot 内部存在 **6 类消息流**，`reply_to` 字段统一描述目的地：

```
reply_to = {
  "channel": "wechat" | "web",
  "admin_id": "f14af5eb",
  "service_id": "svc_3d10bfff" | null,      // null 表示 admin 主体
  "session_id": "ws_b826a2ba",              // WeChat session
  "conversation_id": "017f99af00"           // 落盘对话 ID
}
```

### 8.1 六类消息流总表

| # | 触发方 | 接收方 | 入口 | 出口 | 落盘位置 |
|---|--------|--------|------|------|----------|
| 1 | 用户 (Web) | Admin Agent | `POST /api/chat` | SSE 流回浏览器 | `users/{uid}/conversations/{cid}.json` |
| 2 | 用户 (WeChat) | Admin Agent | iLink polling | iLink sendmessage | 同上 |
| 3 | 用户 (Web) | Consumer Agent | `POST /api/v1/chat` | SSE 流 | `users/{adm}/services/{svc}/conversations/{cid}/messages.json` |
| 4 | 用户 (WeChat) | Consumer Agent | iLink polling | iLink sendmessage | 同上 |
| 5 | Scheduler | 用户 (WeChat) | `_execute_task` | 通过 `_resolve_wechat_client` | 写入对应 conversation 历史 |
| 6 | Service Agent | Admin (Inbox→WeChat) | `contact_admin` 工具 | 经 inbox agent 转发 | `users/{adm}/inbox/inbox_*.json` |

### 8.2 `reply_to` → WeChat client 的解析（`_resolve_wechat_client`）

```python
if reply_to.channel != "wechat":
    return (None, None, None)

if reply_to.service_id:
    # === Service 渠道 ===
    mgr = get_session_manager()              # 内存中的全局 session_manager
    session = mgr.get_session(reply_to.session_id)
    client  = mgr.get_client(session.session_id)
    return (client, session.from_user_id, session.context_token)
else:
    # === Admin 自接入渠道 ===
    admin_sess = _get_admin_session(reply_to.admin_id)   # admin_router._admin_sessions
    return (admin_sess["client"], admin_sess["from_user_id"], admin_sess["context_token"])
```

### 8.3 send_message 工具拦截（消息流 5）

定时任务的 agent 输出 **用户看不到** —— 必须用 `send_message` 工具显式投递。
执行循环（`_run_agent_loop`）逐 token 流式扫描：

```
async for event in agent.astream(...):
  for msg in event[node].messages:
    if msg.type == "tool" and msg.name == "send_message":
        await _handle_send_message_tool(msg.content, client, to_user, ctx_token, ...)
```

`_handle_send_message_tool` 解析 `payload = {"text": ..., "media": ...}`：
- `media` → `_send_media_for_task` → 按 admin / service 分支读 `storage.read_bytes`
  或 `storage.read_consumer_bytes` → 按扩展名调 `client.send_image/video/voice/file`
- `text` → `client.send_text(to_user, text, ctx_token)`

每次投递追加 `wechat_send` step 到 run record，**便于事后审计**。
若 `to_user` 或 `ctx_token` 为空 → 写 `wechat_error` / `wechat_warning` step
而非静默吞掉。

### 8.4 `<<FILE:>>` 标签兼容（消息流 4 + 5 共享）

历史包袱：`tools.py::generate_image/speech/video` 工具返回字符串
`"请使用 <<FILE:/generated/images/xxx.png>> 展示给用户"`，system prompt 也教 agent
用此标签（web 端 markdown 解析契约）。Agent 写 WeChat 时常把它直接塞进
`send_message(text=...)` 而不用 `media_path` 参数，导致用户看到字面字符串。

修复（`delivery.py::extract_media_tags`）：

```
text 输入 → re.findall(r"<<FILE:([^>]+?)>>") → 媒体路径列表
        → 同时 sub("") + 折叠空行 → 干净文本
        → bridge / scheduler 各自把媒体作为额外消息投递
```

`deliver_tool_message` 同时支持显式 `media` 字段和内联 `<<FILE:>>`，
两端都自动获益。

### 8.5 Admin → Service 群发（`publish_service_task` 工具）

Admin Agent 调用 `publish_service_task(service_ids, prompt, schedule, session_ids)`
→ 在每个匹配 Service 的 `tasks/` 下创建 `stask_*.json`，`reply_to` 自动
按 `session_ids`（精确投递）或活跃 sessions（全员广播）填充。`run_now=true`
时立即调度（`_schedule_coro` 线程安全，先尝试 `get_running_loop()`，
失败回落 `run_coroutine_threadsafe`）。

### 8.6 短期记忆注入

| 调用方 | 注入内容 | 实现 |
|--------|----------|------|
| `_run_agent_task`（admin 定时） | 从 `conversations/{cid}.json` 读最近 N 条 | `load_recent_admin_messages` |
| `_run_service_agent_task`（service 定时） | 从 `services/{svc}/conversations/{cid}/messages.json` 读 | `load_recent_consumer_messages` |
| `_trigger_inbox_agent`（inbox） | 最近 3 条 inbox 历史 | `load_recent_inbox` |

`max_recent_messages` 可在 `users/{uid}/soul/config.json` 中调整（默认 5）。

### 8.7 消息流时序示例（Service 定时任务推送给微信用户）

```
00:00  cron 触发 → scheduler loop 取出 stask_xxx.json
00:01  _execute_service_task → _run_service_agent_task
00:01    ├─ 加载 doc_path 文档 → docs_loaded step
00:01    ├─ load_recent_consumer_messages → 注入最近 5 条
00:01    ├─ 拼前缀 "[系统指令 - 来自管理员]" + "[对话上下文]" + 任务指令
00:01    ├─ create_consumer_agent(channel="scheduler", extra_capabilities=[humanchat])
00:01    └─ _resolve_wechat_client(reply_to) → (client, to_user, ctx_token)
00:02  agent.astream() 开始 → 模型推理 → 调用 send_message
00:03    └─ _handle_send_message_tool 拦截
00:03         ├─ media 字段 → _send_media_for_task → CDN 上传 → iLink sendmessage
00:03         └─ text → client.send_text → iLink sendmessage
00:04  agent 完成 → save_consumer_message 写入 messages.json (source=admin_broadcast)
00:04  task.runs[] 追加 run record（含全部 steps）→ 写回 stask_xxx.json
```

---

## 9. WeChat 实现总览

JellyfishBot 集成腾讯 iLink Bot（`ilinkai.weixin.qq.com`），有 **两个独立的接入栈**：

```
┌──────────────────────────────────────────────────────┐
│ Service 渠道（多用户）                                 │
│   /wc/{service_id} 中间页 → /api/wc/* QR API         │
│   → ILinkClient → SessionManager → bridge.py         │
│   → consumer agent (channel="wechat")                │
│   → 写 services/{svc}/conversations/{cid}/           │
│                                                       │
│ Admin 自接入（单用户主 agent）                         │
│   /api/admin/wechat/* → ILinkClient → admin_bridge   │
│   → admin agent (capabilities + humanchat)           │
│   → 写 users/{uid}/conversations/{cid}.json          │
└──────────────────────────────────────────────────────┘
```

两栈共用同一份 `client.py`（iLink HTTP 协议）+ `media.py`（AES-128-ECB
解密）+ `delivery.py`（媒体投递解析器），但 session 管理、agent 工厂、
落盘路径完全分开。

### 9.1 Service 渠道完整链路

```
[访客手机]
   │ 浏览 https://your.host/wc/svc_3d10bfff （短链，可分发到名片/海报）
   ▼
[FastAPI: GET /wc/{service_id}]  app/routes/wechat_ui.py
   │ 1. _find_service_admin(service_id) 扫描 users/*/services/{sid}/config.json
   │ 2. 校验 svc.published + wechat_channel.enabled
   │ 3. 读 frontend/public/wechat-scan.html 模板，替换 {{SERVICE_ID}} 等
   │ 4. 返回 HTML（前端含 JS：轮询 /api/wc/{sid}/qrcode）
   ▼
[访客手机浏览器]
   │ JS 调 GET /api/wc/{service_id}/qrcode
   ▼
[FastAPI: app/channels/wechat/router.py]
   ├─ rate_limiter.check_qr_rate(service_id)        ← 5次/60s
   ├─ ILinkClient.generate_qrcode()                 ← 调 iLink API
   │   返回 { qr_id, qr_image_png (PNG bytes), qr_url }
   └─ Response: { qr_id, qr_image_b64, qr_url }
   ▼
[访客手机]
   │ 显示 QR → 用户用微信扫码
   │ JS 同时轮询 GET /api/wc/{sid}/qrcode/status?qrcode={qr_id}
   ▼
[FastAPI: api_qrcode_status]
   ├─ poll_qrcode_status(qr_id)
   │   返回 { status: "waiting"|"confirmed", bot_token, ilink_user_id, ... }
   └─ 若 confirmed:
       ├─ session_manager.create_session(...)       ← 内存 + 持久化
       │     ├─ 创建 ILinkClient
       │     ├─ create_consumer_conversation(...)   ← 写 conversations/{cid}/messages.json
       │     ├─ _save_sessions(...)                 ← 写 services/{sid}/wechat_sessions.json
       │     └─ start_polling(session_id)           ← 启动后台 asyncio task
       └─ 返回 { status:"confirmed", session_id, conversation_id }
   ▼
[后台 polling task: session_manager._poll_loop]
   loop {
     msgs = await client.get_updates()              ← iLink 长轮询
     for msg in msgs:
        await message_handler(session, msg)         ← bridge.handle_wechat_message
     dynamic interval (2-30s based on activity)
   }
```

### 9.2 中间页 vs SPA 入口对比

| 路径 | 文件 | 作用 |
|------|------|------|
| `/s/{service_id}` | `frontend/dist/service-chat.html`（多 entry vite 产物） | Consumer Web 聊天 SPA（React） |
| `/wc/{service_id}` | `frontend/public/wechat-scan.html`（静态模板，占位符替换） | 微信扫码中间页（不是 SPA） |
| `/api/v1/*` | — | Consumer 数据 API（带 `sk-svc-` key） |
| `/api/wc/*` | — | WeChat 渠道控制 API（部分 admin 鉴权） |
| `/api/admin/wechat/*` | — | Admin 自接入渠道 |

**注意 `/s/{id}?key=sk-svc-...` 自助化**：consumer_ui.py 注入
`window.__SVC__`，前端读 `URLSearchParams.key` → 写 localStorage →
`history.replaceState` 立即清掉 query。等同分享 Key（仍残留在 referer/网关日志），
admin Key Modal 会同时显示警告文案。

### 9.3 API 路由分发

```
app/main.py
├── auth_router            /api/auth/*
├── conversations_router   /api/conversations/*       (admin)
├── chat_router            /api/chat                  (admin SSE)
├── files_router           /api/files/*               (admin filesystem CRUD + media)
├── scripts_router         /api/scripts/*             (run_script REST)
├── services_router        /api/services/*            (admin Service CRUD)
├── consumer_router        /api/v1/*                  (consumer chat + conv + files)
├── consumer_ui_router     /s/{service_id}            (SPA 入口)
├── wechat_router          /api/wc/*                  (Service 渠道)
├── wechat_ui_router       /wc/{service_id}           (扫码中间页)
├── admin_wechat_router    /api/admin/wechat/*        (Admin 自接入)
├── scheduler_router       /api/scheduler/*           (admin + service tasks)
├── inbox_router           /api/inbox/*               (admin inbox CRUD)
├── settings_router        /api/settings/*            (api_keys, capability_prompts, soul, ...)
├── batch_router           /api/batch                 (Excel 批量)
├── models_router          /api/models                (LLM 列表)
└── voice_router           /ws/voice                  (S2S WebSocket)
```

### 9.4 Service 渠道 session 持久化

`users/{admin}/services/{svc}/wechat_sessions.json`：

```jsonc
{
  "sessions": [
    {
      "session_id": "ws_b826a2ba",
      "service_id": "svc_3d10bfff",
      "admin_id": "f14af5eb",
      "conversation_id": "51812cb083",     // ← 与 consumer conversations 目录绑定
      "bot_token": "9fe251d47e99@im.bot:060000dd...",
      "ilink_user_id": "o9cq80...@im.wechat",
      "ilink_bot_id": "9fe251d47e99@im.bot",
      "base_url": "https://ilinkai.weixin.qq.com",
      "from_user_id": "o9cq80...@im.wechat",  // 真正的微信用户 OpenID（首次发消息后填入）
      "context_token": "AARzJWAFAAABAAAA...",  // ★ 每次收到消息都更新 ★
      "updates_buf": "ChAIBRD53e...",          // 长轮询游标
      "created_at": "2026-04-02T10:36:05.705825",
      "last_active_at": "2026-04-02T12:03:19.146821"
    }
  ]
}
```

**生命周期**：
- 创建：`create_session()` 入磁盘
- 每次 `from_user_id` / `context_token` 更新时同步落盘
- `start_all_polling()` 在 main.py startup 中扫描所有 `services/*/wechat_sessions.json`
  恢复未关闭的 session（重启不丢失对话）
- 24h 无活动清理：**仅** `from_user_id` 为空的会话被回收（已建立用户的会话长期保留）
- 连续错误 20 次或主动断开 → `remove_session()` 删条目 + 删客户端 + 删持久化
- 50 次连续空轮询 + 无用户 → 视为废弃，自动 remove

### 9.5 Admin 自接入 session 持久化

`users/{user_id}/admin_wechat_session.json`：

```jsonc
{
  "user_id": "f14af5eb",
  "conversation_id": "ef485e98",            // ← 与 admin conversations 绑定（普通对话共存）
  "bot_token": "...",
  "ilink_user_id": "...",
  "ilink_bot_id": "...",
  "base_url": "https://ilinkai.weixin.qq.com",
  "connected": true,
  "connected_at": "...",
  "context_token": "...",                   // 同样需要每次更新落盘
  "from_user_id": "..."
}
```

**与 Service 渠道的区别**：
- 全局只允许 1 个活跃 admin session（重新扫码报 409，需先断开）
- 走 `users/{uid}/conversations/{cid}.json`（与 web 聊天共用对话目录）
- 由 `app/channels/wechat/admin_bridge.py` 处理，注入完整 admin 工具集
- 微信用户发的消息会走 admin agent —— **管理员相当于把自己的 agent 接到了微信**

### 9.6 媒体收发的存储路径一致性（关键设计）

| 步骤 | 相对路径 | 存储 API | 物理位置 |
|------|----------|----------|----------|
| 用户发图：bridge `_download_images` 解密后 | `images/wx_xxxxxxxx.jpg` | `save_consumer_attachment` | `services/{svc}/conversations/{cid}/query_appendix/images/...` |
| Agent 调用 `generate_image` | `images/xxx.png` | `_consumer_write` → `write_consumer_bytes` | `services/{svc}/conversations/{cid}/generated/images/...` |
| Bridge 发图：`send_media_to_wechat` | `images/xxx.png` | `read_consumer_bytes` | 同上 |

`<<FILE:/generated/images/xxx.png>>` 在投递前自动剥 `generated/` 前缀，
最终始终回到统一的 consumer 存储接口。

**Admin 自接入的媒体路径不同**：

| 步骤 | 路径 | API | 物理位置 |
|------|------|-----|----------|
| Admin 接收用户发图 | `images/wx_xxxxxxxx.jpg` | `save_attachment` | `users/{uid}/conversations/{cid}/query_appendix/images/...` |
| Admin agent 生成图 | `/generated/images/xxx.png` | `storage.write_bytes` | `users/{uid}/filesystem/generated/images/...` |
| Admin bridge 发图 | `/generated/images/xxx.png` | `storage.read_bytes` | 同上 |

### 9.7 接收路径的协议细节

iLink 的 inbound 媒体 **没有 `cdn_url`**，下载逻辑与发送完全不对称：

```
inbound image_item
├── image_item.aeskey          (hex 直连 32 字符) | OR
├── image_item.aes_key         (base64 编码) | OR
└── image_item.media.aes_key   (base64 编码)
└── image_item.media.encrypt_query_param   ← 必须 URL encode 后传给 CDN

GET https://novac2c.cdn.weixin.qq.com/c2c/download
    ?encrypted_query_param={url_encode(encrypt_query_param)}
    ↓
返回 AES-128-ECB 加密的密文 → media.b64_to_key() → 解密 → 原始 bytes
```

**语音特殊**：微信用 SILK 格式（变体首字节 `0x02`），需 `pysilk` 解码
为 PCM → 包 WAV → Whisper 转文字。SILK 反向编码发送至今未在 WeChat
客户端成功播放（编码方向静音），所以 TTS 输出统一作为 `.mp3` **文件附件** 发送。

### 9.8 限流（`rate_limiter.py`）

| 范围 | 限制 |
|------|------|
| 单用户消息 | 10 条 / 60s |
| QR 生成 | 5 次 / 60s（per service / per admin） |
| 全局 session | `service.wechat_channel.max_sessions`（默认 100） |

---

## 10. 存储后端切换（Local ↔ S3）

### 10.1 工厂函数

```python
# app/storage/__init__.py
get_storage_service()                # → LocalStorageService | S3StorageService
create_agent_backend(root, user_id)  # deepagents BackendProtocol（admin filesystem）
create_consumer_backend(admin, svc, conv, gen_dir)   # consumer generated/
```

`STORAGE_BACKEND=local`（默认）/`s3` 切换。**业务代码绝对不要 `is_s3_mode()`**，
所有差异藏在 storage 包内部。

### 10.2 S3 键映射

| 用途 | S3 Key |
|------|--------|
| Admin filesystem | `{prefix}/{user_id}/fs/{path}` |
| Consumer generated | `{prefix}/{admin_id}/svc/{service_id}/{conv_id}/gen/{path}` |
| 媒体访问 | 302 → presigned URL（`_get_media_url` / `_get_consumer_media_url`） |
| 脚本执行 | 临时下载到 `tempfile.mkdtemp()` 执行，结果上传回 S3 |

**JSON 配置类文件**（users.json / config.json / messages.json / task.json /
inbox.json / soul/config.json / wechat_sessions.json / api_keys.json）
**当前仍写本地磁盘**——只有 deepagents 看到的 filesystem 内容才走 S3 后端。
这是设计折衷：避免 S3 模式下每次读 task list 都要 LIST + GET。

### 10.3 脚本执行的 S3 适配

```
Local: 直接 cwd = users/{uid}/filesystem/scripts/
S3:    storage.script_execution(user_id, script_path) ContextManager
       ├─ download script → tmp_scripts_dir/<name>.py
       ├─ yield {scripts_dir, docs_dir, write_dirs}   ← 全部是 tmpfs 路径
       └─ 退出时遍历 tmp_write_dirs/* → 上传到 S3
```

Consumer 脚本同理，但 `write_dirs` 上传到 consumer key prefix 而非 admin。

---

## 11. 权限矩阵汇总

### 11.1 Admin Agent

| 资源 | 读 | 写 | 备注 |
|------|----|----|------|
| `filesystem/docs/` | ✅ | ✅（HITL 审批） | 文档 |
| `filesystem/scripts/` | ✅ | ✅（HITL 审批） | 脚本 |
| `filesystem/generated/` | ✅ | ✅（AI 工具直写） | 生成物 |
| `filesystem/soul/` | ✅ | ✅（仅 `soul_edit_enabled` 时） | 笔记 |
| `users/*` 其它 admin 数据 | ❌ | ❌ | 跨用户隔离 |

### 11.2 Consumer Agent

| 资源 | 读 | 写 | 备注 |
|------|----|----|------|
| Admin `docs/` | ✅（受 `allowed_docs` 过滤） | ❌ | 只读共享 |
| Admin `scripts/` | ✅（受 `allowed_scripts` 过滤，仅执行） | ❌ | 不能改源码 |
| Admin `generated/` | ❌ | ❌ | 完全隔离 |
| Service `conversations/{cid}/generated/` | ✅ | ✅ | Consumer 唯一可写区域 |
| Service `conversations/{cid}/query_appendix/` | ✅（通过 attachments 字段） | 间接 | bridge 写入 |

### 11.3 各 channel 的 send_message 注入

| channel | 注入 send_message? | 文本 fallback | 媒体投递 |
|---------|--------------------|----------------|----------|
| `web` | ❌（直接 SSE 给浏览器） | — | `<<FILE:>>` 由前端 markdown 处理 |
| `wechat` | ✅ | bridge 用 `client.send_text` 兜底 | delivery.py |
| `scheduler` | ✅ | 仅作摘要写入 task runs | scheduler `_handle_send_message_tool` |
| `humanchat`（admin agent） | ✅ | admin_bridge 兜底 | admin_bridge 内联 |

---

## 12. Capabilities 速查

| Capability | 工具 | 启用方式 | 写入位置 |
|------------|------|----------|----------|
| `web` | `web_search` / `web_fetch` | 默认（admin），可选（service） | 不写存储 |
| `image` | `generate_image` | service capabilities | `{owner}/generated/images/` |
| `speech` | `generate_speech` | service capabilities | `{owner}/generated/audio/` |
| `video` | `generate_video` | service capabilities | `{owner}/generated/videos/` |
| `scheduler` | `schedule_task` + `manage_scheduled_tasks` | admin 默认；service 可选 | tasks 目录 |
| `humanchat` | `send_message` | WeChat 启用时**自动注入** | 不写存储（投递层处理） |
| `memory_subagent` | soul_list/read/write/delete + 对话查询 | `soul/config.json.memory_subagent_enabled` | `filesystem/soul/` |
| `soul_edit` | filesystem 直接读写 soul/ | `soul_edit_enabled` | `filesystem/soul/` |

---

## 13. 改动检查清单

修改文件系统结构时，**必须**同步更新这些位置（不全将引发隐性 bug）：

| 改动 | 同步位置 |
|------|----------|
| 新增用户级目录 | `_create_user_dirs` (`security.py`) + `ensure_user_dirs` (`storage/local.py`) |
| 新增 conversation 字段 | `save_message` + `save_consumer_message` + 前端 `Message` 类型 |
| 新增 task 字段 | `create_task` / `create_service_task` + 前端 `Scheduler/index.tsx` |
| 新增 capability | `tools.py::CAPABILITY_PROMPTS` + `agent.py::create_user_agent` + `consumer_agent.py` + 前端 capability 选择 |
| 新增 channel | `consumer_agent.py::create_consumer_agent` + cache_key + 调用方传参 |
| 新增微信 API 路由 | `router.py` 或 `admin_router.py` + `frontend/server.js` proxy 规则 + 路由总数计数 |
| 新增 storage 操作 | `base.py` 抽象方法 + `local.py` + `s3.py` + 测试 |

---

## 附录 A：典型操作的物理路径快速对照

```
Admin 在 web 端发起对话
  → users/{adm}/conversations/{cid}.json                 [save_message]
  → users/{adm}/conversations/{cid}/query_appendix/...   [上传图片]
  → users/{adm}/filesystem/generated/images/...          [generate_image]

Admin 通过自己的微信对话
  → users/{adm}/admin_wechat_session.json                [扫码后写入]
  → users/{adm}/conversations/{cid}.json                 [复用普通对话]
  → users/{adm}/conversations/{cid}/query_appendix/...   [接收图片]

Consumer 在 /s/{svc} 网页对话
  → users/{adm}/services/{svc}/conversations/{cid}/messages.json
  → users/{adm}/services/{svc}/conversations/{cid}/query_appendix/...
  → users/{adm}/services/{svc}/conversations/{cid}/generated/...

Consumer 通过 /wc/{svc} 扫码 → 微信对话
  → users/{adm}/services/{svc}/wechat_sessions.json      [扫码确认时持久化]
  → users/{adm}/services/{svc}/conversations/{cid}/messages.json
  → users/{adm}/services/{svc}/conversations/{cid}/query_appendix/images/wx_*.jpg
  → users/{adm}/services/{svc}/conversations/{cid}/generated/...

Admin 创建 cron 任务 → 微信群发
  → users/{adm}/tasks/task_*.json                        [admin 任务]
  → users/{adm}/services/{svc}/tasks/stask_*.json        [service 任务，publish_service_task 创建]
  → 触发后写 wechat_send step 到 runs[]
  → 落盘对话历史（source=scheduled_task / admin_broadcast）

Service Agent → contact_admin
  → users/{adm}/inbox/inbox_*.json                       [立即写入]
  → 如果 admin 已连微信 → inbox agent (thread_id=inbox-{adm})
  → admin 微信收到通知（status=handled）
```

---

## 附录 B：JSON Schema 速查索引

| 文件 | 主要字段 | Schema 章节 |
|------|----------|-------------|
| `users/users.json` | username/password_hash/token/reg_key | §5.1 |
| `config/registration_keys.json` | keys[].key/used/used_by | §5.2 |
| `users/{uid}/api_keys.json` | 9 个 provider 字段（加密） | §5.3 |
| `users/{uid}/soul/config.json` | memory_enabled / memory_subagent_enabled / soul_edit_enabled | §2 |
| `users/{uid}/conversations/{cid}.json` | id/title/messages[].{role,content,blocks,attachments,tool_calls} | §3.1 |
| `users/{uid}/services/{svc}/config.json` | capabilities/wechat_channel/welcome_message/quick_questions | §4.1 |
| `users/{uid}/services/{svc}/keys.json` | keys[].{prefix,key_hash,name} | §4.2 |
| `users/{uid}/services/{svc}/wechat_sessions.json` | sessions[].{from_user_id,context_token,updates_buf} | §9.4 |
| `users/{uid}/services/{svc}/conversations/{cid}/messages.json` | 同 admin 对话格式 | §3.2 |
| `users/{uid}/tasks/task_*.json` | schedule_type/task_type/reply_to/runs[].steps | §6.1 |
| `users/{uid}/services/{svc}/tasks/stask_*.json` | admin_id/service_id/runs[] | §6.2 |
| `users/{uid}/inbox/inbox_*.json` | service_id/wechat_session_id/status/handled_by | §7.1 |
| `users/{uid}/admin_wechat_session.json` | conversation_id/from_user_id/context_token | §9.5 |
