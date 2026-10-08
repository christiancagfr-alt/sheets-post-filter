/**
 * 头像代理：浏览器直连 graph.facebook.com / fbcdn 在国内常失败。
 * Worker 在境外拉取后回源同域，并加长缓存。
 *
 * 真实头像需要 Facebook access_token（无 token 时 Graph 只返回灰色默认剪影）。
 * token 来源：
 *  1) 环境变量 FB_GRAPH_ACCESS_TOKEN / FACEBOOK_ACCESS_TOKEN
 *  2) 请求 url= 参数里自带的 access_token（导出 JSON 含完整 Graph URL 时）
 * 不再从公开 R2 的 _internal 前缀读取。
 *
 * GET /api/avatar?id=6157...&w=120
 * GET /api/avatar?url=https://graph.facebook.com/.../picture?...
 */
const DEFAULT_W = 120;
const MAX_BYTES = 2 * 1024 * 1024;
const CACHE_SEC = 7 * 24 * 3600;
// 进程内短缓存 env token
let cachedToken = { value: "", at: 0 };
const TOKEN_TTL_MS = 5 * 60 * 1000;

export async function onRequestGet(context) {
  const { request, env } = context;
  const reqUrl = new URL(request.url);
  const idRaw = String(reqUrl.searchParams.get("id") || "").trim();
  const width = clampInt(reqUrl.searchParams.get("w") || reqUrl.searchParams.get("width"), DEFAULT_W, 40, 320);
  const remoteUrlParam = String(reqUrl.searchParams.get("url") || "").trim();

  const serverToken = await resolveFbAccessToken(env);

  let target = "";
  const authorId = idRaw.replace(/[^\d]/g, "");
  if (/^\d{8,}$/.test(authorId)) {
    target = buildGraphPictureUrl(authorId, width, serverToken);
  } else if (remoteUrlParam) {
    target = sanitizeRemoteAvatarUrl(remoteUrlParam, serverToken);
  }

  if (!target) {
    return new Response("Missing or invalid avatar id/url", {
      status: 400,
      headers: { "Cache-Control": "no-store", "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  try {
    const hasToken = /access_token=/i.test(target);
    // 先用 redirect=false 探测：可区分剪影 / 权限错误（小组 ID 常 400）
    let imageUrl = target;
    let isSilhouette = false;
    if (/graph\.facebook\.com/i.test(target) && /\/picture/i.test(target)) {
      try {
        const metaUrl = target.includes("redirect=")
          ? target
          : target + (target.includes("?") ? "&" : "?") + "redirect=false";
        const controllerMeta = new AbortController();
        const tMeta = setTimeout(() => controllerMeta.abort(), 8000);
        const metaRes = await fetch(metaUrl, {
          redirect: "follow",
          signal: controllerMeta.signal,
          headers: {
            "User-Agent": "q-gallery-promo-avatar/1.0",
            Accept: "application/json,image/*,*/*;q=0.8",
          },
        });
        clearTimeout(tMeta);
        const metaType = String(metaRes.headers.get("content-type") || "").toLowerCase();
        if (metaRes.ok && metaType.includes("json")) {
          const meta = await metaRes.json().catch(() => null);
          const data = meta && meta.data ? meta.data : meta;
          if (data && data.url) {
            imageUrl = String(data.url);
            isSilhouette = Boolean(data.is_silhouette);
          }
        } else if (!metaRes.ok) {
          // 小组等无权限对象：直接 404，让前端换作者 ID / Page ID
          return new Response(`Upstream ${metaRes.status}`, {
            status: 404,
            headers: {
              "Cache-Control": "public, max-age=600",
              "Content-Type": "text/plain; charset=utf-8",
              "X-Avatar-Token": hasToken ? "1" : "0",
            },
          });
        }
      } catch {
        // meta 失败则回退直拉 picture
      }
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    const upstream = await fetch(imageUrl, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        "User-Agent": "q-gallery-promo-avatar/1.0",
        Accept: "image/*,*/*;q=0.8",
      },
    });
    clearTimeout(timeout);

    if (!upstream.ok) {
      return new Response(`Upstream ${upstream.status}`, {
        status: upstream.status === 404 ? 404 : 502,
        headers: { "Cache-Control": "public, max-age=300", "Content-Type": "text/plain; charset=utf-8" },
      });
    }

    const contentType = String(upstream.headers.get("content-type") || "").toLowerCase();
    if (contentType && !contentType.startsWith("image/") && !contentType.includes("octet-stream")) {
      return new Response("Not an image", {
        status: 502,
        headers: { "Cache-Control": "public, max-age=120", "Content-Type": "text/plain; charset=utf-8" },
      });
    }

    const buf = await upstream.arrayBuffer();
    if (!buf.byteLength || buf.byteLength > MAX_BYTES) {
      return new Response("Empty or too large", {
        status: 502,
        headers: { "Cache-Control": "public, max-age=120", "Content-Type": "text/plain; charset=utf-8" },
      });
    }

    // 极小 GIF 剪影：返回 404 让前端尝试 Page ID / 其它候选
    if (isSilhouette || buf.byteLength < 800) {
      return new Response("Silhouette or tiny placeholder", {
        status: 404,
        headers: {
          "Cache-Control": "public, max-age=300",
          "Content-Type": "text/plain; charset=utf-8",
          "X-Avatar-Token": hasToken ? "1" : "0",
          "X-Avatar-Silhouette": "1",
        },
      });
    }

    return new Response(buf, {
      status: 200,
      headers: {
        "Content-Type": contentType.startsWith("image/") ? contentType : "image/jpeg",
        "Cache-Control": hasToken
          ? `public, max-age=${CACHE_SEC}, stale-while-revalidate=86400`
          : "public, max-age=600",
        "Access-Control-Allow-Origin": "*",
        "X-Avatar-Source": "proxy",
        "X-Avatar-Token": hasToken ? "1" : "0",
      },
    });
  } catch (error) {
    return new Response(String(error && error.message ? error.message : error || "fetch failed"), {
      status: 502,
      headers: { "Cache-Control": "public, max-age=60", "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

async function resolveFbAccessToken(env) {
  const now = Date.now();
  if (cachedToken.value && now - cachedToken.at < TOKEN_TTL_MS) {
    return cachedToken.value;
  }
  const fromEnv = String(env.FB_GRAPH_ACCESS_TOKEN || env.FACEBOOK_ACCESS_TOKEN || "").trim();
  cachedToken = { value: fromEnv, at: now };
  return fromEnv;
}

function buildGraphPictureUrl(authorId, width, token) {
  let url =
    "https://graph.facebook.com/" +
    encodeURIComponent(authorId) +
    "/picture?width=" +
    encodeURIComponent(String(width)) +
    "&height=" +
    encodeURIComponent(String(width));
  if (token) {
    url += "&access_token=" + encodeURIComponent(token);
  }
  return url;
}

function sanitizeRemoteAvatarUrl(value, serverToken) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return "";
    const host = url.hostname.toLowerCase();
    const allowed =
      host === "graph.facebook.com" ||
      host.endsWith(".facebook.com") ||
      host.endsWith(".fbcdn.net") ||
      host.endsWith(".fbsbx.com");
    if (!allowed) return "";

    if (host === "graph.facebook.com" && /\/picture/i.test(url.pathname)) {
      if (serverToken && !url.searchParams.get("access_token")) {
        url.searchParams.set("access_token", serverToken);
      }
    }
    return url.toString();
  } catch {
    return "";
  }
}

function clampInt(value, fallback, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}
