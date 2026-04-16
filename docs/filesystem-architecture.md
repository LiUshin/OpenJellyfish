# 文件系统架构

本文档描述 Admin、Service（Consumer）和 WeChat Bridge 三个层面的文件系统布局、读写权限、沙箱机制和路径约定。

---

## 1. 整体目录结构

### 本地存储（Local）

```
data/users/{user_id}/
├── filesystem/               ← Admin 主文件系统根
│   ├── docs/                 ← 只读文档（对 consumer 也可读）
│   ├── scripts/              ← Python 脚本（admin 可写，consumer 可执行）
│   └── generated/            ← Admin 侧 AI 生成物
│       ├── images/
│       ├── audio/
│       └── videos/
└── services/
    └── {service_id}/
        ├── config.json       ← Service 配置
        ├── keys.json         ← API 密钥
        └── conversations/
            └── {conv_id}/
                ├── messages.json    ← 对话记录
                └── generated/       ← Consumer 侧生成物（隔离）
                    ├── images/
                    ├── audio/
                    └── videos/
```

### S3 存储

| 用途 | Key 模式 |
|------|----------|
| Admin 文件系统 | `{prefix}/{user_id}/fs/{path}` |
| Consumer 生成物 | `{prefix}/{admin_id}/svc/{service_id}/{conv_id}/gen/{path}` |

---

## 2. Admin 文件系统

### 2.1 存储接口

| 操作 | 本地路径 | S3 Key |
|------|----------|--------|
| `read_bytes(uid, "/docs/foo.md")` | `users/{uid}/filesystem/docs/foo.md` | `{prefix}/{uid}/fs/docs/foo.md` |
| `write_bytes(uid, "/generated/images/a.png", data)` | `users/{uid}/filesystem/generated/images/a.png` | `{prefix}/{uid}/fs/generated/images/a.png` |

所有路径通过 `safe_join` 约束在用户根目录内，防止目录遍历攻击。

### 2.2 Admin Agent 工具

| 工具 | 存储行为 | 权限控制 |
|------|----------|----------|
| `write_file` / `edit_file` | 通过 deepagents Backend 写入 `filesystem/` | HITL 中断：需用户 approve/edit/reject |
| `read_file` / `ls` | 通过 deepagents Backend 读取 `filesystem/` | 无限制 |
| `generate_image/speech/video` | `storage.write_bytes(uid, "/generated/...")` | 无中断，直接写入 |
| `run_script` | 执行 `scripts/` 中的脚本，可写 `scripts/` + `generated/` | 沙箱限制（见第 5 节） |
| `send_message` | 不读写存储，返回 JSON `{text, media}` | 仅在 humanchat 模式启用 |

### 2.3 deepagents Backend

```
create_agent_backend(root_dir=filesystem_dir, user_id=uid)
  ├── Local → FilesystemBackend(root_dir, virtual_mode=True)
  └── S3    → S3Backend(bucket, prefix="{prefix}/{uid}/fs")
```

---

## 3. Consumer（Service）文件系统

### 3.1 核心原则

> **Consumer 侧产生的一切新内容，都写到会话目录（`conversations/{conv_id}/generated/`），而非 Admin 主文件系统。Agent 不感知存储位置，tool 实现透明路由。**

### 3.2 存储接口

| 操作 | 本地路径 | S3 Key |
|------|----------|--------|
| `write_consumer_bytes(admin, svc, conv, "images/a.png", data)` | `.../conversations/{conv}/generated/images/a.png` | `{prefix}/{admin}/svc/{svc}/{conv}/gen/images/a.png` |
| `read_consumer_bytes(admin, svc, conv, "images/a.png")` | 同上 | 同上 |

### 3.3 Consumer Agent 工具

| 工具 | 数据来源 | 存储行为 |
|------|----------|----------|
| `ls` / `read_file` | Admin 的 `docs/`（只读） | 受 `allowed_docs` 过滤 |
| `generate_image/speech/video` | 调用 `ai_tools.py` 的 API | 通过 `_consumer_write` → `write_consumer_bytes` 写到会话 generated/ |
| `run_script` | 执行 Admin 的 `scripts/`（只读） | 输出通过 `consumer_script_execution` 写到会话 generated/ |
| `send_message` | 不读写存储 | 返回 JSON，bridge 解释执行 |

### 3.4 `_consumer_write` 路径转换

```
ai_tools 返回 rel_path: "/generated/images/xxx.png"
        ↓ 去掉 "generated/" 前缀
_consumer_write 调用: write_consumer_bytes(admin, svc, conv, "images/xxx.png", data)
        ↓
实际位置: conversations/{conv}/generated/images/xxx.png
```

### 3.5 deepagents Backend

```
create_consumer_backend(admin_id, service_id, conv_id, gen_dir)
  ├── Local → FilesystemBackend(root_dir=gen_dir, virtual_mode=True)
  └── S3    → S3Backend(bucket, prefix="{prefix}/{admin}/svc/{svc}/{conv}/gen")
```

---

## 4. WeChat Bridge 媒体处理

### 4.1 接收微信消息中的媒体

```
微信用户发送图片/语音
  → iLink getupdates 接收
  → _download_images: decrypt → storage.write_consumer_bytes(admin, svc, conv, "/images/{filename}", raw)
  → _transcribe_voices: decrypt → Whisper → 文本
```

接收的媒体直接写入 consumer 会话目录。

### 4.2 发送媒体到微信

```
Agent 调用 send_message(text="...", media_path="/generated/images/xxx.png")
  → bridge 拦截 ToolMessage (name="send_message")
  → _send_media_to_wechat:
      1. media_path 去掉 "generated/" 前缀 → "images/xxx.png"
      2. storage.read_consumer_bytes(admin, svc, conv, "images/xxx.png")
      3. 按扩展名选择: send_image / send_video / send_voice / send_file
```

### 4.3 路径一致性

| 步骤 | 路径 | 方法 |
|------|------|------|
| AI 生成写入 | `images/xxx.png` | `write_consumer_bytes` |
| 微信接收写入 | `images/xxx.png` | `write_consumer_bytes` |
| bridge 读取发送 | `images/xxx.png` | `read_consumer_bytes` |

三者使用相同的相对路径和同一个 consumer 存储接口，确保路径一致。

### 4.4 兜底逻辑

如果 Agent 流式执行过程中从未调用 `send_message` 工具，bridge 会将 Agent 的完整文本输出通过 `client.send_text()` 直接发送给微信用户。

---

## 5. 脚本沙箱

### 5.1 沙箱架构

```
run_script()
  → script_runner.run_script()
    → subprocess: python _sandbox_wrapper.py
        --allowed-read  "dir1|dir2"
        --allowed-write "dir1|dir2"
        --script script.py
```

脚本通过 `_sandbox_wrapper.py` 在子进程中执行，沙箱通过以下机制限制访问：

- **文件路径白名单**：`--allowed-read` / `--allowed-write` 指定可读/可写目录
- **AST 黑名单**：禁止危险的 Python 构造
- **环境变量白名单**：仅暴露安全的环境变量
- **资源限制**（非 Windows）：内存、子进程数量等

### 5.2 Admin 脚本执行

| 目录 | 权限 | 内容 |
|------|------|------|
| `filesystem/scripts/` | 读 + 写 | 脚本自身（也是 cwd） |
| `filesystem/docs/` | 只读 | 参考文档 |
| `filesystem/generated/` | 写 | 脚本输出 |

### 5.3 Consumer 脚本执行

| 目录 | 权限 | 内容 |
|------|------|------|
| Admin `filesystem/scripts/` | 读 + 写 | 脚本来源（共享 admin 的脚本） |
| Admin `filesystem/docs/` | 只读 | 参考文档（共享 admin 的文档） |
| Consumer `conversations/{conv}/generated/` | 写 | 脚本输出（隔离到会话） |

**关键区别**：consumer 脚本的写入目标从 admin 的 `generated/` 切换到会话级的 `generated/`。

### 5.4 S3 模式下的脚本执行

S3 模式下，脚本执行在临时目录中进行：

```
Admin:
  download script → tmp/scripts/
  execute with write_dirs=[tmp/scripts, tmp/generated]
  upload tmp/generated/* → write_bytes(uid, "/generated/{rel}")

Consumer:
  download script → tmp/scripts/ (from admin's S3 key)
  execute with write_dirs=[tmp/scripts, tmp/generated]
  upload tmp/generated/* → write_consumer_bytes(admin, svc, conv, "{rel}")
```

---

## 6. 权限矩阵

### Admin Agent

| 资源 | 读 | 写 | 备注 |
|------|----|----|------|
| `docs/` | ✅ | ✅（需 HITL 审批） | 文档 |
| `scripts/` | ✅ | ✅（需 HITL 审批） | 脚本 |
| `generated/` | ✅ | ✅（AI 工具直写） | 生成物 |

### Consumer Agent

| 资源 | 读 | 写 | 备注 |
|------|----|----|------|
| Admin `docs/` | ✅（受 allowed_docs 过滤） | ❌ | 只读共享 |
| Admin `scripts/` | ✅（执行，受 allowed_scripts 过滤） | ❌ | 只执行不修改 |
| Admin `generated/` | ❌ | ❌ | 完全隔离 |
| 会话 `generated/` | ✅ | ✅ | Consumer 唯一可写区域 |

### WeChat Bridge

| 操作 | 存储接口 | 方向 |
|------|----------|------|
| 接收微信图片 | `write_consumer_bytes` | 微信 → 会话 generated |
| 发送媒体到微信 | `read_consumer_bytes` | 会话 generated → 微信 |
| 发送文本到微信 | `client.send_text` | Agent 输出 → 微信 |

---

## 7. 配置与 Capabilities

### humanchat capability

- 通过微信 router 启用微信渠道时**自动注入** `humanchat` 到 capabilities
- 注入 `send_message` 工具 + HumanChat 系统提示（强制要求用 `send_message` 发送回复）
- 通过通用 service 更新接口修改 capabilities 时，若微信渠道为启用状态，`humanchat` 会被**自动保留**
- 禁用微信渠道时，`humanchat` 会被**自动移除**

### 其他 capabilities

| Capability | 工具 | 存储位置 |
|------------|------|----------|
| `image` | `generate_image` | Admin: `generated/images/` / Consumer: 会话 `generated/images/` |
| `speech` | `generate_speech` | Admin: `generated/audio/` / Consumer: 会话 `generated/audio/` |
| `video` | `generate_video` | Admin: `generated/videos/` / Consumer: 会话 `generated/videos/` |
| `web` | `web_search` / `web_fetch` | 不涉及存储 |
| `scheduler` | `schedule_task` | 任务配置存储在 scheduler 系统 |
