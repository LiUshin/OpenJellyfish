# OpenJellyfish v1.3.0

## 本次源码更新

- 管理员主聊天可选择 DeepAgents、Codex 或 Cursor；外部引擎支持授权模型切换、流式文本与工具、审批、停止、附件及产物归档。
- 独立 `/superadmin` 主机控制台管理供应商连接、登录与 admin 模型授权；连接级客户端预热与复用，旧 owner 数据自动迁移。
- Docker 可通过固定版本构建参数独立选装 Codex / Cursor CLI；主机管理 key 与 Runtime 状态持久化。
- Service 按 API Key 区分 hosted / BYOK，支持模型发现；新增硅基流动适配和 R2 / S3 兼容配置。
- 更新 `.env.example`、中英文用户 / 开发指南及标准模式说明；启动器版本与两份锁文件统一为 1.3.0。

## 升级入口

按 [超管部署说明](superadmin-console.md) 安装需要的 CLI，参考 [标准模式](runtime-standard-mode.md) 启用、授权与恢复。旧 DeepAgents 会话保持原执行路径。标准模式默认关闭，仅支持 macOS / Linux、单 API worker、可信团队共享本机执行；整套应用运行在 Docker 内不等于每租户容器隔离。

## 验证范围

发布准备验证：111 项 Runtime 回归、4 项主机 key CLI 测试、本地存储 22 项、S3 模拟客户端 48 项、BYOK 32 项断言、硅基流动 58 项，以及应用前端与官网生产构建通过。前端仍有较大 bundle 提示。

本轮未做新的供应商真实账号、EC2、完整生产 Docker 镜像或桌面安装包验收。历史 CLI / 原生能力验收范围见 [Cursor 状态](runtime-cursor-status.md)、[流式聊天](runtime-streaming-chat.md) 与 [超管部署说明](superadmin-console.md)。本次发布源码到 main，不创建桌面 Release 标签或安装包。
