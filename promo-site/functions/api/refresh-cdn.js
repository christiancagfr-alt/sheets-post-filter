/**
 * 前端「刷新 CDN」按钮：
 * - 需已登录站点（若配置了 ACCESS_PASSWORD）
 * - 有频率限制（默认 30 分钟 1 次），避免狂点烧 Drive/Worker 配额
 * - 服务端用已保存的 DRIVE_CACHE_MANIFEST_URL 镜像到 R2
 *
 * POST /api/refresh-cdn
 * Body 可选: { manifestUrl, force?: boolean }
 */
import { isAuthenticated, json, readJsonBody } from "../_lib/auth.js";
import {
  mirrorManifestToR2,
  normalizeDriveUrl,
  readLastRefresh,
  defaultManifestUrl,
} from "../_lib/mirror-cache.js";

const DEFAULT_MIN_INTERVAL_MS = 30 * 60 * 1000; // 30 分钟

export async function onRequestPost(context) {
  const { request, env } = context;

  if (!(await isAuthenticated(request, env))) {
    return json({ ok: false, error: "需要先登录站点后再刷新 CDN。" }, 401);
  }

  let body = {};
  try {
    body = await readJsonBody(request);
  } catch {
    body = {};
  }

  const minInterval = Math.max(
    60 * 1000,
    Number(env.REFRESH_CDN_MIN_INTERVAL_MS || DEFAULT_MIN_INTERVAL_MS)
  );
  const force = body.force === true && String(env.ALLOW_FORCE_CDN_REFRESH || "") === "1";

  const last = await readLastRefresh(env);
  if (last?.at && !force) {
    const elapsed = Date.now() - Date.parse(last.at);
    if (Number.isFinite(elapsed) && elapsed >= 0 && elapsed < minInterval) {
      const waitMin = Math.ceil((minInterval - elapsed) / 60000);
      return json(
        {
          ok: false,
          error: `刷新过于频繁。为节省 Drive / Worker 配额，请约 ${waitMin} 分钟后再试。`,
          lastRefresh: last,
          retryAfterMinutes: waitMin,
          quotaWarning: true,
        },
        429
      );
    }
  }

  const manifestUrl = normalizeDriveUrl(
    body.manifestUrl || defaultManifestUrl(env)
  );

  try {
    const result = await mirrorManifestToR2(env, manifestUrl);
    return json({
      ...result,
      message:
        "CDN 已更新。请到首页点「同步」拉取最新数据到本机（用户日常读 CDN，不再打 Drive）。",
      quotaNote: "本次消耗：Drive 读分片若干次 + R2 写入；请勿短时间重复点击。",
    });
  } catch (error) {
    const status = error.code === "NO_R2" ? 501 : error.code === "NO_MANIFEST" ? 400 : 502;
    return json({ ok: false, error: error.message || String(error) }, status);
  }
}

export async function onRequestGet(context) {
  const { env } = context;
  const last = await readLastRefresh(env);
  const minInterval = Math.max(
    60 * 1000,
    Number(env.REFRESH_CDN_MIN_INTERVAL_MS || DEFAULT_MIN_INTERVAL_MS)
  );
  let canRefresh = true;
  let retryAfterMinutes = 0;
  if (last?.at) {
    const elapsed = Date.now() - Date.parse(last.at);
    if (Number.isFinite(elapsed) && elapsed >= 0 && elapsed < minInterval) {
      canRefresh = false;
      retryAfterMinutes = Math.ceil((minInterval - elapsed) / 60000);
    }
  }
  return json({
    ok: true,
    service: "q-gallery-promo-refresh-cdn",
    lastRefresh: last,
    canRefresh,
    retryAfterMinutes,
    minIntervalMinutes: Math.round(minInterval / 60000),
    warning: "刷新会消耗 Drive 下载与 Worker 配额，请仅在表格更新后点击。",
  });
}
