const crypto = require("crypto");

const COOKIE_NAME = "q_gallery_auth";
const MAX_CACHE_BYTES = 60 * 1024 * 1024;
const MANIFEST_URL = process.env.DRIVE_CACHE_MANIFEST_URL || "";
const DRIVE_FILE_IDS = [
  "1qYU46yLqgGv0O--3uPxI96em8_QOpIo4",
  "1R92nn1qd-MGNgfCoDAcUHR_DA8gzCwyD",
  "1jSHMAQSi9OOB_frmbLhLeK6Yrf5prJR7",
  "1_n6q3hCNtcTfIgpr1ABzBAingnkmNnNU",
  "1hlFmAiq8di3AjnSw-R8q4py4DMjYYzxl",
  "1bZtc6tRRnleYArr2WEyo8RVx71sTqMbK",
  "18TCyLCvSQEzCbiDhiIAW73lPv_2niBTL",
  "1sjYFUSl-bBG6KjiTsuweDdSyfkDo6D0M",
  "19DT-xvT0WsYrOLziufkYm5oiwcWY9NH2",
  "1qdEpnDkM-v0apjtmlstAC8IZ3YY-F6tt",
  "10O48ml9XY_057NMIAyGkD82r8N7vg4ky",
  "1cWa-kocVl2HmjYFcR5NBpPjRAxvMcbGB",
  "1grf8PnImuuT5pXW_pvaDajd9XjZnDCsC",
  "1LednAqFhyhFQZuoCQmMrWc-XGmmOJXtM",
  "1_B2Y3jP_j7Qe_C57AEDAk14lVSOCt0Ua",
  "1piKnBjthTCeMxYAMgY6ap0ySoXo_n1S4",
  "1qJKT9wZPH067_9JnzE3wA2Qykf-PG7Cw",
  "1T1edAPO9I1DOkESGAYPe3oMwUVrZevpB",
  "1ZKHe-yhDagylAHOJJeeFxouC0X9WxFqD",
  "1tIIVhAJdNEN7hIQb9oagkYUZVAUz_EJe",
  "1G_IeFOKbt-eT4uVrVGGBZHoXoRd1SVTW",
  "1ZZvvH6LlXRilMGMS_vZBFfbeppWSNaWq",
  "1n_GgfSEACAsEPgk69Rxc0PgRf-pu6aUG",
  "11zIkKySnoUKq67V2fcz7bSJViBGuV3E0",
  "1OXqkiK19han00s6zGdItd7JsvtmmHRnO",
  "17k_D7ohs05MqPYa2V2OVBER7WNFqTVTh"
];

function getAccessPassword() {
  return process.env.ACCESS_PASSWORD || "";
}

function getSigningSecret() {
  return process.env.AUTH_SECRET || process.env.APPS_SCRIPT_API_SECRET || getAccessPassword() || "q-gallery";
}

function sign(value) {
  return crypto.createHmac("sha256", getSigningSecret()).update(value).digest("hex");
}

function parseCookies(header = "") {
  return Object.fromEntries(
    header
      .split(";")
      .map(part => part.trim())
      .filter(Boolean)
      .map(part => {
        const index = part.indexOf("=");
        return index === -1 ? [part, ""] : [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      })
  );
}

function isAuthenticated(req) {
  const password = getAccessPassword();
  if (!password) return true;
  const cookies = parseCookies(req.headers.cookie || "");
  return cookies[COOKIE_NAME] === sign(password);
}

function send(res, status, payload) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", status === 200 ? "s-maxage=300, stale-while-revalidate=3600" : "no-store");
  res.end(JSON.stringify(payload));
}

async function fetchJsonFile(fileId) {
  const url = `https://drive.google.com/uc?export=download&id=${encodeURIComponent(fileId)}`;
  return fetchJsonUrl(url);
}

async function fetchJsonUrl(url) {
  const response = await fetch(url, {
    headers: { "User-Agent": "q-gallery-drive-cache" }
  });
  if (!response.ok) throw new Error(`Drive JSON download failed: ${response.status}`);

  const text = await response.text();
  if (text.length > MAX_CACHE_BYTES) throw new Error("Drive JSON is too large");
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("Drive response is not JSON");
  }
}

async function fetchManifestChunks() {
  if (!MANIFEST_URL) return null;
  const manifest = await fetchJsonUrl(MANIFEST_URL);
  const chunks = Array.isArray(manifest.chunks) ? manifest.chunks : [];
  if (!chunks.length) throw new Error("Manifest has no chunks");
  const chunkData = await Promise.all(chunks.map(chunk => {
    if (chunk.url) return fetchJsonUrl(chunk.url);
    if (chunk.fileId) return fetchJsonFile(chunk.fileId);
    throw new Error("Manifest chunk missing url/fileId");
  }));
  return {
    manifest,
    chunks: chunkData
  };
}

/**
 * 优先读 PUBLIC_JSON_CACHE_URL（CDN / 二级域名整包 JSON）→ 1 次请求，不打 Apps Script。
 * 再回退 manifest / 多文件 Drive 分片。
 */
async function fetchPublicJsonCache() {
  const publicUrl = String(process.env.PUBLIC_JSON_CACHE_URL || process.env.Q_GALLERY_PUBLIC_CACHE_URL || "").trim();
  if (!publicUrl) return null;
  const data = await fetchJsonUrl(publicUrl);
  const rows = Array.isArray(data.rows)
    ? data.rows
    : Array.isArray(data.assets)
      ? data.assets
      : Array.isArray(data)
        ? data
        : [];
  if (!rows.length && data.ok === false) {
    throw new Error(data.error || "Public JSON cache empty");
  }
  return {
    ok: true,
    source: "public-json-cache",
    runId: data.runId || data.buildId || data.cacheMeta?.buildId || `public-${Date.now()}`,
    updatedAt: data.updatedAt || data.cacheMeta?.updatedAt || new Date().toISOString(),
    totalRows: Number(data.totalRows || data.total || rows.length) || rows.length,
    rows
  };
}

module.exports = async function handler(req, res) {
  if (req.method !== "GET") {
    send(res, 405, { ok: false, error: "Method not allowed" });
    return;
  }
  if (!isAuthenticated(req)) {
    send(res, 401, { ok: false, error: "需要访问密码。" });
    return;
  }

  try {
    // 1) CDN / 二级域名整包（推荐，最省配额）
    try {
      const publicPayload = await fetchPublicJsonCache();
      if (publicPayload && publicPayload.rows && publicPayload.rows.length) {
        send(res, 200, publicPayload);
        return;
      }
    } catch (publicError) {
      // fall through to Drive paths
    }

    // 2) Manifest 分片 / 内置 Drive 文件列表
    const manifestResult = await fetchManifestChunks();
    const chunks = manifestResult
      ? manifestResult.chunks
      : await Promise.all(DRIVE_FILE_IDS.map(fetchJsonFile));
    const rows = [];
    let buildId = manifestResult?.manifest?.buildId || "";
    chunks
      .sort((a, b) => Number(a.index || 0) - Number(b.index || 0))
      .forEach(chunk => {
        if (chunk.buildId && !buildId) buildId = chunk.buildId;
        if (Array.isArray(chunk.rows)) rows.push(...chunk.rows);
        if (Array.isArray(chunk.assets)) rows.push(...chunk.assets);
      });

    send(res, 200, {
      ok: true,
      source: "drive-cache",
      runId: buildId ? `drive-${buildId}` : `drive-${Date.now()}`,
      updatedAt: manifestResult?.manifest?.updatedAt || new Date().toISOString(),
      totalRows: rows.length,
      rows
    });
  } catch (error) {
    send(res, 502, {
      ok: false,
      error: error.message || String(error)
    });
  }
};
