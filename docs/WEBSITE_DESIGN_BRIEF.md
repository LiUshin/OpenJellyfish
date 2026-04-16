# JellyfishBot 官网设计需求文档

> 本文档作为 JellyfishBot 官网的设计需求说明，供设计师使用。
> 设计完成后将交由开发实现。

---

## 零、竞品参考分析

### OpenClaw (openclaw.ai) — 值得借鉴的做法

| 做法 | 说明 | 我们是否采用 |
|------|------|:---:|
| 一句话标语极简 | "The AI that actually does things." | ✅ 借鉴 |
| 吉祥物人格化 | 龙虾 Molty，有性格有灵魂 | ✅ 我们有水母 |
| 社会认证铺满首页 | 大量真实用户推文 | ⚠️ 先留占位，后续收集 |
| Quick Start 代码块 | 一行命令装好 | ✅ Docker 一行起 |
| "Works With Everything" 集成矩阵 | 展示可连接的服务图标 | ✅ 改为"触手可达"矩阵 |
| 功能用动词不用名词 | "Runs on Your Machine" 而非 "Self-hosted" | ✅ 借鉴语气 |
| 轻 Landing Page | 不堆砌功能列表，让人"感受到" | ✅ 减少 Section 数量 |

### 与 OpenClaw 的核心差异（这是我们的定位基础）

| 维度 | OpenClaw | JellyfishBot |
|------|----------|-------------|
| 关系 | 1人 ↔ 1 AI | 多 Admin，每个 Admin → 多 Service → 多用户 |
| 场景 | 个人助手 | 个人/团队/部门 AI 服务运营（多人各自独立运营） |
| 分发 | 不关心分发 | **核心就是分发**（API / 微信 / 网页） |
| 渠道 | WhatsApp / Telegram | **微信**（iLink Bot 协议） |
| 架构 | 本机 CLI + 插件 | 文件驱动 Web 平台 + Agent 引擎 |
| 自我进化 | 用户手动写 skill | **Admin Agent 通过对话改写自己的 Service** |
| 数据 | 本机文件 + 记忆 | **结构化文件系统** — 文档/脚本/生成物分离，可换 S3 |
| 体量 | 需要一台 Mac/PC 常开 | 同样一台笔记本，多个 Admin 各自运营，同时服务多人 |

### Dify (dify.ai) — 参考但要区别

Dify 定位 "Agentic Workflow Builder"，面向企业，强调 visual drag-and-drop、RAG pipeline。太"正经"了，跟我们不是一个路子。我们更像 OpenClaw 的**分发版**——有 OpenClaw 的亲切感和"runs on your machine"的精神，但加上了 Service 分发和微信触达的超能力。

---

## 一、产品哲学与定位

### 1.1 水母隐喻（核心品牌叙事）

JellyfishBot 的产品设计暗合水母（Jellyfish）的生物学特征：

**a. 身体与触手**
- **一台机器 = 一片海域**：一台服务器/笔记本上可以有**多只水母**（多个 Admin），各自独立漂浮
- **每只水母的身体 = 一个 Admin**：核心大脑，感知一切，做出决策
- **触手 = Service**：每只水母伸出无数触手，每条触手独立接触外界（微信用户、API 调用者）
- Admin 对每条触手有感知（收件箱、运行记录），也能收回触手（停止 Service）
- 水母之间互不干扰——每个 Admin 的文件系统、对话、Service 完全隔离

**b. 腔肠动物的进食方式**
- 水母吃和拉是同一个口
- JellyfishBot 也一样：Admin Agent 通过**对话**输入知识（文档/脚本），也通过**同一个对话界面**组织和改写 Service 配置，然后再输出给 Consumer
- 文件系统既是输入也是输出：`docs/` 进去 → Agent 处理 → `generated/` 出来 → 可以被下一个 Service 引用
- **自循环迭代**：Admin Agent 自己就能运营整个系统

**c. 简单但有效的生物**
- 水母没有心脏、没有大脑、没有骨骼——但已经存活了 5 亿年
- JellyfishBot 没有复杂的数据库、没有消息队列、没有微服务——文件系统驱动一切
- 简单到一台不关机的笔记本就能跑，但已经能服务多人

### 1.2 一句话定位

**JellyfishBot — 你的 AI 触手，触达每一个人。**

副标语：`一台机器，多只水母，无数触手。文件驱动，对话运营，微信分发。`

### 1.3 核心卖点（重新组织）

不再用"企业级功能列表"的方式，而是围绕**水母哲学**和**用户真实场景**组织：

#### 触手 1：跑在你的机器上，数据只属于你

> "你的 AI，你的文件，你的规则。"

- 一台笔记本 / 一台云服务器 / 一个树莓派
- Docker 一行命令启动完整平台
- 所有数据就是文件夹里的 JSON 和文件
- 重启不丢数据，`cp -r` 就是备份
- 不需要数据库运维，不需要消息队列
- 想用 S3？改一个环境变量

#### 触手 2：对话即运营

> "不写代码，不开后台，跟你的 Agent 聊天就能配置一切。"

- Admin Agent 拥有完整的文件系统读写权限
- 上传文档 → 对话微调 Prompt → Agent 自己改写配置 → 发布为 Service
- 定时任务？跟 Agent 说"每天早上 9 点跑一下这个分析"
- 想修改 Service 的行为？跟 Admin Agent 对话就行，它自己重写 Prompt 和脚本
- **进出同口**：信息进来，处理完，直接分发出去

#### 触手 3：多只水母，无数触手

> "一片海域里可以有很多只水母，每只各自漂浮，各有触手。"

- 一台机器上多个 Admin 各自独立运营（注册码制，互不干扰）
- 每个 Admin 配置好 Agent → 发布为 Service → 生成 API Key 或微信二维码
- 每条触手（Service）独立工作，各有各的文件空间和对话记录
- Admin 通过收件箱感知所有触手的状态
- 触手可以联网搜索、生成图片/语音/视频、执行脚本、安排定时任务
- 能力不同的触手做不同的事——一个做客服，一个做数据分析，一个做内容生成
- 一个部门一只水母，一个人一只水母，各管各的

#### 触手 4：微信扫一扫，AI 直达用户

> "最短的路径：Admin 配置 → 微信二维码 → 用户扫码对话。"

- 基于 iLink Bot 协议的微信原生集成
- 不是"对接 API"，是**扫码就能用**
- 图片、语音、文字双向互通
- 定时任务结果自动推送到用户微信
- Admin 自己也可以微信扫码接入自己的主 Agent

#### 触手 5：文件驱动，所见即所得

> "没有神秘的数据库，打开文件夹你就看到一切。"

- 对话历史 = JSON 文件
- 用户文档 = 文件夹里的文件
- Service 配置 = config.json
- API Key = keys.json（哈希后的）
- 定时任务 = tasks/ 目录下的 JSON
- Admin 文件系统和 Service 文件系统隔离但可控
- 迁移 = 复制文件夹；备份 = 压缩文件夹

#### 触手 6：安全沙箱，触手不会失控

> "让 Agent 执行代码，但划好边界。"

- 双层安全：编译时 AST 检查 + 运行时文件权限沙箱
- 每个 Service 对话的文件空间完全隔离
- 文件操作需要人工审批（HITL），diff 预览修改内容
- Plan Mode 让 Agent 先说想做什么，你同意了才做

### 1.4 与竞品的差异化表达

不要做功能对比表（太"正经"了），用场景故事代替：

> **用 Dify？** 你需要拖拽 workflow、配置 RAG pipeline、部署到 K8s。
> **用 OpenClaw？** 你有了一个超强的个人助手，但它只服务你自己。
> **用 JellyfishBot？** 一台机器上每个人都有自己的水母。跟它聊天就能配置服务，微信扫码就能分发给用户。部门里每人一只，各管各的。

---

## 二、品牌视觉规范

### 2.1 品牌名称与吉祥物

**JellyfishBot** — 像素水母

Logo 已有素材：`frontend/public/media_resources/jellyfishlogo.png`

水母应当是有**性格**的（参考 OpenClaw 给龙虾赋予灵魂）：
- 安静但强大
- 柔软但有边界（沙箱！）
- 半透明（流式全透明）
- 发光（品牌色光晕）
- 漂浮在深色海洋中

### 2.2 色彩体系

| Token | 色值 | 用途 |
|-------|------|------|
| **Primary** | `#E89FD9` | 水母粉紫（主色） |
| **Secondary** | `#8B7FD9` | 深海蓝紫 |
| **Accent** | `#5FC9E6` | 生物荧光青 |
| **Highlight** | `#FF8FCC` | 触须亮粉 |
| **Warning** | `#FFB86C` | 警告橙 |
| **Error** | `#FF6B9D` | 危险粉 |
| **Base BG** | `#0f0f13` | 深海黑 |
| **Container BG** | `#16161d` | 中层海水 |
| **Card BG** | `#1c1c27` | 浅层海水 |
| **Text Primary** | `#e4e4ed` | 主文字 |
| **Text Secondary** | `#9494a8` | 次文字 |
| **Border** | `#2a2a3a` | 边框 |

**整体意象**：深海 + 生物荧光 + 柔和赛博朋克

### 2.3 字体

- **标题**：Space Grotesk（科技感 + 几何感）
- **正文**：Inter / Segoe UI / system-ui
- **代码**：JetBrains Mono

### 2.4 视觉风格

- 暗色深海背景
- 水母 Logo 带呼吸动画 + 荧光光晕
- 触手元素可作为装饰线条/连接线（替代传统的箭头/连线）
- 微粒子浮动（像深海浮游生物）
- 玻璃拟态卡片（像水母的透明身体）

---

## 三、官网页面结构

### 3.1 页面清单

| 页面 | 路径 | 优先级 |
|------|------|--------|
| 首页 | `/` | P0 |
| 文档 | `/docs` | P0 |
| 源码 | `/sourcecode`| P0 |
| 关于 | `/about` | P2 |

**注意**：不需要单独的 `/features` 页面。功能通过首页的触手隐喻和场景故事传达，不要做功能清单。

### 3.2 首页（Landing Page）

#### Section 1: Hero

**布局**：全屏高度，居中

**核心元素**：
- 水母 Logo（大尺寸，居中偏上，呼吸动画 + 荧光光晕 + 触须微摆）
- 深海背景（粒子浮动 + 暗色渐变）

**文案**：

```
JellyfishBot

你的 AI 触手，触达每一个人。

一台机器，多只水母，无数触手。
文件驱动，对话运营，微信分发。

[开始使用]  [GitHub]
```

**CTA 按钮**：
- 主按钮 `开始使用` → 文档页 Quick Start（渐变背景 Primary → Secondary）
- 次按钮 `GitHub ★` → 仓库链接（描边按钮，可加 Star 数量）

**视觉要点**：
- 标语 "你的 AI 触手，触达每一个人" 使用渐变文字（Primary → Accent）
- 下方副标语更小更淡
- 整体感觉是**安静的深海中一只发光的水母**，不是 SaaS 企业首页的感觉

#### Section 2: 三句话说明白

**布局**：三个并排的短句/图标块（参考 OpenClaw 的 "What It Does"）

**不要用功能名词**，用**你能做什么**的句子：

```
🖥️ 跑在你的电脑上
笔记本、云服务器、树莓派。
Docker 一行命令。数据只属于你。

💬 对话就是运营
跟 Admin Agent 聊天配置一切。
上传文档、微调 Prompt、发布 Service。
不写代码，不开后台。

🐙 多只水母，无数触手
一台机器上，每人一只水母。
每只水母伸出自己的触手：API、微信、网页。
互不干扰，各自运营。
```

**视觉**：
- 三列玻璃拟态卡片
- 每张卡片顶部一个简洁图标
- 深海背景延续

#### Section 3: "它能做什么"（场景驱动，不是功能列表）

**布局**：左右交替的场景块（图文并排）

**灵感来源**：不要像 Dify 那样列 "RAG Pipeline | Workflow Builder | Agent"，而是像 OpenClaw 那样说人话。

**场景 A：微信 AI 客服**

```
"上传产品文档，扫码分发，客户直接在微信问 AI。"

配置 Service → 开启微信渠道 → 生成二维码
客户扫码 → 在微信里直接跟你的 Agent 对话
图片、语音、文字全支持
```

配图：手机微信对话截图 mockup

**场景 B：每天自动跑分析**

```
"跟 Agent 说一句话，每天早上 9 点自动分析数据，结果发到你微信。"

Admin: "每天早上分析 docs/sales.csv，发到我微信"
Agent 自动创建定时任务 → 每天执行 → 结果推送微信
```

配图：定时任务 + 微信推送 mockup

**场景 C：Agent 自己改写自己**

```
"你的 Agent 可以修改自己的 Prompt、重写自己的脚本、迭代自己的服务。"

Admin Agent 有完整的文件读写权限
对话 → 修改 Prompt → 更新 Service → 下一轮对话验证效果
进出同口，自我迭代
```

配图：对话中 Agent 修改文件的流式截图

**场景 D：OpenAI 兼容 API**

```python
# 换个 base_url，你的 Agent 就是一个 API
from openai import OpenAI
client = OpenAI(
    api_key="sk-svc-your-service-key",
    base_url="http://your-server/api/v1"
)
response = client.chat.completions.create(
    model="default",
    messages=[{"role": "user", "content": "分析一下最近的市场趋势"}]
)
```

配图：终端/代码编辑器 mockup

#### Section 4: 水母架构（替代传统架构图）

**布局**：居中，视觉化

**用水母的身体结构来展示架构**，而不是方框+箭头：

```
    🪼 水母 A (Admin 甲)              🪼 水母 B (Admin 乙)
   ┌─────────────────┐              ┌─────────────────┐
   │  Agent + 文件    │              │  Agent + 文件    │
   └────────┬────────┘              └────────┬────────┘
            │                                │
   ┌────────┼────────┐              ┌────────┼────────┐
   │        │        │              │        │        │
 触手1    触手2    触手3          触手1    触手2
[微信客服][数据分析][API]        [内容生成][微信顾问]
   ↓        ↓       ↓              ↓        ↓
 微信用户  定时推送  开发者        API调用  微信用户

         ──── 同一台机器 / 同一片海域 ────
```

**视觉**：
- 水母身体用品牌色半透明玻璃拟态
- 触手用品牌色渐变线条，带微动效（缓慢飘动）
- 每条触手末端是一个场景图标
- **不画方框**，用有机的曲线

#### Section 5: Quick Start

**布局**：终端风格代码块（参考 OpenClaw 的 Quick Start）

```bash
# 克隆 & 配置
git clone https://github.com/LiUshin/semi-deep-agent.git
cd semi-deep-agent && cp .env.example .env

# 填入你的 API Key
echo "ANTHROPIC_API_KEY=sk-ant-xxx" >> .env

# 一行启动
docker compose up -d --build

# 打开浏览器，开始使用
# → http://localhost
```

**附注文案**：

```
也可以不用 Docker：
pip install → uvicorn → npm run dev → 三分钟跑起来

跑在一台不关机的笔记本上就行。
重启不丢数据，cp -r 就是备份。
```

**视觉**：
- 深色终端背景
- 命令行带语法高亮
- 步骤旁边可选品牌色数字标记

#### Section 6: 技术基石（简洁横排）

**布局**：水平排列的技术图标

```
Python · FastAPI · React · TypeScript · LangGraph · Docker · 微信 iLink
```

**视觉**：灰度图标，悬浮变品牌色

#### Section 7: Footer

- 水母 Logo + JellyfishBot
- 链接：文档 / GitHub / 定价
- "Built for those who want AI that actually serves others."
- Copyright

---

## 四、交互与动效

### 4.1 核心动效

| 动效 | 位置 | 描述 |
|------|------|------|
| **水母呼吸** | Hero Logo | scale 0.97 ↔ 1.03，周期 4s，ease-in-out |
| **触须飘动** | Hero + 架构图 | 轻微的左右摆动 + 透明度变化 |
| **荧光光晕** | Hero 背景 | Primary → Secondary 渐变光晕，缓慢脉冲 |
| **深海粒子** | 全页背景 | 微小亮点缓慢上浮（像气泡/浮游生物） |
| **卡片浮现** | 各 Section | 滚动进入视口时淡入 + 微上浮 |
| **触手连线** | 架构图 | 从水母身体到触手末端的光点流动 |
| **代码打字** | Quick Start | 终端打字机效果 |

### 4.2 响应式

| 断点 | 调整 |
|------|------|
| ≥1200px | 标准 |
| 768-1199px | 三列变两列，场景图文改为上下 |
| <768px | 单列，Hero 居中缩小 |

### 4.3 性能

- 首屏 < 2s
- 动效 GPU 加速
- 图片 WebP + 懒加载
- 粒子效果用 Canvas，不用 DOM

---

## 五、文案语气指南

### 要

- **说人话**："跑在你的电脑上" 而非 "支持自托管部署"
- **用动词**："扫码就能用" 而非 "微信集成"
- **轻松但不随意**：有深度，但不学术
- **水母意象**：触手、荧光、深海、透明、安静但强大
- **强调"你控制一切"**：你的数据、你的机器、你的规则

### 不要

- 不说"企业级"、"生产环境"、"大规模"
- 不堆砌功能列表
- 不用 "AI Agent Platform" 这种通用描述
- 不做 ❌✅ 对比表（太 SaaS 了）
- 不假装很大——我们的优势就是小而美、文件驱动、触手可达

### 参考语气

OpenClaw 用的是**极客社区的真实声音**（推文）来建立信任。我们暂时没有用户推文，但可以：
- 预留社会认证区域
- 用**场景故事**代替推文
- 标语和描述的口吻参考 OpenClaw 的亲切感

---

## 六、技术实现建议

### 推荐栈

- **框架**：Astro（SSG，极快）或 Next.js（SSR/SSG 混合）
- **样式**：Tailwind CSS + 自定义品牌色
- **动效**：Framer Motion（React）或 CSS 原生动画
- **粒子背景**：Canvas 自绘 或 tsParticles（轻量配置）
- **图标**：Phosphor Icons（与产品一致）
- **部署**：Vercel / Cloudflare Pages
- **字体**：Google Fonts — Space Grotesk + Inter + JetBrains Mono

### SEO

- 标题：`JellyfishBot — 你的 AI 触手，触达每一个人`
- 描述：`文件驱动的 AI Agent 平台。一台笔记本，对话运营，微信分发。开源、自托管、零运维。`
- OG 图：深海背景 + 水母 Logo + 标语

---

## 七、素材清单

### 需要制作

| 素材 | 说明 |
|------|------|
| Hero 水母动画 | 放大版 Logo + 触须 + 光晕，可以是 CSS/SVG 动画 |
| 深海背景 | 渐变 + 粒子/气泡 |
| 水母架构图 | 有机曲线的架构可视化（SVG） |
| 微信对话 mockup | 手机端截图框 |
| 产品界面 mockup | 浏览器框内的产品截图（对话/Service管理） |
| OG 图 | 1200×630，深海背景 + 水母 + 标语 |
| Favicon | 水母 Logo 32×32 / 180×180 |

### 已有

| 素材 | 路径 |
|------|------|
| 水母 Logo（像素风） | `frontend/public/media_resources/jellyfishlogo.png` |
| 产品实际界面 | 运行应用后截取 |
| 品牌色 Token | 见 Section 二 |

---

## 八、交付要求

1. **设计稿**：Figma，Desktop + Mobile
2. **组件系统**：按钮、卡片、代码块、导航
3. **动效标注**：timing / easing / 触发条件
4. **标注**：间距、字号、色值

---

## 九、核心哲学总结（给设计师的一段话）

> JellyfishBot 不是一个"AI SaaS 平台"。它是一片海域里的水母群。
>
> 想象在深海中，几只发着柔和荧光的水母安静地漂浮。每只水母是一个 Admin——拥有完整的智慧和感知。每只水母的触手是 Service——伸向不同的方向，接触不同的用户。一台机器就是一片海域，每个注册的人都有自己的水母，互不干扰。
>
> 这些水母有一个特别的能力：进食和排泄用同一个口。文件进来，被消化处理，再输出为新的服务。Admin Agent 通过对话吃进知识，再通过同一个对话界面吐出配置好的 Service。不需要后台，不需要 dashboard，聊天就是运营。
>
> 它们不需要复杂的心脏（数据库）或骨骼（微服务架构）。一个文件夹就是一只水母的整个世界。但这已经够用了——够一个人、一个团队、一个部门，各自把 AI 能力通过微信扫码分发给所有需要的人。
>
> 设计这个网站时，请让访客**感受到深海的安静和水母的优雅**。不要喧嚣的 SaaS 首页，不要功能堆砌。让他们看到安静发光的水母群，然后被其中一条触手轻轻抓住。

 JellyfishBot 官网交付级设计文档1. 核心视觉特征 (The "Deep Sea" Glow)背景层 (The Abyss)：禁止纯黑。使用 #0f0f13 到 #16161d 的极深蓝紫径向渐变。浮游粒子 (Plankton)：使用 Canvas 渲染约 50 个半透明微小圆点，上下缓慢漂浮，模拟深海有机感。发光逻辑：所有 UI 组件不使用投影（Shadow），统一使用 Outer Glow (Drop Shadow with high spread/blur)。颜色采用 #E89FD9 (20% 透明度)。2. 首页 Section 细节补全Section 1: 英雄区 (The Jellyfish Pulsing)中心组件：像素水母：放置 jellyfishlogo.png。动效：CSS animation: pulse 4s ease-in-out infinite。伴随缩放时，外围增加一层淡粉色光晕扩散效果。文案布局：主标题：Space Grotesk 字体，渐变色 #E89FD9 → #5FC9E6。打字机副标：一台机器，多只水母，无数触手。 循环显示，末尾带一个闪烁的 _ 符号。Section 2: 三大核心能力 (The Trinity Cards)卡片设计：材质：backdrop-filter: blur(12px) 的毛玻璃效果，边框为 1px 的 #2a2a3a。交互：鼠标悬停时，边框颜色变为亮青色 #5FC9E6，卡片内图标做轻微浮动位移。内容补全：Card 1 (本地性)：图标为像素风格的服务器。强调“数据只属于你”。Card 2 (对话性)：图标为对话气泡。强调“对话即运营，进出同口”。Card 3 (分发性)：图标为水母触手延伸出的二维码。强调“微信扫一扫”。Section 3: 场景深度展示 (The Interactive Mockups)场景 A：微信 AI 客服视觉：左侧为 iOS 风格的微信聊天 Mockup。右侧为 Admin 端的 Service 配置 JSON。细节：展示一个用户发送语音，AI 回复文字+图片的动态模拟。场景 B：Admin Agent 进化视觉：展示一个 Split Screen（分屏）。左边是 Admin 说：“帮我写个 Python 脚本每天抓取新闻。”右边代码流：实时滚动展示 scripts/fetch_news.py 文件的生成过程。强调“文件驱动”。3. 核心交互：水母架构图 (The Bioluminescent Network)这是页面的灵魂，替代传统的死板流程图。元素视觉表现交互逻辑Admin Body半透明粉紫圆形容器，内部有流动纹理。点击可展开看到内部的文件结构（docs/, config/）。Tentacles (触手)从 Body 延伸出的曲线，线条末端渐隐。随着鼠标移动做轻微的反向摆动（Parallax 效果）。Nodes (触手末端)分别为：微信图标、API 齿轮、Web 浏览器。悬停在“微信”上时，整条触手变亮，并指向一个手机 Mockup。4. 快速开始区 (The Developer Terminal)设计：采用 JetBrains Mono 字体的深色终端。代码高亮：git clone 使用 #5FC9E6 (青色)。docker compose 使用 #FF8FCC (亮粉)。一键复制：点击代码块右上角的“触手图标”完成复制，并显示 Copied! 气泡。5. 导航与底部 (The Surface & The Trench)Header：全透明，滚动超过 50px 后变为带背景色的毛玻璃。Logo：左上角 32x32 像素水母，鼠标悬停时会转一圈。Footer：背景色深度下沉为最暗的 #0a0a0d。右侧放置 GitHub Star 按钮，样式自定义为水母发光风格。6. 动效标注 (Animation Spec)Entrance：页面所有 Section 在滚动进入视口 20% 时触发，使用 y: 20px, opacity: 0 -> 1 的过渡，持续 0.8s。Floating：所有图标拥有独立的浮动周期（3s 到 6s 不等），避免机械感。Bioluminescence：页面背景偶尔会有微弱的颜色闪烁（从粉紫到青色），周期极长（20s一次），模拟深海生物发光。7. 技术实现建议前端框架：Astro + React。动画库：Framer Motion (用于卡片和文字出现)。绘图库：Rough.js (如果想增加一种“手绘/草图”的极客感，可以用来画触手)。部署：Vercel，配置 Edge Middleware 实现极致的首屏加载。

1. 关于“水母架构图”的交互 (Section 4)
不要只画静态图。建议使用 SVG 路径动画：

流动感：让品牌色的光点在“触手”线条上缓慢流动，象征数据在 Admin 和 Service 之间传递。

悬停反馈：当用户鼠标悬停在某只“水母”上时，该水母微微放光，对应的“文件系统”或“微信图标”产生轻微位移。

2. 强化“对话即运营”的视觉表现 (Section 3)
在展示 Agent 修改配置时，建议做一个对比动效：

左边是 Admin 发出一句语音或文字：“把这个客服 Agent 的回复风格改得更幽默一点。”

右边是一个透明的代码编辑器，展示 config.json 里的 system_prompt 字段被 Agent 实时重写，并带上高亮闪烁。

潜台词：让用户看到，“文件驱动”不是落后，而是透明可控。

3. 针对“微信分发”的信任背书
由于微信集成的特殊性（iLink Bot 协议），用户可能会担心稳定性。

在 Section 3 的微信场景下，加一个小标签：“原生协议支持，稳定不掉线”。

展示一个“微信扫码 -> 立即对话”的 3 步走极简动效。

4. 增加“开发者浪漫”的小细节
深海彩蛋：在页面滚动到最底部（深海最深处）时，可以藏一个很淡的像素小鱼或者沉船，点击跳转到你的 GitHub Profile。

状态指示器：在 Hero 区水母 Logo 旁边，做一个微小的“Live Status”灯，显示当前开源社区的 Star 数或最新版本号，增加真实感。

🛠️ 技术实现补全建议
粒子背景优化：如果用 tsParticles，建议粒子数量不要太多，保持“深海”的空灵感，而不是那种密集的“星空”感。

文字渐变：

CSS
.jellyfish-gradient {
  background: linear-gradient(135deg, #E89FD9 0%, #5FC9E6 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
毛玻璃效果 (Glassmorphism)：

CSS
.jellyfish-card {
  background: rgba(22, 22, 29, 0.7);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(232, 159, 217, 0.1);
}
📝 总结
你的这份 Brief 已经非常成熟，特别是**“水母哲学”**这一部分，是这款产品能否建立粉丝粘性的关键。

一句话建议： 保持那种“安静而强大”的深海氛围，把网站做成一个会发光的艺术品，而不是一个冷冰冰的文档站。