import { isAuthenticated, json } from "../_lib/auth.js";

const MAX_CACHE_BYTES = 60 * 1024 * 1024;

/**
 * Cloudflare Pages Worker 子请求有限（免费约 50）。
 * 禁止在一次请求里 Promise.all 拉几十个 Drive 分片。
 *
 * 支持：
 *  1) PUBLIC_JSON_CACHE_URL / Q_GALLERY_PUBLIC_CACHE_URL — 整包 JSON（推荐，1 次子请求）
 *  2) ?fileId= 或 ?url= — 只拉 1 个分片
 *  3) DRIVE_CACHE_MANIFEST_URL — 只返回 manifest 元数据（不拉分片）
 */

async function fetchJsonUrl(url) {
  const response = await fetch(url, {
    headers: { "User-Agent": "q-gallery-drive-cache" },
    redirect: "follow",
  });
  if (!response.ok) throw new Error(`缓存下载失败：${response.status}`);
  const text = await response.text();
  if (text.length > MAX_CACHE_BYTES) throw new Error("缓存文件太大");
  if (/<!doctype html>|<html/i.test(text)) {
    throw new Error("上游返回 HTML（Drive 未公开或配额拦截），请检查文件共享");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("上游不是有效 JSON");
  }
}

function extractRows(data) {
  if (!data) return [];
  if (Array.isArray(data.rows)) return data.rows;
  if (Array.isArray(data.assets)) return data.assets;
  if (Array.isArray(data)) return data;
  return [];
}

async function fetchPublicJsonCache(env) {
  const publicUrl = String(env.PUBLIC_JSON_CACHE_URL || env.Q_GALLERY_PUBLIC_CACHE_URL || "").trim();
  if (!publicUrl) return null;
  const data = await fetchJsonUrl(publicUrl);
  const rows = extractRows(data);
  if (!rows.length && data.ok === false) {
    throw new Error(data.error || "Public JSON cache empty");
  }
  return {
    ok: true,
    source: "public-json-cache",
    runId: data.runId || data.buildId || data.cacheMeta?.buildId || `public-${Date.now()}`,
    updatedAt: data.updatedAt || data.cacheMeta?.updatedAt || new Date().toISOString(),
    totalRows: Number(data.totalRows || data.total || rows.length) || rows.length,
    rows,
  };
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== "GET") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }
  if (!(await isAuthenticated(request, env))) {
    return json({ ok: false, error: "需要访问密码。" }, 401);
  }

  const url = new URL(request.url);
  const fileId = String(url.searchParams.get("fileId") || "").trim();
  const singleUrl = String(url.searchParams.get("url") || "").trim();

  try {
    // 1) 整包公共 JSON
    try {
      const publicPayload = await fetchPublicJsonCache(env);
      if (publicPayload && publicPayload.rows && publicPayload.rows.length) {
        return json(publicPayload, 200, {
          "Cache-Control": "s-maxage=300, stale-while-revalidate=3600",
        });
      }
    } catch (e) {
      // continue
    }

    // 2) 单分片（供调试 / 兼容）
    if (fileId || singleUrl) {
      const target = singleUrl
        ? singleUrl
        : `https://drive.google.com/uc?export=download&confirm=t&id=${encodeURIComponent(fileId)}`;
      const data = await fetchJsonUrl(target);
      const rows = extractRows(data);
      return json({
        ok: true,
        source: "drive-cache-single",
        runId: data.runId || data.buildId || `drive-single-${Date.now()}`,
        updatedAt: data.updatedAt || new Date().toISOString(),
        totalRows: rows.length,
        rows,
      });
    }

    // 3) 仅返回 manifest（不拉分片，避免 Too many subrequests）
    const manifestUrl = String(env.DRIVE_CACHE_MANIFEST_URL || "").trim();
    if (manifestUrl) {
      const manifest = await fetchJsonUrl(manifestUrl);
      return json({
        ok: true,
        source: "drive-manifest-meta",
        runId: manifest.buildId ? `drive-${manifest.buildId}` : `drive-${Date.now()}`,
        updatedAt: manifest.updatedAt || new Date().toISOString(),
        totalRows: Number(manifest.totalRows || 0),
        rows: [],
        manifest,
        hint: "请在前端用分片 URL 逐个下载；Worker 不会一次拉全部分片。",
      });
    }

    return json(
      {
        ok: false,
        error:
          "未配置 PUBLIC_JSON_CACHE_URL / DRIVE_CACHE_MANIFEST_URL。请在 Cloudflare 环境变量中配置，或前端用 Q_GALLERY_DRIVE_CACHE_URLS 分片逐个同步。",
        hint: "不要在 Worker 里一次拉 20+ 个 Drive 文件，会触发 Too many subrequests。",
      },
      503
    );
  } catch (error) {
    return json({ ok: false, error: error.message || String(error) }, 502);
  }
}
