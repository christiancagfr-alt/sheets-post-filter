# AGENTS — promo-site deploy

Canonical human+AI procedure: **[DEPLOY.md](./DEPLOY.md)**. Read it before any Cloudflare command.

## Stop if

- User only needs Google Sheets aggregation → no deploy; they add a service-account JSON in the desktop app Settings.
- User wants to publish into an existing gallery → no new project; they fill `https://gallery.example.com/api/publish-cache` (their real host) plus the existing publish secret. Publishing overwrites that site.

## Never

- Deploy this folder onto another live Pages project that already serves a different site.
- Reuse another site's R2 bucket or Pages project for a third-party independent gallery.
- Rename binding `GALLERY_CACHE`.
- Commit secrets.

## Track A (update an existing site the user already owns)

```bash
cd promo-site
npx wrangler pages deploy . --project-name=<EXISTING_PROJECT> --commit-dirty=true --branch=production
```

Replace `<EXISTING_PROJECT>` with the user's Pages project name. Do not invent a new project.

## Preferred for other people using the desktop app

Do **not** walk them through wrangler. Tell them:

1. Open 设置
2. 登录 Cloudflare（用软件打开的令牌页创建 API Token，不要粘 Global API Key）
3. Enter their hostname, for example `gallery.example.com`
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
