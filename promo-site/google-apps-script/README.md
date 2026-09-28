# Google Apps Script（图片分析表 → JSON 缓存）

原 Vercel 部署包未附带 `.gs`，已从同源工程补回并加上 **J/AB 列**。

## 文件

- `drive-cache-manifest-export.gs` — **日更导出主脚本**（你贴的这份）：跑 `qGalleryExportDriveCacheManifest()`
- `google-apps-script-water.gs` — 可选：在线 API / 内表缓存后端
- `appsscript.json` — Apps Script 清单

## 关键列

| Excel | 字段 | 用途 |
|-------|------|------|
| J | `likes` | 点赞排行榜 |
| AB | `designer` | 美工名字，前端显示为 `名字 · 美工` |

源表读取宽度：`SOURCE_READ_COLS = 28`（A–AB）。

## 部署到 Google

1. 打开绑定「图片分析」的脚本项目  
2. 用本目录脚本覆盖对应文件  
3. 重新跑缓存刷新 / `qGalleryExportDriveCacheManifest`  
4. 确认输出 JSON 的每条记录含 `likes`、`designer`  
5. 把 JSON 公共链接填到 Vercel `PUBLIC_JSON_CACHE_URL`
