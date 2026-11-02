数据汇总工具 v1.5.19

- 修复 macOS 按钮背景与文字颜色不一致、白底白字等显示问题。
- 使用统一主题按钮，改善悬停、选中、键盘焦点与禁用状态的可读性。
- 统一输入框、下拉框及弹窗的颜色，保留 Mac 字号、触控板滚动与图标修复。
- 保留配置加密导入导出、现有站点复用及原发布密钥恢复功能。

下载按系统选择：

- Windows 安装包：`data-summary-tool-setup-v1.5.19.exe`
- Windows 便携版：`data-summary-tool-windows-v1.5.19.zip`
- Mac（M 系列芯片）：`data-summary-tool-macos-arm64-v1.5.19.zip`
- Mac（Intel 芯片）：`data-summary-tool-macos-x86_64-v1.5.19.zip`

更新前退出旧程序。Mac 解压后用新版“数据汇总工具.app”替换“应用程序”中的旧版；运行配置保存在用户数据目录，替换程序保留原配置。

验证：199 项 Python 测试（包含 3 项真实 Tk 控件测试），29 项网页测试；Windows、Apple Silicon、Intel 构建分别测试并生成最终文件的构建来源证明。
