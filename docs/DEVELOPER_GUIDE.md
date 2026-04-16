# JellyfishBot 开发指南

> 面向开发者的完整技术文档，涵盖架构设计、开发规范、API 参考、安全机制和扩展方式。

---

## 目录

1. [架构概览](#1-架构概览)
2. [技术栈](#2-技术栈)
3. [开发环境搭建](#3-开发环境搭建)
4. [后端架构](#4-后端架构)
   - [目录结构](#41-目录结构)
   - [FastAPI 路由](#42-fastapi-路由)
   - [依赖注入](#43-依赖注入)
   - [Agent 引擎](#44-agent-引擎)
   - [工具系统](#45-工具系统)
   - [存储层](#46-存储层)
   - [安全架构](#47-安全架构)
5. [前端架构](#5-前端架构)
   - [目录结构](#51-目录结构)
   - [路由系统](#52-路由系统)
   - [状态管理](#53-状态管理)
   - [API 客户端](#54-api-客户端)
   - [SSE 流式处理](#55-sse-流式处理)
   - [设计系统](#56-设计系统)
   - [组件规范](#57-组件规范)
6. [API 参考](#6-api-参考)
7. [两层架构 (Admin / Consumer)](#7-两层架构)
8. [微信集成](#8-微信集成)
9. [定时任务](#9-定时任务)
10. [Docker 部署](#10-docker-部署)
11. [测试与调试](#11-测试与调试)
12. [扩展开发](#12-扩展开发)

---

## 1. 架构概览

```
┌────────────────────────────────────────────────────────────┐
│                    客户端 (Browser)                          │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │  Admin SPA      │  │ Consumer Page│  │ WeChat Scan   │ │
│  │  React 19       │  │ service-chat │  │ wechat-scan   │ │
│  │  Vite/TS/AntD   │  │ .html        │  │ .html         │ │
│  └────────┬────────┘  └──────┬───────┘  └───────┬───────┘ │
└───────────┼──────────────────┼──────────────────┼──────────┘
            │ /api/*           │ /api/v1/*        │ /wc/*
            ▼                  ▼                  ▼
┌────────────────────────────────────────────────────────────┐
│  Nginx (:80) — SSL 终止 + 反向代理                          │
│  → Express (:3000) — 静态资源 + API 代理                    │
│  → FastAPI (:8000) — 核心 API                               │
├────────────────────────────────────────────────────────────┤
│  FastAPI Application (app/)                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │  routes/  │ │ services/│ │ channels/│ │   storage/   │  │
│  │ 路由层    │ │ 业务层    │ │ 渠道层    │ │  存储抽象    │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       └─────┬──────┴───────┬────┘               │          │
│             ▼              ▼                     ▼          │
│  ┌─────────────────────────────┐  ┌──────────────────────┐ │
│  │  deepagents + LangGraph     │  │  Local FS / S3       │ │
│  │  Agent 运行引擎              │  │  文件存储后端         │ │
│  └─────────────────────────────┘  └──────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### 分层设计

| 层 | 职责 | 目录 |
|---|---|---|
| **路由层** | HTTP 请求处理、参数校验、SSE 流 | `app/routes/` |
| **业务层** | Agent 管理、对话、工具、调度 | `app/services/` |
| **渠道层** | 微信 iLink 协议适配 | `app/channels/wechat/` |
| **存储层** | Local / S3 文件存储抽象 | `app/storage/` |
| **核心层** | 认证、配置、安全 | `app/core/` |

---

## 2. 技术栈

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行时 |
| FastAPI | 0.104+ | Web 框架 |
| Uvicorn | 0.24+ | ASGI 服务器 |
| deepagents | 0.4.1+ | AI Agent 框架 |
| LangGraph | latest | Agent 状态图 |
| LangChain | latest | LLM 抽象层 |
| langchain-anthropic | latest | Claude 适配 |
| langchain-openai | latest | OpenAI 适配 |
| langgraph-checkpoint-sqlite | latest | 状态持久化 |
| Pydantic | 2.0+ | 数据校验 |
| boto3 / aioboto3 | latest | S3 存储 |
| httpx | 0.27+ | 异步 HTTP 客户端 |
| bcrypt | 4.0+ | 密码哈希 |
| croniter | latest | Cron 表达式 |
| pycryptodome | 3.20+ | AES 加解密 |
| silk-python | 0.2+ | SILK 音频 |

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 19 | UI 框架 |
| TypeScript | 5.7 | 类型安全 |
| Vite | 6 | 构建工具 |
| Ant Design | 5 | UI 组件库 |
| React Router | 7 | 路由 |
| Phosphor Icons | 2.x | 图标 |
| marked | 15+ | Markdown 渲染 |
| DOMPurify | 3.2+ | XSS 防护 |
| highlight.js | 11 | 代码高亮 |

---

## 3. 开发环境搭建

### 后端

```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate     # Linux/macOS

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env

# 生成注册码
python generate_keys.py

# 启动（开发模式，热重载）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API 文档自动生成：http://localhost:8000/docs（Swagger UI）

### 前端

```bash
cd frontend
npm install

# 开发模式（Vite，支持热更新）
npm run dev          # → http://localhost:3000

# 生产构建
npm run build        # → dist/

# 预览生产构建
npm run preview

# 旧版 Express（兼容模式）
npm run legacy       # → http://localhost:3000（从 dist/ 提供静态资源）
```

**Vite 开发代理规则**（`vite.config.ts`）：

| 路径 | 目标 | 说明 |
|------|------|------|
| `/api/*` | `localhost:8000` | FastAPI 所有 API |
| `/s/*` | `localhost:8000` | Consumer 聊天页 |
| `/wc/*` | `localhost:8000` | 微信中间页 |

SSE 请求自动设置 `Accept: text/event-stream` 并禁用缓冲。

---

## 4. 后端架构

### 4.1 目录结构

```
app/
├── main.py                    # FastAPI 应用入口，路由注册，生命周期
├── deps.py                    # 依赖注入函数
├── core/
│   ├── settings.py            # 环境变量 + 路径常量
│   ├── security.py            # 用户认证（注册/登录/JWT）
│   ├── api_config.py          # API Key/URL 按能力路由
│   ├── path_security.py       # 路径遍历防护
│   └── observability.py       # Langfuse 集成
├── schemas/
│   ├── requests.py            # Admin 请求模型
│   └── service.py             # Service/Consumer 模型
├── services/
│   ├── agent.py               # Agent 创建/缓存/模型解析
│   ├── consumer_agent.py      # Consumer Agent 工厂
│   ├── tools.py               # @tool 工厂函数
│   ├── ai_tools.py            # 多媒体生成底层
│   ├── web_tools.py           # 联网搜索/抓取
│   ├── script_runner.py       # 沙箱脚本执行
│   ├── _sandbox_wrapper.py    # 运行时 I/O 沙箱
│   ├── conversations.py       # 对话持久化
│   ├── prompt.py              # System Prompt 版本管理
│   ├── subagents.py           # Subagent 配置
│   ├── published.py           # Service 发布 + Consumer 会话
│   ├── scheduler.py           # 定时任务调度器
│   └── inbox.py               # 收件箱
├── routes/
│   ├── auth.py                # /api/auth/*
│   ├── conversations.py       # /api/conversations/*
│   ├── chat.py                # /api/chat (SSE)
│   ├── files.py               # /api/files/*
│   ├── scripts.py             # /api/scripts/*
│   ├── models.py              # /api/models
│   ├── settings_routes.py     # /api/system-prompt, user-profile, subagents
│   ├── batch.py               # /api/batch/*
│   ├── services.py            # /api/services/*
│   ├── consumer.py            # /api/v1/* (外部消费者)
│   ├── consumer_ui.py         # Consumer 页面路由
│   ├── scheduler.py           # /api/scheduler/*
│   ├── inbox.py               # /api/inbox/*
│   └── wechat_ui.py           # 微信中间页路由
├── channels/wechat/
│   ├── client.py              # iLink 协议客户端
│   ├── bridge.py              # Consumer 消息桥接
│   ├── admin_bridge.py        # Admin 微信接入
│   ├── session_manager.py     # 会话管理
│   ├── media.py               # AES 加解密 + CDN
│   ├── delivery.py            # 统一投递
│   ├── rate_limiter.py        # 频率限制
│   └── router.py              # 微信 API 路由
├── storage/
│   ├── config.py              # S3Config, is_s3_mode
│   ├── base.py                # StorageService ABC
│   ├── local.py               # LocalStorageService
│   ├── s3.py                  # S3StorageService
│   ├── s3_backend.py          # S3 BackendProtocol
│   └── __init__.py            # 工厂函数
└── voice/
    └── router.py              # WebSocket S2S 代理
```

### 4.2 FastAPI 路由

路由模块使用 `APIRouter`，在 `app/main.py` 中统一注册。当前约 **70 条路由**。

路由前缀约定：
- `/api/auth/*` — 认证
- `/api/conversations/*` — 对话管理
- `/api/chat` — SSE 聊天
- `/api/files/*` — 文件操作
- `/api/scripts/*` — 脚本执行
- `/api/models` — 模型列表
- `/api/system-prompt/*` — Prompt 管理
- `/api/user-profile/*` — 用户画像
- `/api/subagents/*` — 子代理配置
- `/api/batch/*` — 批量执行
- `/api/services/*` — Service CRUD + API Key
- `/api/scheduler/*` — 定时任务
- `/api/inbox/*` — 收件箱
- `/api/v1/*` — Consumer 对外接口
- `/api/voice/*` — 实时语音
- `/api/wc/*` — 微信管理端点
- `/api/admin/wechat/*` — Admin 微信接入

### 4.3 依赖注入

**`app/deps.py`** 提供两个核心依赖：

```python
# Admin 认证 — 通过 Bearer Token
async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    ...

# Consumer 认证 — 通过 Service API Key (sk-svc-...)
async def get_service_context(authorization: str = Header(...)) -> ServiceContext:
    ...
```

- Admin 路由使用 `Depends(get_current_user)`
- Consumer 路由使用 `Depends(get_service_context)`

### 4.4 Agent 引擎

#### Agent 创建 (`app/services/agent.py`)

- 使用 `deepagents` 框架创建 Agent
- `AsyncSqliteSaver` 提供状态持久化（`data/checkpoints.db`）
- Agent 实例 per-user 缓存，避免重复创建
- 模型解析支持 `provider:model-id` 格式
- Thinking 模型在 `THINKING_MODEL_CONFIG` 中配置

#### 模型支持

```python
# 模型 ID 格式
"anthropic:claude-sonnet-4-6-20250929"
"openai:gpt-5.4"

# Thinking 模型自动配置 extended_thinking 参数
THINKING_MODEL_CONFIG = {
    "anthropic:claude-opus-4-6-20250929-thinking": {...},
    ...
}
```

#### Consumer Agent (`app/services/consumer_agent.py`)

- 独立的 Agent 工厂，根据 Service 配置创建
- 工具集受 Service 能力配置限制
- 文件系统隔离在 `users/{admin}/services/{svc}/conversations/{conv}/`

### 4.5 工具系统

#### 内置工具 (`app/services/tools.py`)

| 工具 | 说明 | 注入条件 |
|------|------|----------|
| `read_file` | 读取文件 | 始终注入 |
| `write_file` | 写入文件（可触发 HITL） | 始终注入 |
| `list_directory` | 列出目录 | 始终注入 |
| `run_python_script` | 执行 Python 脚本 | 始终注入 |
| `generate_image` | AI 图片生成 | `image` capability |
| `generate_speech` | TTS 语音 | `speech` capability |
| `generate_video` | AI 视频 | `video` capability |
| `web_search` | 联网搜索 | Admin 始终注入；Consumer 需 `web` capability |
| `web_fetch` | 网页抓取 | 同上 |
| `schedule_task` | 定时任务 | Admin 始终注入；Consumer 需 `scheduler` capability |
| `send_message` | 发送消息到人类 | `humanchat` capability |

#### AI 工具 (`app/services/ai_tools.py`)

底层实现调用 OpenAI API：
- `generate_image`：gpt-image-1
- `generate_speech`：tts-1 / tts-1-hd（6 种音色）
- `generate_video`：Sora 2

#### 联网工具 (`app/services/web_tools.py`)

双 provider 架构：
1. **CloudsWay**（优先）：`CLOUDSWAY_SEARCH_KEY`
2. **Tavily**（备选）：`TAVILY_API_KEY`

### 4.6 存储层

#### 抽象接口 (`app/storage/base.py`)

```python
class StorageService(ABC):
    async def read_file(self, path: str) -> bytes: ...
    async def write_file(self, path: str, content: bytes) -> None: ...
    async def list_directory(self, path: str) -> list: ...
    async def delete(self, path: str) -> None: ...
    async def exists(self, path: str) -> bool: ...
    async def move(self, src: str, dst: str) -> None: ...
```

#### 后端选择

通过 `STORAGE_BACKEND` 环境变量切换：
- `local`（默认）：`LocalStorageService` — 使用 `os.*` 操作本地磁盘
- `s3`：`S3StorageService` — boto3 S3 API（兼容 AWS/MinIO/R2/OSS）

#### S3 键映射

```
{prefix}/{user_id}/fs/{path}                              # 用户文件
{prefix}/{admin_id}/svc/{svc_id}/{conv_id}/gen/{path}     # Consumer 生成文件
```

#### 工厂函数 (`app/storage/__init__.py`)

```python
get_storage_service()           # → StorageService 实例
create_agent_backend()          # → deepagents BackendProtocol（Admin）
create_consumer_backend()       # → deepagents BackendProtocol（Consumer）
```

### 4.7 安全架构

#### 路径遍历防护 (`app/core/path_security.py`)

```python
safe_join(base, user_path)    # 安全路径拼接
ensure_within(path, root)      # 验证路径在根目录内
```

使用 `pathlib.Path.resolve()` + 分隔符感知的边界检查。

#### 脚本沙箱（双层）

**第一层：AST 静态分析** (`script_runner._check_script_safety`)
- 禁止危险模块：subprocess, pathlib, ctypes, io, pickle, threading
- 禁止危险内置：exec, eval, getattr, setattr, globals
- 禁止危险 os 函数：chdir, listdir, walk, scandir
- 禁止访问 `__builtins__`/`__subclasses__`/`__globals__`

**第二层：运行时沙箱** (`_sandbox_wrapper.py`)
- 猴子补丁 `builtins.open`, `io.open`, `os.listdir`, `os.scandir`, `os.walk`, `os.chdir`
- 运行时强制目录级读/写权限

**权限配置**：
- Admin：read = `scripts/ + docs/`，write = `scripts/ + generated/`
- Consumer：read = `scripts/ + docs/`（按 allowed_scripts 过滤），write = conversation `generated/`

#### XSS 防护

- 后端：`consumer_ui.py` 使用 `html.escape()` 处理模板注入
- 前端：`marked.parse()` 输出经 DOMPurify 清洗

#### 认证

- Admin：JSON 存储（`users/users.json`），bcrypt 哈希（SHA-256 降级）
- Consumer：per-service API Key（`sk-svc-...`），SHA-256 哈希，存储在 `users/{admin}/services/{svc}/keys.json`

---

## 5. 前端架构

### 5.1 目录结构

```
frontend/src/
├── main.tsx               # React 19 入口
├── App.tsx                # ConfigProvider + AuthProvider + StreamProvider + Router
├── router/index.tsx       # BrowserRouter 路由定义
├── layouts/AppLayout.tsx  # 主布局（侧栏 + 内容区 + 文件面板）
├── services/api.ts        # 统一 API 客户端
├── stores/
│   ├── authContext.tsx     # 认证状态 (React Context)
│   └── streamContext.tsx   # SSE 流状态 (React Context)
├── types/index.ts          # TypeScript 类型定义
├── styles/
│   ├── theme.ts            # 设计系统 Tokens
│   └── global.css          # 全局 CSS
├── pages/
│   ├── Login.tsx           # 登录/注册
│   ├── Chat/               # 主聊天
│   ├── AdminServices/      # Service 管理
│   ├── Scheduler/          # 定时任务
│   ├── WeChat/             # 微信接入
│   └── Settings/           # 设置子页面（含 GeneralPage：内嵌 BatchRunner）
└── components/
    ├── FilePanel.tsx        # 文件面板
    └── modals/              # 模态框组件
```

### 5.2 路由系统

使用 React Router 7 的 `BrowserRouter`：

```tsx
<BrowserRouter>
  <Routes>
    <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
    <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
      <Route path="/" element={<ChatPage />} />
      <Route path="/settings" element={<SettingsLayout />}>
        <Route index element={<Navigate to="/settings/prompt" replace />} />
        <Route path="prompt" element={<PromptPage />} />
        <Route path="subagents" element={<SubagentPage />} />
        <Route path="packages" element={<PackagesPage />} />
        <Route path="batch" element={<Navigate to="/settings/general" replace />} />
        <Route path="services" element={<AdminServicesPage />} />
        <Route path="scheduler" element={<SchedulerPage />} />
        <Route path="wechat" element={<WeChatPage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="general" element={<GeneralPage />} />
      </Route>
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
</BrowserRouter>
```

- `ProtectedRoute`：检查认证状态，未登录重定向到 `/login`
- `PublicRoute`：已登录重定向到 `/`
- `AppLayout`：提供侧栏导航和 `<Outlet/>`
- `GeneralPage`（`/settings/general`）：时区、界面样式、高级 Tab 开关；**内嵌** `BatchRunner`（`inline`）提供 Excel 批量运行
- `/settings/batch`：**重定向**到 `/settings/general`（兼容旧链接）

### 5.3 状态管理

使用 React Context + 组件局部状态，不使用全局状态库。

**`authContext.tsx`**：
- `user`：当前用户信息
- `loading`：认证状态加载中
- `login(username, password)`：登录
- `register(username, password, regKey)`：注册
- `logout()`：退出
- Token 存储在 `localStorage`

**`streamContext.tsx`**：
- 管理 SSE 流式状态
- 跨组件共享流式回调

### 5.4 API 客户端

`src/services/api.ts` 提供统一的类型安全 API 客户端：

```typescript
// 通用请求函数
async function request<T>(method: string, path: string, body?: unknown): Promise<T>

// 自动处理:
// - Bearer Token 注入
// - Content-Type (JSON / FormData)
// - 401 自动清除 Token 并刷新页面
// - 错误提取 detail 字段
```

**模块划分**：
- Auth：`login`, `register`, `getMe`
- Conversations：`listConversations`, `createConversation`, `getConversation`, `deleteConversation`
- Chat：`streamChat`, `resumeChat`, `stopChat`, `abortStream`
- Files：`listFiles`, `readFile`, `writeFile`, `editFile`, `deleteFile`, `moveFile`, `uploadFiles`, `downloadFile`, `mediaUrl`
- System Prompt：`getSystemPrompt`, `updateSystemPrompt`, `resetSystemPrompt`, 版本管理
- User Profile：`getUserProfile`, `updateUserProfile`, 版本管理
- Scripts：`runScript`
- Models：`getModels`
- Audio：`transcribeAudio`
- Subagents：`listSubagents`, `addSubagent`, `getSubagent`, `updateSubagent`, `deleteSubagent`
- Batch：`uploadBatchExcel`, `startBatchRun`, `listBatchTasks`, `getBatchTask`, `cancelBatchTask`, `batchDownloadUrl`
- Scheduler：`listSchedulerTasks`, `createSchedulerTask`, `updateSchedulerTask`, `deleteSchedulerTask`, `runSchedulerTaskNow`
- Services：`listServices`, `createService`, `updateService`, `deleteService`, `listServiceKeys`, `createServiceKey`, `deleteServiceKey`
- Inbox：`listInbox`, `getInboxUnreadCount`, `getInboxMessage`, `updateInboxStatus`, `deleteInboxMessage`

### 5.5 SSE 流式处理

#### 事件类型

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

#### 性能优化

```
SSE 回调 → useRef 直接修改 blocks 数组（避免每 token setState）
         → requestAnimationFrame 节流刷新（~60fps）
         → 批量更新到 React state
         → StreamingMessage (React.memo) 避免不必要重渲染
```

### 5.6 设计系统

#### 品牌色

| Token | 值 | 用途 |
|-------|------|------|
| Primary | `#E89FD9` | 主色（按钮、选中态、Logo 光晕） |
| Secondary | `#8B7FD9` | 辅色（渐变、次级操作） |
| Accent | `#5FC9E6` | 强调色（链接、成功） |
| Highlight | `#FF8FCC` | 高亮 |

#### 语义色

| Token | 值 | 用途 |
|-------|------|------|
| Success | `#5FC9E6` | 成功状态 |
| Warning | `#FFB86C` | 警告 |
| Error | `#FF6B9D` | 错误 |
| Info | `#8B7FD9` | 信息 |

#### 背景层级

| 层级 | 值 | 用途 |
|------|------|------|
| Base | `#0f0f13` | 最底层背景 |
| Container | `#16161d` | 容器/侧栏 |
| Elevated | `#1c1c27` | 卡片/弹窗 |

#### 文字层级

| 层级 | 值 |
|------|------|
| Primary | `#e4e4ed` |
| Secondary | `#9494a8` |
| Tertiary | `#66668a` |

#### 其他 Token

- 圆角：sm=4px / md=8px / lg=12px / bubble=16px
- 字体：正文 Segoe UI / 代码 JetBrains Mono
- Logo：`/media_resources/jellyfishlogo.png`

#### CSS 变量

Chat 组件使用 `--jf-*` 前缀的 CSS 变量（定义在 `chat.module.css` 的 `.chatContainer` 上），确保样式 scoped。

### 5.7 组件规范

#### 通用规范

- **函数式组件 + Hooks**，不使用 class 组件
- **图标统一使用 `@phosphor-icons/react`**，不再新增 `@ant-design/icons` 引用
- **样式优先级**：antd 组件 > inline style > CSS Modules
- **类型定义**集中在 `src/types/index.ts`
- **API 调用**统一通过 `src/services/api.ts`

#### Chat 组件层次

```
ChatPage (index.tsx)
├── 对话列表 (createPortal → sider-slot)
├── 消息区域
│   ├── MessageBubble (历史消息)
│   │   └── tool_calls 回放
│   ├── StreamingMessage (流式消息)
│   │   ├── ThinkingBlock (思考)
│   │   ├── ToolIndicator (工具调用)
│   │   └── SubagentCard (子代理)
│   └── ApprovalCard (HITL 审批)
├── 输入区域
│   ├── ImageAttachment (图片附件)
│   ├── VoiceInput (语音输入)
│   ├── 能力开关 / Plan Mode / 模型选择器
│   └── 发送/停止按钮
└── useSmartScroll (智能滚动)
```

#### 侧栏 Portal 机制

`ChatPage` 和 `SettingsLayout` 使用 `createPortal` 将侧栏内容注入到 `AppLayout` 的 `#sider-slot` DOM 节点，实现页面级侧栏内容切换。

---

## 6. API 参考

### Admin API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/register` | 注册（需注册码） |
| `POST` | `/api/auth/login` | 登录 |
| `GET` | `/api/auth/me` | 获取当前用户 |
| `GET` | `/api/conversations` | 对话列表 |
| `POST` | `/api/conversations` | 创建对话 |
| `GET` | `/api/conversations/{id}` | 对话详情 |
| `DELETE` | `/api/conversations/{id}` | 删除对话 |
| `POST` | `/api/chat` | SSE 聊天 |
| `POST` | `/api/chat/resume` | 恢复聊天（审批后） |
| `POST` | `/api/chat/stop` | 停止流式输出 |
| `GET` | `/api/files?path=` | 列出文件 |
| `GET` | `/api/files/read?path=` | 读取文件 |
| `POST` | `/api/files/write` | 写入文件 |
| `PUT` | `/api/files/edit` | 编辑文件 |
| `DELETE` | `/api/files?path=` | 删除文件 |
| `POST` | `/api/files/move` | 移动文件 |
| `POST` | `/api/files/upload` | 上传文件 |
| `GET` | `/api/files/download?path=` | 下载文件 |
| `GET` | `/api/files/media?path=` | 媒体文件 |
| `POST` | `/api/scripts/run` | 执行脚本 |
| `POST` | `/api/audio/transcribe` | 语音转写 |
| `GET` | `/api/models` | 模型列表 |
| `GET/PUT/DELETE` | `/api/system-prompt` | Prompt 管理 |
| `GET/POST` | `/api/system-prompt/versions` | Prompt 版本 |
| `GET/PUT` | `/api/user-profile` | 用户画像 |
| `GET/POST/PUT/DELETE` | `/api/subagents` | Subagent 管理 |
| `POST` | `/api/batch/upload` | 上传批量 Excel |
| `POST` | `/api/batch/run` | 开始批量执行 |
| `GET` | `/api/batch/tasks` | 批量任务列表 |
| `GET/POST` | `/api/services` | Service 列表/创建 |
| `GET/PUT/DELETE` | `/api/services/{id}` | Service CRUD |
| `GET/POST/DELETE` | `/api/services/{id}/keys` | API Key 管理 |
| `GET/POST` | `/api/scheduler` | 定时任务列表/创建 |
| `GET/PUT/DELETE` | `/api/scheduler/{id}` | 定时任务 CRUD |
| `POST` | `/api/scheduler/{id}/run-now` | 立即执行 |
| `GET` | `/api/scheduler/{id}/runs` | 运行记录 |
| `GET/PUT/DELETE` | `/api/inbox` | 收件箱管理 |

### Consumer API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/conversations` | 创建对话 |
| `GET` | `/api/v1/conversations/{id}` | 对话历史 |
| `GET` | `/api/v1/conversations/{id}/files` | 生成文件列表 |
| `POST` | `/api/v1/chat` | 自定义 SSE 聊天 |
| `POST` | `/api/v1/chat/completions` | OpenAI 兼容接口 |

### 页面路由

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/s/{service_id}` | Consumer 独立聊天页 |
| `GET` | `/wc/{service_id}` | 微信扫码中间页 |

---

## 7. 两层架构

### Admin 层

管理员通过 Web UI 登录，拥有完整权限：
- 管理文件系统（docs/scripts/generated）
- 配置 System Prompt + 用户画像
- 管理 Subagent
- 发布 Service + 管理 API Key
- 配置定时任务
- 微信接入

### Consumer 层

外部消费者通过 API Key 认证，权限受限：
- 只能访问 Service 配置的能力
- 文件系统隔离在 `users/{admin}/services/{svc}/conversations/{conv}/`
- 生成的文件隔离在对话级 `generated/` 目录
- 不能访问 Admin 的文件系统

### 数据隔离

```
users/{admin_id}/
├── filesystem/                    # Admin 文件系统
├── conversations/                 # Admin 对话
└── services/{service_id}/
    ├── config.json                # Service 配置
    ├── keys.json                  # API Key (hashed)
    └── conversations/{conv_id}/   # Consumer 对话
        └── generated/             # Consumer 生成文件
```

---

## 8. 微信集成

### 架构

```
微信用户 → iLink API → JellyfishBot Bridge → Consumer Agent
                                ↓
                         Session Manager
                              ↓
                    WeChat Delivery Layer
```

### 关键模块

| 模块 | 职责 |
|------|------|
| `client.py` | iLink 协议：认证、轮询、发送、上传 |
| `bridge.py` | 消息路由：微信消息 → Agent → 微信回复 |
| `admin_bridge.py` | Admin 微信接入（完整权限） |
| `session_manager.py` | 会话生命周期管理 |
| `media.py` | AES-128-ECB 加解密、CDN 上传下载 |
| `delivery.py` | 统一投递（文本 + 媒体） |
| `rate_limiter.py` | 频率限制（10 条/60s、QR 5 次/60s） |
| `router.py` | HTTP API 端点 |

### 多媒体支持

- **接收图片**：CDN GET → AES 解密 → base64 → Agent Vision
- **发送图片**：getuploadurl → AES 加密 → CDN POST → sendmessage
- **接收语音**：CDN GET → AES 解密 → SILK→WAV → Whisper 转文字
- **发送语音/文件**：通过 send_file 作为文件附件发送

---

## 9. 定时任务

### 调度器 (`app/services/scheduler.py`)

- `TaskScheduler` 单例，asyncio 循环每 30s 检查
- 在 `main.py` startup 启动，shutdown 停止

### 任务类型

| 类型 | 说明 | 支持范围 |
|------|------|----------|
| `script` | 执行 Python 脚本 | 仅 Admin |
| `agent` | 执行 Agent 任务 | Admin + Service |

### 调度类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `once` | 一次性 | `2025-12-31T09:00:00` |
| `cron` | Cron 表达式 | `0 9 * * *` |
| `interval` | 间隔（秒） | `3600` |

### 运行记录步骤

| 步骤类型 | 说明 |
|----------|------|
| `start` | 开始执行 |
| `docs_loaded` | 文档加载完成 |
| `loop` | 循环迭代 |
| `tool_call` | 工具调用 |
| `tool_result` | 工具结果 |
| `ai_message` | AI 消息 |
| `auto_approve` | 自动审批 |
| `finish` | 完成 |
| `error` | 错误 |
| `reply` | 推送结果 |

---

## 10. Docker 部署

### 多阶段构建

```dockerfile
# Stage 1: Node.js 编译 React
FROM node:20-alpine AS frontend-builder
RUN npm ci && npm run build

# Stage 2: Python 3.11 + Node.js 20 运行时
FROM python:3.11-slim
RUN pip install -r requirements.txt
# 安装 Express 运行时依赖
RUN npm install express http-proxy-middleware
# 复制构建产物
COPY --from=frontend-builder /build/dist /app/frontend/dist
```

### Docker Compose

```
Nginx(:80) → Express(:3000) → FastAPI(:8000)
```

- Nginx：SSL 终止 + 反向代理
- Express：静态资源托管 + API 代理
- FastAPI：核心后端

### 容器启动 (`start.sh`)

1. 启动 FastAPI（:8000）
2. 等待就绪后启动 Express（:3000）
3. `wait -n` 监控两个进程

---

## 11. 测试与调试

### 后端调试

- **API 文档**：http://localhost:8000/docs（Swagger UI）
- **日志级别**：通过 `LOG_LEVEL` 环境变量控制
- **Langfuse 追踪**：启用后可在 Langfuse UI 查看 Agent 执行链路

### 前端调试

- **Vite HMR**：`npm run dev` 支持热模块替换
- **React DevTools**：浏览器安装 React Developer Tools
- **Network**：检查 SSE 连接和 API 请求

### 常见调试场景

1. **SSE 不通**：检查 Vite proxy 配置、Accept header
2. **Agent 卡死**：检查 `POST /api/chat/stop` 是否生效，查看后端日志
3. **文件操作报错**：检查路径安全（path_security 相关日志）
4. **微信消息不送达**：检查 `wechat.*` 日志、iLink 连接状态

---

## 12. 扩展开发

### 添加新工具

1. 在 `app/services/tools.py` 中用 `@tool` 装饰器定义工具函数
2. 在 Agent 创建逻辑中注入工具
3. 如需 Consumer 支持，在 `consumer_agent.py` 中按 capability 条件注入

### 添加新路由

1. 在 `app/routes/` 下创建新模块，使用 `APIRouter`
2. 在 `app/main.py` 中注册 router

### 添加新页面

1. 在 `frontend/src/pages/` 下创建页面组件
2. 在 `frontend/src/router/index.tsx` 中添加路由
3. 如需侧栏导航项，在 `SettingsLayout` 的 `settingsNav` 数组中添加

### 添加新 API 调用

1. 在 `frontend/src/services/api.ts` 中添加 typed 函数
2. 在 `frontend/src/types/index.ts` 中添加类型定义

### 开发规范清单

- [ ] 所有 Python 导入使用 `app.*` 包路径
- [ ] 不在 `app/services/agent.py`、`consumer_agent.py`、`voice/router.py` 之外顶层导入 `deepagents`
- [ ] 避免循环导入：在函数内延迟导入
- [ ] Consumer 路由使用 `get_service_context` 依赖（非 `get_current_user`）
- [ ] 前端图标使用 `@phosphor-icons/react`
- [ ] 前端组件使用函数式 + Hooks
- [ ] API 调用通过 `api.ts` 统一管理
- [ ] 样式使用 antd 组件 + inline style，复杂场景用 CSS Modules
