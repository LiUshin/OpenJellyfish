# JellyfishBot

<p align="center">
  <img src="frontend/public/media_resources/jellyfishlogo.png" width="120" alt="JellyfishBot Logo" />
</p>

<p align="center">
  <strong>基于 deepagents + LangGraph 构建的企业级 AI Agent 平台</strong><br>
  多用户隔离 · 服务发布 · 微信集成 · 定时任务 · 实时语音
</p>

---

## 概览

JellyfishBot 是一个功能完整的 AI Agent 管理与分发平台，支持 **Admin 管理 + Consumer 消费** 两层架构。管理员可以配置 Agent（模型、文档、脚本、System Prompt、子代理），发布为 Service 并生成 API Key 供外部消费者调用，也可通过微信 iLink 渠道扫码接入。

### 核心特性

- **多模型支持**：Claude Opus/Sonnet/Haiku 4.x（含 Thinking）、GPT-5.x、GPT-4o、o3-mini
- **SSE 流式对话**：Thinking 块、工具调用、子代理执行全程流式展示
- **HITL 审批**：文件操作 diff 预览、Plan Mode 审批与编辑
- **多模态**：图片附件（粘贴/拖拽）、语音输入转写、AI 图片/语音/视频生成
- **文件系统**：per-user 隔离的虚拟文件系统，支持本地或 S3 后端
- **脚本沙箱**：AST 静态分析 + 运行时沙箱双层安全
- **Service 发布**：API Key 认证、OpenAI 兼容接口、独立 Consumer 对话
- **微信集成**：iLink Bot 协议、双向图片/语音/视频、定时任务推送
- **定时任务**：cron / interval / once 调度，脚本或 Agent 任务类型
- **实时语音**：OpenAI Realtime WebSocket S2S 代理
- **联网工具**：CloudsWay / Tavily 双 provider 搜索 + 网页抓取
- **批量处理**：Excel 上传 → 批量 Agent 执行 → 结果下载（前端入口：**设置 → 通用**）
- **可观测性**：Langfuse / LangSmith 追踪集成

---

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  Nginx (:80)  — 反向代理（生产环境）                      │
├──────────────────────────────────────────────────────────┤
│  React SPA (:3000)                                      │
│  ├─ Vite 6 + React 19 + TypeScript + Ant Design 5       │
│  ├─ 开发: Vite dev server, proxy /api → FastAPI         │
│  └─ 生产: Express 托管 dist/, proxy /api → FastAPI      │
├──────────────────────────────────────────────────────────┤
│  FastAPI Backend (:8000)                                │
│  ├─ 用户认证 (注册码制, Bearer Token)                    │
│  ├─ 对话管理 (CRUD + SSE Streaming)                     │
│  ├─ 文件管理 (浏览/读写/上传/下载)                       │
│  ├─ 脚本执行 (沙箱 Python)                              │
│  ├─ Service 发布 (API Key + Consumer API)               │
│  ├─ 微信 iLink 渠道 (Bridge + Session)                  │
│  ├─ 定时任务 (Scheduler)                                │
│  ├─ 语音 (S2S Realtime WebSocket)                       │
│  └─ 收件箱 (Inbox)                                      │
├──────────────────────────────────────────────────────────┤
│  deepagents + LangGraph Engine                          │
│  ├─ per-user Agent 实例 + SQLite Checkpoint             │
│  ├─ FilesystemBackend (Local / S3)                      │
│  ├─ SubAgent 调度                                       │
│  └─ 工具链 (文件/脚本/联网/多媒体/调度)                  │
└──────────────────────────────────────────────────────────┘
```

---

## 环境要求

- Python 3.11+
- Node.js 20+
- Anthropic API Key 和/或 OpenAI API Key（至少配置一个）

---

## 方式一：本地开发运行

### 1. 克隆仓库

```bash
git clone https://github.com/LiUshin/semi-deep-agent.git
cd semi-deep-agent
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填入一个 LLM API Key：

```bash
# Anthropic（Claude 系列）
ANTHROPIC_API_KEY=sk-ant-your-actual-key

# OpenAI（GPT 系列 + 图片/语音/视频生成 + 实时语音对话）
OPENAI_API_KEY=sk-your-actual-key
```

两者可同时配置，前端会自动显示所有可用模型供切换。完整可配置项见 `.env.example`。

### 3. 生成注册码

注册采用**注册码制**，用户注册时必须提供一个有效的一次性注册码。

```bash
# 生成 10 个注册码（默认）
python generate_keys.py

# 或指定数量
python generate_keys.py 20

# 追加到已有注册码文件
python generate_keys.py 5 --append
```

注册码保存在 `config/registration_keys.json`（已被 `.gitignore` 排除）。

### 4. 安装 Python 依赖

```bash
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

### 5. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

### 6. 启动后端（FastAPI）

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

后端启动后可访问 API 文档：http://localhost:8000/docs

### 7. 启动前端（Vite 开发服务器）

新开一个终端：

```bash
cd frontend
npm run dev
```

Vite 开发服务器运行在 `:3000`，自动将 `/api` 请求代理到 FastAPI `:8000`，支持热更新。

### 8. 访问应用

打开浏览器访问 http://localhost:3000

使用步骤 3 中生成的注册码注册账号，即可开始使用。

---

## 方式二：Docker 运行

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY 等必要配置
```

### 2. 生成注册码

```bash
python generate_keys.py
```

生成的 `config/registration_keys.json` 会在构建 Docker 镜像时一并打包。

### 3. 构建并启动

```bash
docker compose up -d --build
```

Docker 镜像采用多阶段构建：
- **Stage 1**：Node.js 20 编译 React 前端（`npm ci` + `npm run build`）
- **Stage 2**：Python 3.11 + Node.js 20 运行时，Express 托管构建产物 + FastAPI 后端

架构：`Nginx (:80) → Express (:3000) → FastAPI (:8000)`

### 4. 访问应用

打开浏览器访问 http://localhost（Nginx 默认 80 端口）

如需本机调试，可取消 `docker-compose.yml` 中端口映射的注释。

### 5. 查看日志

```bash
docker compose logs -f
```

### 6. 停止服务

```bash
docker compose down
```

### 数据持久化

用户数据（对话、文件、checkpoints）挂载到宿主机 `./data/users/`，容器重建不会丢失。

---

## 可选：Langfuse 可观测性

项目内置 Langfuse 自托管部署配置：

```bash
cd langfuse
cp env.example .env
# 编辑 .env，修改密码和密钥
docker compose up -d
```

启动后在 `.env`（项目根目录）中配置 Langfuse 相关环境变量即可开启追踪。

---

## 项目结构

```
jellyfishbot/
├── app/                           # FastAPI 后端应用
│   ├── main.py                    # 应用入口（~70 条路由）
│   ├── deps.py                    # 依赖注入 (admin / consumer)
│   ├── core/
│   │   ├── settings.py            # 环境变量、路径常量
│   │   ├── security.py            # 用户认证 (注册/登录/token)
│   │   ├── api_config.py          # API Key / Base URL 按能力路由
│   │   ├── path_security.py       # 路径遍历防护
│   │   └── observability.py       # Langfuse 追踪集成
│   ├── schemas/
│   │   ├── requests.py            # Admin Pydantic 模型
│   │   └── service.py             # Service / Consumer 模型
│   ├── services/
│   │   ├── agent.py               # Agent 创建/缓存/模型解析
│   │   ├── consumer_agent.py      # Consumer Agent 工厂
│   │   ├── tools.py               # LangChain @tool 工厂
│   │   ├── ai_tools.py            # 图片/语音/视频生成底层
│   │   ├── web_tools.py           # 联网搜索/抓取
│   │   ├── script_runner.py       # 沙箱 Python 脚本执行
│   │   ├── _sandbox_wrapper.py    # 运行时文件 I/O 沙箱
│   │   ├── conversations.py       # 对话持久化
│   │   ├── prompt.py              # System Prompt 版本控制
│   │   ├── subagents.py           # Subagent 配置管理
│   │   ├── published.py           # Service 发布 + Consumer 会话
│   │   ├── scheduler.py           # 定时任务调度器
│   │   └── inbox.py               # 收件箱
│   ├── routes/                    # FastAPI 路由模块
│   │   ├── auth.py                # /api/auth/*
│   │   ├── conversations.py       # /api/conversations/*
│   │   ├── chat.py                # /api/chat (SSE streaming + resume/stop)
│   │   ├── files.py               # /api/files/*
│   │   ├── scripts.py             # /api/scripts/run + /api/audio/transcribe
│   │   ├── models.py              # /api/models
│   │   ├── settings_routes.py     # system-prompt + user-profile + subagents
│   │   ├── batch.py               # /api/batch/* (Excel 批量)
│   │   ├── services.py            # /api/services/* (Service CRUD + API Key)
│   │   ├── consumer.py            # /api/v1/* (Consumer 对外接口)
│   │   ├── consumer_ui.py         # Consumer 聊天页
│   │   ├── scheduler.py           # /api/scheduler/*
│   │   ├── inbox.py               # /api/inbox/*
│   │   └── wechat_ui.py           # 微信中间页路由
│   ├── channels/wechat/           # 微信 iLink 集成
│   │   ├── client.py              # iLink 协议客户端
│   │   ├── bridge.py              # Consumer 消息桥接
│   │   ├── admin_bridge.py        # Admin 微信接入
│   │   ├── session_manager.py     # 会话管理 (指数退避/清理)
│   │   ├── media.py               # AES 加解密 + CDN
│   │   ├── delivery.py            # 统一投递层
│   │   ├── rate_limiter.py        # 频率限制
│   │   └── router.py              # 微信 API 路由
│   ├── storage/                   # 文件存储抽象层
│   │   ├── local.py               # 本地文件系统
│   │   ├── s3.py                  # S3 兼容存储
│   │   └── ...
│   └── voice/
│       └── router.py              # WebSocket S2S 实时语音代理
│
├── frontend/                      # React 前端 (Vite + TypeScript)
│   ├── package.json               # jellyfishbot-frontend v2.0.0
│   ├── vite.config.ts             # Vite 6, :3000, proxy → :8000
│   ├── index.html                 # Vite 入口
│   ├── server.js                  # 生产环境 Express (托管 dist/)
│   ├── src/
│   │   ├── main.tsx               # React 19 入口
│   │   ├── App.tsx                # ConfigProvider (antd dark) + Auth + Router
│   │   ├── router/index.tsx       # BrowserRouter 路由定义
│   │   ├── layouts/AppLayout.tsx  # 侧栏导航 + 文件面板 + 设置入口
│   │   ├── services/api.ts        # 统一 API 客户端
│   │   ├── stores/
│   │   │   ├── authContext.tsx     # 认证状态管理
│   │   │   └── streamContext.tsx   # SSE 流状态管理
│   │   ├── types/index.ts         # TypeScript 类型定义
│   │   ├── styles/
│   │   │   ├── theme.ts           # 设计系统 (品牌色/语义色/圆角/阴影)
│   │   │   └── global.css         # 全局样式
│   │   ├── pages/
│   │   │   ├── Login.tsx           # 品牌分栏登录/注册
│   │   │   ├── Chat/              # 主聊天页面
│   │   │   │   ├── index.tsx      # 对话列表 + 消息区 + SSE 流式
│   │   │   │   ├── chat.module.css # CSS Modules 样式
│   │   │   │   ├── markdown.ts    # Markdown 渲染 (按需高亮)
│   │   │   │   ├── useSmartScroll.ts # 智能滚动 hook
│   │   │   │   └── components/
│   │   │   │       ├── StreamingMessage.tsx  # 流式消息容器
│   │   │   │       ├── MessageBubble.tsx     # 历史消息气泡
│   │   │   │       ├── ThinkingBlock.tsx     # 思考过程折叠
│   │   │   │       ├── ToolIndicator.tsx     # 工具调用指示器
│   │   │   │       ├── SubagentCard.tsx      # 子代理执行卡片
│   │   │   │       ├── ApprovalCard.tsx      # HITL 审批卡片
│   │   │   │       ├── ImageAttachment.tsx   # 图片附件
│   │   │   │       └── VoiceInput.tsx        # 语音输入
│   │   │   ├── AdminServices/index.tsx  # Service 管理 (CRUD + Key + 微信 + 测试)
│   │   │   ├── Scheduler/index.tsx      # 定时任务管理
│   │   │   ├── WeChat/index.tsx         # 微信接入 (QR/状态/消息)
│   │   │   └── Settings/               # 设置子页面
│   │   │       ├── PromptPage.tsx       # System Prompt 版本管理
│   │   │       ├── SubagentPage.tsx     # Subagent 管理
│   │   │       ├── GeneralPage.tsx      # 通用（时区/界面样式/高级开关；内嵌 BatchRunner 批量运行）
│   │   │       └── InboxPage.tsx        # 收件箱
│   │   └── components/
│   │       ├── FilePanel.tsx            # 文件浏览/编辑面板
│   │       └── modals/                  # 各类模态框
│   ├── public/                    # 静态资源
│   │   ├── media_resources/       # 品牌素材 (logo 等)
│   │   ├── service-chat.html      # Consumer 独立聊天页
│   │   └── wechat-scan.html       # 微信扫码中间页
│   └── dist/                      # Vite 构建产物 (gitignore)
│
├── config/                        # 应用配置
│   ├── agent_config.json          # Agent 默认配置
│   └── registration_keys.json     # 注册码 (gitignore)
├── docs/                          # 项目文档
├── langfuse/                      # Langfuse 自托管部署配置
├── nginx/default.conf             # Nginx 反代配置
├── wechat-bot/                    # iLink 协议参考实现
├── Dockerfile                     # 多阶段构建
├── docker-compose.yml             # Docker Compose 编排
├── requirements.txt               # Python 依赖
├── start.sh                       # 容器启动脚本
├── generate_keys.py               # 注册码生成工具
└── .env.example                   # 环境变量模板
```

---

## 前端技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 19 | UI 框架 |
| TypeScript | 5.7 | 类型安全 |
| Vite | 6 | 构建工具 + 开发服务器 |
| Ant Design | 5 | UI 组件库 (暗色主题) |
| React Router | 7 | 客户端路由 (BrowserRouter) |
| Phosphor Icons | 2.x | 图标库 (全站统一) |
| marked + DOMPurify | - | Markdown 渲染 (XSS 安全) |
| highlight.js | 11 | 代码语法高亮 (按需加载) |

### 前端命令

```bash
cd frontend
npm run dev        # Vite 开发服务器 (:3000), 热更新, proxy → :8000
npm run build      # TypeScript 编译 + Vite 生产构建 → dist/
npm run preview    # 本地预览生产构建
npm run legacy     # 旧版 Express 服务器 (兼容)
```

---

## 支持的模型与能力

### Anthropic（需配置 `ANTHROPIC_API_KEY`）

| 模型 | 说明 |
|------|------|
| Claude Opus 4.6 (Thinking) | 最强模型，自适应 thinking，适合复杂推理与编程 |
| Claude Sonnet 4.6 (Thinking) | 速度与智能的最佳平衡，支持自适应 thinking |
| Claude Haiku 4.5 (Thinking) | 最快模型，支持 extended thinking |
| Claude Opus 4.6 | 最强标准模型，1M token 上下文 |
| Claude Sonnet 4.6 | 高性能标准模型，1M token 上下文 |
| Claude Haiku 4.5 | 最快响应，200k token 上下文 |

### OpenAI（需配置 `OPENAI_API_KEY`）

| 模型 / 服务 | 说明 |
|-------------|------|
| GPT-5.4 | Reasoning 模型 |
| GPT-5.3 / GPT-5.2 | 高性能对话 |
| GPT-4o / GPT-4o Mini | 标准 / 轻量对话 |
| o3-mini | 推理模型 |
| gpt-image-1 | AI 图片生成 (`generate_image` 工具) |
| tts-1 / tts-1-hd | 文字转语音 (6 种音色) |
| Sora 2 | AI 视频生成 (`generate_video` 工具) |
| Whisper | 语音转文字 |
| gpt-4o-realtime-preview | 实时语音对话 (WebSocket S2S) |

---

## 环境变量说明

| 变量 | 必需 | 说明 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 二选一 | Anthropic API 密钥 |
| `OPENAI_API_KEY` | 二选一 | OpenAI API 密钥 |
| `ANTHROPIC_BASE_URL` | 否 | Anthropic 自定义端点 |
| `OPENAI_BASE_URL` | 否 | OpenAI 自定义端点 |
| `IMAGE_API_KEY` / `IMAGE_BASE_URL` | 否 | 图片生成能力覆盖 |
| `TTS_API_KEY` / `TTS_BASE_URL` | 否 | TTS 能力覆盖 |
| `VIDEO_API_KEY` / `VIDEO_BASE_URL` | 否 | 视频生成能力覆盖 |
| `S2S_API_KEY` / `S2S_BASE_URL` | 否 | 实时语音能力覆盖 |
| `STT_API_KEY` / `STT_BASE_URL` | 否 | 语音转写能力覆盖 |
| `CLOUDSWAY_SEARCH_KEY` | 否 | CloudsWay 搜索 API (优先) |
| `TAVILY_API_KEY` | 否 | Tavily 搜索 API (备选) |
| `STORAGE_BACKEND` | 否 | `local` (默认) 或 `s3` |
| `S3_BUCKET` / `S3_REGION` / ... | 否 | S3 存储配置 |
| `LANGFUSE_*` | 否 | Langfuse 追踪配置 |
| `LANGCHAIN_*` | 否 | LangSmith 追踪配置 |

完整变量列表见 [.env.example](.env.example)。

---

## 两层架构 (Admin / Consumer)

### Admin 层
- 注册 / 登录，完整管理界面
- 配置 Agent：选择模型、上传文档/脚本、自定义 System Prompt、管理子代理
- 发布 Service：配置能力集、生成 API Key、绑定微信渠道
- 定时任务、批量执行、收件箱

### Consumer 层
- 通过 Service API Key (`sk-svc-...`) 认证
- 独立的对话和文件隔离
- API 接口：
  - `POST /api/v1/chat` — 自定义 SSE 流式
  - `POST /api/v1/chat/completions` — OpenAI 兼容接口
  - `POST /api/v1/conversations` — 创建对话
  - `GET /api/v1/conversations/{id}` — 获取对话历史
- 独立聊天页：`/s/{service_id}`

---

## 用户数据存储

```
users/
├── {user_id}/
│   ├── conversations/             # 对话历史 JSON
│   ├── filesystem/                # 用户虚拟文件系统
│   │   ├── docs/                  # 文档目录
│   │   ├── scripts/               # Python 脚本目录
│   │   └── generated/             # AI 生成文件
│   ├── services/                  # 发布的 Service
│   │   └── {service_id}/
│   │       ├── config.json        # Service 配置
│   │       ├── keys.json          # API Key (sha256 hash)
│   │       ├── conversations/     # Consumer 对话
│   │       └── tasks/             # Service 定时任务
│   ├── tasks/                     # Admin 定时任务
│   ├── system_prompt.txt          # 自定义 System Prompt
│   ├── system_prompt_versions.json
│   ├── user_profile.json          # 用户画像
│   └── subagents.json             # 子代理配置
└── users.json                     # 用户账号信息

data/
└── checkpoints.db                 # SQLite Agent 状态持久化
```

---

## License

MIT
