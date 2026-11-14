/**
 * 软件一键部署用的 Pages Advanced Mode Worker。
 * 静态页面走 env.ASSETS；发布接口和 CDN 读 R2 绑定 GALLERY_CACHE。
 */
const MANIFEST_KEY = "promo/manifest.json";
const CHUNK_PREFIX = "promo/chunks/";
const LAST_REFRESH_KEY = "promo/last-refresh.json";

const MAX_CHUNK_INDEX = 2000;
const MAX_ASSETS_PER_CHUNK = 5000;
const MAX_CHUNK_BYTES = 6 * 1024 * 1024;
const MAX_DRIVE_IMAGE_BYTES = 4 * 1024 * 1024;

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...extraHeaders,
    },
  });
}

async function secretsEqual(left, right) {
  const enc = new TextEncoder();
  const a = await crypto.subtle.digest("SHA-256", enc.encode("cmp|" + String(left || "")));
  const b = await crypto.subtle.digest("SHA-256", enc.encode("cmp|" + String(right || "")));
  return crypto.subtle.timingSafeEqual(a, b);
}

function safeDriveFileId(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  let id = "";
  const fileMatch = text.match(/(?:drive|docs)\.google\.com\/file\/d\/([^/?#]+)/i);
  const lh3 = text.match(/lh3\.googleusercontent\.com\/d\/([a-zA-Z0-9_-]+)/i);
  if (fileMatch) id = fileMatch[1];
  else if (lh3) id = lh3[1];
  else if (/^[a-zA-Z0-9_-]{20,128}$/.test(text)) id = text;
  else {
    const idMatch = text.match(/[?&]id=([^&#]+)/i);
    if (idMatch) {
      try {
        id = decodeURIComponent(idMatch[1]);
      } catch {
        id = idMatch[1];
      }
    }
  }
  return /^[a-zA-Z0-9_-]{20,128}$/.test(id) ? id : "";
}

function isPublicCacheKey(key) {
  let normalized = String(key || "");
  try {
    normalized = decodeURIComponent(normalized);
  } catch {
    return false;
  }
  normalized = normalized.replace(/\\/g, "/").replace(/^\/+/, "");
  if (!normalized || normalized.includes("..") || normalized.includes("\0")) return false;
  if (normalized.includes("/_internal") || normalized.startsWith("_internal")) return false;
  if (normalized === "promo/manifest.json" || normalized === "promo/last-refresh.json") return true;
  return /^promo\/chunks\/[0-9A-Za-z._-]+$/.test(normalized);
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

async function boundedJson(request, limit) {
  const fail=()=>Object.assign(new Error('Request too large'),{status:413});
  if(Number(request.headers.get('content-length'))>limit) throw fail();
  if(!request.body) return {};
  const reader=request.body.getReader(), chunks=[];
  let size=0;
  try {
    while(true) {
      const {done,value}=await reader.read();if(done) break;
      size+=value.byteLength;
      if(size>limit) {await reader.cancel();throw fail();}
      chunks.push(value);
    }
  } finally {reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;
  for(const chunk of chunks) {bytes.set(chunk,offset);offset+=chunk.byteLength;}
  if (!size) return {};
  return JSON.parse(new TextDecoder().decode(bytes));
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
  if (!(await secretsEqual(secret, expected))) {
    return json({ ok: false, error: "Unauthorized. 发布密钥与 Cloudflare CACHE_PUBLISH_SECRET 不一致。" }, 401);
  }

  let body = {};
  try {
    body = await boundedJson(request, MAX_CHUNK_BYTES);
  } catch (error) {
    return json({ok:false,error:'Invalid request body'},error.status===413?413:400);
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
    if (!Number.isFinite(index) || index < 1 || index > MAX_CHUNK_INDEX) {
      throw new Error("index must be 1.." + MAX_CHUNK_INDEX);
    }
    const assets = Array.isArray(body.assets) ? body.assets : [];
    if (assets.length > MAX_ASSETS_PER_CHUNK) {
      throw new Error("too many assets in chunk");
    }
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
    if (payload.length > MAX_CHUNK_BYTES) throw new Error("chunk payload too large");
    await env.GALLERY_CACHE.put(key, payload, {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=3600",
      },
    });
    return { ok: true, action: "put-chunk", key, index, count: assets.length, url: `${base}/${key}` };
  }

  const chunkCount = Number(body.chunkCount || 0);
  if (!chunkCount || chunkCount < 1 || chunkCount > MAX_CHUNK_INDEX) {
    throw new Error("chunkCount must be 1.." + MAX_CHUNK_INDEX);
  }
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
  if (!isPublicCacheKey(key)) return new Response("Not found", { status: 404 });
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.get !== "function") {
    return new Response("R2 binding GALLERY_CACHE missing", { status: 501 });
  }
  const obj = await env.GALLERY_CACHE.get(key);
  if (!obj) return new Response("Not found", { status: 404 });
  const headers = new Headers();
  headers.set("Cache-Control", "public, max-age=300");
  const type = (obj.httpMetadata && obj.httpMetadata.contentType) || "application/json; charset=utf-8";
  headers.set("Content-Type", type);
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("X-Frame-Options", "DENY");
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
  const secret = String(env.AUTH_SECRET || env.CACHE_PUBLISH_SECRET || accessPassword(env) || "").trim();
  if (!secret) return "";
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
  const [expires, mac] = String(cookies[AUTH_COOKIE] || "").split(".");
  if (!/^\d+$/.test(expires) || Number(expires) < Date.now()) return false;
  return secretsEqual(mac, await signValue(`session|${expires}|${password}`, env));
}

async function authCookieHeader(env, clear) {
  if (clear) return `${AUTH_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
  const expires = String(Date.now() + 2592000000);
  const token = expires + "." + await signValue(`session|${expires}|${accessPassword(env)}`, env);
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
    body = await boundedJson(request, 64 * 1024);
  } catch (error) {
    return json({ok:false,error:'Invalid request body'},error.status===413?413:400);
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
    if (!password || (await secretsEqual(body.password, password))) {
      return json({ ok: true, authenticated: true }, 200, { "Set-Cookie": await authCookieHeader(env, false) });
    }
    return json({ ok: false, authenticated: false, error: "密码错误。" }, 401);
  }
  return json({ ok: false, error: "Unknown action" }, 400);
}

async function handleDriveImage(request) {
  const reqUrl = new URL(request.url);
  const id = safeDriveFileId(reqUrl.searchParams.get("id") || reqUrl.searchParams.get("url") || "");
  if (!id) return new Response("Missing Drive file id", { status: 400 });
  const targets = [
    `https://lh3.googleusercontent.com/d/${encodeURIComponent(id)}`,
    `https://drive.google.com/thumbnail?id=${encodeURIComponent(id)}&sz=w1000`,
    `https://drive.google.com/uc?export=view&id=${encodeURIComponent(id)}`,
  ];
  for (const target of targets) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 12000);
      const upstream = await fetch(target, {
        redirect: "follow",
        signal: controller.signal,
        headers: { Accept: "image/*,*/*;q=0.8", "User-Agent": "q-gallery-promo-drive-image/1.0" },
      });
      clearTimeout(timer);
      const type = String(upstream.headers.get("content-type") || "").toLowerCase();
      if (!upstream.ok || type.includes("text/html")) continue;
      if (type && !type.startsWith("image/") && !type.includes("octet-stream")) continue;
      const buffer = await upstream.arrayBuffer();
      if (!buffer.byteLength || buffer.byteLength > MAX_DRIVE_IMAGE_BYTES) continue;
      return new Response(buffer, {
        status: 200,
        headers: {
          "Content-Type": type.startsWith("image/") ? type : "image/jpeg",
          "Cache-Control": "public, max-age=86400",
          "X-Content-Type-Options": "nosniff",
        },
      });
    } catch {
      continue;
    }
  }
  return new Response("Drive image unavailable", { status: 404 });
}

async function handleAvatar(request, env) {
  if (!(await isAuthenticated(request, env))) return json({ ok: false, error: "需要访问密码" }, 401);
  const url = new URL(request.url);
  const token = String(env.FB_GRAPH_ACCESS_TOKEN || env.FACEBOOK_ACCESS_TOKEN || "").trim();
  if (url.searchParams.get("check") === "1") {
    return json({
      ok: true,
      authenticated: true,
      tokenConfigured: Boolean(token),
      hint: token ? "头像密钥已配置。" : "当前 Cloudflare Pages 项目尚未配置 FB_GRAPH_ACCESS_TOKEN。",
    });
  }

  const rawId = String(url.searchParams.get("id") || "").trim();
  const id = /^\d{8,}$/.test(rawId) ? rawId : "";
  const width = Math.max(40, Math.min(320, Math.round(Number(url.searchParams.get("w") || 120) || 120)));
  let target;
  if (id) {
    target = new URL(`https://graph.facebook.com/${encodeURIComponent(id)}/picture`);
    target.searchParams.set("width", String(width));
    target.searchParams.set("height", String(width));
    if (token) target.searchParams.set("access_token", token);
  } else {
    try {
      target = new URL(String(url.searchParams.get("url") || ""));
    } catch {
      return new Response("Missing or invalid avatar id/url", { status: 400 });
    }
    const host = target.hostname.toLowerCase();
    if (target.protocol !== "https:" || !(
      host === "graph.facebook.com" || host.endsWith(".facebook.com") ||
      host.endsWith(".fbcdn.net") || host.endsWith(".fbsbx.com")
    )) return new Response("Invalid avatar URL", { status: 400 });
    if (host === "graph.facebook.com" && /\/picture/i.test(target.pathname) && token) {
      target.searchParams.set("access_token", token);
    }
  }

  try {
    let imageUrl = target;
    if (target.hostname === "graph.facebook.com" && /\/picture/i.test(target.pathname)) {
      const metaUrl = new URL(target);
      metaUrl.searchParams.set("redirect", "false");
      const metaController = new AbortController();
      const metaTimer = setTimeout(() => metaController.abort(), 8000);
      const metaRes = await fetch(metaUrl, {
        signal: metaController.signal,
        headers: { Accept: "application/json,image/*,*/*;q=0.8", "User-Agent": "q-gallery-avatar/1.0" },
      });
      clearTimeout(metaTimer);
      if (!metaRes.ok) return new Response(`Facebook returned HTTP ${metaRes.status}`, { status: 502 });
      const metaType = String(metaRes.headers.get("content-type") || "").toLowerCase();
      if (metaType.includes("json")) {
        const meta = await metaRes.json().catch(() => null);
        const data = meta && meta.data ? meta.data : meta;
        if (data && data.is_silhouette) return new Response("Facebook returned a default avatar", { status: 404 });
        if (data && data.url) imageUrl = new URL(data.url);
      } else if (metaType.startsWith("image/")) {
        return avatarImageResponse(metaRes, token);
      }
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    const imageRes = await fetch(imageUrl, {
      redirect: "follow",
      signal: controller.signal,
      headers: { Accept: "image/*,*/*;q=0.8", "User-Agent": "q-gallery-avatar/1.0" },
    });
    clearTimeout(timer);
    if (!imageRes.ok) return new Response(`Facebook returned HTTP ${imageRes.status}`, { status: 502 });
    const type = String(imageRes.headers.get("content-type") || "").toLowerCase();
    if (!type.startsWith("image/")) return new Response("Facebook did not return an image", { status: 502 });
    return avatarImageResponse(imageRes, token);
  } catch (cause) {
    return new Response(String(cause && cause.message || "Avatar request failed"), { status: 502 });
  }
}

async function avatarImageResponse(upstream, token) {
  const bytes = await upstream.arrayBuffer();
  if (!bytes.byteLength || bytes.byteLength > 2 * 1024 * 1024) {
    return new Response("Empty or oversized avatar image", { status: 502 });
  }
  if (bytes.byteLength < 800) return new Response("Facebook returned a tiny placeholder", { status: 404 });
  return new Response(bytes, {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "image/jpeg",
      "Cache-Control": token ? "public, max-age=604800, stale-while-revalidate=86400" : "public, max-age=600",
      "X-Content-Type-Options": "nosniff",
      "X-Avatar-Token": token ? "1" : "0",
    },
  });
}

// Analytics uses its own binding; it never reads or writes gallery R2 data.
const recentVisits = new Map();
async function recordVisit(request, env, authenticate) {
  if (!env.GALLERY_ANALYTICS) return new Response(null,{status:204});
  const origin = new URL(request.url).origin;
  if (request.method !== 'POST' || request.headers.get('origin') !== origin) return json({ok:false},403);
  const body = await boundedJson(request,2048);
  if (!(await authenticate(body))) return json({ok:false},401);
  const now = Date.now()/1000;
  if (!/^[a-f0-9]{32}$/.test(body.visitor || '') || !/^[a-f0-9]{32}$/.test(body.session || '') ||
      !Number.isFinite(body.seconds) || body.seconds<0 || body.seconds>28800 ||
      !Number.isFinite(body.started) || body.started>now+60 || body.started<now-86400) return json({ok:false},400);
  // Best-effort isolate deduplication and visitor rate cap, without storage calls.
  const key = body.visitor;
  let recent = recentVisits.get(key);
  if (!recent || now-recent.at>60) recent={at:now,sessions:new Set()};
  if (recent.sessions.has(body.session)) return new Response(null,{status:204});
  if (recent.sessions.size>=12) return json({ok:false},429);
  recent.sessions.add(body.session);
  if(recentVisits.size>=2048) recentVisits.delete(recentVisits.keys().next().value);
  recentVisits.set(key,recent);
  env.GALLERY_ANALYTICS.writeDataPoint({indexes:[new URL(request.url).hostname],
    blobs:[body.visitor,body.session], doubles:[body.seconds,body.started]});
  return new Response(null,{status:204,headers:{'Cache-Control':'no-store','X-Robots-Tag':'noindex'}});
}

const galleryWorker = {
  async fetch(request, env) {
    const url = new URL(request.url);
    if(url.pathname==='/api/visit') return await recordVisit(request,env,()=>isAuthenticated(request,env));
    if (request.method === "OPTIONS") {
      if (url.pathname === "/api/publish-cache") {
        return new Response(null, { status: 204 });
      }
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": url.origin,
          "Access-Control-Allow-Headers": "content-type",
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
      if (!(await isAuthenticated(request, env))) return json({ok:false,error:'需要访问密码'},401);
      const result = await handleCdn(request, env);
      if (!accessPassword(env)) return result;
      const headers = new Headers(result.headers);
      headers.set('Cache-Control','private, no-store');
      headers.delete('Access-Control-Allow-Origin');
      return new Response(result.body,{status:result.status,headers});
    }
    if (url.pathname === "/api/auth") {
      return handleAuth(request, env);
    }
    if (url.pathname === "/api/avatar" && request.method === "GET") {
      return handleAvatar(request, env);
    }
    if (url.pathname === "/api/drive-image" && request.method === "GET") {
      if (!(await isAuthenticated(request, env))) return json({ok:false,error:'需要访问密码'},401);
      const result = await handleDriveImage(request);
      if (!accessPassword(env)) return result;
      const headers = new Headers(result.headers);
      headers.set('Cache-Control','private, no-store');
      return new Response(result.body,{status:result.status,headers});
    }
    const asset = await env.ASSETS.fetch(request);
    const headers = new Headers(asset.headers);
    if (!headers.has("X-Frame-Options")) headers.set("X-Frame-Options", "DENY");
    if (!headers.has("X-Content-Type-Options")) headers.set("X-Content-Type-Options", "nosniff");
    return new Response(asset.body, { status: asset.status, statusText: asset.statusText, headers });
  },
};


export default {
  async fetch(request,env) {
    let response;
    try { response = await galleryWorker.fetch(request,env); }
    catch { response = json({ok:false,error:'服务暂时不可用'},500); }
    const headers = new Headers(response.headers);
    headers.set('X-Robots-Tag','noindex, nofollow, noarchive, nosnippet, noimageindex');
    return new Response(response.body,{status:response.status,statusText:response.statusText,headers});
  },
};
