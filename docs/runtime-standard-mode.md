# 标准模式：超管连接与可信 admin 共享（PR 00–06）

v1.3.0：PR 00–06、Cursor 接入、主机超管控制台及可选 Docker CLI 安装已合入开发仓 main。PR 00–06 是实施阶段编号，不是 GitHub PR 编号。本文描述当前代码；早期验收记录仅代表各自当时的范围。

## 当前可用

- DeepAgents、Codex 和 Cursor 有明确的聊天适配器注册表。无 runtime 字段的旧会话继续使用 DeepAgents；未知或损坏的绑定直接拒绝，不切换引擎。
- 超管是独立主机身份 `host:owner`，通过 `/superadmin` 输入主机 key 管理连接；不属于普通注册账号。`JELLYFISH_OWNER_USER_ID` 已废弃。
- 在后端部署环境执行 `python3 launcher.py --superadmin-key`（Docker 用 `docker compose exec openjellyfish python launcher.py --superadmin-key`），由操作者在页面输入 key。创建 Codex 或 Cursor 连接，使用官方登录（Codex 支持设备码 / 浏览器，Cursor 使用浏览器），探测模型并授权可信 admin。普通 admin 设置页只显示获授权的团队连接。
- admin 只能使用授权模型；其会话、文档工具、记忆、服务文档和产物归自己。共享的是超管的供应商账号与额度。
- 新对话直接在聊天输入区选择 API / Codex / Cursor 模型，首次发送时自动建立内部会话，不再经过创建配置弹窗。已有会话可在同一连接内逐轮切换模型；每轮保存实际选择并重新检查授权。引擎、连接、账号代次、授权版本和生图模式仍绑定会话；不会跨账号或供应商自动迁移上下文。
- Codex / Cursor 请求在单调度进程的 SQLite 队列中运行；同连接并发为 1，全局和每 admin 排队有上限。管理员主聊天断开浏览器不停止任务；刷新后重新打开会话可恢复输出和审批。内部 Service 的流断开会取消该次任务。
- 支持文本、文档副本、原生命令/文件审批、停止、产物预览 / 下载。管理员业务工具包含 `/docs` 列出、读取和持久写入，带版本检查的个人记忆更新，以及当前 admin 的服务文档读取。
- 管理员 Web 聊天中的 CLI 会话现复用欢迎入口和输入组件；运行记录支持流式过程、长会话虚拟列表、问答导航、回到底部、附件在用户消息内展示、搜索/生图入口状态及独立审批卡。Cursor 的这两项状态只表示适配入口存在，真实能力尚待验收。运行中发送的文字会在前端按会话排队；同一浏览器标签内切换页面或刷新后，待发项从 `sessionStorage` 恢复，并按固定请求编号与服务端对账。新会话首条消息在提交期间显示等待状态；首条和普通消息的待确认提交也按 admin 保存在同一标签的 `sessionStorage`，恢复后先按请求编号对账，未确认时用原编号重试，存储失败则保留输入并阻止提交。前端构建和模拟浏览器验收已覆盖主要布局；真实供应商端到端交互仍需验收。

**范围说明：** DeepAgents 适配器保留原 LangGraph/checkpointer/HITL 执行机制与 SSE 行为；新增 CLI 队列、账号并发和凭据租用作用于 Codex / Cursor。没有把旧 DeepAgents 任务伪装为 Codex 持久队列任务。

admin 还可将已授权连接与模型用于[内部 Service 的网页、API 和微信](runtime-service-distribution.md)。访客仅通过 Service 工具访问发布的资源，不能取得超管凭据或使用管理员主聊天工具。

### CLI 管理员写入文档库

Codex / Cursor 的会话工作目录是副本。原生文件工具在其中修改 `docs/`，运行结束后只会把修改归档为会话产物，不会自动更新用户文档库。需要保存到文档库时，管理员 Agent 调用 `jellyfish_write_document`，提供完整 `/docs/...` 路径和 UTF-8 内容；成功返回后，文档可从文件面板、`/api/files` 和文档读取工具查看。单次写入上限为 64 KiB。默认只新建文件；同内容重试不重复写入，覆盖已有的不同内容要显式传 `overwrite=true`。写入请求会显示确认卡（包括同内容重试），拒绝时不写入；YOLO 轮次按该轮设置自动处理。Service 与定时任务的工具范围不因此扩大。

新建 Cursor 对话立即取得该工具；已有 Cursor 对话在下一轮重建 MCP 桥并恢复原生会话。Codex 原生线程不能在续聊时增加动态工具，因此旧 Codex 对话会如实提示当前没有此能力；新建 Codex 对话即可取得。以前只写在 CLI 工作副本中的文件不会因这次改动自动进入文档库，需要从原会话产物或工作副本读取内容后再调用写入工具。

## 管理员个人微信与实时语音

- 管理员个人微信扫码确认时新建独立对话，并按当时的默认引擎与授权连接绑定。绑定 Codex / Cursor 的微信消息进入同一 Runtime 队列；绑定属于会话，之后改变默认引擎不会切换这段微信对话。接入前已创建、没有 Runtime 绑定的微信对话继续使用 DeepAgents；损坏的 CLI 绑定直接报错。
- 微信入站消息用 iLink `message_id` 生成稳定请求编号，避免协议重投重复执行。CLI 运行结果从该会话读取，归档的原生图片可作为微信媒体投递。出站每个部分保存 `pending`、`sending`、`sent` 或 `unknown` 状态：明确拒收的 `pending` 可在原消息重投时重试；发送结果不确定时不盲目重发。当前没有后台投递恢复任务，微信端收发仍需真实 iLink 验收。
- LiveKit 实时语音前台仍使用配置的低延迟 LLM 处理对话与工具调用。只有前台调用 `delegate_to_jellyfish` 委派后台任务时，Core 才按该对话的引擎绑定执行：Codex / Cursor 进入 Runtime 队列，DeepAgents 保留原执行路径。真实 LiveKit 音频往返与委派端到端验收尚未完成。

## 管理员定时任务

- 管理员 Agent 定时任务可显式选择 DeepAgents、Codex 或 Cursor，并在保存时固定连接、模型和授权版本。旧任务没有 `runtime_choice` 时仍走 DeepAgents；改变管理员的默认聊天引擎不会暗中切换已有任务。脚本任务不能选择 CLI。
- CLI 定时任务复用 Runtime 的连接队列，但由持久化调度授权限定读写目录和业务工具。客户端原生命令与文件工具被拒绝，文件读写只能经过受限业务工具；联网搜索和原生生图须在任务能力中单独授权。当前不开放语音、视频或 CLI 子任务创建；任务最终文本由调度账本提交后投递，不依赖 `send_message` 工具。
- 参考文档会随执行指令一起进入 CLI 输入；合计超过 32,000 字符时任务明确失败并提示缩短内容，不静默截断。
- 执行中持续检查任务版本、账号授权和能力范围。取消外层任务会停止内层 CLI 运行；结果提交前再次核对授权与投递目标，失效结果不投递。文件写入和原生图片归档记录持久化副作用，崩溃后不自动重放不确定写入。
- 代码与模拟后端回归已覆盖授权收紧、撤权、重复写入、取消、最终提交及 Cursor 本地搜索拒绝；真实 Codex / Cursor 定时执行、联网搜索、生图和最终微信投递尚需部署侧验收。Service 定时任务继续遵循[内部 Service 能力边界](runtime-service-distribution.md#能力边界)。

## Admin YOLO 模式

「设置 → 通用 → YOLO」同时作用于 DeepAgents、Codex 与 Cursor 的 admin 聊天。新对话首条消息及后续消息均传递该设置，服务端按每轮请求保存；关闭后下一轮恢复手动审批，已发出的轮次保留发送时的设置。网络重试复用原请求及原设置。Cursor 自行提出的计划始终需要人工接受或拒绝，YOLO 不自动批准计划；完整的 CLI Plan mode 尚未接入，范围见 [Plan 与 CLI mode 契约草案](runtime-plan-mode-contract.md)。

Codex / Cursor 的普通命令和文件修改先经过原有账号授权、工作区范围和供应商审批选项检查，再由 YOLO 自动批准。超范围或无法核实的操作自动拒绝，不扩大沙箱权限，不生成等待点击的审批卡。自动处理记录保存在该轮事件中，界面用 YOLO 标签标识开启状态。Cursor 的计划请求保留人工审批；Service 访客的执行范围及拒绝原生命令 / 文件审批的规则不受此开关影响。

## 部署开关

默认关闭。只支持 macOS / Linux、`trusted_shared + local`。可以将整套应用部署在 Docker 中，此时 local 指在应用容器内运行 CLI；每租户 Docker 隔离后端、Cloudflare 执行后端仍未实现，配置这些 backend 会失败。

```dotenv
JELLYFISH_RUNTIME_ENABLED=1
JELLYFISH_RUNTIME_ACCESS_MODE=trusted_shared
JELLYFISH_RUNTIME_BACKEND=local
JELLYFISH_RUNTIME_MAX_RUNNING=2
JELLYFISH_RUNTIME_MAX_QUEUED=32
JELLYFISH_RUNTIME_QUEUE_PER_ADMIN=4
JELLYFISH_RUNTIME_RUN_TIMEOUT=1800
JELLYFISH_RUNTIME_APPROVAL_TIMEOUT=300
# 按连接预热并复用客户端；0 表示关闭预热
JELLYFISH_RUNTIME_KEEP_WARM=1
JELLYFISH_RUNTIME_MAX_CLIENTS=2
JELLYFISH_RUNTIME_CODEX_BIN=/absolute/path/to/codex
# Cursor 是可选独立安装，不增加主安装包体积
JELLYFISH_RUNTIME_CURSOR_BIN=/absolute/path/to/cursor-agent
JELLYFISH_RUNTIME_DATA_DIR=/private/persistent/runtime
JELLYFISH_CODEX_PILOT=0
```

必须使用单个 API worker。第二个进程不能取得同一目录的锁，会拒绝启动。不要在多副本中共享此本地状态目录；分布式状态属于 PR 07。

本次真实验收 CLI：`codex-cli 0.154.0-alpha.6.2`，模型 `gpt-5.6-sol`。动态业务工具使用该版本的 app-server `experimentalApi`；升级 CLI 后先运行协议测试和真实验收，再更新部署版本。未使用标记为 OpenAI 内部用途的 `chatgptAuthTokens` 接口。

Docker 另支持可选安装公开稳定版 Codex `0.154.0`；Linux 非 root 的 App Server 初始化、设备码启动和取消已验证，稳定版真实账号聊天尚未验收。安装与配置见 [超管部署说明](superadmin-console.md#docker-中启用-codex)。

Cursor 当前接入与真实验收状态见 [Cursor 状态](runtime-cursor-status.md)。不要把协议回归结果视为真实账号验收。

常规启动沿用现有 FastAPI / Vite 启动方式。前端可用 `JELLYFISH_API_TARGET=http://127.0.0.1:8002` 连接独立测试端口。

## 登录与凭据

- Codex 远端服务器优先使用设备码登录。浏览器登录回调需要在运行 Codex 的主机上完成；设备码是否可用由供应商账号设置决定。
- 登录挑战仅对对应超管开放，过期 / 取消会关闭登录进程并清理临时目录。取消重新登录会保留此前可用连接。
- 凭据在 Runtime 专用目录使用 AES-GCM 加密，密钥文件权限为 0600。请同时备份数据库、加密凭据和密钥；不要放入普通用户文件目录或公开备份。
- 同一个供应商身份只能保留一个有效连接，避免重复连接绕过串行执行和争用刷新凭据。
- 默认客户端属于连接，在专用私有 HOME 中预热并复用；每轮重新绑定 admin、工作区、会话、模型和工具。`JELLYFISH_RUNTIME_MAX_CLIENTS` 默认等于最大运行数，上限 16，同连接仍串行。容量不足时回收空闲客户端；取消、撤权、探测、断开与停服按作用域回收。当前连接池没有 120 秒空闲超时或 `JELLYFISH_RUNTIME_IDLE_SECONDS` 配置。`KEEP_WARM=0` 切回每轮关闭。凭据异常时暂停连接，绝不复制宿主全部 HOME 或其他会话历史。
- 本机执行依赖 POSIX 进程机制及 `ps`，本次在 macOS 验收；Windows 后端尚未适配。跟踪并清理执行者创建的独立进程组，无法确认退出则暂停连接。
- 这是同一宿主下的可信团队模式。磁盘加密和业务作用域不构成恶意租户间的 OS 隔离。
- 断开会停止任务、增加账号代次并删除本应用保存的凭据；不宣称撤销供应商在其他设备上的所有登录。

已登录的专用缓存可由宿主导入。先停止 API 进程，并确认没有其他 Codex 进程继续使用此缓存：

```bash
python scripts/runtime_admin.py import-codex \
  --auth-file /private/dedicated-codex/auth.json \
  --name '我的 Codex' --model gpt-5.6-sol --default
```

导入后由新 Runtime 独占刷新生命周期；不要继续启用旧试验台。浏览器端没有上传或导出原始凭据的接口。

## 故障与回退

- 撤销授权先持久化新的授权版本，再取消排队和运行任务，等待进程退出。历史内容仍由原 actor 读取。
- 中断服务后，未完成任务标记失败，不重放可能有外部副作用的命令。运行、登录、模型探测或凭据租用中断，都会锁住相关连接，要求宿主检查旧进程。无法确认进程退出时，不回写其临时登录缓存；保留私有恢复目录供宿主处理。
- 宿主确认旧进程全部退出后，停止 API，再执行恢复命令；命令也会检查已记录的运行 / 登录 / 清理进程 PID。恢复后必须重新登录和重新授权。

```bash
python scripts/runtime_admin.py recover --profile-id <profile_id> --confirm-stopped
```

- 关闭新功能并重启可停止新 Codex 入口，DeepAgents 继续可用。聊天记录仍在 SQLite 中，聊天历史读取不要求功能开启；不要删除 Runtime 数据目录。
- 超时、账号失效、额度不足、授权撤销都不会自动改用其他账号、DeepAgents 或付费图片 API。

## 能力与验收边界

- Codex 原生生图已通过本机 API 生成与归档 PNG 验证；Cursor 生图适配已实现，但未完成真实生图验收。`image_mode=off` 现会在每轮重绑定客户端，并在 Codex 会话配置、Cursor 生图回调及原生产物归档处执行；它控制 CLI 原生生图，不限制图片输入或普通文件。真实 Cursor 生图、模式切换及前端生图视觉效果仍需部署侧验收。详见 [流式聊天与原生能力](runtime-streaming-chat.md)。
- 每租户 Docker / Cloudflare 执行后端、多副本协调、不可信公众的套餐分发尚未实现。管理员个人微信、受限调度与语音后台委派已有上述 CLI 代码路径，但真实 iLink / LiveKit 和定时 CLI 端到端验收仍待完成；内部 Service 微信已接入共享执行路径，实际微信收发仍需部署侧验收。整套应用的 Docker 部署不等于这些隔离后端。
- Cloudflare、第二个独立供应商身份没有真实验证。两个测试 admin 共享同一供应商账号的成功不能替代这些验收。

## 验证方法

```bash
python -m unittest discover -s tests -p 'test_runtime*.py' -v
python -m unittest tests.test_admin_wechat_runtime tests.test_scheduler_cli -v
python tests/test_storage_local.py
cd frontend && npm run build
```

真实供应商验收是显式操作，会使用传入的个人计划。执行前停止所有使用同一缓存的其他进程：

```bash
python scripts/verify_runtime_standard.py \
  --auth /private/dedicated-codex/auth.json \
  --codex /absolute/path/to/codex \
  --root /private/runtime-acceptance \
  --output /private/runtime-acceptance/evidence.json
```

脚本创建合成的超管和两个 admin，验证实际文本、审批、文档、记忆、服务文档、归档和续话；结束后回写同身份的刷新缓存。验收账号只能用于 loopback 测试，不得直接用于对外部署。验收结束删除测试账号和测试凭据，保留脱敏证据即可。

[本次验收结果](runtime-pr06-evidence.md) · [实现后自审](runtime-pr06-self-review.md) · [完整后续 PR 计划](runtime-implementation-pr-plan.md)
