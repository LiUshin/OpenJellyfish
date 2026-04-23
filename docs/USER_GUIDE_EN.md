# JellyfishBot User Guide

> This document covers all feature modules in JellyfishBot v2.0+ with React frontend and Tauri desktop app.
> Last updated: 2026-04-21

---

## Table of Contents

1. [Quick Start](#1-quick-start)
   - [1.1 Three Launch Methods](#11-three-launch-methods)
   - [1.2 Desktop App (Recommended for New Users)](#12-desktop-app-recommended-for-new-users)
   - [1.3 Command Line (Developers)](#13-command-line-developers)
   - [1.4 Docker Deployment (Team/Server)](#14-docker-deployment-teamserver)
2. [Login & Registration](#2-login--registration)
3. [Interface Overview](#3-interface-overview)
4. [Chat](#4-chat)
5. [File Panel](#5-file-panel)
6. [Settings Center](#6-settings-center)
   - [6.1 Operation Rules (System Prompt + Memory & Soul + Capability Prompts)](#61-operation-rules)
   - [6.2 Subagent Management](#62-subagent-management)
   - [6.3 Python Environment (per-user venv)](#63-python-environment-per-user-venv)
   - [6.4 Inbox](#64-inbox)
   - [6.5 General (API Key + Timezone + Theme + Batch Run + Advanced Switches)](#65-general)
   - [6.6 Service Management](#66-service-management)
   - [6.7 Scheduled Tasks](#67-scheduled-tasks)
   - [6.8 WeChat Integration (Admin Self-onboarding)](#68-wechat-integration-admin-self-onboarding)
7. [Service Channel: Let Consumers Use via WeChat QR](#7-service-channel-let-consumers-use-via-wechat-qr)
8. [Consumer Usage (External API Integration)](#8-consumer-usage-external-api-integration)
9. [Soul Memory System](#9-soul-memory-system)
10. [Voice Interaction](#10-voice-interaction)
11. [Environment Variable Configuration](#11-environment-variable-configuration)
12. [FAQ](#12-faq)

---

## 1. Quick Start

### 1.1 Three Launch Methods

| Method | Audience | Advantages |
|--------|----------|-----------|
| **Desktop App** (Tauri) | Non-technical end users | Double-click to start, bundled runtime, auto-managed |
| **Command Line** | Developers | Hot reload, free debugging |
| **Docker** | Team / server deployment | One-click deploy, production-stable |

### 1.2 Desktop App (Recommended for New Users)

1. Download the installer for your platform from GitHub Release:
   - Windows: `JellyfishBot-x.y.z-x64.exe` (NSIS installer)
   - macOS (Apple Silicon): `JellyfishBot-x.y.z-aarch64.dmg`
   - macOS (Intel): `JellyfishBot-x.y.z-x64.dmg`
2. Install and open JellyfishBot.
3. **First launch automatically**:
   - Detects bundled Python 3.12 + Node.js 20 runtime
   - Extracts backend / frontend resources to install directory
4. On the **Console** tab, enter at least one LLM API Key (OpenAI or Anthropic) and click **Test Connection** to verify.
5. Click the central circular **START** button to launch backend services.
6. When ready, browser opens automatically at <http://localhost:3000>.

#### Desktop App 4 Tabs

| Tab | Function |
|-----|---------|
| **Console** | Environment detection, API Keys config, START / STOP button |
| **Registration Code Management** | Generate, copy, delete registration codes (needed on first deploy) |
| **Account Management** | View user list, reset passwords, delete users, statistics |
| **About / Tools** | Version number, check latest Release, open project dir / user data / log dir |

> Closing the desktop app = stops backend services + cleans up child processes.

### 1.3 Command Line (Developers)

#### Prerequisites

- Python 3.11+
- Node.js 20+
- At least one LLM API Key (Anthropic or OpenAI)
- A valid registration code (run `python generate_keys.py` on first deploy to generate)

#### Recommended: Cross-platform Launcher

```bash
# One-click start (auto port detection + old instance cleanup + dual-process management)
python launcher.py              # Production mode
python launcher.py --dev        # Dev mode (uvicorn --reload + vite dev)
python launcher.py --port 9000  # Custom backend port
python launcher.py --backend-only  # Backend only

# Shortcut scripts
./start_local.sh    # Mac/Linux
start_local.bat     # Windows (double-click)
```

Open <http://localhost:3000> in browser after startup.

#### Manual Start (for debugging)

```bash
# 1. Backend
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux/macOS
pip install -r requirements.txt
cp .env.example .env
# Edit .env, fill in API Key
python generate_keys.py        # Generate registration codes (first time)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev                    # → http://localhost:3000
```

### 1.4 Docker Deployment (Team/Server)

```bash
# 1. Prepare config
cp .env.example .env
# Edit .env, fill in API Key, etc.

# 2. Generate registration codes
python generate_keys.py
# Or use the "Registration Code Management" tab in the Tauri Desktop App

# 3. Prepare data directory permissions (required on first deploy)
#    The app container runs as jellyfish (uid=1000); the mounted ./data must be writable by it
mkdir -p ./data/users
sudo chown -R 1000:1000 ./data

# 4. Build and start
docker compose up -d --build

# 5. View logs
docker compose logs -f
docker compose logs -f jellyfishbot   # App logs only
docker compose logs -f nginx          # Nginx logs only
```

> ⚠️ **Step 3 is mandatory**: if `./data` doesn't exist, the docker daemon will create it as `root`, and the in-container uid=1000 process won't be able to write. FastAPI startup will then crash with `sqlite3.OperationalError: unable to open database file` and the container will restart in a loop. See FAQ.

#### Architecture

```
Cloudflare (SSL) → Nginx (:80) → Express (:3000) → FastAPI (:8000)
```

#### Data Persistence

```yaml
volumes:
  - ./data/users:/app/users    # User data: conversations, files, checkpoints, API Keys
```

#### Health Check

Container has built-in health check (checks FastAPI `/docs`). Nginx only accepts traffic after backend is ready.

---

## 2. Login & Registration

### 2.1 Registration

1. Open the app (<http://localhost:3000>), it auto-redirects to the login page.
2. Switch to the **Register** tab.
3. Fill in:
   - **Registration Code**: A one-time code distributed by the admin (e.g., `JFBOT-XXXX-XXXX-XXXX`)
   - **Username**: Choose a username
   - **Password**: Set a password
4. Click **Register**.
5. After successful registration, auto-login and redirect to main interface.

> Each registration code can only be used once. If lost or insufficient, admin can generate new ones in the Tauri Desktop App's "Registration Code Management" tab.

### 2.2 Login

1. Enter username and password on the login tab.
2. Click **Login**.
3. On success, redirects to the chat page.

### 2.3 Design

The login page uses a split-panel brand design: left 40% is the jellyfish Logo brand area (gradient + breathing animation + pixel dot texture), right 60% is the form area. Stacks vertically on narrow screens (~900px and below).

---

## 3. Interface Overview

### 3.1 Main Interface Layout

```
┌──────────┬──────────────────────────────────────┐
│ Sidebar   │                                      │
│ (240px)   │       Content Area                   │
│           │                                      │
│ ┌──────┐  │                                      │
│ │User  │  │                                      │
│ │row + │  │                                      │
│ │gear  │  │                                      │
│ ├──────┤  │                                      │
│ │      │  │                                      │
│ │Conv  │  │                                      │
│ │list /│  │                                      │
│ │Settings│ │                                      │
│ │menu  │  │                                      │
│ ├──────┤  │                                      │
│ │Logo  │  │                                      │
│ │+theme│  │                                      │
│ │+file │  │                                      │
│ │+...  │  │                                      │
│ │+exit │  │                                      │
│ └──────┘  │                                      │
└──────────┴──────────────────────────────────────┘
```

**Sidebar elements** (top to bottom):

- **User row**: avatar + username + settings gear button (enters Settings Center)
- **Content area**: conversation page shows conversation list; settings page shows settings nav menu
- **Brand area**: JellyfishBot Logo + name (click to collapse sidebar from 240px → 64px)
- **Bottom quick actions**: System Prompt / Subagent / User Profile / File Panel (all with tooltips) + **Theme toggle** (Sun/Moon/Terminal three-state cycle)
- **Bottom user area**: Avatar + username + logout button

**Top right**: File panel button (visible only on chat page).

### 3.2 Page Navigation

| Path | Page | Entry |
|------|------|-------|
| `/` | Chat | Default home |
| `/settings/prompt` | Operation Rules (System Prompt + Memory & Soul + Capability Prompts) | Settings → Prompt |
| `/settings/subagents` | Subagent Management | Settings → Subagent Management |
| `/settings/packages` | Python Environment | Settings → Python Environment |
| `/settings/inbox` | Inbox | Settings → Inbox (with unread badge) |
| `/settings/general` | General (API Key + Timezone + Theme + Batch Run + Advanced) | Settings → General |
| `/settings/services` | Service Management | Settings → Service Management |
| `/settings/scheduler` | Scheduled Tasks | Settings → Scheduled Tasks |
| `/settings/wechat` | WeChat Integration | Settings → WeChat |

### 3.3 Multi-Theme Switching

The sidebar bottom Sun/Moon/Terminal icon cycles through themes, or choose in **Settings → General → Theme**.

| Theme | Style |
|-------|-------|
| **dark** (default) | Warm pink-purple dark |
| **cyber-ocean** | Cyan-blue light |
| **terminal** | Phosphor-green CRT terminal (global monospace + no border radius + phosphor glow + scanlines) |

Theme choice persists in browser `localStorage`.

---

## 4. Chat

The chat page is JellyfishBot's core interface for streaming conversations with the AI Agent.

### 4.1 Conversation List

- Located in the sidebar, shows all historical conversations
- Click **+** button to create a new conversation
- Click a conversation entry to switch to it
- Hover shows delete button
- Switching back to a conversation still streaming in background auto-restores display

### 4.2 Message Input

Bottom input area contains:

- **Text input**: multi-line input (Shift+Enter for newline, Enter to send)
- **Capability switches**: click to expand capability selection bar
  - 🌐 **Web**: enable web search and fetching (always enabled for Admin by default)
  - 🎨 **Image**: enable AI image generation
  - 🔊 **Voice**: enable TTS voice generation
  - 🎬 **Video**: enable AI video generation
- **Plan Mode**: enable planning mode, Agent plans first then executes (requires approval)
- **Model selector**: dropdown to select AI model for current conversation
- **Image attachments**: three ways to add images
  - 📎 Click attachment button to select file
  - Ctrl+V to paste clipboard image
  - Drag image to input area
- **Voice input**: click microphone button to start recording → click again to stop → auto-transcribed into input field; press Esc during recording to cancel
- **Send/Stop button**:
  - Normal state shows send button (paper plane icon)
  - During streaming shows red Stop button — click to abort current reply

### 4.3 Streaming Message Display

AI replies display in streaming mode, containing the following block types:

#### Text Blocks
- Markdown rendered with code highlighting (17 common languages)
- Code blocks use JetBrains Mono font
- **Media embedding**: auto-recognizes `<<FILE:/generated/xxx.png>>` tags, displays images/audio/video/PDF/HTML

#### Thinking Blocks
- Shows AI's reasoning process (Thinking models only)
- Collapsible/expandable (click header bar)
- Shows three-dot bounce animation during streaming
- Identified by Brain icon

#### Tool Calls (Tool Indicator)
- Shows tool name and argument preview
- Shows spinning loader while executing
- Shows green checkmark when done
- Results are expandable
- Identified by Wrench icon

#### Subagent Cards
- Shows subagent task description
- Internal tool calls displayed in chronological order (text/tool/thinking timeline)
- Streams subagent replies in real time
- Identified by Robot icon

#### Approval Cards (HITL)
- **File operation approval**: shows file modification diff preview, choose approve or reject
- **Plan approval**: shows Agent's execution plan, can edit before approving or rejecting
- Approval buttons use Check (approve) and X (reject) icons

### 4.4 History Messages

- User messages appear on the right (pink-purple gradient bubble)
- AI messages appear on the left (with jellyfish logo avatar)
- History messages replay in interleaved order (thinking → text → tool → text → subagent → text…)
- User attachments displayed as thumbnail gallery, click to enlarge

### 4.5 Smart Scroll

- New messages auto-scroll to bottom
- When user scrolls up to browse history, auto-scroll **pauses**
- Returns to bottom, auto-scroll **resumes**

### 4.6 Stream Recovery

- On backend crash / network interruption, already-generated partial content auto-persists (marked ⚠️ [Connection interrupted — saved generated content])
- Switching back to a conversation still streaming in background shows yellow banner with **"Abort & Save"** and **"Refresh Status"** buttons
- Cannot send new messages while streaming (prevents conflicts)

---

## 5. File Panel

Click the 📁 button in the top right to open the file panel (available only on the chat page).

### 5.1 Browsing

Tree-structured browsing of user's virtual filesystem:

| Directory | Purpose |
|-----------|---------|
| `docs/` | Document directory (upload reference materials, Agent can read) |
| `scripts/` | Script directory (Python scripts, Agent can execute) |
| `generated/` | AI-generated files (images/audio/video, auto-written) |
| `soul/` | Soul memory notes (visible when Soul filesystem is enabled) |

### 5.2 Operations

- **Upload**: drag-drop or select files to upload to specified directory
- **Download**: click download button or toolbar download icon
- **Rename**: right-click menu or long-press
- **Move**: drag to target folder
- **Delete**: right-click menu
- **Diff view**: view differences between file versions

### 5.3 File Preview Panel (Multi-type Viewer)

Different file types use different rendering strategies:

| Type | Rendering |
|------|-----------|
| Image / Audio / Video / PDF | Native playback / preview, toolbar hides "Save" button |
| Markdown | Reuses chat page markdown rendering (code highlighting + media embedding) |
| HTML | `<iframe>` rendering (allows Plotly/ECharts but blocks parent page cookie access) |
| CSV / TSV | antd table rendering (max 2000 rows, shows notice when exceeded) |
| JSON / JSONL | Syntax-highlighted preview, falls back to source on parse failure |
| Text / Code | Simple textarea editor (no syntax highlighting for editing) |
| Binary | Placeholder + download button |

Toggle types (Markdown/HTML/CSV/JSON) get a **Preview/Source** switcher in the toolbar header.

### 5.4 Typical Use Cases

| Scenario | Action |
|----------|--------|
| Upload reference documents | Upload PDF/TXT/CSV etc. to `docs/`, Agent auto-references in conversation |
| Manage scripts | Write or upload Python scripts to `scripts/`, Agent can execute in sandbox |
| View generated content | View AI-generated images, audio, video in `generated/` |
| Soul memory | With Soul enabled, edit Agent personality notes in `soul/` |

---

## 6. Settings Center

Click the ⚙️ gear button on the right of the sidebar user row to enter Settings Center. The settings center sidebar shows the settings navigation menu; click the ← back button in the top left to return to the chat page.

### 6.1 Operation Rules

**Path**: Settings → Prompt

Contains three tabs (some tabs hidden by default, enable in **General → Advanced Features**):

#### 6.1.1 Profile (User Profile)

- Configure Agent's knowledge about you (investment preferences, professional background, personalization notes, etc.)
- **Version management**: save any version, supports Diff comparison and one-click rollback
- User profile injected into System Prompt via `{user_profile_context}` placeholder

#### 6.1.2 Operation Rules (System Prompt + Capability Prompts)

Shown only when **General → Advanced Features → Operation Rules** is enabled.

- **Edit Prompt**: modify current System Prompt in text editor
- **Save version**: add a label and note to each modification
- **Version history**: view all historical versions
- **Diff comparison**: view differences between two versions
- **Rollback**: one-click rollback to any historical version
- **Reset**: restore to system default Prompt
- **Capability Prompts**: collapsible panel — expand to edit/restore defaults for each item (e.g., what timezone format the scheduler must use, how to use `<<FILE:>>` tags for media generation)

#### 6.1.3 Memory & Soul

Shown only when **General → Advanced Features → Memory & Soul** is enabled.

- **Memory Subagent Write**: Switch
  - Off: Memory Subagent can only read conversations and inbox
  - On: Memory Subagent can create/edit/delete notes in `filesystem/soul/` (Agent actively summarizes memory into long-term personality)
  - Includes editable capability prompt below
- **Soul Filesystem**: Switch
  - Off: main Agent cannot see `/soul/` directory
  - On: main Agent gets read/write access to `/soul/` in file panel and tools
  - Includes editable capability prompt below
- **Include Consumer Conversations**: Switch — whether Memory Subagent can read Service Consumer conversation history
- **Recent Message Count**: default 5 (injected into scheduled task and inbox agent prompt prefix)

> Detailed Soul system explanation in §9.

### 6.2 Subagent Management

**Path**: Settings → Subagent Management

Configure subagents that the main Agent can call, enabling collaboration on complex tasks.

**Features**:
- **Create subagent**: set name, description, model to use, available tools
- **Edit config**: modify existing subagent configuration
- **Delete subagent**: remove unwanted subagents
- **Tool config**: check from available tool pool (includes web/media/scheduler/memory/soul, etc.)
- **Model selection**: assign independent model to each subagent (can differ from main Agent)

**Built-in Memory Subagent**:
- Provided by default, cannot delete (can disable)
- Default tools: read Admin conversations / read Service Consumer conversations / read inbox
- After enabling Memory Subagent Write: adds soul write tools (list/read/write/delete)

**Subagent Available Tool Pool**:

| Category | Tools |
|----------|-------|
| General | `run_script` / `web_search` / `web_fetch` / `generate_image` / `generate_speech` / `generate_video` / `schedule_task` / `manage_scheduled_tasks` / `publish_service_task` / `send_message` |
| Memory | `list_conversations` / `read_conversation` / `list_service_conversations` / `read_service_conversation` / `read_inbox` / `soul_list` / `soul_read` / `soul_write` / `soul_delete` |

### 6.3 Python Environment (per-user venv)

**Path**: Settings → Python Environment

Each Admin has an independent Python virtual environment (`users/{your-username}/venv/`) for script execution.

**Features**:
- **Initialize environment**: first-time use requires clicking **Initialize** to create venv (inherits system pre-installed packages: numpy/pandas/matplotlib, etc.)
- **Install packages**: enter package name (e.g., `requests`, `scikit-learn`) and click install
- **Uninstall packages**: click delete in the installed packages list
- **View installed packages**: list with version numbers

**Security restrictions**:
- Package names cannot contain `;|&$\`` injection characters
- pip operations only run in your venv, don't affect other users
- Persistence: installed packages recorded in `users/{your-username}/venv/requirements.txt`, auto-restored on restart/Docker rebuild

### 6.4 Inbox

**Path**: Settings → Inbox (sidebar menu shows unread message count badge)

Receives messages from Service Agents: when a Service Consumer triggers the `contact_admin` tool in conversation, or certain Service tasks require admin decisions, messages appear here.

**Features**:
- **Message list**: displays unread / read / processed messages
- **Unread count**: sidebar menu shows unread count badge
- **Mark status**: mark messages as read / processed / delete
- **View details**: view complete message content, source (Service / conversation ID), urgency level

**Auto-forward to WeChat (Smart Inbox Agent)**:

If you've connected Admin's WeChat via [§6.8 WeChat Integration](#68-wechat-integration-admin-self-onboarding), each received inbox message triggers an **Inbox Agent**:
- Automatically evaluates message urgency and context
- Decides whether to forward to your WeChat (avoids unwanted notifications)
- Sends a summary rather than the full original text

**Message Source Labeling**:

| Source | Label |
|--------|-------|
| Service Consumer active call | `contact_admin` tool |
| Service scheduled task | `contact_admin` triggered by `[System Instruction - From Admin]` |
| Inbox Agent self-evaluation | `[System Instruction - Service Inbox Notification]` |

### 6.5 General

**Path**: Settings → General

#### 6.5.1 API Keys (Strongly Recommended)

Each Admin can configure their own API Key, **taking priority over environment variables**. AES-256-GCM encrypted storage.

| Type | Fields |
|------|--------|
| Anthropic | `anthropic_api_key` + `anthropic_base_url` |
| OpenAI | `openai_api_key` + `openai_base_url` |
| Tavily | `tavily_api_key` |
| Multimedia (as needed) | `image_*` / `tts_*` / `video_*` / `s2s_*` / `stt_*` key + base_url |

**Operations**:
- **Edit**: click to expand collapsible panel, enter Key
- **Test connection**: verify connectivity
- **Save**: auto-encrypted storage + clears Agent cache (takes effect on next request)

> If neither per-admin Key nor environment variable is configured, a guidance modal will appear after login.

#### 6.5.2 Timezone Settings

- Set default timezone (affects: cron expression interpretation in scheduled tasks, chat message timestamp injection)
- Timezone changes sync to `users/{uid}/preferences.json`

#### 6.5.3 Theme Selection

Detailed theme switching + preview (dark / cyber-ocean / terminal).

#### 6.5.4 Advanced Feature Switches

Controls visibility of Advanced Tab in **Settings → Prompt**:
- **Operation Rules** Switch: whether to show System Prompt editor + capability prompts
- **Memory & Soul** Switch: whether to show Soul configuration page

> Off by default. Beginners don't need to see it, preventing accidental changes.

#### 6.5.5 Batch Run (Embedded BatchRunner)

Batch execute Agent tasks through Excel files — suitable for data processing, batch analysis, etc.

**Usage flow**:

1. **Upload Excel**: select Excel file (`.xlsx`) with task data
2. **Configure run**:
   - Select input column (used as Agent's message)
   - Select model to use
   - Configure capability switches
   - Set custom Prompt (optional)
3. **Start run**: batch tasks begin executing
4. **View progress**: real-time display of current progress (completed/total)
5. **Download results**: when done, download result Excel with AI replies

**Notes**:
- Each row executes independently, not affecting others
- Supports mid-run cancellation
- Result file contains original data + AI reply column

> Old link `/settings/batch` auto-redirects to `/settings/general`.

### 6.6 Service Management

**Path**: Settings → Service Management

Publish a configured Agent as a Service for external consumers (Consumers) to use via API or WeChat QR code.

#### 6.6.1 Interface Layout

- **Left (30%)**: Service list cards
  - Green dot = published
  - Gray dot = draft
  - Selected shows brand color highlight bar on left
- **Right (70%)**: Selected Service details, divided into 4 tabs

#### 6.6.2 Basic Info

| Field | Description |
|-------|-------------|
| **Name** | Service display name |
| **Description** | Service description text |
| **Model** | Select AI model for Service |
| **System Prompt** | Optional: use a saved Prompt version, or custom |
| **User Profile** | Optional: use a user profile version (injected into prompt) |
| **Capabilities** | Check capabilities to open: `web` / `scheduler` / `image` / `speech` / `video` / `humanchat` |
| **Accessible Docs/Scripts** | Visual check tree (from `/docs/` and `/scripts/`), folder selection = entire directory |
| **Welcome Message** | Gradient large text on chat page first screen (max 300 chars) |
| **Quick Questions** | First screen chips in a row (max 80 chars each, max 6) |
| **Publish Status** | Switch between published/draft |

#### 6.6.3 API Keys

Each Service can create multiple `sk-svc-...` format API Keys for Consumers.

| Operation | Description |
|-----------|-------------|
| **Generate Key** | Create new API Key (**only visible at creation — full key shown once**; afterward only hash visible) |
| **Key-attached link** | Modal also shows `/s/{service_id}?key=sk-svc-xxx` one-click access link after creation |
| **Copy Key** | One-click copy key to clipboard |
| **Delete Key** | Revoke a key |

> ⚠️ **Key-attached link is equivalent to sharing the key**. Although the link clears from URL immediately when user visits (written to localStorage), it may leave traces in referer/gateway access logs.

#### 6.6.4 WeChat Channel

See §7 for details.

| Field | Description |
|-------|-------------|
| **Enable/Disable** | Switch to toggle WeChat QR channel |
| **Expiration** | Set channel validity period |
| **Max Sessions** | Limit simultaneous connected WeChat users |
| **QR Link** | Get WeChat scan landing page link (`/wc/{service_id}`) |
| **Active Sessions** | View currently connected WeChat users list |
| **Disconnect Session** | Actively disconnect a WeChat user |
| **View Conversation** | Enter a WeChat user's conversation history |

#### 6.6.5 Test (Inline Testing)

Test conversation with the Service directly without leaving the management page. Automatically creates a temporary API Key and test conversation.

> ⚠️ Known issue: each new test creates a new API Key without automatic cleanup (orphan Keys). Must manually delete in the API Keys tab.

### 6.7 Scheduled Tasks

**Path**: Settings → Scheduled Tasks

Manage automatically scheduled tasks, divided into **Admin Tasks** and **Service Tasks** tabs.

#### 6.7.1 Admin Tasks

**Task types**:
- **Script**: scheduled execution of Python scripts under `scripts/` directory
- **Agent**: scheduled execution of Agent tasks (send Prompt + optional document context)

**Schedule types**:
- **once**: one-time execution (specify ISO time, recommend timezone suffix, e.g., `2026-12-31T09:00:00+08:00`)
- **cron**: cron expression timing (e.g., `0 9 * * *` every day at 9am)
- **interval**: interval execution (seconds)

**Sandbox permissions** (Script tasks only):
- Configure script read/write directories (default `docs/scripts/generated/tasks`)
- Paths relative to user filesystem root

**reply_to options**:
- ☐ **No push**: only record run results
- ☐ **Push to my WeChat**: deliver via bound Admin WeChat (requires §6.8 integration first)

#### 6.7.2 Service Tasks

Dispatched by Admin to a Service, or created by Consumer in conversation via `schedule_task` tool.

- Only supports **Agent** type (no scripts)
- Executes using Consumer Agent (per Service's capabilities + document restrictions)
- **reply_to routing** (determines where results are pushed):

| Channel | Target | Use Case |
|---------|--------|---------|
| `wechat` | Push to WeChat user (source session) | Service WeChat channel users |
| `inbox` | Write to Admin inbox (auto-evaluate for forwarding) | Requires admin review |
| `admin_chat` | Write to Admin's regular conversation | Service actively reports |

- Service task list shows service_id, 📬 push marker, reply_to info
- Service's `manage_scheduled_tasks` can only operate tasks for the current conversation (permission isolation)

#### 6.7.3 Run Records

Each execution records detailed step logs:

| Step | Meaning |
|------|---------|
| `start` | Execution begins |
| `docs_loaded` | Documents loaded (Agent only) |
| `loop` | Agent loop iteration |
| `tool_call` / `tool_result` | Tool invocation and result |
| `ai_message` | AI message |
| `auto_approve` | Auto-approval (HITL) |
| `wechat_warning` / `wechat_error` | WeChat delivery warning/error |
| `finish` | Completed |
| `error` | Error |
| `reply` | Fallback delivery |

Can view elapsed time, error messages, and complete steps for each run.

#### 6.7.4 Run Now

The **Run** button on the right of each task list row triggers an immediate execution (doesn't affect next scheduled run).

### 6.8 WeChat Integration (Admin Self-onboarding)

**Path**: Settings → WeChat Integration

Connect your **main Admin Agent** via WeChat iLink protocol, enabling direct WeChat conversations with your JellyfishBot.

> This is completely **independent** of [§7 Service Channel](#7-service-channel-let-consumers-use-via-wechat-qr): Admin onboarding uses your main Agent (full permissions), Service channel serves Consumers (restricted permissions).

#### 6.8.1 Onboarding Flow

1. Go to **Settings → WeChat Integration**, click **Generate QR Code**.
2. Scan the QR code with WeChat (this is the iLink Bot protocol scan login).
3. After successful scan:
   - Status automatically changes to **Connected**
   - Sending messages to JellyfishBot in WeChat, the bot replies
4. **After first scan**, your WeChat account binding is persisted to `users/{your-username}/admin_wechat_session.json`, auto-restored after Docker / service restart.
5. Active disconnect: click **Disconnect** button, or cancel authorization on WeChat side.

#### 6.8.2 Multimodal Support

- **Receive images**: CDN download → AES decrypt → automatically sent as multimodal message to GPT-4o / Claude (Vision capability)
- **Receive voice**: CDN download → AES decrypt → SILK→WAV → Whisper transcription
- **Send images/videos**: auto-triggered via `<<FILE:>>` tag (images go through iLink CDN upload, videos/MP3 as file attachments)
- **Send TTS voice**: sent as file attachment (voice message currently unavailable)

#### 6.8.3 Management Interface

- **Status card**: shows current connection status, bound WeChat user identifier
- **Message list**: view WeChat conversation records (accessible via `/api/admin/wechat/messages` API)
- **Disconnect button**: actively disconnect current connection

#### 6.8.4 Main Use Cases

| Scenario | Action |
|----------|--------|
| Ask Agent simple questions via WeChat while out | Send message directly in WeChat |
| Receive scheduled task push | Select "Push to my WeChat" in reply_to for §6.7 task |
| Receive important Service inbox notifications | Inbox Agent auto-evaluates forwarding (see §6.4) |
| Let Agent actively report | Admin dispatches task via `publish_service_task`, Service reports back to WeChat when done |

---

## 7. Service Channel: Let Consumers Use via WeChat QR

> This is a completely **separate** WeChat stack from [§6.8 Admin Self-onboarding](#68-wechat-integration-admin-self-onboarding), dedicated to serving Consumers.

### 7.1 Enable Flow

1. In **Settings → Service Management**, select (or create) a Service
2. Switch to the **WeChat Channel** tab
3. Configure:
   - Enable Switch
   - Expiration time (QR link validity)
   - Max sessions (upper limit of simultaneous connected WeChat users)
4. Click **Copy QR Link** or open the `/wc/{service_id}` landing page directly

### 7.2 Consumer Scan Flow

1. Consumer scans the `/wc/{service_id}` landing page QR with WeChat
2. Landing page prompts user to follow iLink Bot in WeChat and send any message
3. Backend `session_manager` waits for user's first message (captures `from_user_id`)
4. Once captured, creates an independent `conversation_id` for that user
5. Subsequent messages processed by Consumer Agent + replied via iLink

### 7.3 Consumer Experience

- All received messages processed by Service's configured Consumer Agent
- Agent only has permissions from Service `capabilities` + `allowed_docs/scripts`
- Multimodal: supports send/receive images and voice
- Friendly tool status: when Agent uses tools, shows "Thinking…" or whitelisted friendly text — doesn't expose real tool names

### 7.4 Session Management

In Service Management → **WeChat Channel** tab you can:
- **View active sessions**: each WeChat user's recent activity time, message count
- **Disconnect session**: actively disconnect a user
- **View conversation**: enter a WeChat user's conversation history

### 7.5 Rate Limits

- Per-user: 10 messages / 60 seconds
- QR generation: 5 times / 60 seconds
- Global: Service's configured `max_sessions` limit

### 7.6 Notes

- iLink Bot (domestic) requires **direct connection, no proxy**
- Multi-Admin isolation: each Service's sessions don't cross Admin boundaries
- Sessions **retained long-term** (sessions with `from_user_id` don't participate in 24h idle cleanup)

---

## 8. Consumer Usage (External API Integration)

### 8.1 Overview

The Consumer layer serves external users and systems, accessing AI Agent capabilities authenticated with Service API Keys.

### 8.2 Authentication

All Consumer API requests require in the HTTP header:

```
Authorization: Bearer sk-svc-xxxxxxxxxxxxx
```

### 8.3 API Endpoints

#### 8.3.1 Create Conversation

```bash
curl -X POST http://your-host/api/v1/conversations \
  -H "Authorization: Bearer sk-svc-xxx" \
  -H "Content-Type: application/json" \
  -d '{"title": "New Conversation"}'
```

#### 8.3.2 Send Message (Custom SSE)

```bash
curl -X POST http://your-host/api/v1/chat \
  -H "Authorization: Bearer sk-svc-xxx" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"conversation_id": "conv-id", "message": "Hello"}'
```

SSE event types match Admin side: `token` / `thinking` / `tool_call` / `tool_result` / `done` / `error`, etc. (see [Developer Guide §5.5](DEVELOPER_GUIDE_EN.md#55-sse-streaming)).

#### 8.3.3 OpenAI-Compatible Endpoint

```bash
curl -X POST http://your-host/api/v1/chat/completions \
  -H "Authorization: Bearer sk-svc-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

Fully compatible with OpenAI API format — directly replace `base_url` in existing OpenAI SDK code.

#### 8.3.4 Multimodal Messages

Consumer supports sending images + text:

```json
{
  "conversation_id": "conv-id",
  "message": [
    {"type": "text", "text": "What is in this image?"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ]
}
```

GPT-4o / Claude Sonnet/Opus support natively.

#### 8.3.5 Get Conversation History

```bash
curl http://your-host/api/v1/conversations/conv-id \
  -H "Authorization: Bearer sk-svc-xxx"
```

#### 8.3.6 List Generated Files

```bash
curl http://your-host/api/v1/conversations/conv-id/files \
  -H "Authorization: Bearer sk-svc-xxx"

# Download a file (query param carries key to support <img src>)
GET /api/v1/conversations/conv-id/files/images/xxx.png?key=sk-svc-xxx
```

#### 8.3.7 User Attachments

```bash
# User attachments stored in query_appendix/
GET /api/v1/conversations/conv-id/attachments/images/abc.jpg
```

### 8.4 Standalone Chat Page

Each Service has an accessible standalone chat web page:

```
http://your-host/s/{service_id}
http://your-host/s/{service_id}?key=sk-svc-xxx   # One-click access
```

The page is a React application (Vite multi-entry), FastAPI injects Service config + key before rendering. Key is written to localStorage then immediately cleared from URL.

### 8.5 send_message Tool Behavior

If your Service has `humanchat` capability enabled:

- **Web channel** (`/api/v1/*` or `/s/{sid}`): **does not** inject `send_message` tool — Agent output already streams to browser
- **WeChat channel** (Service WeChat QR): injects `send_message` — when Agent calls it, backend intercepts and delivers via iLink to the corresponding WeChat user
- **Scheduler channel** (scheduled tasks): same as WeChat

#### 8.5.1 Auto Media Sending (`<<FILE:>>` Tags)

When Agent outputs `<<FILE:/generated/images/xxx.png>>` tag in `send_message` text, backend **automatically**:
1. Extracts tags from text
2. Sends corresponding file separately (image/video/voice/PDF)
3. Sends extracted plain text as the message

This matches the chat page markdown rendering convention. Consumer side needs no special handling.

---

## 9. Soul Memory System

> Soul is JellyfishBot's core mechanism for Agents to "grow" long-term, inspired by giving the Agent a "soul" that remembers user preferences and conversation highlights.

### 9.1 Concept

```
┌────────────────────────────────────────────┐
│  Soul                                      │
│                                            │
│  📁 filesystem/soul/    ← Agent read/write │
│     ├── about_user.md  (about the user)    │
│     ├── preferences.md (preferences)       │
│     ├── insights.md    (observations)      │
│     └── ...            (any notes)         │
│                                            │
│  📁 soul/                                  │
│     └── config.json    (app-layer config)  │
└────────────────────────────────────────────┘
```

### 9.2 Three Usage Modes

#### Mode 1: Pure Short-term Memory (Default)

- `memory_enabled` on by default
- Scheduled tasks and inbox agent prompt prefix **automatically** injects most recent 5 conversation messages
- Requires no configuration

#### Mode 2: Memory Subagent Active Writing

- Enable **Memory Subagent Write** in **Settings → Prompt → Memory & Soul** (must first enable Advanced Features in General)
- Main Agent at appropriate times calls Memory Subagent, letting it read conversation history and summarize into `filesystem/soul/`
- Best for: you want Agent to autonomously decide what to remember and how

#### Mode 3: Direct Soul Filesystem Exposure

- Enable **Soul Filesystem** at the same path
- Main Agent gets direct read/write access to `/soul/` directory (visible in file panel and tools)
- Best for: you want Agent to reference Soul in every reply, or Agent needs to update personality notes in real time during conversation

### 9.3 Custom Capability Prompts

Memory & Soul tab has editable capability prompts below each Switch, telling Agent how to use these capabilities. Can restore defaults.

### 9.4 Include Consumer Conversations

After enabling **Include Consumer Conversations** Switch:
- Memory Subagent can read not just Admin's own conversations, but also Service Consumer conversations
- Best for: you want Agent to learn from user feedback across different Services

### 9.5 File Management

After enabling Soul filesystem:
- File panel shows `/soul/` directory
- Can manually edit (e.g., write initial "user profile" notes by hand)
- Agent can also read/write

> Old version `users/{uid}/soul/` content is auto-migrated to `users/{uid}/filesystem/soul/`, no manual handling needed.

---

## 10. Voice Interaction

### 10.1 Voice Input (Chat Box)

- Click microphone button to start recording
- Click again to stop → auto-transcribed into input field
- Press **Esc** during recording to cancel (doesn't send)
- No global keyboard shortcut (avoids breaking accessibility focus navigation)
- Transcription depends on `STT_API_KEY` (defaults to OpenAI Whisper)

### 10.2 Realtime Voice Conversation (S2S WebSocket)

- Direct WebSocket connection to OpenAI Realtime API
- Supports bidirectional streaming voice (speak → AI listens → AI streams reply)
- Transparent tool calls + backend tool execution
- API Key override via `S2S_API_KEY` / `S2S_BASE_URL`

> ⚠️ The current React frontend realtime voice UI is still iterating. Can test via `/api/voice/ws` WebSocket endpoint with third-party clients.

---

## 11. Environment Variable Configuration

> Tip: From v2.x, all API Keys can be configured per-user in **Settings → General → API Keys** (AES-256-GCM encrypted storage), taking priority over environment variables. Environment variables are mainly for initial deployment or fallback.

### 11.1 Required (at least one)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (Claude series) |
| `OPENAI_API_KEY` | OpenAI API key (GPT + multimedia generation) |

### 11.2 Provider Endpoint Overrides

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_BASE_URL` | Custom Anthropic API endpoint |
| `OPENAI_BASE_URL` | Custom OpenAI API endpoint |

### 11.3 Per-Capability Overrides

When a capability needs a different API Key or endpoint from the main provider:

| Capability | Key Variable | URL Variable |
|------------|-------------|-------------|
| Image generation | `IMAGE_API_KEY` | `IMAGE_BASE_URL` |
| TTS voice | `TTS_API_KEY` | `TTS_BASE_URL` |
| Video generation | `VIDEO_API_KEY` | `VIDEO_BASE_URL` |
| Realtime voice S2S | `S2S_API_KEY` | `S2S_BASE_URL` |
| Speech-to-text STT | `STT_API_KEY` | `STT_BASE_URL` |

### 11.4 Web Search Tools

| Variable | Description |
|----------|-------------|
| `CLOUDSWAY_SEARCH_KEY` | CloudsWay search API (preferred) |
| `CLOUDSWAY_READ_URL` | CloudsWay web fetch endpoint (optional override) |
| `CLOUDSWAY_SEARCH_URL` | CloudsWay search endpoint (optional override) |
| `TAVILY_API_KEY` | Tavily search API (fallback) |

### 11.5 S3 Storage (Optional)

| Variable | Description |
|----------|-------------|
| `STORAGE_BACKEND` | `local` (default) or `s3` |
| `S3_BUCKET` | S3 bucket name |
| `S3_REGION` | S3 region |
| `S3_ENDPOINT_URL` | Custom endpoint (MinIO/R2/OSS) |
| `S3_ACCESS_KEY_ID` | S3 access key |
| `S3_SECRET_ACCESS_KEY` | S3 secret key |
| `S3_PREFIX` | S3 key prefix (optional) |

> Note: JSON config files (users.json, conversations, etc.) currently remain on local disk. S3 mode only hosts the filesystem layer (`docs/`, `scripts/`, `generated/`, `soul/`).

### 11.6 Encryption Master Key

| Variable | Description |
|----------|-------------|
| `ENCRYPTION_KEY` | AES-256-GCM master key for per-admin API Keys (if not set, auto-generates `data/encryption.key` on first startup) |

> In production, strongly recommend explicitly setting `ENCRYPTION_KEY` and backing it up properly. If the master key file is lost, all users' API Keys will be unrecoverable.

### 11.7 Script Sandbox Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `SCRIPT_CONCURRENCY` | 4 | Global concurrent script count |
| `SCRIPT_QUEUE_TIMEOUT` | 180 | Queue timeout (seconds) |

### 11.8 Port Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_PORT` | 8000 | FastAPI port (Docker) |
| `FRONTEND_PORT` | 3000 | Express/Vite port |
| `API_TARGET` | `http://localhost:8000` | Express proxy target |

### 11.9 Observability

| Variable | Description |
|----------|-------------|
| `LANGFUSE_SECRET_KEY` | Langfuse secret key |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key |
| `LANGFUSE_HOST` | Langfuse server address |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith |
| `LANGCHAIN_API_KEY` | LangSmith API Key |

---

## 12. FAQ

### Q: "Invalid registration code" on registration?

A: Confirm `config/registration_keys.json` exists and contains valid codes.
- Command line: `python generate_keys.py` to generate new codes
- Desktop App: go to "Registration Code Management" tab to generate
- Each registration code can only be used once

### Q: Frontend cannot connect to backend?

A: Verify:
1. Backend FastAPI is running on `:8000` (test at <http://localhost:8000/docs>)
2. Vite dev server running on `:3000` (auto-proxies `/api` → `:8000`)
3. For Docker, check `docker compose logs -f` to confirm both services are ready
4. For Desktop App, open "About / Tools" → "Log Directory" to troubleshoot

### Q: Model list is empty?

A: Need to configure at least one valid API Key:
- `ANTHROPIC_API_KEY`: enables Claude series models
- `OPENAI_API_KEY`: enables GPT series models + multimedia capabilities

Or configure your own Key in **Settings → General → API Keys** (recommended).

### Q: Web search unavailable?

A: Admin Agent enables web tools by default, but requires search API configuration:
- `CLOUDSWAY_SEARCH_KEY` (recommended, used first)
- `TAVILY_API_KEY` (fallback)

Can configure in **Settings → General → API Keys**.

### Q: Image/voice/video generation fails?

A: Confirm `OPENAI_API_KEY` is configured. To use a different API Key or endpoint, override separately with `IMAGE_API_KEY` / `TTS_API_KEY` / `VIDEO_API_KEY` (works as environment variables or per-admin settings).

### Q: WeChat QR scanned but messages not received?

A: Follow this checklist:
1. Confirm iLink Bot service is running (after scan, status should show "Connected")
2. Check `wechat.*` logs in backend (requires `LOG_LEVEL=INFO`)
3. iLink (domestic) requires **direct connection, no proxy**
4. Check if WeChat session expired (`context_token` invalid), rescan
5. Admin WeChat and Service WeChat are two independent stacks — don't confuse them

### Q: WeChat shows literal `<<FILE:...>>` string?

A: Should be auto-parsed by `delivery.py::extract_media_tags`. If still appearing:
1. Check if file path actually exists
2. Check backend logs for media download failures
3. Confirm this isn't text from non-`send_message` tool calls (e.g., `web_search` results)

### Q: Script execution fails / "queue busy"?

A: Scripts execute in a sandbox with the following restrictions:
- Cannot import dangerous modules (subprocess, pathlib, ctypes, io, pickle, threading, etc.)
- Cannot use dangerous builtins (exec, eval, getattr, setattr, etc.)
- Use relative paths for files (`../docs/file.csv`, not `/docs/file.csv`), script cwd is `scripts/` directory
- Memory limit 1024 MB, process count limit 256
- Global concurrency 4 scripts (adjustable via `SCRIPT_CONCURRENCY`)
- Queue timeout 180 seconds (adjustable via `SCRIPT_QUEUE_TIMEOUT`)

### Q: Scheduled task not triggering?

A:
1. Check task's `next_run` field (visible in run records page)
2. Confirm timezone is correct: **General → Timezone**
3. Cron expressions interpreted in your configured timezone
4. once type must include timezone suffix (e.g., `2026-12-31T09:00:00+08:00`)
5. Scheduler checks every 30s, so execution may be delayed by seconds

### Q: Inbox has messages but didn't auto-forward to my WeChat?

A:
1. Confirm you've connected Admin WeChat via **Settings → WeChat Integration**
2. Check if `users/{your-username}/admin_wechat_session.json` exists
3. Inbox Agent **intelligently evaluates** whether forwarding is worthwhile — not every message is forwarded
4. View inbox agent run logs (visible in inbox detail page)

### Q: How to back up user data?

A:
- **Local mode**: back up `users/` and `data/` directories (includes checkpoints.db, encryption.key)
- **Docker mode**: back up `./data/users/` directory
- **S3 mode**: S3 storage has built-in redundancy, but JSON configs remain local — must back up both local `users/{uid}/` (except `filesystem/`) and `data/`
- **Important**: If you set `ENCRYPTION_KEY`, note that value; otherwise back up `data/encryption.key` (master key) — loss means all API Keys are unrecoverable

### Q: Tauri Desktop App on Windows reports `Cannot load native module 'Crypto.Util._cpuid_c'`?

A: This is a fixed Windows `\\?\` extended long path prefix bug. Please:
1. Upgrade to the latest Desktop App version
2. If self-building, confirm `lib.rs` includes `strip_win_extended_prefix()` helper
3. See [Developer Guide §12.5.1](DEVELOPER_GUIDE_EN.md#1251-windows--extended-long-path-prefix-critical-bug-2026-04-20)

### Q: Is the per-admin API Key configuration secure?

A:
- AES-256-GCM encrypted storage in `users/{uid}/api_keys.json`
- Master key in `data/encryption.key` (or `ENCRYPTION_KEY` environment variable)
- Frontend only reads masked version (e.g., `sk-...abc123`)
- Transmitted over HTTPS (must enable SSL in production)
- **Strictly protect the master key file**: all user API Keys are unrecoverable if lost

### Q: Can multiple Admins share one API Key?

A: No. Each Admin configures independently in `users/{uid}/api_keys.json`. For centralized management, configure a global default key in environment variables — all users without individual config will use it as fallback.

### Q: How is Service data isolated?

A: Strict isolation:
- Each Service under `users/{admin_id}/services/{service_id}/`
- Each Consumer conversation in `users/{admin_id}/services/{svc_id}/conversations/{conv_id}/`
- Consumer generated files in their conversation's `generated/` directory
- Consumer cannot see Admin filesystem, other Services, or other Consumers' data

### Q: Some components have wrong colors after theme switch?

A: Most components have migrated to `--jf-*` CSS variables. If you find missed ones, check variable definitions in `frontend/src/styles/themes.css`, or change hardcoded colors to `var(--jf-primary)` etc. See [Developer Guide §5.9](DEVELOPER_GUIDE_EN.md#59-design-system--multi-theme).

### Q: How to view backend logs?

A:
- **Desktop App**: "About / Tools" → "Log Directory", daily-rolling log files
- **Command line**: see stdout directly in terminal, or `python launcher.py` subprocesses also write to `logs/{name}-YYYYMMDD.log`
- **Docker**: `docker compose logs -f jellyfishbot`

### Q: Docker deployment keeps restarting with `sqlite3.OperationalError: unable to open database file`?

A: **Data directory permission issue.** The container runs as `jellyfish` (uid=1000), but the host's `./data` directory is owned by `root`, so the in-container process can't write `checkpoints.db`.

**Fix** (run from the project root on the host):

```bash
mkdir -p ./data/users
sudo chown -R 1000:1000 ./data
docker compose restart jellyfishbot
```

**Verify**:

```bash
ls -ld ./data ./data/users
# Expected: owner is 1000:1000
```

> Note: With multiple FastAPI routers, the `merged_lifespan` is nested, so the traceback contains a long chain of repeated `routing.py:216` frames. **Only the bottom two lines matter** (the exception type and message).

### Q: After Docker deploy, the domain doesn't resolve, but `curl http://localhost` works on the server?

A: Check in this order:

1. **DNS**: `nslookup yourdomain.com` should resolve to the server's public IP; in the Cloudflare dashboard, confirm the A record is **Proxied (orange cloud)**.
2. **Firewall / security group**: Port 80 must be open to the public (cloud provider's security group, `ufw status`).
3. **Cloudflare SSL mode**: Must be **Flexible** (HTTPS at the edge, HTTP to origin). If you wrongly pick Full / Full(Strict) but the server only listens on port 80, you'll see infinite 301 redirects or a 525 error.
4. **Is nginx really on :80?**: `docker compose ps` should show `jellyfishbot-nginx` as Up with `0.0.0.0:80->80/tcp`.
5. **Streaming output arrives in chunks instead of token-by-token**: The bundled nginx already disables `proxy_buffering` for `/api/chat`. If there is another reverse proxy (custom nginx / Caddy / Traefik) in front of the server, that layer must also disable buffering and enable WebSocket Upgrade, otherwise SSE will be buffered.

---

