# OpenJellyfish v1.4.2

## 更新内容

- **Codex / Cursor 保存文档库**：管理员 Agent 可调用 `jellyfish_write_document` 将 UTF-8 文本保存到 `/docs`。写入前展示路径、原内容预览和拟写入内容；同意后持久保存，拒绝、超时或取消不会保存。YOLO 轮次沿用自动审批设置。
- **明确的覆盖保护**：每次最多 64 KiB，默认只新建；覆盖已有不同内容需要 `overwrite=true`，相同内容重试不会重复写入。路径限制、工作区锁、撤权检查及审批期间的文档变化检查共同保护写入。
- **旧会话升级提示**：Cursor 在下一轮刷新工具并恢复原生会话；旧 Codex 线程不能追加动态工具，会提示新建对话。CLI 工作副本中的修改仍只归档为产物，不会自动同步进文档库，旧文件也不会自动搬运。
- **紧凑文件预览**：打开的文件收纳在预览工具栏胶囊中，悬浮、点击或键盘操作可展开。悬停其他文件可先看内容，保持当前文件不变；保留切换、关闭、横向标签及窄屏操作，并调整圆角与展开动效。
- **文档同步**：双语用户与开发者指南补充保存步骤、权限范围和会话兼容说明。没有新增环境变量，沿用 `.env.example` 的配置。

本版包含 [v1.4.1](release-v1.4.1.md) 的项目 brief、Service 测试、Tracing 回放、持久定时执行及消息回复能力，并保留其框架依赖约束和 Windows 日期兼容修复。

## 升级与范围

1. 停止应用后备份完整 `users/`、部署配置、Runtime 凭据 vault 与部署加密密钥。SQLite 应使用一致性备份，不能遗漏运行中数据库的 WAL；个人 ZIP 不包含完整任务与投递台账。
2. 安装对应平台的新包，重启后检查对话、文档和任务恢复状态。需要文档库写入的旧 Codex 对话请新建；Cursor 可继续原对话。
3. 可先在新管理员对话请求保存 `/docs/release-check.md`：核对审批内容，同意后在文件面板打开；再试一次拒绝写入，确认未改变文件。无需接入真实 Service 或外部消息渠道进行这个检查。

CLI Runtime 仍面向可信内部团队，仅支持 macOS/Linux、单 API worker 和 `trusted_shared + local`。Windows 安装包保留 DeepAgents / API 模式。新增工具不扩大 Service 或定时任务的权限。供应商账号套餐接入不等于原生 Plan mode。

macOS 安装包使用 ad-hoc 签名，尚未完成 Apple 公证。复制文档可复用方法，不代表运行状态、账号与依赖同步迁移，也不保证模型输出完全相同。

## 验证与构建

发布前通过 163 项 Runtime 回归、188 项项目 / Service / 调度等后端回归、2 项真实 Agent 图及日期兼容检查、107 项前端测试和 TypeScript / Vite 生产构建。真实图检查使用离线模型，实际构建并执行管理员与批处理 Agent；Runtime 覆盖审批同意 / 拒绝 / 取消、文档覆盖、MCP 请求大小、旧工具刷新等路径。

浏览器回归通过 Codex、Cursor、DeepAgents 三种对话，覆盖文件胶囊展开 / 悬浮预览、键盘切换、附件、模型切换与发送、移动布局和浅色主题，未记录页面脚本错误；接口使用测试数据。

安装包按 `constraints-release.txt` 构建。每个平台使用包内 Python 执行真实 Agent 图检查，三个平台附件齐备后才公开 Release。前端构建仍有现存的大 chunk 提醒。这些检查不替代真实供应商账号、微信 / LiveKit 和各目标机器的安装验收。

## English summary

v1.4.2 adds approved admin document-library writes for Codex / Cursor and a compact, expandable file-preview capsule. The write tool persists up to 64 KiB of UTF-8 text under `/docs`, requires explicit overwrite for different existing content, and respects approval, cancellation, authorization and workspace locks. Cursor refreshes tools on the next turn; existing Codex threads require a new chat. Native working-copy edits remain session artifacts until explicitly saved to the library.

Back up complete state consistently before upgrading. No new environment variables or expanded Service/scheduler permissions are introduced. CLI Runtime remains a trusted-team macOS/Linux feature; Windows retains DeepAgents/API workflows. Builds preserve verified dependency constraints and run real offline Agent graphs with bundled Python. Publication waits for all three installers. macOS packages use ad-hoc signing without Apple notarization; real supplier/channel and target-machine acceptance remain separate.
