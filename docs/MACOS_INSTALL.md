# macOS 安装与签名状态

当前构建使用 **ad-hoc 签名，尚未经过 Apple 公证**。这能校验包是否完整，
但不能向 macOS 证明开发者身份。首次从浏览器下载后，Gatekeeper 仍可能拦截。
请勿把这类构建描述为已经通过 Apple 认证或可无提示安装。

## 安装

1. Apple Silicon（M 系列）选择 `aarch64.dmg`；Intel 选择 `x64.dmg`。
2. 从官方 GitHub Release 下载 DMG 和对应 `.dmg.sha256`，放在同一目录。
   用 `shasum -a 256 -c OpenJellyfish_<版本>_aarch64.dmg.sha256` 校验。
3. 打开 DMG，将 OpenJellyfish 拖到“应用程序”，再从“应用程序”打开。
4. 如果 macOS 因未验证开发者而阻止打开，确认下载来源与校验和后，
   在“系统设置 → 隐私与安全性”中允许打开。Apple 的说明：
   <https://support.apple.com/102445>。
5. 如果系统仍明确提示“已损坏”，请记录 macOS 版本、安装包名称、
   SHA-256 和完整错误文字。不要关闭 Gatekeeper，也不要对来源不明的软件移除隔离属性。

## 数据位置与升级

新版 Mac 启动器把运行目录放在
`~/Library/Application Support/com.openjellyfish.launcher/`：

- `shared/` 保存 `.env`、`config/`、`users/`、`data/` 和 `logs/`。
- `runtimes/<内容摘要>/` 保存该构建的运行代码、Python 和 Node，链接到共享数据。
- `.app/Contents/Resources` 保持只读；首次复制未完成会在下次启动重试。
- 同版本重打包也按内容摘要隔离，升级不覆盖共享数据或正在使用的旧运行目录。
  此修复不自动清理旧运行目录。

**从旧版本升级前先停止服务、使用启动器备份数据，并保留旧 app 副本。**
旧版曾把数据写入 `.app/Contents/Resources`，直接替换旧 app 会把这些数据一起替换。
首次初始化可复制当前 app 中保留的旧数据，但不能找回已经被覆盖的旧安装目录。
恢复旧备份时应恢复到新版的共享数据目录。

## 发布验收

`build.py` 对内嵌 Mach-O 文件逐个签名，再由 Tauri 封装并签名 app。
构建完成后只读挂载最终 DMG，执行镜像校验、app 与内嵌二进制严格签名校验、
目标架构校验，以及 Python 原生模块和 Node 的启动测试。全部通过才生成 SHA-256 并上传。
`npm ci` 固定 Tauri CLI 到已提交的 lockfile。

本仓库当前没有 Developer ID 证书及公证配置。未来正式签名需要付费 Apple Developer
账号、Developer ID Application 证书及公证凭据；届时应升级内嵌运行时签名流程，
为 Node/Python 配置经过验证的 hardened runtime 权限，完成公证和 stapling，
并用 `macos_release.py --require-notarized` 检查 Gatekeeper。不能只替换最外层签名。
参考：<https://v2.tauri.app/distribute/sign/macos/>。
