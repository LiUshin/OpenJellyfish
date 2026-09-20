# 主机超管控制台

访问前端 `/superadmin`。CLI / 单服务器与打包 App 共用这个页面和 `/api/superadmin/runtime` API。超管是主机身份 `host:owner`，不能注册成普通用户；所有 admin（包括原先绑定为 owner 的账号）只使用获授权的连接。

## 启动与 key

### 直接部署 / 本机

在运行后端的项目目录执行（激活项目虚拟环境后，`python` 也可用）：

```sh
python3 launcher.py --superadmin-key        # 查看 key；不启动服务
python3 launcher.py --rotate-superadmin-key # 换 key；旧页面下一次请求即失效
```

key / help 命令使用标准库，已验证 Python 3.9 无第三方依赖的环境。启动完整应用仍遵循 README 的 Python 3.11+ 要求。

### Docker Compose 部署

默认服务名为 `openjellyfish`，使用容器中的 Python 与部署环境读取 key：

```sh
docker compose exec openjellyfish python launcher.py --superadmin-key
```

新版镜像设置 `JELLYFISH_SUPERADMIN_KEY_FILE=/app/data/superadmin.key`，复用现有 `./data:/app/data` 持久化挂载；重建容器保留 key，构建上下文排除宿主的管理 key。旧镜像把 key 放在容器的 `/app/config/superadmin.key`，升级重建前可保留原 key：

```sh
docker compose exec openjellyfish sh -c 'if [ -f /app/config/superadmin.key ] && [ ! -e /app/data/superadmin.key ]; then cp -p /app/config/superadmin.key /app/data/superadmin.key; fi'
```

网页和 CLI 必须使用同一部署的 key。手动指定 `JELLYFISH_SUPERADMIN_KEY_FILE` 时，将其作为进程环境变量同时提供给后端与 CLI；相对路径按项目根目录解析。

直接运行 uvicorn 时启动钩子也会生成 key。后端单独启动时还需要前端服务，网页入口由 Vite / Express 提供。服务器远程访问使用 HTTPS；key 是主机管理凭据，不是注册链接或 admin token。

直接部署时 key 随机生成并保存于 `config/superadmin.key`（Docker 使用上述 data 路径），POSIX 文件权限 600；在每次管理请求时读取、常量时间比较，轮换无需重启。浏览器只在页面内存持有 key，刷新、退出或关闭后重新输入；不进 URL、cookie 或 localStorage。失效只影响控制台管理权限，不会退出供应商或终止已授权聊天。文件不存在或权限异常时管理 API 拒绝访问。打包启动器有“打开超管页面”和“查看主机 key”，备份排除此文件。

## Docker 中启用 Cursor

环境开关只启用 Jellyfish 的接入能力；后端还需要能执行 Cursor CLI。
宿主机安装的 CLI 不会自动进入容器。默认镜像包含 Git / procps，Cursor 仍为可选下载。

在 `.env` 中设置（不要把本机 macOS 的 CLI 路径复制到 Linux 容器）：

```dotenv
JELLYFISH_RUNTIME_ENABLED=1
JELLYFISH_RUNTIME_ACCESS_MODE=trusted_shared
JELLYFISH_RUNTIME_BACKEND=local
JELLYFISH_CURSOR_CLI_VERSION=2026.09.15-d2fe57e
JELLYFISH_RUNTIME_CURSOR_BIN=/usr/local/bin/cursor-agent
```

然后重新构建并更新应用容器；版本变量是构建参数，只重启容器不会安装 CLI：

```sh
docker compose up -d --build openjellyfish
docker compose exec openjellyfish sh -c 'cursor-agent --version && git --version && ps -p 1 -o pid='
docker compose exec nginx nginx -s reload
```

重新加载 Nginx，使它重新解析重建后的应用容器地址。

构建按镜像架构安装官方固定版本（Linux x64 / arm64），CLI 位于 `/opt/cursor-agent/`；
账号仍在超管网页逐个授权，不需要进入容器执行 `agent login`。
连接数据和授权缓存由 `/app/data` 卷持久化，重建镜像保留。
如使用旧版容器内 key，请先按上文迁移 key，再重建。

### 登录弹出空白页 / 502

查看浏览器 Network 中 `/api/superadmin/runtime/profiles/.../login` 的 Response：

- JSON `detail` 为“无法启动 Cursor”时，旧版接口会返回 502；优先检查容器内 CLI 路径和执行权限。
- 新版依赖缺失返回 503 和具体安装提示，弹窗显示准备状态或错误；登录开始前检查 CLI、Git 和 ps。
- JSON 提示登录地址超时或进程提前退出时，检查 CLI 版本、依赖和服务器到 Cursor 的网络连接。
- 如果返回 HTML 网关错误页，检查对应时间的反向代理和后端日志；健康接口正常不能排除单个请求的代理故障。

官方安装和协议：[Cursor CLI 安装](https://cursor.com/docs/cli/installation)、[ACP](https://cursor.com/docs/cli/acp)。
Linux 安装/启动检查不等同于真实账号登录、续话和工具调用的线上验收。

## Docker 中启用 Codex

Codex 与 Cursor 使用同一套超管连接和授权管理，也可独立选择安装或同时安装。
在上面的 Runtime 公共配置之外，向 `.env` 增加：

```dotenv
JELLYFISH_CODEX_CLI_VERSION=0.154.0
JELLYFISH_RUNTIME_CODEX_BIN=/usr/local/bin/codex
```

然后重建并检查：

```sh
docker compose up -d --build openjellyfish
docker compose exec openjellyfish sh -c 'codex --version && git --version && ps -p 1 -o pid='
docker compose exec nginx nginx -s reload
```

构建通过官方 `@openai/codex` npm 包安装指定版本及对应平台二进制，固定在 `/opt/codex-cli/<版本>/`，
入口为 `/usr/local/bin/codex`。版本变量留空时不下载，现有 Cursor 构建选项继续生效。
只接受完整版本号，不使用 `latest` 或版本范围，以免重建时静默升级协议。

进入 `/superadmin` 创建 Codex 连接，选择 **设备码登录（服务器）**，打开官方链接并输入页面显示的设备码。
设备码方式适用于 EC2，不依赖用户浏览器访问服务器的 localhost 回调；也无需在容器中手动执行登录命令。
账户缓存继续由 Jellyfish 管理，保存在现有 `/app/data` 持久化卷；镜像不包含主机登录信息。
缺少 CLI / ps 时使用与 Cursor 相同的部署错误提示。

官方依据：[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)、[App Server 登录协议](https://learn.chatgpt.com/docs/app-server#auth-endpoints)。

本次验证：111 项 Runtime 回归与四种客户端构建参数组合通过。Linux x64 测试镜像中两种 CLI 共存；
ARM Docker 仿真下以 UID 1000、独立 HOME 运行真实 CodexAdapter，初始化 App Server、取得设备码、
取消登录及进程退出通过。未授权真实账号，未做稳定版真实聊天 / 模型切换 / 生图或 EC2 验收。
完整生产镜像此前受本地 Debian 下载源 503 影响，本次在已有 Linux 基础镜像验证同一安装脚本。

## 使用

1. 超管输入 key，创建 Codex 或 Cursor 连接，在官方网页完成授权。
2. 探测可用模型、预热；选择现有 admin 和允许模型后保存授权。
3. admin 设置页显示团队连接，可以选择默认引擎和在对话中切换已授权模型；不能登录、探测、断开或重新分配供应商账号。
4. admin 在「设置 → Service 管理」中选择获授权的连接与模型，发布给内部可信成员；网页、API 和 Service 微信共用该配置。见[套餐 Service 分发](runtime-service-distribution.md)。

本次网页控制台提供连接与授权管理；打包启动器已有的注册码、账号、用量和系统运维界面继续保留，尚未全部迁到网页。

## 旧数据迁移

首次打开运行数据时，在同一 SQLite 事务内将旧连接转为主机所有。凭据 vault、profile ID、原生会话 ID 和文件目录不变。旧连接所有者若仍是有效 admin，会获得该连接已存在模型的一条显式授权，旧会话补齐授权绑定；已有授权/撤销记录优先，不会自动恢复撤销。迁移幂等；后续新增 admin 没有默认授权，新增模型也须显式授权。历史 run 保持原快照便于审计。

标准模式面向可信团队。指定工作目录不构成操作系统隔离；Docker / 集群后端仍未启用，不能因为有独立超管页面就宣称支持不互信租户执行。

## 本次验证（2026-09-17）

107 项 Runtime 测试、22 项存储检查和前端生产构建通过。真实本机迁移后两条供应商连接保持 ready，原 admin 只收到授权后的模型目录；旧 Codex / Cursor 会话续聊均成功。浏览器已核实独立登录、无效 key 拒绝、刷新重登和 admin 管理按钮移除；有效 key 的管理 API 已实测，完整登录后控制台操作尚未做浏览器验收。

打包启动器 Rust/JavaScript 语法检查通过；完整 Cargo 编译因离线依赖缺失、在线注册表更新未完成而未验收，未发布新桌面安装包。
