const Q_CONFIG = {
  DATA_SHEET_NAME: "图片分析",
  CACHE_SHEET_NAME: "__q_gallery_cache",
  NEXT_CACHE_SHEET_NAME: "__q_gallery_cache_next",
  BACKUP_CACHE_SHEET_NAME: "__q_gallery_cache_backup",
  STATE_SHEET_NAME: "__q_gallery_refresh",
  SHARED_CACHE_JSON_SHEET_NAME: "__q_gallery_shared_cache_json",
  DRIVE_CACHE_FILE_NAME: "q-gallery-cache.json",
  DRIVE_CACHE_FOLDER_ID: "1BiNcAV7uBfySk2nEztifgKus69COOs_i",
  DRIVE_CACHE_FILE_ID_PROPERTY: "Q_GALLERY_DRIVE_CACHE_FILE_ID",
  DRIVE_CACHE_FOLDER_ID_PROPERTY: "Q_GALLERY_DRIVE_CACHE_FOLDER_ID",
  SECRET_PROPERTY: "Q_GALLERY_API_SECRET",
  DATA_START_ROW: 3,
  // 读到 AB 列：J=点赞(index9)，AB=美工(index27) → 列数至少 28
  SOURCE_READ_COLS: 28,
  DEFAULT_LIMIT: 1000,
  DEFAULT_BATCH_SIZE: 1000,
  MIN_BATCH_SIZE: 300,
  MAX_LIMIT: 1000,
  TARGET_STEP_MS: 45000,
  STALE_STEP_MS: 90000,
  STABLE_BATCHES_TO_GROW: 2
};

const CACHE_HEADERS = [
  "id",
  "rowNumber",
  "name",
  "postId",
  "postLink",
  "lead",
  "water",
  "waterTime",
  "thumbnailUrl",
  "content",
  "rewrite",
  "date",
  "category",
  "likes",
  "comments",
  "shares",
  "postType",
  "ocr",
  "ocrTranslation",
  "audioText",
  "audioTranslation",
  "authorName",
  "sourceType",
  "sourceChannel",
  "conversionRate",
  "mediaMode",
  "thumbnailFallbackUrl",
  "pageName",
  "avatarUrl",
  "designer" // AB 列美工，排行榜名字后展示
];

function doPost(e) {
  try {
    const payload = parsePayload_(e);
    verifySecret_(payload);
    const action = String(payload.action || "health");
    let result;

    if (action === "health") result = health_();
    else if (action === "assets") result = assets_(payload);
    else if (action === "assetsSnapshot") result = assetsSnapshot_(payload);
    else if (action === "driveCache") result = driveCache_(payload);
    else if (action === "driveCacheContent") result = driveCacheContent_(payload);
    else if (action === "catalogs") result = catalogs_();
    else if (action === "quickPublishCache") result = quickPublishCache_(payload);
    else if (action === "importDriveCacheUrl") result = importDriveCacheUrl_(payload);
    else if (action === "refreshCancel") result = refreshCancel_();
    else if (action === "refreshStart") result = refreshStart_(payload);
    else if (action === "refreshStep") result = refreshStep_(payload);
    else if (action === "refreshStatus") result = refreshStatus_();
    else result = { ok: false, error: "Unknown action: " + action };

    return json_(result);
  } catch (error) {
    return json_({ ok: false, error: error.message || String(error) });
  }
}

function doGet(e) {
  return doPost(e);
}

function qGalleryRefreshWorker() {
  refreshStep_({});
}

function qGalleryDailyRefresh() {
  const state = getRefreshState_();
  if (state.running) {
    refreshStep_({ batchSize: state.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE });
    return;
  }
  refreshStart_({ batchSize: Q_CONFIG.DEFAULT_BATCH_SIZE });
}

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("图库缓存")
    .addItem("快速更新共享缓存", "qGalleryQuickPublishCache")
    .addItem("启动后台分批刷新", "qGalleryDailyRefresh")
    .addItem("停止后台分批刷新", "qGalleryStopRefresh")
    .addToUi();
}

function qGalleryQuickPublishCache() {
  const result = quickPublishCache_({});
  const ui = SpreadsheetApp.getUi();
  if (result.ok) {
    ui.alert(
      "共享缓存已更新",
      `已发布当前缓存 ${result.cachedRows || 0} 条。\n\nDrive JSON 直链：\n${result.driveCacheUrl || "未生成"}\n\n如果要从源表重建最新缓存，请使用“启动后台分批刷新”。`,
      ui.ButtonSet.OK
    );
    return;
  }
  ui.alert("共享缓存更新失败", result.error || "请稍后再试。", ui.ButtonSet.OK);
}

function qGalleryStopRefresh() {
  const result = refreshCancel_();
  const ui = SpreadsheetApp.getUi();
  ui.alert(
    result.ok ? "后台分批刷新已停止" : "停止失败",
    result.message || result.error || "已清理运行状态。",
    ui.ButtonSet.OK
  );
}

function qGalleryAuthorizeExternalRequest() {
  const response = UrlFetchApp.fetch("https://www.google.com/generate_204", {
    muteHttpExceptions: true
  });
  SpreadsheetApp.getUi().alert(
    "授权检查完成",
    "外部链接访问权限已可用，状态码：" + response.getResponseCode(),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function qGalleryAuthorizeAllPermissions() {
  const externalResponse = UrlFetchApp.fetch("https://www.google.com/generate_204", {
    muteHttpExceptions: true
  });
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const triggers = ScriptApp.getProjectTriggers();
  SpreadsheetApp.getUi().alert(
    "授权检查完成",
    [
      "外部请求状态码：" + externalResponse.getResponseCode(),
      "表格：" + spreadsheet.getName(),
      "共享缓存存储：隐藏 Sheet",
      "触发器数量：" + triggers.length
    ].join("\n"),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function health_() {
  const source = getSourceSheet_();
  const cache = getCacheSheet_();
  return {
    ok: true,
    sourceSheet: source.getName(),
    sourceRows: getSourceDataRowCount_(source),
    cacheRows: Math.max(0, cache.getLastRow() - 1),
    status: getRefreshState_()
  };
}

function assets_(payload) {
  const limit = clamp_(Number(payload.limit || Q_CONFIG.DEFAULT_LIMIT), 1, Q_CONFIG.MAX_LIMIT);
  const cursor = Math.max(0, Number(payload.cursor || 0));
  const mode = String(payload.mode || "cache");
  const cache = getCacheSheet_();
  const state = getRefreshState_();
  const hasCache = cache.getLastRow() > 1;

  if (mode === "cache" && hasCache) {
    const rows = readCacheRows_(cursor, limit);
    return {
      ok: true,
      rows: rows,
      cursor: String(cursor),
      nextCursor: cursor + rows.length < Math.max(0, cache.getLastRow() - 1) ? String(cursor + rows.length) : "",
      totalRows: Math.max(0, cache.getLastRow() - 1),
      runId: state.runId || "",
      source: "cache",
      catalogs: buildCatalogsFromRecords_(rows)
    };
  }

  const source = getSourceSheet_();
  const lastRow = source.getLastRow();
  const totalRows = getSourceDataRowCount_(source);
  if (!totalRows) {
    return { ok: true, rows: [], cursor: String(cursor), nextCursor: "", totalRows: 0, runId: state.runId || "", source: "sheet", catalogs: emptyCatalogs_() };
  }

  const startRow = cursor + Q_CONFIG.DATA_START_ROW;
  const available = Math.max(0, lastRow - startRow + 1);
  const take = Math.min(limit, available);
  const records = take
      ? (function() {
        const readCols = Q_CONFIG.SOURCE_READ_COLS || 28;
        const range = source.getRange(startRow, 1, take, readCols);
        const values = range.getValues();
        const formulas = range.getFormulas();
        const richTextValues = range.getRichTextValues();
        const avatarMap = buildAnchoredImageMap_(source, startRow, take, 20);
        const avatarAccessToken = getAvatarAccessToken_(source);
        return values.map(function(row, index) {
          return mapRow_(row, startRow + index, formulas[index], richTextValues[index], avatarMap[startRow + index], avatarAccessToken);
        });
      })().filter(isValidRecord_)
    : [];

  return {
    ok: true,
    rows: records,
    cursor: String(cursor),
    nextCursor: cursor + take < totalRows ? String(cursor + take) : "",
    totalRows: totalRows,
    runId: state.runId || "",
    source: "sheet",
    catalogs: buildCatalogsFromRecords_(records)
  };
}

function assetsSnapshot_(payload) {
  const mode = String(payload.mode || "cache");
  if (mode !== "cache") {
    return { ok: false, error: "assetsSnapshot only supports cache mode." };
  }

  const cache = getCacheSheet_();
  const state = getRefreshState_();
  const totalRows = Math.max(0, cache.getLastRow() - 1);
  const rows = totalRows ? readCacheRows_(0, totalRows) : [];
  return {
    ok: true,
    rows: rows,
    cursor: "0",
    nextCursor: "",
    totalRows: totalRows,
    runId: state.runId || "",
    source: "cache-snapshot",
    catalogs: buildCatalogsFromRecords_(rows)
  };
}

function driveCache_(payload) {
  const state = getRefreshState_();
  const sharedCache = getSharedCacheJsonMeta_();
  if (sharedCache.available) {
    return {
      ok: true,
      available: true,
      storage: "sheet",
      updatedAt: sharedCache.updatedAt,
      runId: sharedCache.runId || state.runId || "",
      totalRows: Number(sharedCache.totalRows || state.cachedRows || state.activeCacheRows || 0)
    };
  }

  let file = null;
  try {
    file = getDriveCacheFile_();
  } catch (error) {
    file = null;
  }
  if (!file) {
    return {
      ok: true,
      available: false,
      runId: state.runId || "",
      totalRows: Math.max(0, getCacheSheet_().getLastRow() - 1)
    };
  }

  return {
    ok: true,
    available: true,
    fileId: file.getId(),
    name: file.getName(),
    url: driveDownloadUrl_(file.getId()),
    viewUrl: file.getUrl(),
    updatedAt: file.getLastUpdated().toISOString(),
    runId: state.runId || "",
    totalRows: Number(state.cachedRows || state.activeCacheRows || 0)
  };
}

function driveCacheContent_(payload) {
  const sheetCache = readSharedCacheJson_();
  if (sheetCache) return sheetCache;

  let file = null;
  try {
    file = getDriveCacheFile_();
  } catch (error) {
    file = null;
  }
  if (!file) return { ok: false, error: "Drive cache file is not available." };

  const text = file.getBlob().getDataAsString("UTF-8");
  const data = JSON.parse(text);
  data.source = "drive-cache-proxy";
  return data;
}

function catalogs_() {
  const cache = getCacheSheet_();
  if (cache.getLastRow() > 1) {
    return { ok: true, catalogs: buildCatalogsFromRecords_(readCacheRows_(0, Math.max(0, cache.getLastRow() - 1))) };
  }

  const source = getSourceSheet_();
  const totalRows = getSourceDataRowCount_(source);
  return { ok: true, catalogs: emptyCatalogs_(), totalRows: totalRows };
}

function refreshStart_(payload) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    return {
      ok: true,
      status: getRefreshState_(),
      skipped: true,
      message: "Refresh is already running. Try again later."
    };
  }
  let runId = "";
  try {
    const source = getSourceSheet_();
    const cache = getCacheSheet_();
    const nextCache = prepareNextCacheSheet_();
    ensureCacheHeader_(cache);
    ensureCacheHeader_(nextCache);

    const state = {
      running: true,
      runId: Utilities.getUuid(),
      sourceSheet: source.getName(),
      nextRow: Q_CONFIG.DATA_START_ROW,
      processed: 0,
      cachedRows: 0,
      activeCacheRows: Math.max(0, cache.getLastRow() - 1),
      totalRows: getSourceDataRowCount_(source),
      batchSize: clampBatchSize_(Number(payload.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE)),
      stableBatches: 0,
      stepRunning: false,
      stepRow: 0,
      stepBatchSize: 0,
      stepStartedAt: "",
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      completedAt: "",
      error: ""
    };
    runId = state.runId;
    setRefreshState_(state);
    ensureWorkerTrigger_();
  } finally {
    lock.releaseLock();
  }

  return { ok: true, runId: runId, status: getRefreshState_(), queued: true };
}

function refreshStep_(payload) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    return { ok: true, status: getRefreshState_(), skipped: true };
  }

  try {
    const state = getRefreshState_();
    if (!state.running) {
      cleanupWorkerTriggers_();
      return { ok: true, status: state };
    }

    const source = getSourceSheet_();
    const nextCache = getNextCacheSheet_();
    ensureCacheHeader_(nextCache);
    if (!state.nextRow || state.nextRow < Q_CONFIG.DATA_START_ROW) {
      state.nextRow = Q_CONFIG.DATA_START_ROW;
    }

    recoverStaleStep_(state);
    const batchSize = clampBatchSize_(Number(payload.batchSize || state.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE));
    const lastRow = source.getLastRow();
    if (state.nextRow > lastRow) {
      completeRefresh_(state, nextCache, source);
      cleanupWorkerTriggers_();
      return { ok: true, status: state };
    }

    const take = Math.min(batchSize, lastRow - state.nextRow + 1);
    const stepStartedAt = Date.now();
    state.batchSize = batchSize;
    state.stepRunning = true;
    state.stepRow = state.nextRow;
    state.stepBatchSize = take;
    state.stepStartedAt = new Date(stepStartedAt).toISOString();
    state.updatedAt = state.stepStartedAt;
    setRefreshState_(state);

    const readCols = Q_CONFIG.SOURCE_READ_COLS || 28;
    const range = source.getRange(state.stepRow, 1, take, readCols);
    const values = range.getValues();
    const formulas = range.getFormulas();
    const richTextValues = range.getRichTextValues();
    const avatarMap = buildAnchoredImageMap_(source, state.stepRow, take, 20);
    const avatarAccessToken = getAvatarAccessToken_(source);
    const records = values.map(function(row, index) {
      const rowNumber = state.stepRow + index;
      return mapRow_(row, rowNumber, formulas[index], richTextValues[index], avatarMap[rowNumber], avatarAccessToken);
    }).filter(isValidRecord_);

    appendCacheRows_(nextCache, records);
    state.nextRow += take;
    state.processed += take;
    state.cachedRows = Math.max(0, nextCache.getLastRow() - 1);
    state.totalRows = getSourceDataRowCount_(source);
    state.updatedAt = new Date().toISOString();
    state.stepRunning = false;
    state.stepRow = 0;
    state.stepBatchSize = 0;
    state.stepStartedAt = "";
    state.error = "";
    tuneBatchAfterSuccess_(state, Date.now() - stepStartedAt);

    if (state.nextRow > lastRow) {
      completeRefresh_(state, nextCache, source);
      cleanupWorkerTriggers_();
    } else {
      ensureWorkerTrigger_();
    }

    setRefreshState_(state);
    return { ok: true, rows: records, status: state };
  } catch (error) {
    const state = getRefreshState_();
    trimCacheRowsTo_(getNextCacheSheet_(), Number(state.cachedRows || 0));
    state.batchSize = reduceBatchSize_(state.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE);
    state.stableBatches = 0;
    state.stepRunning = false;
    state.stepRow = 0;
    state.stepBatchSize = 0;
    state.stepStartedAt = "";
    state.error = error.message || String(error);
    state.updatedAt = new Date().toISOString();
    setRefreshState_(state);
    ensureWorkerTrigger_();
    return { ok: true, retrying: true, error: state.error, status: state };
  } finally {
    lock.releaseLock();
  }
}

function refreshStatus_() {
  const state = getRefreshState_();
  return { ok: true, status: state };
}

function refreshCancel_() {
  cleanupWorkerTriggers_();
  const state = resetRefreshState_("已手动停止后台分批刷新。");
  return {
    ok: true,
    message: "已停止后台分批刷新，可以重新运行“快速更新共享缓存”。",
    status: state
  };
}

function quickPublishCache_(payload) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    return {
      ok: false,
      status: getRefreshState_(),
      error: "Refresh is already running. Try again later."
    };
  }

  try {
    const source = getSourceSheet_();
    const cache = getCacheSheet_();
    ensureCacheHeader_(cache);
    const totalRows = getSourceDataRowCount_(source);
    const cachedRows = Math.max(0, cache.getLastRow() - 1);
    const runId = Utilities.getUuid();
    const startedAt = new Date().toISOString();
    const driveCache = publishDriveCacheSnapshot_({ runId: runId }, cache);

    const state = {
      running: false,
      runId: runId,
      sourceSheet: source.getName(),
      nextRow: source.getLastRow() + 1,
      processed: cachedRows,
      cachedRows: cachedRows,
      activeCacheRows: cachedRows,
      totalRows: totalRows,
      batchSize: clampBatchSize_(Number(payload.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE)),
      stableBatches: 0,
      stepRunning: false,
      stepRow: 0,
      stepBatchSize: 0,
      stepStartedAt: "",
      startedAt: startedAt,
      updatedAt: startedAt,
      completedAt: startedAt,
      driveCacheFileId: driveCache.fileId || "",
      driveCacheUrl: driveCache.url || "",
      driveCacheUpdatedAt: driveCache.updatedAt || "",
      driveCacheError: driveCache.error || "",
      error: ""
    };
    cleanupWorkerTriggers_();
    setRefreshState_(state);

    return {
      ok: true,
      runId: state.runId,
      totalRows: state.totalRows,
      cachedRows: state.cachedRows,
      driveCacheUrl: state.driveCacheUrl || "",
      driveCacheUpdatedAt: state.driveCacheUpdatedAt || "",
      status: state
    };
  } finally {
    lock.releaseLock();
  }
}

function importDriveCacheUrl_(payload) {
  try {
    const sourceUrl = normalizeDriveCacheImportUrl_(payload.url || payload.cacheUrl || "");
    if (!sourceUrl) {
      return { ok: false, error: "请提供 Google Drive JSON 缓存链接。" };
    }

    const response = UrlFetchApp.fetch(sourceUrl, {
      followRedirects: true,
      muteHttpExceptions: true
    });
    const statusCode = response.getResponseCode();
    if (statusCode < 200 || statusCode >= 300) {
      return { ok: false, error: "缓存链接下载失败：" + statusCode };
    }

    const data = JSON.parse(response.getContentText("UTF-8"));
    const rows = Array.isArray(data.rows) ? data.rows : [];
    if (!rows.length && Number(data.totalRows || 0) > 0) {
      return { ok: false, error: "链接内容不是完整的图库缓存 JSON。" };
    }

    const runId = data.runId || Utilities.getUuid();
    const updatedAt = new Date().toISOString();
    const payloadToPublish = {
      ok: true,
      rows: rows,
      cursor: "0",
      nextCursor: "",
      totalRows: rows.length,
      runId: runId,
      source: "drive-cache",
      updatedAt: updatedAt,
      catalogs: data.catalogs || buildCatalogsFromRecords_(rows)
    };

    const stored = writeSharedCacheJson_(payloadToPublish, JSON.stringify(payloadToPublish));
    const state = getRefreshState_();
    state.running = false;
    state.stepRunning = false;
    state.runId = runId;
    state.processed = rows.length;
    state.cachedRows = rows.length;
    state.activeCacheRows = rows.length;
    state.totalRows = rows.length;
    state.updatedAt = updatedAt;
    state.completedAt = updatedAt;
    state.driveCacheFileId = "";
    state.driveCacheUrl = "";
    state.driveCacheUpdatedAt = stored.updatedAt;
    state.driveCacheError = "";
    state.error = "";
    setRefreshState_(state);

    return {
      ok: true,
      runId: runId,
      cachedRows: rows.length,
      totalRows: rows.length,
      driveCacheUrl: "",
      storage: "sheet",
      driveCacheUpdatedAt: state.driveCacheUpdatedAt,
      status: state
    };
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}

function normalizeDriveCacheImportUrl_(value) {
  const text = text_(value);
  if (!text) return "";
  const fileMatch = text.match(/drive\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) return driveDownloadUrl_(fileMatch[1]);
  const idMatch = text.match(/[?&]id=([^&#]+)/i);
  if (/drive\.google\.com/i.test(text) && idMatch) return driveDownloadUrl_(decodeURIComponent(idMatch[1]));
  if (/^https:\/\/(?:drive|docs)\.google\.com\//i.test(text)) return text;
  if (/^https:\/\/(?:[^/]+\.)?googleusercontent\.com\//i.test(text)) return text;
  return "";
}

function writeSharedCacheJson_(payload, content) {
  const sheet = getSharedCacheJsonSheet_();
  const text = content || JSON.stringify(payload);
  const chunkSize = 45000;
  const chunks = [];
  for (let index = 0; index < text.length; index += chunkSize) {
    chunks.push([Math.floor(index / chunkSize), text.slice(index, index + chunkSize)]);
  }

  if (chunks.length + 3 > sheet.getMaxRows()) {
    throw new Error("共享缓存太大，当前状态表行数不足；请改用 CSV 本地导入或精简缓存。");
  }

  sheet.getRange(1, 3, sheet.getMaxRows(), 6).clearContent();
  sheet.getRange(1, 3, 1, 6).setValues([[
    "updatedAt",
    "runId",
    "totalRows",
    "chunkCount",
    "storage",
    "version"
  ]]);
  const updatedAt = payload.updatedAt || new Date().toISOString();
  sheet.getRange(2, 3, 1, 6).setValues([[
    updatedAt,
    payload.runId || "",
    Number(payload.totalRows || (payload.rows && payload.rows.length) || 0),
    chunks.length,
    "sheet",
    1
  ]]);
  if (chunks.length) {
    sheet.getRange(4, 3, chunks.length, 2).setValues(chunks);
  }
  sheet.hideSheet();
  return {
    updatedAt: updatedAt,
    runId: payload.runId || "",
    totalRows: Number(payload.totalRows || (payload.rows && payload.rows.length) || 0),
    chunkCount: chunks.length
  };
}

function readSharedCacheJson_() {
  const sheet = getSharedCacheJsonSheet_();
  if (!sheet || sheet.getLastRow() < 4) return null;

  const meta = getSharedCacheJsonMeta_();
  if (!meta.available) return null;

  const chunkCount = Number(meta.chunkCount || 0);
  if (!chunkCount) return null;

  const chunks = sheet.getRange(4, 3, chunkCount, 2).getValues()
    .sort(function(a, b) { return Number(a[0]) - Number(b[0]); })
    .map(function(row) { return row[1] || ""; });
  const data = JSON.parse(chunks.join(""));
  data.source = "sheet-cache";
  data.updatedAt = data.updatedAt || meta.updatedAt || "";
  data.runId = data.runId || meta.runId || "";
  data.totalRows = Number(data.totalRows || meta.totalRows || 0);
  return data;
}

function getSharedCacheJsonMeta_() {
  const sheet = getSharedCacheJsonSheet_();
  if (!sheet || sheet.getLastRow() < 2) {
    return { available: false };
  }
  const values = sheet.getRange(2, 3, 1, 6).getValues()[0];
  return {
    available: Number(values[3] || 0) > 0,
    updatedAt: values[0] || "",
    runId: values[1] || "",
    totalRows: Number(values[2] || 0),
    chunkCount: Number(values[3] || 0),
    storage: values[4] || "sheet"
  };
}

function getSharedCacheJsonSheet_() {
  return getStateSheet_();
}

function recoverStaleStep_(state) {
  if (!state.stepRunning || !state.stepStartedAt) return;

  const started = new Date(state.stepStartedAt).getTime();
  if (!started || Date.now() - started < Q_CONFIG.STALE_STEP_MS) return;

  const nextCache = getNextCacheSheet_();
  trimCacheRowsTo_(nextCache, Number(state.cachedRows || 0));
  state.nextRow = state.stepRow || state.nextRow;
  state.batchSize = reduceBatchSize_(state.stepBatchSize || state.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE);
  state.stableBatches = 0;
  state.stepRunning = false;
  state.stepRow = 0;
  state.stepBatchSize = 0;
  state.stepStartedAt = "";
  state.error = "Last batch timed out; retrying with batch size " + state.batchSize + ".";
}

function tuneBatchAfterSuccess_(state, elapsedMs) {
  if (elapsedMs > Q_CONFIG.TARGET_STEP_MS) {
    state.batchSize = reduceBatchSize_(state.batchSize || Q_CONFIG.DEFAULT_BATCH_SIZE);
    state.stableBatches = 0;
    return;
  }

  state.stableBatches = Number(state.stableBatches || 0) + 1;
  if (state.stableBatches < Q_CONFIG.STABLE_BATCHES_TO_GROW) return;

  state.batchSize = growBatchSize_(state.batchSize || Q_CONFIG.MIN_BATCH_SIZE);
  state.stableBatches = 0;
}

function completeRefresh_(state, nextCache, source) {
  promoteNextCache_(nextCache);
  const cache = getCacheSheet_();
  let driveCache = {};
  try {
    driveCache = publishDriveCacheSnapshot_(state, cache);
  } catch (error) {
    driveCache = { error: error.message || String(error) };
  }
  state.running = false;
  state.stepRunning = false;
  state.stepRow = 0;
  state.stepBatchSize = 0;
  state.stepStartedAt = "";
  state.completedAt = new Date().toISOString();
  state.updatedAt = state.completedAt;
  state.cachedRows = Math.max(0, cache.getLastRow() - 1);
  state.activeCacheRows = state.cachedRows;
  state.totalRows = getSourceDataRowCount_(source);
  state.driveCacheFileId = driveCache.fileId || "";
  state.driveCacheUrl = driveCache.url || "";
  state.driveCacheUpdatedAt = driveCache.updatedAt || "";
  state.driveCacheError = driveCache.error || "";
  state.error = "";
  setRefreshState_(state);
}

function publishDriveCacheSnapshot_(state, cache) {
  ensureCacheHeader_(cache);
  const totalRows = Math.max(0, cache.getLastRow() - 1);
  const rows = totalRows ? readCacheRows_(0, totalRows) : [];
  const payload = {
    ok: true,
    rows: rows,
    cursor: "0",
    nextCursor: "",
    totalRows: totalRows,
    runId: state.runId || "",
    source: "drive-cache",
    updatedAt: new Date().toISOString(),
    catalogs: buildCatalogsFromRecords_(rows)
  };

  const content = JSON.stringify(payload);
  const stored = writeSharedCacheJson_(payload, content);
  return {
    fileId: "",
    url: "",
    updatedAt: stored.updatedAt,
    storage: "sheet"
  };
}

function upsertDriveCacheFile_(content) {
  const properties = PropertiesService.getScriptProperties();
  const existingId = properties.getProperty(Q_CONFIG.DRIVE_CACHE_FILE_ID_PROPERTY);
  const folder = getDriveCacheFolder_();
  let file = null;

  if (existingId) {
    try {
      file = DriveApp.getFileById(existingId);
      file.setContent(content);
    } catch (error) {
      file = null;
    }
  }

  if (!file && folder) {
    const matches = folder.getFilesByName(Q_CONFIG.DRIVE_CACHE_FILE_NAME);
    if (matches.hasNext()) {
      file = matches.next();
      file.setContent(content);
      properties.setProperty(Q_CONFIG.DRIVE_CACHE_FILE_ID_PROPERTY, file.getId());
    }
  }

  if (!file) {
    file = createDriveCacheFile_(folder, content);
    properties.setProperty(Q_CONFIG.DRIVE_CACHE_FILE_ID_PROPERTY, file.getId());
  }

  file.setName(Q_CONFIG.DRIVE_CACHE_FILE_NAME);
  if (folder) {
    try {
      file.moveTo(folder);
    } catch (error) {
      // Keep the cache file where it was created if the target folder rejects script writes.
    }
  }
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  return file;
}

function createDriveCacheFile_(folder, content) {
  if (folder) {
    try {
      return folder.createFile(Q_CONFIG.DRIVE_CACHE_FILE_NAME, content, "application/json");
    } catch (error) {
      // Some link-shared folders allow UI editing but reject Apps Script folder writes.
    }
  }
  return DriveApp.createFile(Q_CONFIG.DRIVE_CACHE_FILE_NAME, content, "application/json");
}

function getDriveCacheFolder_() {
  const folderId = text_(
    Q_CONFIG.DRIVE_CACHE_FOLDER_ID
    || PropertiesService.getScriptProperties().getProperty(Q_CONFIG.DRIVE_CACHE_FOLDER_ID_PROPERTY)
  );
  if (!folderId) return null;
  return DriveApp.getFolderById(folderId);
}

function getDriveCacheFile_() {
  const fileId = PropertiesService.getScriptProperties().getProperty(Q_CONFIG.DRIVE_CACHE_FILE_ID_PROPERTY);
  if (!fileId) return null;
  try {
    return DriveApp.getFileById(fileId);
  } catch (error) {
    return null;
  }
}

function driveDownloadUrl_(fileId) {
  return "https://drive.google.com/uc?export=download&id=" + encodeURIComponent(fileId);
}

function promoteNextCache_(nextCache) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const cache = spreadsheet.getSheetByName(Q_CONFIG.CACHE_SHEET_NAME);
  const backup = spreadsheet.getSheetByName(Q_CONFIG.BACKUP_CACHE_SHEET_NAME);

  if (backup) spreadsheet.deleteSheet(backup);
  if (cache) cache.setName(Q_CONFIG.BACKUP_CACHE_SHEET_NAME);
  nextCache.setName(Q_CONFIG.CACHE_SHEET_NAME);

  const oldCache = spreadsheet.getSheetByName(Q_CONFIG.BACKUP_CACHE_SHEET_NAME);
  if (oldCache) spreadsheet.deleteSheet(oldCache);
}

function trimCacheRowsTo_(cache, keepRows) {
  ensureCacheHeader_(cache);
  const lastRow = cache.getLastRow();
  const firstExtraRow = Math.max(0, Number(keepRows || 0)) + 2;
  if (lastRow >= firstExtraRow) {
    cache.deleteRows(firstExtraRow, lastRow - firstExtraRow + 1);
  }
}

function clampBatchSize_(value) {
  return clamp_(value, Q_CONFIG.MIN_BATCH_SIZE, Q_CONFIG.MAX_LIMIT);
}

function reduceBatchSize_(value) {
  const current = clampBatchSize_(Number(value || Q_CONFIG.DEFAULT_BATCH_SIZE));
  if (current > 500) return 500;
  if (current > Q_CONFIG.MIN_BATCH_SIZE) return Q_CONFIG.MIN_BATCH_SIZE;
  return Q_CONFIG.MIN_BATCH_SIZE;
}

function growBatchSize_(value) {
  const current = clampBatchSize_(Number(value || Q_CONFIG.MIN_BATCH_SIZE));
  if (current < 500) return 500;
  return Q_CONFIG.MAX_LIMIT;
}

function mapRow_(row, rowNumber, formulaRow, richTextRow, anchoredAvatarUrl, avatarAccessToken) {
  const postType = text_(row[12]);
  const category = text_(row[8]);
  const mediaMode = isVideo_(postType, category) ? "video" : "image";
  const postId = text_(row[1]);
  const authorName = text_(row[17]);
  const postLink = linkUrl_(row[2], formulaRow && formulaRow[2], richTextRow && richTextRow[2]);
  const thumbnailUrl = imageUrl_(row[4], formulaRow && formulaRow[4], richTextRow && richTextRow[4]);
  const avatarUrl = imageUrl_(row[19], formulaRow && formulaRow[19], richTextRow && richTextRow[19])
    || anchoredAvatarUrl
    || facebookAvatarUrl_(authorName, avatarAccessToken);
  const thumbnailFallbackUrl = imageUrl_(row[22], formulaRow && formulaRow[22], richTextRow && richTextRow[22]);

  return {
    id: postId || postLink || "row-" + rowNumber,
    rowNumber: rowNumber,
    name: text_(row[0]),
    postId: postId,
    postLink: postLink,
    lead: row[3],
    water: row[25],
    waterTime: dateTimeText_(row[26]),
    thumbnailUrl: thumbnailUrl,
    thumbnailFallbackUrl: thumbnailFallbackUrl,
    content: text_(row[5]),
    rewrite: text_(row[6]),
    date: dateText_(row[7]),
    category: category,
    likes: row[9], // J 列：点赞排行
    comments: row[10],
    shares: row[11],
    postType: postType,
    ocr: text_(row[13]),
    ocrTranslation: text_(row[14]),
    audioText: text_(row[15]),
    audioTranslation: text_(row[16]),
    authorName: authorName,
    sourceType: text_(row[20]),
    sourceChannel: text_(row[21]),
    conversionRate: row[23],
    mediaMode: mediaMode,
    pageName: text_(row[24]),
    avatarUrl: avatarUrl,
    designer: text_(row[27]) // AB 列：美工名字
  };
}

function isValidRecord_(record) {
  return Boolean(
    text_(record.postId)
    || text_(record.postLink)
    || text_(record.thumbnailUrl)
    || text_(record.thumbnailFallbackUrl)
  );
}

function readCacheRows_(cursor, limit) {
  const cache = getCacheSheet_();
  ensureCacheHeader_(cache);
  const lastRow = cache.getLastRow();
  if (lastRow < 2) return [];

  const startRow = cursor + 2;
  const take = Math.min(limit, Math.max(0, lastRow - startRow + 1));
  if (take <= 0) return [];

  return cache.getRange(startRow, 1, take, CACHE_HEADERS.length).getValues().map(function(row) {
    const record = {};
    CACHE_HEADERS.forEach(function(key, index) {
      record[key] = row[index];
    });
    return record;
  });
}

function appendCacheRows_(cache, records) {
  if (!records.length) return;
  const values = records.map(function(record) {
    return CACHE_HEADERS.map(function(key) {
      return record[key] === undefined || record[key] === null ? "" : record[key];
    });
  });
  cache.getRange(cache.getLastRow() + 1, 1, values.length, CACHE_HEADERS.length).setValues(values);
}

function clearCache_(cache) {
  const lastRow = cache.getLastRow();
  if (lastRow > 1) {
    cache.getRange(2, 1, lastRow - 1, CACHE_HEADERS.length).clearContent();
  }
}

function ensureCacheHeader_(cache) {
  migrateWaterTimeColumn_(cache);
  const current = cache.getRange(1, 1, 1, CACHE_HEADERS.length).getValues()[0];
  const needsHeader = CACHE_HEADERS.some(function(header, index) {
    return current[index] !== header;
  });
  if (needsHeader) {
    cache.getRange(1, 1, 1, CACHE_HEADERS.length).setValues([CACHE_HEADERS]);
    cache.setFrozenRows(1);
  }
}

function migrateWaterTimeColumn_(cache) {
  const width = Math.max(cache.getLastColumn(), CACHE_HEADERS.length);
  const current = cache.getRange(1, 1, 1, width).getValues()[0];
  if (current.indexOf("waterTime") !== -1) return;

  const waterIndex = current.indexOf("water");
  const thumbnailIndex = current.indexOf("thumbnailUrl");
  if (waterIndex !== -1 && thumbnailIndex === waterIndex + 1) {
    cache.insertColumnAfter(waterIndex + 1);
  }
}

function getSourceSheet_() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  if (Q_CONFIG.DATA_SHEET_NAME) {
    const named = spreadsheet.getSheetByName(Q_CONFIG.DATA_SHEET_NAME);
    if (named) return named;
  }

  const sheets = spreadsheet.getSheets();
  for (let i = 0; i < sheets.length; i += 1) {
    const name = sheets[i].getName();
    if (name !== Q_CONFIG.CACHE_SHEET_NAME && name !== Q_CONFIG.STATE_SHEET_NAME && name.indexOf("__q_") !== 0) {
      return sheets[i];
    }
  }
  return sheets[0];
}

function getSourceDataRowCount_(source) {
  return Math.max(0, source.getLastRow() - Q_CONFIG.DATA_START_ROW + 1);
}

function getCacheSheet_() {
  return getOrCreateSheet_(Q_CONFIG.CACHE_SHEET_NAME);
}

function getNextCacheSheet_() {
  return getOrCreateSheet_(Q_CONFIG.NEXT_CACHE_SHEET_NAME);
}

function prepareNextCacheSheet_() {
  const nextCache = getNextCacheSheet_();
  nextCache.clear();
  ensureCacheHeader_(nextCache);
  return nextCache;
}

function getStateSheet_() {
  return getOrCreateSheet_(Q_CONFIG.STATE_SHEET_NAME);
}

function getOrCreateSheet_(name) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = spreadsheet.getSheetByName(name);
  if (!sheet) sheet = spreadsheet.insertSheet(name);
  return sheet;
}

function getRefreshState_() {
  const sheet = getStateSheet_();
  const value = sheet.getRange(1, 1).getValue();
  if (!value) {
    return {
      running: false,
      runId: "",
      sourceSheet: getSourceSheet_().getName(),
      nextRow: Q_CONFIG.DATA_START_ROW,
      processed: 0,
      cachedRows: Math.max(0, getCacheSheet_().getLastRow() - 1),
      activeCacheRows: Math.max(0, getCacheSheet_().getLastRow() - 1),
      totalRows: getSourceDataRowCount_(getSourceSheet_()),
      batchSize: Q_CONFIG.DEFAULT_BATCH_SIZE,
      stableBatches: 0,
      stepRunning: false,
      stepRow: 0,
      stepBatchSize: 0,
      stepStartedAt: "",
      startedAt: "",
      updatedAt: "",
      completedAt: "",
      driveCacheFileId: "",
      driveCacheUrl: "",
      driveCacheUpdatedAt: "",
      driveCacheError: "",
      error: ""
    };
  }

  try {
    return JSON.parse(value);
  } catch (error) {
    return { running: false, error: "Refresh state is invalid." };
  }
}

function setRefreshState_(state) {
  const sheet = getStateSheet_();
  sheet.getRange(1, 1).setValue(JSON.stringify(state));
}

function resetRefreshState_(message) {
  const source = getSourceSheet_();
  const cache = getCacheSheet_();
  const state = {
    running: false,
    runId: "",
    sourceSheet: source.getName(),
    nextRow: Q_CONFIG.DATA_START_ROW,
    processed: 0,
    cachedRows: Math.max(0, cache.getLastRow() - 1),
    activeCacheRows: Math.max(0, cache.getLastRow() - 1),
    totalRows: getSourceDataRowCount_(source),
    batchSize: Q_CONFIG.DEFAULT_BATCH_SIZE,
    stableBatches: 0,
    stepRunning: false,
    stepRow: 0,
    stepBatchSize: 0,
    stepStartedAt: "",
    startedAt: "",
    updatedAt: new Date().toISOString(),
    completedAt: "",
    driveCacheFileId: "",
    driveCacheUrl: "",
    driveCacheUpdatedAt: "",
    driveCacheError: "",
    error: message || ""
  };
  setRefreshState_(state);
  return state;
}

function ensureWorkerTrigger_() {
  const exists = ScriptApp.getProjectTriggers().some(function(trigger) {
    return trigger.getHandlerFunction() === "qGalleryRefreshWorker";
  });
  if (!exists) {
    ScriptApp.newTrigger("qGalleryRefreshWorker").timeBased().everyMinutes(1).create();
  }
}

function cleanupWorkerTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (trigger.getHandlerFunction() === "qGalleryRefreshWorker") {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function buildCatalogsFromRecords_(records) {
  const catalogs = emptyCatalogs_();
  records.forEach(function(record) {
    pushUnique_(catalogs.categories, record.category);
    pushUnique_(catalogs.sourceTypes, record.sourceType);
    pushUnique_(catalogs.sourceChannels, record.sourceChannel);
    pushUnique_(catalogs.postTypes, record.postType);
  });
  catalogs.categories.sort();
  catalogs.sourceTypes.sort();
  catalogs.sourceChannels.sort();
  catalogs.postTypes.sort();
  return catalogs;
}

function emptyCatalogs_() {
  return {
    categories: [],
    sourceTypes: [],
    sourceChannels: [],
    postTypes: []
  };
}

function pushUnique_(list, value) {
  const text = text_(value);
  if (text && list.indexOf(text) === -1) list.push(text);
}

function parsePayload_(e) {
  if (e && e.postData && e.postData.contents) {
    return JSON.parse(e.postData.contents);
  }
  return e && e.parameter ? e.parameter : {};
}

function verifySecret_(payload) {
  const required = PropertiesService.getScriptProperties().getProperty(Q_CONFIG.SECRET_PROPERTY);
  if (required && payload.secret !== required) {
    throw new Error("Unauthorized");
  }
}

function json_(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function text_(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function dateText_(value) {
  if (!value) return "";
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  return text_(value);
}

function dateTimeText_(value) {
  if (!value) return "";
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm:ss");
  }
  return text_(value);
}

function linkUrl_(value, formula, richText) {
  return imageUrlFromFormula_(formula) || richTextUrl_(richText) || urlFromText_(value);
}

function imageUrl_(value, formula, richText) {
  return imageUrlFromFormula_(formula) || richTextUrl_(richText) || cellImageUrl_(value) || urlFromText_(value);
}

function imageUrlFromFormula_(formula) {
  const text = text_(formula);
  if (!text) return "";
  const imageMatch = text.match(/=\s*IMAGE\s*\(\s*"((?:[^"]|"")+)"/i);
  if (imageMatch) return imageMatch[1].replace(/""/g, '"').trim();
  const hyperlinkMatch = text.match(/=\s*HYPERLINK\s*\(\s*"((?:[^"]|"")+)"/i);
  if (hyperlinkMatch) return hyperlinkMatch[1].replace(/""/g, '"').trim();
  return "";
}

function richTextUrl_(richText) {
  if (!richText) return "";
  try {
    if (typeof richText.getLinkUrl === "function") {
      const link = richText.getLinkUrl();
      if (link) return link;
    }
    if (typeof richText.getRuns === "function") {
      const runs = richText.getRuns();
      for (let i = 0; i < runs.length; i += 1) {
        if (typeof runs[i].getLinkUrl === "function") {
          const runLink = runs[i].getLinkUrl();
          if (runLink) return runLink;
        }
      }
    }
  } catch (error) {
    return "";
  }
  return "";
}

function cellImageUrl_(value) {
  if (!value || typeof value !== "object") return "";
  return imageObjectUrl_(value);
}

function imageObjectUrl_(value) {
  if (!value || typeof value !== "object") return "";
  const methodNames = ["getContentUrl", "getUrl"];
  for (let i = 0; i < methodNames.length; i += 1) {
    const method = value[methodNames[i]];
    if (typeof method === "function") {
      try {
        const url = method.call(value);
        if (url) return String(url).trim();
      } catch (error) {}
    }
  }
  return imageObjectDataUrl_(value);
}

function imageObjectDataUrl_(value) {
  const blobMethodNames = ["getBlob", "getAs"];
  for (let i = 0; i < blobMethodNames.length; i += 1) {
    const method = value[blobMethodNames[i]];
    if (typeof method !== "function") continue;
    try {
      const blob = blobMethodNames[i] === "getAs" ? method.call(value, "image/png") : method.call(value);
      if (!blob || typeof blob.getBytes !== "function") continue;
      const bytes = blob.getBytes();
      if (!bytes || bytes.length > 40000) return "";
      const mimeType = typeof blob.getContentType === "function" ? blob.getContentType() : "image/png";
      return "data:" + (mimeType || "image/png") + ";base64," + Utilities.base64Encode(bytes);
    } catch (error) {}
  }
  return "";
}

function buildAnchoredImageMap_(sheet, startRow, rowCount, column) {
  const map = {};
  if (!sheet || typeof sheet.getImages !== "function") return map;

  try {
    const endRow = startRow + rowCount - 1;
    const images = sheet.getImages();
    images.forEach(function(image) {
      try {
        if (typeof image.getAnchorCell !== "function") return;
        const cell = image.getAnchorCell();
        if (!cell) return;
        const row = cell.getRow();
        const col = cell.getColumn();
        if (col !== column || row < startRow || row > endRow) return;
        const url = imageObjectUrl_(image);
        if (url) map[row] = url;
      } catch (error) {}
    });
  } catch (error) {}

  return map;
}

function getAvatarAccessToken_(sheet) {
  if (!sheet) return "";
  try {
    const formulas = sheet.getRange(1, 20, Math.min(10, sheet.getMaxRows()), 1).getFormulas();
    for (let row = 0; row < formulas.length; row += 1) {
      const formula = text_(formulas[row][0]);
      const match = formula.match(/access_token=([^"&\s,)]+)/i);
      if (match) return match[1];
    }
  } catch (error) {}
  return "";
}

function facebookAvatarUrl_(authorId, accessToken) {
  const id = text_(authorId).replace(/[^\d]/g, "");
  if (!id) return "";
  let url = "https://graph.facebook.com/" + encodeURIComponent(id) + "/picture?width=100&height=100";
  if (accessToken) {
    url += "&access_token=" + encodeURIComponent(accessToken);
  }
  return url;
}

function urlFromText_(value) {
  if (value && typeof value === "object") return "";
  const text = text_(value);
  if (!text) return "";
  const formulaUrl = imageUrlFromFormula_(text);
  if (formulaUrl) return formulaUrl;
  const directUrl = text.match(/https?:\/\/[^\s"'<>)]+/i);
  return directUrl ? directUrl[0].trim() : text;
}

function isVideo_(postType, category) {
  return /短视频|视频|video|reel|tiktok|douyin|快手/i.test(text_(postType) + " " + text_(category));
}

function clamp_(value, min, max) {
  if (!isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}
