<p align="center">
  <img src="frontend/public/media_resources/jellyfishlogo.png" width="120" alt="JellyfishBot Logo" />
</p>

<h1 align="center">JellyfishBot</h1>

<p align="center">
  <strong>Enterprise-grade AI Agent Platform built on deepagents + LangGraph</strong><br>
  <em>企业级 AI Agent 管理与分发平台，基于 deepagents + LangGraph 构建</em>
</p>

<p align="center">
  <a href="#english">English</a> ·
  <a href="#中文">中文</a>
</p>

---

<h2 id="english">🇬🇧 English</h2>

### Overview

JellyfishBot is a full-featured AI Agent management and delivery platform built on the **Admin + Consumer** two-tier architecture. Admins configure Agents (model, documents, scripts, System Prompt, Subagents), publish them as Services with API Keys for external consumers, and optionally connect via WeChat iLink for mobile conversations.

### ✨ Core Features

| Feature | Description |
|---------|-------------|
| **Multi-model** | Claude Opus/Sonnet/Haiku 4.x (w/ Thinking), GPT-5.x, GPT-4o, o3-mini |
| **SSE Streaming** | Thinking blocks, tool calls, and Subagent executions all streamed in real time |
| **HITL Approval** | File diff preview, Plan Mode review + edit before execution |
| **Multimodal** | Image paste/drag, voice input transcription, AI image/speech/video generation |
| **Virtual Filesystem** | Per-user isolated filesystem (Local or S3 backend) |
| **Script Sandbox** | Two-layer defense: AST static analysis + runtime path whitelist |
| **Service Publishing** | API Key auth, OpenAI-compatible interface, standalone Consumer chat page |
| **WeChat Integration** | iLink Bot protocol — bidirectional image/voice/video, dual-stack (Admin + Service) |
| **Scheduler** | cron / interval / once scheduling, script or Agent task types, WeChat push |
| **Soul Memory** | Long-term memory system — Memory Subagent writes soul notes, short-term injection |
| **Per-user venv** | Each Admin gets an isolated Python virtualenv for script execution |
| **Per-user API Keys** | AES-256-GCM encrypted keys stored per Admin, priority over env vars |
| **Realtime Voice** | OpenAI Realtime WebSocket S2S proxy |
| **Web Search** | CloudsWay / Tavily dual-provider search + web fetch |
| **Batch Processing** | Excel upload → bulk Agent execution → result download |
| **Multi-theme** | Dark (default) / Cyber Ocean / Terminal (phosphor-green CRT) |
| **Desktop App** | Tauri v2 launcher — double-click to start, no CLI needed |
| **Observability** | Langfuse / LangSmith tracing integration |

### 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Clients                                                     │
│  Admin SPA (React 19)  │  Service Page (/s/{id})  │  WeChat │
│  Tauri Desktop App     │  WeChat Scan (/wc/{id})   │  iLink  │
├─────────────────────────────────────────────────────────────┤
│  Nginx (:80) — SSL termination + reverse proxy               │
│  → Express (:3000) — static dist/ + /api proxy               │
│  → FastAPI (:8000) — core backend                            │
├─────────────────────────────────────────────────────────────┤
│  FastAPI  app/                                               │
│  routes/  ·  services/  ·  channels/wechat/  ·  storage/    │
│  core/    ·  schemas/   ·  voice/                            │
├─────────────────────────────────────────────────────────────┤
│  deepagents + LangGraph                                      │
│  Per-user Agent instances · AsyncSqlite Checkpoint           │
│  FilesystemBackend (Local / S3) · Subagent orchestration     │
└─────────────────────────────────────────────────────────────┘

External:
  WeChat iLink ⇄ app/channels/wechat/
  OpenAI Realtime ⇄ app/voice/router.py
  CloudsWay / Tavily ⇄ app/services/web_tools.py
```

### 🚀 Quick Start

#### Option 1 — Desktop App (Recommended for non-developers)

1. Download the installer from GitHub Releases:
   - Windows: `JellyfishBot-x.y.z-x64.exe`
   - macOS Apple Silicon: `JellyfishBot-x.y.z-aarch64.dmg`
   - macOS Intel: `JellyfishBot-x.y.z-x64.dmg`
2. Install and open JellyfishBot. The embedded Python 3.12 + Node.js 20 runtime starts automatically.
3. Enter your LLM API Key on the **Console** page and click **Test** to verify.
4. Press the central **START** button. The browser opens automatically at `http://localhost:3000`.
5. Use a registration code from the **Registration Keys** tab to create your account.

#### Option 2 — Command Line

**Prerequisites:** Python 3.11+, Node.js 20+, at least one LLM API Key.

```bash
git clone https://github.com/LiUshin/semi-deep-agent.git
cd semi-deep-agent

# Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and/or OPENAI_API_KEY

# Generate registration codes
python generate_keys.py

# Install dependencies
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd frontend && npm install && cd ..

# Start everything (recommended)
python launcher.py

# Or start manually:
# Terminal 1: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Terminal 2: cd frontend && npm run dev
```

Open `http://localhost:3000`, register with a generated code, and start chatting.

#### Option 3 — Docker

```bash
cp .env.example .env        # fill in API Keys
python generate_keys.py     # generate registration codes
docker compose up -d --build
# Open http://localhost (Nginx port 80)
```

Docker multi-stage build: Node.js 20 compiles the React frontend, then Python 3.11 + Node.js 20 run it alongside FastAPI.

### 🔑 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | One of these | Anthropic API key (Claude models) |
| `OPENAI_API_KEY` | One of these | OpenAI API key (GPT + media generation) |
| `ANTHROPIC_BASE_URL` | No | Custom Anthropic endpoint |
| `OPENAI_BASE_URL` | No | Custom OpenAI endpoint |
| `IMAGE_API_KEY` / `IMAGE_BASE_URL` | No | Override for image generation |
| `TTS_API_KEY` / `TTS_BASE_URL` | No | Override for text-to-speech |
| `VIDEO_API_KEY` / `VIDEO_BASE_URL` | No | Override for video generation |
| `S2S_API_KEY` / `S2S_BASE_URL` | No | Override for realtime voice |
| `STT_API_KEY` / `STT_BASE_URL` | No | Override for speech-to-text |
| `CLOUDSWAY_SEARCH_KEY` | No | CloudsWay search API (preferred) |
| `TAVILY_API_KEY` | No | Tavily search API (fallback) |
| `STORAGE_BACKEND` | No | `local` (default) or `s3` |
| `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` / ... | No | S3-compatible storage |
| `ENCRYPTION_KEY` | No | AES-256-GCM master key for per-user API Keys (auto-generated if omitted) |
| `SCRIPT_CONCURRENCY` | No | Max concurrent sandboxed scripts (default: 4) |
| `LANGFUSE_*` / `LANGCHAIN_*` | No | Observability |

> **Tip:** From v2.x, every Admin can configure their own API Keys in **Settings → General → API Keys** (AES-256-GCM encrypted). These take priority over environment variables.

Full list: see [.env.example](.env.example).

### 📦 Supported Models

**Anthropic** (`ANTHROPIC_API_KEY`): Claude Opus/Sonnet/Haiku 4.x, optional Thinking variants  
**OpenAI** (`OPENAI_API_KEY`): GPT-5.4/5.3/5.2, GPT-4o, o3-mini, gpt-image-2, tts-1/tts-1-hd, Sora 2, Whisper, gpt-4o-realtime-preview

Model IDs use `provider:model-id` format (e.g., `anthropic:claude-sonnet-4-6-20250929`).

### 🗂️ Two-Tier Architecture

```
Admin Layer
  ├── Full Web UI: chat, files, scripts, settings, Service management
  ├── publish Services → generate sk-svc-... API Keys
  ├── WeChat self-onboarding (/api/admin/wechat/*)
  ├── Scheduler (Admin tasks + Service tasks)
  └── Inbox (notifications from Consumer Agents → auto WeChat forward)

Consumer Layer
  ├── Authenticate via sk-svc-... Bearer Token
  ├── Isolated filesystem: users/{admin}/services/{svc}/conversations/{conv}/
  ├── POST /api/v1/chat        (custom SSE)
  ├── POST /api/v1/chat/completions  (OpenAI-compatible)
  ├── GET  /s/{service_id}     (standalone React chat page)
  └── GET  /wc/{service_id}    (WeChat scan landing page)
```

### 📁 User Data Layout

```
users/
└── {user_id}/
    ├── filesystem/          # Agent virtual filesystem (Local or S3)
    │   ├── docs/            # Reference documents
    │   ├── scripts/         # Python scripts (sandboxed)
    │   ├── generated/       # AI-generated files (images/audio/video)
    │   └── soul/            # Soul memory notes (if enabled)
    ├── conversations/       # Admin chat history JSON
    ├── services/{svc_id}/
    │   ├── config.json      # Model, capabilities, allowed docs/scripts
    │   ├── keys.json        # Hashed API Keys
    │   ├── wechat_sessions.json
    │   ├── conversations/   # Consumer conversations
    │   └── tasks/           # Service scheduled tasks
    ├── tasks/               # Admin scheduled tasks
    ├── inbox/               # Inbox messages from Service Agents
    ├── soul/config.json     # Soul system config (app-layer, not agent-visible)
    ├── venv/                # Per-user Python virtualenv
    ├── api_keys.json        # AES-256-GCM encrypted per-user API Keys
    └── admin_wechat_session.json  # Persistent Admin WeChat session

data/
└── checkpoints.db           # SQLite Agent state (LangGraph)
config/
└── registration_keys.json   # One-time registration codes
```

### 📚 Documentation

| Document | Description |
|----------|-------------|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Full user guide (all features, WeChat, FAQ) |
| [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Architecture, APIs, extension guide |
| [docs/filesystem-architecture.md](docs/filesystem-architecture.md) | Filesystem layout, JSON schemas, message flows |
| [docs/wechat-integration-guide.md](docs/wechat-integration-guide.md) | iLink WeChat integration deep-dive |

### License

MIT

---

<h2 id="中文">🇨🇳 中文</h2>

### 概览

JellyfishBot 是一个功能完整的 AI Agent 管理与分发平台，采用 **Admin 管理 + Consumer 消费** 两层架构。管理员可以配置 Agent（模型、文档、脚本、System Prompt、子代理），发布为 Service 并生成 API Key 供外部消费者调用，也可通过微信 iLink 渠道扫码接入，随时随地在微信中与 Agent 对话。

### ✨ 核心特性

| 功能 | 说明 |
|------|------|
| **多模型支持** | Claude Opus/Sonnet/Haiku 4.x（含 Thinking 变体）、GPT-5.x、GPT-4o、o3-mini |
| **SSE 流式对话** | Thinking 块、工具调用、子代理执行全程流式展示（60fps 节流刷新） |
| **HITL 审批** | 文件操作 diff 预览、Plan Mode 审批与编辑后才执行 |
| **多模态** | 图片附件（粘贴/拖拽）、语音输入转写、AI 图片/语音/视频生成 |
| **虚拟文件系统** | per-user 隔离，支持本地磁盘或 S3 兼容后端 |
| **脚本沙箱** | 双层防护：AST 静态分析 + 运行时路径白名单沙箱 |
| **Service 发布** | API Key 认证、OpenAI 兼容接口、独立 Consumer 聊天页 |
| **微信集成** | iLink Bot 协议，双向图片/语音/视频，双栈（Admin 自接入 + Service 渠道） |
| **定时任务** | cron / interval / once 调度，脚本或 Agent 任务，支持微信推送 |
| **Soul 记忆系统** | 长期记忆：Memory Subagent 自主写入 soul 笔记，短期记忆自动注入 prompt |
| **per-user venv** | 每个 Admin 独立 Python 虚拟环境，可自由安装第三方包 |
| **per-user API Key** | AES-256-GCM 加密存储，优先级高于环境变量 |
| **实时语音（S2S）** | OpenAI Realtime WebSocket 代理 |
| **联网工具** | CloudsWay / Tavily 双 provider 搜索 + 网页抓取 |
| **批量处理** | Excel 上传 → 批量 Agent 执行 → 结果下载 |
| **多主题** | 暗色（默认）/ 青蓝 / 磷绿 CRT 终端三套主题 |
| **桌面 App** | Tauri v2 启动器，双击即用，自带 Python + Node.js 运行时 |
| **可观测性** | Langfuse / LangSmith 追踪集成 |

### 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  客户端                                                      │
│  Admin SPA (React 19)  │ Service 聊天页 /s/{id} │ 微信      │
│  Tauri 桌面 App        │ 微信中间页 /wc/{id}    │ iLink     │
├─────────────────────────────────────────────────────────────┤
│  Nginx (:80) — SSL 终止 + 反向代理                           │
│  → Express (:3000) — 静态资源 dist/ + /api 代理              │
│  → FastAPI (:8000) — 核心后端                                │
├─────────────────────────────────────────────────────────────┤
│  FastAPI  app/                                               │
│  routes/（路由）· services/（业务）· channels/wechat/（微信）│
│  storage/（存储）· core/（认证/加密/路径安全）· voice/（S2S）│
├─────────────────────────────────────────────────────────────┤
│  deepagents + LangGraph 引擎                                 │
│  per-user Agent 实例 · AsyncSqlite 状态持久化                │
│  FilesystemBackend（Local / S3）· Subagent 调度               │
└─────────────────────────────────────────────────────────────┘

外部协议：
  微信 iLink ⇄ app/channels/wechat/
  OpenAI Realtime ⇄ app/voice/router.py
  CloudsWay / Tavily ⇄ app/services/web_tools.py
```

### 🚀 快速开始

#### 方式一：桌面 App（推荐非开发者使用）

1. 从 GitHub Release 下载安装包：
   - Windows：`JellyfishBot-x.y.z-x64.exe`
   - macOS Apple Silicon：`JellyfishBot-x.y.z-aarch64.dmg`
   - macOS Intel：`JellyfishBot-x.y.z-x64.dmg`
2. 安装并打开 JellyfishBot，内置 Python 3.12 + Node.js 20 自动启动。
3. 在 **控制台** 页填入 LLM API Key，点击 **测试连接** 验证。
4. 点击中央 **START** 按钮，浏览器自动打开 `http://localhost:3000`。
5. 在 **注册码管理** Tab 生成注册码，注册账号即可使用。

#### 方式二：命令行（开发者）

**前置条件**：Python 3.11+、Node.js 20+、至少一个 LLM API Key。

```bash
git clone https://github.com/LiUshin/semi-deep-agent.git
cd semi-deep-agent

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY 或 OPENAI_API_KEY

# 生成注册码（首次部署）
python generate_keys.py

# 安装依赖
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux/macOS
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 一键启动（推荐）
python launcher.py

# 手动启动（调试用）：
# 终端 1: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 终端 2: cd frontend && npm run dev
```

打开 `http://localhost:3000`，使用注册码注册账号后即可开始使用。

> **快捷脚本**：`./start_local.sh`（Mac/Linux）或 `start_local.bat`（Windows 双击）。

#### 方式三：Docker

```bash
cp .env.example .env           # 填入 API Key 等必要配置
python generate_keys.py        # 生成注册码
docker compose up -d --build   # 构建并启动
# 访问 http://localhost（Nginx 端口 80）
```

Docker 采用多阶段构建：Stage 1 用 Node.js 20 编译前端，Stage 2 用 Python 3.11 + Node.js 20 运行后端和静态文件服务。

```bash
docker compose logs -f           # 查看日志
docker compose down              # 停止服务
```

### 🔑 环境变量说明

| 变量 | 必需 | 说明 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 二选一 | Anthropic API 密钥（Claude 系列） |
| `OPENAI_API_KEY` | 二选一 | OpenAI API 密钥（GPT + 多媒体生成） |
| `ANTHROPIC_BASE_URL` | 否 | 自定义 Anthropic 端点 |
| `OPENAI_BASE_URL` | 否 | 自定义 OpenAI 端点 |
| `IMAGE_API_KEY` / `IMAGE_BASE_URL` | 否 | 图片生成能力覆盖 |
| `TTS_API_KEY` / `TTS_BASE_URL` | 否 | TTS 能力覆盖 |
| `VIDEO_API_KEY` / `VIDEO_BASE_URL` | 否 | 视频生成能力覆盖 |
| `S2S_API_KEY` / `S2S_BASE_URL` | 否 | 实时语音覆盖 |
| `STT_API_KEY` / `STT_BASE_URL` | 否 | 语音转写覆盖 |
| `CLOUDSWAY_SEARCH_KEY` | 否 | CloudsWay 搜索（优先） |
| `TAVILY_API_KEY` | 否 | Tavily 搜索（备选） |
| `STORAGE_BACKEND` | 否 | `local`（默认）或 `s3` |
| `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` / ... | 否 | S3 兼容存储配置 |
| `ENCRYPTION_KEY` | 否 | per-user API Key 的 AES-256-GCM master key（不设则自动生成 `data/encryption.key`） |
| `SCRIPT_CONCURRENCY` | 否 | 全局并发脚本数（默认 4） |
| `LANGFUSE_*` / `LANGCHAIN_*` | 否 | 可观测性配置 |

> **提示**：从 v2.x 起，每个 Admin 可在 **设置 → 通用 → API Keys** 中配置自己的 Key（AES-256-GCM 加密存储），**优先级高于环境变量**。

完整变量列表见 [.env.example](.env.example)。

### 📦 支持的模型

**Anthropic**（`ANTHROPIC_API_KEY`）：Claude Opus/Sonnet/Haiku 4.x，可选 Thinking 变体  
**OpenAI**（`OPENAI_API_KEY`）：GPT-5.4/5.3/5.2、GPT-4o、o3-mini、gpt-image-2、tts-1/tts-1-hd、Sora 2、Whisper、gpt-4o-realtime-preview

模型 ID 格式：`provider:model-id`（如 `anthropic:claude-sonnet-4-6-20250929`）。

### 🗂️ 两层架构（Admin / Consumer）

```
Admin 层（管理员）
  ├── 完整 Web UI：聊天、文件、脚本、设置、Service 管理、定时任务、微信
  ├── 发布 Service → 生成 sk-svc-... API Key
  ├── 微信自接入（/api/admin/wechat/*）
  ├── 调度器（Admin 任务 + Service 任务）
  └── 收件箱（接收 Service Agent 通知，自动评估转发到微信）

Consumer 层（消费者）
  ├── 通过 sk-svc-... Bearer Token 认证
  ├── 文件系统隔离：users/{admin}/services/{svc}/conversations/{conv}/
  ├── POST /api/v1/chat                （自定义 SSE 流式）
  ├── POST /api/v1/chat/completions    （OpenAI 兼容）
  ├── GET  /s/{service_id}             （独立 React 聊天页）
  └── GET  /wc/{service_id}            （微信扫码中间页）
```

### 📁 用户数据目录

```
users/
└── {user_id}/
    ├── filesystem/          # Agent 虚拟文件系统（Local 或 S3）
    │   ├── docs/            # 文档目录（Agent 可读）
    │   ├── scripts/         # Python 脚本（沙箱执行）
    │   ├── generated/       # AI 生成文件（图片/音频/视频）
    │   └── soul/            # Soul 记忆笔记（启用后可见）
    ├── conversations/       # Admin 对话历史 JSON
    ├── services/{svc_id}/
    │   ├── config.json      # 模型、能力、允许的文档/脚本
    │   ├── keys.json        # API Key（sha256 哈希）
    │   ├── wechat_sessions.json  # 微信会话状态
    │   ├── conversations/   # Consumer 对话
    │   └── tasks/           # Service 定时任务
    ├── tasks/               # Admin 定时任务
    ├── inbox/               # 收件箱消息（来自 Service Agent）
    ├── soul/config.json     # Soul 配置（应用层，Agent 不可直接访问）
    ├── venv/                # per-user Python 虚拟环境
    ├── api_keys.json        # AES-256-GCM 加密的 per-user API Key
    └── admin_wechat_session.json  # Admin 微信会话持久化

data/
└── checkpoints.db           # SQLite Agent 状态持久化（LangGraph）
config/
└── registration_keys.json   # 一次性注册码
```

### 📚 项目文档

| 文档 | 说明 |
|------|------|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 完整用户指南（全功能说明、微信接入、FAQ） |
| [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | 架构设计、API 参考、扩展开发指南 |
| [docs/filesystem-architecture.md](docs/filesystem-architecture.md) | 文件系统布局、JSON Schema、6 大消息流时序图 |
| [docs/wechat-integration-guide.md](docs/wechat-integration-guide.md) | iLink 微信集成实战（iLink 协议踩坑记录） |

### 可选：Langfuse 可观测性（自托管）

```bash
cd langfuse
cp env.example .env
# 修改密码和密钥
docker compose up -d
# 然后在项目根 .env 中配置 LANGFUSE_* 变量
```

### License

MIT
