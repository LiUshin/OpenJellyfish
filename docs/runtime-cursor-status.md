# Cursor 同层接入与验收状态

v1.3.0 已包含此接入；下方验收记录保留各阶段的验证范围。当前部署入口为 `/superadmin`，详见 [主机超管控制台](superadmin-console.md)。

## 实现范围

Cursor 与 Codex 共用连接管理、超管权限、可信 admin 授权、加密凭据租约、固定会话绑定、运行队列、审批、业务工具和产物归档。供应商差异集中在 provider 与协议适配器。

- Cursor 使用官方 CLI 的 ACP 协议和浏览器登录；CLI 可选安装，通过 `JELLYFISH_RUNTIME_CURSOR_BIN` 指定，默认不打包进 Jellyfish；Docker 可通过固定版本构建参数选择安装，见 [部署说明](superadmin-console.md#docker-中启用-cursor)。
- 每次租用只解密当前连接的缓存，使用独立 HOME；禁止继承宿主 API Key、Cursor 桌面配置与其他会话历史。
- 同一供应商身份只保留一个连接，同连接串行执行，清理完整进程树后才回收刷新凭据。
- 会话固定 Cursor 连接；模型可逐轮切换，每轮重新检查允许的模型。不可用时明确失败，不自动切换到其他模型、Codex 或 DeepAgents。
- ACP `allow_once` 映射为“允许本次”；拒绝永久许可、未知方法、越界文件和未支持的网络审批。原生删除请求缺少可验证路径时只允许拒绝。
- 短期 loopback MCP 使用每个客户端的随机凭据；复用时重新绑定当前 run，空闲时禁用业务调用。业务工具绑定服务端 actor，并在每次调用重查授权。
- 设置页在对应连接旁展示授权链接、复制入口和到期时间；尝试打开登录窗口，窗口被阻止时仍可手动打开。登录适配器构造失败会清理预约，不再永久停在“等待登录”。
- 独立工作区创建无模板 Git 仓库，防止 Cursor 向上发现应用源码仓库。拒绝工作区 / 私有 HOME 中未托管的 MCP、插件和 hooks 配置，并禁用项目权限配置。存在宿主全局 Cursor hooks 时拒绝启动。

模型切换与热复用的后续验收见 [流式聊天改造](runtime-streaming-chat.md)。以下为连接接入时的基线记录。

## 验证证据

- Runtime 全套协议和业务回归：84 项通过。覆盖包含 Cursor 登录成功 / 取消 / 失败 / 到期、构造失败清理、缓存身份切换、重复账号、撤销、续话与历史去重、供应商路由、MCP actor 边界、审批越界和撤销后迟到审批。
- StorageService：22 项通过。
- 前端 TypeScript / Vite 构建通过；保留既有大 bundle 警告。
- 真实 Codex 专用连接刷新模型成功（HTTP 200，5 个模型），主聊天接口返回 `CODEX_PEER_OK`，全程 8.4 秒。
- 真实 Cursor CLI `2026.09.15-d2fe57e`：已完成用户官方浏览器授权，获取 38 个模型；连接重启后仍 ready，无恢复围栏。

### 本机真实验收（2026-09-17）

使用 `composer-2.5[fast=true]`；验收会话 `e5db82b2`（本机侧栏“Cursor 接入验收”）。

| 项目 | 结果 |
| --- | --- |
| 首轮文本 | 正确返回 `CURSOR_PEER_OK`；首段 15.1 秒，全程 16.3 秒 |
| 重启 CLI 后续话 | 正确返回上一轮标记 `JF_CURSOR_71`；首段 16.6 秒，全程 18.4 秒 |
| actor 绑定 MCP | `jellyfish_list_documents` 读取合成空目录，服务端记录 completed |
| 前端审批 | 浏览器实际显示 Cursor、固定模型、“允许本次 / 拒绝本次”；点击后分别完成 MCP 和命令审批 |
| 产物 | 精确审批 `printf JF_CURSOR_71 > cursor-check.txt`；下载归档文件，字节等于 `JF_CURSOR_71` |
| 取消与清理 | 两次取消等待审批的验收任务；连接无 recovery_required，后续任务成功 |
| 同层兼容 | Codex 真实文本回归成功；两种连接最终均 ready |

一次审批等待期间遇到供应商 HTTP/2 CANCEL。CLI 以错误文本结束，未生成文件；重新执行并立即审批后成功。已据此增加错误信封识别及回归：此类供应商错误应显示 failed，不再显示 completed。供应商错误只提供文本信号，这项识别依赖当前 CLI 的固定格式，升级必须复验。

**仍未覆盖：** Linux 真实账号、Cursor 第二个独立账号、Cursor 两个真实 admin 共享、原生生图。多 admin 隔离与撤销使用协议 / 业务测试验证，不宣称已完成全部线上场景验收。

## 自审修正与限制

- 已修正启动异常发生在清理范围外导致的残留登录预约。
- 对照真实审批修正 MCP 的 `jellyfish-<tool>: <tool>` 名称映射，仅匹配已注册业务工具；未知工具保持拒绝。
- 对照官方发行包修正 `cursor/ask_question` 和 `cursor/create_plan` 的嵌套 `outcome` 响应。
- 审批不接受 `allow_always`，供应商自定义 optionId 保留映射；模型选择与连接 runtime 不一致会拒绝。
- 当前仅 `trusted_shared + local`。这是可信团队同宿主模式；应用级授权、缓存隔离和审批不等于恶意租户间的操作系统隔离。Docker / 集群隔离未在本次实现。
- 原生生图保持关闭：私有配置拒绝 `GenerateImage(*)`，发现图片事件后终止；没有完成供应商真实图片行为验证。
- macOS CLI 包已核验；Linux x64 安装与登录链接生成已验证，Linux 凭据路径依据官方包实现，尚未做 Linux 真实账号验收。CLI 升级需重验 ACP、文件凭据存储、扩展响应及配置发现行为。

## 官方依据与本机依赖

- [Cursor ACP](https://cursor.com/docs/cli/acp)
- [Cursor CLI 登录](https://cursor.com/docs/cli/reference/authentication)
- [Cursor CLI 配置](https://cursor.com/docs/cli/reference/configuration)
- [Cursor CLI 安装](https://cursor.com/docs/cli/installation)

本机可选 CLI 位于忽略的 `data/runtime-bin/cursor-2026.09.15-d2fe57e/`，未修改全局 shell 或桌面 Cursor 登录。`AGENT_CLI_CREDENTIAL_STORE=file`、`--disable-project-configs` 与平台缓存路径核对自官方发行包，属于升级时必须复验的接入约束。

## Docker 登录故障修复验证（2026-09-17）

- 复现原版缺少 Cursor CLI 时登录 API 返回 502。新版缺少 CLI / Git / ps 会给出部署错误；启动失败、30 秒未取得地址后都释放登录预约。
- 111 项 Runtime 回归与前端生产构建通过。Chrome 使用模拟 API 检查等待提示、错误弹窗、父页面错误和成功跳转；未使用真实账号。
- 同一安装脚本在 Linux x64 测试镜像中成功安装 `2026.09.15-d2fe57e`（本机 ARM Docker 仿真 x64）。UID 1000、独立 HOME 下 CLI 启动、无模板 Git 工作区及 ps 检查通过，1.01 秒取得官方登录链接，随后关闭登录进程；未完成账号授权。
- 完整生产基础镜像构建被本机 Debian HTTP 下载源 503 阻断；未宣称全镜像构建通过，未部署 EC2 或验证线上账号续话。
