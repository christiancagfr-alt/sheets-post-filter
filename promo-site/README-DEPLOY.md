# 图库站点 — 部署入口

完整流程（给人看、也给 AI 照着部署）：**[DEPLOY.md](./DEPLOY.md)**  
AI 先读：**[AGENTS.md](./AGENTS.md)**

密钥怎么填进桌面软件：仓库根目录 **[Cloudflare发布密钥说明.md](../Cloudflare发布密钥说明.md)**

## 示例（请换成自己的项目和域名）

| 项 | 示例 |
|---|---|
| Pages | `your-gallery-project` |
| 站点 | https://gallery.example.com |
| 发布接口 | https://gallery.example.com/api/publish-cache |
| R2 桶 | `your-gallery-cache`（绑定名 `GALLERY_CACHE`） |
| CDN | https://cdn.example.com/promo/ |

只更新现有站点代码：

```bash
cd promo-site
npx wrangler pages deploy . --project-name=your-gallery-project --commit-dirty=true --branch=production
```
