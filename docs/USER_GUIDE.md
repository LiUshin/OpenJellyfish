# OpenJellyfish 使用指南

> 本文档基于 OpenJellyfish v2.0+ React 前端与 Tauri 桌面端，涵盖全部功能模块的详细使用说明。
> 更新日期：2026-07-07

---

## 目录

1. [快速开始](#1-快速开始)
   - [1.1 三种启动方式](#11-三种启动方式)
   - [1.2 桌面 App（推荐新用户）](#12-桌面-app推荐新用户)
   - [1.3 命令行启动（开发者）](#13-命令行启动开发者)
   - [1.4 Docker 部署（团队/服务器）](#14-docker-部署团队服务器)
2. [登录与注册](#2-登录与注册)
3. [界面总览](#3-界面总览)
4. [对话（Chat）](#4-对话chat)
5. [文件面板](#5-文件面板)
6. [设置中心](#6-设置中心)
   - [6.1 操作规则（System Prompt + Memory & Soul + 能力提示词）](#61-操作规则)
   - [6.2 Subagent 管理](#62-subagent-管理)
   - [6.3 Python 环境（per-user venv）](#63-python-环境per-user-venv)
   - [6.4 收件箱](#64-收件箱)
   - [6.5 环境与通用设置（API Key + 时区 + 主题 + Advanced 开关）](#65-环境与通用设置)
   - [6.6 Service 管理](#66-service-管理)
   - [6.7 定时任务](#67-定时任务)
   - [6.8 微信接入（管理员自接入）](#68-微信接入管理员自接入)
7. [Service 渠道：让 Consumer 通过微信扫码使用](#7-service-渠道让-consumer-通过微信扫码使用)
8. [Consumer 使用（外部 API 集成）](#8-consumer-使用外部-api-集成)
9. [Soul 记忆系统](#9-soul-记忆系统)
10. [语音交互](#10-语音交互)
11. [环境变量配置](#11-环境变量配置)
12. [常见问题（FAQ）](#12-常见问题faq)

---

## 1. 快速开始

### 1.1 三种启动方式

| 方式 | 适合人群 | 优点 |
|------|----------|------|
| **桌面 App**（Tauri） | 不熟悉命令行的终端用户 | 双击即用，自带运行时，自动管理 |
| **命令行** | 开发者 | 热重载、自由调试 |
| **Docker** | 团队/服务器部署 | 一键部署、生产稳定 |

### 1.2 桌面 App（推荐新用户）

1. 从 GitHub Release 下载对应平台的安装包：
   - Windows：`OpenJellyfish-x.y.z-x64.exe`（NSIS 安装器）
   - macOS（Apple Silicon）：`OpenJellyfish-x.y.z-aarch64.dmg`
   - macOS（Intel）：`OpenJellyfish-x.y.z-x64.dmg`
2. 安装并打开 OpenJellyfish。
3. **首次启动会自动完成**：
   - 检测内置 Python 3.12 + Node.js 20 运行时
   - 解压 backend / frontend 资源到安装目录
4. 在 **控制台** 页填入至少一个 LLM API Key（OpenAI 或 Anthropic），点击 **测试连接** 验证。
5. 点击中央圆形 **START** 按钮启动后台服务。
6. 服务就绪后会自动跳到浏览器：默认 <http://localhost:3000>。

#### 桌面端 4 个 Tab

| Tab | 功能 |
|---|---|
| **控制台** | 环境检测、API Keys 配置、START / STOP 按钮 |
| **注册码管理** | 生成、复制、删除注册码（首次部署需要） |
| **账户管理** | 查看用户列表、重置密码、删除用户、统计 |
| **关于 / 工具** | 版本号、查看最新 Release、打开项目目录 / 用户数据 / 日志目录 |

> 关闭桌面 App = 停止后台服务 + 清理子进程。

### 1.3 命令行启动（开发者）

#### 前置条件

- Python 3.11+
- Node.js 20+
- 至少一个 LLM API Key（Anthropic 或 OpenAI）
- 一个有效的注册码（首次部署运行 `python generate_keys.py` 生成）

#### 推荐：跨平台启动器

```bash
# 一键启动（自动端口检测 + 旧实例清理 + 双进程管理）
python launcher.py              # 生产模式
python launcher.py --dev        # 开发模式（uvicorn --reload + vite dev）
python launcher.py --port 9000  # 自定义后端端口
python launcher.py --backend-only  # 仅后端

# 快捷脚本
./start_local.sh    # Mac/Linux
start_local.bat     # Windows（双击）
```

启动后浏览器访问 <http://localhost:3000>。

#### 手动启动（调试用）

```bash
# 1. 后端
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux/macOS
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 API Key
python generate_keys.py        # 生成注册码（首次）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 2. 前端（新终端）
cd frontend
npm install
npm run dev                    # → http://localhost:3000
```

### 1.4 Docker 部署（团队/服务器）

```bash
# 1. 准备配置
cp .env.example .env
# 编辑 .env，填入 API Key 等

# 2. 生成注册码
python generate_keys.py
# 或在 Tauri 桌面 App 的「注册码管理」页生成

# 3. 准备数据目录权限（首次部署必做）
#    应用容器以 jellyfish (uid=1000) 运行，挂载的 ./data 必须可写
mkdir -p ./data/users
sudo chown -R 1000:1000 ./data

# 4. 构建并启动
docker compose up -d --build

# 5. 查看日志
docker compose logs -f
docker compose logs -f openjellyfish   # 仅应用日志
docker compose logs -f nginx          # 仅 Nginx 日志
```

> ⚠️ **第 3 步不能省**：如果 `./data` 不存在，docker daemon 会以 root 自动创建，容器内 uid=1000 写不进去，FastAPI 启动会报 `sqlite3.OperationalError: unable to open database file` 然后无限重启。详见 FAQ。

#### 架构

```
Cloudflare (SSL) → Nginx (:80) → Express (:3000) → FastAPI (:8000)
```

#### 数据持久化

```yaml
volumes:
  - ./data/users:/app/users    # 用户数据：对话、文件、checkpoints、API Key
```

#### 健康检查

容器内置健康检查（检查 FastAPI `/docs`），Nginx 在后端就绪后才接受流量。

---

## 2. 登录与注册

### 2.1 注册

1. 打开应用（<http://localhost:3000>），自动跳转到登录页。
2. 切换到 **注册** 标签页。
3. 填入：
   - **注册码**：管理员分发的一次性注册码（如 `OJF-XXXX-XXXX-XXXX`）
   - **用户名**：自定义用户名
   - **密码**：设置密码
4. 点击 **注册** 按钮。
5. 注册成功后自动登录并跳转到主界面。

> 注册码每个仅可使用一次。新版注册码前缀为 **`OJF-`**；历史上发放的 `JFBOT-` / `DA-` 格式码**仍然有效**（校验只看完整字符串，不看前缀）。如果丢失或不够用，管理员可在 Tauri 桌面 App 的「注册码管理」页生成新的。

### 2.2 登录

1. 在登录标签页输入用户名和密码。
2. 点击 **登录** 按钮。
3. 登录成功后跳转到对话页面。

### 2.3 设计

登录页采用品牌分栏设计：左侧 40% 是水母 Logo 品牌区（渐变 + 呼吸动画 + 像素点底纹），右侧 60% 是表单区。窄屏（约 900px 以下）改为上下堆叠。

---

## 3. 界面总览

### 3.1 主界面布局

桌面最左侧是 56px 的全局图标栏：聊天、服务、定时任务、环境；鼠标悬停显示名称，底部依次是设置和用户图标。用户菜单包含退出登录。服务图标显示收件箱未读数。中间区域根据当前入口显示二级列表或菜单及正文。聊天的二级栏显示对话列表；服务显示“我的服务”和“收件箱”；定时任务显示任务列表；环境显示模型与连接、Python 依赖；设置包含通用设置。二级栏顶部只显示 OpenJellyfish 名称、明暗切换和语言切换。

文件浏览面板保持在右侧，可以与上述任何区域和设置页同时使用；打开文件时可在正文旁预览。窄桌面改用右侧浮层/抽屉，手机用全屏抽屉。手机通过左上角菜单切换四个一级入口。

### 3.2 页面导航

| 路径 | 页面 | 入口 |
|------|------|------|
| `/` | 对话 | 默认首页 |
| `/settings/prompt` | 操作规则（System Prompt + Memory & Soul + 能力提示词） | 设置 → Prompt |
| `/settings/subagents` | Subagent 管理 | 设置 → Subagent 管理 |
| `/settings/packages` | Python 依赖 | 环境 → Python 依赖 |
| `/settings/inbox` | 收件箱 | 服务 → 收件箱（含未读数徽章） |
| `/settings/environment` | 模型与 API/CLI 连接 | 环境 → 模型与连接 |
| `/settings/general` | 界面与时间、高级选项 | 设置 → 通用设置 |
| `/settings/services` | Service 管理 | 服务 → 我的服务 |
| `/settings/scheduler` | 定时任务 | 左侧“定时任务” |
| `/settings/wechat` | 微信接入 | 设置 → 微信接入 |

### 3.3 多主题切换

二级栏顶部的太阳/月亮图标切换明暗模式；更多主题选项在 **设置 → 通用设置 → 界面与时间** 中选择。

| 主题 | 风格 |
|---|---|
| **dark**（默认） | 暖粉紫深色 |
| **cyber-ocean** | 青蓝浅色 |
| **terminal** | 磷绿 CRT 终端（全局等宽字体 + 无圆角 + 磷光发光 + 扫描线） |

主题选择持久化在浏览器 `localStorage` 中。

---

## 4. 对话（Chat）

对话页面是 OpenJellyfish 的核心界面，支持与 AI Agent 进行流式对话。

### 4.1 对话列表

- 位于侧栏中，按项目和未归类显示历史对话
- 点击 **+** 按钮创建新对话
- 点击对话条目切换到该对话
- 悬浮显示删除按钮
- 后台仍在 streaming 的对话切换回去时会自动恢复显示

### 4.1.1 项目

项目用于在同一主题下管理多个管理员对话。在聊天侧栏创建项目、把现有对话移入项目，或在项目中创建新对话；打开项目可查看相关对话、编辑一份 Markdown brief，并在该项目范围内搜索对话。删除项目会把这些对话放回未归类，不删除聊天记录。

项目 brief 会在该项目的每轮对话中提供给 Agent，注入部分的上限为 5,000 token。修改 brief 后，已有对话的下一轮也会使用新内容；超出预算时保存的文档保留原文，进入上下文的副本会截断。项目只组织对话，不改变 `/docs` 的文档管理方式，也不会自动成为 Service 消费者的上下文。

### 4.2 消息输入

底部输入区域包含：

- **文本输入框**：支持多行输入（Shift+Enter 换行，Enter 发送）
- **能力开关**：点击展开能力选择栏
  - 🌐 **联网**：启用网页搜索和抓取（Admin 默认始终启用）
  - 🎨 **绘图**：启用 AI 图片生成
  - 🔊 **语音**：启用 TTS 语音生成
  - 🎬 **视频**：启用 AI 视频生成
- **Plan Mode（DeepAgents）**：Agent 先规划再执行（需审批）；Codex / Cursor 尚未统一接入原生 Plan mode，账号套餐支持不等于计划模式支持
- **模型选择器**：下拉选择当前对话使用的 AI 模型
- **图片附件**：支持三种方式添加图片
  - 📎 点击附件按钮选择文件
  - Ctrl+V 粘贴剪贴板图片
  - 拖拽图片到输入区
- **语音输入**：单击麦克风按钮开始录音 → 再次单击停止 → 自动转写填入输入框；录音中按 Esc 取消
- **工作区锁**：工具栏锁图标，选择 **自动 / 手动路径 / Agent 自声明** 三种模式（见 §4.10）；chat 顶栏另有入口打开锁状态面板
- **发送/Stop 按钮**：
  - 正常状态显示发送按钮（纸飞机图标）
  - 流式输出时变为红色 Stop 按钮，点击中止当前回复

### 4.3 消息队列（Query Queue）

当 Agent **正在流式回复**时，你仍可继续输入并发送——消息会进入**当前对话的排队列表**（显示在输入区上方），而不是被丢弃。

| 行为 | 说明 |
|---|---|
| **默认排队** | 新消息 FIFO 入队；当前轮次 `done` 后自动按顺序发送下一条 |
| **↵ 立刻执行** | 排队行右侧的回车按钮：中断当前流式轮次（保留已生成内容），用该条消息**同一条 SSE 连接**继续对话 |
| **编辑 / 排序** | 排队项可改文案、上移/下移；HITL 审批等待期间队列功能受限（仅排队模式可用） |

> 队列仅作用于 **Admin 对话页**，不影响 Service 消费者页面。

### 4.4 流式消息展示

AI 回复以流式方式展示，包含以下类型的块：

#### 文本块
- Markdown 格式渲染，支持代码高亮（17 种常用语言）
- 代码块使用 JetBrains Mono 字体
- **媒体嵌入**：自动识别 `<<FILE:/generated/xxx.png>>` 标签，展示图片/音频/视频/PDF/HTML

#### 思考块（Thinking Block）
- 显示 AI 的推理思考过程（仅 Thinking 模型）
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
- 内部工具调用按时间顺序展示（text/tool/thinking 交替的 timeline）
- 流式输出子代理的回复
- 使用 Robot 图标标识

#### 审批卡片（Approval Card）
- **文件操作审批**：显示文件修改的 diff 预览（`edit_file` 为 git 风格 hunk），可选择批准或拒绝
- **Plan 审批**：显示 Agent 的执行计划，可编辑后批准或拒绝
- 审批按钮使用 Check（批准）和 X（拒绝）图标

#### 文件写入流式预览（Streaming File Preview）
- `write_file` / `edit_file` 在 LLM 生成参数的过程中即显示代码预览（打字机效果）
- 审批前即可看到即将写入/修改的内容

#### 定时任务结果卡片（Scheduled Task Card）
- 定时任务执行结果以专用蓝色卡片展示（与工具调用区分）
- 任务元信息（名称、状态、输出摘要）可折叠查看

### 4.5 历史消息

- 用户消息显示在右侧（粉紫渐变气泡）
- AI 消息显示在左侧（带水母 Logo 头像）
- 历史消息自动按交错顺序回放（thinking → text → tool → text → subagent → text…）
- 用户附件以缩略图画廊形式展示，点击可放大

### 4.6 智能滚动

- 新消息自动滚动到底部
- 用户向上滚动浏览历史时**暂停**自动滚动
- 返回底部时**恢复**自动滚动

### 4.7 断流恢复

- 后端崩溃 / 网络中断时，已生成的部分内容会自动持久化（标记 ⚠️ [连接中断 — 已保存已生成内容]）
- 切换回正在 streaming 的对话时，前端会显示黄色横幅，提供 **「终止并保存」** 和 **「刷新状态」** 按钮
- 后台 streaming 中无法发送新消息（防止冲突）；可使用 §4.3 队列在中途追加后续指令

### 4.8 YOLO 模式（自动批准）

在 **环境 → 模型与连接** 中开启 **YOLO 模式** 后，Admin 对话中的 HITL 审批（`write_file` / `edit_file` / `propose_plan` 等）将**自动批准**，不再弹出审批卡片。

- 仅作用于 **Admin 端**；Service 消费者页面无审批流程，不受此开关影响
- 状态保存在浏览器 `localStorage`，各设备独立
- 发生过自动批准的对话，输入区底部会显示极简橙色 `yolo` 小标签提示

### 4.9 @ 文件引用

在输入框输入 `@` 可唤起**文件候选列表**（模糊搜索 `docs/`、`scripts/`、`generated/` 等），选中后插入 `[[FILE:/path]]` 芯片。

- 发送时后端自动展开为 `<<FILE:/path>>`，与聊天渲染、文件面板跳转一致
- 点击聊天中的文件链接可一键在右侧文件面板定位

### 4.10 工作区锁（Workspace Lock）

防止多个 Agent 进程（对话、定时任务、子代理）同时写入同一目录。工具栏锁图标可选择：

| 模式 | 行为 |
|---|---|
| **自动** | 优先独占整个工作区；若冲突则退化为锁定空闲的顶层目录 |
| **手动** | 仅锁定你勾选的路径（文件或目录） |
| **Agent** | 不预锁，由 Agent 运行时调用 `acquire_workspace` 自行声明 |

chat 顶栏 **锁** 按钮可查看当前活跃进程、占用路径，并手动释放（不中止 Agent，仅解锁）。

### 4.11 左侧 Query 导航

对话区左侧悬浮短横线导航：**每条用户消息对应一根 bar**。滚动时高亮当前可见的 query；点击可跳到对应消息（长会话配合虚拟列表使用）。

---

### 4.12 Codex / Cursor 对话（可选）

默认引擎为 DeepAgents。部署方启用外部运行模式并授权连接后，在聊天输入区选择获授权的 API / Codex / Cursor 模型；首次发送绑定引擎和连接。同一连接可逐轮切换获授权模型，切换引擎或供应商账号需要新建对话。

支持文本与工具流式输出、逐次审批、停止、附件和产物预览 / 下载。关闭浏览器不会取消任务，重新打开会话可恢复输出与待审批项。同一连接共享供应商额度并串行运行；连接失效或授权撤销时不会静默换账号或付费 API。

自 v1.3.1 起，此路径支持管理员 Web 聊天，以及已授权 Codex / Cursor 套餐的内部 Service 网页、API 和 Service 微信。管理员个人微信沿用原有执行路径；套餐 Service 暂不支持定时任务、语音与视频。管理员在 Service 设置中固定连接、模型和开放资源，并创建「使用已授权套餐」Key；套餐 Key 不支持 BYOK。此模式仅面向可信内部成员，不提供恶意租户间的操作系统隔离。详见[内部 Service 分发](https://github.com/LiUshin/OpenJellyfish/blob/main/docs/runtime-service-distribution.md)。Codex 原生生图有本机 API 验证，Cursor 真实生图与生图前端最终视觉验收仍未完成。

### 4.13 在管理员对话中测试 Service（v1.4.1）

在对话栏选择自己的 Service 并开启「测试模式」，下一条消息由该 Service 按当前保存的配置执行；草稿与已发布 Service 均可测试。关闭后回到管理员 Agent，测试提问和回复仍留在同一对话中，可以直接要求 Agent 复盘和修改。

测试使用 Service 的模型 / CLI core、Prompt、开放文档与能力，独立维护上下文，不继承管理员此前的聊天或项目 brief。测试记录不混入真实使用者记录；`contact_admin` 外部提醒在测试中禁用，其他已授权工具仍可能执行真实操作。测试通过不能代替真实网页、API 或微信渠道验收。

### 4.14 Tracing：查看与回放工作过程（v1.4.1）

在管理员聊天切换到 Tracing，可查看每轮提问、回答和已记录的工具活动；用工作区画布查看涉及的文件与动作，展开输入、输出、差异和文件位置，也可按动作顺序回放。画布支持平移、缩放和适应视图。

Tracing 展示的是会话已记录的工具证据，不是模型隐藏推理或完整系统审计。文件位置可能为近似值或「未记录」；未显示读取范围不等于没有读过文件。旧对话、不同引擎和工具返回的信息完整程度可能不同。

### 4.15 用 Codex / Cursor 保存文档（v1.4.2）

可以直接要求管理员 Agent「把结果保存到文档库 `/docs/研究记录.md`」。使用 `jellyfish_write_document` 时，会出现路径、原内容预览和拟写入内容的确认卡；同意后才能在文件面板中看到保存结果。拒绝或审批超时不会保存；开启 YOLO 的轮次按该轮设置自动批准。

每次最多写入 64 KiB UTF-8 文本。默认只新建，替换不同内容的旧文件需要明确覆盖；同内容重试不会重复写入。CLI 在会话工作副本中修改 `docs/` 仍只会归档为产物，不会自动更新文档库。

已有 Cursor 对话下一轮会刷新工具并恢复会话；已有 Codex 对话无法追加新工具，需要新建对话。升级不会自动搬运旧工作副本文件。该能力仅用于管理员文档库，不扩大 Service 或定时任务权限。

## 5. 文件面板

点击右上角的 📁 按钮打开文件面板（仅对话页面可用）。

文件预览工具栏新增紧凑胶囊：平时显示当前文件的简短标识；鼠标悬浮、点击或键盘聚焦后按 Enter 展开文件标签。悬停其他文件可先看内容预览，不切换当前文件；点击或键盘选择后再切换。窄屏保留横向标签和文件操作入口。

### 5.1 浏览

树形结构浏览用户虚拟文件系统：

| 目录 | 用途 |
|---|---|
| `docs/` | 文档目录（上传参考资料，Agent 可读） |
| `scripts/` | 脚本目录（Python 脚本，Agent 可执行） |
| `generated/` | AI 生成的文件（图片/音频/视频，自动写入） |
| `soul/` | Soul 记忆笔记（启用 Soul 文件系统时显示） |

### 5.2 操作

- **上传**：拖拽或选择文件上传到指定目录
- **下载**：单击下载按钮或工具栏的下载图标
- **重命名**：右键菜单或长按弹出
- **移动**：拖拽到目标文件夹
- **删除**：右键菜单
- **Diff 查看**：查看文件修改前后的差异

### 5.3 文件预览面板（多类型查看器）

不同类型的文件采用不同的渲染策略：

| 类型 | 渲染方式 |
|---|---|
| 图片 / 音频 / 视频 / PDF | 原生播放 / 预览，工具栏隐藏「保存」按钮 |
| Markdown | 复用聊天页 markdown 渲染（含代码高亮 + 媒体嵌入） |
| HTML | `<iframe>` 渲染（允许 Plotly/ECharts 但禁止访问父页 cookies） |
| CSV / TSV | antd 表格渲染（最多 2000 行，超过显示提示） |
| JSON / JSONL | 语法高亮预览，解析失败降级为源码 |
| 文本 / 代码 | 简洁 textarea 编辑器（无高亮编辑） |
| 二进制 | 占位 + 下载按钮 |

可切换类型（Markdown/HTML/CSV/JSON）的工具栏头部带 **预览/源码** 切换器。

### 5.4 典型使用场景

| 场景 | 操作 |
|---|---|
| 上传参考文档 | 将 PDF/TXT/CSV 等上传到 `docs/`，Agent 对话时自动引用 |
| 管理脚本 | 在 `scripts/` 编写或上传 Python 脚本，Agent 可在沙箱中执行 |
| 查看生成物 | 在 `generated/` 查看 AI 生成的图片、音频、视频 |
| Soul 记忆 | 启用 Soul 后在 `soul/` 中编辑 Agent 的人格笔记 |

---

## 6. 设置中心

点击左侧图标栏底部的 ⚙️ 齿轮按钮进入设置中心。其下方是用户图标；点击后可在菜单中退出登录。切换回聊天请点击左侧聊天图标。

### 6.1 操作规则

**路径**：设置 → Prompt

包含三个 Tab（部分 Tab 默认隐藏，需在 **通用 → 高级功能** 开启）：

#### 6.1.1 Profile（用户画像）

- 配置 Agent 对你的认知（投资偏好、专业背景、个性化备注等）
- **我的个性规则**（`custom_notes`）：你手写的规则，带**版本历史**（Diff / 回滚 / 重命名）
- **Agent 记忆**（`agent_notes`）：由 Agent 通过 `update_personal_memory` 工具维护的自由文本；可**锁定**防止 Agent 再改
- 用户画像通过 `{user_profile_context}` 占位符注入到 System Prompt（个性规则与 Agent 记忆分两段展示）

#### 6.1.2 操作规则（System Prompt + 能力提示词）

仅在 **通用 → 高级功能 → 操作规则** 开启时显示。

- **编辑 Prompt**：在文本编辑器中修改当前 System Prompt
- **保存版本**：可为每次修改添加标签和备注
- **版本历史**：查看所有历史版本列表
- **Diff 对比**：查看两个版本之间的差异
- **回滚**：一键回滚到任意历史版本
- **重置**：恢复为系统默认 Prompt
- **能力提示词**：折叠面板，展开可逐条编辑/恢复默认（如 scheduler 必须用什么时区格式、媒体生成怎么用 `<<FILE:>>` 标签）

#### 6.1.3 Memory & Soul（记忆与灵魂）

仅在 **通用 → 高级功能 → Memory & Soul** 开启时显示。

- **Memory Subagent 写入**：Switch
  - 关闭：Memory Subagent 仅能读对话和 inbox
  - 开启：Memory Subagent 可在 `filesystem/soul/` 创建/编辑/删除笔记（Agent 主动总结记忆到长期人格中）
  - 下方附带可编辑的能力提示词
- **Soul 文件系统**：Switch
  - 关闭：主 Agent 不可见 `/soul/` 目录
  - 开启：主 Agent 在文件面板和工具中可直接读写 `/soul/`
  - 下方附带可编辑的能力提示词
- **包含消费者对话**：Switch — Memory Subagent 是否能读取 Service Consumer 对话历史
- **最近消息条数**：默认 5（供调度 Agent 任务注入对话上下文；收件箱提醒不调用模型）

> Soul 系统详细说明见 §9。

### 6.2 Subagent 管理

**路径**：设置 → Subagent 管理

配置主 Agent 可调用的子代理（Subagent），实现复杂任务的分工协作。

**功能**：
- **创建子代理**：设置名称、描述、使用的模型、可用工具
- **编辑配置**：修改已有子代理的配置
- **删除子代理**：移除不需要的子代理
- **工具配置**：从可用工具池中勾选（包括联网/媒体/调度/记忆/Soul 等）
- **模型选择**：为子代理指定独立的模型（可与主 Agent 不同）

**内置 Memory Subagent**：
- 默认提供，不可删除（可禁用）
- 默认工具：读 Admin 对话 / 读 Service Consumer 对话 / 读 inbox
- 启用 Memory Subagent 写入后追加 soul 写工具（list/read/write/delete）

**Subagent 可用工具池**：

| 类别 | 工具 |
|---|---|
| 通用 | `run_script` / `web_search` / `web_fetch` / `generate_image` / `generate_speech` / `generate_video` / `schedule_task` / `manage_scheduled_tasks` / `publish_service_task` / `send_message` / `spawn_child_task` |
| 记忆 | `list_conversations` / `read_conversation` / `list_service_conversations` / `read_service_conversation` / `read_inbox` / `soul_list` / `soul_read` / `soul_write` / `soul_delete` / `list_task_trees` / `read_task_tree` |

### 6.3 Python 环境（per-user venv）

**路径**：环境 → Python 依赖

每个 Admin 拥有独立的 Python 虚拟环境（`users/{你的用户名}/venv/`），用于脚本执行。

**功能**：
- **初始化环境**：首次使用需点击 **初始化** 创建 venv（继承系统预装包：numpy/pandas/matplotlib 等）
- **安装包**：输入包名（如 `requests`、`scikit-learn`）点击安装
- **卸载包**：在已安装列表中点击删除
- **查看已安装包**：列表展示版本号

**安全限制**：
- 包名不允许包含 `;|&$\`` 等注入字符
- pip 操作仅在你的 venv 内执行，不影响其他用户
- 持久化：安装的包记录到 `users/{你的用户名}/venv/requirements.txt`，重启/Docker 重建时自动还原

### 6.4 收件箱

**路径**：服务 → 收件箱（侧栏菜单显示未读数量徽章）

Service Agent 调用 `contact_admin` 后，反馈先保存为持久 case，同时记录所属管理员、Service、原消费者会话与渠道。Service 定时 Agent 也可通过该工具请求管理员处理；接收反馈本身不启动管理员 Agent。

**查看与处理**：
- 列表与详情自动刷新，可查看原反馈、关联会话、回复及逐条投递状态。
- 阅读状态与业务状态独立。打开详情可以标记已读；确认受理、已创建回复、已解决分别记录，收到微信提醒不会自动标记已读或已处理。
- 在详情填写回复后，服务端向原消费者会话排队投递，不接受另换 Service、会话或微信用户。“已回复”表示回复已创建，不代表对方已读或微信已送达。
- 网页/API 消息写入原会话历史；微信来源另有独立的微信投递记录。应分别查看两者，历史已保存不等于微信已确认。

**管理员微信提醒与明确回复**：

每条新反馈都会排队提醒已绑定的管理员微信，正文包含反馈与 case 编号，不使用模型筛选或改写。尚未连接或未建立发送上下文时保留待重试，反馈仍可从网页查看。提醒会给出可复制的命令：

```text
回复 inbox_反馈编号：回复正文
```

该文字命令在模型之前解析，核对微信发送人和 case 所属管理员，并以微信真实消息编号去重。确认只表示“已记录并排队”；不承诺原消费者已经收到。普通微信聊天继续使用原管理员 Agent。

投递状态包括排队中、投递中、等待重试、渠道已确认、已取消和结果未知。结果未知可能已经发送，系统不会自动重发；人工重试前须明确确认可能重复。停用服务、删除会话或绑定变化会阻止后续投递，不会静默换收件人。

旧 inbox JSON 会幂等迁移，旧“已处理”只保留为已受理，不推断为已回复或已解决；缺少可靠回执的历史消息不会补发微信提醒。无法验证旧微信原绑定时拒绝回复，不能猜测收件人。当前管理员回复仅支持文字。详见 [Service 消息](service-messaging.md)。

### 6.5 环境与通用设置

**路径**：环境 → 模型与连接（Agent 引擎、模型和 API/CLI 凭据）；设置 → 通用设置（界面、时间和高级选项）。

#### 6.5.1 API Keys（强烈推荐）

每个 Admin 可配置自己的 API Key，**优先级高于环境变量**。AES-256-GCM 加密存储。

| 类型 | 字段 |
|---|---|
| Anthropic | `anthropic_api_key` + `anthropic_base_url` |
| OpenAI | `openai_api_key` + `openai_base_url` |
| AWS Bedrock | `bedrock_api_key` + `bedrock_region` |
| Kimi（Moonshot） | `kimi_api_key` + `kimi_base_url` |
| MiniMax | `minimax_api_key` + `minimax_group_id` |
| Tavily | `tavily_api_key` |
| CloudsWay | `cloudsway_search_key` |
| 多媒体（按需） | `image_*` / `tts_*` / `video_*` / `s2s_*` / `stt_*` 的 key + base_url |

**操作**：
- **编辑**：点击折叠面板展开，输入 Key
- **测试连接**：验证连通性
- **保存**：自动加密存储 + 清除 Agent 缓存（下次请求生效）

> 如果你既未配置 per-admin Key 也未在 `.env` 中配置环境变量，登录后会弹出引导 Modal 提示设置。

#### 6.5.2 时区设置

- 设置默认时区（影响：定时任务的 cron 表达式解释、聊天消息时间戳注入）
- 时区改动会同步到 `users/{uid}/preferences.json`

#### 6.5.3 主题选择

详细的主题切换 + 预览（dark / cyber-ocean / terminal）。

#### 6.5.4 高级功能开关

控制 **设置 → Prompt** 页内 Advanced Tab 的可见性：
- **操作规则** Switch：是否显示 System Prompt 编辑器 + 能力提示词
- **Memory & Soul** Switch：是否显示 Soul 配置页

> 默认关闭。新手不需要看到，避免误操作。

#### 6.5.5 YOLO 模式

见 §4.8。在通用页开启后，Admin 对话 HITL 自动批准。

#### 6.5.6 数据备份

**路径**：设置 → 数据备份

- 按模块勾选导出 ZIP（文件系统 / 对话 / Service / 定时任务 / 设置 / API Keys）
- 支持「包含媒体」「导出 API Keys（明文，ZIP 需自行保密）」
- 导入支持 **合并**（跳过已存在）或 **覆盖**（先快照到 `users/{uid}.pre-restore-{ts}/`，需密码确认）
- ZIP 内含 manifest `_openjellyfish_backup.json`（兼容旧版 `_jellyfishbot_backup.json`）

#### 6.5.7 用量统计

**路径**：设置 → 用量统计

查看本账号 LLM Token 用量（按 Agent Core / 模型 / 供应商 / Service / API Key / 渠道 / 日期拆分）。DeepAgents 和 Service 调用来自 `users/{uid}/llm_usage/` 按月 JSONL；管理员 Codex / Cursor 运行由持久 Runtime 记录补入。选择月份按自然月计算。CLI 未完整上报 Token 的运行会单独提示，汇总数字只包含已记录的用量，可能低于实际消耗。DeepAgents 的“调用次数”按模型调用计，CLI 按运行轮次计。

#### 6.5.8 语音前台（LiveKit）

**路径**：`/settings/voice`（导航入口暂时隐藏）

配置实时语音通话的前台 Agent（问候语、路由策略、填充语、STT/TTS/LLM 供应商等）。详见 §10.2。需部署方提供 LiveKit 与语音 Worker 环境。

#### 6.5.9 批量运行（内嵌 BatchRunner）

入口暂时隐藏；以下为保留的能力说明。

通过 Excel 文件批量执行 Agent 任务，适合数据处理、批量分析等场景。

**使用流程**：

1. **上传 Excel**：选择包含任务数据的 Excel 文件（`.xlsx`）
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

> 旧链接 `/settings/batch` 会自动重定向到 `/settings/general`。

### 6.6 Service 管理

**路径**：服务 → 我的服务

将配置好的 Agent 发布为 Service，供外部消费者（Consumer）通过网页、API 或微信扫码使用。

#### 6.6.1 界面布局

- **左侧 (30%)**：Service 列表卡片
  - 绿色圆点 = 已发布
  - 灰色圆点 = 草稿
  - 选中时左侧显示品牌色高亮条
- **右侧 (70%)**：选中 Service 的详情，包含基础配置、API Keys、微信渠道、测试、使用情况与广播等功能区

#### 6.6.2 Basic Info（基础信息）

| 字段 | 说明 |
|---|---|
| **名称** | Service 显示名称 |
| **描述** | Service 说明文本（简介） |
| **模型** | 选择 Service 使用的 AI 模型 |
| **System Prompt** | 可选：使用某个保存的 Prompt 版本，或自定义 |
| **User Profile** | 可选：使用某个用户画像版本（注入到 prompt） |
| **能力** | 勾选 Service 开放的能力：`web` / `scheduler` / `image` / `speech` / `video` / `humanchat` |
| **可访问的文档/脚本** | 可视化勾选树（来自 `/docs/` 和 `/scripts/`），文件夹勾选 = 整个目录 |
| **欢迎语** | 聊天页首屏的渐变大字（最多 300 字） |
| **快速问题** | 首屏 chips 横排（每条最多 80 字，最多 6 条） |
| **发布状态** | Switch 切换 发布/草稿 |

#### 6.6.3 API Keys

每个 Service 可创建多把 `sk-svc-...` 格式的 API Key 供 Consumer 使用。

| 操作 | 说明 |
|---|---|
| **生成 Key** | 创建新的 API Key（**只在创建那一刻能看到完整 Key**，关闭后只能看到哈希） |
| **附 Key 链接** | 创建后 Modal 额外显示 `/s/{service_id}?key=sk-svc-xxx` 一键访问链接 |
| **复制 Key** | 一键复制 Key 到剪贴板 |
| **删除 Key** | 撤销某个 Key |

> ⚠️ **附 Key 链接等同分享 Key**。链接虽然在用户访问时立即从 URL 中清除并写入 localStorage，但 referer/网关访问日志中可能留痕。

#### 6.6.4 WeChat Channel（微信渠道）

详见 §7。

| 字段 | 说明 |
|---|---|
| **启用/禁用** | Switch 开关微信扫码渠道 |
| **过期时间** | 设置渠道有效期 |
| **最大会话数** | 限制同时连接的微信用户数 |
| **QR 链接** | 获取微信扫码中间页链接（`/wc/{service_id}`） |
| **活跃会话** | 查看当前连接的微信用户列表 |
| **断开会话** | 主动断开某个微信用户 |
| **查看对话** | 进入某个微信用户的对话历史 |

#### 6.6.5 Test（内联测试）

无需离开管理页面，直接与 Service 进行测试对话。自动创建临时 API Key 和测试对话。

> ⚠️ 已知问题：每次新测试会创建新的 API Key 但不会自动清理（孤立 Key），需手动在 API Keys Tab 删除。

#### 6.6.6 使用情况

Service 详情右侧 **使用情况** 模块，Segmented 切换：

| 视图 | 内容 |
|---|---|
| **会话** | Consumer 对话列表（来源 web/api/wechat）、消息数、查看 / 删除 |
| **调用** | 请求级 access log（时间、endpoint、Key、状态、耗时），可按渠道过滤 |
| **用量** | 该 Service 的 Token 统计（复用用量页 V3 可视化，按渠道/模型/日期等） |

#### 6.6.7 消费者聊天页增强

独立聊天页 `/s/{service_id}`（React 版）支持：

- **多会话抽屉**：本浏览器 `localStorage` 记住会话列表，刷新不丢；切换时从后端拉历史
- **生成文件面板**：header「📁 文件」列出本会话 `generated/`，带短期签名 token 下载/预览
- 欢迎语 + 快速问题 chips（在 Service Basic Info 配置）
- **后台消息自动刷新**：管理员回复和直接通知写入原会话后自动补拉；切回页面时补拉遗漏消息，按稳定消息 ID 去重

#### 6.6.8 广播与直接通知

在 Service 的广播功能中，先区分两种动作：

- **直接通知**：管理员填写明确正文，勾选当前 Service 已有的消费者会话，选择立即发送或指定时间。无需模型执行，按原网页/API/微信渠道建立逐对象投递记录，不会默认向所有人发送。
- **Service Agent 任务**：需要模型读资料或完成工作时进入定时任务，使用受限执行授权。当前 Codex / Cursor Service 不支持这类定时 Agent 任务；直接文字通知可独立使用。

通知创建后可查看每个会话的投递状态。取消只影响尚未开始的排队或待重试项，已经在途的微信请求不能承诺撤回。等待重试可再次排队；结果未知须确认可能重复，已取消项不会被批量重试复活。指定时间是开始投递的时间，不保证该时刻所有微信用户都已收到。

网页会自动读取后台消息。API 集成方需使用 [增量消息接口](#839-增量后台消息)，聊天请求的 SSE 不会持续订阅之后的管理员回复或通知。当前直接通知与管理员回复仅支持文字，不包含通用 webhook。

---

### 6.7 定时任务

**路径**：左侧「定时任务」

管理定时执行的自动化任务，分为 **管理员任务** 和 **服务任务** 两个 Tab。任务定义采用文件树存储（`tasks/{root_id}/{child_id}/_meta.json`），调度由最小堆和事件唤醒驱动；运行、授权和投递意图另有 SQLite 持久记录，不能仅凭任务文件判断已执行或已送达。

#### 6.7.0 界面视图

| 视图 | 说明 |
|---|---|
| **列表** | 侧栏**仅显示 root 任务**；spawn 子任务带缩进标记与 `+N` 子计数 |
| **谱系图** | reactflow 谱系图，点击节点切换选中任务 |
| **时间轴** | 24h / 7d / 全部 三档 swim-lane |

Agent 任务可指定**执行模型**（留空 = 创建时 Agent 当前模型）；`spawn_child_task` 可派生子任务（默认继承 reply_to / 权限 / 时区，每小时链上限可配）。

#### 6.7.1 管理员任务

**任务类型**：
- **Script**：可保存脚本任务配置；当前持久执行路径在接入操作系统级隔离适配前拒绝原生脚本执行
- **Agent**：定时执行 Agent 任务（发送 Prompt + 可选文档上下文）

**调度类型**：
- **once**：一次性执行（指定 ISO 时间，建议带时区后缀，如 `2026-12-31T09:00:00+08:00`）
- **cron**：Cron 表达式定时（如 `0 9 * * *` 每天 9 点）
- **interval**：间隔执行（秒数）

**脚本权限配置**（仅 Script 任务；保存配置不代表已具备隔离执行适配）：
- 可配置脚本读/写目录（默认 `docs/scripts/generated/tasks`）
- 路径相对用户文件系统根目录

**reply_to 选项**：
- ☐ **不推送**：仅记录运行结果
- ☐ **推送到我的微信**：通过已绑定的 Admin WeChat 投递（需先在 §6.8 接入）

#### 6.7.2 服务任务

由 Admin 给某个 Service 派发，或由 Consumer 在具备调度能力的对话中通过 `schedule_task` 创建。

- 仅支持 **Agent** 类型，保留发布的 Service Prompt、用户画像和文档范围，在本次保存的执行授权下运行。
- 使用受限任务工具，不能继承完整聊天 Agent 的工具权限。当前不提供脚本、CLI、联网和媒体生成适配；不支持的能力会明确拒绝，不能按日常聊天能力推断任务可执行。
- `reply_to` 固定消费者会话及渠道；`send_message` 只创建本次运行提交后的文字投递意图。普通模型说明、任务异常不会作为兜底消息自动发给消费者。

| Channel | 目标 | 适用场景 |
|---|---|---|
| `web` | 原消费者会话历史 | 网页展示或 API 读取 |
| `wechat` | 原消费者会话与绑定微信用户 | Service 微信渠道用户 |

需要联系管理员时调用 `contact_admin`，另行创建收件箱反馈；它不是 `reply_to` 的目标渠道。Service 的 `manage_scheduled_tasks` 仅能操作当前 conversation 的任务。Service 任务列表显示 Service、推送标记与目标信息；任务运行状态和消息投递状态应分别查看。

#### 6.7.3 运行记录

每次执行记录详细的步骤日志：

| 步骤 | 含义 |
|---|---|
| `start` | 开始执行 |
| `docs_loaded` | 文档加载完成（仅 Agent） |
| `loop` | Agent 循环迭代 |
| `tool_call` / `tool_result` | 工具调用与结果 |
| `ai_message` | AI 消息 |
| `wechat_warning` / `wechat_error` | 微信投递警告/错误 |
| `finish` | 完成 |
| `error` | 错误 |

可以查看每次运行的耗时、错误信息、完整步骤及相关投递记录。运行完成不表示渠道已确认；没有 `send_message` 意图的 Service Agent 运行不会自动产生消费者消息。

#### 6.7.4 立即执行

每个任务列表行右侧的 **运行** 按钮可立即触发一次执行（不影响下次定时）。

### 6.8 微信接入（管理员自接入）

**路径**：设置 → 微信接入

将 **管理员自己的主 Agent** 通过微信 iLink 协议接入，实现在微信中直接与你的 OpenJellyfish 对话。

> 这与 [§7 Service 渠道](#7-service-渠道让-consumer-通过微信扫码使用) 是两套**完全独立**的栈：Admin 接入用的是你的主 Agent（完整权限），Service 渠道服务于 Consumer（受限权限）。

#### 6.8.1 接入流程

1. 进入 **设置 → 微信接入**，点击 **生成二维码**。
2. 用微信扫描 QR 码（即 iLink Bot 协议的扫码登录）。
3. 扫码成功后：
   - 状态自动变为 **已连接**
   - 你在微信中给 OpenJellyfish 发消息，机器人会回复
4. **首次扫码后**，你的微信账号绑定关系会被持久化到 `users/{你的用户名}/admin_wechat_session.json`，Docker / 服务重启后自动恢复连接。
5. 主动断开：点击 **断开连接** 按钮，或微信端取消授权。

#### 6.8.2 多模态支持

- **接收图片**：CDN 下载 → AES 解密 → 自动作为多模态消息发给 GPT-4o / Claude（Vision 能力）
- **接收语音**：CDN 下载 → AES 解密 → SILK→WAV → Whisper 转文字
- **发送图片/视频**：自动通过 `<<FILE:>>` 标签触发（图片走 iLink CDN 上传，视频/MP3 走文件附件）
- **发送 TTS 语音**：以文件附件形式发送（语音条暂不可用）

#### 6.8.3 管理界面

- **状态卡片**：显示当前连接状态、绑定的微信用户标识
- **消息列表**：查看微信对话记录（可在 `/api/admin/wechat/messages` 接口拉取）
- **断开按钮**：主动断开当前连接

#### 6.8.4 主要场景

| 场景 | 操作 |
|---|---|
| 出门时用微信问 Agent 简单问题 | 直接在微信发消息 |
| 接收定时任务推送 | 在 §6.7 任务的 reply_to 选「推送到我的微信」 |
| 接收 Service 反馈 | 每条新反馈持久排队提醒，未连微信时保留待重试（见 §6.4） |
| 回复原消费者 | 发送 `回复 inbox_反馈编号：正文`，核对身份后按原绑定排队投递 |
| 让管理员 Agent 回复指定 Service 用户 | 在管理员对话或个人微信中给出 Service 与会话；Agent 通过 `send_service_message` 将完整回复按原绑定排队投递。收件箱反馈还可附 `inbox_id`；CLI Core 使用 `jellyfish_send_service_message`。入队不等于用户已收到。 |
| 给 Service 消费者派发任务 | 通过 `publish_service_task` 明确目标；运行产生的 `send_message` 意图按任务绑定投递 |

---

## 7. Service 渠道：让 Consumer 通过微信扫码使用

> 这是与 [§6.8 管理员自接入](#68-微信接入管理员自接入) **完全独立**的另一套 WeChat 栈，专为 Consumer 服务。

### 7.1 启用流程

1. 在 **服务 → 我的服务** 选中（或创建）一个 Service
2. 切换到 **WeChat Channel** 标签页
3. 配置：
   - 启用 Switch
   - 过期时间（渠道有效期，过期后拒绝新接入、消息处理与后续投递）
   - 最大会话数（同时连接的微信用户上限）
4. 点击 **复制 QR 链接** 或直接打开 `/wc/{service_id}` 中间页

### 7.2 Consumer 扫码流程

1. Consumer 用微信扫描你分享的 `/wc/{service_id}` 中间页 QR
2. 中间页提示用户在微信中关注 iLink Bot 并发送任意消息
3. 扫码确认时，后端重新检查 Service 和渠道准入，创建绑定的独立消费者会话
4. 用户首次消息建立微信接收人与发送上下文
5. 后续消息再次检查会话绑定与渠道状态，通过 Consumer Agent 处理并由 iLink 回复

### 7.3 Consumer 体验

- 用户发送的问题通过 Service 配置的 Consumer Agent 处理；明确的管理员回复与直接通知由投递 worker 发送，不再调用模型
- Agent 仅有 Service `capabilities` + `allowed_docs/scripts` 的权限
- 普通聊天的多模态路径支持收/发图片、语音；新增管理员回复与直接通知当前仅支持文字
- 友好工具状态：Agent 调用工具时显示「思考中…」或白名单友好文案，不暴露真实工具名

### 7.4 会话管理

在 Service 管理 → **WeChat Channel** Tab 可以：
- **查看活跃会话**：每个微信用户的最近活动时间、消息数
- **断开会话**：主动断开某个用户
- **查看对话**：进入某个微信用户的对话历史

### 7.5 频率限制

- 单用户：10 条消息 / 60 秒
- QR 生成：5 次 / 60 秒
- 全局：Service 配置的 `max_sessions` 上限

### 7.6 注意事项

- iLink Bot（国内）网络要求**直连，不走代理**
- 多 Admin 隔离：每个 Service 的 sessions 不会跨 Admin 越权
- 消息会话**长期保留**（有 `from_user_id` 的会话不参与 24h 无活动清理）
- 取消发布、渠道停用或过期会阻止后续处理与投递；已开始的外部请求不能承诺撤回
- 重新扫码不会把旧反馈、旧回复或旧通知静默转交新绑定

---

## 8. Consumer 使用（外部 API 集成）

### 8.1 概述

Consumer 层面向外部用户和系统，通过 **Service API Key** 认证访问 AI Agent 能力。Base URL 与 Key **由部署方提供**，勿使用文档中的示例域名或密钥。

完整接入说明见 **[CONSUMER_API_INTEGRATION.md](CONSUMER_API_INTEGRATION.md)**（含环境变量 `JF_BASE_URL` / `JF_API_KEY` 示例）。

### 8.2 认证方式

所有 Consumer API 请求需在 HTTP Header 中携带：

```
Authorization: Bearer sk-svc-<由部署方提供>
```

### 8.3 API 接口

> 将 `https://<你的部署域名>` 替换为部署方给你的实例地址。

#### 8.3.1 创建对话

```bash
curl -X POST "https://<你的部署域名>/api/v1/conversations" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>" \
  -H "Content-Type: application/json" \
  -d '{"title": "新对话"}'
```

#### 8.3.2 发送消息（自定义 SSE）

```bash
curl -X POST "https://<你的部署域名>/api/v1/chat" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"conversation_id": "conv-id", "message": "你好"}'
```

SSE 事件类型与 Admin 端一致：`token` / `thinking` / `tool_call` / `tool_result` / `done` / `error` 等（详见 [开发指南 §5.5](DEVELOPER_GUIDE.md#55-sse-流式处理)）。

#### 8.3.3 OpenAI 兼容接口

```bash
curl -X POST "https://<你的部署域名>/api/v1/chat/completions" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

完全兼容 OpenAI API 格式，可直接替换 `base_url` 在已有的 OpenAI SDK 代码中使用。响应 `usage` 字段可能为 0；真实 Token 统计见 Admin **设置 → 用量统计**。

#### 8.3.4 媒体 Token（生成文件访问）

```bash
curl "https://<你的部署域名>/api/v1/conversations/{conv_id}/media-token" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>"
```

返回短期 `token`，供 `<img src>` / 下载链接使用（`?token=` 查询参数，无需 Authorization 头）。

#### 8.3.5 多模态消息

Consumer 支持发送图片 + 文字：

```json
{
  "conversation_id": "conv-id",
  "message": [
    {"type": "text", "text": "这张图片是什么？"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ]
}
```

GPT-4o / Claude Sonnet/Opus 均原生支持。

#### 8.3.6 获取对话历史

```bash
curl "https://<你的部署域名>/api/v1/conversations/conv-id" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>"
```

#### 8.3.7 列出生成文件

```bash
curl "https://<你的部署域名>/api/v1/conversations/conv-id/files" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>"

# 下载（query 参数携带 token 或 key，支持 <img src>）
GET /api/v1/conversations/conv-id/files/images/xxx.png?token=<media-token>
```

#### 8.3.8 上传附件

```bash
# 用户附件存储到 query_appendix/
GET /api/v1/conversations/conv-id/attachments/images/abc.jpg
```

#### 8.3.9 增量后台消息

```bash
curl "https://<你的部署域名>/api/v1/conversations/conv-id/events?after=0&limit=100" \
  -H "Authorization: Bearer sk-svc-<由部署方提供>"
```

返回 `{events, next_cursor, has_more}`。每个事件包含稳定的 `id`、递增的 `seq`、`type: "message"` 和 `message`，用于读取已写入会话的管理员回复与直接通知。

- 初次可从 `after=0` 开始；处理成功后保存 `next_cursor`，下次放入 `after`。`has_more=true` 时继续补拉。
- 按事件 `id`（等于消息 ID）去重；完整历史使用相同的 `event_id`，避免历史加载与补拉重复显示。
- 当前聊天 POST 的 SSE 只服务该次请求，不能代替后台消息轮询；OpenAI 兼容客户端需自行接入此扩展接口，当前没有通用 webhook。
- 授权沿用当前 Service Key，能访问该 Service 范围；它不是逐消费者登录或隐私隔离凭据。微信发送状态需要由管理员侧投递记录查询，事件可见不代表微信已送达。

### 8.4 独立聊天页

每个 Service 可通过以下 URL 访问独立的聊天网页：

```
https://<你的部署域名>/s/{service_id}
https://<你的部署域名>/s/{service_id}?key=sk-svc-<由部署方提供>   # 一键访问（等同分享 Key）
```

页面是 React 应用（Vite multi-entry），由 FastAPI 注入 Service 配置 + key 后渲染。Key 写入 localStorage 后会立即从 URL 中清除。

### 8.5 send_message 工具行为

如果你的 Service 启用了 `humanchat` capability：

- **Web 渠道**（`/api/v1/*` 或 `/s/{sid}`）：**不会**注入 `send_message` 工具，因为 Agent 输出已直接流给浏览器
- **WeChat 渠道**（Service 微信扫码）：注入 `send_message`，Agent 调用时由后端拦截并通过 iLink 投递到对应微信用户
- **Scheduler 渠道**（定时 Agent 任务）：`send_message` 在执行授权内生成文字投递意图，运行提交后再向固定的 `web` / `wechat` 目标投递；不直接调用微信，也不自动发送普通模型输出

#### 8.5.1 媒体自动发送（`<<FILE:>>` 标签）

以下只适用于已接入媒体适配的普通微信聊天；定时 Agent 的持久投递、管理员回复和直接通知当前仅支持文字。普通聊天 Agent 在 `send_message` 文本中输出 `<<FILE:/generated/images/xxx.png>>` 标签时，后端会**自动**：
1. 从文本中抽取标签
2. 把对应文件单独发送（图片/视频/语音/PDF）
3. 把抽取后的纯文本作为消息发出

这是为了和聊天页 markdown 渲染统一约定。无需 Consumer 端做任何处理。

---

### Service Key 计费：hosted / BYOK

计费方式按 API Key 指定，同一个 Service 可同时发放运营方付费（`hosted`）和调用方自付（`byok`）Key，旧 Key 默认保持 hosted。使用 Service Key 调用 `GET /api/v1/models` 可查看计费模式与允许的模型 / 主机。BYOK 聊天请求提供 `provider`、`api_key`、`model`，必要时加 `base_url` / `region`，并通过供应商、模型和端点校验；hosted Key 拒绝调用方凭据字段，保留服务原有模型配置行为。上述 hosted / BYOK 说明适用于 DeepAgents Service。Codex / Cursor 套餐 Service 使用超管授权的共享登录连接，连接和模型由管理员固定，仅允许 hosted 套餐 Key，拒绝 BYOK 及调用方凭据。

## 9. Soul 记忆系统

> Soul 是 OpenJellyfish 让 Agent 长期"成长"的核心机制，灵感来自给 Agent 一个"灵魂"，记住用户偏好和对话精华。

### 9.1 概念

```
┌────────────────────────────────────────────┐
│  Soul（灵魂）                              │
│                                            │
│  📁 filesystem/soul/    ← Agent 可读写笔记 │
│     ├── about_user.md  （关于用户）         │
│     ├── preferences.md （偏好）             │
│     ├── insights.md    （观察）             │
│     └── ...            （任意笔记）         │
│                                            │
│  📁 soul/                                  │
│     └── config.json    （应用层配置）       │
└────────────────────────────────────────────┘
```

### 9.2 三种使用模式

#### 模式 1：纯短期记忆（默认）

- 默认开启 `memory_enabled`
- 调度 Agent 任务按设置注入最近对话历史，默认 5 条；收件箱提醒使用持久反馈正文，不调用模型
- 不需要任何配置

#### 模式 2：Memory Subagent 主动写入

- 在 **设置 → Prompt → Memory & Soul**（需先在通用页开启高级功能）开启 **Memory Subagent 写入**
- 主 Agent 在合适时机会调用 Memory Subagent，让它阅读对话历史并总结写入 `filesystem/soul/`
- 适合：你希望 Agent 自主决定记什么、怎么记

#### 模式 3：Soul 文件系统直接暴露

- 同上路径开启 **Soul 文件系统**
- 主 Agent 直接获得 `/soul/` 目录的读写权限（在文件面板和工具中可见）
- 适合：你希望 Agent 在每次回复中都能引用 Soul、或 Agent 需要在对话中实时更新人格笔记

### 9.3 自定义能力提示词

Memory & Soul Tab 中两个 Switch 下方各带可编辑的能力提示词，告诉 Agent 怎么使用这些能力。可恢复默认。

### 9.4 包含消费者对话

开启 **包含消费者对话** Switch 后：
- Memory Subagent 不仅能读 Admin 自己的对话，也能读 Service Consumer 的对话
- 适合：你想让 Agent 学习用户在不同 Service 中的反馈

### 9.5 文件管理

启用 Soul 文件系统后：
- 文件面板显示 `/soul/` 目录
- 可手动编辑（如手写一些初始的"用户档案"）
- Agent 也可读写

> 旧版本 `users/{uid}/soul/` 内容会自动迁移到 `users/{uid}/filesystem/soul/`，无需手动处理。

---

## 10. 语音交互

### 10.1 语音输入（聊天框）

- 单击麦克风按钮开始录音
- 再次单击停止 → 自动转写为文字填入输入框
- 录音中按 **Esc** 取消（不发送）
- 转写依赖 `STT_API_KEY`（默认走 OpenAI Whisper，可在 per-admin API Keys 覆盖）

### 10.2 实时语音通话（LiveKit）

**路径**：`/settings/voice`（导航入口暂时隐藏）

通过 **LiveKit WebRTC** 与前台 Agent 实时对话；复杂任务由前台 LLM 委派给 OpenJellyfish 主 Agent（与文字对话共享 `thread_id` 与对话历史）。

**使用前**：部署方需配置 LiveKit Server、语音 Worker 插件，以及 `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` / `VOICE_BRIDGE_SECRET` 等环境变量。

**调音台可配置**：
- 问候语、系统提示、路由策略、打断策略
- STT：OpenAI / Fish Audio / 阿里云 Paraformer
- 前台 LLM：与 Chat 模型表一致（OpenAI / Anthropic / Kimi / MiniMax / Bedrock，经 Core 内置 OpenAI 兼容网关）
- TTS：OpenAI / Fish Audio / 阿里云 CosyVoice

在调音台点击 **试通话** 或（若已接入 Chat 入口）发起通话即可。

### 10.3 实时语音对话（S2S WebSocket，旧路径）

- 通过 WebSocket 直连 OpenAI Realtime API（`/api/voice/ws`）
- 适合第三方客户端或脚本集成；React 主界面以 LiveKit（§10.2）为主

---

## 11. 环境变量配置

> 提示：从 v2.x 起，所有 API Key 都可以在 **环境 → 模型与连接 → API Keys** 中按用户配置（AES-256-GCM 加密存储），优先级高于环境变量。环境变量主要用于初次部署或 fallback。

### 11.1 必需配置（至少一个）

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API 密钥（Claude 系列） |
| `OPENAI_API_KEY` | OpenAI API 密钥（GPT + 多媒体生成） |

也可配置 `BEDROCK_API_KEY` / `BEDROCK_REGION`、`OPENROUTER_API_KEY` 或 `SILICONFLOW_API_KEY`。硅基流动与 OpenRouter 模型需在设置页白名单中启用，可通过 `OPENROUTER_BASE_URL` / `SILICONFLOW_BASE_URL` 覆盖端点；示例见 `.env.example`。这些 API 凭据与 Codex / Cursor 供应商登录相互独立。

### 11.2 Provider 端点覆盖

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_BASE_URL` | 自定义 Anthropic API 端点 |
| `OPENAI_BASE_URL` | 自定义 OpenAI API 端点 |

### 11.3 按能力覆盖

当某项能力需要使用与主 Provider 不同的 API Key 或端点时：

| 能力 | Key 变量 | URL 变量 |
|------|----------|----------|
| 图片生成 | `IMAGE_API_KEY` | `IMAGE_BASE_URL` |
| TTS 语音 | `TTS_API_KEY` | `TTS_BASE_URL` |
| 视频生成 | `VIDEO_API_KEY` | `VIDEO_BASE_URL` |
| 实时语音 S2S | `S2S_API_KEY` | `S2S_BASE_URL` |
| 语音转写 STT | `STT_API_KEY` | `STT_BASE_URL` |

### 11.4 联网工具

| 变量 | 说明 |
|------|------|
| `CLOUDSWAY_SEARCH_KEY` | CloudsWay 搜索 API（优先使用） |
| `CLOUDSWAY_READ_URL` | CloudsWay 网页抓取端点（可选覆盖） |
| `CLOUDSWAY_SEARCH_URL` | CloudsWay 搜索端点（可选覆盖） |
| `TAVILY_API_KEY` | Tavily 搜索 API（备选） |

### 11.5 S3 存储（可选）

| 变量 | 说明 |
|------|------|
| `STORAGE_BACKEND` | `local`（默认）或 `s3` |
| `S3_BUCKET` | S3 桶名称 |
| `S3_REGION` | S3 区域 |
| `S3_ENDPOINT_URL` | 自定义端点（MinIO/R2/OSS） |
| `S3_ACCESS_KEY_ID` | S3 访问密钥 |
| `S3_SECRET_ACCESS_KEY` | S3 秘密密钥 |
| `S3_PREFIX` | S3 key 前缀（可选） |

> 注意：JSON 配置文件（users.json、conversations 等）目前仍走本地盘。S3 模式仅托管文件系统层（`docs/`、`scripts/`、`generated/`、`soul/`）。

Cloudflare R2 的 `S3_ENDPOINT_URL` 使用 `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`。region 留空时 R2 自动使用 `auto`，其余使用 `us-east-1`；`S3_CHECKSUM_MODE=auto` 对自定义端点使用 `when_required`，需要路径寻址的兼容服务可配置 `S3_ADDRESSING_STYLE=path`。浏览器直接 fetch 签名 URL 时需配置桶 CORS，完整 R2 示例见 `.env.example`。

### 11.6 加密 Master Key

| 变量 | 说明 |
|------|------|
| `ENCRYPTION_KEY` | per-admin API Key 的 AES-256-GCM master key（不设则首次启动自动生成 `data/encryption.key`） |

> 生产环境强烈建议显式设置 `ENCRYPTION_KEY` 并妥善备份，否则 master key 文件丢失将导致所有用户的 API Key 无法解密。

### 11.7 脚本沙箱调优

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SCRIPT_CONCURRENCY` | 4 | 全局并发脚本数 |
| `SCRIPT_QUEUE_TIMEOUT` | 180 | 排队超时（秒） |

### 11.8 端口配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `BACKEND_PORT` | 8000 | FastAPI 端口（Docker 用） |
| `FRONTEND_PORT` | 3000 | Express/Vite 端口 |
| `API_TARGET` | `http://localhost:8000` | Express 代理目标 |

### 11.9 可观测性

| 变量 | 说明 |
|------|------|
| `LANGFUSE_SECRET_KEY` | Langfuse 密钥 |
| `LANGFUSE_PUBLIC_KEY` | Langfuse 公钥 |
| `LANGFUSE_HOST` | Langfuse 服务地址 |
| `LANGCHAIN_TRACING_V2` | 启用 LangSmith |
| `LANGCHAIN_API_KEY` | LangSmith API Key |

---

### 11.10 可选供应商运行引擎

以 `.env.example` 为配置入口。设置 `JELLYFISH_RUNTIME_ENABLED=1`、`JELLYFISH_RUNTIME_ACCESS_MODE=trusted_shared`、`JELLYFISH_RUNTIME_BACKEND=local`、`UVICORN_WORKERS=1`，要求 macOS / Linux，CLI 安装在后端执行环境内。Docker 可分别配置固定构建参数 `JELLYFISH_CODEX_CLI_VERSION=0.154.0`、`JELLYFISH_CURSOR_CLI_VERSION=2026.09.15-d2fe57e`，重建应用容器后重新加载 Nginx。这些版本有安装验证，不代表完整生产验收。

部署方通过 `python3 launcher.py --superadmin-key` 查看主机 key（Docker 用 `docker compose exec openjellyfish python launcher.py --superadmin-key`），在 `/superadmin` 输入后登录供应商并向已有 admin 授权模型。`JELLYFISH_OWNER_USER_ID` 已废弃；供应商登录与 Jellyfish API Key 相互独立。

运行数据默认位于 `data/runtime`，Docker 通过 `/app/data` 持久化。备份时同时保留数据库、凭据 vault 和加密密钥。主机管理 key 默认位于本机 `config/superadmin.key` 或 Docker `/app/data/superadmin.key`；自定义 `JELLYFISH_SUPERADMIN_KEY_FILE` 时必须给后端与 key CLI 提供相同的进程环境变量。local 后端只面向可信团队；把整套应用放进 Docker 不等于每租户容器隔离。

完整步骤见 [部署与登录](https://github.com/LiUshin/OpenJellyfish/blob/main/docs/superadmin-console.md)、[能力边界与恢复](https://github.com/LiUshin/OpenJellyfish/blob/main/docs/runtime-standard-mode.md)。

## 12. 常见问题（FAQ）

### Q: 注册时提示"注册码无效"？

A: 确认 `config/registration_keys.json` 文件存在且包含有效注册码。
- 命令行：`python generate_keys.py` 生成新注册码
- 桌面 App：进入「注册码管理」页生成
- 每个注册码只能使用一次

### Q: 前端无法连接后端？

A: 确认：
1. 后端 FastAPI 已在 `:8000` 启动（访问 <http://localhost:8000/docs> 验证）
2. 前端开发服务器 Vite 在 `:3000` 启动（自动代理 `/api` → `:8000`）
3. 如使用 Docker，检查 `docker compose logs -f` 确认两个服务都已就绪
4. 如使用桌面 App，进入「关于 / 工具」打开日志目录排查

### Q: 模型列表为空？

A: 至少需要配置一个有效的 API Key：
- `ANTHROPIC_API_KEY`：启用 Claude 系列模型
- `OPENAI_API_KEY`：启用 GPT 系列模型 + 多媒体生成能力

或在 **环境 → 模型与连接 → API Keys** 中配置自己的 Key（推荐）。

### Q: 联网搜索不可用？

A: Admin Agent 默认启用联网工具，但需要配置搜索 API：
- `CLOUDSWAY_SEARCH_KEY`（推荐，优先使用）
- `TAVILY_API_KEY`（备选）

可在 **环境 → 模型与连接 → API Keys** 中配置。

### Q: 图片/语音/视频生成失败？

A: 确认已配置 `OPENAI_API_KEY`。如果需要使用不同的 API Key 或端点，可通过 `IMAGE_API_KEY` / `TTS_API_KEY` / `VIDEO_API_KEY` 分别覆盖（环境变量或 per-admin 设置均可）。

### Q: 微信扫码后无法收到消息？

A: 参考以下检查清单：
1. 确认 iLink Bot 服务端正常（扫码后应看到状态变为"已连接"）
2. 检查后端日志中 `wechat.*` 相关日志（需要 `LOG_LEVEL=INFO`）
3. iLink（国内）需要**直连，不走代理**
4. 检查微信 session 是否过期（`context_token` 失效），重新扫码
5. Admin 微信和 Service 微信是两套独立栈，不要混淆

### Q: 微信中看到字面 `<<FILE:...>>` 字符串？

A: 应该已被 `delivery.py::extract_media_tags` 自动解析。如果仍出现：
1. 检查文件路径是否真实存在
2. 查看后端日志是否报媒体下载失败
3. 确认是不是非 `send_message` 工具调用产生的文本（如 `web_search` 结果）

### Q: 脚本执行失败 / "队列繁忙"？

A: 脚本在沙箱中执行，注意以下限制：
- 不能导入危险模块（subprocess、pathlib、ctypes、io、pickle、threading 等）
- 不能使用 exec、eval、getattr、setattr 等危险内置函数
- 文件路径使用相对路径（`../docs/file.csv`，不是 `/docs/file.csv`），脚本的 cwd 是 `scripts/` 目录
- 内存限制 1024 MB，进程数限制 256
- 全局并发 4 个脚本（可通过 `SCRIPT_CONCURRENCY` 调整）
- 排队超时 180 秒（可通过 `SCRIPT_QUEUE_TIMEOUT` 调整）

### Q: 定时任务不触发？

A:
1. 检查任务的 `next_run_at`（详情 / 谱系图节点状态）
2. 确认时区：**环境 → 模型与连接 → 时区**；创建任务时会记录 `tz_offset_hours`
3. cron 按你设置的时区解释；once 建议带时区后缀（如 `2026-12-31T09:00:00+08:00`）
4. v2 使用堆驱动调度，到期约 1 秒内触发；侧栏只列 root，子任务在谱系图查看
5. 若服务 OOM，可设 `DISABLE_SCHEDULER=1` 或调低 `SCHEDULER_MAX_CONCURRENT`（见开发指南）

### Q: 对话排队 / 中断不生效？

A: 队列与 **↵ 立刻执行** 仅 Admin 对话页；需当前会话正在流式输出且非 HITL 等待。中断会保留 partial 回复并同连接续跑。

### Q: 工作区锁导致 Agent 无法写文件？

A: 查看 chat 顶栏锁面板：是否有其他进程（定时任务 / 另一对话）占用同路径。可手动释放锁（不中止 Agent）或等进程结束自动释放。

### Q: 如何备份用户数据？

A: 推荐 **设置 → 数据备份** 按模块导出 ZIP（见 §6.5.6）。也可手动备份：
- **本地**：`users/` + `data/`（含 checkpoints.db、encryption.key）
- **Docker**：`./data/users/` volume
- **S3**：对象存储 + 本地 `users/{uid}/` 配置目录
- **重要**：备份 `ENCRYPTION_KEY` 或 `data/encryption.key`，否则 API Key 无法解密
- **运行与消息记录**：还需包含 `users/.scheduler/executions.sqlite3`；按用户模块导出的 ZIP 不能代替该数据库的备份。停机后备份，或使用 SQLite 一致性备份，不能在写入期间只复制主数据库文件而遗漏 WAL。详见 [Service 消息](service-messaging.md)。

### Q: 收件箱有消息但没有自动转发到我的微信？

A:
1. 确认已通过 **设置 → 微信接入** 连接管理员微信，并至少发过一条消息建立发送上下文
2. 在收件箱详情查看管理员提醒的投递状态与原因；未连接时保持等待重试，连接变更不会静默换目标
3. `unknown` 表示发送结果未知，可能已送达；系统不会自动重发，人工确认后才能重试。`cancelled` 表示该投递已取消或被拒绝
4. 确认部署未设置 `DISABLE_SERVICE_MESSAGING=1` 或 `SAFE_STARTUP`；只关闭调度器不影响消息 worker
5. 迁移的旧反馈不会自动补发历史提醒。每条新反馈直接排队提醒，不再由 Inbox Agent 筛选，也没有 Inbox Agent 运行日志

### Q: Tauri 桌面 App 在 Windows 启动报 `Cannot load native module 'Crypto.Util._cpuid_c'`？

A: 这是已修复的 Windows `\\?\` 扩展长路径前缀 bug。请：
1. 升级到最新版桌面 App
2. 如果是自构建，确认 `lib.rs` 中包含 `strip_win_extended_prefix()` helper
3. 详见 [开发指南 §12.5.1](DEVELOPER_GUIDE.md#1251-windows--扩展长路径前缀2026-04-20-关键-bug)

### Q: per-admin API Key 设置后是否安全？

A:
- AES-256-GCM 加密存储在 `users/{uid}/api_keys.json`
- master key 在 `data/encryption.key`（或 `ENCRYPTION_KEY` 环境变量覆盖）
- 前端只读取脱敏版本（如 `sk-...abc123`）
- 通过 HTTPS 传输（生产环境务必启用 SSL）
- **严格保护 master key 文件**：丢失后所有 user API Key 都无法解密

### Q: 我能让多个 Admin 共用一份 API Key 吗？

A: 不能。每个 Admin 独立配置 `users/{uid}/api_keys.json`。如果想统一管理，可以在环境变量中配置全局默认 key，让所有未单独配置的用户 fallback 使用。

### Q: Service 数据如何隔离？

A: 严格隔离：
- 每个 Service 在 `users/{admin_id}/services/{service_id}/` 下
- 每个 Consumer 对话在 `users/{admin_id}/services/{svc_id}/conversations/{conv_id}/`
- Consumer 生成的文件在自己对话的 `generated/` 目录
- Consumer 看不到 Admin 的文件系统、其他 Service、其他 Consumer 的数据

### Q: 主题切换后部分组件颜色不对？

A: 大部分组件已迁移到 `--jf-*` CSS 变量。如果发现遗漏，请到 `frontend/src/styles/themes.css` 检查变量定义，或将硬编码颜色改为 `var(--jf-primary)` 等。详见 [开发指南 §5.9](DEVELOPER_GUIDE.md#59-设计系统与多主题)。

### Q: 如何查看后台日志？

A:
- **桌面 App**：「关于 / 工具」→「日志目录」，按天滚动的 log 文件
- **命令行**：终端直接看 stdout，或 `python launcher.py` 启动的子进程也会写到 `logs/{name}-YYYYMMDD.log`
- **Docker**：`docker compose logs -f openjellyfish`

### Q: Docker 部署后端反复重启，日志报 `sqlite3.OperationalError: unable to open database file`？

A: **数据目录权限问题**。容器以 `jellyfish` (uid=1000) 运行，但宿主机 `./data` 目录的 owner 是 root，容器内进程没法写入 `checkpoints.db`。

**解决**（在项目根目录执行）：

```bash
mkdir -p ./data/users
sudo chown -R 1000:1000 ./data
docker compose restart openjellyfish
```

**验证**：

```bash
ls -ld ./data ./data/users
# 期望 owner 是 1000:1000
```

> 顺便说明：FastAPI lifespan 在多 router 场景下会嵌套 `merged_lifespan`，所以你看到的 traceback 会有一长串重复的 `routing.py:216` 帧，这是正常现象。**真正的错误只看最底下两行**（异常类型 + 消息）。

### Q: Docker 部署后域名打不开，但服务器本地 `curl http://localhost` 能通？

A: 排查顺序：

1. **DNS**：`nslookup yourdomain.com` 是否解析到你的服务器 IP；Cloudflare 控制台确认 A 记录是 **Proxied（橙色云朵）**。
2. **防火墙 / 安全组**：80 端口对外是否放行（云厂商控制台的安全组、`ufw status`）。
3. **Cloudflare SSL 模式**：必须设为 **Flexible**（边缘 HTTPS、回源 HTTP）。如果错误地选了 Full / Full(Strict) 但服务器只有 80 端口，会出现无限 301 或 525 错误。
4. **nginx 是否真在 80**：`docker compose ps` 看 `openjellyfish-nginx` 是否 Up，`0.0.0.0:80->80/tcp`。
5. **流式输出"一坨一坨"出来**：项目内置 nginx 已经为 `/api/chat` 关闭了 `proxy_buffering`；如果服务器前面还有一层反代（自建 nginx / Caddy / Traefik），那一层也要关 buffering 并支持 WebSocket Upgrade，否则 SSE 会被缓冲。

---
