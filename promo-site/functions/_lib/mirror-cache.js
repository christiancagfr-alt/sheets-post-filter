/**
 * Drive Manifest → R2 镜像（日更 / 手动刷新共用）
 */

export const MANIFEST_KEY = "promo/manifest.json";
export const CHUNK_PREFIX = "promo/chunks/";
export const LAST_REFRESH_KEY = "promo/last-refresh.json";

export function normalizeDriveUrl(url) {
  const text = String(url || "").trim();
  if (!text) return "";
  const idMatch = text.match(/\/file\/d\/([a-zA-Z0-9_-]+)/) || text.match(/[?&]id=([a-zA-Z0-9_-]+)/);
  if (idMatch) {
    return (
      "https://drive.google.com/uc?export=download&confirm=t&id=" +
      encodeURIComponent(idMatch[1])
    );
  }
  if (/drive\.google\.com/i.test(text) && !/[?&]confirm=/i.test(text)) {
    return text + (text.includes("?") ? "&" : "?") + "confirm=t";
  }
  return text;
}

export function publicBase(env) {
  return String(
    env.PUBLIC_CACHE_BASE ||
      env.PUBLIC_JSON_CACHE_BASE ||
      "https://gallery-cache.zhixianglife.com"
  ).replace(/\/$/, "");
}

export function defaultManifestUrl(env) {
  return normalizeDriveUrl(
    env.DRIVE_CACHE_MANIFEST_URL ||
      "https://drive.google.com/uc?export=download&id=1zj9ZJHGO_q6iuWdHiBgCghE0LA--snxi"
  );
}

async function fetchText(url) {
  const res = await fetch(url, {
    headers: { "User-Agent": "q-gallery-promo-publish" },
    redirect: "follow",
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Upstream HTTP ${res.status} for ${url.slice(0, 100)}`);
  if (/<!doctype html>|<html/i.test(text)) {
    throw new Error("Upstream returned HTML (Drive 未公开或拦截): " + url.slice(0, 80));
  }
  return text;
}

/**
 * @returns {Promise<object>} publish result
 */
export async function mirrorManifestToR2(env, manifestUrlInput) {
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.put !== "function") {
    const err = new Error("R2 binding GALLERY_CACHE missing");
    err.code = "NO_R2";
    throw err;
  }

  const manifestUrl = normalizeDriveUrl(manifestUrlInput || defaultManifestUrl(env));
  if (!manifestUrl) {
    const err = new Error("Missing manifestUrl / DRIVE_CACHE_MANIFEST_URL");
    err.code = "NO_MANIFEST";
    throw err;
  }

  const manifestText = await fetchText(manifestUrl);
  const manifest = JSON.parse(manifestText);
  const chunks = Array.isArray(manifest.chunks) ? manifest.chunks : [];
  if (!chunks.length) throw new Error("Manifest has no chunks");

  const base = publicBase(env);
  const publishedChunks = [];
  let totalAssets = 0;

  for (let i = 0; i < chunks.length; i += 1) {
    const chunk = chunks[i];
    const src =
      normalizeDriveUrl(chunk.url) ||
      (chunk.fileId
        ? `https://drive.google.com/uc?export=download&confirm=t&id=${encodeURIComponent(chunk.fileId)}`
        : "");
    if (!src) throw new Error(`Chunk ${i + 1} missing url/fileId`);

    const text = await fetchText(src);
    const data = JSON.parse(text);
    const n = Array.isArray(data.assets)
      ? data.assets.length
      : Array.isArray(data.rows)
        ? data.rows.length
        : 0;
    totalAssets += n;

    const key = `${CHUNK_PREFIX}${String(i + 1).padStart(4, "0")}.json`;
    await env.GALLERY_CACHE.put(key, text, {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=3600",
      },
      customMetadata: {
        buildId: String(manifest.buildId || ""),
        index: String(i + 1),
      },
    });

    publishedChunks.push({
      index: i + 1,
      fileId: chunk.fileId || "",
      name: key,
      url: `${base}/${key}`,
      size: text.length,
    });
  }

  const publishedAt = new Date().toISOString();
  const outManifest = {
    ...manifest,
    source: "q-gallery-promo-r2",
    publishedAt,
    chunkCount: publishedChunks.length,
    chunks: publishedChunks,
    mirroredAssets: totalAssets,
  };

  await env.GALLERY_CACHE.put(MANIFEST_KEY, JSON.stringify(outManifest), {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
      cacheControl: "public, max-age=300",
    },
    customMetadata: {
      buildId: String(manifest.buildId || ""),
      totalRows: String(manifest.totalRows || totalAssets),
    },
  });

  await env.GALLERY_CACHE.put(
    LAST_REFRESH_KEY,
    JSON.stringify({
      at: publishedAt,
      buildId: manifest.buildId || "",
      totalRows: manifest.totalRows || totalAssets,
      chunkCount: publishedChunks.length,
    }),
    {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "no-store",
      },
    }
  );

  return {
    ok: true,
    storage: "r2",
    buildId: manifest.buildId || "",
    totalRows: manifest.totalRows || totalAssets,
    mirroredAssets: totalAssets,
    chunkCount: publishedChunks.length,
    publishedAt,
    publicManifestUrl: `${base}/${MANIFEST_KEY}`,
    publicChunkBase: `${base}/${CHUNK_PREFIX}`,
  };
}

export async function readLastRefresh(env) {
  if (!env.GALLERY_CACHE || typeof env.GALLERY_CACHE.get !== "function") return null;
  try {
    const obj = await env.GALLERY_CACHE.get(LAST_REFRESH_KEY);
    if (!obj) return null;
    return JSON.parse(await obj.text());
  } catch {
    return null;
  }
}
