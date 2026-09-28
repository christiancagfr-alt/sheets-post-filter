"use strict";

const API_URL = "/api/gallery";
const AUTH_URL = "/api/auth";
const DRIVE_CACHE_URL = "/api/drive-cache";
const CACHE_PROXY_URL = "/api/cache-proxy";
const DB_VERSION = 1;
const PAGE_SIZE = 1000;
const INITIAL_VISIBLE_ROWS = 100;
const LOAD_MORE_ROWS = 100;
const SHARED_DB_NAME = "q-gallery-cache-v1";
// 与首页共用：优先 R2 CDN Manifest（浏览器直读）
function getManifestUrl() {
  return (
    window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL ||
    window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL ||
    "https://gallery-cache.zhixianglife.com/promo/manifest.json"
  );
}
const DIRECT_CACHE_URL =
  window.Q_GALLERY_DRIVE_CACHE_URL ||
  window.Q_GALLERY_PUBLIC_CACHE_URL ||
  "";
const RANK_METRICS = {
  lead: { key: "lead", dateKey: "date", label: "引流" },
  water: { key: "water", dateKey: "waterTime", label: "水滴" },
  // J 列（点赞）排行榜
  likes: { key: "likes", dateKey: "date", label: "点赞" }
};
const rankMetric = RANK_METRICS[document.body.dataset.rankMetric] || RANK_METRICS.lead;
const DB_NAME = SHARED_DB_NAME;

const els = {
  authGate: document.getElementById("authGate"),
  appShell: document.getElementById("appShell"),
  passwordForm: document.getElementById("passwordForm"),
  passwordInput: document.getElementById("passwordInput"),
  passwordError: document.getElementById("passwordError"),
  syncBtn: document.getElementById("syncBtn"),
  reloadBtn: document.getElementById("reloadBtn"),
  statusText: document.getElementById("statusText"),
  rangeText: document.getElementById("rangeText"),
  rankCount: document.getElementById("rankCount"),
  recordCount: document.getElementById("recordCount"),
  summaryText: document.getElementById("summaryText"),
  body: document.getElementById("leaderboardBody"),
  emptyState: document.getElementById("emptyState"),
  loadMoreBtn: document.getElementById("loadMoreBtn"),
  tabs: Array.from(document.querySelectorAll(".range-tab"))
};

const state = {
  db: null,
  records: new Map(),
  ranked: [],
  range: "7",
  visibleRows: INITIAL_VISIBLE_ROWS,
  syncRunning: false,
  appStarted: false
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindAuthEvents();
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
}

function bindEvents() {
  els.tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      state.range = tab.dataset.range || "7";
      state.visibleRows = INITIAL_VISIBLE_ROWS;
      syncTabs();
      render();
    });
  });

  els.syncBtn.addEventListener("click", () => {
    void syncSharedCache({ reset: true });
  });

  els.reloadBtn.addEventListener("click", () => {
    void syncSharedCache({ reset: true });
  });

  els.loadMoreBtn.addEventListener("click", () => {
    state.visibleRows += LOAD_MORE_ROWS;
    renderRows();
  });
}

function showAuth(message = "") {
  els.appShell.hidden = true;
  els.authGate.hidden = false;
  els.passwordError.textContent = message;
  window.setTimeout(() => els.passwordInput.focus(), 0);
}

async function loadLocalCache() {
  if (!state.db) {
    setStatus("浏览器缓存不可用，正在读取云端 CDN…");
    window.setTimeout(() => void syncSharedCache({ reset: true }), 0);
    return;
  }

  const records = await idbGetAll("records");
  records.forEach(row => {
    const record = normalizeRecord(row);
    if (!isHeaderRecord(record)) state.records.set(record.id, record);
  });

  // 首页同步过的数据直接用于排行榜，不再因缺头像强行重拉云端
  if (state.records.size > 0) {
    setStatus(`已使用首页本地缓存 ${state.records.size} 条，可直接看排行。需要更新时点「加载数据」。`);
    return;
  }

  setStatus("本地暂无缓存，正在从 CDN 加载…");
  window.setTimeout(() => void syncSharedCache({ reset: true }), 0);
}

async function syncSharedCache({ reset = false } = {}) {
  if (state.syncRunning) return;
  state.syncRunning = true;
  if (els.syncBtn) els.syncBtn.disabled = true;
  if (els.reloadBtn) els.reloadBtn.disabled = true;

  if (reset) {
    state.visibleRows = INITIAL_VISIBLE_ROWS;
    setStatus("正在从 CDN / 云端加载…");
  }

  try {
    const loaded = await syncSharedCacheSnapshot();
    if (!loaded) {
      // 关键：首页已有本地数据时，云端失败也不清榜、不报「整站不可用」
      if (state.records.size > 0) {
        setStatus(`云端更新失败，继续显示本地 ${state.records.size} 条（首页已同步的数据可用）。`);
      } else {
        throw new Error("云端缓存暂时不可用。请先到首页点「同步」加载数据，再回排行榜。");
      }
    } else {
      setStatus(`加载完成，共 ${state.records.size} 条数据。`);
    }
  } catch (error) {
    if (state.records.size > 0) {
      setStatus(`云端失败，仍显示本地 ${state.records.size} 条：${error.message || error}`);
    } else {
      setStatus(error.message || "排行榜加载失败。", true);
    }
  } finally {
    state.syncRunning = false;
    if (els.syncBtn) els.syncBtn.disabled = false;
    if (els.reloadBtn) els.reloadBtn.disabled = false;
    render();
  }
}

async function syncSharedCacheSnapshot() {
  // 1) CDN / Drive Manifest 分片（与首页一致，浏览器直读 CDN）
  const candidates = [
    getManifestUrl(),
    window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL || "",
    "https://gallery-cache.zhixianglife.com/promo/manifest.json",
    "https://drive.google.com/uc?export=download&id=1zj9ZJHGO_q6iuWdHiBgCghE0LA--snxi"
  ].filter(Boolean);

  for (const manifestUrl of [...new Set(candidates)]) {
    try {
      const data = await fetchManifestAllChunks(manifestUrl);
      if (data && (await applySnapshotData(data))) return true;
    } catch (error) {
      console.warn("manifest load failed", manifestUrl, error);
    }
  }

  // 2) 整包 DIRECT_CACHE_URL
  if (DIRECT_CACHE_URL) {
    try {
      const data = await fetchJsonUrl(DIRECT_CACHE_URL);
      if (data && (await applySnapshotData(data))) return true;
    } catch (error) {
      console.warn("direct cache failed", error);
    }
  }

  return false;
}

async function fetchManifestAllChunks(manifestUrl) {
  const manifest = await fetchJsonUrl(manifestUrl);
  const chunks = Array.isArray(manifest.chunks) ? manifest.chunks : [];
  if (!chunks.length) {
    // 可能是整包 JSON
    if (Array.isArray(manifest.rows) || Array.isArray(manifest.assets)) return manifest;
    throw new Error("Manifest 无分片");
  }

  const sorted = chunks.slice().sort((a, b) => Number(a.index || 0) - Number(b.index || 0));
  const allRows = [];
  for (let i = 0; i < sorted.length; i += 1) {
    setStatus(`加载分片 ${i + 1}/${sorted.length}…`);
    const chunk = sorted[i];
    const url =
      chunk.url ||
      (chunk.fileId
        ? `https://drive.google.com/uc?export=download&id=${encodeURIComponent(chunk.fileId)}`
        : "");
    if (!url) continue;
    const part = await fetchJsonUrl(url);
    const rows = Array.isArray(part.rows)
      ? part.rows
      : Array.isArray(part.assets)
        ? part.assets
        : [];
    allRows.push(...rows);
  }

  return {
    ok: true,
    source: manifest.source || "manifest",
    runId: manifest.buildId ? `drive-${manifest.buildId}` : `drive-${Date.now()}`,
    updatedAt: manifest.updatedAt || new Date().toISOString(),
    totalRows: Number(manifest.totalRows || allRows.length),
    rows: allRows,
    assets: allRows
  };
}

async function fetchJsonUrl(url) {
  const text = String(url || "");
  const isCdn =
    /zhixianglife\.com|r2\.dev|cloudflarestorage\.com|pages\.dev/i.test(text) || text.startsWith("/");
  try {
    const response = await fetch(text, {
      cache: isCdn ? "default" : "no-store",
      mode: "cors",
      credentials: "omit"
    });
    if (!response.ok) throw new Error(`下载失败 ${response.status}`);
    return response.json();
  } catch (error) {
    // Drive 无 CORS → 同域代理
    if (isCdn) throw error;
    const response = await fetch(CACHE_PROXY_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: text })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `代理下载失败 ${response.status}`);
    }
    return data;
  }
}

async function applySnapshotData(data) {
  const runId = data.runId || data.buildId || "";
  const raw = Array.isArray(data.rows)
    ? data.rows
    : Array.isArray(data.assets)
      ? data.assets
      : [];
  const rows = raw.map(row => normalizeSnapshotRecord(row, runId)).filter(record => !isHeaderRecord(record));

  if (!rows.length && toNumber(data.totalRows) > 0) return false;
  if (!rows.length) return false;

  rows.forEach(record => state.records.set(record.id, record));
  if (rows.length && state.db) await saveRecords(rows);
  render();

  const sourceLabel = /r2|promo|cdn|gallery-cache/i.test(String(data.source || ""))
    ? "CDN 缓存"
    : "云端缓存";
  setStatus(`${sourceLabel}已加载，共 ${state.records.size} 条。`);
  return true;
}

function normalizeSnapshotRecord(row, runId) {
  const assetId = row.catalogId && row.rowNumber
    ? `${row.catalogId}-${row.rowNumber}-${row.id || row.fileId || ""}`
    : row.id;
  return normalizeRecord({
    ...row,
    id: assetId || row.id || row.postId || row.postLink,
    name: row.name || row.title,
    postLink: row.postLink || row.viewUrl || row.sourceUrl,
    category: row.category || row.type,
    postType: row.postType || row.type,
    authorName: row.authorName || row.maker,
    sourceType: row.sourceType || row.group || row.catalogId,
    sourceChannel: row.sourceChannel || row.pageName || row.catalogId || "",
    likes: row.likes ?? row.j ?? row.J,
    designer: row.designer || row.美工 || row.ab || "",
    nameImage: row.nameImage || row.wImage || "",
    thumbnailUrl: row.thumbnailUrl || row.previewUrl || row.image || "",
    previewUrl: row.previewUrl || row.thumbnailUrl || "",
    thumbnailFallbackUrl: row.thumbnailFallbackUrl || row.nameImage || "",
    lead: row.lead,
    water: row.water,
    waterTime: row.waterTime || row.waterDate,
    date: row.date,
    authorName: row.authorName || row.maker || "",
    maker: row.maker || row.authorName || "",
    avatarUrl: row.avatarUrl || "",
    cacheRunId: runId
  });
}

function render() {
  syncTabs();
  const records = Array.from(state.records.values());
  let range = getRangeWindow(state.range, records);
  state.ranked = buildRankedRecords(records, range);
  if (!state.ranked.length) {
    const fallbackRange = getFallbackRangeWindow(state.range, records);
    if (fallbackRange) {
      const fallbackRanked = buildRankedRecords(records, fallbackRange);
      if (fallbackRanked.length) {
        range = fallbackRange;
        state.ranked = fallbackRanked;
      }
    }
  }

  els.recordCount.textContent = String(records.length);
  els.rankCount.textContent = String(state.ranked.length);
  els.rangeText.textContent = range.label;
  els.summaryText.textContent = `${range.label}按${rankMetric.label}数从高到低排序。`;
  renderRows();
}

function buildRankedRecords(records, range) {
  return records
    .filter(record => toNumber(record[rankMetric.key]) > 0)
    .filter(record => isInsideRange(record[rankMetric.dateKey], range.start, range.end))
    .sort((a, b) => {
      const metricDiff = toNumber(b[rankMetric.key]) - toNumber(a[rankMetric.key]);
      if (metricDiff) return metricDiff;
      return dateNumber(b[rankMetric.dateKey]) - dateNumber(a[rankMetric.dateKey]);
    });
}

function renderRows() {
  const rows = state.ranked.slice(0, state.visibleRows);
  const fragment = document.createDocumentFragment();
  els.body.textContent = "";

  rows.forEach((record, index) => {
    const tr = document.createElement("tr");
    tr.append(
      rankCell(index + 1),
      avatarCell(record),
      metricCell(record),
      nameTextCell(record),
      nameImageCell(record),
      sourceChannelCell(record),
      tableCell(record.sourceType, "source-type-cell"),
      tableCell(getCopyText(record), "copy-cell"),
      tableCell(formatMetricDate(record[rankMetric.dateKey]), "date-cell"),
      linkCell(record.postLink)
    );
    fragment.append(tr);
  });

  els.body.append(fragment);
  els.emptyState.hidden = state.ranked.length > 0;
  els.loadMoreBtn.hidden = state.visibleRows >= state.ranked.length;
  els.loadMoreBtn.textContent = `加载更多（${Math.min(state.visibleRows, state.ranked.length)} / ${state.ranked.length}）`;
}

function rankCell(rank) {
  const td = document.createElement("td");
  td.className = "rank-cell";
  const badge = document.createElement("span");
  badge.className = "rank-badge";

  if (rank === 1) {
    badge.classList.add("gold");
    badge.textContent = "🥇";
    badge.title = "第 1 名";
  } else if (rank === 2) {
    badge.classList.add("silver");
    badge.textContent = "🥈";
    badge.title = "第 2 名";
  } else if (rank === 3) {
    badge.classList.add("bronze");
    badge.textContent = "🥉";
    badge.title = "第 3 名";
  } else if (rank === 4) {
    badge.classList.add("iron");
    badge.textContent = "4";
    badge.title = "第 4 名";
  } else {
    if (rank >= 1000) badge.classList.add("is-compact");
    badge.textContent = rank >= 1000 ? formatCompactNumber(rank) : String(rank);
    badge.title = `第 ${rank} 名`;
  }

  td.append(badge);
  return td;
}

function avatarCell(record) {
  const td = document.createElement("td");
  td.className = "avatar-cell";
  const candidates = resolveAvatarUrls(record);

  if (!candidates.length) {
    td.append(avatarFallback(record));
    return td;
  }

  const img = document.createElement("img");
  img.alt = record.sourceChannel || record.name || "头像";
  img.loading = "lazy";
  // Facebook 图床对 no-referrer 更友好；gyazo 等同理
  img.referrerPolicy = "no-referrer";
  let tryIndex = 0;
  const tryNext = () => {
    tryIndex += 1;
    if (tryIndex < candidates.length) {
      img.src = candidates[tryIndex];
      return;
    }
    img.remove();
    td.append(avatarFallback(record));
  };
  img.src = candidates[0];
  img.addEventListener("error", tryNext);
  // 50×50 小 GIF 剪影也算失败，换下一个候选（专页常用 Page ID）
  img.addEventListener("load", () => {
    if (img.naturalWidth > 0 && img.naturalWidth <= 50 && img.naturalHeight <= 50) {
      tryNext();
    }
  });
  td.append(img);
  return td;
}

/**
 * 头像地址解析：
 * 1) avatarUrl / T 列
 * 2) 按来源类型优先：
 *    - 专页：帖文链接里的 Page ID（pageId_postId）→ 作者 ID
 *    - 小组：作者 ID → 链接里的 Group ID
 * 3) 其它候选
 */
function resolveAvatarUrls(record) {
  const list = [];
  const push = (u) => {
    let url = rewriteAvatarThroughProxy(toPreviewUrlLikeHome(u) || stringOr(u));
    if (!url) return;
    // 允许 https 外链，或同域相对路径（头像代理）
    if (!/^(https?:\/\/|\/)/i.test(url)) return;
    if (!list.includes(url)) list.push(url);
  };
  const pushId = (id) => {
    const clean = String(id || "").replace(/[^\d]/g, "");
    if (/^\d{8,}$/.test(clean)) {
      push(`/api/avatar?id=${encodeURIComponent(clean)}&w=120`);
    }
  };

  push(record.avatarUrl);
  push(Array.isArray(record.raw) ? record.raw[19] : "");

  const authorId = extractFacebookId(record.authorName || record.maker || record.authorId);
  const link = record.postLink || record.sourceUrl || "";
  const pageId = extractFacebookPageId(link);
  const groupId = extractFacebookGroupId(link);
  const sourceType = stringOr(record.sourceType || record.group);
  const isGroup = /小组|group/i.test(sourceType) || Boolean(groupId && !pageId);
  const isPage = /专页|page/i.test(sourceType) || Boolean(pageId && !groupId);

  if (isPage) {
    // 专页：作者个人 ID 常无权限，Page ID 才能出真头像
    pushId(pageId);
    pushId(authorId);
  } else if (isGroup) {
    // 小组：先发帖作者；再试小组 ID（部分 token 可出图）
    pushId(authorId);
    pushId(groupId);
  } else {
    pushId(authorId);
    pushId(pageId);
    pushId(groupId);
  }

  push(Array.isArray(record.raw) ? record.raw[17] : "");
  return list;
}

/** 来源渠道单元格：名字前加专页/小组头像 */
function sourceChannelCell(record) {
  const td = document.createElement("td");
  td.className = "title-cell source-channel-cell";
  const text = stringOr(record.sourceChannel) || "-";
  td.title = text;

  const wrap = document.createElement("div");
  wrap.className = "channel-with-avatar";

  const link = record.postLink || record.sourceUrl || "";
  const pageId = extractFacebookPageId(link);
  const groupId = extractFacebookGroupId(link);
  const channelId = pageId || groupId;
  const label = document.createElement("span");
  label.className = "channel-label";
  label.textContent = text;

  if (channelId) {
    const img = document.createElement("img");
    img.className = "channel-avatar";
    img.alt = "";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.src = `/api/avatar?id=${encodeURIComponent(channelId)}&w=64`;
    img.addEventListener("error", () => {
      img.remove();
      const fb = document.createElement("span");
      fb.className = "channel-avatar-fallback";
      fb.textContent = text.slice(0, 1) || (groupId ? "组" : "页");
      wrap.insertBefore(fb, label);
    });
    img.addEventListener("load", () => {
      if (img.naturalWidth > 0 && img.naturalWidth <= 50) {
        img.dispatchEvent(new Event("error"));
      }
    });
    wrap.append(img, label);
  } else {
    wrap.append(label);
  }

  td.append(wrap);
  return td;
}

/** Facebook Graph / fbcdn 直链 → 同域代理（国内可加载） */
function rewriteAvatarThroughProxy(url) {
  const text = stringOr(url);
  if (!text) return "";

  // 已是本站代理
  if (/^\/api\/avatar(\?|$)/i.test(text)) return text;

  // 含 access_token 的 Graph URL：整段交给代理（勿只抽 id，否则丢 token → 灰色剪影）
  if (/graph\.facebook\.com/i.test(text) && /access_token=/i.test(text) && /^https?:\/\//i.test(text)) {
    return `/api/avatar?url=${encodeURIComponent(text)}`;
  }

  const idFromGraph = text.match(/graph\.facebook\.com\/(?:v\d+(?:\.\d+)?\/)?(\d{8,})\/picture/i);
  if (idFromGraph) {
    // 无 token 时走 id=；服务端若已同步 FB token 会自动补上
    return `/api/avatar?id=${encodeURIComponent(idFromGraph[1])}&w=120`;
  }

  // 其它 fbcdn / scontent 直链：经 url= 代理
  try {
    if (/^https?:\/\//i.test(text)) {
      const host = new URL(text).hostname.toLowerCase();
      if (
        host === "graph.facebook.com" ||
        host.endsWith(".facebook.com") ||
        host.endsWith(".fbcdn.net") ||
        host.endsWith(".fbsbx.com")
      ) {
        return `/api/avatar?url=${encodeURIComponent(text)}`;
      }
    }
  } catch {
    // ignore
  }

  return text;
}

/** 纯数字 ID 或 profile?id=；禁止把 URL 里多个数字拼成一个假 ID */
function extractFacebookId(value) {
  const text = stringOr(value);
  if (!text) return "";
  const trimmed = text.trim();
  if (/^\d{8,}$/.test(trimmed)) return trimmed;
  const idParam = text.match(/[?&]id=(\d{8,})/i);
  if (idParam) return idParam[1];
  // 勿用 replace 去非数字：会把 groups/A/posts/B 拼成 AB
  return "";
}

/** 专页帖：https://fb.com/{pageId}_{postId} */
function extractFacebookPageId(value) {
  const text = stringOr(value);
  if (!text) return "";
  if (/\/groups\//i.test(text)) return "";
  const m =
    text.match(/(?:fb\.com|facebook\.com)\/(\d{8,})_\d+/i) ||
    text.match(/(?:fb\.com|facebook\.com)\/(\d{8,})(?:[/?#]|$)/i);
  return m ? m[1] : "";
}

/** 小组帖：https://fb.com/groups/{groupId}/posts/... */
function extractFacebookGroupId(value) {
  const text = stringOr(value);
  if (!text) return "";
  const m = text.match(/\/groups\/(\d{8,})/i);
  return m ? m[1] : "";
}

function avatarFallback(record) {
  const fallback = document.createElement("span");
  fallback.className = "avatar-fallback";
  fallback.textContent = stringOr(record.sourceChannel || record.name).slice(0, 1) || "-";
  return fallback;
}

function tableCell(text, className = "") {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text || "-";
  td.title = text || "";
  return td;
}

/** 名字 · 美工（文本） */
function nameTextCell(record) {
  const td = document.createElement("td");
  td.className = "name-cell";
  const label = formatRankDisplayName(record);
  td.textContent = label;
  td.title = label;
  return td;
}

/**
 * W 列预览：表格内小图；仅悬停放大到接近原图尺寸（无需点击）。
 * 图片逻辑对齐首页：gyazo 等 https 直链。
 */
function nameImageCell(record) {
  const td = document.createElement("td");
  td.className = "name-image-cell";

  const box = document.createElement("div");
  box.className = "name-image-box";

  const imageUrl = resolveNameImageUrl(record);
  if (imageUrl) {
    box.title = "悬停在右侧查看大图";
    const img = document.createElement("img");
    img.className = "name-image-img";
    img.src = imageUrl;
    img.alt = "";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.addEventListener(
      "error",
      () => {
        const fallback = resolveNameImageUrl(record, { skipPrimary: true });
        if (fallback && fallback !== imageUrl) {
          img.src = fallback;
          return;
        }
        img.remove();
        box.classList.add("is-empty");
        box.textContent = "失效";
        box.title = "";
      },
      { once: true }
    );
    // 靠近右边缘时，大图改到左侧，避免出屏
    box.addEventListener("mouseenter", () => {
      const rect = td.getBoundingClientRect();
      const spaceRight = window.innerWidth - rect.right;
      td.classList.toggle("is-near-right", spaceRight < 360);
    });
    box.append(img);
  } else {
    box.classList.add("is-empty");
    box.textContent = "无图";
    box.title = "暂无预览图";
  }

  td.append(box);
  return td;
}

/**
 * 与首页 toPreviewUrl / extractUrl 同一套规则：
 * 优先 nameImage(W) → thumbnailUrl(E，常见 gyazo) → fallback → raw
 */
function resolveNameImageUrl(record, options = {}) {
  const skipPrimary = Boolean(options.skipPrimary);
  const candidates = skipPrimary
    ? [
        record.thumbnailFallbackUrl,
        Array.isArray(record.raw) ? record.raw[4] : "",
        Array.isArray(record.raw) ? record.raw[22] : ""
      ]
    : [
        record.nameImage,
        record.thumbnailUrl,
        record.previewUrl,
        record.image,
        record.thumbnailFallbackUrl,
        record.wImage,
        Array.isArray(record.raw) ? record.raw[4] : "", // E 列：常见 IMAGE 源链接 / gyazo
        Array.isArray(record.raw) ? record.raw[22] : "" // W 列
      ];

  for (let i = 0; i < candidates.length; i += 1) {
    const url = toPreviewUrlLikeHome(candidates[i]);
    if (url) return url;
  }
  return "";
}

/** 对齐首页 app.js 的 toPreviewUrl + extractUrl */
function toPreviewUrlLikeHome(url) {
  let value = extractUrlLikeHome(stringOr(url));
  if (!value) return "";
  if (value.startsWith("//")) value = `https:${value}`;

  const fileMatch = value.match(/(?:drive|docs)\.google\.com\/file\/d\/([^/?#]+)/i);
  const lh3 = value.match(/lh3\.googleusercontent\.com\/d\/([a-zA-Z0-9_-]+)/i);
  let id = fileMatch ? fileMatch[1] : lh3 ? lh3[1] : "";
  if (!id && /(?:drive|docs|usercontent)\.google\.com/i.test(value)) {
    const idMatch = value.match(/[?&]id=([^&#]+)/i);
    if (idMatch) id = decodeURIComponent(idMatch[1]);
  }
  if (id) return `https://lh3.googleusercontent.com/d/${id}`;

  // gyazo / 其它 https 图片：原样使用
  return value;
}

function extractUrlLikeHome(value) {
  const text = stringOr(value);
  if (!text || /^(#VALUE!|#N\/A|#REF!|未找到|-|—)$/i.test(text)) return "";

  // =IMAGE("https://i.gyazo.com/xxx.jpg") 或带第二参数
  let m = text.match(/=\s*IMAGE\s*\(\s*"((?:[^"]|"")*)"\s*[,;)]/i);
  if (m) return m[1].replace(/""/g, '"').trim();
  m = text.match(/=\s*IMAGE\s*\(\s*'((?:[^']|'')*)'\s*[,;)]/i);
  if (m) return m[1].replace(/''/g, "'").trim();
  m = text.match(/=\s*(?:IMAGE|HYPERLINK)\s*\(\s*"((?:[^"]|"")*)"/i);
  if (m) return m[1].replace(/""/g, '"').trim();

  const urlMatch = text.match(/https?:\/\/[^\s"'<>)]+/i);
  if (urlMatch) return urlMatch[0].replace(/[),.;]+$/, "");

  if (/^(data:image\/|blob:|\/\/)/i.test(text)) return text;
  return "";
}

/** 名字后面拼接 AB 列美工：如「帖文名 · 美工名」；VLOOKUP 失败的「未找到」不展示 */
function formatRankDisplayName(record) {
  const name = stringOr(record.name) || "未命名";
  const designer = normalizeDesignerLabel(record.designer);
  if (!designer || designer === name) return name;
  return `${name} · ${designer}`;
}

function normalizeDesignerLabel(value) {
  const text = stringOr(value);
  if (!text) return "";
  // 表内公式找不到美工时的占位，不当作真实名字展示
  if (/^(未找到|#N\/A|N\/A|null|undefined|-|—|无)$/i.test(text)) return "";
  return text;
}

function pickDesigner(input) {
  if (!input || typeof input !== "object") return "";
  const fromField = normalizeDesignerLabel(
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
  // 0-based 列索引：AB = 27
  if (Array.isArray(input.raw) && input.raw.length > 27) {
    return normalizeDesignerLabel(input.raw[27]);
  }
  if (Array.isArray(input.row) && input.row.length > 27) {
    return normalizeDesignerLabel(input.row[27]);
  }
  return "";
}

function pickLikes(input) {
  if (!input || typeof input !== "object") return input && input.likes;
  if (input.likes != null && input.likes !== "") return input.likes;
  // J 列别名
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

function metricCell(record) {
  const value = record[rankMetric.key];
  const td = tableCell(formatCompactNumber(value), "lead-cell");
  td.title = formatNumber(value);
  return td;
}

function linkCell(href) {
  const td = document.createElement("td");
  td.className = "link-cell";
  if (!href) {
    td.textContent = "-";
    return td;
  }

  const link = document.createElement("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = "打开贴文";
  td.append(link);
  return td;
}

function getCopyText(record) {
  return firstLine(isVideoRecord(record) ? record.audioTranslation : record.ocrTranslation);
}

function firstLine(value) {
  const text = stringOr(value).replace(/\s+/g, " ");
  return text.length > 88 ? `${text.slice(0, 88)}...` : text;
}

function syncTabs() {
  els.tabs.forEach(tab => {
    const active = tab.dataset.range === state.range;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
}

function getRangeWindow(range, records = []) {
  const today = startOfLocalDay(new Date());
  let start;
  let label;

  // 当天：[今天 00:00, 明天 00:00)
  if (range === "today") {
    return { label: "当天", start: today, end: addDays(today, 1) };
  }

  // 昨天：[昨天 00:00, 今天 00:00)
  if (range === "yesterday") {
    start = addDays(today, -1);
    return { label: "昨天", start, end: today };
  }

  if (range === "30") {
    start = addDays(today, -29);
    label = "最近 30 天";
  } else {
    start = addDays(today, -6);
    label = "最近 7 天";
  }

  return { label, start, end: addDays(today, 1) };
}

function getFallbackRangeWindow(range, records = []) {
  const metricRecords = records
    .filter(record => toNumber(record[rankMetric.key]) > 0)
    .map(record => parseRecordDate(record[rankMetric.dateKey]))
    .filter(Boolean)
    .sort((a, b) => b.getTime() - a.getTime());
  if (!metricRecords.length) return null;

  const latest = startOfLocalDay(metricRecords[0]);
  // 当天 / 昨天：若日历上无数据，回退到「数据里最新一天」
  if (range === "today" || range === "yesterday") {
    return {
      label: `最新一天（${formatDateOnly(latest)}）`,
      start: latest,
      end: addDays(latest, 1)
    };
  }

  const days = range === "30" ? 30 : 7;
  return {
    label: `数据最新 ${days} 天`,
    start: addDays(latest, -(days - 1)),
    end: addDays(latest, 1)
  };
}

function isInsideRange(value, start, end) {
  const date = parseRecordDate(value);
  return Boolean(date && date >= start && date < end);
}

function parseRecordDate(value) {
  const text = stringOr(value);
  if (!text) return null;

  const match = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (match) {
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }

  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : startOfLocalDay(parsed);
}

function startOfLocalDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function normalizeRecord(input) {
  const rowNumber = input.rowNumber || "";
  const postId = stringOr(input.postId);
  const postLink = stringOr(input.postLink);
  return {
    ...input,
    id: stringOr(rowNumber ? `row-${rowNumber}` : input.id || postId || postLink || `row-${Math.random().toString(36).slice(2)}`),
    rowNumber,
    name: stringOr(input.name),
    postId,
    postLink,
    lead: input.lead,
    water: input.water ?? input.waterDrops ?? input.droplets ?? input["水滴"] ?? input.z ?? input.Z ?? input.columnZ ?? input.colZ,
    waterTime: stringOr(input.waterTime || input.waterDate || input["水滴时间"] || input.aa || input.AA || input.columnAA || input.colAA),
    content: stringOr(input.content),
    ocrTranslation: stringOr(input.ocrTranslation),
    audioTranslation: stringOr(input.audioTranslation),
    date: stringOr(input.date),
    category: stringOr(input.category || input.type),
    postType: stringOr(input.postType),
    pageName: stringOr(input.pageName),
    sourceChannel: stringOr(input.sourceChannel),
    sourceType: stringOr(input.sourceType),
    avatarUrl: stringOr(input.avatarUrl),
    authorName: stringOr(input.authorName || input.maker || input.authorId || ""),
    maker: stringOr(input.maker || input.authorName || ""),
    mediaMode: stringOr(input.mediaMode),
    likes: pickLikes(input),
    designer: pickDesigner(input),
    nameImage: stringOr(input.nameImage || input.wImage || ""),
    thumbnailUrl: stringOr(input.thumbnailUrl || input.previewUrl || input.image || input.thumb || ""),
    previewUrl: stringOr(input.previewUrl || input.thumbnailUrl || ""),
    thumbnailFallbackUrl: stringOr(input.thumbnailFallbackUrl || input.nameImage || ""),
    raw: Array.isArray(input.raw) ? input.raw : input.raw,
    comments: input.comments,
    shares: input.shares
  };
}

function isVideoRecord(record) {
  if (record.mediaMode === "video") return true;
  const probe = `${record.postType} ${record.category}`.toLowerCase();
  return /短视频|视频|video|reel|tiktok|douyin|快手/.test(probe);
}

function isHeaderRecord(record) {
  return Number(record.rowNumber) <= 2
    || record.postId === "帖文ID"
    || record.postLink === "贴文链接"
    || record.lead === "引流"
    || record.water === "水滴"
    || record.likes === "点赞"
    || record.designer === "美工"
    || record.designer === "美工名字";
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
    throw new Error(data.error || response.statusText || "API 请求失败");
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
  return Number.isFinite(number) ? number : 0;
}

function dateNumber(value) {
  const date = parseRecordDate(value);
  return date ? date.getTime() : 0;
}

function formatDate(value) {
  const date = parseRecordDate(value);
  if (!date) return stringOr(value) || "-";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatMetricDate(value) {
  return rankMetric.key === "water" ? (formatDateOnly(value) || "-") : formatDate(value);
}

function formatDateOnly(value) {
  const date = parseRecordDate(value);
  if (!date) return stringOr(value) || "";
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0")
  ].join("-");
}

function formatDateTimeShort(value) {
  const text = stringOr(value);
  if (!text) return "";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function formatNumber(value) {
  return toNumber(value).toLocaleString("zh-CN");
}

function formatCompactNumber(value) {
  const number = toNumber(value);
  const sign = number < 0 ? "-" : "";
  const absolute = Math.abs(number);

  if (absolute < 1000) return `${sign}${Math.round(absolute).toLocaleString("zh-CN")}`;
  if (absolute < 1000000) return `${sign}${formatKNumber(absolute)}k`;

  const millions = Math.ceil((absolute / 1000000) * 10) / 10;
  return `${sign}${trimDecimal(millions.toFixed(1), 1)}m`;
}

function formatKNumber(absolute) {
  if (absolute < 10000) {
    const thousands = Math.ceil((absolute / 1000) * 100) / 100;
    return trimDecimal(thousands.toFixed(2), 1);
  }

  if (absolute < 100000) {
    const thousands = Math.ceil((absolute / 1000) * 10) / 10;
    return trimDecimal(thousands.toFixed(1), 0);
  }

  return String(Math.ceil(absolute / 1000));
}

function trimDecimal(text, minDecimals = 0) {
  const [integer, fraction = ""] = text.split(".");
  let trimmed = fraction.replace(/0+$/, "");
  while (trimmed.length < minDecimals) trimmed += "0";
  return trimmed ? `${integer}.${trimmed}` : integer;
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

function saveRecords(records) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction("records", "readwrite");
    const store = tx.objectStore("records");
    records.forEach(record => store.put(record));
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}
