/**
 * 管理员 / 脚本日更：Drive Manifest → R2
 * POST /api/publish-cache
 * Header: x-publish-secret: <CACHE_PUBLISH_SECRET>
 *
 * Body:
 *  - { manifestUrl } 镜像全部分片
 *  头像 token 请放到 Pages 密钥 FB_GRAPH_ACCESS_TOKEN，不再写入公开 R2。
 */
import {
  CHUNK_PREFIX,
  LAST_REFRESH_KEY,
  MANIFEST_KEY,
  mirrorManifestToR2,
  normalizeDriveUrl,
  publicBase,
} from "../_lib/mirror-cache.js";

/** R2 内私有键：仅 Worker binding 读取，不写进 public manifest */
export const FB_TOKEN_R2_KEY = "promo/_internal/fb-graph-access-token";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function readPublishSecret(request) {
  const header = (request.headers.get("x-publish-secret") || "").trim();
  if (header) return header;
  const auth = (request.headers.get("authorization") || "").trim();
  if (/^bearer\s+/i.test(auth)) return auth.replace(/^bearer\s+/i, "").trim();
  return "";
}

export async function onRequestPost(context) {
  const { request, env } = context;
  const secret = readPublishSecret(request);
  const expected = String(env.CACHE_PUBLISH_SECRET || "").trim();
  if (!expected) {
    return json({ ok: false, error: "Unauthorized. Cloudflare 未配置 CACHE_PUBLISH_SECRET。" }, 401);
  }
  if (!secret) {
    return json({ ok: false, error: "Unauthorized. 缺少 x-publish-secret 请求头。" }, 401);
  }
  const enc = new TextEncoder();
  const left = await crypto.subtle.digest("SHA-256", enc.encode("cmp|" + secret));
  const right = await crypto.subtle.digest("SHA-256", enc.encode("cmp|" + expected));
  if (!crypto.subtle.timingSafeEqual(left, right)) {
    return json({ ok: false, error: "Unauthorized. 发布密钥与 Cloudflare CACHE_PUBLISH_SECRET 不一致。" }, 401);
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  const tokenFromBody = String(body.fbAccessToken || body.facebookAccessToken || "").trim();
  const action = String(body.action || "").trim().toLowerCase();
  if (tokenFromBody) {
    await deleteLegacyFbToken(env);
  }

  // Python 汇总工具直推：分片写入 R2，不再经过 Apps Script / Drive
  if (action === "put-chunk" || action === "finalize") {
    try {
      const result = await publishDirectToR2(env, action, body);
      return json(result);
    } catch (error) {
      const status = error.code === "NO_R2" ? 501 : 400;
      return json({ ok: false, error: error.message || String(error) }, status);
    }
  }

  if (action === "set-fb-token" || (tokenFromBody && !body.manifestUrl && action !== "mirror")) {
    return json(
      {
        ok: false,
        error:
          "Facebook 头像 token 请写入 Cloudflare Pages 密钥 FB_GRAPH_ACCESS_TOKEN，不要再写到 R2 公开前缀。",
      },
      400
    );
  }

  try {
    const result = await mirrorManifestToR2(
      env,
      normalizeDriveUrl(body.manifestUrl || env.DRIVE_CACHE_MANIFEST_URL || "")
    );
    return json({
      ...result,
      fbTokenStored: false,
      hint: "前端读 publicManifestUrl（CDN，0 Functions）。头像 token 请用 Pages 密钥 FB_GRAPH_ACCESS_TOKEN。",
    });
  } catch (error) {
    const status = error.code === "NO_R2" ? 501 : error.code === "NO_MANIFEST" ? 400 : 502;
    return json({ ok: false, error: error.message || String(error) }, status);
  }
}

export async function onRequestGet() {
  return json({
    ok: true,
    service: "q-gallery-promo-publish-cache",
    usage:
      "POST x-publish-secret + { manifestUrl } | { action:'put-chunk', buildId, index, assets } | { action:'finalize', buildId, totalRows, chunkCount, maxDate }",
  });
}

function pad4(n) {
  return String(n).padStart(4, "0");
}

async function publishDirectToR2(env, action, body) {
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.put !== "function") {
    const err = new Error("R2 binding GALLERY_CACHE missing");
    err.code = "NO_R2";
    throw err;
  }
  const base = publicBase(env);
  const buildId = String(body.buildId || "").trim();
  if (!buildId) throw new Error("buildId required");

  if (action === "put-chunk") {
    const index = Number(body.index || 0);
    if (!Number.isFinite(index) || index < 1 || index > 2000) throw new Error("index must be 1..2000");
    const assets = Array.isArray(body.assets) ? body.assets : [];
    if (assets.length > 5000) throw new Error("too many assets in chunk");
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
    if (payload.length > 6 * 1024 * 1024) throw new Error("chunk payload too large");
    await env.GALLERY_CACHE.put(key, payload, {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=3600",
      },
      customMetadata: { buildId, index: String(index) },
    });
    return {
      ok: true,
      action: "put-chunk",
      key,
      index,
      count: assets.length,
      url: `${base}/${key}`,
    };
  }

  const chunkCount = Number(body.chunkCount || 0);
  if (!chunkCount || chunkCount < 1 || chunkCount > 2000) throw new Error("chunkCount must be 1..2000");
  const publishedChunks = [];
  for (let i = 1; i <= chunkCount; i += 1) {
    const name = `${CHUNK_PREFIX}${pad4(i)}.json`;
    publishedChunks.push({
      index: i,
      fileId: "",
      name,
      url: `${base}/${name}`,
      size: 0,
    });
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
    customMetadata: {
      buildId,
      totalRows: String(body.totalRows || 0),
    },
  });
  await env.GALLERY_CACHE.put(
    LAST_REFRESH_KEY,
    JSON.stringify({
      at: publishedAt,
      buildId,
      totalRows: body.totalRows || 0,
      chunkCount,
      source: "python",
    }),
    {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=60",
      },
    }
  );
  return {
    ok: true,
    action: "finalize",
    buildId,
    totalRows: body.totalRows || 0,
    chunkCount,
    publicManifestUrl: `${base}/${MANIFEST_KEY}`,
    hint: "前端读 publicManifestUrl（CDN）",
  };
}

async function deleteLegacyFbToken(env) {
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.delete !== "function") return;
  try {
    await env.GALLERY_CACHE.delete(FB_TOKEN_R2_KEY);
  } catch {
    // ignore missing key
  }
}
