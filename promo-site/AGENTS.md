# AGENTS — promo-site deploy

Canonical human+AI procedure: **[DEPLOY.md](./DEPLOY.md)**. Read it before any Cloudflare command.

## Stop if

- User only needs Google Sheets aggregation → no deploy; they add a service-account JSON in the desktop app Settings.
- User wants to publish into the existing gallery `promo.zhixianglife.com` → no new project; they fill `https://promo.zhixianglife.com/api/publish-cache` + existing `CACHE_PUBLISH_SECRET`. Publishing overwrites that site.

## Never

- Deploy this folder to Pages project `q-gallery`.
- Reuse bucket `q-gallery-json-cache` or project `q-gallery-promo` for a third-party independent gallery.
- Rename binding `GALLERY_CACHE`.
- Commit secrets.

## Track A (this owner's site)

```bash
cd promo-site
npx wrangler pages deploy . --project-name=q-gallery-promo --commit-dirty=true --branch=production
```

## Preferred for other people using the desktop app

Do **not** walk them through wrangler. Tell them:

1. Open 设置
2. 登录 Cloudflare（用软件打开的令牌页创建 API Token，不要粘 Global API Key）
3. Enter their hostname
4. Click 生成密钥并部署

The app generates the secret, rewrites CDN URLs, creates R2 + Pages, and fills 发布地址.

## Track B (new independent gallery, command line)

1. Change `wrangler.toml` `name` and `bucket_name`.
2. Point the four HTML `Q_GALLERY_DRIVE_CACHE_MANIFEST_URL` values at `https://<R2_PUBLIC_HOST>/promo/manifest.json`.
3. `npx wrangler r2 bucket create <BUCKET>`
4. Attach a public R2 custom domain (Dashboard).
5. `npx wrangler pages deploy . --project-name=<PROJECT> --commit-dirty=true --branch=production`
6. `npx wrangler pages secret put CACHE_PUBLISH_SECRET --project-name=<PROJECT>`
7. `npx wrangler pages secret put PUBLIC_CACHE_BASE --project-name=<PROJECT>` value `https://<R2_PUBLIC_HOST>`
8. Desktop app: 发布地址 `https://<SITE_HOST>/api/publish-cache`, secret = step 6.

Verify: GET site HTML 200; POST `/api/publish-cache` without secret → 401.
