# JellyfishBot 使用指南

> 本文档基于 JellyfishBot v2.0 React 前端，涵盖全部功能模块的详细使用说明。

---

## 目录

1. [快速开始](#1-快速开始)
2. [登录与注册](#2-登录与注册)
3. [界面总览](#3-界面总览)
4. [对话（Chat）](#4-对话chat)
5. [文件面板](#5-文件面板)
6. [设置中心](#6-设置中心)
   - [System Prompt 管理](#61-system-prompt-管理)
   - [Subagent 管理](#62-subagent-管理)
   - [批量运行](#63-批量运行)
   - [Service 管理](#64-service-管理)
   - [定时任务](#65-定时任务)
   - [微信接入](#66-微信接入)
   - [收件箱](#67-收件箱)
7. [Consumer 使用（外部集成）](#7-consumer-使用外部集成)
8. [环境变量配置](#8-环境变量配置)
9. [Docker 部署](#9-docker-部署)
10. [常见问题](#10-常见问题)

---

## 1. 快速开始

### 前置条件

- 部署好的 JellyfishBot 后端 (FastAPI :8000) + 前端 (Vite :3000 / Express :3000)
- 至少一个 LLM API Key（Anthropic 或 OpenAI）
- 一个有效的注册码

### 最简启动流程

```bash
# 1. 配置
cp .env.example .env
# 编辑 .env，填入 API Key

# 2. 生成注册码
python generate_keys.py

# 3. 安装并启动
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. 新终端启动前端
cd frontend && npm install && npm run dev

# 5. 浏览器访问 http://localhost:3000
```

---

## 2. 登录与注册

### 注册

1. 打开应用（http://localhost:3000），自动跳转到登录页
2. 切换到 **注册** 标签页
3. 填入：
   - **注册码**：管理员分发的一次性注册码
   - **用户名**：自定义用户名
   - **密码**：设置密码
4. 点击 **注册** 按钮
5. 注册成功后自动登录并跳转到主界面

### 登录

1. 在登录标签页输入用户名和密码
2. 点击 **登录** 按钮
3. 登录成功后跳转到对话页面

> 登录页面采用品牌分栏设计：左侧水母 Logo 品牌区（渐变 + 呼吸动画），右侧表单区。

---

## 3. 界面总览

### 主界面布局

应用采用经典的侧栏 + 内容区布局：

```
┌──────────┬──────────────────────────────────────┐
│ 侧栏      │                                      │
│ (240px)   │       内容区域                        │
│           │                                      │
│ ┌──────┐  │                                      │
│ │用户头│  │                                      │
│ │像+名 │  │                                      │
│ │+设置 │  │                                      │
│ ├──────┤  │                                      │
│ │      │  │                                      │
│ │对话列│  │                                      │
│ │表/设 │  │                                      │
│ │置菜单│  │                                      │
│ │      │  │                                      │
│ ├──────┤  │                                      │
│ │ Logo │  │                                      │
│ │+退出 │  │                                      │
│ └──────┘  │                                      │
└──────────┴──────────────────────────────────────┘
```

**侧栏元素**（从上到下）：
- **用户行**：头像 + 用户名 + 设置齿轮按钮（进入设置中心）
- **内容区域**：对话页面显示对话列表；设置页面显示设置导航菜单
- **品牌区**：JellyfishBot Logo + 名称（点击可折叠侧栏）
- **退出按钮**：退出登录

**右上角**：文件面板按钮（仅对话页面显示）

### 页面导航

| 路径 | 页面 | 入口 |
|------|------|------|
| `/` | 对话 | 默认首页 |
| `/settings/prompt` | System Prompt | 设置 → Prompt |
| `/settings/subagents` | Subagent 管理 | 设置 → Subagent 管理 |
| `/settings/general` | 通用设置（含批量运行） | 设置 → 通用 |
| `/settings/services` | Service 管理 | 设置 → Service 管理 |
| `/settings/scheduler` | 定时任务 | 设置 → 定时任务 |
| `/settings/wechat` | 微信接入 | 设置 → 微信接入 |
| `/settings/inbox` | 收件箱 | 设置 → 收件箱 |

---

## 4. 对话（Chat）

对话页面是 JellyfishBot 的核心界面，支持与 AI Agent 进行流式对话。

### 对话列表

- 位于侧栏中，显示所有历史对话
- 点击 **+** 按钮创建新对话
- 点击对话条目切换到该对话
- 悬浮显示删除按钮

### 消息输入

底部输入区域包含：

- **文本输入框**：支持多行输入（Shift+Enter 换行，Enter 发送）
- **能力开关**：点击展开能力选择栏
  - 🌐 **联网**：启用网页搜索和抓取
  - 🎨 **绘图**：启用 AI 图片生成
  - 🔊 **语音**：启用 TTS 语音生成
  - 🎬 **视频**：启用 AI 视频生成
- **Plan Mode**：启用计划模式，Agent 先规划再执行（需审批）
- **模型选择器**：下拉选择当前对话使用的 AI 模型
- **图片附件**：支持三种方式添加图片
  - 📎 点击附件按钮选择文件
  - Ctrl+V 粘贴剪贴板图片
  - 拖拽图片到输入区
- **语音输入**：点击麦克风按钮录音，松开自动转写为文字
- **发送/停止**：
  - 正常状态显示发送按钮（纸飞机图标）
  - 流式输出时变为红色停止按钮

### 流式消息展示

AI 回复以流式方式展示，包含以下类型的块：

#### 文本块
- Markdown 格式渲染，支持代码高亮（17 种常用语言）
- 代码块使用 JetBrains Mono 字体

#### 思考块（Thinking Block）
- 显示 AI 的推理思考过程
- 可折叠/展开（点击标题栏）
- 流式输出时显示三点弹跳动画
- 使用 Brain 图标标识

#### 工具调用（Tool Indicator）
- 显示工具名称和参数预览
- 执行中显示旋转加载图标
- 完成后显示绿色勾号
- 结果可折叠查看
- 使用 Wrench 图标标识

#### 子代理卡片（Subagent Card）
- 显示子代理任务描述
- 内部工具调用展示
- 流式输出子代理的回复
- 使用 Robot 图标标识

#### 审批卡片（Approval Card）
- **文件操作审批**：显示文件修改的 diff 预览，可选择批准或拒绝
- **Plan 审批**：显示 Agent 的执行计划，可编辑后批准或拒绝
- 审批按钮使用 Check（批准）和 X（拒绝）图标

### 历史消息

- 用户消息显示在右侧（蓝紫色气泡）
- AI 消息显示在左侧（带水母 Logo 头像）
- 工具调用历史以内联方式回放

### 智能滚动

- 新消息自动滚动到底部
- 用户向上滚动浏览历史时暂停自动滚动
- 返回底部时恢复自动滚动

---

## 5. 文件面板

点击右上角的 📁 按钮打开文件面板（仅对话页面可用）。

### 功能

- **浏览**：树形结构浏览用户虚拟文件系统
  - `docs/` — 文档目录（上传参考资料）
  - `scripts/` — 脚本目录（Python 脚本）
  - `generated/` — AI 生成的文件（图片/音频/视频）
- **上传**：拖拽或选择文件上传到指定目录
- **编辑**：在线编辑文本文件（代码高亮）
- **下载**：下载文件到本地
- **重命名**：修改文件/文件夹名称
- **移动**：移动文件到其他目录
- **删除**：删除文件或文件夹
- **Diff 查看**：查看文件修改前后的差异

### 典型使用场景

1. **上传参考文档**：将 PDF、TXT、CSV 等文件上传到 `docs/` 目录，Agent 对话时可自动引用
2. **管理脚本**：在 `scripts/` 目录编写或上传 Python 脚本，Agent 可在沙箱中执行
3. **查看生成物**：在 `generated/` 目录查看 AI 生成的图片、音频、视频等文件

---

## 6. 设置中心

点击侧栏用户行右侧的 ⚙️ 齿轮按钮进入设置中心。设置中心侧栏显示设置导航菜单，点击左上角 ← 返回按钮可回到对话页面。

### 6.1 System Prompt 管理

**路径**：设置 → Prompt

管理 AI Agent 的系统提示词（System Prompt），控制 Agent 的行为和角色。

**功能**：
- **编辑 Prompt**：在文本编辑器中修改当前 System Prompt
- **保存版本**：可为每次修改添加标签和备注
- **版本历史**：查看所有历史版本列表
- **Diff 对比**：查看两个版本之间的差异
- **回滚**：一键回滚到任意历史版本
- **重置**：恢复为系统默认 Prompt

### 6.2 Subagent 管理

**路径**：设置 → Subagent 管理

配置主 Agent 可调用的子代理（Subagent），实现复杂任务的分工协作。

**功能**：
- **创建子代理**：设置名称、描述、使用的模型、可用工具
- **编辑配置**：修改已有子代理的配置
- **删除子代理**：移除不需要的子代理
- **工具配置**：为每个子代理选择可用的工具集
- **模型选择**：为子代理指定独立的模型（可与主 Agent 不同）

### 6.3 批量运行

**路径**：设置 → 通用 → 页面内的「批量运行」区块（旧链接 `/settings/batch` 会重定向到本页）

通过 Excel 文件批量执行 Agent 任务，适合数据处理、批量分析等场景。

**使用流程**：

1. **上传 Excel**：选择包含任务数据的 Excel 文件（.xlsx）
2. **配置运行**：
   - 选择输入列（作为 Agent 的消息）
   - 选择使用的模型
   - 配置能力开关
   - 设置自定义 Prompt（可选）
3. **开始运行**：批量任务开始执行
4. **查看进度**：实时显示当前进度（已完成/总数）
5. **下载结果**：完成后下载包含 AI 回复的结果 Excel

**注意事项**：
- 每行数据独立执行，互不影响
- 支持中途取消
- 结果文件包含原始数据 + AI 回复列

### 6.4 Service 管理

**路径**：设置 → Service 管理

将配置好的 Agent 发布为 Service，供外部消费者（Consumer）通过 API 调用。

#### 界面布局

- **左侧 (30%)**：Service 列表卡片
  - 绿色圆点 = 已发布
  - 灰色圆点 = 草稿
  - 选中时左侧显示品牌色高亮条
- **右侧 (70%)**：选中 Service 的详情，分为 4 个标签页

#### 标签页

##### Basic Info（基础信息）
- **名称**：Service 显示名称
- **描述**：Service 说明文本
- **模型**：选择 Service 使用的 AI 模型
- **System Prompt**：可选择使用已保存的 Prompt 版本或自定义
- **能力**：勾选 Service 开放的能力（联网搜索、定时任务、图片生成、语音生成、视频生成）
- **允许的文档/脚本**：选择 Consumer 可访问的文档和脚本
- **发布状态**：切换 Service 的发布/草稿状态

##### API Keys
- **生成 Key**：为 Service 创建新的 API Key（格式 `sk-svc-...`）
- **查看 Key**：列表展示所有 Key（创建时间、备注）
- **复制 Key**：一键复制 Key 到剪贴板
- **删除 Key**：撤销某个 Key

##### WeChat Channel（微信渠道）
- **启用/禁用**：开关微信扫码渠道
- **过期时间**：设置渠道有效期
- **最大会话数**：限制同时连接数
- **QR 链接**：获取微信扫码中间页链接
- **活跃会话**：查看当前连接的微信用户
- **断开会话**：主动断开某个微信用户

##### Test（内联测试）
- 无需离开管理页面，直接与 Service 进行测试对话
- 自动创建临时 API Key 和测试对话

### 6.5 定时任务

**路径**：设置 → 定时任务

管理定时执行的自动化任务，支持管理员任务和服务任务两类。

#### 管理员任务

- **任务类型**：
  - **Script**：定时执行 `scripts/` 目录下的 Python 脚本
  - **Agent**：定时执行 Agent 任务（发送 Prompt + 可选文档上下文）
- **调度类型**：
  - **once**：一次性执行（指定 ISO 时间）
  - **cron**：Cron 表达式定时（如 `0 9 * * *` 每天 9 点）
  - **interval**：间隔执行（秒数）
- **沙箱权限**：可配置脚本读/写目录
- **微信推送**：开启 `reply_wechat` 可将结果推送到管理员微信

#### 服务任务

- 仅支持 Agent 类型
- 使用 Consumer Agent 执行
- 结果自动推送到 WeChat 渠道
- 记录 `reply_to` 信息（channel、session、conversation）

#### 运行记录

- 查看每次执行的详细步骤日志
- 步骤类型包括：start → docs_loaded → loop → tool_call → tool_result → ai_message → finish
- 可查看错误信息和执行时间

### 6.6 微信接入

**路径**：设置 → 微信接入

将管理员自己的主 Agent 通过微信 iLink 协议接入，实现在微信中与 Agent 对话。

**功能**：
- **生成二维码**：生成微信扫码登录二维码
- **状态轮询**：自动检查扫码登录状态
- **连接管理**：查看连接状态、断开连接
- **消息展示**：查看微信对话记录
- **多模态支持**：支持接收/发送图片、语音（通过 CDN + AES 加解密）

**注意**：微信接入使用 iLink Bot 协议，需要在微信端完成扫码绑定。

### 6.7 收件箱

**路径**：设置 → 收件箱

接收来自 Consumer 的 humanchat 消息（当 Consumer 在对话中请求人工介入时）。

**功能**：
- **消息列表**：显示未读/已读/已处理的消息
- **未读计数**：侧栏菜单显示未读消息数量徽章
- **标记状态**：将消息标记为已读/已处理
- **查看详情**：查看消息的完整内容和上下文

---

## 7. Consumer 使用（外部集成）

### 概述

Consumer 层面向外部用户和系统，通过 Service API Key 认证访问 AI Agent 能力。

### 认证方式

所有 Consumer API 请求需在 HTTP Header 中携带：

```
Authorization: Bearer sk-svc-xxxxxxxxxxxxx
```

### API 接口

#### 创建对话

```bash
curl -X POST http://your-host/api/v1/conversations \
  -H "Authorization: Bearer sk-svc-xxx" \
  -H "Content-Type: application/json" \
  -d '{"title": "新对话"}'
```

#### 发送消息（自定义 SSE）

```bash
curl -X POST http://your-host/api/v1/chat \
  -H "Authorization: Bearer sk-svc-xxx" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"conversation_id": "conv-id", "message": "你好"}'
```

SSE 事件类型与 Admin 端一致：`token`、`thinking`、`tool_call`、`tool_result`、`done`、`error` 等。

#### OpenAI 兼容接口

```bash
curl -X POST http://your-host/api/v1/chat/completions \
  -H "Authorization: Bearer sk-svc-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

完全兼容 OpenAI API 格式，可直接替换 `base_url` 在已有的 OpenAI SDK 代码中使用。

#### 获取对话历史

```bash
curl http://your-host/api/v1/conversations/conv-id \
  -H "Authorization: Bearer sk-svc-xxx"
```

#### 列出生成文件

```bash
curl http://your-host/api/v1/conversations/conv-id/files \
  -H "Authorization: Bearer sk-svc-xxx"
```

### 独立聊天页

每个 Service 可通过以下 URL 访问独立的聊天网页：

```
http://your-host/s/{service_id}
```

页面由 FastAPI 后端渲染，无需 API Key（通过页面内置认证）。

### 多模态消息

Consumer 支持发送多模态消息（图片 + 文字）：

```json
{
  "conversation_id": "conv-id",
  "message": [
    {"type": "text", "text": "这张图片是什么？"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ]
}
```

---

## 8. 环境变量配置

### 必需配置（至少一个）

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API 密钥（Claude 系列） |
| `OPENAI_API_KEY` | OpenAI API 密钥（GPT + 多媒体生成） |

### Provider 端点覆盖

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_BASE_URL` | 自定义 Anthropic API 端点 |
| `OPENAI_BASE_URL` | 自定义 OpenAI API 端点 |

### 按能力覆盖

当某项能力需要使用与主 Provider 不同的 API Key 或端点时：

| 能力 | Key 变量 | URL 变量 |
|------|----------|----------|
| 图片生成 | `IMAGE_API_KEY` | `IMAGE_BASE_URL` |
| TTS 语音 | `TTS_API_KEY` | `TTS_BASE_URL` |
| 视频生成 | `VIDEO_API_KEY` | `VIDEO_BASE_URL` |
| 实时语音 | `S2S_API_KEY` | `S2S_BASE_URL` |
| 语音转写 | `STT_API_KEY` | `STT_BASE_URL` |

### 联网工具

| 变量 | 说明 |
|------|------|
| `CLOUDSWAY_SEARCH_KEY` | CloudsWay 搜索 API（优先使用） |
| `CLOUDSWAY_READ_URL` | CloudsWay 网页抓取端点 |
| `CLOUDSWAY_SEARCH_URL` | CloudsWay 搜索端点 |
| `TAVILY_API_KEY` | Tavily 搜索 API（备选） |

### S3 存储（可选）

| 变量 | 说明 |
|------|------|
| `STORAGE_BACKEND` | `local`（默认）或 `s3` |
| `S3_BUCKET` | S3 桶名称 |
| `S3_REGION` | S3 区域 |
| `S3_ENDPOINT_URL` | 自定义端点（MinIO/R2/OSS） |
| `S3_ACCESS_KEY_ID` | S3 访问密钥 |
| `S3_SECRET_ACCESS_KEY` | S3 秘密密钥 |

### 可观测性

| 变量 | 说明 |
|------|------|
| `LANGFUSE_SECRET_KEY` | Langfuse 密钥 |
| `LANGFUSE_PUBLIC_KEY` | Langfuse 公钥 |
| `LANGFUSE_HOST` | Langfuse 服务地址 |
| `LANGCHAIN_TRACING_V2` | 启用 LangSmith |
| `LANGCHAIN_API_KEY` | LangSmith API Key |

---

## 9. Docker 部署

### 架构

```
Cloudflare (SSL) → Nginx (:80) → Express (:3000) → FastAPI (:8000)
```

### 部署步骤

```bash
# 1. 准备配置
cp .env.example .env
# 编辑 .env

# 2. 生成注册码
python generate_keys.py

# 3. 构建并启动
docker compose up -d --build
```

### 镜像构建过程

- **Stage 1**（frontend-builder）：Node.js 20 编译 React 前端
- **Stage 2**（production）：Python 3.11 + Node.js 20 运行时
  - 安装 Python 依赖
  - 安装 Express 生产运行时
  - 复制前端构建产物
  - 清理源码和开发依赖

### 数据持久化

用户数据通过 Docker Volume 挂载：

```yaml
volumes:
  - ./data/users:/app/users    # 用户数据（对话、文件、checkpoints）
```

### 健康检查

容器内置健康检查（检查 FastAPI `/docs` 端点），Nginx 在后端就绪后才接受流量。

### 日志查看

```bash
docker compose logs -f              # 全部日志
docker compose logs -f jellyfishbot # 仅应用日志
docker compose logs -f nginx        # 仅 Nginx 日志
```

---

## 10. 常见问题

### Q: 注册时提示"注册码无效"？

A: 确认 `config/registration_keys.json` 文件存在且包含有效注册码。使用 `python generate_keys.py` 生成新注册码。每个注册码只能使用一次。

### Q: 前端无法连接后端？

A: 确认：
1. 后端 FastAPI 已在 `:8000` 启动
2. 前端开发服务器 Vite 在 `:3000` 启动（自动代理 `/api` → `:8000`）
3. 如使用 Docker，检查 `docker compose logs -f` 确认两个服务都已就绪

### Q: 模型列表为空？

A: 至少需要配置一个有效的 API Key：
- `ANTHROPIC_API_KEY`：启用 Claude 系列模型
- `OPENAI_API_KEY`：启用 GPT 系列模型 + 多媒体生成能力

### Q: 联网搜索不可用？

A: Admin Agent 默认启用联网工具，但需要配置搜索 API：
- `CLOUDSWAY_SEARCH_KEY`（推荐，优先使用）
- `TAVILY_API_KEY`（备选）

### Q: 图片/语音/视频生成失败？

A: 确认已配置 `OPENAI_API_KEY`。如果需要使用不同的 API Key 或端点，可通过 `IMAGE_API_KEY`、`TTS_API_KEY`、`VIDEO_API_KEY` 分别覆盖。

### Q: 微信扫码后无法收到消息？

A: 参考以下检查清单：
1. 确认 iLink Bot 服务端正常（扫码后应看到连接状态变为"已连接"）
2. 检查后端日志中 `wechat.*` 相关日志
3. 确认 `main.py` 中 `logging.basicConfig(level=logging.INFO)` 已启用
4. 注意：iLink（国内）需直连不走代理

### Q: 脚本执行失败？

A: 脚本在沙箱中执行，注意以下限制：
- 不能导入危险模块（subprocess、pathlib、ctypes、io、pickle 等）
- 不能使用 exec、eval、getattr 等危险内置函数
- 文件路径使用相对路径（`../docs/file.csv`，不是 `/docs/file.csv`）
- 脚本的 cwd 是 `scripts/` 目录

### Q: 如何备份用户数据？

A: 
- 本地模式：备份 `users/` 和 `data/` 目录
- Docker 模式：备份 `./data/users/` 目录（包含所有用户数据和 checkpoints.db）
- S3 模式：S3 存储自带冗余，按需备份即可
