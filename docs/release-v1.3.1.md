# OpenJellyfish v1.3.1

## 更新内容

- **授权套餐的内部 Service 分发**：可将 admin 已获授权的 Codex / Cursor 连接和模型用于 Service 网页、API 与 Service 微信，沿用队列、流式输出、会话续聊、撤权和产物归档。Service 固定连接与模型，并限定开放文档、脚本和能力；套餐 Key 不支持 BYOK。
- **管理员 YOLO 修复**：Codex / Cursor 遵循管理员所选审批模式，同时保留 Service 资源边界。
- **聊天与文件体验**：统一输入区、连续工具进展和左侧问答导航；恢复文件预览、编辑差异及完整工具详情；修复归档更新后的内联文件顺序。
- **工作区与设置**：调整登录、导航、规则与记忆、供应商、备份恢复、语音、微信、服务及任务页面，改善深浅主题、窄屏、草稿保留、失败提示和重试体验；大型预览与页面按需加载。
- **发布资料**：更新 README、中英文用户 / 开发指南和 `.env.example`；启动器及两份锁文件统一为 1.3.1。

## 升级与范围

按[超管部署说明](superadmin-console.md)启用 Runtime 并安装所选 CLI；在超管控制台完成供应商登录和模型授权，再在 Service 中选择连接、模型及开放资源。仅使用授权 CLI 连接时无需填写 LLM API Key；DeepAgents 及额外 API 能力仍需相应凭据。本版不新增环境变量。

套餐分发只面向可信内部成员。运行条件仍为 macOS / Linux、单 API worker、`trusted_shared + local`；Service Key 与目录范围不构成恶意租户之间的操作系统隔离。套餐 Service 暂不支持定时任务、语音或视频，管理员个人微信保留原执行路径。详情见[套餐 Service 分发](runtime-service-distribution.md)。

Windows 安装包继续提供原有 DeepAgents / API 工作流；不将 Windows 安装包构建成功视为 CLI Runtime 支持。

## 验证与安装包

发布准备通过：142 项 Runtime 回归、14 项聊天呈现测试、32 项 BYOK 断言、22 项本地存储与 48 项模拟 S3 存储检查，以及 TypeScript / Vite 生产构建。前端仍有既有大 chunk 提示。

此前真实供应商验证范围见[Service 分发验收记录](runtime-service-distribution.md)。本次不将模拟测试或构建当作真实微信终端收发、生产部署或目标设备安装验收。

公开仓 `v1.3.1` 标签触发 Windows x64、macOS Apple Silicon 和 macOS Intel 安装包构建；是否可下载以 [GitHub Release](https://github.com/LiUshin/OpenJellyfish/releases/tag/v1.3.1) 附件为准。

## English summary

v1.3.1 adds authorized Codex / Cursor plans to trusted internal Services over web, API and Service WeChat; fixes admin YOLO approval behavior; restores file previews, edit diffs and inline artifact order; and improves chat, navigation and settings workflows. README, bilingual guides and the sample environment file are updated.

Plan-backed Services use a fixed authorized connection/model and scoped resources, reject BYOK, and currently exclude scheduled tasks, voice and video. Runtime still requires macOS/Linux and a single API worker; it is not hostile-tenant OS isolation. Windows installers retain the existing DeepAgents/API workflow. Installer build success does not replace installation or live-provider acceptance testing.
