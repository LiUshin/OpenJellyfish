# Runtime Plan 与 CLI Mode 契约草案

状态：设计草案，**统一 Plan/mode 契约尚未实现**；第 6 节记录同批改造的 Service 记录区。本文以 2026-10-02 当前工作区代码为基线。现有的队列、事件存储及 Cursor 计划人工审批，不等于已经接入原生 Plan mode。

## 1. 已有运行层与分界

- Codex 和 Cursor 共用 [RuntimeManager](../app/runtime/manager.py)、[RunService](../app/runtime/service.py)、[RuntimeStore](../app/runtime/store.py)、连接授权、排队、取消、原生操作审批、事件回放与产物归档。供应商差异由 [RuntimeAdapter](../app/runtime/types.py) 和各自的协议适配器处理。会话绑定引擎、连接和账号代次；模型可逐轮选择。
- 产品聊天入口通过 [CHAT_ADAPTERS](../app/runtime/chat_adapters.py) 分发。DeepAgents 保留自己的 LangGraph/checkpointer/HITL 与 SSE 链路；Codex/Cursor 使用同一个外部 Runtime 队列。这里统一的是入口选择，不是三种引擎的执行循环或计划语义。
- 当前 [RuntimeAdapter](../app/runtime/types.py) 仅提供探测、开会话、发送文本轮次、响应请求、取消和关闭；[TurnRequest](../app/routes/runtime.py) 与 [RuntimeRun 前端类型](../frontend/src/services/runtime.ts) 均没有 mode、计划策略或计划事件字段。
- DeepAgents 的 `plan_mode` 在用户输入中加入规划提示，`propose_plan` 由 HITL 审批，前端从 `write_todos` 工具参数读取步骤。这是现有 DeepAgents 流程，不是供应商无关的 Plan 契约。[实现入口](../app/routes/chat.py) · [计划提示](../app/services/tools.py) · [计划 UI](../frontend/src/stores/streamContext.tsx)
- CLI 路径尚未传递 `plan_mode`：旧 `/api/chat` 的 [ExternalChatAdapter](../app/runtime/chat_adapters.py) 忽略它；新 `/api/runtime/turns` 也不接收它。Cursor 适配器目前将会话设为 `agent`；当供应商自行提出 `cursor/create_plan` 时，运行层会以独立计划审批要求人工接受或拒绝，但 `cursor/update_todos` 仍未展示。Codex 尚未传递原生协作 mode 或映射计划更新。[Cursor](../app/runtime/cursor.py) · [Codex](../app/runtime/codex.py)

## 2. 能力协商

将部署开关、连接可用性与运行能力分开。服务端为 **连接 + 账号代次 + 模型 + 原生会话** 提供带版本的能力快照；最终以当前会话握手和本轮执行前校验为准。供应商只提供部分信息时，标记 `unknown`，不得转成 `supported`。Cursor 的会话 modes 在 `session/new`/`session/load` 返回，因此连接列表只能显示候选能力，不能代替会话校验。

建议的能力描述符（字段名为目标契约，非现有 API）：

```json
{
  "version": 1,
  "source": { "runtime": "cursor", "profile_id": "…", "model": "…", "client_version": "…" },
  "modes": [
    { "id": "agent", "label": "Agent", "availability": "supported" },
    { "id": "plan", "label": "Plan", "availability": "unknown" }
  ],
  "plan": {
    "proposal": "unknown",
    "progress": "unknown",
    "approve": "unknown",
    "edit": "unsupported",
    "reject": "unknown",
    "execution_gate": "unknown"
  }
}
```

`availability` 取 `supported | unsupported | unknown`。除原生 mode 外，按 mode 描述联网搜索、生图、文件输入、工具与写入能力；账号授权和 Service/调度范围再与供应商能力取交集。当前 profile 的 `web_search`、`image_generation` 等粗粒度值不能推断 Plan、某模型的配额或审批语义。客户端版本、账号、模型、授权或会话变化时使快照失效并重验。

## 3. 每轮意图与 mode 语义

引入供应商无关的 `TurnIntent`，从 Web 请求一直传到 `RunService` 和适配器，并随 run 保存：

```json
{
  "mode_id": "plan",
  "plan_policy": "review_then_execute"
}
```

- `mode_id` 是该供应商能力描述符中的原生 mode ID，不把不同供应商同名 mode 视为等价。缺省使用经验证的默认 mode。适配器在原生 prompt 前设置并核对 **effective mode**，将 requested/effective 值和能力快照写入 run。若 mode 作用于会话而非轮次，切换前须确认没有活跃轮次，恢复会话时重新核对。
- `plan_policy` 独立于原生 mode，初期定义 `none | draft_only | review_then_execute`。`none` 不承诺计划门控；`draft_only` 只在适配器能保证规划阶段不执行有副作用操作时开放；`review_then_execute` 必须先得到可展示的计划，再经用户明确批准才进入执行阶段。原生“Plan”按钮或提示词本身不构成这一保证。
- RunService 在入队前校验已知能力；会话握手后、发送原生 prompt 前再次校验。请求的 mode、计划策略、模型、附件与 YOLO 一起纳入 `request_id` 幂等比较。不能满足意图时以明确的 `unsupported_mode` / `unsupported_plan_policy` 等错误结束，不切换 mode、引擎或模型重试。
- 计划审批独立于命令/文件审批。`review_then_execute` 下，通用 YOLO 不自动批准计划；计划拒绝应停止后续执行。只有供应商实际支持计划编辑回传时，前端才提供“编辑计划”。纯文本“建议性计划”若以后需要，应是另一个显式选项，并标明没有执行门控。

## 4. 计划事件、审批与展示

适配器将原生协议映射为可持久化、可回放的事件，而不是让前端解析供应商工具名或普通文本。建议事件为：

| 事件 | 最少字段 | 用途 |
| --- | --- | --- |
| `mode_selected` | `requested_mode_id`, `effective_mode_id`, `capability_version` | 显示本轮实际 mode，核对预期与实际并审计漂移 |
| `plan_proposed` | `plan_id`, `revision`, `steps[]` | 显示待审计划；步骤有稳定 ID 和文本 |
| `plan_updated` | `plan_id`, `revision`, `steps[]` | 更新步骤状态，按版本覆盖，支持断线回放 |
| `plan_review_requested` | `approval_id`, `plan_id`, `revision`, `allowed_decisions[]` | 进入独立的计划审批 UI |
| `plan_review_resolved` | `approval_id`, `decision`, `revision` | 记录批准、编辑或拒绝结果 |

`steps[].status` 统一为 `pending | in_progress | completed | failed | skipped`。Run 生命周期仍使用现有 queued/running/waiting_approval/terminal 状态；另存 `phase=planning | waiting_plan_review | executing`，不把计划阶段误认为命令审批。`RuntimeStore` 同时保存事件和最新计划投影，前端重连读取同一投影，审批使用稳定 `approval_id` 与 `revision` 防止对过期计划作答。

审批请求应带 `kind=plan | command | file_change` 和该请求实际允许的决定。当前 Cursor 的 `cursor/create_plan` 已有独立 `plan/requestApproval` kind、人工 `accept | decline` 和专门的前端卡片，但尚无稳定计划 ID、版本、编辑或步骤进度。通用 `/approve` 将来需要按 `kind` 校验更丰富的决策；`cursor/update_todos` 应产生计划更新。Codex 仅在已核对的原生协议支持时接入相应 mode/事件。DeepAgents 可在产品入口将 `propose_plan`、`write_todos` 映射为相同展示事件，同时保留旧 SSE 和 graph，不要求先迁移执行内核。

## 5. 未实现边界与落地顺序

目前没有上述能力目录、`TurnIntent`、CLI Plan mode 选择、标准化计划事件或跨引擎计划审批；Cursor 仅有供应商自行提出计划时的人工接受/拒绝。Codex/Cursor 的联网搜索和生图能力字段也不证明特定模型、账号或部署中已经可用；这些能力应单独验收。本文不承诺 DeepAgents、Codex、Cursor 的 Plan 功能等价，也不将 Service、微信、语音委派和调度自动纳入新模式。

1. **契约与守卫：** 增加能力描述符和 `TurnIntent` 的类型/API/持久化/幂等校验；默认 `none` 保持现有行为。覆盖未知 mode、能力变化、重复 request ID 与 YOLO 不越过计划审批。
2. **供应商 mode：** 分别核对固定版本的 Codex App Server 与 Cursor ACP 协议、可用 mode 和会话恢复语义；实现设置、回读和显式失败。先验收 mode 切换，不把 Plan 展示或执行门控作为已完成。
3. **计划闭环：** 映射提案、进度、审批和结果；实现持久投影、断线恢复及前端卡片。分别验证批准、拒绝、可用时的编辑、取消和审批前无副作用。
4. **渠道扩展：** 在主聊天验收后，让 DeepAgents 计划事件进入共同展示；Service、个人微信、语音后台委派和调度逐项决定哪些 mode/计划策略可用，并复验各自授权与恢复边界。

## 6. Service 记录区的跨核读取边界

Service 记录区与 Plan/mode 相互独立。新的 `service_records_enabled` 默认关闭，作为管理员主聊天跨 DeepAgents、Codex、Cursor 读取本人 Service 记录的唯一显式开关。旧 `include_consumer_conversations=true` **只保留** DeepAgents 记忆子代理范围内的 Service 对话与反馈读取权限，不自动授予顶层 Agent 或 CLI；设置 API 不再允许新授予此旧权限，已有授权仍可关闭。旧记忆工具在新开关或旧开关打开时可读；顶层 Agent 与 CLI 只认新开关。DeepAgents 主 Agent 在新开关打开时直接获得通用 list/read 工具，不需要记忆子代理参与。受限的 Service 访客和 CLI 定时任务仍按各自的授权范围运行，不继承此主聊天开关。

三种内核的顶层记录区入口通过服务端的 [Service 记录区 Reader](../app/services/service_records.py) 按虚拟路径读取，而不接触原始 Service 文件树。根路径 `/service-records` 可列本人 Service 和反馈区；各 Service 下可列对话摘要、调用记录和 Token 汇总，可分页读取对话最近消息。长消息可按 `content_offset` 继续读取，续读时须原样传回首段的 `message_ref`，以防新消息令 offset 错位；反馈区可读单条反馈。Reader 以当前 admin 身份解析 Service 与对话 ID，限制条数、字符和输出大小，并在返回前复查开关。DeepAgents 的旧 Memory 工具保留原输出格式，但每次调用实时检查相同的授权开关。记录内容是访客数据，不能当成 Agent 指令。

这次把旧 Memory 子代理的 `read_inbox` 纳入同一个开关判断：旧开关或新开关均关闭时，Agent 不再能通过该工具读反馈；这是对原先无门控行为的**有意收紧**。admin 在 Service 管理页查看自己记录的既有 UI 权限不受影响；界面折叠或展开也不代表授权。Service 访客的当前对话历史权限维持原范围，不因 admin 打开记录区而获得其他对话。

记录区是应用层只读投影，不是 CLI 的原生文件系统挂载。当前本地 Runtime 标为 `trusted_team_only`，Codex/Cursor admin 会话仍拥有各自的原生文件/命令能力；应用层 Reader 的授权不能证明原生 CLI 已达到恶意租户间的 OS 级文件隔离。因此仅对可信团队 admin 开放此能力，不把原始 `users/{admin}/services/...` 路径或凭据挂入 CLI 工作区。关闭开关阻止**后续读取**，不能从既有模型上下文撤回已经读过的内容。
