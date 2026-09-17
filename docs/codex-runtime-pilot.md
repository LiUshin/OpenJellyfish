# Codex Runtime 管理员试验台

> 历史设计 / 阶段验收记录：文中的分支、角色与未实现项以记录当时为准。当前 v1.3.0 使用 [标准模式](runtime-standard-mode.md) 和 [主机超管控制台](superadmin-console.md)；早期验证结果不代表当前完整生产验收。

分支：`codex/runtime-codex-pilot`。入口：`/runtime-pilot`。

这是架构提案的首个可操作试验切片。当前管理员聊天页仍使用 DeepAgents；试验台有独立的会话和运行记录。尚未完成全平台 Runtime Service 迁移，也没有接入 Cursor、微信、调度或消费者服务。

## 已实现

- Python 直接管理 `codex app-server --listen stdio://`；工具响应、消息、产物和终态转成 Jellyfish 事件。
- 固定后端/模型的会话；持久化 Codex thread ID，后续轮次走 thread/resume。
- 后台执行与 SSE 分离：浏览器断开不终止任务，刷新/重新连接可重放事件。
- request_id 幂等；同时只接受一轮执行；第二个 backend worker 拒绝接管试验台。
- 命令/文件审批只支持允许本次和拒绝；展示命令或文件变更。额外权限、网络升级、越界路径拒绝；未支持的阻塞 RPC 明确报错，不自动放行。
- 取消先尝试 turn/interrupt，再终止进程组；重启将未完成轮次标为失败，并锁定该会话续跑，避免旧进程状态未知时重复执行。已完成的会话可恢复。
- 导入用户所选 `/docs/` 文件的副本，投影当前系统提示和用户资料。原文件不自动覆盖。
- 完成后将新增/修改的文件通过 StorageService 写入 `/generated/runtime/{session}/{run}/`；可在试验页预览图片和下载文件。
- 原生图片事件必须对应工作区内真实、可校验的图片；无文件/越界/符号链接/硬链接/损坏图片报错。不自动调用其他生图 API。

## 启用

仅适用于可信管理员的 macOS / Linux 自托管环境，后端使用单 worker。原生 shell 使用 Codex workspace-write 沙箱，**它不是多租户读取隔离**，可能具备对宿主机其他文件的读取权限。不要向消费者开放。用户选择的专用 Codex profile 也应只配置可信工具；首版不会复制或清理已有 profile。

在实例的环境配置中设置（替换占位值）：

```dotenv
JELLYFISH_CODEX_PILOT=1
JELLYFISH_CODEX_ADMIN_ID=<Jellyfish 管理员 user_id>
JELLYFISH_CODEX_BIN=/absolute/path/to/codex
JELLYFISH_CODEX_HOME=/absolute/path/to/dedicated-codex-home
```

管理员 user_id 可从现有 `/api/auth/me` 查看；只能配置一个 ID。省略 CODEX_HOME 配置时使用该用户目录下 `runtime_pilot/codex-home`，不会继承当前开发者的 Codex 登录。

使用同一专用目录完成 Codex 官方登录，例如：

```bash
CODEX_HOME=/absolute/path/to/dedicated-codex-home /absolute/path/to/codex login
```

然后执行只读探针：

```bash
.venv/bin/python scripts/probe_codex_runtime.py \
  --codex /absolute/path/to/codex \
  --home /absolute/path/to/dedicated-codex-home
```

启动现有 Jellyfish 后端和前端，打开 `/runtime-pilot`。点击“检测 Codex 连接”，新建会话时可选模型和文档路径。连接已认证仍不代表原生生图已实测通过。

本机试验可在配置好 `.env` 后运行 `bash scripts/start_runtime_pilot_local.sh`，打开 `http://127.0.0.1:3001/runtime-pilot`。该启动器固定单 worker、仅监听本机、使用本地存储，并在当前进程关闭定时任务、启动时环境恢复和微信通道（`DISABLE_WECHAT_CHANNEL=1`，包括路由与已有会话恢复）。按 Ctrl+C 停止两项服务；其他启动方式仍保持原来的默认行为。运行前确保 8000 和 3001 未被占用。

Jellyfish 的 OpenAI/Anthropic 等 API key 不会注入 Codex 子进程。试验台使用专用 Codex profile 的身份和配额。更换 profile 路径或 CLI 路径后应重启后端并新建会话；首版不能检测同一目录内账号被手工切换，切换账号时也必须新建会话。

## 手工验收任务

1. 在 Jellyfish 文档目录创建测试 brief，导入 `/docs/brief.txt`。让 Codex 读取并改写成 `report.md`；检查原 brief 未改变、report 正确归档。
2. 第二轮提及上一轮结论；刷新页面、重启后端后继续，核对会话恢复。
3. 触发一条需要审批的命令并拒绝，验证对应操作未执行。若没有变更预览，文件审批只能拒绝。
4. 执行耗时任务后停止，检查终态为 cancelled、后续轮次可启动、没有孤儿进程继续写入。
5. 要求原生生成图片并保存到工作区，核对真实图片预览、下载字节、来源标记与账号用量。失败应显示失败，不能静默改用其他 API。
6. 断开浏览器后重新连接；同一 request_id 重试不得产生第二次执行。

## 自动验证与边界

```bash
.venv/bin/python -m unittest discover -s tests -p test_runtime_pilot.py -v
cd frontend
npm ci
npm run build
```

离线测试使用测试替身和真实 stdio 子进程，覆盖执行/存储/API/流式重放/拒绝/取消/幂等/重启/归档边界；不作为真实模型效果或原生生图可用性的证明。

浏览器离线验证可运行 `tests/runtime_pilot_preview.py`，只监听 `127.0.0.1:8766`，使用临时目录、假身份和测试替身，不加载生产 app.main。在登录页填写任意测试用户名/密码，再打开 `/runtime-pilot`；不要填写真实凭据。发送 `image` 生成测试图片，发送 `approve` 显示审批卡，发送 `wait` 验证停止。该服务仅用于 UI 验证，不能用于实际模型任务。

本轮真实 CLI 验证基线：`codex-cli 0.154.0-alpha.6.2`。空专用身份目录下初始化、account/read 和 thread/start 成功，认证状态为 false；没有真实推理和生图验证。刚创建、尚未开始首轮的 Codex thread 可能尚无可恢复 rollout；该情况会显式失败，需要新建会话，不会重放旧任务。

本轮验证结果：15 项离线测试通过；完整 FastAPI app 导入通过；前端生产构建通过（保留现有大 bundle 提示）。应用内浏览器使用离线 fixture 验证文档导入、流式消息、图片预览、审批拒绝、刷新记录和停止后的 cancelled 状态，试验页面未捕获到控制台错误。

大小边界：最多导入 20 个文档，单文件 20 MB、总工作区扫描 50 MB / 1000 个文件；单轮最长 30 分钟、审批最长 5 分钟、事件最多 8 MB / 10000 条。更大工作负载需要后续资源和留存管理。

实现局限：原生工具仍受其自身配置影响；业务 MCP 桥、自定义记忆子代理、图片附件输入、跨引擎迁移、S3 工作区持续同步未实现。StorageService 支持源文档读取和成果归档；运行元数据与 scratch 保存在本机，不是分布式存储。失败/取消轮次的未归档文件保留在工作区供人工排查，后续成功轮次可能将其归档。

停用时将 `JELLYFISH_CODEX_PILOT` 设为 `0` 并重启。清理试验数据前先停止运行并下载成果；本实现不自动删除用户文件或登录目录。

## 2026-09-15 本机启用记录

- 后端 `127.0.0.1:8000`、前端 `127.0.0.1:3001`；健康检查、前端 API 代理、试验台权限检查通过。
- 专用 profile 使用仓库已忽略的 `data/codex-pilot-home`，通过官方浏览器登录完成认证。
- 真实探针返回 `authenticated=true`、模型列表以及 `native_image=true`。后者只表示能力声明，原生图片生成仍待实测。
- 真实文本轮次已完成，流式返回「Jellyfish Codex 连接成功。」；记录保存在试验会话 `17ada8e29143435b81b52d1f0dd8d21d`。本次没有调用工具或生成文件。
- 浏览器验证了真实应用登录页正常显示、无控制台错误；管理员登录后的页面操作仍待用户验证。此前离线 fixture 验证不能替代这一步。
- 本次进程关闭了定时任务、微信通道与启动时 venv 恢复。新启动脚本通过 shell 语法检查，微信关闭配置通过路由检查。
