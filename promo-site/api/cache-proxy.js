const crypto = require("crypto");

const COOKIE_NAME = "q_gallery_auth";
const MAX_CACHE_BYTES = 60 * 1024 * 1024;

function getAccessPassword() {
  return process.env.ACCESS_PASSWORD || "";
}

function getSigningSecret() {
  return process.env.AUTH_SECRET || process.env.APPS_SCRIPT_API_SECRET || getAccessPassword() || "";
}

function secretsEqual(left, right) {
  const a = crypto.createHash("sha256").update("cmp|" + String(left || "")).digest();
  const b = crypto.createHash("sha256").update("cmp|" + String(right || "")).digest();
  return crypto.timingSafeEqual(a, b);
}

function sign(value) {
  const secret = getSigningSecret();
  if (!secret) return "";
  return crypto
    .createHmac("sha256", secret)
    .update(value)
    .digest("hex");
}

function parseCookies(header = "") {
  return Object.fromEntries(
    header
      .split(";")
      .map(part => part.trim())
      .filter(Boolean)
      .map(part => {
        const index = part.indexOf("=");
        return index === -1
          ? [part, ""]
          : [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      })
  );
}

function isAuthenticated(req) {
  const password = getAccessPassword();
  if (!password) return true;
  const cookies = parseCookies(req.headers.cookie || "");
  const [expires, mac] = String(cookies[COOKIE_NAME] || "").split(".");
  if (!/^\d+$/.test(expires) || Number(expires) < Date.now()) return false;
  return secretsEqual(mac, sign(`session|${expires}|${password}`));
}

function send(res, status, payload) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(payload));
}

function normalizeUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const fileMatch = text.match(/drive\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) return `https://drive.google.com/uc?export=download&id=${encodeURIComponent(fileMatch[1])}`;
  return text;
}

function isAllowedUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return false;
    const host = url.hostname.toLowerCase();
    if (
      host === "drive.google.com" ||
      host === "drive.usercontent.google.com" ||
      host === "docs.google.com" ||
      host === "lh3.googleusercontent.com"
    ) {
      return true;
    }
    return host === "example.com" || host.endsWith(".example.com");
  } catch (error) {
    return false;
  }
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    send(res, 405, { ok: false, error: "Method not allowed" });
    return;
  }

  if (!isAuthenticated(req)) {
    send(res, 401, { ok: false, error: "需要访问密码。" });
    return;
  }

  const body = typeof req.body === "object" && req.body ? req.body : {};
  const targetUrl = normalizeUrl(body.url);
  if (!targetUrl || !isAllowedUrl(targetUrl)) {
    send(res, 400, { ok: false, error: "只支持 Google Drive 的 HTTPS 缓存链接。" });
    return;
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 55000);
    const response = await fetch(targetUrl, {
      headers: { "User-Agent": "q-gallery-cache-import" },
      signal: controller.signal
    });
    clearTimeout(timeout);

    if (!response.ok) {
      send(res, response.status, { ok: false, error: `缓存链接下载失败：${response.status}` });
      return;
    }

    const contentLength = Number(response.headers.get("content-length") || 0);
    if (contentLength > MAX_CACHE_BYTES) {
      send(res, 413, { ok: false, error: "缓存文件太大，请改用本地 JSON/CSV 导入。" });
      return;
    }

    const text = await response.text();
    if (text.length > MAX_CACHE_BYTES) {
      send(res, 413, { ok: false, error: "缓存文件太大，请改用本地 JSON/CSV 导入。" });
      return;
    }

    let data;
    try {
      data = JSON.parse(text);
    } catch (error) {
      send(res, 400, { ok: false, error: "链接内容不是有效 JSON。" });
      return;
    }

    send(res, 200, data);
  } catch (error) {
    const isAbort = error && error.name === "AbortError";
    send(res, isAbort ? 504 : 500, {
      ok: false,
      error: isAbort ? "缓存链接下载超时。" : error.message || String(error)
    });
  }
};
