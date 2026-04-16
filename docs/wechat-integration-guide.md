# 微信 iLink Bot 集成实战指南

> 本文档记录了 semi-deep-agent 项目接入腾讯 iLink Bot API（通过微信分发 AI Service）的完整经历，包含架构设计、协议细节、踩坑记录和解决方案。

---

## 1. 项目概述

### 1.1 目标

将 semi-deep-agent 平台已发布的 AI Service 通过微信扫码分发给终端用户，无需微信公众号开发者资质。

### 1.2 最终架构

```
Admin 发布 Service → 启用微信渠道 → 生成 Service QR（稳定 URL）
                                          │
用户扫码 → 中间页 → iLink QR（动态刷新） → 微信内对话
                                          │
                    ┌─────────────────────┘
                    ▼
        WeChat Bridge ←→ Consumer Agent（humanchat 模式）
         ├── 文字消息双向
         ├── 图片消息双向（CDN 下载解密 → multimodal vision / generate_image → send_image）
         └── 语音消息（CDN 下载 → AES 解密 → SILK→WAV → Whisper → 文字）
```

### 1.3 两层二维码架构

| 层级 | 类型 | 生命周期 | 用途 |
|------|------|----------|------|
| Layer 1 | Service QR | Admin 控制（可设过期） | 稳定 URL，可打印/分享 |
| Layer 2 | iLink QR | ~5 分钟自动过期 | 临时，per-visitor，前端自动刷新 |

**为什么需要两层？**  
iLink QR 天然过期且一人一码，Service QR 需要稳定可复用。两层解耦后互不干扰。

---

## 2. iLink Bot 协议详解

### 2.1 基础信息

- **API 基地址**: `https://ilinkai.weixin.qq.com/ilink/bot`
- **认证**: Header `X-WECHAT-UIN: {bot_token}`
- **源码参考**: `@tencent-weixin/openclaw-weixin@1.0.2`（npm 包）
- **Go SDK 参考**: `github.com/openilink/openilink-sdk-go`
- **协议文档**: https://www.wechatbot.dev/zh/protocol

### 2.2 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `get_bot_qrcode` | POST | 生成二维码，返回 `qrcode_img_content`（URL）和 `qr_id` |
| `get_qrcode_status` | POST | 轮询扫码状态，参数为 `qr_id`（不是 URL） |
| `getupdates` | POST | 长轮询获取新消息，通过 `get_updates_buf` 游标分页 |
| `sendmessage` | POST | 发送消息（文字/图片） |
| `getconfig` | POST | 获取配置（需 `ilink_user_id`） |
| `sendtyping` | POST | 发送输入状态（需 `ilink_user_id`） |

### 2.3 sendmessage 正确格式（重要！）

```json
{
  "msg": {
    "from_user_id": "",
    "to_user_id": "xxx@im.wechat",
    "client_id": "openclaw-weixin:1774427815868-44e6cc41",
    "message_type": 2,
    "message_state": 2,
    "context_token": "<从 inbound 消息取>",
    "item_list": [{"type": 1, "text_item": {"text": "回复内容"}}]
  },
  "base_info": {"channel_version": "1.0.2"}
}
```

### 2.4 消息类型常量

| type | 含义 | item 字段 |
|------|------|-----------|
| 1 | 文字 | `text_item.text` |
| 2 | 图片 | `image_item` |
| 3 | 语音 | `voice_item` |

---

## 3. 媒体处理协议（核心踩坑区域）

### 3.1 关键认知：发送 vs 接收完全不同

| 操作 | 方式 | 备注 |
|------|------|------|
| **发送**图片 | `sendmessage` + `image_item` + `cdn_url` | 需要先上传获取 cdn_url |
| **接收**图片 | CDN GET 下载 + AES 解密 | 没有 cdn_url 字段！ |

### 3.2 接收消息中的媒体结构

```json
{
  "image_item": {
    "url": "3057020100044b3049...",       // ← WeChat media ID，不是 CDN URL！
    "aeskey": "542a954e73284480c7ddacf5b1e08fed",  // ← 直接 hex 格式
    "media": {
      "encrypt_query_param": "Q2tPbGJPbUhUUDZ...",  // ← base64，用于 CDN 下载
      "aes_key": "NTQyYTk1NGU3MzI4NDQ4MGM3ZGRhY2Y1YjFlMDhm",  // ← base64(hex字符串)
      "hd_size": 123456,    // ← 纯数字，不是对象
      "mid_size": 45678,
      "thumb_size": 1234
    }
  }
}
```

### 3.3 CDN 下载协议

```
GET https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param={url_encode(value)}
```

**三个致命细节：**

| # | 问题 | 正确做法 |
|---|------|----------|
| 1 | 参数名 | `encrypted_query_param`（有 **d**），不是 `encrypt_query_param` |
| 2 | 参数值 | 必须 URL 编码：`urllib.parse.quote(value, safe="")` |
| 3 | 下载方式 | 直接 GET 请求，不走 iLink API（没有 `getmedia` 端点） |

**错误排查经历：**
1. ❌ 第一次：`POST /ilink/bot/getmedia` → 404（端点不存在）
2. ❌ 第二次：`GET ...?encrypt_query_param=...`（无 URL 编码）→ 400
3. ✅ 第三次：参考 Go SDK `cdn.go`，修正参数名 + URL 编码 → 200

### 3.4 AES 密钥的三种格式

从 iLink 消息中拿到的 AES key 可能是以下任一格式：

| 格式 | 示例 | 解码方法 |
|------|------|----------|
| 直接 hex（32 字符） | `542a954e73284480c7ddacf5b1e08fed` | `bytes.fromhex(key)` → 16 字节 |
| base64(原始 16 字节) | `VCqVTnMoRIDH3az1seCP7Q==` | `b64decode(key)` → 16 字节 |
| base64(hex 字符串) | `NTQyYTk1NGU3MzI4...` | `b64decode(key).decode().fromhex()` → 16 字节 |

**解码策略：** 优先检查字段位置（`media.aes_key` > `image_item.aeskey`），然后按长度和格式自动检测。

```python
def _decode_aes_key(raw: str) -> bytes:
    if len(raw) == 32 and all(c in '0123456789abcdef' for c in raw.lower()):
        return bytes.fromhex(raw)
    decoded = _b64_decode_flexible(raw)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        return bytes.fromhex(decoded.decode("ascii"))
    raise ValueError(f"Cannot decode AES key: len={len(raw)}")
```

### 3.5 AES-128-ECB 解密

```python
from Crypto.Cipher import AES

def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    plaintext = cipher.decrypt(ciphertext)
    return _unpad_pkcs7(plaintext)
```

**注意：** PKCS7 unpad 需要验证 padding 合法性，防止解密后数据损坏。

### 3.6 语音消息：SILK 格式处理

微信语音使用 **SILK 编码**（Skype 开发），且是微信变体：

| 标准 SILK | WeChat SILK |
|-----------|-------------|
| `#!SILK_V3` 开头 | `0x02` + `#!SILK_V3` 开头 |
| `0xFF 0xFF` 结尾 | 无结尾标记 |

**处理流程：**

```
CDN 下载 → AES-ECB 解密 → 去掉 0x02 前缀 → pysilk.decode(24000Hz)
→ PCM 数据 → 手动构建 WAV header → 发送给 Whisper API
```

**依赖：** `pip install silk-python`（PyPI 包名），`import pysilk`（Python 导入名）

```python
def _silk_to_wav(silk_bytes: bytes) -> bytes:
    import io, struct, pysilk

    silk_input = io.BytesIO(silk_bytes)
    if silk_bytes[:1] == b'\x02':
        silk_input = io.BytesIO(silk_bytes[1:])

    pcm_output = io.BytesIO()
    pysilk.decode(silk_input, pcm_output, 24000)
    pcm_data = pcm_output.getvalue()

    # 手动构建 WAV header（16-bit mono PCM, 24kHz）
    wav_buf = io.BytesIO()
    wav_buf.write(b'RIFF')
    wav_buf.write(struct.pack('<I', 36 + len(pcm_data)))
    wav_buf.write(b'WAVEfmt ')
    wav_buf.write(struct.pack('<IHHIIHH', 16, 1, 1, 24000, 48000, 2, 16))
    wav_buf.write(b'data')
    wav_buf.write(struct.pack('<I', len(pcm_data)))
    wav_buf.write(pcm_data)
    return wav_buf.getvalue()
```

### 3.7 图片格式检测

CDN 解密后的图片不带文件扩展名信息，需要通过 magic bytes 检测：

```python
def _detect_image_format(data: bytes) -> str:
    if data[:3] == b'\xff\xd8\xff': return ".jpg"
    if data[:8] == b'\x89PNG\r\n\x1a\n': return ".png"
    if data[:4] == b'GIF8': return ".gif"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return ".webp"
    if data[:2] == b'BM': return ".bmp"
    return ".jpg"  # fallback
```

---

## 4. sendmessage 协议踩坑记录

| # | 坑 | 后果 | 正确做法 |
|---|---|---|---|
| 1 | 缺少顶层 `base_info` | 消息静默丢失（HTTP 200） | 必须包含 `{"channel_version": "1.0.2"}` |
| 2 | `msg.client_id` 不唯一 | 消息不送达 | 格式 `openclaw-weixin:{ts_ms}-{random_hex_8}` |
| 3 | `msg.from_user_id` 填了 bot ID | 消息不送达 | **必须是空字符串 `""`** |
| 4 | `sendmessage` 加了 `ilink_user_id` | 出错 | `ilink_user_id` 仅用于 `getconfig`/`sendtyping` |
| 5 | `SendMessageResp` 返回 `{}` | 误以为失败 | **正常就是空对象** |
| 6 | `qrcode_img_content` 当作 base64 | 显示乱码 | **是扫码 URL**，需用 qrcode 库生成 QR 图 |
| 7 | iLink 走了代理 | ConnectTimeout | 国内服务器**必须直连** `proxy=None` |

---

## 5. 网络分流策略

```python
# iLink → 国内服务器，必须直连
ilink_http = httpx.AsyncClient(
    base_url="https://ilinkai.weixin.qq.com/ilink/bot",
    proxy=None,
    headers={"X-WECHAT-UIN": bot_token}
)

# CDN 下载 → 也是国内，直连
cdn_http = httpx.AsyncClient(proxy=None)

# OpenAI (Whisper/GPT) → SOCKS5 代理（如需要）
openai_client = openai.AsyncOpenAI(http_client=httpx.AsyncClient(proxy=socks5_url))
```

---

## 6. 会话管理

### 6.1 QR 扫码确认去重

iLink API 在扫码确认后会**持续返回 confirmed 状态**，必须用缓存去重：

```python
_confirmed_qrcodes: set[str] = set()

async def handle_qr_status(qr_id, status):
    if status == "confirmed":
        if qr_id in _confirmed_qrcodes:
            return  # 已处理过
        _confirmed_qrcodes.add(qr_id)
        await create_session(...)
```

### 6.2 Session 持久化与恢复

会话信息持久化到 `wechat_sessions.json`，服务重启时恢复轮询：

```python
@app.on_event("startup")
async def startup():
    await session_manager.restore_sessions()
    await session_manager.start_all_polling()
```

### 6.3 会话清理策略

- **指数退避重连**：最多 20 次失败后自动移除
- **24h 无活动清理**：`last_active_at` 超过 24 小时自动断开
- **Admin 手动断开**：`DELETE /api/wc/{service_id}/sessions/{session_id}`

---

## 7. 多模态支持（全链路）

### 7.1 微信 → Agent（图片识别）

```
微信图片消息 → CDN 下载 → AES 解密 → base64 编码
→ OpenAI multimodal content:
  [
    {"type": "text", "text": "用户发送了图片，请描述图片内容"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]
→ GPT-4o / Claude Vision 处理
```

### 7.2 Agent → 微信（图片生成）

```
Agent 调用 generate_image 工具 → 生成图片文件
→ Agent 调用 send_message(media_path="generated/images/xxx.png")
→ Bridge 读取文件 → iLink send_image → 微信用户收到图片
```

### 7.3 Web 端多模态

Admin 和 Consumer Web 聊天也支持图片上传（粘贴/拖拽/按钮），构建 OpenAI compatible multimodal list 发送给 Agent。

---

## 8. 限流与安全

### 8.1 Rate Limiter

| 维度 | 限制 |
|------|------|
| 单用户消息频率 | 10 条/60 秒 |
| QR 生成频率 | 5 次/60 秒 |
| 全局 session 上限 | config `max_sessions` 控制 |

### 8.2 多 Admin 隔离

- `list_sessions` / `remove_session` 按 `admin_id` 过滤
- 不同 Admin 各自管理自己的微信渠道，互不干扰

---

## 9. 文件结构

```
app/channels/wechat/
├── __init__.py
├── client.py            # iLink 协议客户端（多实例，QR/消息/媒体）
├── media.py             # AES-128-ECB 加解密 + PKCS7
├── session_manager.py   # 多用户会话管理 + 持久化 + 自动清理
├── bridge.py            # Service Agent ↔ iLink 消息桥接（含图片/语音）
├── admin_bridge.py      # Admin Agent ↔ iLink 桥接（完整权限）
├── router.py            # Service 微信 API 路由
├── admin_router.py      # Admin 个人微信 API 路由
└── rate_limiter.py      # 限流

app/routes/
└── wechat_ui.py         # 中间页路由 GET /wc/{service_id}

frontend/public/
├── wechat-scan.html     # 微信扫码中间页
└── js/admin-wechat.js   # Admin 微信面板前端逻辑
```

---

## 10. 依赖

```
httpx[socks]>=0.27.0     # HTTP 客户端 + SOCKS5 代理
qrcode>=7.4              # QR 码生成
pycryptodome>=3.20.0     # AES 加解密
silk-python>=0.2.0       # SILK 音频解码（import pysilk）
openai                   # Whisper 语音转文字 + GPT Vision
```

---

## 11. Logging 配置（重要！）

Python `logging` 模块默认 WARNING 级别。如果不在 `main.py` 中配置 `basicConfig`，所有 `wechat.*` 模块的 INFO 日志**都不会输出**，导致调试时完全没有日志。

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
```

---

## 12. 调试建议

1. **先确认 logging 配置**：看不到 `wechat.bridge` / `wechat.ilink` 日志 = 没配 basicConfig
2. **打印原始消息**：`json.dumps(msgs[0], ensure_ascii=False)[:600]` 查看完整字段结构
3. **关注 media 对象**：接收消息的媒体字段和文档/设计稿中预想的**很可能不同**
4. **CDN 返回码**：400 通常是参数名或编码问题，404 是端点不存在
5. **消息静默丢失**：iLink sendmessage 即使格式错误也返回 200，通过 `{}` 空响应无法判断成败——只能看微信端是否收到
6. **QR 状态轮询**：confirmed 会持续返回，必须缓存去重防止创建重复 session
