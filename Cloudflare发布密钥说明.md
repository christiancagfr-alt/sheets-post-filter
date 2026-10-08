# 发布图库：发布地址和密钥怎么拿

软件「数据筛选汇总」第 4 步里有两栏：

| 界面栏位 | 填什么 |
|---|---|
| 发布地址 | `https://gallery.example.com/api/publish-cache` |
| 发布密钥 | Cloudflare 里保存的同一串密钥 |

这两项**不是向 Google 申请的 API**。发布地址是图库站点自己的接口；密钥是部署站点时在 Cloudflare 里设置的密码。软件点「发布图库」时，会把表格数据 POST 到这个地址，请求头带上密钥。

下面的域名都是示例，请换成你自己的站点。

---

## 1. 发布地址（API）

格式固定为：

```text
https://gallery.example.com/api/publish-cache
```

把 `gallery.example.com` 换成你的图库域名。对应 Cloudflare Pages 项目里的接口 `/api/publish-cache`。

只有在这些情况才改地址：

- 站点换了域名：改成 `https://你的域名/api/publish-cache`
- 还在用 Pages 默认域名：`https://your-project.pages.dev/api/publish-cache`

填错成首页、对象存储地址或网盘链接都会发布失败。

---

## 2. 发布密钥

密钥是**你自己（或部署站点的人）设定的一串密码**，必须和 Cloudflare 里保存的值完全一致。Cloudflare 保存后不会再明文显示，所以按下面顺序找。

### 方法 A：以前用过谷歌表格脚本

如果图库曾经用表格脚本往 CDN 推过数据，密钥多半已经写在脚本属性里：

1. 打开结果表那张 Google 表格
2. 菜单 **扩展程序 → Apps Script**
3. 左边齿轮 **项目设置**（或 **项目设置 → 脚本属性**）
4. 找到发布密钥相关属性
5. 把这个值原样粘贴到软件的「发布密钥」

脚本属性里的发布地址一般也是上面那个 `publish-cache` 链接。

### 方法 B：到 Cloudflare 控制台查看或重设

1. 打开 [Cloudflare Dashboard](https://dash.cloudflare.com) 并登录
2. 左侧进入 **Workers 和 Pages**
3. 打开你的图库 Pages 项目（不要打开其它站点项目）
4. 进入 **Settings → Variables and Secrets**（设置 → 变量和密钥）
5. 环境选 **Production**
6. 找到发布密钥变量

- 如果还记得当初填的内容：直接填进软件
- 如果只看得到「Encrypted / 已加密」、看不到原文：点 **Edit**，改成一串新密钥，保存后再填进软件（旧密钥会立刻失效）

新密钥建议用随机长字符串，例如 32 位以上字母数字。

### 方法 C：命令行写入（适合本机已装 wrangler）

在 `promo-site` 目录执行，按提示粘贴密钥：

```bash
npx wrangler pages secret put CACHE_PUBLISH_SECRET --project-name=your-gallery-project
```

写完后，把**同一串**填进软件。

---

## 3. 填进数据汇总工具

1. 打开软件，左侧选 **数据筛选汇总**（或你用来发布的模板）
2. 展开第 4 步 **发布到 Cloudflare**
3. **发布地址**填 `https://你的域名/api/publish-cache`
4. **发布密钥**贴上和第 2 步相同的密钥
5. 点软件顶部 **保存**
6. 再点顶部 **发布图库**

「发布哪份数据」：`all` = 全部结果表，`hot` = 高亮结果表。数据和上次相同会自动跳过，不占配额。

---

## 4. 怎么确认配对成功

发布成功时，运行日志会出现写入分片、完成之类的提示。
常见失败：

| 日志 / 提示 | 原因 |
|---|---|
| 请先填写 Cloudflare 发布地址和发布密钥 | 软件里还是空的 |
| Unauthorized. Cloudflare 未配置发布密钥 | Cloudflare 项目里还没设这个变量 |
| Unauthorized. 发布密钥与 Cloudflare 不一致 | 软件里填的和 Cloudflare 里的不是同一串 |
| 404 | 发布地址写错，或站点没部署 `/api/publish-cache` |

改过 Cloudflare 密钥后，软件里必须同步改，否则会一直 401。

---

## 5. 和「服务账号 JSON」的区别

| | 服务账号 JSON | 发布密钥 |
|---|---|---|
| 用在哪 | 读/写 Google 表格、网盘视频 | 把图库 JSON 推到 Cloudflare |
| 从哪拿 | Google Cloud 控制台下载 | Cloudflare 项目环境变量 |
| 填在软件哪 | 顶部 **设置** | 第 4 步 **发布到 Cloudflare** |

没有服务账号，汇总表格会失败。
没有发布密钥，表格汇总仍可做，只是「发布图库」会失败。

---

## 6. 相关地址（示例）

| 用途 | 示例 |
|---|---|
| 发布接口（填进软件） | https://gallery.example.com/api/publish-cache |
| 图库站点 | https://gallery.example.com |
| Pages 默认域名 | https://your-gallery-project.pages.dev |
| 浏览器读的 CDN | https://cdn.example.com/promo/ |

密钥只给需要点「发布图库」的人，不要提交到 GitHub，也不要发到公开群。

---

## 7. 还要不要去 Cloudflare 部署？

- 发到已经在跑的图库：不用新建项目，填该站的地址和密钥即可（会覆盖这座站的图库）。
- 自己做独立图库网站：打开软件 **设置 → 图库站点**，登录 Cloudflare，填自己的域名（例如 `gallery.example.com`），点部署。密钥由软件生成，不必去控制台手工建项目。命令行方案见 `promo-site/DEPLOY.md`。
