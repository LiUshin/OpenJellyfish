# JellyfishBot Developer Guide

> Complete technical documentation for developers: architecture, backend/frontend modules, security mechanisms, extension patterns, and deployment.
> Last updated: 2026-04-21 (synchronized with `.cursorrules` and `docs/filesystem-architecture.md`)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Technology Stack](#2-technology-stack)
3. [Development Environment Setup](#3-development-environment-setup)
4. [Backend Architecture](#4-backend-architecture)
   - [4.1 Directory Structure](#41-directory-structure)
   - [4.2 FastAPI Route Reference](#42-fastapi-route-reference)
   - [4.3 Dependency Injection & Authentication](#43-dependency-injection--authentication)
   - [4.4 Agent Engine](#44-agent-engine)
   - [4.5 Tool System](#45-tool-system)
   - [4.6 Subagent / Memory / Soul](#46-subagent--memory--soul)
   - [4.7 Storage Layer](#47-storage-layer)
   - [4.8 Security Architecture](#48-security-architecture)
   - [4.9 Per-Admin Python venv](#49-per-admin-python-venv)
   - [4.10 Per-Admin API Keys](#410-per-admin-api-keys)
5. [Frontend Architecture](#5-frontend-architecture)
   - [5.1 Directory Structure](#51-directory-structure)
   - [5.2 Routing System](#52-routing-system)
   - [5.3 State Management](#53-state-management)
   - [5.4 API Client](#54-api-client)
   - [5.5 SSE Streaming](#55-sse-streaming)
   - [5.6 Chat Component Tree & Shared Rendering](#56-chat-component-tree--shared-rendering)
   - [5.7 File Preview Panel](#57-file-preview-panel)
   - [5.8 Error Boundary](#58-error-boundary)
   - [5.9 Design System & Multi-Theme](#59-design-system--multi-theme)
6. [Service & Consumer Two-Tier Layer](#6-service--consumer-two-tier-layer)
7. [WeChat Integration](#7-wechat-integration)
8. [Scheduler (Scheduled Tasks)](#8-scheduler-scheduled-tasks)
9. [Inbox](#9-inbox)
10. [Realtime Voice (S2S WebSocket)](#10-realtime-voice-s2s-websocket)
11. [API Reference](#11-api-reference)
12. [Tauri Desktop Launcher](#12-tauri-desktop-launcher)
13. [Cross-platform Launcher (launcher.py)](#13-cross-platform-launcher-launcherpy)
14. [Docker Deployment](#14-docker-deployment)
15. [Testing & Debugging](#15-testing--debugging)
16. [Extension Development](#16-extension-development)
17. [Development Checklist](#17-development-checklist)

> Deep references: `docs/filesystem-architecture.md` (filesystem/JSON Schema), `docs/wechat-integration-guide.md` (WeChat dual-stack details).

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Clients                                    │
│  ┌──────────────┐ ┌────────────┐ ┌─────────┐ ┌────────────────┐ │
│  │  Admin SPA   │ │  Service   │ │ WeChat  │ │ Tauri Desktop  │ │
│  │  React 19    │ │ /s/{sid}   │ │ /wc/..  │ │ (Rust + WV)    │ │
│  └──────┬───────┘ └─────┬──────┘ └────┬────┘ └────────┬───────┘ │
└─────────┼───────────────┼─────────────┼───────────────┼─────────┘
          │ /api/*        │ /api/v1/*   │ iLink         │ launcher.py
          ▼               ▼             ▼               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Nginx (:80) — SSL termination + reverse proxy                    │
│  → Express (:3000) — static files (dist/) + /api proxy            │
│  → FastAPI (:8000) — core backend                                  │
├──────────────────────────────────────────────────────────────────┤
│  FastAPI Application (app/)                                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ routes/  │ │ services/│ │ channels/│ │ storage/ │ │ voice/ │ │
│  │ routing  │ │ business │ │ wechat   │ │ abstraction│S2S WS │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
│       └─────┬──────┴───────┬────┘            │           │      │
│             ▼              ▼                  ▼           ▼      │
│  ┌─────────────────────────────┐  ┌──────────────────┐ ┌──────┐ │
│  │  deepagents + LangGraph     │  │  Local FS / S3   │ │ S2S  │ │
│  │  Agent runtime + AsyncSqlite│  │  + AES key enc.  │ │ Proxy│ │
│  └─────────────────────────────┘  └──────────────────┘ └──────┘ │
└──────────────────────────────────────────────────────────────────┘

External protocols:
  • WeChat iLink Bot: app/channels/wechat/ ⇄ ilinkai.weixin.qq.com
  • OpenAI Realtime:  app/voice/router.py ⇄ wss://api.openai.com/.../realtime
  • CloudsWay / Tavily: app/services/web_tools.py (web search)
```

### Layered Design

| Layer | Responsibility | Directory |
|-------|---------------|-----------|
| **Route Layer** | HTTP request handling, parameter validation, SSE streaming | `app/routes/` |
| **Business Layer** | Agent creation/cache, tools, conversations, scheduling, inbox | `app/services/` |
| **Channel Layer** | WeChat iLink protocol (Service + Admin dual-stack) | `app/channels/wechat/` |
| **Storage Layer** | Local / S3 file storage abstraction | `app/storage/` |
| **Core Layer** | Auth, config, path security, encryption, Langfuse | `app/core/` |
| **Voice Layer** | S2S WebSocket proxy (OpenAI Realtime) | `app/voice/` |

### Three Runtime Environments

| Environment | Entry Point | Use Case |
|-------------|------------|----------|
| **Local Dev** | `python launcher.py --dev` or `uvicorn` + `vite dev` | Development, hot reload |
| **Docker** | `docker compose up -d --build` | Server / team deployment |
| **Desktop App** | `tauri-launcher/` compiled to `.dmg` / `.exe` | End users, double-click launch |

---

## 2. Technology Stack

### Backend

| Technology | Version | Purpose |
|-----------|---------|---------|
| Python | 3.11+ | Runtime (Tauri bundles 3.12.7) |
| FastAPI | 0.104+ | Web framework |
| Uvicorn | 0.24+ | ASGI server |
| deepagents | 0.4.1+ | AI Agent framework (custom fork) |
| LangGraph | latest | Agent state graph + checkpointer |
| LangChain | latest | LLM abstraction layer |
| langchain-anthropic / openai | latest | Claude / GPT adapters |
| langgraph-checkpoint-sqlite | latest | State persistence (WAL mode) |
| Pydantic | 2.0+ | Data validation |
| boto3 / aioboto3 | latest | S3 storage (S3 mode only) |
| httpx | 0.27+ | Async HTTP client |
| bcrypt | 4.0+ | Password hashing (sha256 fallback) |
| pycryptodome | 3.20+ | AES-128-ECB (WeChat media) + AES-256-GCM (API Keys) |
| croniter | latest | Cron expressions (per-user timezone) |
| silk-python | 0.2+ | WeChat voice SILK decoding |
| websockets | 11+ | S2S WebSocket proxy |

### Frontend

| Technology | Version | Purpose |
|-----------|---------|---------|
| React | 19 | UI framework |
| TypeScript | 5.7 | Type safety (strict) |
| Vite | 6 | Build tool (multi-entry) |
| Ant Design | 5 | UI component library (dynamic theming) |
| React Router | 7 | Routing (BrowserRouter) |
| Phosphor Icons | 2.x | Icons (**sole icon library**) |
| marked | 15+ | Markdown rendering |
| DOMPurify | 3.2+ | XSS protection |
| highlight.js | 11 | Code highlighting (17 languages on demand) |
| dayjs | latest | Time formatting |

### Desktop

| Technology | Purpose |
|-----------|---------|
| Tauri | v2, Rust + WebView |
| Rust crates | tauri 2 / reqwest / tokio / serde / open / libc |
| Embedded runtimes | python-build-standalone 3.12.7 + Node.js 20.18.0 |

---

## 3. Development Environment Setup

### 3.1 Backend

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env — set at least ANTHROPIC_API_KEY or OPENAI_API_KEY

# Generate registration codes (first deploy)
python generate_keys.py

# Start (dev mode with hot reload)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API documentation: <http://localhost:8000/docs> (Swagger UI).

> **Tip**: The cross-platform launcher `python launcher.py [--dev]` in the project root automatically detects old instances, port conflicts, and manages both processes. See §13 for details.

### 3.2 Frontend

```bash
cd frontend
npm install

npm run dev       # Vite dev server → http://localhost:3000 (recommended)
npm run build     # Production build → dist/
npm run preview   # Preview production build
npm run legacy    # Legacy Express server (serves static files from dist/)
```

**Vite dev proxy rules** (`vite.config.ts`):

| Path | Target | Notes |
|------|--------|-------|
| `/api/*` | `localhost:8000` | All FastAPI APIs |
| `/s/*` | `localhost:8000` | Consumer chat page |
| `/wc/*` | `localhost:8000` | WeChat landing page |
| `/media_resources/*` | `localhost:8000` | Logo/icon static resources |

SSE requests automatically set `Accept: text/event-stream` and disable proxy buffering.

**Vite multi-entry**: `rollupOptions.input` declares both `main` and `service-chat` entries, building to `dist/index.html` and `dist/service-chat.html` respectively.

### 3.3 One-click Start

```bash
# Windows
start_local.bat
# Linux/macOS
./start_local.sh
# Both equivalent to: python launcher.py
```

---

## 4. Backend Architecture

### 4.1 Directory Structure

```
app/
├── main.py                    # FastAPI app entry, router registration, startup/shutdown hooks
├── deps.py                    # get_current_user / get_service_context dependencies
│
├── core/
│   ├── settings.py            # Path constants (ROOT_DIR, USERS_DIR, DATA_DIR)
│   ├── security.py            # User auth (register/login/JWT) + _create_user_dirs
│   ├── api_config.py          # Route API Key + base_url by capability (supports user_id)
│   ├── user_api_keys.py       # AES-256-GCM encrypted per-admin key storage
│   ├── encryption.py          # AES-GCM master key (data/encryption.key)
│   ├── path_security.py       # safe_join / ensure_within (path traversal protection)
│   ├── fileutil.py            # File read/write utilities
│   └── observability.py       # Langfuse integration (v3 SDK)
│
├── schemas/
│   ├── requests.py            # Admin request models
│   └── service.py             # Service / Consumer request/config models
│
├── services/
│   ├── agent.py               # Admin Agent factory + model resolution + cache
│   ├── consumer_agent.py      # Consumer Agent factory (channel-aware)
│   ├── tools.py               # @tool factories + CAPABILITY_PROMPTS
│   ├── ai_tools.py            # Multimedia generation (image/tts/video) internals
│   ├── web_tools.py           # CloudsWay / Tavily dual provider
│   ├── script_runner.py       # Script execution (subprocess + AST check + semaphore queue)
│   ├── _sandbox_wrapper.py    # Runtime I/O sandbox (monkey-patch)
│   ├── conversations.py       # Admin conversation persistence + attachments
│   ├── prompt.py              # System Prompt version management + capability prompts
│   ├── preferences.py         # User timezone/UI preferences
│   ├── subagents.py           # Subagent config + DEFAULT_SUBAGENTS
│   ├── memory_tools.py        # Memory Subagent tools + Soul config + short-term memory
│   ├── published.py           # Service CRUD + API Key + Consumer sessions
│   ├── scheduler.py           # Scheduled tasks (Admin + Service dual-track)
│   ├── inbox.py               # Inbox (contact_admin → Inbox Agent → WeChat forward)
│   └── venv_manager.py        # Per-user Python virtual environments
│
├── routes/                    # See §4.2 for full route table
│   ├── auth.py
│   ├── conversations.py
│   ├── chat.py
│   ├── files.py
│   ├── scripts.py
│   ├── models.py
│   ├── settings_routes.py     # system_prompt / user_profile / subagents / api_keys / soul / capability_prompts
│   ├── batch.py
│   ├── services.py
│   ├── consumer.py            # /api/v1/* (Consumer external API)
│   ├── consumer_ui.py         # /s/{service_id} (Consumer chat page)
│   ├── scheduler.py
│   ├── inbox.py
│   └── wechat_ui.py           # /wc/{service_id} (WeChat scan landing page)
│
├── channels/wechat/
│   ├── client.py              # iLink protocol client (getconfig/getupdates/sendmessage/cdn)
│   ├── bridge.py              # Service Consumer Bridge (multimodal + send_message interception)
│   ├── admin_bridge.py        # Admin self-onboarding Bridge (independent logic)
│   ├── admin_router.py        # /api/admin/wechat/* routes
│   ├── router.py              # /api/wc/* public routes + Admin service channel management
│   ├── session_manager.py     # Session lifecycle + persistence + reconnection
│   ├── media.py               # AES-128-ECB + CDN upload/download
│   ├── delivery.py            # Unified delivery (with <<FILE:>> tag parsing)
│   └── rate_limiter.py        # Per-user/QR/global session rate limiting
│
├── storage/
│   ├── config.py              # S3Config + is_s3_mode
│   ├── base.py                # StorageService ABC
│   ├── local.py               # Local filesystem implementation
│   ├── s3.py                  # boto3 S3 implementation (MinIO/R2/OSS compatible)
│   ├── s3_backend.py          # deepagents BackendProtocol for S3
│   └── __init__.py            # get_storage_service / create_agent_backend / create_consumer_backend
│
└── voice/
    └── router.py              # WebSocket S2S proxy (OpenAI Realtime)
```

### 4.2 FastAPI Route Reference

`app/main.py` registers all routes — **approximately 70 routes total**.

#### Public / Auth

| Prefix | Route | Description |
|--------|-------|-------------|
| `/api/auth` | `register` / `login` / `me` | Register (requires code) + Login + JWT |

#### Admin (requires `get_current_user`)

| Prefix | Purpose |
|--------|---------|
| `/api/conversations` | Conversation CRUD + attachments |
| `/api/chat` | Primary SSE chat + `/resume` + `/stop` + `/streaming-status` |
| `/api/files` | File CRUD + upload + download + media |
| `/api/scripts` | Script execution + audio transcription |
| `/api/models` | Available models list |
| `/api/system-prompt` | Prompt content + version management |
| `/api/user-profile` | User profile + version management |
| `/api/subagents` | Subagent CRUD + `available_tools` |
| `/api/capability-prompts` | Per-user capability prompt overrides |
| `/api/soul/config` | Soul config (GET/PUT) |
| `/api/batch` | Excel batch execution |
| `/api/services` | Service CRUD + API Key |
| `/api/scheduler` | Admin scheduled tasks + run history |
| `/api/scheduler/services/...` | Service scheduled tasks |
| `/api/inbox` | Inbox (list/get/update/delete) |
| `/api/packages` | Per-user venv package management |
| `/api/settings/api-keys` | Per-admin API Keys (encrypted) |
| `/api/wc/...` | WeChat Service channel (QR + sessions + messages) |
| `/api/admin/wechat/*` | Admin WeChat self-onboarding |
| `/api/voice/...` | WebSocket S2S proxy |

#### Consumer (requires `get_service_context`)

| Route | Description |
|-------|-------------|
| `POST /api/v1/conversations` | Create conversation |
| `GET /api/v1/conversations/{id}` | Get conversation history |
| `GET /api/v1/conversations/{id}/files` | List generated files |
| `GET /api/v1/conversations/{id}/files/{path}` | Download generated file (query `?key=`) |
| `GET /api/v1/conversations/{id}/attachments/{path}` | Download user attachment |
| `POST /api/v1/chat` | Custom SSE chat |
| `POST /api/v1/chat/completions` | OpenAI-compatible (streaming + non-streaming) |

#### Static Pages

| Route | Description |
|-------|-------------|
| `GET /s/{service_id}` | Consumer standalone chat page (React multi-entry) |
| `GET /wc/{service_id}` | WeChat scan landing page (HTML template) |

### 4.3 Dependency Injection & Authentication

`app/deps.py` provides two core dependencies:

```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict
async def get_service_context(authorization: str = Header(...)) -> ServiceContext
```

| Dimension | Admin | Consumer |
|-----------|-------|---------|
| Credential | JWT Bearer Token | `sk-svc-...` Bearer Token |
| Storage | `users/users.json` (bcrypt + sha256 fallback) | `users/{admin}/services/{svc}/keys.json` (sha256 hashed) |
| Context | `{user_id, username}` | `ServiceContext{admin_id, service_id, service_config, key_id}` |
| Applicable routes | `/api/*` (except `/api/v1/*`) | `/api/v1/*` + `/s/{sid}` |

> Both dependency sets coexist in the same FastAPI instance. Route files must explicitly choose the right one — **Consumer routes must not use `get_current_user`**.

### 4.4 Agent Engine

#### 4.4.1 Admin Agent (`app/services/agent.py`)

- **Creation**: `create_user_agent(user_id, model_id, capabilities, plan_mode, ...)`
- **Cache**: per-(user_id, model_id, capabilities, plan_mode, channel) — avoids re-initialization
- **Checkpointer**: `AsyncSqliteSaver` → `data/checkpoints.db`
  - Executes `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` on startup to reduce lock contention with multiple bridges
- **Soul injection**: injects `memory_subagent` / `soul_edit` capability prompts based on `soul/config.json`
- **Cache invalidation**: `clear_agent_cache(user_id)` — called when prompt / subagent / API key changes

#### 4.4.2 Model Resolution

```python
# Model ID format
"anthropic:claude-sonnet-4-6-20250929"
"openai:gpt-5.4"

# Thinking models auto-configure extended_thinking
THINKING_MODEL_CONFIG = {
    "anthropic:claude-opus-4-6-20250929-thinking": {...},
    ...
}

# api_key + base_url resolved via api_config + user_api_keys
_resolve_model(model_id, user_id=None) → ChatModel
```

#### 4.4.3 Consumer Agent (`app/services/consumer_agent.py`)

- **Factory**: `create_consumer_agent(admin_id, service_id, conv_id, channel="web", ...)`
- **Channel-aware** (critical!):
  - `channel="web"`: Web /s/ + `/api/v1/*`, does **not** inject `send_message` (messages already flow through SSE)
  - `channel="wechat"`: injects `send_message`, results delivered by `delivery.py` to WeChat
  - `channel="scheduler"`: same as wechat, for scheduled task push
- **cache_key**: includes `::ch={channel}` (for non-web), preventing incorrect cache reuse
- **Restricted capabilities**: only injects tools based on Service `capabilities` list
- **Filesystem**: isolated to `users/{admin_id}/services/{svc}/conversations/{conv_id}/generated/`

#### 4.4.4 Message Timestamp Injection (Refactored 2026-04-13)

Exact time is no longer written to system prompt (daily cache would freeze time). Instead, `[YYYY-MM-DD HH:MM:SS]` is injected before each user message:

- `prompt.py::stamp_message(content, user_id)` handles both `str` and multimodal `list` formats
- Injection points: `chat.py` (Web), `admin_bridge.py` (Admin WeChat), `consumer.py` (Consumer × 3), `bridge.py` (Service WeChat)
- Consumer side uses `admin_id` to resolve timezone

### 4.5 Tool System

#### 4.5.1 Built-in Tools (`app/services/tools.py`)

| Tool | Description | Admin | Consumer Injection Condition |
|------|-------------|-------|------------------------------|
| `read_file` / `write_file` / `ls` / `glob` / `grep` | deepagents built-in | ✅ Always | ✅ Always |
| `edit_file` / `write_todos` | deepagents built-in | ✅ | ✅ |
| `task` | deepagents subagent dispatch | ✅ | ✅ |
| `run_python_script` | Sandboxed script execution | ✅ | ✅ |
| `web_search` / `web_fetch` | CloudsWay / Tavily | ✅ Always | `web` capability |
| `generate_image` / `generate_speech` / `generate_video` | OpenAI multimedia | By capability | By capability |
| `schedule_task` / `manage_scheduled_tasks` | Scheduled task CRUD | ✅ Always | `scheduler` capability |
| `publish_service_task` | Admin dispatches task to Service | ✅ Always | ❌ |
| `send_message` | Send to WeChat user | Injected in wechat channel | `humanchat` capability + non-web channel |
| `contact_admin` | Service notifies admin | ❌ | `humanchat` capability |
| `soul_list` / `soul_read` / `soul_write` / `soul_delete` | Soul file operations | Memory Subagent only (when `memory_subagent_enabled`) | ❌ |

#### 4.5.2 Multimedia Tools (`app/services/ai_tools.py`)

Calls OpenAI API under the hood:

- `generate_image`: `gpt-image-2`, output stored in `generated/images/`
- `generate_speech`: `tts-1` / `tts-1-hd` (6 voices), stored in `generated/audio/`
- `generate_video`: `Sora 2`, stored in `generated/videos/`

**Return convention**: Returns the message `"Generated ... Please display with <<FILE:/generated/xxx>> to the user"`. Both frontend `markdown.ts` and `delivery.py::extract_media_tags` recognize this tag.

#### 4.5.3 Web Tools (`app/services/web_tools.py`)

Dual provider, resolves key by user_id:

| Provider | Key Source | Notes |
|----------|-----------|-------|
| **CloudsWay** (preferred) | `CLOUDSWAY_SEARCH_KEY` | `CLOUDSWAY_SEARCH_URL` / `CLOUDSWAY_READ_URL` override endpoints |
| **Tavily** (fallback) | `TAVILY_API_KEY` | Automatic fallback |

#### 4.5.4 Capability Prompts

- `tools.py::CAPABILITY_PROMPTS` defines tool usage rules for each capability
- **Per-user overrides**: `users/{uid}/capability_prompts.json` (stores only overridden entries)
- API: `GET /api/capability-prompts` (with `is_custom`), `PUT /key`, `DELETE /key`
- Resolution: `prompt.py::get_resolved_capability_prompt(user_id, key)`

### 4.6 Subagent / Memory / Soul

#### 4.6.1 Subagent (`app/services/subagents.py`)

- **Storage**: `users/{uid}/subagents.json`
- **DEFAULT_SUBAGENTS**: includes one built-in `memory` subagent (cannot delete, but can disable)
- **Available tool pool** (`SHARED_TOOL_NAMES + MEMORY_TOOL_NAMES`):
  - General: `run_script` / `web_search` / `web_fetch` / `generate_image` / `generate_speech` / `generate_video` / `schedule_task` / `manage_scheduled_tasks` / `publish_service_task` / `send_message`
  - Memory: `list_conversations` / `read_conversation` / `list_service_conversations` / `read_service_conversation` / `read_inbox` / `soul_list` / `soul_read` / `soul_write` / `soul_delete`
- **On-demand creation**: `build_subagent_tools(subagent_config, user_id)` only instantiates tools listed in config
- **API**: `GET /api/subagents` (with `available_tools`) + CRUD

#### 4.6.2 Memory Subagent

- **Admin Memory Subagent**: default tools = 5 conversation/inbox read tools
  - When `memory_subagent_enabled=true`: adds 4 soul write tools
  - Conversation history is **read-only**, soul content is read-write
- **Consumer Memory Subagent**: only `read_my_conversation` (own conversations, read-only)
- **Factories**:
  - `create_admin_memory_tools(user_id)` → 5-9 tools
  - `create_consumer_memory_tools(admin_id, svc_id, conv_id)` → 1 tool

#### 4.6.3 Soul System (`app/services/memory_tools.py`)

```
users/{uid}/
├── soul/
│   └── config.json              # App-layer config (not agent-accessible)
└── filesystem/soul/             # Agent-readable/writable soul content (notes/personality)
    └── *.md / *.json
```

- **config.json fields**:
  - `memory_enabled`: enables short-term memory injection
  - `include_consumer_conversations`: whether Memory Subagent can read Consumer conversations
  - `max_recent_messages`: default 5
  - `memory_subagent_enabled`: whether Memory Subagent has soul write permission
  - `soul_edit_enabled`: whether main Agent can directly read/write Soul via `filesystem/soul/`
- **Path migration**: `sync_soul_symlink()` removes old symlinks and auto-migrates content to `filesystem/soul/` (avoids deepagents `Path.resolve()` following symlinks causing escape errors)
- **Capability prompts**: `memory_subagent` / `soul_edit` injected by toggle state in `CAPABILITY_PROMPTS`

#### 4.6.4 Short-term Memory Injection

- `scheduler.py::_run_*_agent_task`: reads recent N messages from conversation JSON, prepends to prompt
- `inbox.py::_trigger_inbox_agent`: injects 3 most recent inbox messages
- Source tagging:
  - Service scheduled task prompt header: `[System Instruction - From Admin]`
  - Inbox agent prompt header: `[System Instruction - Service Inbox Notification]`

### 4.7 Storage Layer

#### 4.7.1 Abstract Interface (`app/storage/base.py`)

```python
class StorageService(ABC):
    async def read_file(self, path: str) -> bytes
    async def write_file(self, path: str, content: bytes) -> None
    async def list_directory(self, path: str) -> list
    async def delete(self, path: str) -> None
    async def exists(self, path: str) -> bool
    async def move(self, src: str, dst: str) -> None
```

#### 4.7.2 Backend Selection

Controlled via `STORAGE_BACKEND` environment variable:

- `local` (default): `LocalStorageService` — `os.*` operations on local disk
- `s3`: `S3StorageService` — boto3 API (AWS S3 / MinIO / R2 / Alibaba OSS compatible)

#### 4.7.3 S3 Key Mapping

```
{prefix}/{user_id}/fs/{path}                              # Admin filesystem
{prefix}/{admin_id}/svc/{svc_id}/{conv_id}/gen/{path}     # Consumer generated files
```

> **Note**: JSON config files (users.json, conversations, service configs) **currently remain on local disk**. S3 mode only hosts the filesystem layer.

#### 4.7.4 Media Access Differences

| Mode | `/api/files/media` Behavior |
|------|----------------------------|
| `local` | `FileResponse` streams file directly |
| `s3` | Generates presigned URL, returns 302 redirect |

#### 4.7.5 Factory Functions (`app/storage/__init__.py`)

```python
get_storage_service() → StorageService
create_agent_backend(root_dir, user_id=None) → BackendProtocol  # Admin
create_consumer_backend(admin_id, svc_id, conv_id, gen_dir) → BackendProtocol
```

#### 4.7.6 Script Execution

- **Local mode**: reads scripts directly from `users/{uid}/filesystem/scripts/`
- **S3 mode**: temporarily downloads to local → subprocess execution → uploads results back to S3

> Full directory tree, JSON schemas, and 6 message flow sequences: see `docs/filesystem-architecture.md`.

### 4.8 Security Architecture

#### 4.8.1 Path Traversal Protection (`app/core/path_security.py`)

```python
safe_join(base, user_path) → str        # Safe path joining
ensure_within(path, root) → bool        # Verify path is within root directory
```

Implementation: `pathlib.Path.resolve()` + separator-aware boundary check (`startswith(root + os.sep)`). Be cautious of mixed-case paths on case-insensitive Windows file systems.

#### 4.8.2 Script Sandbox (Two-Layer Defense-in-Depth)

**Layer 1: AST Static Analysis** (`script_runner._check_script_safety`)

- Blocks dangerous modules: `subprocess` / `pathlib` / `ctypes` / `io` / `pickle` / `threading` / `posix` / `nt` / `_posixsubprocess`
- Blocks dangerous builtins: `exec` / `eval` / `getattr` / `setattr` / `globals`
- Blocks "absolutely dangerous" os functions: `system` / `popen` / `exec*` / `spawn*` / `fork` / `kill` / `chown` / `setuid` / `chroot` / `chdir`
- Blocks access to `__builtins__` / `__subclasses__` / `__globals__` / `__dict__` / `__mro__` / `__bases__`
- **Rejects `Subscript`/`Call` forms of function calls** (closes `os.__dict__['system'](...)` / `(lambda:...)()` attack vectors)
- File I/O functions (remove / rename / mkdir / listdir / chmod, etc.) are **intentionally not in AST blocklist** — they are intercepted at runtime by path whitelist

**Layer 2: Runtime Sandbox** (`_sandbox_wrapper.py`)

- Monkey-patches `builtins.open` / `io.open` / `os.listdir` / `os.scandir` / `os.walk` / `os.chdir` / `os.open` / `os.readlink` to enforce read permissions
- Write operations (remove / unlink / rmdir / rename / replace / mkdir / makedirs / chmod / chown / link / symlink / utime / truncate / mkfifo / mknod) go through `_check_write` path whitelist
- "Absolutely dangerous" functions (system / popen / exec* / spawn* / posix_spawn* / fork / forkpty / kill / killpg / chown / lchown / setuid / setgid / setres*id / chroot / pipe / pipe2 / dup / dup2) are **overwritten with `PermissionError`-raising functions** — even if attackers obtain references via `os.__dict__[name]` / `vars(os)[name]`, calls still raise errors

**Sandbox Permission Configuration**:

| Role | read | write |
|------|------|-------|
| **Admin** | `scripts/ + docs/` | `scripts/ + generated/` |
| **Consumer** | `scripts/ + docs/` (filtered by `allowed_scripts`) | conversation's own `generated/` |
| **Scheduled Task** | `task_config.permissions.read_dirs` | `task_config.permissions.write_dirs` |

**Auto-exempted sandbox read paths**:

- `_PYTHON_READ_ROOTS`: `sys.prefix` / `sys.base_prefix` / `sys.exec_prefix` / `site.getsitepackages()` — allows `import matplotlib`
- `_SYSTEM_READ_DIRS`: `/usr/share` / `/etc/fonts` / `/etc/ssl` / macOS font directories
- `_TEMP_DIR` (write): `tempfile.gettempdir()` — library cache writes
- `MPLCONFIGDIR` redirected to temp directory

**Resource Limits** (tuned 2026-04-18):

| Limit | Value | Reason |
|-------|-------|--------|
| `_MAX_NPROC` | 256 | numpy/OpenBLAS crashes with limit of 16 |
| `_MEMORY_LIMIT_BYTES` | 1024 MB | pandas/matplotlib headroom |
| `OPENBLAS_NUM_THREADS` etc. | 2 | Prevents single script consuming all CPU |
| `_SCRIPT_SEMAPHORE` | 4 | Global concurrency (`SCRIPT_CONCURRENCY` override) |
| `_QUEUE_TIMEOUT` | 180s | Queue timeout (`SCRIPT_QUEUE_TIMEOUT` override) |

> Linux note: `RLIMIT_NPROC` limits the total number of processes/threads for the current uid (pthread = LWP). On Windows, `preexec_fn=None` — no `resource.setrlimit`.

#### 4.8.3 XSS Protection

- Backend: `consumer_ui.py` / `wechat_ui.py` use `html.escape()` for template injection
- `_safe_json_for_inline_script` escapes `</` to `<\/` to prevent script breakout
- Frontend: `marked.parse()` output sanitized by `DOMPurify.sanitize()`, whitelist includes `audio` / `video` / `iframe`

#### 4.8.4 Encryption

- **Master Key**: `data/encryption.key` (auto-generated first time, or override with `ENCRYPTION_KEY` env var)
- **API Key encryption**: AES-256-GCM, stored in `users/{uid}/api_keys.json`
- **WeChat media**: AES-128-ECB (required by iLink protocol)

### 4.9 Per-Admin Python venv

- **Module**: `app/services/venv_manager.py`
- **Directory**: `users/{uid}/venv/`, each Admin has an isolated Python virtualenv
- **Creation**: `--system-site-packages` to inherit system pre-installed packages
- **Persistence**: packages installed by users are recorded in `users/{uid}/venv/requirements.txt`
- **Script execution**: `tools.py::create_run_script_tool` uses `get_user_python(user_id)`; Consumer uses admin's venv
- **API**:
  - `GET /api/packages` — list installed packages + venv status
  - `POST /api/packages/init` — initialize user venv
  - `POST /api/packages/install` — install package (name cannot contain `;|&$`` injection characters)
  - `POST /api/packages/uninstall` — uninstall package
- **Startup restore**: `main.py` startup calls `restore_all_venvs()`, auto `pip install -r` for users with `requirements.txt`

### 4.10 Per-Admin API Keys

#### 4.10.1 Design

- Each Admin can configure their own OpenAI / Anthropic / Tavily / multimedia keys in Settings → General
- AES-256-GCM encrypted storage
- **Priority chain**: `user config > environment variables > not configured (prompt to set)`
- Admin's Agents (main Agent / Subagent / Consumer Agent) all use that Admin's keys

#### 4.10.2 Call Chain

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

#### 4.10.3 Supported Fields

- Keys: `openai_api_key` / `anthropic_api_key` / `tavily_api_key` / `cloudsway_search_key` / `image_api_key` / `tts_api_key` / `video_api_key` / `s2s_api_key` / `stt_api_key`
- URLs: `openai_base_url` / `anthropic_base_url` / `image_base_url` / `tts_base_url` / `video_base_url` / `s2s_base_url` / `stt_base_url`

#### 4.10.4 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings/api-keys` | Masked response + `*_configured` flags |
| PUT | `/api/settings/api-keys` | Encrypted save (triggers `clear_agent_cache + clear_consumer_cache`) |
| POST | `/api/settings/api-keys/test` | Test connectivity (openai / anthropic / tavily / all) |
| GET | `/api/settings/api-keys/status` | Quick check if any LLM provider is available |

#### 4.10.5 Cache Invalidation

After key update, automatically calls `clear_agent_cache(user_id)` + `clear_consumer_cache(admin_id=user_id)` — next Agent request creates a fresh instance.

---

## 5. Frontend Architecture

### 5.1 Directory Structure

```
frontend/src/
├── main.tsx                      # React 19 entry (Admin SPA)
├── App.tsx                       # ConfigProvider + AuthProvider + ThemeProvider + StreamProvider + Router
├── router/index.tsx              # BrowserRouter + three-layer ErrorBoundary
├── layouts/AppLayout.tsx         # Main layout (sidebar + content area + file panel trigger)
│
├── stores/
│   ├── authContext.tsx           # Authentication state
│   ├── streamContext.tsx         # SSE stream state (admin)
│   ├── fileWorkspaceContext.tsx  # File panel state
│   └── themeContext.tsx          # Multi-theme switching + Antd ThemeConfig
│
├── services/api.ts               # Unified API client
│
├── types/index.ts                # Shared types (Message / MessageBlock / Subagent / etc.)
│
├── styles/
│   ├── global.css                # Global styles / scrollbars / markdown
│   ├── themes.css                # Multi-theme CSS variable definitions
│   └── theme.ts                  # Fallback JS constants (CSS variables take precedence)
│
├── utils/
│   ├── csvParse.ts               # CSV/TSV state machine parser
│   ├── fileKind.ts               # Extension → kind classification
│   └── timezone.ts               # Timezone utilities
│
├── pages/
│   ├── Login.tsx                 # Split-panel login/register page
│   ├── AdminServices/index.tsx   # Service management (4 tabs)
│   ├── Scheduler/index.tsx       # Scheduled tasks (Admin / Service dual tabs)
│   ├── WeChat/index.tsx          # Admin WeChat onboarding
│   ├── Settings/
│   │   ├── index.tsx             # SettingsLayout (sidebar menu)
│   │   ├── PromptPage.tsx        # System Prompt + Memory & Soul + Capability Prompts
│   │   ├── SubagentPage.tsx
│   │   ├── PackagesPage.tsx      # Per-user venv
│   │   ├── InboxPage.tsx
│   │   └── GeneralPage.tsx       # API Keys + timezone + theme + Advanced switches + BatchRunner embedded
│   └── Chat/
│       ├── index.tsx             # Main chat page
│       ├── chat.module.css       # CSS Modules (--jf-* variables)
│       ├── markdown.ts           # Sole rendering pipeline (including <<FILE:>> handling)
│       ├── useSmartScroll.ts     # Smart scroll hook
│       ├── types.ts              # StreamBlock union types
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
│   ├── FilePreview.tsx           # Multi-type file viewer
│   ├── FileTreePicker.tsx        # Visual file/script selector
│   ├── HeaderControls.tsx
│   ├── LogoLoading.tsx
│   ├── SplitToggle.tsx
│   ├── ApiKeyWarning.tsx         # Guidance modal when no LLM key configured
│   ├── ErrorBoundary.tsx         # The only class component in the entire codebase
│   └── modals/
│       ├── BatchRunner.tsx       # Embedded in GeneralPage
│       ├── SoulSettings.tsx
│       ├── SubagentManager.tsx
│       ├── SystemPromptEditor.tsx
│       └── UserProfileEditor.tsx
│
└── service-chat/                 # Vite second entry (Consumer side)
    ├── main.tsx
    ├── ServiceChatApp.tsx
    ├── ServiceToolBadge.tsx      # Friendly tool status bar (replaces admin's ToolIndicator)
    ├── streamHandler.ts          # Lightweight SSE handler (no HITL/subagent)
    ├── serviceApi.ts             # Consumer API (decoupled from services/api.ts)
    └── serviceChat.module.css
```

### 5.2 Routing System

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

- `ProtectedRoute`: redirects to `/login` if unauthenticated
- `PublicRoute`: redirects to `/` if already authenticated
- `AppLayout` provides sidebar navigation + `<Outlet/>` + Portal node `#sider-slot`
- `ChatPage` / `SettingsLayout` use `createPortal` to inject sidebar content into `#sider-slot`
- `/settings/batch` redirects to `/settings/general` (backward compatibility)

### 5.3 State Management

No global state library. Uses React Context + component-local state.

| Context | File | Purpose |
|---------|------|---------|
| `authContext` | `stores/authContext.tsx` | `user` / `loading` / `login` / `register` / `logout`, Token in `localStorage` |
| `streamContext` | `stores/streamContext.tsx` | SSE stream state, blocks buffer, HITL interrupt |
| `fileWorkspaceContext` | `stores/fileWorkspaceContext.tsx` | File panel state, current editing file |
| `themeContext` | `stores/themeContext.tsx` | Multi-theme switching + Antd ThemeConfig |

### 5.4 API Client

`src/services/api.ts` provides a typed API client.

```typescript
async function request<T>(method: string, path: string, body?: unknown): Promise<T>
```

Auto-handles:
- Bearer Token injection (from `localStorage`)
- Content-Type (JSON / FormData)
- 401 auto-clears token and refreshes
- Error extraction of `detail` field

**Module breakdown**:

| Module | Functions |
|--------|-----------|
| Auth | `login` / `register` / `getMe` |
| Conversations | `list/create/get/delete` + `attachmentUrl` |
| Chat | `streamChat` / `resumeChat` / `stopChat` / `abortStream` / `checkServerStreaming` |
| Files | `list/read/write/edit/delete/move` + `uploadFiles` / `downloadFile` / `mediaUrl` |
| System Prompt | CRUD + version management |
| User Profile | CRUD + version management |
| Capability Prompts | `getCapabilityPrompts` / `updateCapabilityPrompt` / `resetCapabilityPrompt` |
| Soul Config | `getSoulConfig` / `updateSoulConfig` |
| Subagents | `list/get/add/update/delete` |
| Scripts | `runScript` |
| Audio | `transcribeAudio` |
| Models | `getModels` |
| Batch | `uploadBatchExcel` / `startBatchRun` / `listBatchTasks` / `getBatchTask` / `cancelBatchTask` / `batchDownloadUrl` |
| Scheduler | Admin + Service task CRUD + `runNow` |
| Services | CRUD + Keys + WeChat channel |
| Inbox | `list/get/updateStatus/delete` + `getUnreadCount` |
| Packages | venv status + install/uninstall |
| API Keys | `getApiKeys` / `updateApiKeys` / `testApiKeys` / `getApiKeysStatus` |
| WeChat (Admin) | `adminWechatQrcode` / `adminWechatStatus` / `adminWechatSession` / `adminWechatMessages` |
| WeChat (Service) | `serviceWcQrcode` / `serviceWcSessions` / `serviceWcSessionMessages` |

### 5.5 SSE Streaming

#### 5.5.1 Event Types

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

#### 5.5.2 Performance Optimization

```
SSE callback → useRef directly mutates blocks array (avoids setState per-token)
            → requestAnimationFrame throttled re-render (~60fps)
            → Batch update to React state
            → StreamingMessage (React.memo) avoids unnecessary re-renders
```

#### 5.5.3 Stream Recovery

- Backend `_stream_agent` / `_stream_consumer` in `finally` block detects unsaved partial replies, appends `⚠️ [Connection interrupted — saved generated content]` then persists (`_saved` flag prevents duplicate saves)
- `_active_streams` dict tracks `{thread_id → {user_id, conv_id}}`
- `GET /api/chat/streaming-status` returns current user's active streaming conversations
- Chat page on load calls `checkServerStreaming()` — if background streaming detected, shows yellow banner with "Abort & Save" / "Refresh Status" buttons

#### 5.5.4 Stop Button

- Send button turns red Stop (Phosphor `Stop`) during streaming output
- `POST /api/chat/stop` sets `asyncio.Event` cancel flag, `_stream_agent` checks it each iteration

### 5.6 Chat Component Tree & Shared Rendering

#### 5.6.1 Component Tree

```
ChatPage (pages/Chat/index.tsx)
├── Conversation list (createPortal → #sider-slot)
├── Message area
│   ├── MessageBubble (history messages)
│   │   └── BlocksRenderer (msg.blocks priority → fallback to legacy logic)
│   ├── StreamingMessage (streaming container, React.memo)
│   │   ├── ThinkingBlock (collapsible)
│   │   ├── ToolIndicator (tool calls, expandable args/result)
│   │   └── SubagentCard (subagent timeline rendering)
│   └── ApprovalCard (HITL: file diff / Plan editing)
├── Input area
│   ├── ImageAttachment (paste/drag/file select)
│   ├── VoiceInput (toggle: click start → click stop → auto-transcribe)
│   ├── Capability switches / Plan Mode / Model selector
│   └── Send/Stop button
└── useSmartScroll (pause auto-scroll when user scrolls up, resume at bottom)
```

#### 5.6.2 Admin / Service Shared Rendering

Components shared across admin / service (**change once, both sides update**):

- `pages/Chat/markdown.ts` — sole markdown rendering pipeline, includes `<<FILE:>>` handling / DOMPurify / hljs
- `pages/Chat/components/StreamingMessage.tsx` — shared rendering component, accepts `toolRenderer` / `hideSubagents` / `avatarSrc` props
- `pages/Chat/types.ts` `StreamBlock` data structure

**Rendering differences**:

| Dimension | Admin | Service |
|-----------|-------|---------|
| Tool blocks | Default `ToolIndicator` (real tool name + args/result) | Pass `toolRenderer={ServiceToolBadge}` (friendly text, non-whitelisted shows "Thinking…") |
| Subagent | Shows full `SubagentCard` | Pass `hideSubagents` to hide |
| Media URL | `adminMediaUrl(path)` | `setMediaUrlBuilder(buildConsumerMediaUrl)` to use query param with service API key |

#### 5.6.3 Message Blocks Persistence

Streaming and history messages use unified interleaved rendering:

- **Backend**: `save_message(blocks=...)`, `blocks` is an ordered array
  - `text`: `{"type": "text", "content": "..."}`
  - `thinking`: `{"type": "thinking", "content": "..."}`
  - `tool`: `{"type": "tool", "name": "...", "args": "...", "result": "...", "done": true}`
  - `subagent`: `{"type": "subagent", "name": "...", "task": "...", "status": "done", "content": "...", "tools": [...], "timeline": [...], "done": true}`
- **Frontend**: `MessageBubble` uses `BlocksRenderer` when `msg.blocks` exists, otherwise falls back to legacy logic

### 5.7 File Preview Panel

- **Kind classification**: `utils/fileKind.ts` by extension → `image|audio|video|pdf|markdown|html|csv|json|text|binary`
- **openFile optimization**: media/binary skips `api.readFile`, directly `setEditingFile(path)`
- **Rendering strategy**:

| Kind | Rendering |
|------|-----------|
| image / audio / video / pdf | Native `<img>/<audio>/<video>/<iframe>` via `mediaUrl(path)`, toolbar hides "Save" |
| markdown | Reuses `pages/Chat/markdown.ts::renderMarkdown`, class `.jf-file-md-preview` |
| html | `<iframe sandbox="allow-scripts">` (**no same-origin** — allows Plotly/ECharts but blocks parent page access) |
| csv / tsv | `utils/csvParse.ts` state machine → antd `Table` (max 2000 rows) |
| json / jsonl / ndjson | `JSON.parse` + hljs highlighting |
| text / code | textarea editing |
| binary | `Empty` placeholder + download button |

- **Toolbar toggle**: toggle types (md/html/csv/json) get antd `Segmented` "Preview/Source" in header
- **Download button**: all kinds get download button in toolbar

### 5.8 Error Boundary

- Component: `components/ErrorBoundary.tsx` (**the only class component in the codebase** — React 19 still requires class form, intentionally exempted from "avoid classes" rule)
- **Three-layer deployment** (`router/index.tsx`):
  - `scope="app-layout"` wraps `<AppLayout/>` — catches errors in the entire protected area
  - `scope="chat"` wraps `<ChatPage/>` — chat page crash doesn't affect sidebar
  - `scope="settings"` wraps `<SettingsLayout/>` — settings page crash doesn't affect chat
- **Interaction**: friendly message + expandable error details + **copy error info** button (includes scope/time/URL/UA/stack/componentStack)
- **Styling**: Antd `Result` + `Collapse`, background uses `--jf-bg-deep`

### 5.9 Design System & Multi-Theme

#### 5.9.1 Multi-Theme (`themes.css` + `themeContext.tsx`)

- `[data-theme]` attribute on `<html>`, controlled by ThemeProvider
- **Persistence**: `localStorage.jf-theme`
- **Three built-in themes**:

| Theme | Style | Primary Color | Special Rules |
|-------|-------|--------------|---------------|
| `dark` (default) | Warm pink-purple dark | `#E89FD9` | — |
| `cyber-ocean` | Cyan-blue light | — | — |
| `terminal` | Phosphor-green CRT terminal | `#33ff00` | Global monospace font, `border-radius: 0`, phosphor `text-shadow`, CRT scanlines, button hover invert, `text-transform: uppercase` |

- **Adding a new theme**:
  1. Copy a `[data-theme]` block in `themes.css` and adjust values
  2. Add `THEMES` entry + Antd ThemeConfig in `themeContext.tsx`

- **CSS variable naming**:
  - Brand colors: `--jf-primary/secondary/accent/highlight/legacy`
  - RGB triplets: `--jf-primary-rgb` (for `rgba(var(--jf-primary-rgb), 0.12)`)
  - Gradients: `--jf-gradient-from/to`, `--jf-user-bubble-bg/shadow`
  - Backgrounds: `--jf-bg-deep/panel/raised/code/inset`
  - Text: `--jf-text/text-muted/text-dim/text-quaternary`
  - Borders: `--jf-border/border-rgb/border-strong`
  - Semantic: `--jf-success/warning/error/info`
  - Shadows: `--jf-shadow-float/hover/brand`
  - Diff: `--jf-diff-add-bg/del-bg/eq-text`
  - Antd: `--jf-menu-selected-bg/select-option-bg`

#### 5.9.2 Border Radius System

| Tier | CSS Variable | Usage |
|------|-------------|-------|
| sm | `var(--jf-radius-sm)` | 4px, inset corners |
| md | `var(--jf-radius-md)` | 8px, buttons, panels |
| lg | `var(--jf-radius-lg)` | 12px, cards, modals |
| bubble | `var(--jf-radius-bubble)` | 16px, message bubbles |

- Circular elements still use `'50%'`
- Inline `borderRadius` **must** use variable strings: `'var(--jf-radius-md)'` — no hardcoding

#### 5.9.3 Other Standards

- **Icons exclusively from `@phosphor-icons/react`** (replaces `@ant-design/icons`)
- **Style priority**: antd components > inline style (with CSS variables) > CSS Modules
- **Type definitions** centralized in `src/types/index.ts`
- **API calls** unified through `src/services/api.ts`
- **Logo**: `/media_resources/jellyfishlogo.png`
- **Fonts**: body Segoe UI, code JetBrains Mono (Google Fonts CDN)

#### 5.9.4 Advanced Tab Visibility

- `GeneralPage.tsx` "Advanced Features" card: two Switches control Prompt page's Advanced Tab visibility
- `localStorage` keys: `show_advanced_system` (operation rules) / `show_advanced_soul` (Memory & Soul)
- Off by default; custom event `advanced-settings-changed` for real-time response

---

## 6. Service & Consumer Two-Tier Layer

### 6.1 Admin Layer

- Login via Web UI with full permissions
- Manage filesystem (`docs/scripts/generated/soul`)
- Configure System Prompt + user profile
- Manage Subagents + capability prompts
- Publish Services + manage API Keys
- Configure scheduled tasks + WeChat integration

### 6.2 Consumer Layer

- Authenticate via `sk-svc-...` API Key (or `/s/{sid}?key=...` self-service link)
- **Restricted permissions**: only access capabilities + `allowed_docs/scripts` configured in Service
- **Filesystem isolation**: `users/{admin}/services/{svc}/conversations/{conv}/`
- **Generated file isolation**: each conversation has its own `generated/`
- Cannot access Admin's filesystem

### 6.3 Service Configuration

Each Service is a published config:

```
users/{admin_id}/services/{service_id}/
├── config.json          # Model + system_prompt + capabilities + allowed_docs/scripts + welcome_message + quick_questions + wechat_channel
├── keys.json            # API Keys (hashed)
├── wechat_sessions.json # WeChat session state
├── conversations/       # Consumer conversation directory
└── tasks/               # Service scheduled tasks
```

Full schema in `docs/filesystem-architecture.md` §4.

### 6.4 Service Self-Service (v2.x)

#### 6.4.1 Key-Attached Links

- URL format: `/s/{service_id}?key=sk-svc-xxx`
- Backend: `consumer_ui.py` injects template variables; frontend reads `key` from `URLSearchParams` → writes to `localStorage` → `history.replaceState` immediately clears query
- Frontend admin: Key Modal on success **additionally** shows full link with key + warning (**equivalent to sharing the key**)

#### 6.4.2 Welcome Message + Quick Questions

- Fields: `welcome_message: str` + `quick_questions: List[str]`
- Backend template injection: `_safe_json_for_inline_script` prevents script breakout
- Frontend chat: ChatGPT-style first screen (large welcome text + gradient chips), auto-hides after first message

#### 6.4.3 Visual File/Script Selector

- `FileTreePicker.tsx`: antd `Tree` checkable + `loadData` lazy loading + "All (*)" Switch
- Folder selection = entire directory recursively (key ends with `/`)
- Root constraint: `allowed_docs` only shows `/docs`, `allowed_scripts` only shows `/scripts`
- Empty `allowed_docs` falls back to `["*"]`, empty `allowed_scripts` keeps empty (semantic = no scripts)

### 6.5 Consumer Agent Channel-Awareness

```python
create_consumer_agent(..., channel: str = "web")
```

- `channel="web"`: does **not** inject `send_message` even if humanchat capability is enabled. Reason: agent output on web is already streamed to browser; calling send_message would have no delivery target and would expose tool events to consumers
- `channel="wechat"`: injects `send_message`, tool results delivered by the delivery layer
- `channel="scheduler"`: same as wechat
- `cache_key` includes `::ch={channel}` (only when non-default web)

---

## 7. WeChat Integration

> Full iLink protocol details, CDN encryption/decryption, and gotcha notes: see `docs/wechat-integration-guide.md`.

### 7.1 Dual-Stack Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Service Channel               │  Admin Self-onboarding  │
├────────────────────────────────┼─────────────────────────┤
│  /api/wc/*                     │  /api/admin/wechat/*    │
│  router.py                     │  admin_router.py         │
│  bridge.py                     │  admin_bridge.py         │
│  Consumer Agent (channel=wechat│  Admin Agent             │
│  Convos in service/conversations│  Convos in admin convos │
│  Sessions in wechat_sessions.json│  admin_wechat_session. │
│  Permissions per service caps  │  Full docs/scripts perms │
└────────────────────────────────┴─────────────────────────┘
Shared: client.py / media.py / delivery.py / rate_limiter.py
```

### 7.2 Key Modules

| Module | Responsibility |
|--------|---------------|
| `client.py` | iLink protocol: getconfig / getupdates / sendmessage / getuploadurl / cdn upload/download |
| `bridge.py` | Service Bridge: message routing + multimodal construction + send_message interception + auto-approve HITL |
| `admin_bridge.py` | Admin Bridge: full permissions + inline send_message handling |
| `session_manager.py` | Session lifecycle + persistence + reconnection (exponential backoff) |
| `media.py` | AES-128-ECB encryption/decryption + CDN upload/download |
| `delivery.py` | Unified delivery (with `<<FILE:>>` tag parsing) |
| `rate_limiter.py` | Per-user 10/60s + QR 5/60s + global session limit |
| `router.py` / `admin_router.py` | HTTP API endpoints |

### 7.3 Multimedia Delivery

| Direction | Flow |
|-----------|------|
| **Receive image** | CDN GET → AES decrypt → base64 multimodal → Agent Vision |
| **Send image** | `getuploadurl` → AES encrypt → CDN POST → `sendmessage(image_item.media)` |
| **Receive voice** | CDN GET → AES decrypt → SILK→WAV (pysilk) → Whisper transcription |
| **Send TTS** | MP3 under `/generated/audio/` sent as file attachment via `send_file` |

> Voice message (voice_item) approach suspended: pysilk-encoded SILK always silent in WeChat client at 24kHz/16kHz. Under further investigation.

### 7.4 `<<FILE:>>` Tag Parsing

`delivery.py::extract_media_tags(text) -> (cleaned_text, [paths])`:

```python
_MEDIA_TAG_RE = re.compile(r"<<FILE:([^>]+?)>>")
```

- Actively extracts `<<FILE:path>>` tags from `send_message` text, converts to additional media delivery
- Remaining cleaned_text is sent as text
- Supports both `media_path` parameter and `<<FILE:>>` tag usage
- Bridge and Scheduler share this module, **avoiding code duplication**

### 7.5 Session Management Key Points

- **Exponential backoff reconnect** (auto-removes after 20 failures)
- Sessions with `from_user_id` **don't** participate in 24h idle cleanup (long-term retention)
- Sessions without `from_user_id` (empty sessions) are cleaned based on inactivity
- Empty polling only discards "not-yet-established" sessions after 50 attempts
- **Multi-Admin isolation**: `list_sessions` / `remove_session` filter by `admin_id`

### 7.6 Admin Self-Onboarding

- Endpoints: `POST /api/admin/wechat/qrcode` / `GET qrcode/status` / `GET/DELETE session` / `GET messages`
- **Session persistence**: `users/{user_id}/admin_wechat_session.json`, auto-restored after Docker/restart
- `_save_admin_session()` writes on session creation, first `from_user_id` capture, `context_token` update
- `restore_admin_sessions()` called on `main.py` startup
- `shutdown_admin_sessions()` only stops polling/closes connections, **does not delete** persistence file

---

## 8. Scheduler (Scheduled Tasks)

### 8.1 Scheduler Design

- `app/services/scheduler.py::TaskScheduler` singleton, asyncio loop checks every 30s
- Started by `main.py` startup, gracefully stopped on shutdown

### 8.2 Task Storage

| Type | Path | Prefix |
|------|------|--------|
| Admin tasks | `users/{uid}/tasks/{task_id}.json` | `task_*` |
| Service tasks | `users/{uid}/services/{svc}/tasks/{task_id}.json` | `stask_*` |

Each task file contains the last 20 run records.

### 8.3 Task Types

| Type | Description | Scope |
|------|-------------|-------|
| `script` | Execute Python script under `scripts/` | Admin only |
| `agent` | Execute Agent task (prompt + optional document context) | Admin + Service |

### 8.4 Schedule Types

| Type | Description | Example |
|------|-------------|---------|
| `once` | One-time | `2026-12-31T09:00:00+08:00` (include timezone suffix) |
| `cron` | Cron expression | `0 9 * * *` |
| `interval` | Interval (seconds) | `3600` |

### 8.5 Timezone Handling

- Each task stores `tz_offset_hours` field (user's timezone offset at creation time)
- **Cron interpreted in user's timezone**: `_next_cron` UTC now → user local → croniter → back to UTC
- **once must include timezone suffix**: `_ensure_tz_suffix` provides fallback with `tz_offset_hours`
- **interval not affected by timezone**: directly uses second offsets
- Old tasks without the field use `_resolve_task_tz_offset(task)` falling back to `get_tz_offset(user_id)` (consistent with preferences default +8)
- React Scheduler must include `tz_offset_hours: getTzOffset()` in create/update task body

### 8.6 reply_to Routing

Service tasks control result delivery target via `reply_to` field:

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

| Channel | Delivery Target |
|---------|----------------|
| `wechat` | `delivery.py::deliver_tool_message` delivers to WeChat user |
| `inbox` | Writes to Admin inbox |
| `admin_chat` | Writes to Admin's regular conversation |

`_run_service_agent_task` **in real-time** intercepts `send_message` tool calls during agent execution loop, sends to WeChat via `delivery.py` (supports text + media); `_deliver_reply` only as fallback when agent doesn't use send_message (plain text summary).

### 8.7 Run Record Steps

Each run record contains `steps[]`:

| Step Type | Description |
|-----------|-------------|
| `start` | Execution begins |
| `docs_loaded` | Documents loaded |
| `loop` | Agent loop iteration |
| `tool_call` | Tool invocation |
| `tool_result` | Tool result |
| `ai_message` | AI message |
| `auto_approve` | Auto-approval (HITL) |
| `wechat_warning` | WeChat client unavailable warning |
| `wechat_error` | WeChat delivery failure |
| `finish` | Completed |
| `error` | Error |
| `reply` | Fallback delivery |

### 8.8 Task Result Persistence

After `_run_agent_loop` finishes, calls `save_message` / `save_consumer_message` to write conversation JSON, including:
- `source: "scheduled_task"` or `"admin_broadcast"` marker
- Complete `blocks[]`

### 8.9 Sync→Async Main Loop Bridge

LangChain sync tools (like `contact_admin`) execute via `BaseTool._arun` in `run_in_executor` thread pool, which has no event loop. Fix:

```python
# inbox.py / scheduler.py
def set_main_loop(loop: asyncio.AbstractEventLoop):
    global _main_loop
    _main_loop = loop

# When scheduling:
try:
    loop = asyncio.get_running_loop()
    loop.create_task(coro)
except RuntimeError:
    if _main_loop is not None and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _main_loop)
```

### 8.10 Service Task Tools

Consumer agent via:
- `create_service_schedule_tool` injects `schedule_task`
- `create_service_manage_tasks_tool` injects `manage_scheduled_tasks` (only when `"scheduler"` in capabilities)
- Service's `manage_scheduled_tasks` **can only operate tasks for the current conversation_id** (permission isolation)

`publish_service_task` (Admin tool):
- `service_ids` supports ID and name matching (case-insensitive), returns available Service list when no match
- `session_ids` optional parameter to target specific WeChat sessions
- `run_now` scheduling uses `_schedule_coro` thread-safe mode

---

## 9. Inbox

### 9.1 Purpose

Service Agent sends notifications to Admin via `contact_admin` tool, which Admin can handle in the inbox. When Admin WeChat integration is enabled, the Inbox Agent automatically evaluates and forwards to WeChat.

### 9.2 Data Structure

```
users/{admin_id}/inbox/
└── inbox_{message_id}.json
```

Fields include `from_service_id` / `from_conv_id` / `subject` / `content` / `urgency` / `status: unread|read|archived` / `created_at`, etc. (see `docs/filesystem-architecture.md` §7).

### 9.3 Processing Pipeline

```
Service Agent
  └── contact_admin(subject, content, urgency)  [sync tool]
        └── inbox.post_to_inbox()  [via run_coroutine_threadsafe → _main_loop]
              ├── writes inbox_{id}.json
              ├── _trigger_inbox_agent()
              │     └── injects 3 recent inbox messages → evaluation Agent
              │           └── send_message → Admin WeChat (if connected)
              └── frontend polls GET /api/inbox/unread-count → shows badge
```

### 9.4 Key Fixes

- **Inbox agent thread pool issue**: `contact_admin` is a sync tool, LangGraph executes via `run_in_executor` in thread pool, `asyncio.get_running_loop()` fails. Fix: cache main event loop + `run_coroutine_threadsafe`
- **thread_id stabilization**: `inbox-{admin_id}` (shared within same Admin, accumulating memory)

### 9.5 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/inbox` | List |
| GET | `/api/inbox/unread-count` | Unread count |
| GET | `/api/inbox/{id}` | Details |
| PUT | `/api/inbox/{id}` | Update status |
| DELETE | `/api/inbox/{id}` | Delete |

---

## 10. Realtime Voice (S2S WebSocket)

### 10.1 Module

`app/voice/router.py` provides WebSocket endpoint `/api/voice/ws` as a proxy for OpenAI Realtime API.

### 10.2 Workflow

```
Browser ──WebSocket──→ FastAPI ──WebSocket──→ wss://api.openai.com/v1/realtime
                       │
                       └── injects tool config on session.update
                       └── transparent tool calls + backend tool execution
```

- Tool injection: session.update event includes Admin's configured toolset
- Authentication: JWT in query parameter
- API Key: override via `S2S_API_KEY` / `S2S_BASE_URL` (falls back to `OPENAI_API_KEY`)

---

## 11. API Reference

### 11.1 Admin API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/register` | Register (requires code) |
| `POST` | `/api/auth/login` | Login |
| `GET` | `/api/auth/me` | Current user |
| `GET/POST/DELETE` | `/api/conversations[/{id}]` | Conversation management |
| `POST` | `/api/chat` / `/chat/resume` / `/chat/stop` | SSE chat |
| `GET` | `/api/chat/streaming-status` | Background stream status |
| CRUD | `/api/files/*` | File operations + media + upload/download |
| `POST` | `/api/scripts/run` | Execute script |
| `POST` | `/api/audio/transcribe` | Voice transcription |
| `GET` | `/api/models` | Model list |
| CRUD | `/api/system-prompt[/versions]` | Prompt management |
| CRUD | `/api/user-profile[/versions]` | User profile |
| CRUD | `/api/subagents` | Subagent management |
| CRUD | `/api/capability-prompts[/{key}]` | Capability prompts |
| GET/PUT | `/api/soul/config` | Soul configuration |
| `POST/GET` | `/api/batch/*` | Batch execution |
| CRUD | `/api/services[/{id}[/keys]]` | Service management |
| CRUD | `/api/scheduler[/{id}]` | Admin scheduled tasks |
| `POST` | `/api/scheduler/{id}/run-now` | Execute immediately |
| `GET` | `/api/scheduler/{id}/runs` | Run history |
| `GET` | `/api/scheduler/services/all` | All service tasks |
| CRUD | `/api/scheduler/services/{svc_id}[/{task_id}]` | Service tasks |
| CRUD | `/api/inbox[/{id}]` | Inbox |
| `GET` | `/api/inbox/unread-count` | Unread count |
| GET/POST | `/api/packages[/init/install/uninstall]` | Per-user venv |
| GET/PUT/POST | `/api/settings/api-keys[/test/status]` | Per-admin API Keys |
| `GET` | `/api/wc/{service_id}/qrcode` | Service WeChat QR |
| `GET` | `/api/wc/{service_id}/sessions[/{session_id}/messages]` | Session view |
| `POST` | `/api/admin/wechat/qrcode` | Admin WeChat QR |
| `GET/DELETE` | `/api/admin/wechat/session` | Admin session management |
| `WS` | `/api/voice/ws` | S2S WebSocket |

### 11.2 Consumer API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/conversations` | Create conversation |
| `GET` | `/api/v1/conversations/{id}` | Conversation history |
| `GET` | `/api/v1/conversations/{id}/files[/{path}]` | Generated file list / download |
| `GET` | `/api/v1/conversations/{id}/attachments/{path}` | User attachment |
| `POST` | `/api/v1/chat` | Custom SSE |
| `POST` | `/api/v1/chat/completions` | OpenAI-compatible |

### 11.3 Static Pages

| Path | Description |
|------|-------------|
| `GET /s/{service_id}` | Consumer standalone chat page (React) |
| `GET /s/{service_id}?key=sk-svc-...` | Same, auto-writes to localStorage |
| `GET /wc/{service_id}` | WeChat scan landing page |

---

## 12. Tauri Desktop Launcher

### 12.1 Architecture

- **Tauri v2** (Rust + WebView) packaged as native desktop application
- Bundles Python 3.12.7 + Node.js 20.18.0 + backend code + frontend build output
- Users double-click `.dmg` / `.exe` to start — no command line needed

### 12.2 File Structure

```
tauri-launcher/
├── package.json                 # @tauri-apps/cli
├── dist/
│   └── index.html               # Self-contained launcher UI (inline CSS + JS + base64 logo)
├── bundle-resources/            # Staging area: app/, config/, frontend dist, launcher.py
├── scripts/
│   ├── build.py                 # One-click packaging
│   └── version.py               # Version management
└── src-tauri/
    ├── Cargo.toml               # tauri 2 / reqwest / tokio / serde / open / libc
    ├── tauri.conf.json          # Window 720×580, NSIS (Windows), macOS ≥10.15
    ├── build.rs
    └── src/
        ├── main.rs              # Entry point
        └── lib.rs               # 14 Tauri Commands
```

### 12.3 14 Tauri Commands

**Core (9)**:
- `detect_environment` — Python/Node.js/project file detection
- `load_env_config` / `save_env_config` — read/write `.env` (preserves comments + unknown keys)
- `test_api_key` — HTTP test OpenAI/Anthropic/Tavily connectivity
- `start_jellyfish` — calls `launcher.py --skip-check`
- `stop_jellyfish` — SIGTERM (Unix) / kill (Windows)
- `get_status` — polls process + port liveness
- `open_in_browser` — opens browser to frontend

**Registration Codes / User Management**:
- `list_registration_keys` / `generate_registration_keys(count)` / `delete_registration_key`
- `list_admin_users` / `reset_admin_password` / `delete_admin_user` / `get_admin_stats`

**About / Tools (2026-04-20)**:
- `open_project_dir` / `open_users_dir` / `open_logs_dir` — lazy-create then open
- `open_release_page` — browser navigates to fixed URL
- `get_app_version` — returns `env!("CARGO_PKG_VERSION")`

### 12.4 UI Structure (dist/index.html)

- Left 76px narrow navbar (Logo + 4 page tabs)
- **Page 1 Console**: 3-column environment detection pills + circular START button + API Keys config form
- **Page 2 Registration Code Management**: table + generate/delete + copy button
- **Page 3 Account Management**: stats summary 4 cards + user table + reset password/delete
- **Page 4 About / Tools**: gradient version number + 4 tool cards (project dir / user data / log dir / Release)

> Entire page is **single-file HTML (inline CSS + inline JS + base64 logo)** — no build step. When `window.__TAURI__` is absent, uses `mockInvoke()`, so opening `file://.../dist/index.html` in browser also previews styling.

### 12.5 Key Gotchas

#### 12.5.1 Windows `\\?\` Extended Long Path Prefix (Critical Bug 2026-04-20)

- **Symptom**: Windows .exe startup — uvicorn reports `OSError: Cannot load native module 'Crypto.Util._cpuid_c'` but `.pyd` file physically exists
- **Root cause**: Tauri's `app.path().resource_dir()` on Windows returns extended long path prefix form like `\\?\D:\JellyfishBot\`; this prefix contaminates Python subprocess `sys.executable`; pycryptodome's `os.path.isfile()` cannot find sibling `.pyd` under `\\?\` prefixed paths
- **Fix**: `src-tauri/src/lib.rs`'s `strip_win_extended_prefix()` helper, forcibly strips prefix in three places: `resolve_project_dir` / `find_bundled_python` / `find_bundled_node`; `launcher.py` adds `_strip_extended_prefix()` as double defense
- **Scope**: affects numpy / scipy / matplotlib and all packages with native extensions
- **macOS completely unaffected**

#### 12.5.2 Other Gotchas

- **`withGlobalTauri: true`** — Required! Otherwise `window.__TAURI__` is undefined
- **`server.js` uses ESM** — `package.json` has `"type": "module"`
- **Express 5 wildcards** — use `'/{*path}'` instead of `'*'`
- **Paths must be absolute** — `launcher.py::_resolve_python/node()` must return `os.path.abspath()`
- **macOS signing** — after modifying Resources, need `codesign --force --deep --sign - <app_path>`

### 12.6 Windows Native Dev Environment

`npx tauri dev` / `build` both require:

1. **Rust toolchain** (cargo in `C:\Users\{user}\.cargo\bin\` — not in PATH by default)
2. **MSVC linker (link.exe)** — VS 2022 Build Tools or Community
3. **Windows SDK** (`kernel32.lib` / `ntdll.lib`) — **easily missed when installing VS**!
4. Solution: VS Installer → Modify → check "Desktop development with C++"

One-liner to start dev:

```powershell
$vsBat = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat"
& cmd /c "`"$vsBat`" -arch=x64 -no_logo && set" | ForEach-Object {
  if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$env:Path = "C:\Users\$env:USERNAME\.cargo\bin;$env:Path"
cd tauri-launcher; npx tauri dev
```

> Don't keep `devUrl` in `tauri.conf.json`: static project has no Vite/dev server, keeping it will cause `tauri dev` to hang waiting.
> Cold-start full Rust compilation ~4 minutes (457 crates), subsequent incremental ~5-30 seconds.

### 12.7 Iteration Rules

| Changed File | Handling |
|-------------|---------|
| `dist/index.html` | Ctrl+R in dev mode takes effect immediately; production requires `npx tauri build` |
| `lib.rs` / `tauri.conf.json` | Must `npx tauri build` |
| `launcher.py` / `server.js` / `app/**` / `frontend/dist/**` | Tauri **resources**, **hot-patchable**: directly overwrite corresponding files in install directory + restart launcher (macOS also needs `codesign`) |

### 12.8 Building & Releasing

```bash
cd tauri-launcher
python scripts/version.py bump patch     # Bump version
python scripts/build.py                  # Full packaging
python scripts/version.py tag --push     # Push tag → CI auto-build
```

CI/CD: `.github/workflows/release.yml`
- Trigger: push tag `v*` or manual `workflow_dispatch`
- Three-platform matrix: macOS-arm64 / macOS-x64 / Windows-x64
- Artifacts uploaded + GitHub Release auto-created (draft)

Package size: DMG ~247 MB, extracted ~998 MB.

---

## 13. Cross-platform Launcher (launcher.py)

### 13.1 Features

- **Old instance detection**: detects running JellyfishBot processes via port scan (lsof / netstat)
- **User-confirmed kill**: lists processes occupying ports, SIGTERM → SIGKILL
- **Auto port discovery**: default 8000 (backend) + 3000 (frontend), auto-increments if occupied
- **Dual-process management**: starts uvicorn + Express, cleans up all processes on subprocess crash
- **Clean exit**: Ctrl+C / SIGTERM graceful termination (SIGKILL after 5s timeout)

### 13.2 Log Tee (2026-04-20)

All subprocess stdout+stderr launched by launcher simultaneously writes to:
- `{project_dir}/logs/{name}-YYYYMMDD.log` (daily rotation + append + session header)
- Original stdout

Implementation: `_spawn_with_log(cmd, cwd, env, log_name)` replaces bare `Popen`; subprocess stdout/stderr merged to `PIPE`, then `_tee_pipe_to(fh, src)` background daemon thread writes line-by-line to disk.

> ⚠️ **Do not revert to `subprocess.PIPE` + `communicate()`** — long-running processes will block `wait()`; current daemon thread + `bufsize=0` implementation is a validated non-blocking solution.

### 13.3 Port Configuration

| File | Configuration |
|------|--------------|
| `launcher.py` | `--port` / `--frontend-port` command line args |
| `server.js` | `FRONTEND_PORT` / `API_TARGET` environment variables |
| `start.sh` (Docker) | `BACKEND_PORT` / `FRONTEND_PORT` environment variables |

### 13.4 Usage

```bash
python launcher.py                    # Production mode
python launcher.py --dev              # Dev mode (uvicorn --reload + vite dev)
python launcher.py --port 9000        # Custom backend port
python launcher.py --backend-only     # Backend only
python launcher.py --skip-check       # Skip old instance detection
```

---

## 14. Docker Deployment

### 14.1 Multi-stage Build

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

- React source and devDependencies **not included** in final image
- Express `server.js` serves static files from `dist/`

### 14.2 docker-compose

```
Cloudflare (SSL) → Nginx (:80) → Express (:3000) → FastAPI (:8000)
```

- Nginx: SSL termination + reverse proxy (`nginx/nginx.conf`)
- Express: static files + `/api` proxy
- FastAPI: core backend
- User data volume: `./data/users:/app/users`

### 14.3 Startup Script (`start.sh`)

1. Start FastAPI (`:8000`)
2. Wait until ready, then start Express (`:3000`)
3. `wait -n` monitors both processes

### 14.4 Health Check

Container has built-in health check (checks FastAPI `/docs` endpoint). Nginx only accepts traffic after backend is ready.

### 14.5 .dockerignore

Excludes `frontend/node_modules/` / `frontend/dist/` / `venv/` / `data/` / `.env` / `.git/`.

---

## 15. Testing & Debugging

### 15.1 Backend Debugging

- **API docs**: <http://localhost:8000/docs> (Swagger UI)
- **Log level**: `LOG_LEVEL` environment variable + `main.py::logging.basicConfig(level=logging.INFO)`
- **WeChat logs**: requires `logging.basicConfig(level=logging.INFO)`, otherwise `wechat.*` logs won't output
- **Langfuse tracing**: after enabling, view Agent execution chain in Langfuse UI (v3 SDK reads from env automatically)
- **Sandbox diagnostics**: `script_runner.get_script_runtime_stats()` returns current active/pending script counts

### 15.2 Frontend Debugging

- **Vite HMR**: `npm run dev` supports hot module replacement
- **React DevTools**: install in browser
- **Network**: inspect SSE connections (`Accept: text/event-stream`) and API requests

### 15.3 Common Debugging Scenarios

| Symptom | Investigation |
|---------|--------------|
| SSE not working | Vite proxy config, Accept header, Express proxy buffer |
| Agent hangs | `POST /api/chat/stop`, check backend logs, `get_script_runtime_stats()` |
| File operation errors | `path_security` logs, `safe_join` boundary |
| WeChat messages not delivering | `wechat.*` logs, iLink connection status, `context_token` expiry |
| WeChat shows literal `<<FILE:>>` | Check if `delivery.py::extract_media_tags` was called |
| Scheduled task not running | `scheduler.py` 30s loop logs, `tz_offset_hours` field |
| Inbox not triggering WeChat | `_main_loop` injected, Admin WeChat connected |
| Script "queue busy" | `SCRIPT_CONCURRENCY` env var + `SCRIPT_QUEUE_TIMEOUT` |
| Tauri startup reports pycryptodome error | `\\?\` extended prefix (see §12.5.1) |

---

## 16. Extension Development

### 16.1 Adding a New Tool

1. Define factory function with `@tool` decorator in `app/services/tools.py`
2. Inject into Admin Agent toolset in `agent.py::create_user_agent`
3. For Consumer support, conditionally inject by capability in `consumer_agent.py`
4. For channel distinction (like `send_message`), inject based on `channel` param in `consumer_agent.create_consumer_agent`
5. Add corresponding capability prompt in `tools.py::CAPABILITY_PROMPTS`
6. For Subagent accessibility, add to `subagents.py::SHARED_TOOL_NAMES` or `MEMORY_TOOL_NAMES`

### 16.2 Adding a New Route

1. Create new module in `app/routes/` using `APIRouter`
2. Register in `app/main.py` with `app.include_router(...)`
3. Admin routes: `Depends(get_current_user)`; Consumer routes: `Depends(get_service_context)`

### 16.3 Adding a New Frontend Page

1. Create page component in `frontend/src/pages/`
2. Add `<Route>` in `frontend/src/router/index.tsx`
3. For sidebar nav item, add to `settingsNav` array in `SettingsLayout`
4. Wrap with `ErrorBoundary` (reference: `<ErrorBoundary scope="settings">`)

### 16.4 Adding a New API Call

1. Add typed function in `frontend/src/services/api.ts`
2. Add type definitions in `frontend/src/types/index.ts`

### 16.5 Adding a New Subagent

1. Modify `app/services/subagents.py::DEFAULT_SUBAGENTS` (default) or CRUD via `/api/subagents` API
2. Select tools from `SHARED_TOOL_NAMES + MEMORY_TOOL_NAMES`
3. Frontend `SubagentManager.tsx` adapts automatically (reads `available_tools` from `GET /api/subagents`)

### 16.6 Adding a New Theme

1. Copy a `[data-theme]` block in `frontend/src/styles/themes.css` and adjust variables
2. Add `THEMES` entry + Antd `ThemeConfig` in `themeContext.tsx`
3. Add new theme to the theme toggle button at the bottom of the sidebar in `AppLayout.tsx`

### 16.7 Adding a New Tauri Command

1. Define function with `#[tauri::command]` in `tauri-launcher/src-tauri/src/lib.rs`
2. Register in `invoke_handler!` macro
3. Call in `dist/index.html` via `window.__TAURI__.invoke('cmd_name', { args })`
4. Re-run `npx tauri build`

---

## 17. Development Checklist

- [ ] All Python imports use `app.*` package paths
- [ ] Do **not** top-level import `deepagents` outside `app/services/agent.py`, `consumer_agent.py`, `voice/router.py`
- [ ] Avoid circular imports: use deferred imports inside functions (e.g., `clear_agent_cache` in `prompt.py`/`subagents.py`)
- [ ] Consumer routes use `get_service_context` dependency (**not** `get_current_user`)
- [ ] Consumer Agent creation must pass `channel` parameter (web / wechat / scheduler)
- [ ] Path operations exclusively through `app.core.path_security.safe_join` / `ensure_within`
- [ ] File I/O explicitly UTF-8 (`encoding="utf-8"`)
- [ ] Always read API URLs through `api_config` or env, **never hardcode**
- [ ] All sync tools needing asyncio must use `_main_loop` + `run_coroutine_threadsafe`
- [ ] Service `manage_scheduled_tasks` can only operate tasks for the current `conversation_id`
- [ ] Frontend icons use `@phosphor-icons/react` (**no new** `@ant-design/icons` references)
- [ ] Frontend components use functional + Hooks (sole exception: `ErrorBoundary`)
- [ ] API calls managed through `services/api.ts`
- [ ] Inline `borderRadius` uses `var(--jf-radius-*)` variable strings
- [ ] CSS colors through `var(--jf-*)` references; hardcoding only in `themes.css` and `themeContext.tsx`
- [ ] **When changing admin chat, check service-chat too**: shared `markdown.ts` and `StreamingMessage.tsx`
- [ ] Adding new SSE event types: update both `streamContext.tsx` (admin) and `streamHandler.ts` (service)
- [ ] **Never copy `markdown.ts` into `service-chat/` for convenience** — this was the root cause of the historical `<<FILE:>>` media bug
- [ ] Scheduler task creation must include `tz_offset_hours: getTzOffset()`
- [ ] Tauri paths must be processed through `strip_win_extended_prefix` to strip `\\?\` prefix

---
