import { isAuthenticated, json, readJsonBody } from "../_lib/auth.js";

const MAX_CACHE_BYTES = 60 * 1024 * 1024;

function normalizeUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const fileMatch = text.match(/drive\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) {
    return `https://drive.google.com/uc?export=download&confirm=t&id=${encodeURIComponent(fileMatch[1])}`;
  }
  const idMatch = text.match(/[?&]id=([^&#]+)/i);
  if (/drive\.google\.com/i.test(text) && idMatch && !/[?&]confirm=/i.test(text)) {
    return text.includes("?") ? `${text}&confirm=t` : `${text}?confirm=t`;
  }
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
    return host === "zhixianglife.com" || host.endsWith(".zhixianglife.com");
  } catch {
    return false;
  }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== "POST") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }
  if (!(await isAuthenticated(request, env))) {
    return json({ ok: false, error: "需要访问密码。" }, 401);
  }

  const body = await readJsonBody(request);
  const targetUrl = normalizeUrl(body.url);
  if (!targetUrl || !isAllowedUrl(targetUrl)) {
    return json({ ok: false, error: "只支持 Google Drive / 已授权 HTTPS 缓存链接。" }, 400);
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 55000);
    const response = await fetch(targetUrl, {
      headers: { "User-Agent": "q-gallery-cache-import" },
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!response.ok) {
      return json({ ok: false, error: `缓存链接下载失败：${response.status}` }, response.status);
    }

    const contentLength = Number(response.headers.get("content-length") || 0);
    if (contentLength > MAX_CACHE_BYTES) {
      return json({ ok: false, error: "缓存文件太大，请改用本地 JSON/CSV 导入。" }, 413);
    }

    const text = await response.text();
    if (text.length > MAX_CACHE_BYTES) {
      return json({ ok: false, error: "缓存文件太大，请改用本地 JSON/CSV 导入。" }, 413);
    }

    let data;
    try {
      data = JSON.parse(text);
    } catch {
      return json({ ok: false, error: "链接内容不是有效 JSON。" }, 400);
    }
    return json(data);
  } catch (error) {
    const isAbort = error && error.name === "AbortError";
    return json(
      {
        ok: false,
        error: isAbort ? "缓存链接下载超时。" : error.message || String(error),
      },
      isAbort ? 504 : 500
    );
  }
}
