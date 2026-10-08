"use strict";

const API_URL = "/api/gallery";
const AUTH_URL = "/api/auth";
const CACHE_PROXY_URL = "/api/cache-proxy";
const DRIVE_CACHE_URL = "/api/drive-cache";
const DB_NAME = "q-gallery-cache-v1";
const DB_VERSION = 1;
const PAGE_SIZE = 300;
const FALLBACK_PAGE_SIZE = 100;
const REFRESH_BATCH_SIZE = 1000;
const INITIAL_VISIBLE = 80;
const MORE_STEP = 80;
const REFRESH_POLL_MS = 60000;
// 优先公共 CDN（R2 / 二级域名）— 与素材库一样：日常 0 Worker / 0 Apps Script
const DIRECT_CACHE_URL =
  window.Q_GALLERY_DRIVE_CACHE_URL ||
  window.Q_GALLERY_PUBLIC_CACHE_URL ||
  "";
function getManifestUrl() {
  return (
    window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL ||
    window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL ||
    ""
  );
}
// 兼容旧代码引用
const DIRECT_CACHE_MANIFEST_URL = window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL || "";
const SNAPSHOT_NORMALIZATION_VERSION = "map3";
const DIRECT_CACHE_URLS = Array.isArray(window.Q_GALLERY_DRIVE_CACHE_URLS)
  ? window.Q_GALLERY_DRIVE_CACHE_URLS.filter(Boolean)
  : [];
const CACHE_URL_STORAGE_KEY = "qGalleryDriveCacheUrl";
// 本地有数据且 buildId 一致时，自动同步跳过（减 Drive/Worker 配额）
const LOCAL_CACHE_MAX_AGE_MS = Number(window.Q_GALLERY_LOCAL_CACHE_MAX_AGE_MS || 6 * 60 * 60 * 1000);

const els = {
  authGate: document.getElementById("authGate"),
  appShell: document.getElementById("appShell"),
  passwordForm: document.getElementById("passwordForm"),
  passwordInput: document.getElementById("passwordInput"),
  passwordError: document.getElementById("passwordError"),
  gallery: document.getElementById("gallery"),
  template: document.getElementById("cardTemplate"),
  emptyState: document.getElementById("emptyState"),
  loadMoreBtn: document.getElementById("loadMoreBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  syncBtn: document.getElementById("syncBtn"),
  refreshCdnBtn: document.getElementById("refreshCdnBtn"),
  linkImportBtn: document.getElementById("linkImportBtn"),
  csvImportBtn: document.getElementById("csvImportBtn"),
  csvImportInput: document.getElementById("csvImportInput"),
  clearFiltersBtn: document.getElementById("clearFiltersBtn"),
  statusText: document.getElementById("statusText"),
  syncText: document.getElementById("syncText"),
  lastUpdated: document.getElementById("lastUpdated"),
  recordCount: document.getElementById("recordCount"),
  filteredCount: document.getElementById("filteredCount"),
  refreshProgress: document.getElementById("refreshProgress"),
  refreshLog: document.getElementById("refreshLog"),
  searchInput: document.getElementById("searchInput"),
  dateStart: document.getElementById("dateStart"),
  dateEnd: document.getElementById("dateEnd"),
  categoryFilter: document.getElementById("categoryFilter"),
  sourceTypeFilter: document.getElementById("sourceTypeFilter"),
  sourceChannelFilter: document.getElementById("sourceChannelFilter"),
  postTypeFilter: document.getElementById("postTypeFilter"),
  minLikes: document.getElementById("minLikes"),
  minComments: document.getElementById("minComments"),
  minLead: document.getElementById("minLead"),
  sortSelect: document.getElementById("sortSelect")
};

const state = {
  records: new Map(),
  filtered: [],
  visible: INITIAL_VISIBLE,
  syncCursor: "0",
  activeRunId: "",
  localCacheRunId: "",
  localCacheRows: 0,
  refreshTimer: null,
  statusLogs: [],
  refreshSnapshot: null,
  db: null,
  syncRunning: false,
  refreshRunning: false,
  appStarted: false,
  filters: {
    search: "",
    dateStart: "",
    dateEnd: "",
    category: "",
    sourceType: "",
    sourceChannel: "",
    postType: "",
    minLikes: "",
    minComments: "",
    minLead: "",
    sort: "dateDesc"
  }
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindAuthEvents();
  if (window.location.protocol === "file:") {
    await startApp();
    setStatus("本地预览模式：已跳过密码；API 需要部署到 Vercel 后才能同步。", true);
    return;
  }
  const authenticated = await checkAuthStatus();
  if (!authenticated) {
    showAuth();
    return;
  }
  await startApp();
}

function bindAuthEvents() {
  els.passwordForm.addEventListener("submit", event => {
    event.preventDefault();
    void submitPassword();
  });
}

async function checkAuthStatus() {
  try {
    const data = await authApi({ action: "status" });
    return Boolean(data.authenticated);
  } catch (error) {
    showAuth(error.message || "密码服务暂时不可用。");
    return false;
  }
}

async function submitPassword() {
  const password = els.passwordInput.value.trim();
  if (!password) {
    showAuth("请输入访问密码。");
    return;
  }

  els.passwordError.textContent = "";
  els.passwordForm.querySelector("button").disabled = true;
  try {
    const data = await authApi({ action: "login", password });
    if (!data.authenticated) throw new Error(data.error || "密码错误。");
    els.passwordInput.value = "";
    await startApp();
  } catch (error) {
    showAuth(error.message || "密码错误。");
    els.passwordInput.select();
  } finally {
    els.passwordForm.querySelector("button").disabled = false;
  }
}

async function startApp() {
  els.authGate.hidden = true;
  els.appShell.hidden = false;
  if (state.appStarted) return;
  state.appStarted = true;
  bindEvents();
  state.db = await openDb().catch(() => null);
  await loadLocalCache();
  render();

  // 与素材库一致：本地 IndexedDB 优先；仅在缺失/过期/版本变化时拉云端
  if (state.records.size > 0) {
    const skip = await shouldSkipAutoSync();
    if (skip) {
      setStatus(`已使用本地缓存 ${state.records.size} 条（未消耗云端配额）。需要更新时点「同步」。`);
      appendStatusLog("本地缓存有效，跳过自动同步（减配额）");
      return;
    }
  }

  if (getManifestUrl() || DIRECT_CACHE_URL || DIRECT_CACHE_URLS.length) {
    await syncSharedCache({ reset: true, maxPages: 1 });
    return;
  }
  const savedCacheUrl = getSavedCacheUrl();
  if (savedCacheUrl) {
    setStatus("已找到上次保存的 JSON 缓存链接，可点击“同步”更新本地缓存。");
    appendStatusLog("已保存 JSON 缓存链接；同步会读取该 JSON 文件");
    return;
  }
  setStatus(state.records.size ? `已加载本地缓存 ${state.records.size} 条。` : "未配置 JSON 缓存链接，请使用 JSON 同步或 CSV 本地导入。");
  appendStatusLog("页面不会自动通过 Apps Script 同步数据；JSON 同步仅在手动点击时读取缓存文件。");
}

/** 本地有数据且远程 buildId 未变 / 未超时 → 跳过自动同步 */
async function shouldSkipAutoSync() {
  if (!state.records.size) return false;
  const meta = await idbGet("meta", "summary").catch(() => null);
  const savedAt = meta?.lastUpdated ? Date.parse(meta.lastUpdated) : 0;
  const ageOk = savedAt && Date.now() - savedAt < LOCAL_CACHE_MAX_AGE_MS;
  if (!ageOk) return false;

  // 轻量比对：只下 manifest 元数据（几 KB），不对分片
  const manifestUrl = getManifestUrl();
  if (!manifestUrl) return Boolean(ageOk && state.localCacheRunId);

  try {
    const remote = await fetchCacheSnapshotFromUrl(manifestUrl);
    const remoteBuild = remote.buildId
      ? `drive-${remote.buildId}-${SNAPSHOT_NORMALIZATION_VERSION}`
      : remote.runId || "";
    if (remoteBuild && state.localCacheRunId && remoteBuild === state.localCacheRunId) {
      appendStatusLog(`云端 buildId 未变（${remote.buildId || remoteBuild}），跳过全量下载`);
      return true;
    }
    // 分片数/条数也作参考
    if (
      state.localCacheRunId &&
      Number(remote.totalRows || 0) > 0 &&
      Number(remote.totalRows) === state.records.size &&
      ageOk
    ) {
      appendStatusLog("条数一致且本地未过期，跳过全量下载");
      return true;
    }
  } catch (error) {
    // 远程探测失败时，本地仍有效则继续用本地，避免无谓重试烧配额
    appendStatusLog(`远程版本探测失败，继续使用本地缓存：${error.message || error}`);
    return true;
  }
  return false;
}

function showAuth(message = "") {
  els.appShell.hidden = true;
  els.authGate.hidden = false;
  els.passwordError.textContent = message;
  window.setTimeout(() => els.passwordInput.focus(), 0);
}

function bindEvents() {
  const filterInputs = [
    ["search", els.searchInput],
    ["dateStart", els.dateStart],
    ["dateEnd", els.dateEnd],
    ["category", els.categoryFilter],
    ["sourceType", els.sourceTypeFilter],
    ["sourceChannel", els.sourceChannelFilter],
    ["postType", els.postTypeFilter],
    ["minLikes", els.minLikes],
    ["minComments", els.minComments],
    ["minLead", els.minLead],
    ["sort", els.sortSelect]
  ];

  filterInputs.forEach(([key, element]) => {
    element.addEventListener("input", () => {
      state.filters[key] = element.value.trim();
      state.visible = INITIAL_VISIBLE;
      render();
    });
  });

  els.clearFiltersBtn.addEventListener("click", () => {
    Object.keys(state.filters).forEach(key => {
      state.filters[key] = key === "sort" ? "dateDesc" : "";
    });
    syncFilterControls();
    state.visible = INITIAL_VISIBLE;
    render();
  });

  els.loadMoreBtn.addEventListener("click", () => {
    state.visible += MORE_STEP;
    render();
  });

  els.syncBtn.addEventListener("click", () => {
    if (state.refreshRunning) {
      setStatus("后台正在刷新数据，请等待完成后再同步。", true);
      appendStatusLog("刷新优先：后台刷新中，暂不同步共享缓存");
      return;
    }
    void syncSharedCache({ reset: true, maxPages: Infinity });
  });

  els.refreshCdnBtn?.addEventListener("click", () => {
    void refreshCdnCache();
  });

  els.linkImportBtn?.addEventListener("click", () => {
    void importCacheFromLink();
  });

  els.csvImportBtn?.addEventListener("click", () => {
    els.csvImportInput?.click();
  });

  els.csvImportInput?.addEventListener("change", event => {
    const file = event.target.files?.[0];
    if (file) void importCsvCache(file);
    event.target.value = "";
  });

  els.refreshBtn?.addEventListener("click", () => {
    void startRefresh();
  });
}

function syncFilterControls() {
  els.searchInput.value = state.filters.search;
  els.dateStart.value = state.filters.dateStart;
  els.dateEnd.value = state.filters.dateEnd;
  els.categoryFilter.value = state.filters.category;
  els.sourceTypeFilter.value = state.filters.sourceType;
  els.sourceChannelFilter.value = state.filters.sourceChannel;
  els.postTypeFilter.value = state.filters.postType;
  els.minLikes.value = state.filters.minLikes;
  els.minComments.value = state.filters.minComments;
  els.minLead.value = state.filters.minLead;
  els.sortSelect.value = state.filters.sort;
}

async function loadLocalCache() {
  appendStatusLog("正在读取浏览器本地缓存");
  if (!state.db) {
    setStatus("浏览器缓存不可用，直接从共享缓存同步。");
    appendStatusLog("浏览器缓存不可用，改为读取共享缓存");
    return;
  }

  const [records, meta] = await Promise.all([
    idbGetAll("records"),
    idbGet("meta", "summary")
  ]);

  records.forEach(record => {
    const normalized = normalizeRecord(record);
    if (!isHeaderRecord(normalized)) state.records.set(normalized.id, normalized);
  });
  if (meta?.lastUpdated) {
    els.lastUpdated.textContent = formatDateTime(meta.lastUpdated);
  }
  state.localCacheRunId = stringOr(meta?.runId);
  state.localCacheRows = toNumber(meta?.totalRows) || records.length;
  if (state.localCacheRunId) state.activeRunId = state.localCacheRunId;

  setStatus(records.length ? `已载入本地缓存 ${records.length} 条。` : "本地暂无缓存，正在同步共享缓存。");
  appendStatusLog(records.length ? `本地缓存 ${records.length} 条已载入` : "本地暂无缓存，准备同步共享缓存");
  rebuildCatalogs();
}

/**
 * 把 Drive 最新 Manifest 镜像到 R2 CDN（管理员操作，耗配额）
 * 成功后自动再「同步」到本机 IndexedDB
 */
async function refreshCdnCache() {
  const confirmed = window.confirm(
    "【请谨慎点击】刷新 CDN 会：\n\n" +
      "1. 从 Google Drive 重新下载全部分片（消耗 Drive 流量/配额）\n" +
      "2. 写入 Cloudflare R2（消耗 Worker 子请求）\n" +
      "3. 默认 30 分钟内只能成功刷新 1 次\n\n" +
      "适用：表格已重新导出 Manifest 后更新网站数据。\n" +
      "日常只看数据请点「同步」，不要点本按钮。\n\n" +
      "确定要刷新 CDN 吗？"
  );
  if (!confirmed) {
    appendStatusLog("已取消刷新 CDN");
    return;
  }

  if (els.refreshCdnBtn) els.refreshCdnBtn.disabled = true;
  setStatus("正在刷新 CDN（可能需要 1～3 分钟）…");
  appendStatusLog("CDN 刷新开始：Drive Manifest → R2");

  try {
    const response = await fetch("/api/refresh-cdn", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        manifestUrl:
          window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL ||
          window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL ||
          ""
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `刷新失败 HTTP ${response.status}`);
    }

    const msg =
      `CDN 已更新：${data.mirroredAssets || data.totalRows || "?"} 条 / ${data.chunkCount || "?"} 分片` +
      (data.buildId ? `（build ${String(data.buildId).slice(0, 8)}…）` : "");
    setStatus(msg);
    appendStatusLog(msg);
    if (data.quotaNote) appendStatusLog(data.quotaNote);
    appendStatusLog("正在把 CDN 新数据同步到本机…");
    await syncSharedCache({ reset: true, maxPages: Infinity, quiet: false });
  } catch (error) {
    setStatus(error.message || "刷新 CDN 失败", true);
    appendStatusLog(error.message || "刷新 CDN 失败");
  } finally {
    if (els.refreshCdnBtn) els.refreshCdnBtn.disabled = false;
  }
}

async function syncSharedCache({ reset = false, maxPages = Infinity, quiet = false } = {}) {
  if (state.syncRunning) return;
  state.syncRunning = true;

  try {
    state.syncCursor = "0";
    if (!quiet) {
      setStatus("正在从 JSON 缓存链接同步...");
      appendStatusLog("同步使用 JSON 文件链接；必要时才通过同域代理下载");
    }
    const snapshotLoaded = await syncSharedCacheSnapshot(quiet);
    if (!snapshotLoaded) {
      throw new Error(
        "云端缓存不可用：站点内旧 Drive 分片已 404。" +
          "请在表格运行 qGalleryExportDriveCacheManifest，然后点「JSON同步」粘贴新的 Manifest URL；或使用 CSV 导入。"
      );
    }
  } catch (error) {
    setStatus(error.message || "同步失败。", true);
    appendStatusLog(error.message || "同步失败");
  } finally {
    state.syncRunning = false;
  }
}

async function syncSharedCacheSnapshot(quiet = false) {
  // 1) CDN / Drive Manifest（浏览器逐个下分片；CDN 失败再试 Drive fallback）
  const manifestCandidates = [
    window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL || "",
    window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL || ""
  ].filter(Boolean);

  for (const manifestUrl of [...new Set(manifestCandidates)]) {
    try {
      const manifestData = await fetchManifestCacheSnapshot({
        initialOnly: true,
        manifestUrl
      });

      if (manifestData?.current) return true;
      if (manifestData?.remainingChunks?.length && !manifestData.rows?.length) {
        void syncRemainingManifestChunks(manifestData);
        return true;
      }
      if (manifestData && await applySnapshotData(manifestData, quiet, { replace: true })) {
        if (manifestData.remainingChunks?.length) void syncRemainingManifestChunks(manifestData);
        return true;
      }
    } catch (error) {
      const msg = error && error.message ? error.message : String(error || "");
      if (!quiet) {
        appendStatusLog(`${/gallery-cache|r2\.dev/i.test(manifestUrl) ? "CDN" : "Drive"}: ${msg || "unavailable"}`);
      }
    }
  }
  if (!quiet) {
    appendStatusLog(
      "若 CDN/Drive 均失败：在表格运行 qGalleryExportDriveCacheManifest，再 POST /api/publish-cache 镜像到 R2，或点「JSON同步」。"
    );
  }

  // 2) 已保存的整包 JSON 链接 / 配置的 DIRECT_CACHE_URL
  try {
    const driveData = await fetchDriveCacheSnapshot();
    if (driveData && await applySnapshotData(driveData, quiet, { replace: true })) return true;
  } catch (error) {
    if (!quiet) appendStatusLog(error.message || "Drive cache unavailable");
  }

  // 3) Apps Script 分页（可选；未配置正确 Web App 时容易「未登录/Unauthorized」）
  if (window.Q_GALLERY_ENABLE_APPS_SCRIPT_SYNC === true) {
    try {
      const apiData = await fetchAppsScriptCacheSnapshot({ initialOnly: true });
      if (apiData && await applySnapshotData(apiData, quiet, { replace: true })) {
        if (apiData.nextCursor) void syncRemainingAppsScriptCache(apiData);
        return true;
      }
    } catch (error) {
      if (!quiet) {
        const msg = error && error.message ? error.message : String(error || "");
        appendStatusLog(msg || "Apps Script cache unavailable");
        if (/未登录|Unauthorized|401|需要访问密码|Unauthorized/i.test(msg)) {
          appendStatusLog(
            "Apps Script 鉴权失败：请确认 Cloudflare 的 APPS_SCRIPT_URL 是结果表 Web App，且 secret 一致；或改用 JSON/CSV 同步。"
          );
        }
      }
    }
  } else if (!quiet) {
    appendStatusLog("已跳过 Apps Script 同步（默认关闭）。请用「JSON同步」粘贴导出的 Manifest/JSON 链接，或 CSV 导入。");
  }

  return false;
}

async function fetchManifestCacheSnapshot({ initialOnly = false, manifestUrl = "" } = {}) {
  const url = manifestUrl || getManifestUrl();
  if (!url) return null;
  appendStatusLog(`Reading manifest cache: ${url.slice(0, 72)}${url.length > 72 ? "…" : ""}`);
  const manifest = await fetchCacheSnapshotFromUrl(url);
  const chunks = Array.isArray(manifest.chunks) ? manifest.chunks : [];
  if (!chunks.length) throw new Error("Manifest has no chunks.");
  const manifestRunId = manifest.buildId ? `drive-${manifest.buildId}-${SNAPSHOT_NORMALIZATION_VERSION}` : "";
  const manifestRows = toNumber(manifest.totalRows);
  if (manifestRunId && manifestRows && state.localCacheRunId === manifestRunId && state.records.size >= manifestRows) {
    setStatus(`Local manifest cache is current: ${state.records.size}/${manifestRows} records.`);
    appendStatusLog("Manifest cache unchanged; using local IndexedDB cache");
    return { current: true };
  }

  const sortedChunks = chunks
    .slice()
    .sort((a, b) => Number(a.index || 0) - Number(b.index || 0));
  // 本地条数少于清单总数：必须重新拉全部分片，不能按 3000/片估进度（实际每片 800）
  if (manifestRunId && manifestRows && state.records.size > 0 && state.records.size < manifestRows) {
    appendStatusLog(`本地仅 ${state.records.size}/${manifestRows} 条，重新下载全部分片`);
  }
  const firstChunk = await fetchManifestChunk(sortedChunks[0]);
  const rows = extractSnapshotRows(firstChunk);

  return {
    source: "drive-cache",
    runId: manifestRunId || firstChunk.runId || firstChunk.buildId || `drive-${Date.now()}`,
    updatedAt: manifest.updatedAt || firstChunk.updatedAt || new Date().toISOString(),
    totalRows: manifestRows || toNumber(firstChunk.totalRows) || rows.length,
    rows,
    remainingChunks: initialOnly ? sortedChunks.slice(1) : []
  };
}

async function fetchManifestChunk(chunk) {
  // 直读 CDN，不经 /api/cache-proxy（Worker 免费版 10ms CPU，大分片会中途失败，只装到约 3 万条）
  if (chunk.url) return fetchCacheSnapshotFromUrl(normalizeCacheUrl(chunk.url));
  if (chunk.fileId) {
    return fetchCacheSnapshotFromUrl(
      `https://drive.google.com/uc?export=download&id=${encodeURIComponent(chunk.fileId)}`
    );
  }
  throw new Error("Manifest chunk missing url/fileId.");
}

function extractSnapshotRows(data) {
  if (Array.isArray(data?.rows)) return data.rows;
  if (Array.isArray(data?.assets)) return data.assets;
  return [];
}

async function syncRemainingManifestChunks(snapshot) {
  const chunks = Array.isArray(snapshot.remainingChunks) ? snapshot.remainingChunks : [];
  let loaded = snapshot.rows.length;
  const totalRows = snapshot.totalRows || 0;
  const batchSize = 4;
  for (let index = 0; index < chunks.length; index += batchSize) {
    const batch = chunks.slice(index, index + batchSize);
    let chunkData = [];
    try {
      chunkData = await Promise.all(batch.map(fetchManifestChunk));
    } catch (error) {
      appendStatusLog((error && error.message) || "分片下载失败，重试本批");
      await new Promise(r => setTimeout(r, 800));
      try {
        chunkData = await Promise.all(batch.map(fetchManifestChunk));
      } catch (error2) {
        appendStatusLog((error2 && error2.message) || "Manifest background sync failed");
        continue;
      }
    }
    const rows = chunkData.flatMap(extractSnapshotRows);
    if (!rows.length) continue;
    loaded += rows.length;
    await applySnapshotData({
      source: "drive-cache",
      runId: snapshot.runId,
      updatedAt: snapshot.updatedAt,
      totalRows,
      rows
    }, true, { replace: false });
    setStatus(`Manifest cache loaded ${state.records.size}/${totalRows || loaded} records.`);
  }
  if (totalRows && state.records.size < totalRows) {
    appendStatusLog(`加载结束仍只有 ${state.records.size}/${totalRows} 条，请再点「同步」`);
  } else {
    setStatus(`Manifest cache loaded ${state.records.size}/${totalRows || state.records.size} records.`);
  }
}

async function fetchAppsScriptCacheSnapshot({ initialOnly = false, startCursor = "0" } = {}) {
  appendStatusLog("Reading Apps Script cache pages");
  const rows = [];
  let cursor = startCursor;
  let runId = "";
  let totalRows = 0;
  let catalogs = null;
  let page = 0;
  let nextCursor = "";
  const seenCursors = new Set();

  while (cursor !== "" && cursor !== null && cursor !== undefined) {
    if (seenCursors.has(String(cursor))) break;
    seenCursors.add(String(cursor));
    page += 1;
    const data = await fetchAssetsPage(cursor, REFRESH_BATCH_SIZE);
    const pageRows = Array.isArray(data.rows)
      ? data.rows
      : Array.isArray(data.assets)
        ? data.assets
        : [];
    rows.push(...pageRows);
    runId = runId || data.runId || "";
    totalRows = toNumber(data.totalRows) || totalRows;
    catalogs = catalogs || data.catalogs || null;
    appendStatusLog(`Apps Script page ${page}: ${rows.length}/${totalRows || "?"}`);

    nextCursor = data.nextCursor ?? "";
    if (!nextCursor || !pageRows.length) break;
    if (initialOnly) break;
    cursor = String(nextCursor);
    if (page >= 200) throw new Error("Apps Script cache has too many pages.");
  }

  return {
    source: "sheet-cache",
    runId: runId || `sheet-${Date.now()}`,
    updatedAt: new Date().toISOString(),
    totalRows: totalRows || rows.length,
    catalogs,
    nextCursor,
    rows
  };
}

async function syncRemainingAppsScriptCache(snapshot) {
  let cursor = snapshot.nextCursor || "";
  let loaded = snapshot.rows.length;
  const totalRows = snapshot.totalRows || 0;
  const runId = snapshot.runId || "";

  while (cursor) {
    try {
      const data = await fetchAssetsPage(cursor, REFRESH_BATCH_SIZE);
      const rows = Array.isArray(data.rows)
        ? data.rows
        : Array.isArray(data.assets)
          ? data.assets
          : [];
      if (!rows.length) break;
      loaded += rows.length;
      await applySnapshotData({
        source: "sheet-cache",
        runId: runId || data.runId || "",
        updatedAt: new Date().toISOString(),
        totalRows: totalRows || data.totalRows || loaded,
        catalogs: data.catalogs || snapshot.catalogs || null,
        rows
      }, true, { replace: false });
      setStatus(`Apps Script cache loaded ${loaded}/${totalRows || "?"} records.`);
      cursor = data.nextCursor || "";
    } catch (error) {
      appendStatusLog(error.message || "Apps Script background sync failed");
      break;
    }
  }
}

async function fetchDriveCacheSnapshot() {
  // 重要：不要走 /api/drive-cache 一次拉全部分片（Cloudflare Worker 会 Too many subrequests）
  // 浏览器端逐个下载（每片 1 次请求，可走 cache-proxy）
  if (DIRECT_CACHE_URLS.length) {
    appendStatusLog(`按分片逐个下载（共 ${DIRECT_CACHE_URLS.length} 个），避免 Worker 子请求超限`);
    return fetchCacheSnapshotsFromUrls(DIRECT_CACHE_URLS);
  }
  const cacheUrl = getActiveCacheUrl();
  if (!cacheUrl) return null;
  return fetchCacheSnapshotFromUrl(cacheUrl);
}

/** @deprecated 会触发 CF Worker 子请求上限，保留仅作调试 */
async function fetchBundledDriveCacheSnapshot() {
  appendStatusLog("警告：整包 /api/drive-cache 在 Cloudflare 上易超子请求上限，改用分片下载");
  if (DIRECT_CACHE_URLS.length) return fetchCacheSnapshotsFromUrls(DIRECT_CACHE_URLS);
  const response = await fetch(`${DRIVE_CACHE_URL}?t=${encodeURIComponent(Date.now())}`, {
    cache: "no-store",
    credentials: "same-origin"
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showAuth("请先输入访问密码。");
    throw new Error(data.error || "需要访问密码。");
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `云端缓存下载失败：${response.status}`);
  }
  return data;
}

async function fetchCacheSnapshotsFromUrls(urls) {
  const chunks = [];
  for (let index = 0; index < urls.length; index += 1) {
    appendStatusLog(`JSON 分片 ${index + 1}/${urls.length} 下载中`);
    chunks.push(await fetchCacheSnapshotFromUrl(urls[index]));
  }
  return mergeCacheSnapshots(chunks);
}

function mergeCacheSnapshots(chunks) {
  const rows = [];
  let runId = "";
  let updatedAt = "";
  chunks.forEach(chunk => {
    if (!chunk) return;
    if (chunk.buildId && !runId) runId = `drive-${chunk.buildId}`;
    if (chunk.runId && !runId) runId = chunk.runId;
    if (chunk.updatedAt && !updatedAt) updatedAt = chunk.updatedAt;
    if (Array.isArray(chunk.rows)) rows.push(...chunk.rows);
    if (Array.isArray(chunk.assets)) rows.push(...chunk.assets.map(asset => ({
      ...asset,
      source: "drive-assets"
    })));
  });
  return {
    source: "drive-cache",
    runId: runId || `drive-${Date.now()}`,
    updatedAt: updatedAt || new Date().toISOString(),
    totalRows: rows.length,
    rows
  };
}

async function fetchCacheSnapshotFromUrl(cacheUrl) {
  const url = normalizeCacheUrl(cacheUrl);
  // CDN / 同站 / r2.dev：浏览器直读，不经 Worker（与素材库一致，减配额）
  const isCdn =
    /zhixianglife\.com|r2\.dev|cloudflarestorage\.com|pages\.dev/i.test(url) ||
    url.startsWith("/");
  const separator = url.includes("?") ? "&" : "?";
  const finalUrl = isCdn ? url : `${url}${separator}t=${encodeURIComponent(Date.now())}`;
  try {
    const response = await fetch(finalUrl, {
      cache: isCdn ? "default" : "no-store",
      credentials: "omit",
      mode: "cors"
    });
    if (!response.ok) throw new Error(`共享缓存下载失败：${response.status}`);
    return response.json();
  } catch (error) {
    // Drive 常无 CORS → 才走同域代理（会消耗 Worker 配额，应尽量少用）
    if (isCdn) throw error;
    appendStatusLog("直连下载失败，改用同域代理导入（消耗少量 Worker 配额）");
    return fetchCacheSnapshotViaProxy(url);
  }
}

async function fetchCacheSnapshotViaProxy(cacheUrl) {
  const response = await fetch(CACHE_PROXY_URL, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ url: cacheUrl })
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showAuth("请先输入访问密码。");
    throw new Error(data.error || "需要访问密码。");
  }
  if (!response.ok || data.ok === false) {
    const detail = data.error || `缓存代理下载失败：${response.status}`;
    // 统一成前端好识别的文案
    if (/404|not found/i.test(detail)) {
      throw new Error(`缓存链接下载失败：404（Drive 分片不存在或未公开）`);
    }
    throw new Error(detail);
  }
  return data;
}

async function fetchSharedCacheSnapshotFromApi() {
  return api("driveCacheContent");
}

async function applySnapshotData(data, quiet = false, options = {}) {
  const runId = data.runId || "";
  const sourceRows = Array.isArray(data.rows)
    ? data.rows
    : Array.isArray(data.assets)
      ? data.assets
      : [];
  const rows = sourceRows.length
    ? sourceRows
      .map(row => normalizeSnapshotRecord(row, runId))
      .filter(record => !isHeaderRecord(record))
    : [];

  if (!rows.length && toNumber(data.totalRows) > 0) return false;
  if (options.replace) {
    state.records = new Map();
    if (state.db) await clearStore("records");
  }
  rows.forEach(record => state.records.set(record.id, record));
  if (rows.length && state.db) {
    try {
      await saveRecords(rows);
    } catch (error) {
      appendStatusLog("浏览器本地缓存写满，改为仅内存保留：" + (error && error.message ? error.message : error));
    }
  }

  if (runId) {
    state.activeRunId = runId;
    await removeRecordsOutsideRun(runId);
  }
  state.syncCursor = "";
  state.localCacheRunId = runId || state.localCacheRunId;
  state.localCacheRows = toNumber(data.totalRows) || rows.length || state.records.size;
  await saveSummary({
    lastUpdated: data.updatedAt || new Date().toISOString(),
    runId: state.localCacheRunId,
    totalRows: state.localCacheRows
  });
  rebuildCatalogs(data.catalogs);
  render();

  const sourceLabel = data.source === "drive-cache" || data.source === "drive-cache-proxy"
    ? "Drive 缓存"
    : data.source === "sheet-cache"
      ? "共享缓存"
      : "完整缓存";
  const message = `${sourceLabel}已下载，共 ${state.records.size} 条。`;
  if (!quiet) setStatus(message);
  els.syncText.textContent = message;
  appendStatusLog(message);
  return true;
}

function normalizeSnapshotRecord(row, runId) {
  const repaired = repairShiftedManifestRow(row);
  const assetId = row.catalogId && row.rowNumber
    ? `${row.catalogId}-${row.rowNumber}-${row.id || row.fileId || ""}`
    : row.id;
  return normalizeRecord({
    ...repaired,
    id: assetId || row.id,
    name: repaired.name || repaired.title,
    postLink: repaired.postLink || repaired.viewUrl || repaired.sourceUrl,
    thumbnailUrl: repaired.thumbnailUrl || repaired.previewUrl,
    category: repaired.category || repaired.type,
    postType: repaired.postType || repaired.type,
    authorName: repaired.authorName || repaired.maker,
    sourceType: repaired.sourceType || repaired.group || repaired.catalogId,
    sourceChannel: repaired.sourceChannel || repaired.catalogId,
    cacheRunId: runId
  });
}

function repairShiftedManifestRow(row) {
  const categoryUrl = extractUrl(row.category || row.type || "");
  const thumbUrl = extractUrl(row.thumbnailUrl || row.previewUrl || "");
  if (!categoryUrl || thumbUrl) return row;

  const rawPostType = stringOr(row.postType);
  const numericPostType = rawPostType && /^\d+(?:\.\d+)?$/.test(rawPostType);
  return {
    ...row,
    thumbnailUrl: categoryUrl,
    thumbnailFallbackUrl: row.thumbnailFallbackUrl || row.previewUrl || "",
    category: row.ocr || "",
    type: row.ocr || "",
    postType: row.ocr || "",
    ocr: row.thumbnailUrl || row.previewUrl || row.ocr || "",
    ocrTranslation: row.ocrTranslation || "",
    likes: numericPostType ? rawPostType : row.likes,
    authorName: row.maker || row.authorName || ""
  };
}

async function importCacheFromLink() {
  if (!state.db) {
    setStatus("浏览器本地缓存不可用，不能同步 JSON。", true);
    appendStatusLog("JSON 同步失败：IndexedDB 不可用");
    return;
  }

  const previousUrl = getSavedCacheUrl() || DIRECT_CACHE_URL || DIRECT_CACHE_MANIFEST_URL || "";
  const input = window.prompt(
    "粘贴导出结果里的链接（任选其一）：\n" +
      "1) Manifest URL（q-gallery-cache-manifest.json）\n" +
      "2) 整包 JSON 直链（q-gallery-cache.json）\n\n" +
      "在表格 Apps Script 运行 qGalleryExportDriveCacheManifest 后复制。",
    previousUrl || ""
  );
  if (input === null) return;

  const cacheUrl = normalizeCacheUrl(input);
  if (!cacheUrl) {
    setStatus("链接为空，未同步。", true);
    return;
  }
  if (!/^https?:\/\//i.test(cacheUrl) && !cacheUrl.startsWith("/")) {
    setStatus("请输入 http(s) 公开链接，或站点相对路径。", true);
    return;
  }

  els.linkImportBtn.disabled = true;
  try {
    setStatus("正在读取 JSON / Manifest...");
    appendStatusLog("JSON 同步开始：正在下载");
    // Manifest：走分片逻辑
    if (/manifest/i.test(cacheUrl) || /q-gallery-cache-manifest/i.test(cacheUrl)) {
      window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL = cacheUrl;
      saveCacheUrl(cacheUrl);
      const ok = await syncSharedCache({ reset: true, maxPages: Infinity, quiet: false });
      if (!ok) throw new Error("Manifest 同步失败：分片可能 404 或未公开。请重新导出。");
      const message = `Manifest 已同步，本地共 ${state.records.size} 条。`;
      setStatus(message);
      els.syncText.textContent = message;
      appendStatusLog(message);
      return;
    }

    const data = await fetchCacheSnapshotFromUrl(cacheUrl);
    // 若是 manifest 结构
    if (data && Array.isArray(data.chunks) && data.chunks.length) {
      window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL = cacheUrl;
      saveCacheUrl(cacheUrl);
      const ok = await syncSharedCache({ reset: true, maxPages: Infinity, quiet: false });
      if (!ok) throw new Error("Manifest 同步失败。");
    } else {
      const ok = await applySnapshotData(data, false, { replace: true });
      if (!ok) throw new Error("链接内容不是有效的图库缓存 JSON。");
      saveCacheUrl(cacheUrl);
    }

    const message = `JSON 缓存已同步到本地，共 ${state.records.size} 条。`;
    setStatus(message);
    els.syncText.textContent = message;
    appendStatusLog(message);
  } catch (error) {
    setStatus(error.message || "JSON 同步失败。", true);
    appendStatusLog(error.message || "JSON 同步失败");
  } finally {
    els.linkImportBtn.disabled = false;
  }
}

async function importCsvCache(file) {
  if (!state.db) {
    setStatus("浏览器本地缓存不可用，不能导入 CSV。", true);
    appendStatusLog("CSV 导入失败：IndexedDB 不可用");
    return;
  }

  els.csvImportBtn.disabled = true;
  try {
    setStatus(`正在导入 ${file.name}...`);
    appendStatusLog(`CSV 导入开始：${file.name}`);
    const text = await file.text();
    const parsedRows = parseCsv(text);
    const records = csvRowsToRecords(parsedRows)
      .map(row => normalizeRecord(row))
      .filter(record => !isHeaderRecord(record));

    if (!records.length) throw new Error("CSV 中没有可导入的数据行。");

    state.records = new Map();
    records.forEach(record => state.records.set(record.id, record));

    const runId = `csv-${Date.now()}`;
    const cacheRows = Array.from(state.records.values()).map(record => ({
      ...record,
      cacheRunId: runId
    }));

    await clearStore("records");
    await saveRecords(cacheRows);
    state.activeRunId = runId;
    state.localCacheRunId = runId;
    state.localCacheRows = cacheRows.length;
    state.syncCursor = "";
    await saveSummary({
      lastUpdated: new Date().toISOString(),
      runId,
      totalRows: cacheRows.length
    });
    rebuildCatalogs();
    render();

    const message = `CSV 已导入本地缓存，共 ${cacheRows.length} 条。`;
    setStatus(message);
    els.syncText.textContent = message;
    appendStatusLog(message);
  } catch (error) {
    setStatus(error.message || "CSV 导入失败。", true);
    appendStatusLog(error.message || "CSV 导入失败");
  } finally {
    els.csvImportBtn.disabled = false;
  }
}

function getActiveCacheUrl() {
  return normalizeCacheUrl(DIRECT_CACHE_URL || getSavedCacheUrl());
}

function getSavedCacheUrl() {
  try {
    return window.localStorage.getItem(CACHE_URL_STORAGE_KEY) || "";
  } catch (error) {
    return "";
  }
}

function saveCacheUrl(url) {
  try {
    window.localStorage.setItem(CACHE_URL_STORAGE_KEY, normalizeCacheUrl(url));
  } catch (error) {}
}

function normalizeCacheUrl(url) {
  const value = stringOr(url);
  if (!value) return "";
  const fileMatch = value.match(/drive\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) return `https://drive.google.com/uc?export=download&id=${encodeURIComponent(fileMatch[1])}`;
  const idMatch = value.match(/[?&]id=([^&#]+)/i);
  if (/drive\.google\.com/i.test(value) && idMatch) {
    return `https://drive.google.com/uc?export=download&id=${encodeURIComponent(decodeURIComponent(idMatch[1]))}`;
  }
  return value;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const next = text[i + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        cell += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
      continue;
    }

    if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") i += 1;
      row.push(cell);
      if (row.some(value => stringOr(value))) rows.push(row);
      row = [];
      cell = "";
      continue;
    }

    cell += char;
  }

  row.push(cell);
  if (row.some(value => stringOr(value))) rows.push(row);
  return rows;
}

function csvRowsToRecords(rows) {
  if (!rows.length) return [];
  const aliases = csvHeaderAliases();
  const firstRow = rows[0].map(normalizeCsvHeader);
  const hasHeader = firstRow.some(header => header && aliases[header]);
  const dataRows = hasHeader ? rows.slice(1) : rows;
  const fieldIndexes = {};

  if (hasHeader) {
    firstRow.forEach((header, index) => {
      const field = aliases[header];
      if (field && fieldIndexes[field] === undefined) fieldIndexes[field] = index;
    });
  }

  return dataRows.map((row, index) => {
    const rowNumber = hasHeader ? index + 2 : index + 1;
    const value = (key, fallbackIndex) => {
      const headerIndex = fieldIndexes[key];
      return (headerIndex !== undefined ? row[headerIndex] : "") || row[fallbackIndex] || "";
    };
    const postType = value("postType", 12);
    const category = value("category", 8);

    return {
      id: value("id", -1),
      rowNumber: value("rowNumber", -1) || rowNumber,
      name: value("name", 0),
      postId: value("postId", 1),
      postLink: extractUrl(value("postLink", 2)) || value("postLink", 2),
      lead: value("lead", 3),
      water: value("water", 25),
      waterTime: value("waterTime", 26),
      thumbnailUrl: extractUrl(value("thumbnailUrl", 4)) || value("thumbnailUrl", 4),
      thumbnailFallbackUrl: extractUrl(value("thumbnailFallbackUrl", 22)) || value("thumbnailFallbackUrl", 22),
      content: value("content", 5),
      rewrite: value("rewrite", 6),
      date: value("date", 7),
      category,
      likes: value("likes", 9), // J 列
      designer: value("designer", 27), // AB 列：美工
      comments: value("comments", 10),
      shares: value("shares", 11),
      postType,
      ocr: value("ocr", 13),
      ocrTranslation: value("ocrTranslation", 14),
      audioText: value("audioText", 15),
      audioTranslation: value("audioTranslation", 16),
      authorName: value("authorName", 17),
      avatarUrl: extractUrl(value("avatarUrl", 19)) || value("avatarUrl", 19),
      sourceType: value("sourceType", 20),
      sourceChannel: value("sourceChannel", 21),
      conversionRate: value("conversionRate", 23),
      mediaMode: value("mediaMode", -1) || (isImportedVideo(postType, category) ? "video" : "image"),
      pageName: value("pageName", 24)
    };
  });
}

function csvHeaderAliases() {
  return {
    id: "id",
    rownumber: "rowNumber",
    row: "rowNumber",
    行号: "rowNumber",
    名字: "name",
    名称: "name",
    name: "name",
    postid: "postId",
    帖文id: "postId",
    帖子id: "postId",
    postlink: "postLink",
    帖文链接: "postLink",
    链接: "postLink",
    引流: "lead",
    lead: "lead",
    水滴: "water",
    water: "water",
    watertime: "waterTime",
    水滴时间: "waterTime",
    图片: "thumbnailUrl",
    图片链接: "thumbnailUrl",
    thumbnailurl: "thumbnailUrl",
    thumb: "thumbnailUrl",
    image: "thumbnailUrl",
    content: "content",
    文案: "content",
    改写: "rewrite",
    rewrite: "rewrite",
    日期: "date",
    date: "date",
    分类: "category",
    图片类型: "category",
    category: "category",
    点赞: "likes",
    likes: "likes",
    j: "likes",
    j列: "likes",
    评论: "comments",
    comments: "comments",
    分享: "shares",
    shares: "shares",
    美工: "designer",
    美工名字: "designer",
    designer: "designer",
    artist: "designer",
    ab: "designer",
    ab列: "designer",
    帖文类型: "postType",
    posttype: "postType",
    ocr: "ocr",
    ocr翻译: "ocrTranslation",
    ocrtranslation: "ocrTranslation",
    音频文本: "audioText",
    audiotext: "audioText",
    音频翻译: "audioTranslation",
    audiotranslation: "audioTranslation",
    作者: "authorName",
    作者id: "authorName",
    authorname: "authorName",
    来源: "sourceType",
    来源类型: "sourceType",
    sourcetype: "sourceType",
    渠道: "sourceChannel",
    来源渠道: "sourceChannel",
    sourcechannel: "sourceChannel",
    转换率: "conversionRate",
    conversionrate: "conversionRate",
    mediamode: "mediaMode",
    备用图片: "thumbnailFallbackUrl",
    thumbnailfallbackurl: "thumbnailFallbackUrl",
    页面: "pageName",
    pagename: "pageName",
    头像: "avatarUrl",
    avatarurl: "avatarUrl"
  };
}

function normalizeCsvHeader(value) {
  return stringOr(value)
    .replace(/^\ufeff/, "")
    .replace(/[\s_\-（）()：:]/g, "")
    .toLowerCase();
}

function isImportedVideo(postType, category) {
  return /短视频|视频|video|reel|tiktok|douyin|快手/i.test(`${postType} ${category}`);
}

async function loadRemoteCacheVersion(quiet = false) {
  return null;
}

function isLocalCacheCurrent(remoteVersion) {
  if (!remoteVersion) return false;
  if (!state.records.size && !state.localCacheRows) return false;
  const sameRun = remoteVersion.runId && state.localCacheRunId && remoteVersion.runId === state.localCacheRunId;
  const sameRows = remoteVersion.totalRows > 0 && remoteVersion.totalRows === state.records.size;
  return Boolean(sameRun && sameRows);
}

async function startRefresh() {
  if (!els.refreshBtn) return;
  els.refreshBtn.disabled = true;

  try {
    setStatus("正在检查后台刷新状态...");
    els.refreshProgress.textContent = "检查中";
    appendStatusLog("正在检查是否已有后台刷新任务");
    const current = await api("refreshStatus");
    const currentStatus = current.status || current;
    updateRefreshProgress(currentStatus);
    if (isRefreshRunning(currentStatus)) {
      showRefreshAlreadyRunning(currentStatus);
      scheduleRefreshPoll();
      return;
    }

    setStatus("正在启动后台刷新...");
    els.refreshProgress.textContent = "启动中";
    appendStatusLog(`正在启动后台刷新，Sheet 每批 ${REFRESH_BATCH_SIZE} 条`);
    const data = await api("refreshStart", { batchSize: REFRESH_BATCH_SIZE });
    if (!data.ok) throw new Error(data.error || "启动失败");
    state.activeRunId = data.runId || state.activeRunId;
    state.syncCursor = "0";
    state.refreshRunning = true;
    updateRefreshProgress(data.status || data);
    setStatus("后台刷新已启动，刷新完成后会自动同步最新缓存。");
    appendStatusLog("刷新优先：后台刷新已启动，同步已暂停");
    scheduleRefreshPoll();
  } catch (error) {
    if (isTimeoutError(error)) {
      state.syncCursor = "0";
      setStatus("Apps Script 请求超时，但后台刷新可能已经启动；正在继续检查进度。", true);
      els.refreshProgress.textContent = "检查中";
      state.refreshRunning = true;
      appendStatusLog("启动请求超时，继续检查后台进度");
      scheduleRefreshPoll();
      return;
    }
    if (isAlreadyRunningError(error)) {
      setStatus("后台已经在刷新数据，请等待完成。", true);
      els.refreshProgress.textContent = "刷新中";
      state.refreshRunning = true;
      appendStatusLog("后台已经在刷新数据，请等待完成");
      scheduleRefreshPoll();
      return;
    }
    setStatus(error.message || "刷新启动失败。", true);
    els.refreshProgress.textContent = "失败";
    appendStatusLog(error.message || "刷新启动失败");
  } finally {
    els.refreshBtn.disabled = false;
  }
}

async function fetchAssetsPage(cursor, limit) {
  try {
    return await api("assets", { cursor, limit, mode: "cache" });
  } catch (error) {
    const canFallback = limit > FALLBACK_PAGE_SIZE && isTimeoutError(error);
    if (!canFallback) throw error;
    setStatus(`${limit} 条同步超时，临时按 ${FALLBACK_PAGE_SIZE} 条继续读取...`, true);
    appendStatusLog(`${limit} 条超时，切换为每批 ${FALLBACK_PAGE_SIZE} 条`);
    return api("assets", { cursor, limit: FALLBACK_PAGE_SIZE, mode: "cache" });
  }
}

function isTimeoutError(error) {
  return /timed out|timeout|超时/i.test(error?.message || String(error || ""));
}

function isAlreadyRunningError(error) {
  return /already running|正在刷新|已经在刷新/i.test(error?.message || String(error || ""));
}

function scheduleRefreshPoll() {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  void checkRefreshProgress();
  state.refreshTimer = setInterval(async () => {
    void checkRefreshProgress();
  }, REFRESH_POLL_MS);
}

async function checkRefreshProgress() {
  try {
    const data = await api("refreshStatus");
    const status = data.status || data;
    updateRefreshProgress(status);
    appendRefreshStatusLog(status);
    state.refreshRunning = isRefreshRunning(status);

    if (isRefreshComplete(status)) {
      stopRefreshPoll();
      state.refreshRunning = false;
      state.syncCursor = "0";
      setStatus("后台刷新完成，正在同步最新缓存...");
      appendStatusLog("刷新完成，开始同步最新共享缓存");
      await syncSharedCache({ reset: true, maxPages: Infinity, quiet: true });
      if (state.activeRunId) await removeRecordsOutsideRun(state.activeRunId);
      render();
      setStatus("后台刷新完成，共享缓存已更新。");
      appendStatusLog("后台刷新完成，共享缓存已更新");
    } else {
      setStatus("后台正在刷新数据，已暂停同步；刷新完成后会自动同步最新缓存。");
    }
  } catch (error) {
    if (isTimeoutError(error)) {
      setStatus("Apps Script 状态读取超时，稍后自动重试。", true);
      appendStatusLog("状态读取超时，稍后自动重试");
      return;
    }
    setStatus(error.message || "刷新状态读取失败。", true);
    appendStatusLog(error.message || "刷新状态读取失败");
  }
}

function stopRefreshPoll() {
  if (!state.refreshTimer) return;
  clearInterval(state.refreshTimer);
  state.refreshTimer = null;
}

async function loadRefreshStatus() {
  try {
    const data = await api("refreshStatus");
    const status = data.status || data;
    updateRefreshProgress(status);
    appendRefreshStatusLog(status);
    state.refreshRunning = isRefreshRunning(status);
    if (status.running && !isRefreshComplete(status)) scheduleRefreshPoll();
    return status;
  } catch (error) {
    if (/访问密码|未登录|Unauthorized|401|需要访问密码/i.test(error.message || "")) {
      setStatus("登录状态已失效，请重新输入访问密码。", true);
      return null;
    }
    if (state.records.size === 0) {
      setStatus(error.message || "还没有连上 Sheet API，请检查 Apps Script URL 和密钥。", true);
    }
    return null;
  }
}

function updateRefreshProgress(status) {
  if (!status) {
    els.refreshProgress.textContent = "空闲";
    return;
  }

  const processed = toNumber(status.processed);
  const total = toNumber(status.totalRows);
  if (total && processed >= total) {
    els.refreshProgress.textContent = `完成 ${total}`;
  } else if (status.running) {
    els.refreshProgress.textContent = total ? `${processed}/${total}` : `已刷新 ${processed}`;
  } else if (status.error) {
    els.refreshProgress.textContent = "失败";
  } else if (processed) {
    els.refreshProgress.textContent = `完成 ${processed}`;
  } else {
    els.refreshProgress.textContent = "空闲";
  }
}

function isRefreshRunning(status) {
  if (!status) return false;
  const processed = toNumber(status.processed);
  const total = toNumber(status.totalRows);
  return Boolean(status.running) && !(total && processed >= total);
}

function showRefreshAlreadyRunning(status) {
  const processed = toNumber(status.processed);
  const total = toNumber(status.totalRows);
  const detail = total ? `当前进度 ${processed}/${total}。` : processed ? `已处理 ${processed} 条。` : "";
  setStatus(`后台已经在刷新数据，请等待完成。${detail}`, true);
  appendStatusLog(`后台已经在刷新数据，请等待完成${detail ? `，${detail}` : ""}`);
}

function appendRefreshStatusLog(status) {
  if (!status) return;
  const processed = toNumber(status.processed);
  const total = toNumber(status.totalRows);
  const batchSize = toNumber(status.batchSize) || REFRESH_BATCH_SIZE;
  const now = Date.now();
  const previous = state.refreshSnapshot;
  const delta = previous ? processed - previous.processed : 0;
  const changedAt = delta > 0 || !previous ? now : previous.changedAt;
  const waitingMs = Math.max(0, now - changedAt);
  const percent = total ? `${trimNumber((processed / total) * 100, 1)}%` : "";
  const remaining = total ? Math.max(0, total - processed) : 0;
  state.refreshSnapshot = { processed, total, batchSize, changedAt };

  if (status.error) {
    appendStatusLog(`刷新失败：${status.error}`);
  } else if (total) {
    const progressText = `后台刷新 ${formatNumber(processed)}/${formatNumber(total)}（${percent}），剩余 ${formatNumber(remaining)} 条，每批 ${batchSize}`;
    if (delta > 0) {
      appendStatusLog(`${progressText}，本次新增 ${formatNumber(delta)} 条`);
    } else if (status.running) {
      appendStatusLog(`${progressText}，暂无新增，已等待 ${formatDuration(waitingMs)}`);
    } else {
      appendStatusLog(progressText);
    }
  } else if (status.running || processed) {
    const progressText = `后台刷新已处理 ${formatNumber(processed)} 条，每批 ${batchSize}`;
    appendStatusLog(delta > 0 ? `${progressText}，本次新增 ${formatNumber(delta)} 条` : `${progressText}，等待下一批完成`);
  }
}

function appendStatusLog(message) {
  if (!els.refreshLog || !message) return;
  const stamp = new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
  const entry = `${stamp} ${message}`;
  if (state.statusLogs[0] === entry) return;
  state.statusLogs = [entry, ...state.statusLogs].slice(0, 7);
  els.refreshLog.textContent = state.statusLogs.join("\n");
  els.refreshLog.title = state.statusLogs.join("\n");
}

function formatDuration(ms) {
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function isRefreshComplete(status) {
  if (!status) return true;
  const processed = toNumber(status.processed);
  const total = toNumber(status.totalRows);
  return !status.running || Boolean(total && processed >= total);
}

function render() {
  const records = Array.from(state.records.values());
  state.filtered = records.filter(matchesFilters).sort(sortRecords);
  els.recordCount.textContent = String(records.length);
  els.filteredCount.textContent = String(state.filtered.length);
  renderCards();
  rebuildCatalogs();
}

function renderCards() {
  const visibleRecords = state.filtered.slice(0, state.visible);
  els.gallery.textContent = "";

  const fragment = document.createDocumentFragment();
  visibleRecords.forEach(record => {
    const card = els.template.content.firstElementChild.cloneNode(true);
    const link = card.querySelector(".thumb-link");
    const img = card.querySelector(".thumb");
    const title = card.querySelector("h3");
    const badge = card.querySelector(".media-badge");
    const meta = card.querySelector(".meta-line");
    const metrics = card.querySelector(".metric-row");
    const details = card.querySelector(".detail-list");
    const primary = card.querySelector(".primary-text");
    const translated = card.querySelector(".translated-text");
    const actions = card.querySelector(".card-actions");

    const mediaMode = getMediaMode(record);
    const previewUrl = toPreviewUrl(record.thumbnailUrl);
    const fallbackPreviewUrl = toPreviewUrl(record.thumbnailFallbackUrl);
    link.href = record.postLink || previewUrl || fallbackPreviewUrl || "#";
    title.textContent = formatDisplayName(record) || record.postId || "未命名素材";
    badge.textContent = mediaMode === "video" ? "短视频" : "图片";
    badge.classList.toggle("video", mediaMode === "video");
    meta.textContent = [
      record.date ? `🗓️ ${formatDate(record.date)}` : "",
      record.category ? `分类 ${record.category}` : "",
      record.sourceChannel ? `渠道 ${record.sourceChannel}` : "",
      record.sourceType ? `来源 ${record.sourceType}` : ""
    ].filter(Boolean).join(" · ") || "暂无元信息";

    const previewQueue = uniquePreviewCandidates(record.thumbnailUrl, record.thumbnailFallbackUrl);
    if (previewQueue.length) {
      let previewIndex = 0;
      img.alt = record.name || record.postId || "素材预览";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.decoding = "async";
      const useNextPreview = () => {
        if (previewIndex >= previewQueue.length) {
          img.hidden = true;
          return;
        }
        img.hidden = false;
        img.src = previewQueue[previewIndex];
        previewIndex += 1;
      };
      img.addEventListener("error", useNextPreview);
      useNextPreview();
    } else {
      img.hidden = true;
    }

    metrics.append(
      metricNode("点赞", formatCompactNumber(record.likes), "👍", formatNumber(record.likes)),
      metricNode("评论", formatCompactNumber(record.comments), "💬", formatNumber(record.comments)),
      metricNode("引流", formatCompactNumber(record.lead), "🔥", formatNumber(record.lead)),
      metricNode("水滴", formatWaterDisplay(record), null, formatWaterFullValue(record)),
      metricNode("转换率", formatPercentLike(record.conversionRate), "%")
    );

    appendDetails(details, [
      ["贴文类型", record.postType],
      ["图片类型", record.category || record.postType],
      ["名字", formatDisplayName(record)],
      ["美工", record.designer],
      ["改贴", record.rewrite]
    ]);

    const textLabel = mediaMode === "video" ? "音频文本" : "OCR";
    const translatedLabel = mediaMode === "video" ? "音频翻译" : "OCR 翻译";
    setExpandableText(primary, textLabel, record.displayText);
    setExpandableText(translated, translatedLabel, record.displayTranslation);

    if (record.postLink) {
      actions.append(actionLink("打开贴文", record.postLink));
    }
    const copyText = buildComparisonText(record);
    if (copyText) {
      actions.append(copyButton(copyText));
    }

    fragment.append(card);
  });

  els.gallery.append(fragment);
  els.emptyState.hidden = state.filtered.length > 0;
  els.loadMoreBtn.hidden = state.filtered.length <= state.visible;
}

function metricNode(label, value, icon, fullValue) {
  const item = document.createElement("div");
  item.className = "metric";
  const displayValue = value || "0";
  const iconNode = document.createElement("span");
  const valueNode = document.createElement("strong");
  iconNode.className = "metric-icon";
  iconNode.setAttribute("aria-hidden", "true");
  iconNode.textContent = icon || "";
  if (icon === null) iconNode.hidden = true;
  valueNode.textContent = displayValue;
  if (label === "水滴") item.classList.add("water-metric");
  item.title = `${label}: ${fullValue || displayValue}`;
  item.setAttribute("aria-label", `${label} ${displayValue}`);
  item.append(iconNode, valueNode);
  return item;
}

function setExpandableText(element, label, value) {
  const content = stringOr(value) || "暂无";
  const fullText = `${label}：${content}`;
  element.textContent = fullText;
  element.title = fullText;

  if (fullText.length < 82) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "more-text-button";
  button.textContent = "展开";
  button.addEventListener("click", () => {
    const expanded = element.classList.toggle("is-expanded");
    button.textContent = expanded ? "收起" : "展开";
  });
  element.insertAdjacentElement("afterend", button);
}

function appendDetails(list, rows) {
  rows.forEach(([label, value]) => {
    if (!value && value !== 0) return;
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = String(value);
    list.append(dt, dd);
  });
}

function actionLink(label, href) {
  const link = document.createElement("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = label;
  return link;
}

function copyButton(text) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "复制对照";
  button.title = "复制葡语和中文对照";
  button.addEventListener("click", async () => {
    await copyToClipboard(text);
    button.textContent = "已复制";
    window.setTimeout(() => {
      button.textContent = "复制对照";
    }, 1200);
  });
  return button;
}

function buildComparisonText(record) {
  const portuguese = stringOr(record.displayText);
  const chinese = stringOr(record.displayTranslation);
  if (!portuguese && !chinese) return "";

  return [
    portuguese ? `葡语：${portuguese}` : "",
    chinese ? `中文：${chinese}` : ""
  ].filter(Boolean).join("\n\n");
}

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.append(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function matchesFilters(record) {
  const f = state.filters;

  // 搜索：名字 / 美工 / 渠道 / 专页名 / 作者 / 文案 等（多关键词空格=且）
  if (f.search) {
    const haystack = buildSearchHaystack_(record);
    const tokens = normalizeSearchText_(f.search)
      .split(/\s+/)
      .filter(Boolean);
    if (tokens.length && !tokens.every(token => haystack.includes(token))) {
      return false;
    }
  }

  if (f.category && record.category !== f.category) return false;
  if (f.sourceType && record.sourceType !== f.sourceType) return false;
  if (f.sourceChannel && record.sourceChannel !== f.sourceChannel) return false;
  if (f.postType && record.postType !== f.postType) return false;

  const dateValue = dateNumber(record.date);
  if ((f.dateStart || f.dateEnd) && !dateValue) return false;
  if (f.dateStart && dateValue < dateNumber(f.dateStart)) return false;
  if (f.dateEnd && dateValue > dateNumber(f.dateEnd)) return false;

  if (f.minLikes && toNumber(record.likes) < toNumber(f.minLikes)) return false;
  if (f.minComments && toNumber(record.comments) < toNumber(f.minComments)) return false;
  if (f.minLead && toNumber(record.lead) < toNumber(f.minLead)) return false;

  return true;
}

/** 统一大小写、全角半角、去多余空白，便于中文名搜索 */
function normalizeSearchText_(value) {
  let text = stringOr(value).toLowerCase();
  // 全角英数 → 半角
  text = text.replace(/[\uff01-\uff5e]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xfee0));
  text = text.replace(/\u3000/g, " ");
  text = text.replace(/\s+/g, " ").trim();
  return text;
}

function isPlaceholderLabel_(value) {
  const text = stringOr(value);
  if (!text) return true;
  return /^(未找到|#N\/A|N\/A|#VALUE!|#REF!|#NAME\?|-|—|无|null|undefined|未命名)$/i.test(text);
}

/**
 * 卡片标题/名字字段：只用「名字」(A 列)。
 * 禁止用渠道/小组名顶替（否则会显示成「福音讯息」这类渠道）。
 * A 列为「未找到」时原样显示，便于发现问题。
 */
function getRecordDisplayName_(record) {
  if (!record) return "未命名";
  const name = stringOr(record.name);
  if (name) return name;
  const title = stringOr(record.title);
  if (title) return title;
  return "未命名";
}

function buildSearchHaystack_(record) {
  if (!record) return "";
  if (record._searchHaystack) return record._searchHaystack;
  // 搜索仍可匹配美工/渠道等，但展示名不混用
  const parts = [
    record.name,
    record.nameRaw,
    record.title,
    record.designer,
    record.sourceChannel,
    record.pageName,
    record.authorName,
    record.maker,
    record.sourceType,
    record.category,
    record.postType,
    record.postId,
    record.content,
    record.rewrite,
    record.ocr,
    record.ocrTranslation
  ].filter(v => stringOr(v) && !isPlaceholderLabel_(v));
  record._searchHaystack = normalizeSearchText_(parts.join(" "));
  return record._searchHaystack;
}

function sortRecords(a, b) {
  switch (state.filters.sort) {
    case "dateAsc":
      return compareByDate(a, b, "asc");
    case "likesDesc":
      return toNumber(b.likes) - toNumber(a.likes);
    case "commentsDesc":
      return toNumber(b.comments) - toNumber(a.comments);
    case "leadDesc":
      return toNumber(b.lead) - toNumber(a.lead);
    case "leadAsc":
      return toNumber(a.lead) - toNumber(b.lead);
    case "waterDesc":
      return toNumber(b.water) - toNumber(a.water);
    case "conversionDesc":
      return conversionNumber(b.conversionRate) - conversionNumber(a.conversionRate);
    case "dateDesc":
    default:
      return compareByDate(a, b, "desc");
  }
}

function compareByDate(a, b, direction = "desc") {
  const aDate = dateNumber(a.date);
  const bDate = dateNumber(b.date);
  if (aDate || bDate) {
    const diff = direction === "asc" ? aDate - bDate : bDate - aDate;
    if (diff) return diff;
  }
  const previewDiff = Number(hasUsablePreview(b)) - Number(hasUsablePreview(a));
  if (previewDiff) return previewDiff;
  return toNumber(b.rowNumber) - toNumber(a.rowNumber);
}

function rebuildCatalogs(serverCatalogs) {
  const values = {
    categories: new Map(),
    sourceTypes: new Map(),
    sourceChannels: new Map(),
    postTypes: new Map()
  };

  (serverCatalogs?.categories || []).forEach(value => addCatalogValue(values.categories, value));
  (serverCatalogs?.sourceTypes || []).forEach(value => addCatalogValue(values.sourceTypes, value));
  (serverCatalogs?.sourceChannels || []).forEach(value => addCatalogValue(values.sourceChannels, value));
  (serverCatalogs?.postTypes || []).forEach(value => addCatalogValue(values.postTypes, value));

  state.records.forEach(record => {
    addCatalogValue(values.categories, record.category);
    addCatalogValue(values.sourceTypes, record.sourceType);
    addCatalogValue(values.sourceChannels, record.sourceChannel);
    addCatalogValue(values.postTypes, record.postType);
  });

  fillSelect(els.categoryFilter, values.categories, "全部分类", state.filters.category);
  fillSelect(els.sourceTypeFilter, values.sourceTypes, "全部来源类型", state.filters.sourceType);
  fillSelect(els.sourceChannelFilter, values.sourceChannels, "全部来源渠道", state.filters.sourceChannel);
  fillSelect(els.postTypeFilter, values.postTypes, "全部贴文类型", state.filters.postType);
}

function fillSelect(select, values, firstLabel, selectedValue) {
  const list = values instanceof Map ? Array.from(values.values()) : Array.from(values);
  const sorted = list.filter(Boolean).sort((a, b) => String(a).localeCompare(String(b), "zh-Hans-CN"));
  const current = select.value || selectedValue || "";
  select.textContent = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = firstLabel;
  select.append(all);
  sorted.forEach(value => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  });
  select.value = sorted.includes(current) ? current : "";
}

function addCatalogValue(map, value) {
  const display = normalizeCatalogValue(value);
  if (!display || isCatalogHeaderValue(display)) return;
  const key = display.toLocaleLowerCase("zh-Hans-CN").replace(/\s+/g, "");
  if (!map.has(key)) map.set(key, display);
}

function pickDesignerField(input) {
  if (!input || typeof input !== "object") return "";
  const fromField = stringOr(
    input.designer ||
      input.artist ||
      input.美工 ||
      input.美工名字 ||
      input.ab ||
      input.AB ||
      input.columnAB ||
      input.colAB
  );
  if (fromField) return fromField;
  // Excel AB 列 = 0-based index 27
  if (Array.isArray(input.raw) && input.raw.length > 27) return stringOr(input.raw[27]);
  if (Array.isArray(input.row) && input.row.length > 27) return stringOr(input.row[27]);
  return "";
}

function pickLikesField(input) {
  if (!input || typeof input !== "object") return input && input.likes;
  if (input.likes != null && input.likes !== "") return input.likes;
  // Excel J 列 = 0-based index 9
  return (
    input.j ??
    input.J ??
    input.columnJ ??
    input.colJ ??
    input["点赞"] ??
    (Array.isArray(input.raw) ? input.raw[9] : undefined) ??
    (Array.isArray(input.row) ? input.row[9] : undefined)
  );
}

/** 名字 + AB 列美工；表内「未找到」不当真名 */
function formatDisplayName(record) {
  // 标题：真实「名字」；美工单独拼接（美工也是「未找到」则不拼）
  const name = getRecordDisplayName_(record);
  let designer = stringOr(record && record.designer);
  if (isPlaceholderLabel_(designer)) designer = "";
  if (!designer || designer === name) return name;
  // 名字本身是未找到时，只显示美工（若有），避免「未找到 · 某某」
  if (isPlaceholderLabel_(name)) return designer;
  return `${name} · ${designer}`;
}

function normalizeRecord(input) {
  const rowNumber = input.rowNumber || "";
  const record = {
    id: stringOr(input.id || (rowNumber ? `row-${rowNumber}` : input.postId || input.postLink || `row-${Math.random().toString(36).slice(2)}`)),
    rowNumber,
    name: stringOr(input.name),
    postId: stringOr(input.postId),
    postLink: stringOr(input.postLink),
    lead: input.lead,
    thumbnailUrl: stringOr(input.thumbnailUrl || input.thumb || input.image),
    thumbnailFallbackUrl: stringOr(input.thumbnailFallbackUrl || input.fallbackThumbnailUrl || input.backupThumbnailUrl),
    content: stringOr(input.content),
    rewrite: stringOr(input.rewrite),
    date: stringOr(input.date),
    category: normalizeCatalogValue(input.category || input.type),
    likes: pickLikesField(input),
    designer: pickDesignerField(input),
    comments: input.comments,
    shares: input.shares,
    water: input.water ?? input.waterDrops ?? input.droplets ?? input["水滴"] ?? input.z ?? input.Z ?? input.columnZ ?? input.colZ,
    waterTime: stringOr(input.waterTime || input.waterDate || input["水滴时间"] || input.aa || input.AA || input.columnAA || input.colAA),
    postType: normalizeCatalogValue(input.postType),
    ocr: stringOr(input.ocr),
    ocrTranslation: stringOr(input.ocrTranslation),
    audioText: stringOr(input.audioText),
    audioTranslation: stringOr(input.audioTranslation),
    authorName: stringOr(input.authorName),
    sourceType: normalizeCatalogValue(input.sourceType),
    sourceChannel: normalizeCatalogValue(input.sourceChannel),
    conversionRate: input.conversionRate,
    mediaMode: stringOr(input.mediaMode),
    pageName: stringOr(input.pageName),
    avatarUrl: stringOr(input.avatarUrl),
    cacheRunId: stringOr(input.cacheRunId)
  };

  const isVideo = getMediaMode(record) === "video";
  record.displayText = isVideo ? record.audioText : record.ocr;
  record.displayTranslation = isVideo ? record.audioTranslation : record.ocrTranslation;
  return record;
}

function isHeaderRecord(record) {
  return record.postId === "帖文ID"
    || record.postLink === "贴文链接"
    || record.likes === "点赞"
    || record.designer === "美工"
    || record.designer === "美工名字"
    || record.authorName === "作者id";
}

function normalizeCatalogValue(value) {
  return stringOr(value).replace(/[\u200b-\u200d\ufeff]/g, "").replace(/\s+/g, " ").trim();
}

function isCatalogHeaderValue(value) {
  return ["分类", "图片类型", "来源类型", "来源渠道", "贴文类型"].includes(normalizeCatalogValue(value));
}

function hasUsablePreview(record) {
  return Boolean(toPreviewUrl(record.thumbnailUrl) || toPreviewUrl(record.thumbnailFallbackUrl));
}

function getMediaMode(record) {
  if (record.mediaMode === "video" || record.mediaMode === "image") return record.mediaMode;
  const probe = `${record.postType} ${record.category} ${record.sourceType}`.toLowerCase();
  return /短视频|视频|video|reel|tiktok|douyin|快手/.test(probe) ? "video" : "image";
}

function driveFileId(url) {
  const text = stringOr(url);
  if (!text) return "";
  const fileMatch = text.match(/(?:drive|docs)\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) return fileMatch[1];
  const lh3 = text.match(/lh3\.googleusercontent\.com\/d\/([a-zA-Z0-9_-]+)/i);
  if (lh3) return lh3[1];
  if (/(?:drive|docs|usercontent)\.google\.com/i.test(text)) {
    const idMatch = text.match(/[?&]id=([^&#]+)/i);
    if (idMatch) return decodeURIComponent(idMatch[1]);
  }
  return "";
}

function previewCandidates(url) {
  let value = extractUrl(stringOr(url));
  if (!value) return [];
  if (value.startsWith("//")) value = `https:${value}`;
  const id = driveFileId(value);
  if (id) {
    return [
      `https://lh3.googleusercontent.com/d/${id}`,
      `https://drive.google.com/thumbnail?id=${encodeURIComponent(id)}&sz=w800`,
      `/api/drive-image?id=${encodeURIComponent(id)}`
    ];
  }
  return [value];
}

function uniquePreviewCandidates(...urls) {
  const seen = new Set();
  const out = [];
  urls.forEach((url) => {
    previewCandidates(url).forEach((item) => {
      if (item && !seen.has(item)) {
        seen.add(item);
        out.push(item);
      }
    });
  });
  return out;
}

function toPreviewUrl(url) {
  const list = previewCandidates(url);
  return list[0] || "";
}

function extractUrl(value) {
  const text = stringOr(value);
  if (!text) return "";
  const formulaMatch = text.match(/=\s*(?:IMAGE|HYPERLINK)\s*\(\s*"((?:[^"]|"")+)"/i);
  if (formulaMatch) return formulaMatch[1].replace(/""/g, '"').trim();
  const urlMatch = text.match(/https?:\/\/[^\s"'<>)]+/i);
  if (urlMatch) return urlMatch[0];
  return /^(data:image\/|blob:|\/\/)/i.test(text) ? text : "";
}

async function api(action, payload = {}) {
  const response = await fetch(API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ action, ...payload })
  });

  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showAuth("请先输入访问密码。");
    throw new Error(data.error || "需要访问密码。");
  }
  if (!response.ok || data.ok === false) {
    const staticPreviewError = response.status === 404 || response.status === 405 || response.status === 501;
    const message = staticPreviewError
      ? "当前是静态预览，未连接 Vercel API；部署或运行 vercel dev 后即可连接 Sheet。"
      : data.error || response.statusText || "API 请求失败";
    throw new Error(message);
  }
  return data;
}

async function authApi(payload = {}) {
  const response = await fetch(AUTH_URL, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || response.statusText || "密码验证失败。");
  }
  return data;
}

function setStatus(message, isError = false) {
  els.statusText.textContent = message;
  els.statusText.style.color = isError ? "var(--rose)" : "";
}

function stringOr(value) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function toNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const text = stringOr(value).replace(/,/g, "");
  if (!text) return 0;
  const number = Number.parseFloat(text.replace("%", ""));
  if (!Number.isFinite(number)) return 0;
  return number;
}

function dateNumber(value) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function formatDate(value) {
  if (!value) return "";
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return String(value);
  return time.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatDateTime(value) {
  if (!value) return "未同步";
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return String(value);
  return time.toLocaleString("zh-CN", { hour12: false });
}

function formatNumber(value) {
  const number = toNumber(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN") : "0";
}

function formatCompactNumber(value) {
  const number = toNumber(value);
  if (!Number.isFinite(number)) return "0";
  const abs = Math.abs(number);
  if (abs >= 10000) return `${trimNumber(number / 10000, abs >= 100000 ? 0 : 1)}万`;
  if (abs >= 1000) return `${trimNumber(number / 1000, 1)}千`;
  return String(number);
}

function trimNumber(value, decimals) {
  return value.toFixed(decimals).replace(/\.0$/, "");
}

function formatWaterDisplay(record) {
  const value = formatCompactNumber(record.water);
  const time = formatDateOnly(record.waterTime) || "无日期";
  return `📅${time} 💧 ${value}`;
}

function formatWaterFullValue(record) {
  const value = formatNumber(record.water);
  const time = formatDateOnly(record.waterTime) || "无日期";
  return `📅${time} 💧 ${value}`;
}

function formatDateOnly(value) {
  if (!value) return "";
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    const text = stringOr(value);
    const match = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
    if (!match) return text;
    return [
      match[1],
      String(Number(match[2])).padStart(2, "0"),
      String(Number(match[3])).padStart(2, "0")
    ].join("-");
  }
  return [
    time.getFullYear(),
    String(time.getMonth() + 1).padStart(2, "0"),
    String(time.getDate()).padStart(2, "0")
  ].join("-");
}

function formatDateTimeShort(value) {
  const text = stringOr(value);
  if (!text) return "";
  const time = new Date(text);
  if (Number.isNaN(time.getTime())) return text;
  return time.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function formatPercentLike(value) {
  if (value === "" || value === null || value === undefined) return "0";
  const raw = stringOr(value);
  if (raw.includes("%")) return raw;
  const number = toNumber(value);
  if (!Number.isFinite(number)) return "0";
  return number > 0 && number < 1 ? `${(number * 100).toFixed(2)}%` : String(number);
}

function conversionNumber(value) {
  const raw = stringOr(value);
  const number = toNumber(value);
  if (!Number.isFinite(number)) return 0;
  if (raw.includes("%")) return number;
  return number > 0 && number < 1 ? number * 100 : number;
}

function pause(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function openDb() {
  if (!("indexedDB" in window)) return Promise.reject(new Error("IndexedDB unavailable"));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("records")) {
        db.createObjectStore("records", { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains("meta")) {
        db.createObjectStore("meta", { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function idbGetAll(storeName) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(storeName, "readonly");
    const request = tx.objectStore(storeName).getAll();
    request.onsuccess = () => resolve(request.result || []);
    request.onerror = () => reject(request.error);
  });
}

function idbGet(storeName, key) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(storeName, "readonly");
    const request = tx.objectStore(storeName).get(key);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
}

function saveRecords(records) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction("records", "readwrite");
    const store = tx.objectStore("records");
    records.forEach(record => store.put(record));
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

function clearStore(storeName) {
  if (!state.db) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).clear();
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

function saveSummary(summary) {
  if (!state.db) return Promise.resolve();
  els.lastUpdated.textContent = formatDateTime(summary.lastUpdated);
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction("meta", "readwrite");
    tx.objectStore("meta").put({ key: "summary", ...summary });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

function removeRecordsOutsideRun(runId) {
  if (!state.db || !runId) return Promise.resolve();
  const stale = Array.from(state.records.values()).filter(record => record.cacheRunId && record.cacheRunId !== runId);
  stale.forEach(record => state.records.delete(record.id));
  if (!stale.length) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const tx = state.db.transaction("records", "readwrite");
    const store = tx.objectStore("records");
    stale.forEach(record => store.delete(record.id));
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}
