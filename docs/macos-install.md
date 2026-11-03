# Mac 安装和启动检查

按“关于本机”的芯片选择安装包：Apple Silicon（M 系列）用 `arm64.dmg`，Intel 用 `x86_64.dmg`。

打开 DMG 后，将“数据汇总工具.app”拖到 Applications。复制完成后，从“应用程序”打开，再推出 DMG。更新时先退出旧版；用户配置保存在 `~/Library/Application Support/sheets-post-filter`，替换应用保留原配置。

本构建使用 PyInstaller 的临时签名，并在 Mac 构建机验证完整性；尚未使用 Apple Developer ID 签名及公证。DMG 是安装载体，不能替代 Apple 公证。若 macOS 明确提示开发者无法验证，按照系统“隐私与安全性”中的提示由用户决定是否打开；不要关闭系统安全检查。

如果图标出现但没有窗口，点击 Dock 中的应用图标会重新显示主窗口。发生 Python 启动错误时会显示提示，并将诊断写到用户数据目录的 `desktop_error.log`；只在本机查看，不要未经脱敏公开日志。

每份安装包构建必须通过：

- 真实打包程序启动，主窗口可见，配置导出选择界面可创建。
- `.app` 的签名完整性检查。
- DMG 校验、只读挂载、应用与 Applications 快捷方式检查。
- 从 DMG 复制安装应用，重新检查签名并启动验证。

上述检查验证构建机上的启动，不代表已覆盖所有旧 macOS 版本或用户已有配置。仍无法启动时，请提供 macOS 版本、芯片类型及启动错误提示，继续定位。
