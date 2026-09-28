# 引流数据站 — 部署入口

完整流程（给人看、也给 AI 照着部署）：**[DEPLOY.md](./DEPLOY.md)**  
AI 先读：**[AGENTS.md](./AGENTS.md)**

密钥怎么填进桌面软件：仓库根目录 **[Cloudflare发布密钥说明.md](../Cloudflare发布密钥说明.md)**

## 现网（不要给独立站复用）

| 项 | 值 |
|---|---|
| Pages | `q-gallery-promo` |
| 站点 | https://promo.zhixianglife.com |
| 发布接口 | https://promo.zhixianglife.com/api/publish-cache |
| R2 桶 | `q-gallery-json-cache`（绑定名 `GALLERY_CACHE`） |
| CDN | https://gallery-cache.zhixianglife.com/promo/ |
| 素材库（勿覆盖） | `q-gallery` → https://creatives.zhixianglife.com |

只更新现网代码：

```bash
cd promo-site
npx wrangler pages deploy . --project-name=q-gallery-promo --commit-dirty=true --branch=production
```
