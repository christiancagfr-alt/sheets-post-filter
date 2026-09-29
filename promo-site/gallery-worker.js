/**
 * 软件一键部署用的 Pages Advanced Mode Worker。
 * 静态页面走 env.ASSETS；发布接口和 CDN 读 R2 绑定 GALLERY_CACHE。
 */
const MANIFEST_KEY = "promo/manifest.json";
const CHUNK_PREFIX = "promo/chunks/";
const LAST_REFRESH_KEY = "promo/last-refresh.json";

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "content-type, x-publish-secret, authorization",
      ...extraHeaders,
    },
  });
}

function publicBase(env, request) {
  const configured = String(env.PUBLIC_CACHE_BASE || "").trim().replace(/\/$/, "");
  if (configured) return configured;
  return new URL(request.url).origin + "/cdn";
}

function readPublishSecret(request) {
  const header = (request.headers.get("x-publish-secret") || "").trim();
  if (header) return header;
  const auth = (request.headers.get("authorization") || "").trim();
  if (/^bearer\s+/i.test(auth)) return auth.replace(/^bearer\s+/i, "").trim();
  return "";
}

function pad4(n) {
  return String(n).padStart(4, "0");
}

async function handlePublish(request, env) {
  const secret = readPublishSecret(request);
  const expected = String(env.CACHE_PUBLISH_SECRET || "").trim();
  if (!expected) {
    return json({ ok: false, error: "Unauthorized. Cloudflare 未配置 CACHE_PUBLISH_SECRET。" }, 401);
  }
  if (!secret) {
    return json({ ok: false, error: "Unauthorized. 缺少 x-publish-secret 请求头。" }, 401);
  }
  if (secret !== expected) {
    return json({ ok: false, error: "Unauthorized. 发布密钥与 Cloudflare CACHE_PUBLISH_SECRET 不一致。" }, 401);
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  const action = String(body.action || "").trim().toLowerCase();
  if (action !== "put-chunk" && action !== "finalize") {
    return json({
      ok: true,
      service: "sheets-gallery-publish-cache",
      usage: "POST x-publish-secret + { action:'put-chunk'| 'finalize' }",
    });
  }
  try {
    return json(await publishDirectToR2(env, request, action, body));
  } catch (error) {
    const status = error.code === "NO_R2" ? 501 : 400;
    return json({ ok: false, error: error.message || String(error) }, status);
  }
}

async function publishDirectToR2(env, request, action, body) {
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.put !== "function") {
    const err = new Error("R2 binding GALLERY_CACHE missing");
    err.code = "NO_R2";
    throw err;
  }
  const base = publicBase(env, request);
  const buildId = String(body.buildId || "").trim();
  if (!buildId) throw new Error("buildId required");

  if (action === "put-chunk") {
    const index = Number(body.index || 0);
    if (!Number.isFinite(index) || index < 1) throw new Error("index must be >= 1");
    const assets = Array.isArray(body.assets) ? body.assets : [];
    const key = `${CHUNK_PREFIX}${pad4(index)}.json`;
    const payload = JSON.stringify({
      ok: true,
      buildId,
      index,
      count: assets.length,
      totalRows: Number(body.totalRows || 0),
      updatedAt: new Date().toISOString(),
      maxDate: body.maxDate || "",
      assets,
    });
    await env.GALLERY_CACHE.put(key, payload, {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=3600",
      },
    });
    return { ok: true, action: "put-chunk", key, index, count: assets.length, url: `${base}/${key}` };
  }

  const chunkCount = Number(body.chunkCount || 0);
  if (!chunkCount) throw new Error("chunkCount required");
  const publishedChunks = [];
  for (let i = 1; i <= chunkCount; i += 1) {
    const name = `${CHUNK_PREFIX}${pad4(i)}.json`;
    publishedChunks.push({ index: i, fileId: "", name, url: `${base}/${name}`, size: 0 });
  }
  const publishedAt = new Date().toISOString();
  const outManifest = {
    ok: true,
    source: "q-gallery-promo-python",
    buildId,
    updatedAt: publishedAt,
    publishedAt,
    totalRows: Number(body.totalRows || 0),
    exportedRows: Number(body.totalRows || 0),
    maxDate: body.maxDate || "",
    maxWaterTime: body.maxWaterTime || "",
    chunkCount,
    chunks: publishedChunks,
  };
  await env.GALLERY_CACHE.put(MANIFEST_KEY, JSON.stringify(outManifest), {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
      cacheControl: "public, max-age=300",
    },
  });
  await env.GALLERY_CACHE.put(
    LAST_REFRESH_KEY,
    JSON.stringify({ at: publishedAt, buildId, totalRows: body.totalRows || 0, chunkCount, source: "python" }),
    { httpMetadata: { contentType: "application/json; charset=utf-8", cacheControl: "no-store" } }
  );
  return {
    ok: true,
    action: "finalize",
    buildId,
    totalRows: body.totalRows || 0,
    chunkCount,
    publicManifestUrl: `${base}/${MANIFEST_KEY}`,
  };
}

async function handleCdn(request, env) {
  const url = new URL(request.url);
  const key = url.pathname.replace(/^\/cdn\/+/, "");
  if (!key || key.includes("..")) return new Response("Not found", { status: 404 });
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.get !== "function") {
    return new Response("R2 binding GALLERY_CACHE missing", { status: 501 });
  }
  const obj = await env.GALLERY_CACHE.get(key);
  if (!obj) return new Response("Not found", { status: 404 });
  const headers = new Headers();
  headers.set("Cache-Control", "public, max-age=300");
  const type = (obj.httpMetadata && obj.httpMetadata.contentType) || "application/json; charset=utf-8";
  headers.set("Content-Type", type);
  return new Response(obj.body, { headers });
}

const AUTH_COOKIE = "q_gallery_auth";

function accessPassword(env) {
  return String(env.ACCESS_PASSWORD || "").trim();
}

function parseCookies(header) {
  const out = {};
  String(header || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .forEach((part) => {
      const index = part.indexOf("=");
      if (index === -1) out[part] = "";
      else out[part.slice(0, index)] = decodeURIComponent(part.slice(index + 1));
    });
  return out;
}

async function signValue(value, env) {
  const secret = String(env.AUTH_SECRET || env.CACHE_PUBLISH_SECRET || accessPassword(env) || "q-gallery").trim() || "q-gallery";
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(String(value || "")));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function isAuthenticated(request, env) {
  const password = accessPassword(env);
  if (!password) return true;
  const cookies = parseCookies(request.headers.get("cookie") || "");
  return cookies[AUTH_COOKIE] === (await signValue(password, env));
}

async function authCookieHeader(env, clear) {
  if (clear) return `${AUTH_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
  const token = await signValue(accessPassword(env), env);
  return `${AUTH_COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`;
}

async function handleAuth(request, env) {
  if (request.method === "GET") {
    return json({ ok: true, authenticated: await isAuthenticated(request, env) });
  }
  if (request.method !== "POST") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }
  let body = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  const action = String(body.action || "status").trim().toLowerCase();
  if (action === "status") {
    return json({ ok: true, authenticated: await isAuthenticated(request, env) });
  }
  if (action === "logout") {
    return json({ ok: true, authenticated: false }, 200, { "Set-Cookie": await authCookieHeader(env, true) });
  }
  if (action === "login") {
    const password = accessPassword(env);
    if (!password || body.password === password) {
      return json({ ok: true, authenticated: true }, 200, { "Set-Cookie": await authCookieHeader(env, false) });
    }
    return json({ ok: false, authenticated: false, error: "密码错误。" }, 401);
  }
  return json({ ok: false, error: "Unknown action" }, 400);
}

async function handleDriveImage(request) {
  const reqUrl = new URL(request.url);
  const raw = String(reqUrl.searchParams.get("id") || reqUrl.searchParams.get("url") || "").trim();
  let id = "";
  const fileMatch = raw.match(/(?:drive|docs)\.google\.com\/file\/d\/([^/?#]+)/i);
  const lh3 = raw.match(/lh3\.googleusercontent\.com\/d\/([a-zA-Z0-9_-]+)/i);
  if (fileMatch) id = fileMatch[1];
  else if (lh3) id = lh3[1];
  else if (/^[a-zA-Z0-9_-]{20,}$/.test(raw)) id = raw;
  else {
    const idMatch = raw.match(/[?&]id=([^&#]+)/i);
    if (idMatch) id = decodeURIComponent(idMatch[1]);
  }
  if (!id) return new Response("Missing Drive file id", { status: 400 });
  const targets = [
    `https://lh3.googleusercontent.com/d/${id}`,
    `https://drive.google.com/thumbnail?id=${encodeURIComponent(id)}&sz=w1000`,
    `https://drive.google.com/uc?export=view&id=${encodeURIComponent(id)}`,
  ];
  for (const target of targets) {
    try {
      const upstream = await fetch(target, {
        redirect: "follow",
        headers: { Accept: "image/*,*/*;q=0.8", "User-Agent": "q-gallery-promo-drive-image/1.0" },
      });
      const type = String(upstream.headers.get("content-type") || "").toLowerCase();
      if (!upstream.ok || type.includes("text/html")) continue;
      const buffer = await upstream.arrayBuffer();
      if (!buffer.byteLength) continue;
      return new Response(buffer, {
        status: 200,
        headers: {
          "Content-Type": type.startsWith("image/") ? type : "image/jpeg",
          "Cache-Control": "public, max-age=86400",
        },
      });
    } catch {
      continue;
    }
  }
  return new Response("Drive image unavailable", { status: 404 });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Headers": "content-type, x-publish-secret, authorization",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        },
      });
    }
    if (url.pathname === "/api/publish-cache" && request.method === "POST") {
      return handlePublish(request, env);
    }
    if (url.pathname === "/api/publish-cache" && request.method === "GET") {
      return json({ ok: true, service: "sheets-gallery-publish-cache" });
    }
    if (url.pathname.startsWith("/cdn/")) {
      return handleCdn(request, env);
    }
    if (url.pathname === "/api/auth") {
      return handleAuth(request, env);
    }
    if (url.pathname === "/api/drive-image" && request.method === "GET") {
      return handleDriveImage(request);
    }
    return env.ASSETS.fetch(request);
  },
};
