# promo-site 部署说明

给人和 AI 用的同一份流程。先判断要不要部署，再选方案 A 或方案 B。

相关代码目录：本仓库 `promo-site/`（Cloudflare Pages + Functions + R2）。  
桌面软件「发布图库」会 `POST` 到站点的 `/api/publish-cache`。

---

## 软件里一键部署（推荐给别人用）

数据汇总工具顶部 **设置 → 图库站点**：

1. 点 **登录 Cloudflare**（浏览器打开令牌页，权限已预填：Pages / R2 / Workers Scripts / DNS / Account 读取；账号选全部、区域选全部。创建后把 **API Token** 粘回软件，不要粘 Global API Key）
2. 填写自己的网站域名，例如 `gallery.example.com`
3. 点 **生成密钥并部署**

软件会自动：建 R2 桶、建 Pages 项目、生成发布密钥、换好域名、把发布地址写回第 4 步。域名需要已经加在这个 Cloudflare 账号下，才会自动写 CNAME。

下面的命令行方案留给改代码、或不用这套软件的人。

---

## 0. 先判断：要不要部署

```
只用数据汇总工具做表格？
  └─ 不用部署。软件顶部「设置」加服务账号即可。

要把图库发到已经在跑的站点（例如 gallery.example.com）？
  └─ 不用部署。软件第 4 步填发布地址 + 发布密钥。
     注意：会覆盖这座站现有 CDN 数据。密钥不要给外人。

要自己的独立图库网站？
  └─ 必须部署（方案 B）。

只是更新现有图库的网页/接口代码？
  └─ 方案 A，重新发布 Pages，不要新建项目、不要新建桶。
```

---

## For AI agents

Read this whole file before running commands. Do not invent Cloudflare resources that already exist.

### Non-negotiable

- Working directory for deploy commands: `promo-site/` (this folder).
- Never deploy this folder onto another live Pages project that already serves a different site.
- Never commit secrets, `.wrangler/`, or real publish-secret values.
- Binding name in code is **`GALLERY_CACHE`**. Do not rename the binding.
- R2 object prefix is hardcoded as `promo/manifest.json` and `promo/chunks/`. Do not change JS prefix unless you also change every HTML `Q_GALLERY_DRIVE_CACHE_MANIFEST_URL`.
- `api/*.js` is legacy Vercel. Cloudflare uses `functions/api/*.js`. Deploy with Wrangler Pages, not `vercel`.
- If the user only wants spreadsheet features, stop. No Cloudflare work.
- If the user wants to publish to the existing site, do **Track A** or just tell them to fill URL+secret. Do not create a second project.

### Ask the user (do not guess)

1. Track A (update an existing gallery the user already owns) or Track B (brand-new independent gallery)?
2. Cloudflare account login is available? (`npx wrangler login`)
3. Track B only: new Pages project name, new R2 bucket name, site hostname, R2 public hostname.
4. Track B only: generate or receive the publish secret (do not echo it back in logs/chat if they paste it).

### Track A — update existing production

Keep `wrangler.toml` pointing at the user's existing Pages project and R2 bucket.

```bash
cd promo-site
npx wrangler login
npx wrangler pages deploy . --project-name=<EXISTING_PROJECT> --commit-dirty=true --branch=production
```

Do not recreate the R2 bucket. Do not rotate the publish secret unless asked. Verify:

```bash
curl -sS -o NUL -w "%{http_code}" https://gallery.example.com/
curl -sS -X POST https://gallery.example.com/api/publish-cache
```

POST without secret should return 401 JSON, not 404/500.

### Track B — new independent gallery

1. Copy `promo-site/` (or use this folder for a new independent site).
2. Edit `wrangler.toml`:
   - `name = "<new-pages-project>"` (do not reuse another live site's project name)
   - `bucket_name = "<new-r2-bucket>"` (do not reuse another live site's bucket)
3. Replace hardcoded CDN in these four files with `https://<R2_PUBLIC_HOST>/promo/manifest.json`:
   - `index.html`
   - `leaderboard.html`
   - `likes-leaderboard.html`
   - `water-leaderboard.html`
   Clear or remove `Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL` if it still points at another site's Drive file.
4. Create bucket, deploy, bind (toml binding is enough on deploy), set secrets, attach domains.
5. Tell the user to put this in the desktop app:
   - 发布地址: `https://<SITE_HOST>/api/publish-cache`
   - 发布密钥: the same secret just stored in Cloudflare

Exact commands are in §方案 B.

### Done means

- Pages URL opens the gallery HTML.
- `POST /api/publish-cache` without secret → 401.
- R2 public `GET https://<R2_PUBLIC_HOST>/promo/manifest.json` works after first successful publish (404 before first publish is OK).
- Desktop app can 发布图库 to the new URL with the new secret without touching any other live gallery.

---

## 现有生产环境（方案 A 用你自己的值，方案 B 不要复用）

下面是示例，请换成实际项目和域名：

| 项 | 示例 |
|---|---|
| Pages 项目 | `your-gallery-project` |
| 站点 | https://gallery.example.com |
| Pages 默认域名 | https://your-gallery-project.pages.dev |
| 发布接口 | https://gallery.example.com/api/publish-cache |
| R2 桶 | `your-gallery-cache` |
| R2 绑定名 | `GALLERY_CACHE` |
| 桶内前缀 | `promo/` |
| 浏览器读的 CDN | https://cdn.example.com/promo/manifest.json |

页面路径：`/` 列表，`/leaderboard` 排行，`/likes-leaderboard` 点赞榜，`/water-leaderboard` 其它排行。

---

## 方案 A：更新现有站点（你自己改代码后重新发布）

适合：已经有图库站点（例如 `gallery.example.com`），只是改了 `promo-site` 里的网页或接口。

### 准备

- 本机 Node.js 18+
- Cloudflare 账号能进该图库的 Pages 项目
- 在 `promo-site` 目录执行命令

### 步骤

1. 打开终端，进入目录：

```bash
cd promo-site
```

2. 登录 Cloudflare（浏览器会弹出授权）：

```bash
npx wrangler login
```

3. 发布到现有项目（不要改项目名）：

```bash
npx wrangler pages deploy . --project-name=<EXISTING_PROJECT> --commit-dirty=true --branch=production
```

4. 打开 https://gallery.example.com 看页面是否更新。

5. 软件里的发布地址和密钥**不用改**，除非你主动轮换了发布密钥。

密钥仍在：Cloudflare Dashboard → **Workers 和 Pages** → 你的项目 → **Settings** → **Variables and Secrets** → Production → 发布密钥。

---

## 方案 B：部署一座独立图库（别人或另一套数据）

别人要用自己的图库网站时走这里。结果是**另一座站**，不会覆盖已有图库。

下面用占位符，请换成自己的名字：

| 占位符 | 例子 | 说明 |
|---|---|---|
| `<PROJECT>` | `my-gallery` | Cloudflare Pages 项目名，不要复用其它已上线站点的项目名 |
| `<BUCKET>` | `my-gallery-cache` | 新的 R2 桶，不要复用其它已上线站点的桶 |
| `<SITE_HOST>` | `gallery.example.com` 或 `my-gallery-promo.pages.dev` | 浏览器用的网站 |
| `<R2_PUBLIC_HOST>` | `cdn.example.com` 或 R2 自定义域 | 浏览器直读 JSON 的域名，不要带 `https://` 末尾斜杠 |
| `<SECRET>` | 自己生成的 32 位以上随机串 | 发布密码，只放 Cloudflare 和软件里 |

### B1. 改配置（部署前）

编辑 `promo-site/wrangler.toml`：

```toml
name = "<PROJECT>"
compatibility_date = "2024-11-01"
compatibility_flags = ["nodejs_compat"]
pages_build_output_dir = "."

[[r2_buckets]]
binding = "GALLERY_CACHE"
bucket_name = "<BUCKET>"
```

`binding` 必须仍是 `GALLERY_CACHE`。

编辑这 4 个 HTML，把 CDN 改成自己的（四份要一致）：

- `index.html`
- `leaderboard.html`
- `likes-leaderboard.html`
- `water-leaderboard.html`

```html
window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL =
  "https://<R2_PUBLIC_HOST>/promo/manifest.json";
window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL = "";
```

不要再指向其它站点的 CDN，否则页面仍显示别人的图库。

### B2. 登录并建 R2 桶

```bash
cd promo-site
npx wrangler login
npx wrangler r2 bucket create <BUCKET>
```

### B3. 让浏览器能公开读到桶里的 JSON

桌面软件写入 R2 后，网页**不走** `/api/publish-cache` 读数据，而是直读：

`https://<R2_PUBLIC_HOST>/promo/manifest.json`

做法（Dashboard）：

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **R2** → 打开桶 `<BUCKET>`
2. **Settings** → 允许公开访问
3. 绑定自定义域，例如 `cdn.example.com`（DNS 在同一 Cloudflare 账号最省事）
4. 这个自定义域就是 `<R2_PUBLIC_HOST>`

没有自己的域名时，可用 R2 的 r2.dev 公开 URL，把它整段主机名填进 HTML 和下面的 `PUBLIC_CACHE_BASE`。

### B4. 部署 Pages

```bash
npx wrangler pages deploy . --project-name=<PROJECT> --commit-dirty=true --branch=production
```

第一次会创建项目。记下输出的 `*.pages.dev` 地址，没有自定义域时它就是 `<SITE_HOST>`。

### B5. 环境变量 / 密钥

Dashboard：**Workers 和 Pages** → `<PROJECT>` → **Settings** → **Variables and Secrets** → **Production**

或命令行（会提示输入，不要写进仓库）：

```bash
npx wrangler pages secret put CACHE_PUBLISH_SECRET --project-name=<PROJECT>
npx wrangler pages secret put PUBLIC_CACHE_BASE --project-name=<PROJECT>
```

`PUBLIC_CACHE_BASE` 填 `https://<R2_PUBLIC_HOST>`（无末尾 `/`）。  
接口写进 manifest 的分片 URL 靠这个值。独立站必须设置，否则页面会去读错误的 CDN。

| 变量 | 必填 | 作用 |
|---|---|---|
| `CACHE_PUBLISH_SECRET` | 是 | 和软件第 4 步同一串，发布接口鉴权 |
| `PUBLIC_CACHE_BASE` | 独立站必填 | `https://<R2_PUBLIC_HOST>` |
| `ACCESS_PASSWORD` | 否 | 站点访问密码；空=不校验 |
| `APPS_SCRIPT_URL` | 否 | 旧版 Apps Script 同步；直推 R2 后可空 |
| `APPS_SCRIPT_API_SECRET` | 否 | 同上 |
| `DRIVE_CACHE_MANIFEST_URL` | 否 | 旧版 Drive 镜像上游；直推可空 |
| `PUBLIC_JSON_CACHE_URL` | 否 | 整包 JSON CDN，可选 |

改密钥后要重新填软件，旧密钥立刻失效。Cloudflare 保存后不再显示原文。

### B6. 自定义网站域名（可选）

Dashboard：Pages 项目 → **Custom domains** → 添加 `<SITE_HOST>`。

若域名已在 Cloudflare：加 CNAME，名称是主机头（如 `gallery`），目标 `<PROJECT>.pages.dev`，代理开启。

### B7. 填回数据汇总工具

数据筛选汇总 → 展开第 4 步：

| 栏 | 填 |
|---|---|
| 发布地址 | `https://<SITE_HOST>/api/publish-cache` |
| 发布密钥 | 与 Cloudflare 中同一串 |

保存，点顶部 **发布图库**。第一次成功后，打开：

- 网站：`https://<SITE_HOST>/`
- JSON：`https://<R2_PUBLIC_HOST>/promo/manifest.json`

---

## 给 AI 的命令清单（方案 B 一次性）

把占位符替换后再执行。密钥用 `secret put`，不要出现在命令行历史能看见的明文参数里。

```bash
cd promo-site

# 1. login
npx wrangler login

# 2. bucket
npx wrangler r2 bucket create <BUCKET>

# 3. after editing wrangler.toml + 4 HTML files:
npx wrangler pages deploy . --project-name=<PROJECT> --commit-dirty=true --branch=production

# 4. secrets (prompted)
npx wrangler pages secret put CACHE_PUBLISH_SECRET --project-name=<PROJECT>
npx wrangler pages secret put PUBLIC_CACHE_BASE --project-name=<PROJECT>
# value of PUBLIC_CACHE_BASE must be https://<R2_PUBLIC_HOST> with no trailing slash

# 5. verify
curl -sS -o /dev/null -w "%{http_code}\n" https://<SITE_HOST>/
curl -sS -X POST https://<SITE_HOST>/api/publish-cache
# expect HTTP 401 JSON: Cloudflare 未配置… or 缺少 x-publish-secret / 不一致
```

Human must still bind the R2 **custom domain** in Dashboard if not using r2.dev. Wrangler cannot always attach a zone CNAME without extra token scopes.

---

## 软件怎么接到这座站

发布请求是：

```http
POST https://<SITE_HOST>/api/publish-cache
x-publish-secret: <SECRET>
Content-Type: application/json
```

桌面工具会按分片发送 `action=put-chunk`，最后 `action=finalize`，写入 R2 键：

- `promo/manifest.json`
- `promo/chunks/0001.json` …

数据和上次相同会跳过，不占配额。

没有 `CACHE_PUBLISH_SECRET`：表格汇总仍可用，只有「发布图库」会失败。

---

## 验收清单

- [ ] 打开 `https://<SITE_HOST>/` 能看到页面（首次发布前列表可以是空的）
- [ ] `POST /api/publish-cache` 不带密钥返回 401，不是 404
- [ ] Cloudflare 项目绑定了 R2 `GALLERY_CACHE` → `<BUCKET>`
- [ ] Production 有 `CACHE_PUBLISH_SECRET`
- [ ] 独立站 Production 有 `PUBLIC_CACHE_BASE=https://<R2_PUBLIC_HOST>`
- [ ] 四个 HTML 的 `Q_GALLERY_DRIVE_CACHE_MANIFEST_URL` 指向自己的 CDN
- [ ] 软件第 4 步地址是 `https://<SITE_HOST>/api/publish-cache`
- [ ] 点「发布图库」成功后，`https://<R2_PUBLIC_HOST>/promo/manifest.json` 能打开 JSON
- [ ] 独立站没有改到其它已上线站点的 Pages 项目或域名
- [ ] 密钥没有提交到 git

---

## 常见失败

| 现象 | 处理 |
|---|---|
| 请先填写发布地址和 CACHE_PUBLISH_SECRET | 软件第 4 步是空的 |
| Unauthorized. Cloudflare 未配置 CACHE_PUBLISH_SECRET | Pages Production 没设该密钥，或设到了 Preview |
| 密钥不一致 | 软件和 Cloudflare 不是同一串；刚改过密钥要两边一起改 |
| R2 binding GALLERY_CACHE missing | `wrangler.toml` 未绑定，或绑到了别的 binding 名 |
| 404 on `/api/publish-cache` | 项目名发错，或发到了没有 `functions/api/publish-cache.js` 的旧目录 |
| 页面一直是别人的图库 | HTML 仍指向其它站点的 CDN |
| 发布成功但网页是空的 | `PUBLIC_CACHE_BASE` 或 HTML CDN 和 R2 公开域不一致；或桶未公开 |
| 误覆盖现有图库 | 复用了已有站点的项目名和密钥。独立站必须换项目名、换桶、换密钥 |

---

## 目录说明（部署时看这些）

```
promo-site/
  wrangler.toml                 Pages 项目名、R2 绑定
  functions/api/publish-cache.js  软件调用的发布接口
  functions/api/*.js            其它 Pages Functions
  functions/_lib/               鉴权、R2 镜像
  index.html 等                 前端；内含 CDN 地址硬编码
  assets/                       前端脚本样式
  api/                          旧 Vercel 接口，Pages 部署可忽略
  google-apps-script/           旧表格导出脚本，直推 R2 后不必再部署
```

旧的 Apps Script 日更可以停掉：表格 → 扩展程序 → Apps Script → 触发器，删掉定时导出。桌面软件「发布图库」已替代这条链路。
