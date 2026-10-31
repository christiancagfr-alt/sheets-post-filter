const crypto = require("crypto");

const COOKIE_NAME = "q_gallery_auth";
const MAX_CACHE_BYTES = 60 * 1024 * 1024;
const MANIFEST_URL = process.env.DRIVE_CACHE_MANIFEST_URL || "";
const DRIVE_FILE_IDS = String(process.env.DRIVE_CACHE_FILE_IDS || "").split(",").map(value => value.trim()).filter(Boolean);

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
  return crypto.createHmac("sha256", secret).update(value).digest("hex");
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
  const [expires, mac] = String(cookies[COOKIE_NAME] || "").split(".");
  if (!/^\d+$/.test(expires) || Number(expires) < Date.now()) return false;
  return secretsEqual(mac, sign(`session|${expires}|${password}`));
}

function send(res, status, payload) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", status === 200 && !getAccessPassword() ? "s-maxage=300, stale-while-revalidate=3600" : "private, no-store");
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
