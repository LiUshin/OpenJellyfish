# JellyfishBot 开发指南

> 面向开发者的完整技术文档，涵盖架构设计、后端/前端模块、安全机制、扩展方式和部署。
> 更新日期：2026-04-21（与 `.cursorrules`、`docs/filesystem-architecture.md` 同步）

---

## 目录

1. [架构概览](#1-架构概览)
2. [技术栈](#2-技术栈)
3. [开发环境搭建](#3-开发环境搭建)
4. [后端架构](#4-后端架构)
   - [4.1 目录结构](#41-目录结构)
   - [4.2 FastAPI 路由总表](#42-fastapi-路由总表)
   - [4.3 依赖注入与认证](#43-依赖注入与认证)
   - [4.4 Agent 引擎](#44-agent-引擎)
   - [4.5 工具系统](#45-工具系统)
   - [4.6 Subagent / Memory / Soul](#46-subagent--memory--soul)
   - [4.7 存储层](#47-存储层)
   - [4.8 安全架构](#48-安全架构)
   - [4.9 Per-Admin Python venv](#49-per-admin-python-venv)
   - [4.10 Per-Admin API Key](#410-per-admin-api-key)
5. [前端架构](#5-前端架构)
   - [5.1 目录结构](#51-目录结构)
   - [5.2 路由系统](#52-路由系统)
   - [5.3 状态管理](#53-状态管理)
   - [5.4 API 客户端](#54-api-客户端)
   - [5.5 SSE 流式处理](#55-sse-流式处理)
   - [5.6 Chat 组件层次与共享渲染](#56-chat-组件层次与共享渲染)
   - [5.7 文件预览面板](#57-文件预览面板)
   - [5.8 错误边界](#58-错误边界)
   - [5.9 设计系统与多主题](#59-设计系统与多主题)
6. [Service & Consumer 双层](#6-service--consumer-双层)
7. [WeChat 集成](#7-wechat-集成)
8. [定时任务（Scheduler）](#8-定时任务scheduler)
9. [Inbox（收件箱）](#9-inbox收件箱)
10. [实时语音（S2S WebSocket）](#10-实时语音s2s-websocket)
11. [API 参考](#11-api-参考)
12. [Tauri 桌面启动器](#12-tauri-桌面启动器)
13. [跨平台启动器（launcher.py）](#13-跨平台启动器launcherpy)
14. [Docker 部署](#14-docker-部署)
15. [测试与调试](#15-测试与调试)
16. [扩展开发](#16-扩展开发)
17. [开发规范清单](#17-开发规范清单)

> 深度参考：`docs/filesystem-architecture.md`（文件系统/JSON Schema）、`docs/dev/wechat-channels.md`（WeChat 双栈深度细节）。

---

## 1. 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                        客户端 / 用户                              │
│  ┌──────────────┐ ┌────────────┐ ┌─────────┐ ┌────────────────┐ │
│  │  Admin SPA   │ │  Service   │ │ WeChat  │ │ Tauri 桌面 App │ │
│  │  React 19    │ │ /s/{sid}   │ │ /wc/.. │ │ (Rust + WV)    │ │
│  └──────┬───────┘ └─────┬──────┘ └────┬────┘ └────────┬───────┘ │
└─────────┼───────────────┼─────────────┼───────────────┼─────────┘
          │ /api/*        │ /api/v1/*   │ iLink         │ launcher.py
          ▼               ▼             ▼               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Nginx (:80) — SSL 终止 + 反向代理                                │
│  → Express (:3000) — 静态资源 (dist/) + /api 代理                 │
│  → FastAPI (:8000) — 核心后端                                      │
├──────────────────────────────────────────────────────────────────┤
│  FastAPI Application (app/)                                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ routes/  │ │ services/│ │ channels/│ │ storage/ │ │ voice/ │ │
│  │ 路由层   │ │ 业务层   │ │ 渠道层   │ │ 存储抽象 │ │ S2S WS │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
│       └─────┬──────┴───────┬────┘            │           │      │
│             ▼              ▼                  ▼           ▼      │
│  ┌─────────────────────────────┐  ┌──────────────────┐ ┌──────┐ │
│  │  deepagents + LangGraph     │  │  Local FS / S3   │ │ S2S  │ │
│  │  Agent 运行引擎 + AsyncSqlite│  │  + AES key 加密   │ │ Proxy│ │
│  └─────────────────────────────┘  └──────────────────┘ └──────┘ │
└──────────────────────────────────────────────────────────────────┘

外部协议：
  • WeChat iLink Bot：app/channels/wechat/ ⇄ ilinkai.weixin.qq.com
  • OpenAI Realtime：app/voice/router.py ⇄ wss://api.openai.com/.../realtime
  • CloudsWay / Tavily：app/services/web_tools.py（联网搜索）
```

### 分层设计

| 层 | 职责 | 目录 |
|---|---|---|
| **路由层** | HTTP 请求处理、参数校验、SSE 流 | `app/routes/` |
| **业务层** | Agent 创建/缓存、工具、对话、调度、收件箱、订阅 | `app/services/` |
| **渠道层** | WeChat iLink 协议（Service 与 Admin 双栈） | `app/channels/wechat/` |
| **存储层** | Local / S3 文件存储抽象 | `app/storage/` |
| **核心层** | 认证、配置、路径安全、加密、Langfuse | `app/core/` |
| **语音层** | S2S WebSocket 代理（OpenAI Realtime） | `app/voice/` |

### 三大运行环境

| 环境 | 入口 | 用途 |
|---|---|---|
| **本机开发** | `python launcher.py --dev` 或 `uvicorn` + `vite dev` | 开发，热重载 |
| **Docker 部署** | `docker compose up -d --build` | 服务器/团队部署 |
| **桌面 App** | `tauri-launcher/` 编译为 `.dmg` / `.exe` | 终端用户一键启动 |

---

## 2. 技术栈

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行时（Tauri 内嵌 3.12.7） |
| FastAPI | 0.104+ | Web 框架 |
| Uvicorn | 0.24+ | ASGI 服务器 |
| deepagents | 0.4.1+ | AI Agent 框架（自有 fork） |
| LangGraph | latest | Agent 状态图 + checkpointer |
| LangChain | latest | LLM 抽象层 |
| langchain-anthropic / openai | latest | Claude / GPT 适配 |
| langgraph-checkpoint-sqlite | latest | 状态持久化（WAL 模式） |
| Pydantic | 2.0+ | 数据校验 |
| boto3 / aioboto3 | latest | S3 存储（仅 S3 模式） |
| httpx | 0.27+ | 异步 HTTP 客户端 |
| bcrypt | 4.0+ | 密码哈希（sha256 fallback） |
| pycryptodome | 3.20+ | AES-128-ECB（WeChat 媒体）+ AES-256-GCM（API Key） |
| croniter | latest | Cron 表达式（按用户时区） |
| silk-python | 0.2+ | WeChat 语音 SILK 解码 |
| websockets | 11+ | S2S WebSocket 代理 |

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 19 | UI 框架 |
| TypeScript | 5.7 | 类型安全（strict） |
| Vite | 6 | 构建工具（multi-entry） |
| Ant Design | 5 | UI 组件库（动态主题） |
| React Router | 7 | 路由（BrowserRouter） |
| Phosphor Icons | 2.x | 图标（**唯一图标库**） |
| marked | 15+ | Markdown 渲染 |
| DOMPurify | 3.2+ | XSS 防护 |
| highlight.js | 11 | 代码高亮（按需 17 种语言） |
| dayjs | latest | 时间格式化 |

### 桌面端

| 技术 | 用途 |
|------|------|
| Tauri | v2，Rust + WebView |
| Rust crates | tauri 2 / reqwest / tokio / serde / open / libc |
| 嵌入式运行时 | python-build-standalone 3.12.7 + Node.js 20.18.0 |

---

## 3. 开发环境搭建

### 3.1 后端

```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux/macOS

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（也可以登录后在 Settings → General 配置 per-admin key）
cp .env.example .env
# 编辑 .env，至少配置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY

# 生成注册码（首次部署，未运行过则需要）
python generate_keys.py

# 启动（开发模式，热重载）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API 文档：<http://localhost:8000/docs>（Swagger UI）。

> **提示**：项目根目录提供跨平台启动器 `python launcher.py [--dev]`，自动检测旧实例、端口冲突、双进程管理，详见 §13。

### 3.2 前端

```bash
cd frontend
npm install

npm run dev       # Vite dev server → http://localhost:3000（推荐）
npm run build     # 生产构建 → dist/
npm run preview   # 预览生产构建
npm run legacy    # 旧 Express server（从 dist/ 提供静态资源）
```

**Vite 开发代理规则**（`vite.config.ts`）：

| 路径 | 目标 | 说明 |
|------|------|------|
| `/api/*` | `localhost:8000` | FastAPI 所有 API |
| `/s/*` | `localhost:8000` | Consumer 聊天页 |
| `/wc/*` | `localhost:8000` | WeChat 中间页 |
| `/media_resources/*` | `localhost:8000` | Logo/图标静态资源 |

SSE 请求自动设置 `Accept: text/event-stream` 并禁用代理缓冲。

**Vite multi-entry**：`rollupOptions.input` 同时声明 `main` 和 `service-chat` 两个入口，分别打包到 `dist/index.html` 和 `dist/service-chat.html`。

### 3.3 一键启动

```bash
# Windows
start_local.bat
# Linux/macOS
./start_local.sh
# 都等价于 python launcher.py
```

---

## 4. 后端架构

### 4.1 目录结构

```
app/
├── main.py                    # FastAPI 应用入口、路由注册、startup/shutdown 钩子
├── deps.py                    # get_current_user / get_service_context 依赖
│
├── core/
│   ├── settings.py            # 路径常量（ROOT_DIR、USERS_DIR、DATA_DIR）
│   ├── security.py            # 用户认证（注册/登录/JWT）+ _create_user_dirs
│   ├── api_config.py          # 按能力路由 API Key + base_url（支持 user_id）
│   ├── user_api_keys.py       # AES-256-GCM 加密的 per-admin key 存储
│   ├── encryption.py          # AES-GCM master key（data/encryption.key）
│   ├── path_security.py       # safe_join / ensure_within（路径遍历防护）
│   ├── fileutil.py            # 文件读写工具
│   └── observability.py       # Langfuse 集成（v3 SDK）
│
├── schemas/
│   ├── requests.py            # Admin 请求模型
│   └── service.py             # Service / Consumer 请求/配置模型
│
├── services/
│   ├── agent.py               # Admin Agent 工厂 + 模型解析 + cache
│   ├── consumer_agent.py      # Consumer Agent 工厂（channel-aware）
│   ├── tools.py               # @tool 工厂 + CAPABILITY_PROMPTS
│   ├── ai_tools.py            # 多媒体生成（image/tts/video）底层
│   ├── web_tools.py           # CloudsWay / Tavily 双 provider
│   ├── script_runner.py       # 脚本执行（subprocess + AST 检查 + 信号量队列）
│   ├── _sandbox_wrapper.py    # 运行时 I/O 沙箱（monkey-patch）
│   ├── conversations.py       # Admin 对话持久化 + 附件
│   ├── prompt.py              # System Prompt 版本管理 + capability prompts
│   ├── preferences.py         # 用户时区/界面偏好
│   ├── subagents.py           # Subagent 配置 + DEFAULT_SUBAGENTS
│   ├── memory_tools.py        # Memory Subagent 工具 + Soul 配置 + 短期记忆
│   ├── published.py           # Service CRUD + API Key + Consumer 会话
│   ├── scheduler.py           # 定时任务（Admin + Service 双轨）
│   ├── inbox.py               # 收件箱（contact_admin → Inbox Agent → WeChat 转发）
│   └── venv_manager.py        # per-user Python 虚拟环境
│
├── routes/                    # 详见 §4.2 路由总表
│   ├── auth.py
│   ├── conversations.py
│   ├── chat.py
│   ├── files.py
│   ├── scripts.py
│   ├── models.py
│   ├── settings_routes.py     # system_prompt / user_profile / subagents / api_keys / soul / capability_prompts
│   ├── batch.py
│   ├── services.py
│   ├── consumer.py            # /api/v1/*（Consumer 对外）
│   ├── consumer_ui.py         # /s/{service_id}（Consumer 聊天页）
│   ├── scheduler.py
│   ├── inbox.py
│   └── wechat_ui.py           # /wc/{service_id}（WeChat 扫码中间页）
│
├── channels/wechat/
│   ├── client.py              # iLink 协议客户端（getconfig / getupdates / sendmessage / cdn）
│   ├── bridge.py              # Service Consumer Bridge（multimodal + send_message 拦截）
│   ├── admin_bridge.py        # Admin 自接入 Bridge（独立逻辑）
│   ├── admin_router.py        # /api/admin/wechat/* 路由
│   ├── router.py              # /api/wc/* 公开路由 + Admin 服务渠道管理
│   ├── session_manager.py     # 会话生命周期 + 持久化 + 重连
│   ├── media.py               # AES-128-ECB + CDN 上传/下载
│   ├── delivery.py            # 统一投递（含 <<FILE:>> 标签解析）
│   └── rate_limiter.py        # 单用户/QR/全局 session 频率限制
│
├── storage/
│   ├── config.py              # S3Config + is_s3_mode
│   ├── base.py                # StorageService ABC
│   ├── local.py               # 本地文件系统实现
│   ├── s3.py                  # boto3 S3 实现（兼容 MinIO/R2/OSS）
│   ├── s3_backend.py          # deepagents BackendProtocol for S3
│   └── __init__.py            # get_storage_service / create_agent_backend / create_consumer_backend
│
└── voice/
    └── router.py              # WebSocket S2S 代理（OpenAI Realtime）
```

### 4.2 FastAPI 路由总表

`app/main.py` 中统一注册，**当前约 70 条路由**。

#### 公开 / 认证

| 前缀 | 路由 | 说明 |
|---|---|---|
| `/api/auth` | `register` / `login` / `me` | 用户注册（需注册码）+ 登录 + JWT |

#### Admin（需 `get_current_user`）

| 前缀 | 用途 |
|---|---|
| `/api/conversations` | 对话 CRUD + 附件 |
| `/api/chat` | 主 SSE 聊天 + `/resume` + `/stop` + `/streaming-status` |
| `/api/files` | 文件 CRUD + 上传 + 下载 + media |
| `/api/scripts` | 脚本执行 + audio 转写 |
| `/api/models` | 可用模型列表 |
| `/api/system-prompt` | Prompt 内容 + 版本管理 |
| `/api/user-profile` | 用户画像 + 版本管理 |
| `/api/subagents` | Subagent CRUD + `available_tools` |
| `/api/capability-prompts` | 能力提示词 per-user 覆盖 |
| `/api/soul/config` | Soul 配置（GET/PUT） |
| `/api/batch` | Excel 批量执行 |
| `/api/services` | Service CRUD + API Key |
| `/api/scheduler` | Admin 定时任务 + 运行记录 |
| `/api/scheduler/services/...` | Service 定时任务 |
| `/api/inbox` | 收件箱（list/get/update/delete） |
| `/api/packages` | per-user venv 包管理 |
| `/api/settings/api-keys` | per-admin API Key（加密） |
| `/api/wc/...` | WeChat Service 渠道（QR + sessions + messages） |
| `/api/admin/wechat/*` | Admin 自接入微信 |
| `/api/voice/...` | WebSocket S2S 代理 |

#### Consumer（需 `get_service_context`）

| 路由 | 说明 |
|---|---|
| `POST /api/v1/conversations` | 创建对话 |
| `GET /api/v1/conversations/{id}` | 获取对话历史 |
| `GET /api/v1/conversations/{id}/files` | 列出生成文件 |
| `GET /api/v1/conversations/{id}/files/{path}` | 下载生成文件（query `?key=`） |
| `GET /api/v1/conversations/{id}/attachments/{path}` | 下载用户附件 |
| `POST /api/v1/chat` | 自定义 SSE 聊天 |
| `POST /api/v1/chat/completions` | OpenAI 兼容（流式 + 非流式） |

#### 静态页面

| 路由 | 说明 |
|---|---|
| `GET /s/{service_id}` | Consumer 独立聊天页（React multi-entry） |
| `GET /wc/{service_id}` | WeChat 扫码中间页（HTML 模板） |

### 4.3 依赖注入与认证

`app/deps.py` 提供两个核心依赖：

```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict
async def get_service_context(authorization: str = Header(...)) -> ServiceContext
```

| 维度 | Admin | Consumer |
|---|---|---|
| 凭证 | JWT Bearer Token | `sk-svc-...` Bearer Token |
| 存储 | `users/users.json`（bcrypt + sha256 fallback） | `users/{admin}/services/{svc}/keys.json`（sha256 hashed） |
| 上下文 | `{user_id, username}` | `ServiceContext{admin_id, service_id, service_config, key_id}` |
| 适用路由 | `/api/*`（除 `/api/v1/*`） | `/api/v1/*` + `/s/{sid}` |

> 同一台 FastAPI 内两套依赖共存，路由文件需明确选择，**Consumer 路由不要错用 `get_current_user`**。

### 4.4 Agent 引擎

#### 4.4.1 Admin Agent (`app/services/agent.py`)

- **创建**：`create_user_agent(user_id, model_id, capabilities, plan_mode, ...)`
- **缓存**：per-(user_id, model_id, capabilities, plan_mode, channel) 缓存，避免重复初始化
- **Checkpointer**：`AsyncSqliteSaver` → `data/checkpoints.db`
  - 启动时执行 `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL`，降低多 bridge 并发的锁冲突
- **Soul 注入**：根据 `soul/config.json` 决定是否注入 `memory_subagent` / `soul_edit` 能力提示词
- **缓存失效**：`clear_agent_cache(user_id)` — 在 prompt / subagent / API key 变更时调用

#### 4.4.2 模型解析

```python
# 模型 ID 格式
"anthropic:claude-sonnet-4-6-20250929"
"openai:gpt-5.4"

# Thinking 模型自动配置 extended_thinking
THINKING_MODEL_CONFIG = {
    "anthropic:claude-opus-4-6-20250929-thinking": {...},
    ...
}

# api_key + base_url 由 _resolve_model 通过 api_config + user_api_keys 解析
_resolve_model(model_id, user_id=None) → ChatModel
```

#### 4.4.3 Consumer Agent (`app/services/consumer_agent.py`)

- **工厂**：`create_consumer_agent(admin_id, service_id, conv_id, channel="web", ...)`
- **channel 感知**（重要！）：
  - `channel="web"`：Web /s/ + `/api/v1/*`，**不**注入 `send_message`（消息已直接走 SSE）
  - `channel="wechat"`：注入 `send_message`，结果由 `delivery.py` 反向投递到 WeChat
  - `channel="scheduler"`：同 wechat，定时任务推送结果
- **cache_key**：包含 `::ch={channel}`（非 web 时），避免错误复用
- **能力受限**：仅根据 Service `capabilities` 列表注入工具
- **文件系统**：`users/{admin_id}/services/{svc}/conversations/{conv_id}/generated/` 隔离

#### 4.4.4 消息时间戳注入（2026-04-13 重构）

精确时间不再写入 system prompt（按天缓存会冻结时间），改为每条用户消息前注入 `[YYYY-MM-DD HH:MM:SS]`：

- `prompt.py::stamp_message(content, user_id)` 处理 `str` 和 multimodal `list` 两种格式
- 注入点：`chat.py`（Web）、`admin_bridge.py`（Admin WeChat）、`consumer.py`（Consumer × 3 处）、`bridge.py`（Service WeChat）
- Consumer 端使用 `admin_id` 获取时区

### 4.5 工具系统

#### 4.5.1 内置工具 (`app/services/tools.py`)

| 工具 | 说明 | Admin | Consumer 注入条件 |
|------|------|-------|---|
| `read_file` / `write_file` / `ls` / `glob` / `grep` | deepagents 内置 | ✅ 始终 | ✅ 始终 |
| `edit_file` / `write_todos` | deepagents 内置 | ✅ | ✅ |
| `task` | deepagents subagent 调度 | ✅ | ✅ |
| `run_python_script` | 沙箱脚本执行 | ✅ | ✅ |
| `web_search` / `web_fetch` | CloudsWay / Tavily | ✅ 始终注入 | `web` capability |
| `generate_image` / `generate_speech` / `generate_video` | OpenAI 多媒体 | 按 capability | 按 capability |
| `schedule_task` / `manage_scheduled_tasks` | 定时任务 CRUD | ✅ 始终 | `scheduler` capability |
| `publish_service_task` | Admin 给 Service 派发任务 | ✅ 始终 | ❌ |
| `send_message` | 发送给微信用户 | wechat 渠道注入 | `humanchat` capability + 非 web channel |
| `contact_admin` | Service 通知管理员 | ❌ | `humanchat` capability |
| `soul_list` / `soul_read` / `soul_write` / `soul_delete` | Soul 文件操作 | Memory Subagent 内（且需 `memory_subagent_enabled`） | ❌ |

#### 4.5.2 多媒体工具 (`app/services/ai_tools.py`)

底层调用 OpenAI API：

- `generate_image`：`gpt-image-2`，输出存到 `generated/images/`
- `generate_speech`：`tts-1` / `tts-1-hd`（6 种音色），存到 `generated/audio/`
- `generate_video`：`Sora 2`，存到 `generated/videos/`

**返回约定**：返回提示语 `"已生成 ... 请使用 <<FILE:/generated/xxx>> 展示给用户"`，前端 `markdown.ts` 和 `delivery.py::extract_media_tags` 都识别此标签。

#### 4.5.3 联网工具 (`app/services/web_tools.py`)

双 provider，按 user_id 解析 key：

| Provider | Key 来源 | 备注 |
|---|---|---|
| **CloudsWay**（优先） | `CLOUDSWAY_SEARCH_KEY` | `CLOUDSWAY_SEARCH_URL` / `CLOUDSWAY_READ_URL` 可覆盖端点 |
| **Tavily**（备选） | `TAVILY_API_KEY` | 自动 fallback |

#### 4.5.4 Capability Prompts

- `tools.py::CAPABILITY_PROMPTS` 定义每个能力的工具使用规则
- **per-user 覆盖**：`users/{uid}/capability_prompts.json`（仅存覆盖项）
- API：`GET /api/capability-prompts`（含 `is_custom`）、`PUT /key`、`DELETE /key`
- 解析：`prompt.py::get_resolved_capability_prompt(user_id, key)`

### 4.6 Subagent / Memory / Soul

#### 4.6.1 Subagent (`app/services/subagents.py`)

- **存储**：`users/{uid}/subagents.json`
- **DEFAULT_SUBAGENTS**：包含一个内置 `memory` subagent（不可删，但可禁用）
- **可用工具池**（`SHARED_TOOL_NAMES + MEMORY_TOOL_NAMES`）：
  - 通用：`run_script` / `web_search` / `web_fetch` / `generate_image` / `generate_speech` / `generate_video` / `schedule_task` / `manage_scheduled_tasks` / `publish_service_task` / `send_message`
  - 记忆：`list_conversations` / `read_conversation` / `list_service_conversations` / `read_service_conversation` / `read_inbox` / `soul_list` / `soul_read` / `soul_write` / `soul_delete`
- **按需创建**：`build_subagent_tools(subagent_config, user_id)` 仅实例化配置中列出的工具
- **API**：`GET /api/subagents`（含 `available_tools`）+ CRUD

#### 4.6.2 Memory Subagent

- **Admin Memory Subagent**：默认工具 = 5 个对话/inbox 读工具
  - `memory_subagent_enabled=true` 时追加 4 个 soul 写工具
  - 对话历史**只读**，soul 内容可读写
- **Consumer Memory Subagent**：仅 `read_my_conversation`（自身对话只读）
- **工厂**：
  - `create_admin_memory_tools(user_id)` → 5~9 个工具
  - `create_consumer_memory_tools(admin_id, svc_id, conv_id)` → 1 个工具

#### 4.6.3 Soul 系统 (`app/services/memory_tools.py`)

```
users/{uid}/
├── soul/
│   └── config.json              # 应用层配置（agent 不可访问）
└── filesystem/soul/             # agent 可读写的 Soul 内容（笔记/人格）
    └── *.md / *.json
```

- **config.json 字段**：
  - `memory_enabled`：开启短期记忆注入
  - `include_consumer_conversations`：Memory subagent 是否能读 Consumer 对话
  - `max_recent_messages`：默认 5 条
  - `memory_subagent_enabled`：是否给 Memory Subagent soul 写权限
  - `soul_edit_enabled`：主 Agent 是否能直接通过 `filesystem/soul/` 读写 Soul
- **路径迁移**：`sync_soul_symlink()` 会移除旧 symlink、自动迁移内容到 `filesystem/soul/`（避免 deepagents `Path.resolve()` 跟随符号链接的逃逸报错）
- **能力提示词**：`memory_subagent` / `soul_edit` 在 `CAPABILITY_PROMPTS` 中按开关注入

#### 4.6.4 短期记忆注入

- `scheduler.py::_run_*_agent_task`：从对话 JSON 读最近 N 条消息拼入 prompt 前缀
- `inbox.py::_trigger_inbox_agent`：注入最近 3 条 inbox 历史
- 消息来源标注：
  - Service 定时任务 prompt 头部 `[系统指令 - 来自管理员]`
  - Inbox agent prompt 头部 `[系统指令 - Service 收件箱通知]`

### 4.7 存储层

#### 4.7.1 抽象接口 (`app/storage/base.py`)

```python
class StorageService(ABC):
    async def read_file(self, path: str) -> bytes
    async def write_file(self, path: str, content: bytes) -> None
    async def list_directory(self, path: str) -> list
    async def delete(self, path: str) -> None
    async def exists(self, path: str) -> bool
    async def move(self, src: str, dst: str) -> None
```

#### 4.7.2 后端选择

通过 `STORAGE_BACKEND` 环境变量：

- `local`（默认）：`LocalStorageService` — `os.*` 操作本地磁盘
- `s3`：`S3StorageService` — boto3 API（兼容 AWS S3 / MinIO / R2 / 阿里 OSS）

#### 4.7.3 S3 键映射

```
{prefix}/{user_id}/fs/{path}                              # Admin 文件系统
{prefix}/{admin_id}/svc/{svc_id}/{conv_id}/gen/{path}     # Consumer 生成文件
```

> **注意**：JSON 配置文件（users.json、conversations、services config 等）**目前仍走本地盘**。S3 模式仅托管文件系统层。

#### 4.7.4 媒体访问差异

| 模式 | `/api/files/media` 行为 |
|---|---|
| `local` | `FileResponse` 直接流文件 |
| `s3` | 生成 presigned URL，返回 302 重定向 |

#### 4.7.5 工厂函数 (`app/storage/__init__.py`)

```python
get_storage_service() → StorageService
create_agent_backend(root_dir, user_id=None) → BackendProtocol  # Admin
create_consumer_backend(admin_id, svc_id, conv_id, gen_dir) → BackendProtocol
```

#### 4.7.6 脚本执行

- **本地模式**：直接读 `users/{uid}/filesystem/scripts/` 下的脚本
- **S3 模式**：临时下载到本地 → 子进程执行 → 结果上传回 S3

> 完整的目录树、JSON Schema 和 6 大消息流详见 `docs/filesystem-architecture.md`。

### 4.8 安全架构

#### 4.8.1 路径遍历防护 (`app/core/path_security.py`)

```python
safe_join(base, user_path) → str        # 安全路径拼接
ensure_within(path, root) → bool        # 验证路径在根目录内
```

实现：`pathlib.Path.resolve()` + 分隔符感知的边界检查（`startswith(root + os.sep)`）。Windows 大小写不敏感场景下混合大小写路径理论上可能误判，需注意。

#### 4.8.2 脚本沙箱（双层 Defense-in-Depth）

**第一层：AST 静态分析** (`script_runner._check_script_safety`)

- 禁止危险模块：`subprocess` / `pathlib` / `ctypes` / `io` / `pickle` / `threading` / `posix` / `nt` / `_posixsubprocess`
- 禁止危险内置：`exec` / `eval` / `getattr` / `setattr` / `globals`
- 禁止"绝对危险"的 os 函数：`system` / `popen` / `exec*` / `spawn*` / `fork` / `kill` / `chown` / `setuid` / `chroot` / `chdir`
- 禁止访问 `__builtins__` / `__subclasses__` / `__globals__` / `__dict__` / `__mro__` / `__bases__`
- **拒绝 `Subscript` / `Call` 形式的函数调用**（封堵 `os.__dict__['system'](...)` / `(lambda:...)()`）
- 文件 I/O 函数（remove / rename / mkdir / listdir / chmod 等）**故意不在 AST 黑名单**，仅在运行时按路径白名单拦截

**第二层：运行时沙箱** (`_sandbox_wrapper.py`)

- Monkey-patch `builtins.open` / `io.open` / `os.listdir` / `os.scandir` / `os.walk` / `os.chdir` / `os.open` / `os.readlink` 强制读权限
- 写操作（remove / unlink / rmdir / rename / replace / mkdir / makedirs / chmod / chown / link / symlink / utime / truncate / mkfifo / mknod）走 `_check_write` 路径白名单
- "绝对危险"函数（system / popen / exec* / spawn* / posix_spawn* / fork / forkpty / kill / killpg / chown / lchown / setuid / setgid / setres*id / chroot / pipe / pipe2 / dup / dup2）**直接覆盖为 `PermissionError` 报错函数**，即使攻击者通过 `os.__dict__[name]` / `vars(os)[name]` 拿到引用，调用时仍然抛错

**沙箱权限配置**：

| 角色 | read | write |
|---|---|---|
| **Admin** | `scripts/ + docs/` | `scripts/ + generated/` |
| **Consumer** | `scripts/ + docs/`（按 `allowed_scripts` 过滤） | conversation 自己的 `generated/` |
| **定时任务** | `task_config.permissions.read_dirs` | `task_config.permissions.write_dirs` |

**Sandbox read 豁免**（自动）：

- `_PYTHON_READ_ROOTS`：`sys.prefix` / `sys.base_prefix` / `sys.exec_prefix` / `site.getsitepackages()` — 允许 `import matplotlib`
- `_SYSTEM_READ_DIRS`：`/usr/share` / `/etc/fonts` / `/etc/ssl` / macOS 字体目录
- `_TEMP_DIR`（write）：`tempfile.gettempdir()` — 库缓存写入
- `MPLCONFIGDIR` 重定向到 temp 目录

**资源限制**（2026-04-18 调优）：

| 限制 | 值 | 说明 |
|---|---|---|
| `_MAX_NPROC` | 256 | numpy/OpenBLAS 在 16 配额下崩溃 |
| `_MEMORY_LIMIT_BYTES` | 1024 MB | pandas/matplotlib 余量 |
| `OPENBLAS_NUM_THREADS` 等 | 2 | 防止单脚本占满 CPU |
| `_SCRIPT_SEMAPHORE` | 4 | 全局并发数（`SCRIPT_CONCURRENCY` 覆盖） |
| `_QUEUE_TIMEOUT` | 180s | 排队超时（`SCRIPT_QUEUE_TIMEOUT` 覆盖） |

> Linux 注意：`RLIMIT_NPROC` 限制的是当前 uid 的进程/线程总数（pthread = LWP）。Windows 上 `preexec_fn=None`，无 `resource.setrlimit`。

#### 4.8.3 XSS 防护

- 后端：`consumer_ui.py` / `wechat_ui.py` 使用 `html.escape()` 处理模板注入
- `_safe_json_for_inline_script` 把 `</` 转义为 `<\/` 防 script breakout
- 前端：`marked.parse()` 输出经 `DOMPurify.sanitize()` 清洗，白名单含 `audio` / `video` / `iframe`

#### 4.8.4 加密

- **Master Key**：`data/encryption.key`（首次生成，或 `ENCRYPTION_KEY` 环境变量覆盖）
- **API Key 加密**：AES-256-GCM，存储在 `users/{uid}/api_keys.json`
- **WeChat 媒体**：AES-128-ECB（iLink 协议要求）

### 4.9 Per-Admin Python venv

- **模块**：`app/services/venv_manager.py`
- **目录**：`users/{uid}/venv/`，每个 Admin 独立的 Python 虚拟环境
- **创建**：`--system-site-packages` 继承系统预装包
- **持久化**：用户安装的包记录到 `users/{uid}/venv/requirements.txt`
- **脚本执行**：`tools.py::create_run_script_tool` 使用 `get_user_python(user_id)`；Consumer 使用 admin 的 venv
- **API**：
  - `GET /api/packages` — 列出已安装包 + venv 状态
  - `POST /api/packages/init` — 初始化用户 venv
  - `POST /api/packages/install` — 安装包（包名禁止 ``;|&$` `` 等注入字符）
  - `POST /api/packages/uninstall` — 卸载包
- **启动恢复**：`main.py` startup 调用 `restore_all_venvs()`，对有 `requirements.txt` 的用户自动 `pip install -r`

### 4.10 Per-Admin API Key

#### 4.10.1 设计

- 每个 Admin 在 Settings → General 配置自己的 OpenAI / Anthropic / Tavily / 多媒体 key
- AES-256-GCM 加密存储
- **优先级链**：`用户配置 > 环境变量 > 未配置（提醒设置）`
- Admin 的所有 Agent（主 Agent / Subagent / Consumer Agent）统一使用该 Admin 的 key

#### 4.10.2 调用链

```
agent.py::_resolve_model(model_id, user_id)
  └── api_config.get_openai_llm_config(user_id)
        └── user_api_keys.get_user_api_keys(user_id)
              └── encryption.decrypt(...)

consumer_agent.create_consumer_agent(admin_id, ...)
  └── _resolve_model(model_id, user_id=admin_id)

ai_tools.generate_*(user_id=admin_id)
  └── api_config.get_api_config("image", user_id)

web_tools.web_search(query, user_id=admin_id)
```

#### 4.10.3 支持的字段

- 密钥：`openai_api_key` / `anthropic_api_key` / `tavily_api_key` / `cloudsway_search_key` / `image_api_key` / `tts_api_key` / `video_api_key` / `s2s_api_key` / `stt_api_key`
- URL：`openai_base_url` / `anthropic_base_url` / `image_base_url` / `tts_base_url` / `video_base_url` / `s2s_base_url` / `stt_base_url`

#### 4.10.4 API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/settings/api-keys` | 脱敏返回 + `*_configured` 标记 |
| PUT | `/api/settings/api-keys` | 加密保存（触发 `clear_agent_cache + clear_consumer_cache`） |
| POST | `/api/settings/api-keys/test` | 测试连通性（openai / anthropic / tavily / all） |
| GET | `/api/settings/api-keys/status` | 快速检查是否有 LLM provider 可用 |

#### 4.10.5 缓存失效

Key 更新后自动调用 `clear_agent_cache(user_id)` + `clear_consumer_cache(admin_id=user_id)`，下次 Agent 请求重新创建。

---

## 5. 前端架构

### 5.1 目录结构

```
frontend/src/
├── main.tsx                      # React 19 入口（Admin SPA）
├── App.tsx                       # ConfigProvider + AuthProvider + ThemeProvider + StreamProvider + Router
├── router/index.tsx              # BrowserRouter + 三层 ErrorBoundary
├── layouts/AppLayout.tsx         # 主布局（侧栏 + 内容区 + 文件面板触发）
│
├── stores/
│   ├── authContext.tsx           # 认证状态
│   ├── streamContext.tsx         # SSE 流状态（admin 用）
│   ├── fileWorkspaceContext.tsx  # 文件面板状态
│   └── themeContext.tsx          # 多主题切换 + Antd ThemeConfig
│
├── services/api.ts               # 统一 API 客户端
│
├── types/index.ts                # 共享类型（Message / MessageBlock / Subagent / etc.）
│
├── styles/
│   ├── global.css                # 全局样式 / 滚动条 / markdown
│   ├── themes.css                # 多主题 CSS 变量定义
│   └── theme.ts                  # 后备 JS 常量（实际以 CSS 变量为准）
│
├── utils/
│   ├── csvParse.ts               # CSV/TSV 状态机解析
│   ├── fileKind.ts               # 扩展名 → kind 分类
│   └── timezone.ts               # 时区工具
│
├── pages/
│   ├── Login.tsx                 # 品牌分栏登录/注册
│   ├── AdminServices/index.tsx   # Service 管理（4 Tab）
│   ├── Scheduler/index.tsx       # 定时任务（Admin / Service 双 Tab）
│   ├── WeChat/index.tsx          # Admin WeChat 接入
│   ├── Settings/
│   │   ├── index.tsx             # SettingsLayout（侧栏菜单）
│   │   ├── PromptPage.tsx        # System Prompt + Memory & Soul + 能力提示词
│   │   ├── SubagentPage.tsx
│   │   ├── PackagesPage.tsx      # per-user venv
│   │   ├── InboxPage.tsx
│   │   └── GeneralPage.tsx       # API Keys + 时区 + 主题 + Advanced 开关 + BatchRunner 内嵌
│   └── Chat/
│       ├── index.tsx             # 主聊天页
│       ├── chat.module.css       # CSS Modules（--jf-* 变量）
│       ├── markdown.ts           # 唯一渲染管线（含 <<FILE:>> 处理）
│       ├── useSmartScroll.ts     # 智能滚动 hook
│       ├── types.ts              # StreamBlock 联合类型
│       └── components/
│           ├── ThinkingBlock.tsx
│           ├── ToolIndicator.tsx
│           ├── SubagentCard.tsx
│           ├── StreamingMessage.tsx
│           ├── MessageBubble.tsx
│           ├── ApprovalCard.tsx
│           ├── ImageAttachment.tsx
│           ├── VoiceInput.tsx
│           └── PlanTracker.tsx
│
├── components/
│   ├── FilePanel.tsx
│   ├── FilePreview.tsx           # 多类型文件查看器
│   ├── FileTreePicker.tsx        # 可访问文件/脚本图形选择器
│   ├── HeaderControls.tsx
│   ├── LogoLoading.tsx
│   ├── SplitToggle.tsx
│   ├── ApiKeyWarning.tsx         # 无 LLM Key 时引导 Modal
│   ├── ErrorBoundary.tsx         # 全仓唯一 class 组件
│   └── modals/
│       ├── BatchRunner.tsx       # 内嵌于 GeneralPage
│       ├── SoulSettings.tsx
│       ├── SubagentManager.tsx
│       ├── SystemPromptEditor.tsx
│       └── UserProfileEditor.tsx
│
└── service-chat/                 # Vite 第二入口（Consumer 端）
    ├── main.tsx
    ├── ServiceChatApp.tsx
    ├── ServiceToolBadge.tsx      # 友好工具状态条（替代 admin 的 ToolIndicator）
    ├── streamHandler.ts          # 轻量 SSE handler（无 HITL/subagent）
    ├── serviceApi.ts             # Consumer 端 API（与 services/api.ts 解耦）
    └── serviceChat.module.css
```

### 5.2 路由系统

```tsx
<BrowserRouter>
  <Routes>
    <Route path="/login" element={<PublicRoute><Login/></PublicRoute>} />
    <Route element={
      <ErrorBoundary scope="app-layout">
        <ProtectedRoute><AppLayout/></ProtectedRoute>
      </ErrorBoundary>
    }>
      <Route path="/" element={
        <ErrorBoundary scope="chat"><ChatPage/></ErrorBoundary>
      } />
      <Route path="/settings" element={
        <ErrorBoundary scope="settings"><SettingsLayout/></ErrorBoundary>
      }>
        <Route index element={<Navigate to="/settings/prompt" replace/>} />
        <Route path="prompt" element={<PromptPage/>} />
        <Route path="subagents" element={<SubagentPage/>} />
        <Route path="packages" element={<PackagesPage/>} />
        <Route path="batch" element={<Navigate to="/settings/general" replace/>} />
        <Route path="services" element={<AdminServicesPage/>} />
        <Route path="scheduler" element={<SchedulerPage/>} />
        <Route path="wechat" element={<WeChatPage/>} />
        <Route path="inbox" element={<InboxPage/>} />
        <Route path="general" element={<GeneralPage/>} />
      </Route>
    </Route>
    <Route path="*" element={<Navigate to="/" replace/>} />
  </Routes>
</BrowserRouter>
```

- `ProtectedRoute`：未登录重定向 `/login`
- `PublicRoute`：已登录重定向 `/`
- `AppLayout` 提供侧栏导航 + `<Outlet/>` + Portal 节点 `#sider-slot`
- `ChatPage` / `SettingsLayout` 用 `createPortal` 把侧栏内容注入 `#sider-slot`
- `/settings/batch` 重定向到 `/settings/general`（兼容旧链接）

### 5.3 状态管理

不使用全局状态库，采用 React Context + 组件局部状态。

| Context | 文件 | 用途 |
|---|---|---|
| `authContext` | `stores/authContext.tsx` | `user` / `loading` / `login` / `register` / `logout`，Token 存 `localStorage` |
| `streamContext` | `stores/streamContext.tsx` | SSE 流状态、blocks 缓冲、HITL 中断 |
| `fileWorkspaceContext` | `stores/fileWorkspaceContext.tsx` | 文件面板状态、当前编辑文件 |
| `themeContext` | `stores/themeContext.tsx` | 多主题切换 + Antd ThemeConfig |

### 5.4 API 客户端

`src/services/api.ts` 提供 typed API 客户端。

```typescript
async function request<T>(method: string, path: string, body?: unknown): Promise<T>
```

自动处理：
- Bearer Token 注入（从 `localStorage`）
- Content-Type（JSON / FormData）
- 401 自动清除 Token 并刷新
- 错误提取 `detail` 字段

**模块划分**：

| 模块 | 函数集 |
|---|---|
| Auth | `login` / `register` / `getMe` |
| Conversations | `list/create/get/delete` + `attachmentUrl` |
| Chat | `streamChat` / `resumeChat` / `stopChat` / `abortStream` / `checkServerStreaming` |
| Files | `list/read/write/edit/delete/move` + `uploadFiles` / `downloadFile` / `mediaUrl` |
| System Prompt | CRUD + 版本管理 |
| User Profile | CRUD + 版本管理 |
| Capability Prompts | `getCapabilityPrompts` / `updateCapabilityPrompt` / `resetCapabilityPrompt` |
| Soul Config | `getSoulConfig` / `updateSoulConfig` |
| Subagents | `list/get/add/update/delete` |
| Scripts | `runScript` |
| Audio | `transcribeAudio` |
| Models | `getModels` |
| Batch | `uploadBatchExcel` / `startBatchRun` / `listBatchTasks` / `getBatchTask` / `cancelBatchTask` / `batchDownloadUrl` |
| Scheduler | Admin + Service 任务 CRUD + `runNow` |
| Services | CRUD + Keys + WeChat channel |
| Inbox | `list/get/updateStatus/delete` + `getUnreadCount` |
| Packages | venv 状态 + install/uninstall |
| API Keys | `getApiKeys` / `updateApiKeys` / `testApiKeys` / `getApiKeysStatus` |
| WeChat (Admin) | `adminWechatQrcode` / `adminWechatStatus` / `adminWechatSession` / `adminWechatMessages` |
| WeChat (Service) | `serviceWcQrcode` / `serviceWcSessions` / `serviceWcSessionMessages` |

### 5.5 SSE 流式处理

#### 5.5.1 事件类型

```typescript
type SSEEvent =
  | { type: 'token'; content: string }
  | { type: 'thinking'; content: string }
  | { type: 'tool_call'; name: string; args: string }
  | { type: 'tool_call_chunk'; args_delta: string }
  | { type: 'tool_result'; name: string; content: string }
  | { type: 'subagent_call'; name: string; task: string }
  | { type: 'subagent_call_chunk'; args_delta: string }
  | { type: 'subagent_start'; name: string }
  | { type: 'subagent_token'; content: string; agent: string }
  | { type: 'subagent_thinking'; content: string; agent: string }
  | { type: 'subagent_tool_call'; name: string; args: string; agent: string }
  | { type: 'subagent_tool_chunk'; args_delta: string }
  | { type: 'subagent_tool_result'; name: string; content: string; agent: string }
  | { type: 'subagent_end'; name: string; result: string }
  | { type: 'interrupt'; actions: unknown[]; configs: unknown[] }
  | { type: 'done' }
  | { type: 'error'; content: string }
```

#### 5.5.2 性能优化

```
SSE 回调 → useRef 直接修改 blocks 数组（避免每 token setState）
        → requestAnimationFrame 节流刷新（~60fps）
        → 批量更新到 React state
        → StreamingMessage (React.memo) 避免不必要重渲染
```

#### 5.5.3 断流恢复

- 后端 `_stream_agent` / `_stream_consumer` 在 `finally` 块检测未保存的部分回复，追加 `⚠️ [连接中断 — 已保存已生成内容]` 后持久化（`_saved` 标志位防止重复保存）
- `_active_streams` 字典追踪 `{thread_id → {user_id, conv_id}}`
- `GET /api/chat/streaming-status` 返回当前用户活跃 streaming 对话列表
- 前端 Chat 页加载时 `checkServerStreaming()`，如发现仍在后台 streaming，显示黄色横幅 + 「终止并保存」/「刷新状态」按钮

#### 5.5.4 Stop 按钮

- 流式输出时发送按钮变为红色 Stop（Phosphor `Stop`）
- `POST /api/chat/stop` 设置 `asyncio.Event` 取消标志，`_stream_agent` 每次迭代检查

### 5.6 Chat 组件层次与共享渲染

#### 5.6.1 组件树

```
ChatPage (pages/Chat/index.tsx)
├── 对话列表（createPortal → #sider-slot）
├── 消息区域
│   ├── MessageBubble（历史消息）
│   │   └── BlocksRenderer（msg.blocks 优先 → fallback 旧逻辑）
│   ├── StreamingMessage（流式消息容器，React.memo）
│   │   ├── ThinkingBlock（折叠/展开）
│   │   ├── ToolIndicator（工具调用，可展开 args/result）
│   │   └── SubagentCard（子代理 timeline 渲染）
│   └── ApprovalCard（HITL 审批：文件 diff / Plan 编辑）
├── 输入区
│   ├── ImageAttachment（粘贴/拖拽/文件选择）
│   ├── VoiceInput（toggle 模式：单击开始 → 单击停止 → 自动转写）
│   ├── 能力开关 / Plan Mode / 模型选择器
│   └── 发送/Stop 按钮
└── useSmartScroll（用户上滚暂停吸底，回底部恢复）
```

#### 5.6.2 Admin / Service 共享渲染

跨 admin / service 共享的组件（**改一次两边都生效**）：

- `pages/Chat/markdown.ts` — 唯一 markdown 渲染管线，含 `<<FILE:>>` 处理 / DOMPurify / hljs
- `pages/Chat/components/StreamingMessage.tsx` — 共享渲染组件，接受 `toolRenderer` / `hideSubagents` / `avatarSrc` props
- `pages/Chat/types.ts` 的 `StreamBlock` 数据结构

**渲染差异**：

| 维度 | Admin | Service |
|---|---|---|
| 工具块 | 默认 `ToolIndicator`（真实工具名 + args/result） | 传 `toolRenderer={ServiceToolBadge}`（友好文案，未在白名单的统一"思考中…"） |
| Subagent | 显示完整 `SubagentCard` | 传 `hideSubagents` 隐藏 |
| 媒体 URL | `adminMediaUrl(path)` | `setMediaUrlBuilder(buildConsumerMediaUrl)` 覆盖为 query 参数携带 service API key |

#### 5.6.3 消息 Blocks 持久化

流式输出和历史消息使用统一的交错渲染：

- **后端**：`save_message(blocks=...)`，`blocks` 是有序数组
  - `text`: `{"type": "text", "content": "..."}`
  - `thinking`: `{"type": "thinking", "content": "..."}`
  - `tool`: `{"type": "tool", "name": "...", "args": "...", "result": "...", "done": true}`
  - `subagent`: `{"type": "subagent", "name": "...", "task": "...", "status": "done", "content": "...", "tools": [...], "timeline": [...], "done": true}`
- **前端**：`MessageBubble` 检测 `msg.blocks` 时使用 `BlocksRenderer`，否则 fallback 旧逻辑

### 5.7 文件预览面板

- **kind 分类**：`utils/fileKind.ts` 按扩展名 → `image|audio|video|pdf|markdown|html|csv|json|text|binary`
- **openFile 优化**：媒体/binary 跳过 `api.readFile`，直接 `setEditingFile(path)`
- **渲染策略**：

| kind | 渲染方式 |
|---|---|
| image / audio / video / pdf | 原生 `<img>/<audio>/<video>/<iframe>` 走 `mediaUrl(path)`，工具栏隐藏「保存」 |
| markdown | 复用 `pages/Chat/markdown.ts::renderMarkdown`，样式 `.jf-file-md-preview` |
| html | `<iframe sandbox="allow-scripts">`（**无 same-origin** — 允许 Plotly/ECharts 但禁访问父页） |
| csv / tsv | `utils/csvParse.ts` 状态机解析 → antd `Table`（最多 2000 行） |
| json / jsonl / ndjson | `JSON.parse` + hljs 高亮 |
| text / code | textarea 编辑 |
| binary | `Empty` 占位 + 下载按钮 |

- **工具栏切换**：toggle 类（md/html/csv/json）头部加 antd `Segmented`「预览/源码」
- **下载按钮**：所有 kind 工具栏统一新增

### 5.8 错误边界

- 组件：`components/ErrorBoundary.tsx`（**全仓唯一 class 组件**，React 19 仍要求 class 形式，刻意豁免 "avoid classes" 规则）
- **三层部署**（`router/index.tsx`）：
  - `scope="app-layout"` 包裹 `<AppLayout/>` — 兜底整个受保护区域
  - `scope="chat"` 包裹 `<ChatPage/>` — 聊天页崩溃不影响侧栏
  - `scope="settings"` 包裹 `<SettingsLayout/>` — 设置页崩溃不影响聊天
- **交互**：友好提示 + 可展开错误详情 + **复制错误信息**按钮（含 scope/时间/URL/UA/stack/componentStack）
- **样式**：Antd `Result` + `Collapse`，背景用 `--jf-bg-deep`

### 5.9 设计系统与多主题

#### 5.9.1 多主题（`themes.css` + `themeContext.tsx`）

- `[data-theme]` 属性在 `<html>` 上，由 ThemeProvider 控制
- **持久化**：`localStorage.jf-theme`
- **三套已有主题**：

| 主题 | 风格 | 主色 | 特殊规则 |
|---|---|---|---|
| `dark`（默认） | 暖粉紫深色 | `#E89FD9` Primary | — |
| `cyber-ocean` | 青蓝浅色 | — | — |
| `terminal` | 磷绿 CRT 终端 | `#33ff00` | 全局 monospace 字体、`border-radius: 0`、磷光 `text-shadow`、CRT 扫描线、按钮 hover 反转、`text-transform: uppercase` |

- **添加新主题**：
  1. `themes.css` 复制一个 `[data-theme]` 块并调整值
  2. `themeContext.tsx` 添加 `THEMES` 项 + Antd ThemeConfig
- **CSS 变量命名**：
  - 品牌色：`--jf-primary/secondary/accent/highlight/legacy`
  - RGB 三元组：`--jf-primary-rgb`（用于 `rgba(var(--jf-primary-rgb), 0.12)`）
  - 渐变：`--jf-gradient-from/to`、`--jf-user-bubble-bg/shadow`
  - 背景：`--jf-bg-deep/panel/raised/code/inset`
  - 文字：`--jf-text/text-muted/text-dim/text-quaternary`
  - 边框：`--jf-border/border-rgb/border-strong`
  - 语义色：`--jf-success/warning/error/info`
  - 阴影：`--jf-shadow-float/hover/brand`
  - Diff：`--jf-diff-add-bg/del-bg/eq-text`
  - Antd：`--jf-menu-selected-bg/select-option-bg`

#### 5.9.2 圆角规范

| 档位 | CSS 变量 | 用途 |
|---|---|---|
| sm | `var(--jf-radius-sm)` | 4px，内嵌圆角 |
| md | `var(--jf-radius-md)` | 8px，按钮、面板 |
| lg | `var(--jf-radius-lg)` | 12px，卡片、模态 |
| bubble | `var(--jf-radius-bubble)` | 16px，消息气泡 |

- 圆形元素仍用 `'50%'`
- 内联 `borderRadius` **必须**使用变量字符串：`'var(--jf-radius-md)'`，不要硬编码

#### 5.9.3 其他规范

- **图标统一使用 `@phosphor-icons/react`**（替代 `@ant-design/icons`）
- **样式优先级**：antd 组件 > inline style（含 CSS 变量）> CSS Modules
- **类型定义**集中在 `src/types/index.ts`
- **API 调用**统一通过 `src/services/api.ts`
- **Logo**：`/media_resources/jellyfishlogo.png`
- **字体**：正文 Segoe UI，代码 JetBrains Mono（Google Fonts CDN）

#### 5.9.4 Advanced Tab 可见性

- `GeneralPage.tsx`「高级功能」卡片：两个 Switch 控制 Prompt 页的 Advanced Tab 可见性
- `localStorage` key：`show_advanced_system`（操作规则）/ `show_advanced_soul`（Memory & Soul）
- 默认关闭，通过自定义事件 `advanced-settings-changed` 实时响应

---

## 6. Service & Consumer 双层

### 6.1 Admin 层

- 通过 Web UI 登录，拥有完整权限
- 管理文件系统（`docs/scripts/generated/soul`）
- 配置 System Prompt + 用户画像
- 管理 Subagent + 能力提示词
- 发布 Service + 管理 API Key
- 配置定时任务 + 微信接入

### 6.2 Consumer 层

- 通过 `sk-svc-...` API Key 认证（或 `/s/{sid}?key=...` 自助链接）
- **权限受限**：仅访问 Service 配置的能力 + `allowed_docs/scripts`
- **文件系统隔离**：`users/{admin}/services/{svc}/conversations/{conv}/`
- **生成文件隔离**：每个对话独立的 `generated/`
- 不能访问 Admin 的文件系统

### 6.3 Service 配置

每个 Service 是一个 published 配置：

```
users/{admin_id}/services/{service_id}/
├── config.json          # 模型 + system_prompt + capabilities + allowed_docs/scripts + welcome_message + quick_questions + wechat_channel
├── keys.json            # API Key（hashed）
├── wechat_sessions.json # WeChat 会话状态
├── conversations/       # Consumer 对话目录
└── tasks/               # Service 定时任务
```

详细 schema 见 `docs/filesystem-architecture.md` §4。

### 6.4 Service 自助化（v2.x）

#### 6.4.1 专属链接附 Key

- URL 格式：`/s/{service_id}?key=sk-svc-xxx`
- 后端：`consumer_ui.py` 注入模板变量，前端启动时 `URLSearchParams` 读 `key` → 写 `localStorage` → `history.replaceState` 立即清掉 query
- 前端 admin：Key Modal 生成成功后**额外**展示带 key 的完整链接 + 警告（**等同分享 Key**）

#### 6.4.2 欢迎语 + 快速问题

- 字段：`welcome_message: str` + `quick_questions: List[str]`
- 后端模板注入：`_safe_json_for_inline_script` 防 script breakout
- 前端 chat 页：ChatGPT 风格首屏（大欢迎语 + 渐变 chips），发送第一条消息后自动隐藏

#### 6.4.3 文件/脚本图形选择器

- `FileTreePicker.tsx`：antd `Tree` checkable + `loadData` 懒加载 + 「全部 (*)」 Switch
- 文件夹勾选 = 整个目录递归（key 以 `/` 结尾）
- 根目录限定：`allowed_docs` 只展示 `/docs`，`allowed_scripts` 只展示 `/scripts`
- 空 `allowed_docs` 自动回落 `["*"]`，空 `allowed_scripts` 保持空数组（语义=禁止脚本）

### 6.5 Consumer Agent channel-aware

```python
create_consumer_agent(..., channel: str = "web")
```

- `channel="web"`：**不**注入 `send_message`，即便 humanchat capability 启用。原因：web 上 agent 输出已直接流给浏览器，再调 send_message 既无投递目标又会让消费者看到不该看的工具事件
- `channel="wechat"`：注入 `send_message`，工具结果由 delivery 层反向投递
- `channel="scheduler"`：同 wechat
- `cache_key` 中加入 `::ch={channel}`（仅当非默认 web 时）

---

## 7. WeChat 集成

> 完整 iLink 协议细节、CDN 加解密、踩坑记录见 `docs/dev/wechat-channels.md` 和 `docs/wechat-integration-guide.md`。

### 7.1 双栈架构

```
┌──────────────────────────────────────────────────────────┐
│  Service 渠道                  │  Admin 自接入            │
├────────────────────────────────┼─────────────────────────┤
│  /api/wc/*                     │  /api/admin/wechat/*    │
│  router.py                     │  admin_router.py         │
│  bridge.py                     │  admin_bridge.py         │
│  Consumer Agent (channel=wechat│  Admin Agent             │
│  对话存于 service conversations│  对话存于 admin convos   │
│  会话存 wechat_sessions.json   │  存 admin_wechat_session.│
│  权限按 service capabilities   │  完整 docs/scripts 权限  │
└────────────────────────────────┴─────────────────────────┘
共享：client.py / media.py / delivery.py / rate_limiter.py
```

### 7.2 关键模块

| 模块 | 职责 |
|---|---|
| `client.py` | iLink 协议：getconfig / getupdates / sendmessage / getuploadurl / cdn 上传下载 |
| `bridge.py` | Service Bridge：消息路由 + 多模态构建 + send_message 拦截 + 自动审批 HITL |
| `admin_bridge.py` | Admin Bridge：完整权限 + 内联 send_message 处理 |
| `session_manager.py` | 会话生命周期 + 持久化 + 重连（指数退避） |
| `media.py` | AES-128-ECB 加解密 + CDN 上传下载 |
| `delivery.py` | 统一投递（含 `<<FILE:>>` 标签解析） |
| `rate_limiter.py` | 单用户 10 条/60s + QR 5 次/60s + 全局 session 上限 |
| `router.py` / `admin_router.py` | HTTP API 端点 |

### 7.3 多媒体投递

| 方向 | 流程 |
|---|---|
| **接收图片** | CDN GET → AES 解密 → base64 multimodal → Agent Vision |
| **发送图片** | `getuploadurl` → AES 加密 → CDN POST → `sendmessage(image_item.media)` |
| **接收语音** | CDN GET → AES 解密 → SILK→WAV（pysilk） → Whisper 转文字 |
| **发送 TTS** | `/generated/audio/` 下的 MP3 通过 `send_file` 作为文件附件发送 |

> 语音条（voice_item）方案暂停：pysilk 编码的 SILK 在 WeChat 客户端始终静音，24kHz/16kHz 均无效，待进一步调研。

### 7.4 `<<FILE:>>` 标签解析

`delivery.py::extract_media_tags(text) -> (cleaned_text, [paths])`：

```python
_MEDIA_TAG_RE = re.compile(r"<<FILE:([^>]+?)>>")
```

- 主动从 `send_message` 的 text 抽取 `<<FILE:path>>` 标签，转为额外 media 投递
- 剩余 cleaned_text 作为文本发出
- 同时支持 `media_path` 参数和 `<<FILE:>>` 标签两种用法
- Bridge 和 Scheduler 共享此模块，**避免重复代码**

### 7.5 Session 管理要点

- **指数退避重连**（max 20 次后自动移除）
- 有 `from_user_id` 的会话**不**参与 24h 无活动清理（长期保留）
- 无 `from_user_id` 的空会话才按 inactive 清理
- 空轮询仅对「未建立用户」会话在 50 次后丢弃
- **多 Admin 隔离**：`list_sessions` / `remove_session` 按 `admin_id` 过滤

### 7.6 Admin 自接入

- 端点：`POST /api/admin/wechat/qrcode` / `GET qrcode/status` / `GET/DELETE session` / `GET messages`
- **会话持久化**：`users/{user_id}/admin_wechat_session.json`，Docker/重启后自动恢复
- `_save_admin_session()` 在会话创建、`from_user_id` 首次捕获、`context_token` 更新时写入
- `restore_admin_sessions()` 在 `main.py` startup 时调用
- `shutdown_admin_sessions()` 仅停止轮询和关闭连接，**不删除**持久化文件

---

## 8. 定时任务（Scheduler）

### 8.1 调度器设计

- `app/services/scheduler.py::TaskScheduler` 单例，asyncio 循环每 30s 检查
- `main.py` startup 启动，shutdown 优雅停止

### 8.2 任务存储

| 类型 | 路径 | 前缀 |
|---|---|---|
| Admin 任务 | `users/{uid}/tasks/{task_id}.json` | `task_*` |
| Service 任务 | `users/{uid}/services/{svc}/tasks/{task_id}.json` | `stask_*` |

每个任务文件含最近 20 条运行记录。

### 8.3 任务类型

| 类型 | 说明 | 支持范围 |
|---|---|---|
| `script` | 执行 `scripts/` 下的 Python 脚本 | 仅 Admin |
| `agent` | 执行 Agent 任务（prompt + 可选文档上下文） | Admin + Service |

### 8.4 调度类型

| 类型 | 说明 | 示例 |
|---|---|---|
| `once` | 一次性 | `2026-12-31T09:00:00+08:00`（建议带时区后缀） |
| `cron` | Cron 表达式 | `0 9 * * *` |
| `interval` | 间隔（秒） | `3600` |

### 8.5 时区处理

- 每个任务存 `tz_offset_hours` 字段（创建时的用户时区偏移）
- **cron 按用户时区解释**：`_next_cron` UTC now → 用户本地 → croniter → 转回 UTC
- **once 必须带时区后缀**：无 tz 时按 `tz_offset_hours` 补充，工具层 `_ensure_tz_suffix` 兜底
- **interval 不受时区影响**：直接按秒数偏移
- 缺字段的旧任务用 `_resolve_task_tz_offset(task)` 回退 `get_tz_offset(user_id)`（与 preferences 默认 +8 一致）
- React Scheduler 创建/更新任务时 body 必须带 `tz_offset_hours: getTzOffset()`

### 8.6 reply_to 路由

Service 任务通过 `reply_to` 字段控制结果推送目标：

```json
{
  "reply_to": {
    "channel": "wechat | inbox | admin_chat",
    "admin_id": "...",
    "service_id": "...",
    "conversation_id": "...",
    "session_id": "wechat_user_xxx"
  }
}
```

| channel | 推送目标 |
|---|---|
| `wechat` | `delivery.py::deliver_tool_message` 投递到 WeChat 用户 |
| `inbox` | 写入 Admin inbox |
| `admin_chat` | 写入 Admin 普通对话 |

`_run_service_agent_task` 在 agent 执行循环中**实时**拦截 `send_message` 工具调用，通过 `delivery.py` 发送到 WeChat（支持文本+媒体）；`_deliver_reply` 仅在 agent 未使用 send_message 时作为兜底（纯文本摘要）。

### 8.7 运行记录步骤

每条 run record 含 `steps[]`：

| 步骤类型 | 说明 |
|---|---|
| `start` | 开始执行 |
| `docs_loaded` | 文档加载完成 |
| `loop` | Agent 循环迭代 |
| `tool_call` | 工具调用 |
| `tool_result` | 工具结果 |
| `ai_message` | AI 消息 |
| `auto_approve` | 自动审批（HITL） |
| `wechat_warning` | WeChat client 不可用警告 |
| `wechat_error` | WeChat 投递失败 |
| `finish` | 完成 |
| `error` | 错误 |
| `reply` | 兜底推送 |

### 8.8 任务结果持久化

`_run_agent_loop` 结束后调用 `save_message` / `save_consumer_message` 写入对话 JSON，含：
- `source: "scheduled_task"` 或 `"admin_broadcast"` 标记
- 完整 `blocks[]`

### 8.9 sync→async 主循环桥接

LangChain sync tool（如 `contact_admin`）通过 `BaseTool._arun` 在 `run_in_executor` 线程池执行，无 event loop。修复：

```python
# inbox.py / scheduler.py
def set_main_loop(loop: asyncio.AbstractEventLoop):
    global _main_loop
    _main_loop = loop

# 调度时：
try:
    loop = asyncio.get_running_loop()
    loop.create_task(coro)
except RuntimeError:
    if _main_loop is not None and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _main_loop)
```

### 8.10 Service 任务专用工具

Consumer agent 通过：
- `create_service_schedule_tool` 注入 `schedule_task`
- `create_service_manage_tasks_tool` 注入 `manage_scheduled_tasks`（仅 `"scheduler"` 在 capabilities 中时）
- Service 的 `manage_scheduled_tasks` **仅能操作当前 conversation_id 的任务**（权限隔离）

`publish_service_task`（Admin 工具）：
- `service_ids` 支持 ID 和名称匹配（大小写不敏感），未匹配时返回可用 Service 列表
- `session_ids` 可选参数，精确到单个微信会话
- `run_now` 调度使用 `_schedule_coro` 线程安全模式

---

## 9. Inbox（收件箱）

### 9.1 用途

Service Agent 通过 `contact_admin` 工具向 Admin 发送通知，Admin 可在收件箱处理；启用了 Admin WeChat 接入时，Inbox Agent 会自动评估并转发到 WeChat。

### 9.2 数据结构

```
users/{admin_id}/inbox/
└── inbox_{message_id}.json
```

字段含 `from_service_id` / `from_conv_id` / `subject` / `content` / `urgency` / `status: unread|read|archived` / `created_at` 等（详见 `docs/filesystem-architecture.md` §7）。

### 9.3 处理链路

```
Service Agent
  └── contact_admin(subject, content, urgency)  [sync tool]
        └── inbox.post_to_inbox()  [via run_coroutine_threadsafe → _main_loop]
              ├── 写 inbox_{id}.json
              ├── _trigger_inbox_agent()
              │     └── 注入最近 3 条 inbox 历史 → 评估 Agent
              │           └── send_message → Admin WeChat（如已连接）
              └── 前端轮询 GET /api/inbox/unread-count → 显示徽章
```

### 9.4 关键修复

- **inbox agent 线程池问题**：`contact_admin` 是 sync tool，LangGraph 通过 `run_in_executor` 在线程池执行，`asyncio.get_running_loop()` 失败。修复：缓存主事件循环 + `run_coroutine_threadsafe`
- **thread_id 稳定化**：`inbox-{admin_id}`（同一 Admin 共用，有累积记忆）

### 9.5 API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/inbox` | 列表 |
| GET | `/api/inbox/unread-count` | 未读数 |
| GET | `/api/inbox/{id}` | 详情 |
| PUT | `/api/inbox/{id}` | 更新状态 |
| DELETE | `/api/inbox/{id}` | 删除 |

---

## 10. 实时语音（S2S WebSocket）

### 10.1 模块

`app/voice/router.py` 提供 WebSocket 端点 `/api/voice/ws`，作为 OpenAI Realtime API 的代理。

### 10.2 工作流

```
浏览器 ──WebSocket──→ FastAPI ──WebSocket──→ wss://api.openai.com/v1/realtime
                       │
                       └── session.update 时注入工具配置
                       └── 工具调用透传 + 后端工具执行
```

- 工具注入：`session.update` 事件中包含 Admin 配置的工具集
- 鉴权：通过 query 参数携带 JWT
- API Key：通过 `S2S_API_KEY` / `S2S_BASE_URL` 覆盖（fallback 到 `OPENAI_API_KEY`）

---

## 11. API 参考

### 11.1 Admin API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/auth/register` | 注册（需注册码） |
| `POST` | `/api/auth/login` | 登录 |
| `GET` | `/api/auth/me` | 当前用户 |
| `GET/POST/DELETE` | `/api/conversations[/{id}]` | 对话管理 |
| `POST` | `/api/chat` / `/chat/resume` / `/chat/stop` | SSE 聊天 |
| `GET` | `/api/chat/streaming-status` | 后台流活跃状态 |
| CRUD | `/api/files/*` | 文件操作 + media + 上传/下载 |
| `POST` | `/api/scripts/run` | 执行脚本 |
| `POST` | `/api/audio/transcribe` | 语音转写 |
| `GET` | `/api/models` | 模型列表 |
| CRUD | `/api/system-prompt[/versions]` | Prompt 管理 |
| CRUD | `/api/user-profile[/versions]` | 用户画像 |
| CRUD | `/api/subagents` | Subagent 管理 |
| CRUD | `/api/capability-prompts[/{key}]` | 能力提示词 |
| GET/PUT | `/api/soul/config` | Soul 配置 |
| `POST/GET` | `/api/batch/*` | 批量执行 |
| CRUD | `/api/services[/{id}[/keys]]` | Service 管理 |
| CRUD | `/api/scheduler[/{id}]` | Admin 定时任务 |
| `POST` | `/api/scheduler/{id}/run-now` | 立即执行 |
| `GET` | `/api/scheduler/{id}/runs` | 运行记录 |
| `GET` | `/api/scheduler/services/all` | 所有 Service 任务 |
| CRUD | `/api/scheduler/services/{svc_id}[/{task_id}]` | Service 任务 |
| CRUD | `/api/inbox[/{id}]` | 收件箱 |
| `GET` | `/api/inbox/unread-count` | 未读数 |
| GET/POST | `/api/packages[/init/install/uninstall]` | per-user venv |
| GET/PUT/POST | `/api/settings/api-keys[/test/status]` | per-admin API Key |
| `GET` | `/api/wc/{service_id}/qrcode` | Service WeChat QR |
| `GET` | `/api/wc/{service_id}/sessions[/{session_id}/messages]` | 会话查看 |
| `POST` | `/api/admin/wechat/qrcode` | Admin WeChat QR |
| `GET/DELETE` | `/api/admin/wechat/session` | Admin 会话管理 |
| `WS` | `/api/voice/ws` | S2S WebSocket |

### 11.2 Consumer API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/conversations` | 创建对话 |
| `GET` | `/api/v1/conversations/{id}` | 对话历史 |
| `GET` | `/api/v1/conversations/{id}/files[/{path}]` | 生成文件列表 / 下载 |
| `GET` | `/api/v1/conversations/{id}/attachments/{path}` | 用户附件 |
| `POST` | `/api/v1/chat` | 自定义 SSE |
| `POST` | `/api/v1/chat/completions` | OpenAI 兼容 |

### 11.3 静态页面

| 路径 | 说明 |
|---|---|
| `GET /s/{service_id}` | Consumer 独立聊天页（React） |
| `GET /s/{service_id}?key=sk-svc-...` | 同上，自动写入 localStorage |
| `GET /wc/{service_id}` | WeChat 扫码中间页 |

---

## 12. Tauri 桌面启动器

### 12.1 架构

- **Tauri v2**（Rust + WebView）封装为原生桌面应用
- 内嵌 Python 3.12.7 + Node.js 20.18.0 + 后端代码 + 前端构建产物
- 用户双击 `.dmg` / `.exe` 即启动，无需命令行

### 12.2 文件结构

```
tauri-launcher/
├── package.json                 # @tauri-apps/cli
├── dist/
│   └── index.html               # 自包含启动器 UI（含内联 CSS + JS + base64 logo）
├── bundle-resources/            # 暂存区：app/、config/、frontend dist、launcher.py
├── scripts/
│   ├── build.py                 # 一键打包
│   └── version.py               # 版本管理
└── src-tauri/
    ├── Cargo.toml               # tauri 2 / reqwest / tokio / serde / open / libc
    ├── tauri.conf.json          # 窗口 720×580，NSIS(Windows)，macOS ≥10.15
    ├── build.rs
    └── src/
        ├── main.rs              # entry point
        └── lib.rs               # 14 个 Tauri Command
```

### 12.3 14 个 Tauri Commands

**核心（9）**：
- `detect_environment` — Python/Node.js/项目文件检测
- `load_env_config` / `save_env_config` — 读写 `.env`（保留注释 + 未知键）
- `test_api_key` — HTTP 测试 OpenAI/Anthropic/Tavily 连通性
- `start_jellyfish` — 调用 `launcher.py --skip-check`
- `stop_jellyfish` — SIGTERM(Unix) / kill(Windows)
- `get_status` — 轮询进程 + 端口存活
- `open_in_browser` — 浏览器打开前端

**注册码 / 用户管理**：
- `list_registration_keys` / `generate_registration_keys(count)` / `delete_registration_key`
- `list_admin_users` / `reset_admin_password` / `delete_admin_user` / `get_admin_stats`

**关于 / 工具（2026-04-20）**：
- `open_project_dir` / `open_users_dir` / `open_logs_dir` — lazy 创建后打开
- `open_release_page` — 浏览器跳转固定 URL
- `get_app_version` — 返回 `env!("CARGO_PKG_VERSION")`

### 12.4 UI 结构（dist/index.html）

- 左侧 76px 窄导航栏（Logo + 4 页 Tab）
- **Page 1 控制台**：环境检测 3 列 pill + 圆形 START 按钮 + API Keys 配置表单
- **Page 2 注册码管理**：表格 + 生成/删除 + 复制按钮
- **Page 3 账户管理**：统计摘要 4 卡 + 用户表格 + 重置密码/删除
- **Page 4 关于 / 工具**：渐变版本号 + 4 张工具卡（项目目录 / 用户数据 / 日志目录 / Release）

> 整页是**单文件 HTML（含内联 CSS + 内联 JS + base64 logo）**，无构建步骤。`window.__TAURI__` 不存在时走 `mockInvoke()`，所以拿浏览器直开 `file://.../dist/index.html` 也能预览样式。

### 12.5 关键踩坑

#### 12.5.1 Windows `\\?\` 扩展长路径前缀（2026-04-20 关键 bug）

- **症状**：Windows .exe 启动后 uvicorn 报 `OSError: Cannot load native module 'Crypto.Util._cpuid_c'`，但 `.pyd` 文件物理存在
- **根因**：Tauri 的 `app.path().resource_dir()` 在 Windows 上返回 `\\?\D:\JellyfishBot\` 形式的扩展长路径前缀；该前缀污染 Python 子进程 `sys.executable`，`pycryptodome` 的 `os.path.isfile()` 在 `\\?\` 前缀路径下查不到 sibling `.pyd`
- **修复**：`src-tauri/src/lib.rs` 的 `strip_win_extended_prefix()` helper，在 `resolve_project_dir` / `find_bundled_python` / `find_bundled_node` 三处强制剥离前缀；`launcher.py` 加 `_strip_extended_prefix()` 双重防御
- **影响范围**：会影响 numpy / scipy / matplotlib 等所有依赖原生扩展的包
- **mac 完全无此问题**

#### 12.5.2 其他

- **`withGlobalTauri: true`** — 必须！否则 `window.__TAURI__` undefined
- **`server.js` 使用 ESM** — `package.json` 有 `"type": "module"`
- **Express 5 通配符** — 用 `'/{*path}'` 代替 `'*'`
- **路径必须绝对** — `launcher.py::_resolve_python/node()` 必须返回 `os.path.abspath()`
- **macOS 签名** — 修改 Resources 后需 `codesign --force --deep --sign - <app_path>`

### 12.6 Windows 本机 dev 环境

`npx tauri dev` / `build` 都需要：

1. **Rust toolchain**（cargo 在 `C:\Users\{user}\.cargo\bin\` — 默认不在 PATH）
2. **MSVC linker（link.exe）** — VS 2022 Build Tools 或 Community
3. **Windows SDK**（`kernel32.lib` / `ntdll.lib`）— **VS 安装时容易漏勾**！
4. 解决：VS Installer → Modify → 勾选「使用 C++ 的桌面开发」

一行式启动 dev：

```powershell
$vsBat = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat"
& cmd /c "`"$vsBat`" -arch=x64 -no_logo && set" | ForEach-Object {
  if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$env:Path = "C:\Users\$env:USERNAME\.cargo\bin;$env:Path"
cd tauri-launcher; npx tauri dev
```

> `tauri.conf.json` 不要保留 `devUrl`：纯静态项目没有 Vite/dev server，留着会让 `tauri dev` 卡在等待中。  
> 冷启动 Rust 全量编译约 4 分钟（457 个 crate），后续增量 ~5-30 秒。

### 12.7 迭代规则

| 改动文件 | 处理方式 |
|---|---|
| `dist/index.html` | dev 模式 Ctrl+R 即时生效；生产模式需 `npx tauri build` |
| `lib.rs` / `tauri.conf.json` | 必须 `npx tauri build` |
| `launcher.py` / `server.js` / `app/**` / `frontend/dist/**` | Tauri **resources**，**可热修**：直接覆盖安装目录对应文件 + 重启 launcher（macOS 还需 `codesign`） |

### 12.8 构建与发版

```bash
cd tauri-launcher
python scripts/version.py bump patch     # 升级版本号
python scripts/build.py                  # 完整打包
python scripts/version.py tag --push     # 推送 tag → CI 自动构建
```

CI/CD：`.github/workflows/release.yml`
- 触发：push tag `v*` 或手动 `workflow_dispatch`
- 三平台矩阵：macOS-arm64 / macOS-x64 / Windows-x64
- 产物上传 + 自动创建 GitHub Release（draft）

包大小：DMG ~247 MB，解压 ~998 MB。

---

## 13. 跨平台启动器（launcher.py）

### 13.1 功能

- **旧实例检测**：通过端口扫描（lsof / netstat）检测已运行的 JellyfishBot 进程
- **用户确认后杀掉**：列出占用端口的进程，SIGTERM → SIGKILL
- **自动端口发现**：默认 8000(后端) + 3000(前端)，被占用时自动递增
- **双进程管理**：启动 uvicorn + Express，子进程异常退出时全部清理
- **干净退出**：Ctrl+C / SIGTERM 优雅终止（5s 超时后 SIGKILL）

### 13.2 日志 tee（2026-04-20）

所有由 launcher 拉起的子进程的 stdout+stderr 同时写到：
- `{project_dir}/logs/{name}-YYYYMMDD.log`（按天滚动 + append + session header）
- 原始 stdout

实现：`_spawn_with_log(cmd, cwd, env, log_name)` 替代裸 `Popen`，subprocess stdout/stderr 合并到 `PIPE` 后由 `_tee_pipe_to(fh, src)` 后台 daemon 线程逐行落盘。

> ⚠️ **不要回退到 `subprocess.PIPE` + `communicate()`** — 长流程会阻塞 `wait()`；当前实现的 daemon 线程 + `bufsize=0` 是验证过的非阻塞方案。

### 13.3 端口配置

| 文件 | 配置方式 |
|---|---|
| `launcher.py` | `--port` / `--frontend-port` 命令行参数 |
| `server.js` | `FRONTEND_PORT` / `API_TARGET` 环境变量 |
| `start.sh`（Docker） | `BACKEND_PORT` / `FRONTEND_PORT` 环境变量 |

### 13.4 使用

```bash
python launcher.py                    # 生产模式
python launcher.py --dev              # 开发模式（uvicorn --reload + vite dev）
python launcher.py --port 9000        # 自定义后端端口
python launcher.py --backend-only     # 仅后端
python launcher.py --skip-check       # 跳过旧实例检测
```

---

## 14. Docker 部署

### 14.1 多阶段构建

```dockerfile
# Stage 1: frontend-builder
FROM node:20-alpine AS frontend-builder
RUN npm ci && npm run build       # → dist/

# Stage 2: production
FROM python:3.11-slim
RUN pip install -r requirements.txt
RUN npm install express http-proxy-middleware
COPY --from=frontend-builder /build/dist /app/frontend/dist
```

- React 源码和 devDependencies **不进入**最终镜像
- Express `server.js` 从 `dist/` 提供静态文件

### 14.2 docker-compose

```
Cloudflare (SSL) → Nginx (:80) → Express (:3000) → FastAPI (:8000)
```

- Nginx：SSL 终止 + 反向代理（`nginx/nginx.conf`）
- Express：静态资源 + `/api` 代理
- FastAPI：核心后端
- 用户数据 volume：`./data/users:/app/users`

### 14.3 启动脚本（`start.sh`）

1. 启动 FastAPI（`:8000`）
2. 等待就绪后启动 Express（`:3000`）
3. `wait -n` 监控两个进程

### 14.4 健康检查

容器内置健康检查（检查 FastAPI `/docs` 端点），Nginx 在后端就绪后才接受流量。

### 14.5 .dockerignore

排除 `frontend/node_modules/` / `frontend/dist/` / `venv/` / `data/` / `.env` / `.git/`。

---

## 15. 测试与调试

### 15.1 后端调试

- **API 文档**：<http://localhost:8000/docs>（Swagger UI）
- **日志级别**：`LOG_LEVEL` 环境变量 + `main.py::logging.basicConfig(level=logging.INFO)`
- **WeChat 日志**：必须有 `logging.basicConfig(level=logging.INFO)` 否则 `wechat.*` 日志不输出
- **Langfuse 追踪**：启用后可在 Langfuse UI 查看 Agent 执行链路（v3 SDK 自动从 env 读）
- **沙箱诊断**：`script_runner.get_script_runtime_stats()` 返回当前 active/pending 脚本数

### 15.2 前端调试

- **Vite HMR**：`npm run dev` 支持热模块替换
- **React DevTools**：浏览器安装
- **Network**：检查 SSE 连接（`Accept: text/event-stream`）和 API 请求

### 15.3 常见调试场景

| 症状 | 排查方向 |
|---|---|
| SSE 不通 | Vite proxy 配置、Accept header、Express 代理 buffer |
| Agent 卡死 | `POST /api/chat/stop`、查后端日志、`get_script_runtime_stats()` |
| 文件操作报错 | `path_security` 日志、`safe_join` 边界 |
| 微信消息不送达 | `wechat.*` 日志、iLink 连接状态、`context_token` 是否过期 |
| 微信看到 `<<FILE:>>` 字面 | `delivery.py::extract_media_tags` 是否被调用 |
| 定时任务不执行 | `scheduler.py` 30s 循环日志、`tz_offset_hours` 字段 |
| Inbox 不触发 WeChat | `_main_loop` 是否注入、Admin WeChat 是否已连接 |
| 脚本"队列繁忙" | `SCRIPT_CONCURRENCY` 环境变量 + `SCRIPT_QUEUE_TIMEOUT` |
| Tauri 启动报 pycryptodome | `\\?\` 扩展前缀（见 §12.5.1） |

---

## 16. 扩展开发

### 16.1 添加新工具

1. 在 `app/services/tools.py` 中用 `@tool` 装饰器定义工厂函数
2. 在 `agent.py::create_user_agent` 注入到 Admin Agent 工具集
3. 如需 Consumer 支持，在 `consumer_agent.py` 中按 capability 条件注入
4. 如需 channel 区分（如 `send_message`），在 `consumer_agent.create_consumer_agent` 中按 `channel` 参数注入
5. 在 `tools.py::CAPABILITY_PROMPTS` 中添加对应的能力提示词
6. 如需 Subagent 可调用，加入 `subagents.py::SHARED_TOOL_NAMES` 或 `MEMORY_TOOL_NAMES`

### 16.2 添加新路由

1. 在 `app/routes/` 下创建新模块，使用 `APIRouter`
2. 在 `app/main.py` 中注册 `app.include_router(...)`
3. Admin 路由：`Depends(get_current_user)`；Consumer 路由：`Depends(get_service_context)`

### 16.3 添加新页面（前端）

1. 在 `frontend/src/pages/` 下创建页面组件
2. 在 `frontend/src/router/index.tsx` 中添加 `<Route>`
3. 如需侧栏导航项，在 `SettingsLayout` 的 `settingsNav` 数组中添加
4. 用 `ErrorBoundary` 包裹（参考 `<ErrorBoundary scope="settings">`）

### 16.4 添加新 API 调用

1. 在 `frontend/src/services/api.ts` 中添加 typed 函数
2. 在 `frontend/src/types/index.ts` 中添加类型定义

### 16.5 添加新 Subagent

1. 修改 `app/services/subagents.py::DEFAULT_SUBAGENTS`（默认） 或通过 `/api/subagents` API CRUD
2. 工具列表在 `SHARED_TOOL_NAMES + MEMORY_TOOL_NAMES` 中选择
3. 前端 `SubagentManager.tsx` 自动适配（从 `GET /api/subagents` 取 `available_tools`）

### 16.6 添加新主题

1. 在 `frontend/src/styles/themes.css` 中复制一个 `[data-theme]` 块并调整变量
2. 在 `themeContext.tsx` 中添加 `THEMES` 项 + Antd `ThemeConfig`
3. 在 `AppLayout.tsx` 侧栏底部主题切换按钮中加入新主题

### 16.7 添加新 Tauri Command

1. 在 `tauri-launcher/src-tauri/src/lib.rs` 中用 `#[tauri::command]` 定义函数
2. 在 `invoke_handler!` macro 中注册
3. 在 `dist/index.html` 中通过 `window.__TAURI__.invoke('cmd_name', { args })` 调用
4. 重新 `npx tauri build`

---

## 17. 开发规范清单

- [ ] 所有 Python 导入使用 `app.*` 包路径
- [ ] **不在** `app/services/agent.py`、`consumer_agent.py`、`voice/router.py` 之外顶层导入 `deepagents`
- [ ] 避免循环导入：在函数内延迟导入（如 `clear_agent_cache` 在 `prompt.py`/`subagents.py`）
- [ ] Consumer 路由使用 `get_service_context` 依赖（**非** `get_current_user`）
- [ ] Consumer Agent 创建必须传 `channel` 参数（web / wechat / scheduler）
- [ ] 路径操作统一通过 `app.core.path_security.safe_join` / `ensure_within`
- [ ] 文件 I/O 显式 UTF-8（`encoding="utf-8"`）
- [ ] 永远通过 `api_config` 或 env 读取 API URL，**不硬编码**
- [ ] 所有需要 asyncio 的 sync tool 必须通过 `_main_loop` + `run_coroutine_threadsafe`
- [ ] Service `manage_scheduled_tasks` 仅能操作当前 `conversation_id` 的任务
- [ ] 前端图标使用 `@phosphor-icons/react`（**不再新增** `@ant-design/icons` 引用）
- [ ] 前端组件使用函数式 + Hooks（唯一例外：`ErrorBoundary`）
- [ ] API 调用通过 `services/api.ts` 统一管理
- [ ] 内联 `borderRadius` 使用 `var(--jf-radius-*)` 变量字符串
- [ ] CSS 颜色通过 `var(--jf-*)` 引用，硬编码仅在 `themes.css` 和 `themeContext.tsx`
- [ ] **改 admin chat 时同步检查 service-chat**：共享 `markdown.ts` 和 `StreamingMessage.tsx`
- [ ] 新加 SSE 事件类型：同时改 `streamContext.tsx`（admin）和 `streamHandler.ts`（service）
- [ ] **千万别为图省事在 `service-chat/` 复制一份 `markdown.ts`** — 这是历史 `<<FILE:>>` 媒体 bug 的根因
- [ ] Scheduler 任务创建时必须带 `tz_offset_hours: getTzOffset()`
- [ ] Tauri 路径必须经 `strip_win_extended_prefix` 剥离 `\\?\` 前缀

---
