# Codex / Cursor 套餐的内部 Service 分发

Service 可选择 admin 已获授权的 Codex / Cursor 连接与模型，用同一配置提供网页、API 和 Service 微信对话。默认 DeepAgents Service 保持兼容。客户端处理原生会话与工具循环，Jellyfish 转换流式事件、检查资源权限并保存聊天和产物。

## 配置

1. 按[超管部署说明](superadmin-console.md)启用 Runtime，并在后端运行环境安装对应 CLI。应用整体部署在 Docker 时，CLI 也装在应用容器内；本功能不新增独立容器。
2. 超管在 `/superadmin` 登录供应商账号、探测模型，并将指定模型授权给 admin。
3. admin 进入「设置 → Service 管理」（`/settings/services`），创建或编辑 Service，选择 Codex / Cursor、连接和模型，再勾选开放的文档、脚本和能力。
4. 创建「使用已授权套餐」的 Service Key，供内部成员访问网页或调用 API；需要微信时启用该 Service 的微信渠道。入口和 API 示例沿用 Service 详情页。

模型与连接由 admin 固定在 Service 配置中；调用方不能用请求参数越过模型授权或提交自己的供应商凭据。套餐 Service 不接受 BYOK Key。原有 BYOK Service 改成套餐引擎后，旧 BYOK Key 也不能继续调用。

无需额外环境变量。并发、队列和预热沿用 `JELLYFISH_RUNTIME_*` 配置。源码更新不代表已经发布新桌面安装包，也不代表服务器已部署。

## 资源范围

| 资源 | Service 访客可用范围 |
| --- | --- |
| 文档 | admin 明确开放的文件或目录；`*` 表示全部文档，空列表表示不开放 |
| 脚本 | 仅允许执行已开放脚本；每次只复制开放的脚本和文档作为输入，使用现有受限脚本执行器 |
| 生成文件 | 当前 Service 的当前对话目录，供网页预览、API 下载和微信媒体发送 |
| 历史记忆 | 仅当前对话；不会自动注入 admin 的私有长期记忆 |
| 角色设定 | 保留 Service 选择的 System Prompt / User Profile；admin 应确认其中内容适合分发 |
| 账号凭据 | 由超管和 Runtime 管理，不提供给访客 |

路径由服务端绑定身份并校验，模型无法指定其他 admin、Service 或对话。文档和脚本拒绝目录穿越及本地符号链接。原生命令与文件读写不向 Service 开放；文件操作走注册的 `jellyfish_service_*` 工具。供应商原生产物归档到当前对话。

标准模式面向可信内部成员。Service Key 是现有共享访问边界，不是每个成员的独立登录权限；持有有效 Service Key 的调用方可访问该 Service 开放的会话，不能据此区分同一 Service 的不同内部成员。微信保留扫码用户对应的会话绑定。目录范围、CLI 权限及脚本检查不构成恶意租户间的操作系统隔离，不能据此作为公开 ToC 套餐网关。

## 会话、流式输出与撤权

- 网页、API、微信通过同一 Service Agent 工厂使用 Runtime 队列，同一连接并发为 1。排队与 admin 主聊天共享账号配额和上限。
- 每个 Service 对话绑定自己的原生会话。连接、模型、开放资源或能力变更后，下次请求新建原生会话；已保存的 Jellyfish 聊天仍在，可通过当前对话历史工具读取。不同渠道 / Key 不复用原生会话。
- 同一连接的客户端可热复用。Cursor 切换管理员与 Service 权限类别，或改变原生搜索 / 生图权限时，需要重建客户端；同一权限类别的连续请求保持热复用。Codex 按原生线程配置权限。
- 文本逐段输出，工具事件转换为 Jellyfish 的工具显示。API 流的 chunk 带 `conversation_id`，可用于后续请求；失败输出 `error` 后结束流，不伪装成正常回答。供应商不返回 token 统计时，用量为 `null`。
- 下线 Service、删除 Key、撤销 admin 授权、关闭微信渠道或改变权限配置，会在持续授权检查中终止对应排队 / 运行任务。浏览器或 API 流断开会取消该次 Service 任务。脚本若正在执行，会先等待有时间上限的执行器退出。
- 连接失效、额度不足或任务失败不会偷偷切换其他连接、DeepAgents 或付费生成 API。

## 能力边界

支持文本与现有 Base64 图片输入、开放文档读取、脚本运行、当前对话文件和历史。原生 Web Search / 生图仅在 Service 勾选相应能力时启用，具体支持取决于客户端、模型和账号。联网不调用原有额外付费搜索供应商。

套餐 Service 暂不支持定时任务、语音或视频能力；界面禁用这些选项，后端也拒绝保存。需要这些功能的 Service 继续使用 DeepAgents。管理员个人微信入口的引擎路径未改变。

## 本次验证（2026-09-18）

- 128 项 Runtime 测试、32 项 BYOK 断言、本地存储 22 项及模拟 S3 存储 48 项通过，前端生产构建通过。
- 浏览器使用模拟 API 验证 Codex Service 创建、Cursor 切换保存、DeepAgents 兼容、模型选择与套餐 Key 禁用 BYOK，未出现页面运行错误。真实供应商协议另行验证，未将界面模拟视为线上端到端测试。
- HTTP 回归覆盖网页 SSE、API 流式与非流式、授权 / Key 撤销、配置变更、文档与脚本范围、产物归档及错误处理。微信桥接使用真实入口代码，但消息和媒体投递被模拟，没有向微信用户发送测试消息。
- 真实本机 Codex 与 Cursor 均完成开放文档读取、原生会话续聊和范围外文件请求拒绝。测试只使用临时 Service 和合成文档，未公开真实凭据或工作文件。
- 单次实测：Codex 热续话首段约 2.8 秒、Cursor 约 3.0 秒；首次客户端加文档工具调用分别约 10.6 秒、20.5 秒。样本很小，不能保证所有模型、网络或排队情况下的延迟。
- Service 范围下的真实搜索、生图、微信终端收发，以及 Linux / EC2 上的完整套餐聊天尚未验收。本机读取拒绝测试不是恶意租户隔离证明。

回归命令：

```bash
python -m unittest discover -s tests -p 'test_runtime*.py' -q
python tests/test_byok.py
python tests/test_storage_local.py
python tests/test_s3_storage.py
cd frontend && npm run build
```
