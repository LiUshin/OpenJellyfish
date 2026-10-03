# OpenJellyfish v1.4.1

## 更新内容

- **项目与共享 brief**：按主题整理管理员对话，编辑同一份 Markdown brief，逐轮提供给 DeepAgents、Codex 或 Cursor；支持项目内搜索、移动对话，删除项目不会删除对话。
- **对话内 Service 测试**：选择草稿或已发布 Service，使用其保存的模型、Prompt、文档和工具范围试聊；关闭测试后，保留记录交给管理员 Agent 复盘。测试上下文与真实消费者记录分开。
- **Tracing 画布与顺序回放**：从已记录的工具活动查看文件、读取 / 修改位置、输入输出和差异，支持平移、缩放与按动作顺序回放。未记录或近似的位置会保留相应标记，不代表完整系统审计。
- **持久定时执行**：增加 Run 记录、执行授权、恢复检查、取消及独立投递状态；管理员 Agent 任务可使用获授权的 CLI 引擎。
- **Service 反馈与回复**：反馈持久保存、管理员回复绑定原会话；支持面向选定会话的即时 / 定时文字通知和逐对象投递记录。网页自动补拉后台消息，API 可按游标增量拉取。
- **日常工作区**：整理聊天、服务、任务、环境入口，改进审批、队列、文件保存和流式回复的状态处理。
- **macOS 启动器**：纳入完整应用包签名、嵌入运行环境校验与启动完整性修复。签名仍为 ad-hoc，未完成 Apple 公证。

## 发布环境与依赖

本版安装包按 `constraints-release.txt` 中已验证的框架与 SDK 版本构建，DeepAgents 固定为 0.4.12。未约束的新版本 0.7.x 增加了当前 LockAwareBackend 尚未实现的 `delete` 协议，会导致首轮 Agent 执行失败；不能只凭模拟引擎回归判断依赖升级兼容。

打包流程现在使用包内 Python 真正创建并执行管理员 / 批处理 Agent（固定回答的离线模型，不访问供应商）。所有平台附件完成后才公开 Release。Intel Mac 使用标准拖放安装 DMG，保留签名、架构、嵌入运行时及校验和检查。

v1.4.1 同时修复 Windows 中文日期格式触发的 locale 编码错误，覆盖管理员、消费者、CLI instructions 和 pilot 提示词。年月日显示与用户时区保持不变。该问题由 Windows 包内真实 Agent 图检查发现，已加入回归。

v1.4.0 候选已撤回，旧标签保留且不作为本次安装来源。请使用 v1.4.1 标签或本 Release 的安装包；对应源码已自动加载依赖约束，无需单独补装约束文件。

## 升级前准备

1. 停止当前应用后备份完整 `users/` 及部署配置；或使用 SQLite 一致性备份。`users/.scheduler/executions.sqlite3` 保存任务与消息投递事实源，不能仅备份个人 ZIP、inbox JSON 或运行中的 SQLite 主文件而遗漏 WAL。Runtime 数据库、凭据 vault 和部署加密密钥也需按原部署指南保存。
2. 安装新包或更新源码后启动。检查任务恢复状态、暂停项和投递记录；结果为 `unknown` 的外部发送不要盲目重发。回滚应恢复与旧程序匹配的完整备份。
3. 新增可选配置见 `.env.example`：`SCHEDULER_MAX_PENDING=128` 限制待执行 Run；`DISABLE_SERVICE_MESSAGING=1` 暂停消息 worker。单独禁用调度器不会停止 Service 消息投递；`SAFE_STARTUP=1` 同时停止两者和启动时 venv 恢复。

## 使用范围

- CLI 使用与分发仍面向可信内部团队；后端要求 macOS/Linux、单 API worker 和 `trusted_shared + local`，按超管授权使用供应商账号。Windows 安装包提供 DeepAgents / API 模式，不表示 Windows 已支持 CLI Runtime。
- Service 测试不是无副作用沙盒：`contact_admin` 外部提醒被禁用，其他授权工具仍可能执行真实操作；还需单独验收真实网页、API、微信渠道。
- CLI Service Agent 定时任务、CLI Service 语音 / 视频及 BYOK 仍不开放。管理员 CLI 定时任务与直接文字通知是不同路径。
- Codex / Cursor 账号套餐接入不等于原生 Plan mode；统一 Plan/mode 契约仍为设计草案。
- 现有 Codex 会话可能保留旧的动态工具定义。需要新增项目 brief 写入或 Service 记录分页能力时，新建会话；项目 brief 仍可由界面编辑。
- 运行状态、凭据和环境依赖不是文档；复制文档可复用方法，但不能承诺模型输出完全相同或完成整机迁移。

## 验证

本次在隔离工作区完成前端 TypeScript / Vite 生产构建、107 项前端测试、188 项项目 / Service / 调度 / 消息等后端回归、156 项 Runtime 回归、2 项真实 Agent 图 / Windows 日期兼容测试及 1 项使用真实 codesign / hdiutil 的 macOS 完整性测试。Runtime 回归同时修正了一个未接收新增 `conversation_id` 参数的旧测试替身。前端保留现有大 chunk 提醒。

这些检查包含模拟引擎与合成渠道数据，不等于真实供应商、微信终端、LiveKit、生产数据恢复或各目标机器安装验收。安装包以本 Release 的 Actions 构建和附件结果为准。

## 已知界面限制

项目 brief 编辑后，请点击保存再切换项目或对话；当前界面尚未保护未保存的草稿。Service 测试会按配置真实执行授权工具，只有 `contact_admin` 的外部通知被模拟。官网首页与指南的内容同步另行进行，安装包版本以本 Release 附件为准。

## English summary

v1.4.1 adds admin projects with shared Markdown briefs, in-chat Service testing, an interactive Tracing canvas with sequential playback, durable scheduled execution, and persistent Service feedback/replies/direct text notifications. It also includes workspace interaction and macOS bundle-integrity fixes.

Back up the complete user state and databases consistently before upgrading. Per-user ZIP exports do not include the full execution/delivery ledger. Unknown external-send outcomes require manual review. CLI Runtime remains a trusted-team macOS/Linux feature with one API worker; Windows retains DeepAgents/API workflows. Subscription support is distinct from native Plan mode. Service tests can execute authorized tools and do not replace real channel acceptance tests. macOS builds use ad-hoc signing, without Apple notarization.

Installers use the verified dependency constraints (DeepAgents 0.4.12). The unbounded 0.7.x backend protocol is incompatible with the current lock-aware backend. Real admin and batch Agent graphs are now exercised with the embedded Python before packaging; publication waits for every platform. Windows prompt dates now avoid locale-dependent formatting. The withdrawn v1.4.0 tag is preserved; use v1.4.1 installers or source, which loads the dependency constraints automatically.
