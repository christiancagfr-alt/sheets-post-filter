# 旧版 Google Apps Script 兼容文件

正常流程不需要把这里的脚本同步到 Google 表格，也不需要运行 `qGalleryExportDriveCacheManifest`。
桌面软件的「发布图库」会读取表格实际表头，通过 `publish_cloudflare.py` 直接生成 JSON 并发布到 Cloudflare R2；网站部署包会跳过本目录。

本目录仅供仍在使用旧版 Drive / Apps Script 缓存流程的站点维护。新表格水滴时间字段由桌面发布器读取 AC 列表头（`交教会时间`，也兼容 `水滴时间`、`水滴日期`、`交教会日期`），不需要手动部署此处脚本。
