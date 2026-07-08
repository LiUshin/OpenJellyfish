<div align="center">

<img src="frontend/public/media_resources/jellyfishlogo.png" alt="OpenJellyfish" width="120" />

# OpenJellyfish

**A document-first AI Agent framework.**
_把智能保存在文档里，而不是 Prompt 或编排代码里。_

[English](#-english) · [中文](#-中文) · [Website](https://www.openjellyfish.ai) · [Docs](https://www.openjellyfish.ai/en/docs) · [Concepts](https://www.openjellyfish.ai/en/concepts)

![License](https://img.shields.io/badge/license-MIT-blue)
![Self-hosted](https://img.shields.io/badge/deploy-self--hosted-8B7FD9)
![Docker](https://img.shields.io/badge/docker-ready-5FC9E6)

</div>

---

## 🇬🇧 English

### What is OpenJellyfish?

OpenJellyfish is a **document-first AI Agent framework**. Instead of writing an
agent's intelligence into prompts or orchestration code, OpenJellyfish keeps it
in **documents**:

- **LLM** provides the intelligence — reasoning, language, world knowledge. Swappable at any time (GPT / Claude / local models, any OpenAI-compatible endpoint).
- **Harness** runs the intelligence — a minimal body that only does *Observe · Think · Decide · Act*. It doesn't know who it is or what it does. It stays simple forever.
- **Documents** define the intelligence — Prompt, Memory, Skills, Workflow, Permissions, and Knowledge are all documents: readable, editable, copyable, version-controlled.
- **Environment** projects the intelligence — Filesystem, Tools, MCP, Scheduler, External Services. The world the agent reaches out to.

> `LLM + Harness + Documents + Environment = Agent`

The key idea that sets OpenJellyfish apart: **Documents are not a config file for
the harness — they are the intelligence itself.** Swap the documents and you have
a completely different agent, on the very same runtime.

Because an agent is just a set of documents, intelligence becomes **portable**:

```bash
git clone   # copy an entire intelligence
git diff    # watch an agent grow
git merge   # fuse two agents into one
```

### Why document-first?

| Traditional agent | OpenJellyfish |
| --- | --- |
| Intelligence baked into prompts & code | Intelligence lives in documents |
| Memory hidden in a vector DB | Memory is readable, browsable documents |
| Behavior tied to a specific runtime | Behavior travels with the documents |
| Hard to copy, diff, or migrate | `git clone` / `git diff` / `git merge` |
| Permissions in config tables | Permissions = document paths (natural boundaries) |

### ✨ Highlights

- **Document-native memory** — long-term, layered memory as editable notes, not an opaque store.
- **Path-based isolation** — document paths *are* permission boundaries; different boundaries = different agents.
- **Self-editing agents** — an agent can rewrite its own documents, because the documents *are* the agent.
- **Multi-model** — Claude 4.x (w/ Thinking), GPT-5.x, GPT-4o, o3-mini, and any OpenAI-compatible endpoint.
- **Self-hosted** — runs on your laptop, cloud server, or Raspberry Pi. One Docker command for the full platform. Your data is just files.
- **Two-tier delivery** — Admins configure agents; publish them as Services with API Keys; optional WeChat iLink for mobile conversations.
- **Scheduler & sandbox** — cron / interval / once tasks, AST-checked script sandbox, per-user Python venv.
- **Desktop app** — Tauri v2 launcher, double-click to start, no CLI needed.

### 🚀 Quick Start

#### Option 1 — Desktop App (recommended for non-developers)

Download the latest installer from [GitHub Releases](https://github.com/LiUshin/OpenJellyfish/releases/latest):

- Windows: `JellyfishBot_x.y.z_x64-setup.exe`
- macOS Apple Silicon: `JellyfishBot_x.y.z_aarch64.dmg`
- macOS Intel: `JellyfishBot_x.y.z_x64.dmg`

Install, open, enter your LLM API Key on the **Console** page, press **START**.
The browser opens at `http://localhost:3000`. Register with a code from the
**Registration Keys** tab.

#### Option 2 — Command Line

**Prerequisites:** Python 3.11+, Node.js 20+, at least one LLM API Key.

```bash
git clone https://github.com/LiUshin/OpenJellyfish.git
cd OpenJellyfish

# Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and/or OPENAI_API_KEY

# Generate registration codes (first run)
python generate_keys.py

# Install dependencies
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd frontend && npm install && cd ..

# Start everything
python launcher.py

# Or start manually:
# Terminal 1: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Terminal 2: cd frontend && npm run dev
```

Open `http://localhost:3000`, register with a generated code, and start chatting.

#### Option 3 — Docker

```bash
cp .env.example .env         # fill in API Keys
python generate_keys.py      # generate registration codes
docker compose up -d --build
# Open http://localhost (Nginx port 80)
```

### 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Clients                                                     │
│  Admin SPA (React 19)  │  Service Page (/s/{id})  │  WeChat  │
│  Tauri Desktop App     │  WeChat Scan (/wc/{id})   │  iLink  │
├─────────────────────────────────────────────────────────────┤
│  Nginx (:80) → Express (:3000, static + /api proxy)          │
│              → FastAPI (:8000, core backend)                 │
├─────────────────────────────────────────────────────────────┤
│  Harness: deepagents + LangGraph                             │
│  Per-user Agent instances · AsyncSqlite checkpoints          │
│  FilesystemBackend (Local / S3) · Subagent orchestration     │
├─────────────────────────────────────────────────────────────┤
│  Documents (the intelligence)                                │
│  prompts · memory · skills · workflows · permissions · docs  │
└─────────────────────────────────────────────────────────────┘
```

### 📚 Documentation

| Document | Description |
| --- | --- |
| [Concepts](https://www.openjellyfish.ai/en/concepts) | Core terms: document-first agent, harness, portable intelligence |
| [User Guide](docs/USER_GUIDE.md) | Full user guide (all features, WeChat, FAQ) |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | Architecture, APIs, extension guide |
| [Filesystem Architecture](docs/filesystem-architecture.md) | Filesystem layout, JSON schemas, message flows |
| [WeChat Integration](docs/wechat-integration-guide.md) | iLink WeChat integration deep-dive |

### License

MIT

---

## 🇨🇳 中文

### OpenJellyfish 是什么？

OpenJellyfish 是一个 **Document-first 的 AI Agent 框架**。它不把 Agent 的智能
写进 Prompt 或编排代码，而是保存在**文档**里：

- **LLM** 提供智能 —— 推理、语言、世界知识，随时可替换（GPT / Claude / 本地模型，OpenAI 兼容即插即用）。
- **Harness** 运行智能 —— 最小的智能躯体，只做 *Observe · Think · Decide · Act*。它不知道自己是谁、要做什么，永远保持简单。
- **Documents** 定义智能 —— Prompt、Memory、Skills、Workflow、Permissions、Knowledge 全部是文档：可阅读、可修改、可复制、可版本管理。
- **Environment** 投射智能 —— 文件系统、工具、MCP、调度器、外部服务，是 Agent 触达世界的方式。

> `LLM + Harness + Documents + Environment = Agent`

OpenJellyfish 最有原创性的地方在于：**Documents 并不是 Harness 的配置文件，
而是智能本身。** 换掉文档，就是一个完全不同的 Agent，而运行时保持不变。

因为 Agent 就是一组文档，智能变得**可迁移**：

```bash
git clone   # 复制整个智能
git diff    # 看 Agent 如何成长
git merge   # 融合两个 Agent
```

### 为什么是 Document-first？

| 传统 Agent | OpenJellyfish |
| --- | --- |
| 智能写死在 Prompt 与代码里 | 智能活在文档里 |
| 记忆藏在向量库中 | 记忆是可阅读、可翻阅的文档 |
| 行为绑定特定 Runtime | 行为随文档一起迁移 |
| 难以复制、diff、迁移 | `git clone` / `git diff` / `git merge` |
| 权限写在配置表里 | 权限即文档路径（天然边界） |

### ✨ 亮点

- **文档原生记忆** —— 分层长期记忆是可编辑的笔记，而非不透明的存储。
- **路径即边界** —— 文档路径就是权限边界；不同边界 = 不同 Agent。
- **自我改写** —— Agent 可以改写自己的文档，因为文档就是 Agent 本身。
- **多模型** —— Claude 4.x（含 Thinking）、GPT-5.x、GPT-4o、o3-mini，以及任意 OpenAI 兼容端点。
- **自托管** —— 跑在笔记本、云服务器或树莓派上，一条 Docker 命令启动完整平台，数据只属于你。
- **两层分发** —— 管理员配置 Agent，发布为带 API Key 的 Service，可选微信 iLink 移动端接入。
- **定时任务与沙箱** —— cron / interval / once 调度，AST 检查的脚本沙箱，per-user Python venv。
- **桌面 App** —— Tauri v2 启动器，双击即用，无需命令行。

### 🚀 快速开始

#### 方式一：桌面 App（推荐非开发者）

从 [GitHub Release](https://github.com/LiUshin/OpenJellyfish/releases/latest) 下载安装包：

- Windows：`JellyfishBot_x.y.z_x64-setup.exe`
- macOS Apple Silicon：`JellyfishBot_x.y.z_aarch64.dmg`
- macOS Intel：`JellyfishBot_x.y.z_x64.dmg`

安装并打开，在 **控制台** 页填入 LLM API Key，点击 **START**，浏览器自动打开
`http://localhost:3000`，用 **注册码管理** 里的注册码创建账号即可。

#### 方式二：命令行（开发者）

**前置条件**：Python 3.11+、Node.js 20+、至少一个 LLM API Key。

```bash
git clone https://github.com/LiUshin/OpenJellyfish.git
cd OpenJellyfish

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

# 一键启动
python launcher.py

# 手动启动（调试用）：
# 终端 1: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 终端 2: cd frontend && npm run dev
```

打开 `http://localhost:3000`，用注册码注册后即可开始。

#### 方式三：Docker

```bash
cp .env.example .env           # 填入 API Key
python generate_keys.py        # 生成注册码
docker compose up -d --build   # 构建并启动
# 访问 http://localhost（Nginx 端口 80）
```

### 📚 项目文档

| 文档 | 说明 |
| --- | --- |
| [核心概念](https://www.openjellyfish.ai/zh/concepts) | Document-first Agent、Harness、可迁移智能等术语 |
| [用户指南](docs/USER_GUIDE.md) | 完整用户指南（全功能、微信、FAQ） |
| [开发者指南](docs/DEVELOPER_GUIDE.md) | 架构设计、API 参考、扩展开发 |
| [文件系统架构](docs/filesystem-architecture.md) | 文件系统布局、JSON Schema、消息流时序 |
| [微信集成](docs/wechat-integration-guide.md) | iLink 微信集成实战 |

### License

MIT
