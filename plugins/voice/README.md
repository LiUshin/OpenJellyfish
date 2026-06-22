# JellyfishBot 语音前台插件(LiveKit)

JellyfishBot 之上的**实时语音上层框架**。与任务引擎解耦:对话进程(本 Worker)
通过远程 SSE 驱动任务引擎(Core),互不干扰。

## 架构

```
浏览器 ──WebRTC──> LiveKit Server(SFU) <──音轨── 语音 Worker(本插件)
                                                      │ 桥接令牌(读自参与者 metadata)
                                                      ▼
                              JellyfishBot Core: /api/voice/live/{session,delegate}
                                                      │
                                                      ▼
                                      JellyfishBot agent(任务引擎,/api/chat 同源)
```

- **前台 Copilot**(快速 LLM):处理寒暄/澄清,需要重活时调用 `delegate_to_jellyfish`。
- **bridge**:把委派转成 Core 的 SSE 流,消费与 `/api/chat` 相同的事件词汇。
- **orchestration**:填充语、进度播报节流、打断记忆、多人沉默判定(不依赖 LiveKit,可移植)。

## 模块

| 文件 | 职责 |
|---|---|
| `agent.py` | AgentServer 入口:加入 room → 读桥接令牌 → 引导 → 装配 STT/LLM/TTS → 启动会话 |
| `config.py` | 凭桥接令牌向 Core 拉取会话上下文(标识/前台配置/供应商凭据) |
| `bridge.py` | 委派 SSE 客户端(Worker → Core `/delegate`) |
| `copilot.py` | 前台 Agent + `delegate_to_jellyfish` 工具 |
| `orchestration.py` | 对话编排策略(可移植) |

## 本地运行

```bash
cd plugins/voice
uv venv && uv pip install -e .          # 或 pip install -e .
cp .env.example .env                     # 填 LIVEKIT_* / VOICE_BRIDGE_SECRET / JELLYFISH_API_BASE
python -m jellyfish_voice.agent dev      # 连本地 LiveKit Server,等待派发
```

> `LIVEKIT_API_KEY/SECRET` 必须与 LiveKit Server 及 Core 三方一致(dev 模式为 `devkey`/`secret`)。
> Worker **不需要** `VOICE_BRIDGE_SECRET`——桥接令牌由 Core 签发+校验,Worker 只透传。

## 与 Core 的契约(窄而稳)

- Core 在 `POST /api/voice/live/token` 时把**桥接令牌**塞进浏览器 LiveKit 令牌的
  `metadata`;Worker 加入后从参与者 metadata 读出。
- Worker 永不接触管理员真实登录 token。
- 委派事件词汇见 `bridge.py` 顶部 docstring(与 `app/routes/chat.py` 一致)。
