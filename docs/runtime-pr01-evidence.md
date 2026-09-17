# PR 01 可行性证据（2026-09-16）

> 历史设计 / 阶段验收记录：文中的分支、角色与未实现项以记录当时为准。当前 v1.3.0 使用 [标准模式](runtime-standard-mode.md) 和 [主机超管控制台](superadmin-console.md)；早期验证结果不代表当前完整生产验收。

## 已实际通过：本机共享身份

运行 `scripts/verify_runtime_identity.py`，使用本机 Codex app-server，模型 `gpt-5.6-sol`。同一份专用 ChatGPT 登录缓存依次供给两套私有 HOME；每轮退出进程后回收刷新的缓存，再删除工作 HOME 的凭据。两套会话分别保存随机标记，恢复第一套目录并续话，验证返回正确标记。输出不包含账号和凭据。

结果：`local_shared_identity=true`、`separate_provider_threads=true`、`actor_state_restore=true`、`auth_cache_removed=true`。三次调用总耗时分别 19.33、23.44、8.21 秒（不是首 token 延迟）。这是一个真实供应商身份、两个执行者状态的验证，不是两个供应商账号或 OS 隔离验证。

生产实现采用文件缓存 checkout/checkin，连接级单写锁保护 refresh token；账号主体变化时增加 auth_generation 并要求重新授权。仅复制凭据，不复制宿主配置或全部 HOME。

本机生成的 JSON schema 将 `chatgptAuthTokens` 标为内部不稳定接口，故不采用该接口。采用公开的 account/login/start、account/read、account/logout 与 file 凭据存储协议。

参考：[官方 App Server](https://learn.chatgpt.com/docs/app-server)、[官方鉴权说明](https://learn.chatgpt.com/docs/auth)。具体集成须跟随锁定的 CLI 协议回归。

## 未测，保持关闭

- Cloudflare：没有部署资源，未执行真实销毁/恢复和个人订阅鉴权。
- 独立供应商账号：没有第二个账号的登录操作，不以同账号成功替代。
- Cursor 无头登录：未接入。
- 原生生图：尚无本次真实图片文件验收；不开放此能力，不自动转付费 API。

本机共享可继续进入 PR 02–06；以上未测项不作为已交付能力。
