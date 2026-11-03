数据汇总工具 v1.5.21

- 修复 Mac 图库发布、Cloudflare API 和检查更新时的 HTTPS 根证书加载错误，继续严格校验服务器证书和域名。
- Mac 提供 DMG 安装包，分别适配 Apple Silicon 和 Intel。
- 修复 Intel Mac 加密组件与 OpenSSL 动态库冲突造成的无窗口启动故障。
- 重复启动或点击 Dock 图标时恢复主窗口；启动异常会显示提示并记录到本机日志。
- 导出配置支持选择一个或多个菜单，默认选择当前配置，仅包含关联站点与账号；保留加密迁移。
- 保留 Mac 界面配色、字号、滚动与图标修复。

安装与更新：

- Windows 推荐 `data-summary-tool-setup-v1.5.21.exe`；便携版为 `data-summary-tool-windows-v1.5.21.zip`。
- M 系列 Mac 使用 `data-summary-tool-macos-arm64-v1.5.21.dmg`。
- Intel Mac 使用 `data-summary-tool-macos-x86_64-v1.5.21.dmg`。
- Mac 打开 DMG 后将应用拖入 Applications，从“应用程序”启动，再推出 DMG。
- 更新前退出旧程序。程序替换保留用户数据目录中的配置。旧版无法检查更新时，请先手动安装新版。

验证包括 205 项 Python 测试、29 项网页测试、真实打包程序的可见窗口与重复启动检查、DMG 挂载和复制安装检查，以及系统默认 CA 路径不可用时的真实 Cloudflare HTTPS 校验。每个最终文件均附有 GitHub 构建来源证明。

Mac 包使用临时签名验证完整性，尚未经过 Apple Developer ID 签名和公证；DMG 不替代 Apple 公证。首次打开如被系统拦截，请按系统“隐私与安全性”提示确认，不要关闭系统安全检查。
