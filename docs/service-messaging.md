# Service 反馈、回复与直接通知

本模块提供持久化反馈、管理员回复、明确正文的直接通知。它与“让 Service Agent 执行任务”分开；接收反馈不再创建具有管理员工具权限的 Agent。

## 使用入口

- **消费者反馈**：网页、API、微信中的 Service Agent 调用 `contact_admin`，创建持久 case 和管理员提醒；DeepAgents 与已授权的 hosted Codex / Cursor Service 都使用服务端绑定的联系工具。
- **管理员回复**：设置 → 收件箱查看反馈与投递明细，回复固定回到原消费者会话；管理员微信也支持下文的明确文字命令。
- **直接通知**：Service 管理 → 广播，填写确定正文并勾选已有会话，可立即或指定时间发送。每个目标单独记录历史投影和所需的微信投递，不默认全发。
- **Service Agent 任务**：需要模型完成工作时使用定时任务。该路径使用 Run、执行授权和调度 outbox，只有 `send_message` 意图产生消费者消息；模型普通说明和运行异常不会作为兜底内容投递。当前任务执行不支持脚本、CLI、联网或媒体适配，也不支持 Codex / Cursor Service 任务。直接文字通知不需要这些执行能力。

## 数据与状态

`users/.scheduler/executions.sqlite3` 是消息的事实源，使用独立的 `sm_*` 表，不改动 Run ledger 的表和执行状态。

- `sm_cases`：反馈的原 owner、Service、conversation、渠道和接收绑定。`open/acknowledged/replied/resolved` 是业务状态；`read_at` 是独立阅读状态。`replied` 表示已创建回复，不代表对方已读或微信已经确认。
- `sm_messages`：消费者反馈、管理员通知、管理员回复的正文与作者。消息 ID 稳定。
- `sm_deliveries`：每条消息分别记录本地历史投影和微信发送。状态为 `pending/inflight/retry_wait/delivered/cancelled/unknown`。渠道确认不等同于最终用户阅读。
- `sm_message_events`：本地历史成功投影后，同事务分配可见事件游标，`message_id` 唯一。创建较早、稍后投影的消息仍会取得新游标，不会被客户端跳过。
- `sm_requests`：owner 范围的幂等键。同键不同内容拒绝，重复合法提交返回原记录。
- `sm_broadcasts`：通知正文、受众消息、可选指定时间与聚合投递状态。

反馈和管理员提醒、回复和其投递意图、广播和逐收件人意图分别在同一 SQLite 事务内提交。消费者历史 JSONL 是幂等投影，使用 `event_id=message.id`，并保存管理员作者及 case/broadcast 关联。

旧 `inbox_*.json` 在管理员首次访问收件箱时幂等导入；原文件不删除，旧 `_index.json` 不作为事实源。旧记录无法判断网页/API来源时显示 `unknown`；如果会话确认为微信却缺少可验证的原绑定，拒绝推测收件人。旧 `handled` 保留在 `legacy_status`，业务状态迁移为 `acknowledged`，不解释为已回复或已解决。被删除 case 保留 tombstone，重复迁移不会复活。旧记录缺少可靠投递回执，因此不会批量重发历史微信提醒。

## 回复与权限

`POST /api/inbox/{case_id}/replies` 接收 `{message, idempotency_key}`。owner 来自管理员登录身份；目标从 case 的原绑定读取，不接受客户端替换 owner、Service、conversation 或微信用户。HTTP body 中未知目标字段会被拒绝。

管理员也可在自己的已绑定微信发送明确命令：

```text
回复 inbox_反馈编号：回复正文
```

该命令在模型调用之前处理，仅支持文字。服务端核对微信发送人、case owner 与原消费者绑定，用微信真实 `message_id` 幂等。缺少消息编号时拒绝提交并提示使用网页收件箱。确认只说明“已记录并排队”，不保证消费者已经收到。其他聊天继续使用既有管理员 Agent。

网页和 API 的现有 Service Key 是 **Service 级凭据**，不是每个人的登录身份。增量消息接口沿用这一授权范围，不能声称已实现逐消费者隐私隔离。微信会话继续绑定单个原会话及接收人；重新扫码后旧消息不会静默转给新的会话。

## 直接通知 API

这些接口使用管理员登录身份，不能使用消费者 Service Key 创建通知。创建前核对 Service 所属管理员，并从选中的已有会话冻结渠道、会话及微信接收绑定。

| 操作 | 接口 |
|---|---|
| 创建通知 | `POST /api/services/{service_id}/broadcasts` |
| 列表 / 详情 | `GET /api/services/{service_id}/broadcasts` / `GET /api/services/{service_id}/broadcasts/{broadcast_id}` |
| 取消未开始投递 | `POST /api/services/{service_id}/broadcasts/{broadcast_id}/cancel` |
| 重试 | `POST /api/services/{service_id}/broadcasts/{broadcast_id}/retry`，正文 `{allow_unknown: false}` |

创建正文为 `{message, conversation_ids, idempotency_key, scheduled_at?}`。HTTP 接口正文最长 8000 字符，目标最多 500 个；`scheduled_at` 必须带时区，省略则立即排队。返回 `202` 说明创建成功，不代表已送达。列表和详情包含 `messages` 与逐对象 `deliveries`；列表在指定 Service 内取最近记录。

同 owner 的幂等键必须稳定复用：相同内容重复提交返回原记录，同键不同内容返回冲突。取消只覆盖 `pending/retry_wait`，不能撤回 `inflight/delivered`。重试只覆盖待重试及明确确认的 `unknown`，不恢复 `cancelled`。

## 网页与 API 增量消息

独立网页聊天会自动补拉当前会话的后台消息，回到页面前台时继续补拉，按消息 ID 合并历史。API 客户端需要主动调用：

```http
GET /api/v1/conversations/{conversation_id}/events?after=0&limit=100
Authorization: Bearer <Service Key>
```

响应含 `events`、`next_cursor`、`has_more`。事件形状为 `{id, seq, type: "message", conversation_id, message}`，其中 `id == message.id`；历史消息的 `event_id` 也使用同一 ID。客户端成功处理后保存 `next_cursor`，下次传给 `after`；`has_more=true` 时继续读取，重复事件按 ID 去重。首次或本地游标丢失可从 0 重放。

该接口返回已经完成本地历史投影的管理员回复与直接通知；可见游标在投影完成时分配，创建更早但晚投影的消息不会被后来的游标跳过。事件可见不代表微信已确认。当前聊天 POST 的 SSE 和 OpenAI 兼容流只覆盖单次聊天请求，不订阅未来后台消息；集成方需接入此扩展接口，当前未提供通用 webhook。

## Worker 与故障语义

应用启动完成微信恢复后，启动独立 Service message worker；它不依赖调度器 Run。向消费者发送前复查 owner、Service 发布状态、会话存在以及微信渠道/会话准入；管理员提醒另行核对管理员账号与其微信绑定。

- 未连管理员微信：提醒保持待重试，反馈本体始终可从收件箱读取；不会被标为“已处理”。
- 发送前的暂时连接或存储错误：记录 `retry_wait`，退避重试；权限撤销、对象删除等确定性拒绝转为 `cancelled`。
- 微信明确返回非零错误码：记录 `cancelled` 和拒绝原因，不伪装成成功。
- 外部网络发送已开始但缺少成功回执：记录 `unknown`，不自动重发。人工重发必须明确 `allow_unknown=true`，并理解可能重复。
- 进程退出：待执行意图保留。过期的本地历史投影租约可以安全恢复；过期的微信发送租约转为 `unknown`。租约为 150 秒，避免另一进程启动时抢走仍在执行的工作。
- 广播聚合重试只重试等待重试/明确确认的未知结果，不复活此前人工取消的收件人。
- 已取消、停用或会话重绑：不会自动改收件人；外部已在途调用不能承诺撤回。
- 定时通知使用带时区的 `scheduled_at`，不会阻塞同一会话中其他立即通知。

`DISABLE_SERVICE_MESSAGING=1` 可禁用该 worker（反馈/消息仍可持久提交）。`SAFE_STARTUP` 同时禁用它；仅设置 `DISABLE_SCHEDULER` 不会禁用消息投递。

当前回复和直接通知是文字消息；媒体消息需要不可变附件快照及单独投递适配，不能通过可变本地路径临时拼接。API 提供增量拉取，不包含通用 webhook。

## 迁移与备份边界

消息 worker 不会重放旧 JSON 的历史通知，也不会把旧 `handled` 当作真实回复回执。原文件保留供核对；恢复旧文件不会覆盖已存在的 case，也不会复活 tombstone。数据库迁移与回复只信任可验证的原会话绑定，缺失绑定需要人工处理，不能借新微信会话接管旧目标。

`users/.scheduler/executions.sqlite3` 同时包含运行记录和消息事实源。按用户模块导出的 ZIP 不能代替它的备份；应停机后备份数据库，或使用 SQLite 一致性备份，避免写入期间只复制主数据库文件而遗漏 WAL。恢复时还需保留对应用户、Service、消费者会话和密钥配置。不要只恢复 inbox JSON 后把缺失的投递记录视为未发送。

## 回归

```bash
python -m unittest tests.test_service_messaging -v
```

测试只使用合成 SQLite 数据、假模型依赖和假微信传输，覆盖事务故障、权限绑定、重复请求、旧数据迁移、未知投递、重启恢复、乱序游标、指定时间、微信明确回复及跨 owner 拒绝。测试通过不代表已通过真实 iLink 与真实账号端到端验收。
