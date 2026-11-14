const crypto = require("crypto");

const COOKIE_NAME = "q_gallery_auth";
const MAX_BYTES = 2 * 1024 * 1024;

function accessPassword() {
  return String(process.env.ACCESS_PASSWORD || "").trim();
}

function signingSecret() {
  return String(
    process.env.AUTH_SECRET || process.env.APPS_SCRIPT_API_SECRET || accessPassword() || ""
  ).trim();
}

function parseCookies(header = "") {
  return Object.fromEntries(
    String(header)
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const index = part.indexOf("=");
        return index < 0
          ? [part, ""]
          : [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      })
  );
}

function isAuthenticated(req) {
  const password = accessPassword();
  if (!password) return true;
  const [expires, mac] = String(parseCookies(req.headers.cookie || "")[COOKIE_NAME] || "").split(".");
  if (!/^\d+$/.test(expires) || Number(expires) < Date.now()) return false;
  const secret = signingSecret();
  if (!secret) return false;
  const expected = crypto
    .createHmac("sha256", secret)
    .update(`session|${expires}|${password}`)
    .digest("hex");
  const left = Buffer.from(String(mac || ""), "hex");
  const right = Buffer.from(expected, "hex");
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

function json(res, status, payload) {
  res.status(status).setHeader("Cache-Control", "no-store");
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

function error(res, status, message) {
  res.status(status).setHeader("Cache-Control", "no-store");
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  res.end(message);
}

function graphPictureUrl(id, width, token) {
  const url = new URL(`https://graph.facebook.com/${encodeURIComponent(id)}/picture`);
  url.searchParams.set("width", String(width));
  url.searchParams.set("height", String(width));
  if (token) url.searchParams.set("access_token", token);
  return url;
}

function safeRemoteUrl(value, token) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return null;
    const host = url.hostname.toLowerCase();
    if (
      host !== "graph.facebook.com" &&
      !host.endsWith(".facebook.com") &&
      !host.endsWith(".fbcdn.net") &&
      !host.endsWith(".fbsbx.com")
    ) return null;
    if (host === "graph.facebook.com" && /\/picture/i.test(url.pathname) && token) {
      url.searchParams.set("access_token", token);
    }
    return url;
  } catch {
    return null;
  }
}

async function fetchImage(target) {
  let imageUrl = target;
  if (target.hostname === "graph.facebook.com" && /\/picture/i.test(target.pathname)) {
    const metaUrl = new URL(target);
    metaUrl.searchParams.set("redirect", "false");
    const metaRes = await fetch(metaUrl, {
      headers: { Accept: "application/json,image/*,*/*;q=0.8", "User-Agent": "q-gallery-avatar/1.0" },
      signal: AbortSignal.timeout(10000),
    });
    const contentType = String(metaRes.headers.get("content-type") || "").toLowerCase();
    if (!metaRes.ok) throw Object.assign(new Error(`Facebook returned HTTP ${metaRes.status}`), { status: 502 });
    if (contentType.includes("json")) {
      const meta = await metaRes.json().catch(() => null);
      const data = meta && meta.data ? meta.data : meta;
      if (data && data.is_silhouette) throw Object.assign(new Error("Facebook returned a default avatar"), { status: 404 });
      if (data && data.url) imageUrl = new URL(data.url);
    } else if (contentType.startsWith("image/")) {
      return metaRes;
    }
  }
  const imageRes = await fetch(imageUrl, {
    redirect: "follow",
    headers: { Accept: "image/*,*/*;q=0.8", "User-Agent": "q-gallery-avatar/1.0" },
    signal: AbortSignal.timeout(12000),
  });
  if (!imageRes.ok) throw Object.assign(new Error(`Facebook returned HTTP ${imageRes.status}`), { status: 502 });
  const contentType = String(imageRes.headers.get("content-type") || "").toLowerCase();
  if (!contentType.startsWith("image/")) throw Object.assign(new Error("Facebook did not return an image"), { status: 502 });
  return imageRes;
}

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return json(res, 405, { ok: false, error: "Method not allowed" });
  if (!isAuthenticated(req)) return json(res, 401, { ok: false, error: "需要访问密码" });

  const token = String(process.env.FB_GRAPH_ACCESS_TOKEN || process.env.FACEBOOK_ACCESS_TOKEN || "").trim();
  const url = new URL(req.url, `https://${req.headers.host || "localhost"}`);
  if (url.searchParams.get("check") === "1") {
    return json(res, 200, {
      ok: true,
      authenticated: true,
      tokenConfigured: Boolean(token),
      hint: token
        ? "头像密钥已配置。"
        : "当前 Vercel 项目尚未配置 FB_GRAPH_ACCESS_TOKEN；Cloudflare Pages 的密钥不会自动同步到 Vercel。",
    });
  }

  const rawId = String(url.searchParams.get("id") || "").trim();
  const id = /^\d{8,}$/.test(rawId) ? rawId : "";
  const width = Math.max(40, Math.min(320, Math.round(Number(url.searchParams.get("w") || 120) || 120)));
  const target = id
    ? graphPictureUrl(id, width, token)
    : safeRemoteUrl(String(url.searchParams.get("url") || ""), token);
  if (!target) return error(res, 400, "Missing or invalid avatar id/url");

  try {
    const upstream = await fetchImage(target);
    const bytes = Buffer.from(await upstream.arrayBuffer());
    if (!bytes.length || bytes.length > MAX_BYTES) return error(res, 502, "Empty or oversized avatar image");
    res.status(200);
    res.setHeader("Content-Type", upstream.headers.get("content-type") || "image/jpeg");
    res.setHeader("Cache-Control", token ? "public, max-age=604800, stale-while-revalidate=86400" : "public, max-age=600");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.end(bytes);
  } catch (cause) {
    return error(res, Number(cause && cause.status) || 502, String(cause && cause.message || "Avatar request failed"));
  }
};
