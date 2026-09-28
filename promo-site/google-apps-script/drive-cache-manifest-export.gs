/**
 * ========== 用法（复制整文件到 Apps Script）==========
 *
 * 【推荐：只装一个自动流水线】
 *   1) 运行一次 installQGalleryAutoPipeline()
 *      → 每 1 分钟 qGalleryAutoWorker（有任务就续跑，完成自动推 CDN）
 *      → 每 fullRefreshEveryHours 小时排队全量（默认 3 小时）
 *   2) 手动立刻全量：qGalleryExportForceRestartAndPublish()
 *   3) 看进度：左侧「执行」最新日志，或 qGalleryExportStatus()
 *
 * 【日志会打印】
 *   当前行/总行/剩余行/百分比、有效条数、分片数、maxDate、
 *   本批读了哪几行、是否写分片、完成后 CDN 推送结果
 *
 * 列映射: J=点赞, AB=美工, W=图片, T=头像, R=作者ID
 */

const Q_GALLERY_DRIVE_EXPORT = {
  folderId: "1BiNcAV7uBfySk2nEztifgKus69COOs_i",
  dataSheetName: "图片分析",
  manifestFileName: "q-gallery-cache-manifest.json",
  checkpointFileName: "q-gallery-export-checkpoint.json",
  bufferFileName: "q-gallery-export-buffer.json",
  chunkPrefix: "Q_GALLERY_ASSETS",
  rowsPerChunk: 1200,
  // 有数据区域的批大小
  readBatchRows: 250,
  // 连续空批后加大跳读（加快扫空白尾部）
  readBatchRowsEmpty: 800,
  maxRuntimeMs: 270000,
  continueAfterMs: 30000,
  checkpointMaxAgeMs: 48 * 60 * 60 * 1000,
  includeRaw: false,
  startRow: 3,
  shareFiles: false,
  cleanupOldChunks: true,
  cleanupLimit: 120,

  // 连续多少批「0 有效行」后提前结束（避免 getLastRow 虚高扫几万空行）
  emptyBatchesToStop: 8,
  // 智能找真实末行：在 A/C/H/J 列从下往上探，比 getLastRow 准且快
  useSmartLastRow: true,

  promoPublishUrl: "https://promo.zhixianglife.com/api/publish-cache",
  promoPublishSecret: "",
  // 兼容旧配置：仅当 fullRefreshEveryHours 未启用时用「每天几点」
  dailyHour: 3,
  // ★ 全量刷新间隔（小时）。3 = 每 3 小时排队一次全量导出+推 CDN
  fullRefreshEveryHours: 3,

  idleLogMinIntervalMs: 10 * 60 * 1000
};

const Q_GALLERY_PROP = {
  PENDING_FULL: "Q_GALLERY_PENDING_FULL_EXPORT",
  LAST_IDLE_LOG: "Q_GALLERY_LAST_IDLE_LOG_AT",
  LAST_SUCCESS: "Q_GALLERY_LAST_SUCCESS_JSON"
};

// ---------------------------------------------------------------------------
// 对外入口
// ---------------------------------------------------------------------------

/**
 * ★ 唯一推荐的自动入口（给触发器用）
 * - 有进行中的导出 → 续跑，全部完成则推 CDN
 * - 属性里挂了「待全量」→ 强制重开一轮并推 CDN
 * - 否则空闲（少量日志）
 */
function qGalleryAutoWorker() {
  const props = PropertiesService.getScriptProperties();
  const folder = DriveApp.getFolderById(Q_GALLERY_DRIVE_EXPORT.folderId);
  const state = loadQGalleryCheckpoint_(folder);
  const pending = props.getProperty(Q_GALLERY_PROP.PENDING_FULL) === "1";

  Logger.log("---- qGalleryAutoWorker 唤醒 " + new Date().toLocaleString() + " ----");

  if (state && (state.status === "running" || state.status === "error")) {
    Logger.log("检测到进行中任务，开始续跑（完成后会推 CDN）…");
    state.publishAfter = true;
    // 不 forceRestart
    return qGalleryExportTick_({
      showUi: false,
      publishAfter: true,
      forceRestart: false,
      isResume: true,
      fromWorker: true
    });
  }

  if (pending) {
    props.deleteProperty(Q_GALLERY_PROP.PENDING_FULL);
    Logger.log("检测到「待全量更新」标记 → 强制重开导出并推 CDN");
    return qGalleryExportTick_({
      showUi: false,
      publishAfter: true,
      forceRestart: true,
      isResume: false,
      fromWorker: true
    });
  }

  // 空闲
  const now = Date.now();
  const lastIdle = Number(props.getProperty(Q_GALLERY_PROP.LAST_IDLE_LOG) || 0);
  const gap = Number(Q_GALLERY_DRIVE_EXPORT.idleLogMinIntervalMs || 600000);
  if (now - lastIdle >= gap) {
    props.setProperty(Q_GALLERY_PROP.LAST_IDLE_LOG, String(now));
    const lastOk = props.getProperty(Q_GALLERY_PROP.LAST_SUCCESS) || "";
    Logger.log(
      [
        "========== 空闲（无需续跑）==========",
        "时间: " + new Date().toLocaleString(),
        "说明: 没有进行中的导出，也没有待全量标记",
        "上次成功: " + (lastOk || "（尚无记录）"),
        "手动全量: 运行 qGalleryExportForceRestartAndPublish",
        "或: 运行 qGalleryRequestFullRefresh 挂上待全量，等下分钟 worker 开跑",
        "===================================="
      ].join("\n")
    );
  }
  return { ok: true, idle: true };
}

/**
 * 挂上「待全量」标记（由每分钟 worker 执行 force 重开+推 CDN）
 * - 若已有导出在跑：只打日志，等当前轮结束后的下一轮再全量（避免打断）
 * - 定时（每 3 小时）/ 手动都可调用
 */
function qGalleryRequestFullRefresh() {
  const props = PropertiesService.getScriptProperties();
  const folder = DriveApp.getFolderById(Q_GALLERY_DRIVE_EXPORT.folderId);
  const state = loadQGalleryCheckpoint_(folder);

  props.setProperty(Q_GALLERY_PROP.PENDING_FULL, "1");

  if (state && (state.status === "running" || state.status === "error")) {
    Logger.log(
      "已排队全量更新（当前有导出进行中 " +
        exportPct_(state) +
        "%，等本轮完成后 worker 会再开全量）。buildId=" +
        (state.buildId || "-")
    );
    return { ok: true, pending: true, deferred: true };
  }

  Logger.log(
    "已排队全量更新。qGalleryAutoWorker 约 1 分钟内将强制重开导出并推 CDN。"
  );
  return { ok: true, pending: true, deferred: false };
}

/**
 * ★ 一键安装自动流水线（只需这一个安装函数）
 * - 每分钟：qGalleryAutoWorker（续跑 + 完成后推 CDN）
 * - 每 fullRefreshEveryHours 小时：排队全量（默认 3 小时一轮）
 */
function installQGalleryAutoPipeline() {
  uninstallQGalleryAutoPipeline();

  // 1) 每分钟：续跑进行中的导出 / 执行「待全量」标记
  ScriptApp.newTrigger("qGalleryAutoWorker").timeBased().everyMinutes(1).create();

  // 2) 定时排队全量
  const everyH = Number(Q_GALLERY_DRIVE_EXPORT.fullRefreshEveryHours || 0);
  let scheduleDesc = "";
  if (everyH > 0) {
    // Apps Script 支持 everyHours(1~6) 等；3 小时 = everyHours(3)
    const h = Math.max(1, Math.min(6, Math.round(everyH)));
    ScriptApp.newTrigger("qGalleryRequestFullRefresh").timeBased().everyHours(h).create();
    scheduleDesc = "每 " + h + " 小时 qGalleryRequestFullRefresh → 排队全量更新（导出+推CDN）";
  } else {
    const hour = Number(Q_GALLERY_DRIVE_EXPORT.dailyHour || 3);
    ScriptApp.newTrigger("qGalleryRequestFullRefresh").timeBased().atHour(hour).everyDays(1).create();
    scheduleDesc = "每天 " + hour + " 点 qGalleryRequestFullRefresh → 排队全量更新";
  }

  const hShow = everyH > 0 ? Math.max(1, Math.min(6, Math.round(everyH))) : 0;
  const msg =
    "已安装自动流水线 v3h：\n" +
    "1) 每分钟 qGalleryAutoWorker → 有任务就续跑，完成即推 CDN\n" +
    "2) " +
    scheduleDesc +
    "\n\n" +
    "说明：\n" +
    "· 「每分钟」= 检查/续跑，不是每分钟全量\n" +
    "· 「每 " +
    (hShow || "N") +
    " 小时」= 排队全量导出并同步网站\n" +
    "· 立刻全量：qGalleryExportForceRestartAndPublish\n" +
    "· 看进度：左侧「执行」点最新一条日志\n" +
    "· 若仍显示「每天 3 点」= 脚本未更新，请整份粘贴最新 gs 后再安装";
  // 不用 alert：在编辑器里常导致一直转圈，只写日志
  Logger.log(msg);
  return {
    ok: true,
    everyMinutes: "qGalleryAutoWorker",
    fullRefreshEveryHours: hShow,
    scheduleDesc: scheduleDesc,
    version: "v3h"
  };
}

function uninstallQGalleryAutoPipeline() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    const fn = trigger.getHandlerFunction();
    if (
      fn === "qGalleryAutoWorker" ||
      fn === "qGalleryRequestFullRefresh" ||
      fn === "qGalleryExportResume" ||
      fn === "qGalleryExportAndPublishCdn" ||
      fn === "qGalleryExportDriveCacheManifest"
    ) {
      try {
        ScriptApp.deleteTrigger(trigger);
      } catch (e) {}
    }
  });
  uninstallDailyPromoCdnRefresh();
  clearQGalleryExportContinueTriggers_();
  Logger.log("已卸载自动流水线相关触发器");
  return { ok: true };
}

/** 兼容旧名：改为安装完整流水线 */
function installDailyPromoCdnRefresh() {
  return installQGalleryAutoPipeline();
}

function uninstallDailyPromoCdnRefresh() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "qGalleryExportAndPublishCdn") {
      try {
        ScriptApp.deleteTrigger(trigger);
      } catch (e) {}
    }
  });
  return { ok: true };
}

/** 手动导出（带 UI）；完成后默认不推 CDN */
function qGalleryExportDriveCacheManifest() {
  return qGalleryExportTick_({ showUi: true, publishAfter: false, forceRestart: false });
}

function qGalleryExportDriveCacheManifestSilent() {
  return qGalleryExportTick_({ showUi: false, publishAfter: false, forceRestart: false });
}

/** 有进度则续跑，无则新开；完成后推 CDN */
function qGalleryExportAndPublishCdn() {
  return qGalleryExportTick_({ showUi: false, publishAfter: true, forceRestart: false });
}

/** 手动续跑（与 worker 相同逻辑） */
function qGalleryExportResume() {
  return qGalleryExportTick_({ showUi: false, publishAfter: true, forceRestart: false, isResume: true });
}

function qGalleryCancelExport() {
  clearQGalleryExportContinueTriggers_();
  const folder = DriveApp.getFolderById(Q_GALLERY_DRIVE_EXPORT.folderId);
  trashQGalleryNamedFile_(folder, Q_GALLERY_DRIVE_EXPORT.checkpointFileName);
  trashQGalleryNamedFile_(folder, Q_GALLERY_DRIVE_EXPORT.bufferFileName);
  PropertiesService.getScriptProperties().deleteProperty(Q_GALLERY_PROP.PENDING_FULL);
  Logger.log("已取消导出并删除 checkpoint/buffer / 待全量标记");
  return { ok: true, cancelled: true };
}

function qGalleryExportForceRestart() {
  return qGalleryExportTick_({ showUi: true, publishAfter: false, forceRestart: true });
}

/** 立刻全量重导 + 推 CDN（推荐手动点这个） */
function qGalleryExportForceRestartAndPublish() {
  return qGalleryExportTick_({ showUi: true, publishAfter: true, forceRestart: true });
}

/** 详细进度（日志 + 弹窗） */
function qGalleryExportStatus() {
  const folder = DriveApp.getFolderById(Q_GALLERY_DRIVE_EXPORT.folderId);
  const state = loadQGalleryCheckpoint_(folder);
  const lastOk = PropertiesService.getScriptProperties().getProperty(Q_GALLERY_PROP.LAST_SUCCESS) || "";
  if (!state) {
    const msg =
      "当前无进行中的导出。\n上次成功: " +
      (lastOk || "无") +
      "\n\n立刻更新: qGalleryExportForceRestartAndPublish\n自动流水线: installQGalleryAutoPipeline";
    Logger.log(msg);
    try {
      SpreadsheetApp.getUi().alert(msg);
    } catch (e) {}
    return { ok: true, running: false, lastSuccess: lastOk };
  }
  const text = formatExportProgressText_("状态查询", state, { result: "仅查询，未读写表格" });
  Logger.log(text);
  try {
    SpreadsheetApp.getUi().alert(text);
  } catch (e) {}
  return { ok: true, running: state.status === "running", text: text };
}

/** 兼容旧函数名 */
function qGalleryInstallResumeWatchdog() {
  return installQGalleryAutoPipeline();
}

// ---------------------------------------------------------------------------
// 主循环（可续跑）
// ---------------------------------------------------------------------------

/**
 * @param {{showUi?:boolean, publishAfter?:boolean|null, forceRestart?:boolean, isResume?:boolean}} opt
 * publishAfter=null 时沿用 checkpoint 内的设置
 */
function qGalleryExportTick_(opt) {
  opt = opt || {};
  const lock = LockService.getScriptLock();
  // 防止 everyMinutes 与 after 重叠双开
  if (!lock.tryLock(15000)) {
    Logger.log("另一轮导出仍在运行，本轮跳过（正常）");
    return { ok: true, skipped: true, reason: "locked" };
  }

  try {
    return qGalleryExportTickLocked_(opt);
  } finally {
    try {
      lock.releaseLock();
    } catch (e) {}
  }
}

function qGalleryExportTickLocked_(opt) {
  opt = opt || {};
  const startedAt = Date.now();
  const maxMs = Number(Q_GALLERY_DRIVE_EXPORT.maxRuntimeMs || 270000);
  const folder = DriveApp.getFolderById(Q_GALLERY_DRIVE_EXPORT.folderId);
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = spreadsheet.getSheetByName(Q_GALLERY_DRIVE_EXPORT.dataSheetName);
  if (!sheet) throw new Error("Missing sheet: " + Q_GALLERY_DRIVE_EXPORT.dataSheetName);

  // 仅强制重开时清触发器；正常续跑保留 everyMinutes 看门狗
  if (opt.forceRestart) {
    clearQGalleryExportContinueTriggers_();
  }

  let state = null;
  if (opt.forceRestart) {
    trashQGalleryNamedFile_(folder, Q_GALLERY_DRIVE_EXPORT.checkpointFileName);
    trashQGalleryNamedFile_(folder, Q_GALLERY_DRIVE_EXPORT.bufferFileName);
    Logger.log("forceRestart：已清除旧 checkpoint/buffer");
  } else {
    state = loadQGalleryCheckpoint_(folder);
    if (state && state.status === "running") {
      const age = Date.now() - Number(state.touchedAt || state.startedAt || 0);
      if (age > Number(Q_GALLERY_DRIVE_EXPORT.checkpointMaxAgeMs || 172800000)) {
        Logger.log("checkpoint 过期（" + Math.round(age / 3600000) + "h），重新开始");
        state = null;
      }
    } else if (state && state.status === "done") {
      state = null;
    } else if (state && state.status === "error") {
      Logger.log("发现 error 状态 checkpoint，将尝试续跑: " + (state.error || ""));
    }
  }

  if (!state) {
    if (opt.isResume) {
      Logger.log("无有效 checkpoint，续跑结束（清除看门狗触发器）");
      clearQGalleryExportContinueTriggers_();
      return { ok: true, done: true, message: "nothing to resume" };
    }
    state = createQGalleryExportState_(sheet, opt);
    state.bufferAssets = [];
    saveQGalleryBuffer_(folder, []);
    saveQGalleryCheckpoint_(folder, state);
    logExportProgress_("开始新导出", state, {
      action: "新建 build，准备从第 " + state.nextRow + " 行读取",
      result: "进行中"
    });
    // 确保自动 worker 在跑（有则跳过）
    ensureQGalleryAutoWorkerTrigger_();
  } else {
    if (typeof opt.publishAfter === "boolean") state.publishAfter = opt.publishAfter;
    if (opt.showUi) state.showUi = true;
    state.bufferAssets = loadQGalleryBuffer_(folder);
    state.bufferCount = state.bufferAssets.length;
    logExportProgress_("续跑本轮开始", state, {
      action: "从 checkpoint 恢复，继续处理",
      result: "进行中"
    });
  }

  try {
    let batchesThisRun = 0;
    let assetsThisRun = 0;
    let chunksThisRun = 0;
    if (state.consecutiveEmptyBatches == null) state.consecutiveEmptyBatches = 0;

    // 续跑时若 sheetLastRow 仍是虚高旧值，可再收一次真实末行
    if (Q_GALLERY_DRIVE_EXPORT.useSmartLastRow !== false) {
      try {
        const smartEnd = findQGalleryDataLastRow_(sheet);
        if (smartEnd >= Number(Q_GALLERY_DRIVE_EXPORT.startRow || 3) && smartEnd < state.sheetLastRow) {
          Logger.log(
            "收紧扫描末行: " + state.sheetLastRow + " → " + smartEnd + "（跳过空白尾部，加速）"
          );
          state.sheetLastRow = smartEnd;
          state.sourceLastRow = Math.max(Number(state.sourceLastRow || 0), smartEnd);
        }
      } catch (e) {
        Logger.log("智能末行探测跳过: " + (e.message || e));
      }
    }

    while (state.nextRow <= state.sheetLastRow) {
      if (Date.now() - startedAt > maxMs) {
        persistQGalleryProgress_(folder, state);
        scheduleQGalleryExportContinue_();
        ensureQGalleryAutoWorkerTrigger_();
        const elapsedSec = Math.round((Date.now() - startedAt) / 1000);
        logExportProgress_("本轮到时限，已保存并等待续跑", state, {
          action:
            "本轮处理 " +
            batchesThisRun +
            " 批，新增有效 " +
            assetsThisRun +
            " 条，新写分片 " +
            chunksThisRun +
            " 个",
          elapsedSec: elapsedSec,
          result: "未完成 → 将由 qGalleryAutoWorker（每分钟）自动续跑"
        });
        if (state.showUi) {
          try {
            SpreadsheetApp.getUi().alert(
              formatExportProgressText_("本轮暂停（会自动续跑）", state, {
                result: "请到左侧「执行」看后续 qGalleryAutoWorker 日志"
              })
            );
          } catch (e) {}
        }
        return {
          ok: true,
          done: false,
          continued: true,
          buildId: state.buildId,
          nextRow: state.nextRow,
          sheetLastRow: state.sheetLastRow,
          remainingRows: Math.max(0, state.sheetLastRow - state.nextRow + 1),
          chunkCount: state.completedChunks.length,
          exportedRows: state.exportedRows,
          maxDate: state.maxDate,
          pct: exportPct_(state)
        };
      }

      // 连续空批 → 加大批大小，快速扫空白
      const emptyStreak = Number(state.consecutiveEmptyBatches || 0);
      const baseBatch =
        emptyStreak >= 2
          ? Number(Q_GALLERY_DRIVE_EXPORT.readBatchRowsEmpty || 800)
          : Number(Q_GALLERY_DRIVE_EXPORT.readBatchRows || 250);
      const batchFrom = state.nextRow;
      const batchSize = Math.min(baseBatch, state.sheetLastRow - state.nextRow + 1);
      const batchTo = batchFrom + batchSize - 1;
      const batchAssets = readQGalleryAssetRowsBatch_(
        sheet,
        state.nextRow,
        batchSize,
        state.lastColumn,
        state.avatarAccessToken
      );
      batchesThisRun += 1;
      assetsThisRun += batchAssets.length;

      if (batchAssets.length === 0) {
        state.consecutiveEmptyBatches = emptyStreak + 1;
      } else {
        state.consecutiveEmptyBatches = 0;
      }

      let chunkFlushed = false;
      for (let i = 0; i < batchAssets.length; i += 1) {
        const asset = batchAssets[i];
        state.bufferAssets.push(asset);
        state.exportedRows += 1;
        if (!state.firstRowNumber) state.firstRowNumber = asset.rowNumber || "";
        state.lastRowNumber = asset.rowNumber || state.lastRowNumber;
        if (asset.nameImage) state.nameImageHit += 1;
        if (asset.avatarUrl) state.avatarHit += 1;
        const date = normalizeQGalleryDate_(asset.date);
        if (date && (!state.maxDate || date > state.maxDate)) state.maxDate = date;
        const waterTime = normalizeQGalleryDate_(asset.waterTime);
        if (waterTime && (!state.maxWaterTime || waterTime > state.maxWaterTime)) {
          state.maxWaterTime = waterTime;
        }

        if (state.bufferAssets.length >= Number(Q_GALLERY_DRIVE_EXPORT.rowsPerChunk || 1200)) {
          const before = state.nextChunkIndex;
          flushQGalleryChunkFromBuffer_(folder, state);
          chunksThisRun += 1;
          chunkFlushed = true;
          logExportProgress_("写入分片 #" + before, state, {
            action: "buffer 已满，写出 JSON 分片到 Drive",
            chunkIndex: before,
            result: "分片写入成功"
          });
        }
      }

      state.nextRow += batchSize;
      state.touchedAt = Date.now();
      state.bufferCount = state.bufferAssets.length;
      persistQGalleryProgress_(folder, state);

      Logger.log(
        [
          "[批次] 读表 " + batchFrom + "～" + batchTo + " 行",
          "本批有效=" + batchAssets.length,
          "累计有效=" + state.exportedRows,
          "进度 " + exportPct_(state) + "%",
          "剩余行≈" + Math.max(0, state.sheetLastRow - state.nextRow + 1),
          "分片=" + state.completedChunks.length,
          "buffer=" + state.bufferAssets.length,
          "maxDate=" + (state.maxDate || "-"),
          emptyStreak >= 2 ? "空区快扫" : "",
          chunkFlushed ? "写分片" : ""
        ]
          .filter(Boolean)
          .join(" | ")
      );

      // 已有有效数据后，连续多批全空 → 判定后面是空白，提前结束（可省数万行）
      const stopAfter = Number(Q_GALLERY_DRIVE_EXPORT.emptyBatchesToStop || 8);
      if (
        state.exportedRows > 0 &&
        state.consecutiveEmptyBatches >= stopAfter &&
        state.nextRow > Number(state.lastRowNumber || 0) + 50
      ) {
        Logger.log(
          "连续 " +
            state.consecutiveEmptyBatches +
            " 批无有效数据，提前结束扫描（末有效行≈" +
            (state.lastRowNumber || "-") +
            "，当前行=" +
            state.nextRow +
            "）"
        );
        state.sheetLastRow = state.nextRow - 1;
        break;
      }
    }

    // ---- 表已读完 ----
    if (state.bufferAssets.length) {
      const before = state.nextChunkIndex;
      flushQGalleryChunkFromBuffer_(folder, state);
      logExportProgress_("写入最后分片 #" + before, state, {
        action: "表格读完，刷出 buffer 残留",
        chunkIndex: before,
        result: "OK"
      });
    }

    if (!state.completedChunks.length && state.exportedRows === 0) {
      Logger.log("警告：导出 0 条有效数据，仍写 manifest");
    }

    logExportProgress_("表格扫描完成，开始写 manifest", state, {
      action: "汇总分片并更新 q-gallery-cache-manifest.json",
      result: "收尾中"
    });

    const result = finalizeQGalleryExport_(folder, state);
    state.status = "done";
    state.touchedAt = Date.now();
    state.bufferCount = 0;
    saveQGalleryCheckpoint_(folder, state);

    let published = null;
    if (state.publishAfter) {
      Logger.log("开始推送 CDN /api/publish-cache …");
      published = publishQGalleryManifestToCdn_(result.manifestUrl, state.avatarAccessToken);
      logExportProgress_("CDN 推送结束", state, {
        action: "publish-cache",
        result: published && published.ok
          ? "成功 ok buildId=" + (published.buildId || state.buildId)
          : "失败: " + ((published && published.error) || "unknown")
      });
      if (published && published.publicManifestUrl) {
        Logger.log("CDN manifest: " + published.publicManifestUrl);
      }
    } else {
      Logger.log("未推 CDN（publishAfter=false）。网站不会变新。");
    }

    trashQGalleryNamedFile_(folder, Q_GALLERY_DRIVE_EXPORT.checkpointFileName);
    trashQGalleryNamedFile_(folder, Q_GALLERY_DRIVE_EXPORT.bufferFileName);
    clearQGalleryExportContinueTriggers_();

    const successRec = {
      at: new Date().toISOString(),
      buildId: state.buildId,
      totalRows: result.totalRows,
      chunkCount: result.chunkCount,
      maxDate: result.maxDate || "",
      maxWaterTime: result.maxWaterTime || "",
      sourceLastRow: result.sourceLastRow,
      published: Boolean(published && published.ok),
      manifestUrl: result.manifestUrl
    };
    try {
      PropertiesService.getScriptProperties().setProperty(
        Q_GALLERY_PROP.LAST_SUCCESS,
        JSON.stringify(successRec)
      );
    } catch (e) {}

    logExportProgress_("全部完成", state, {
      action: "导出结束",
      result:
        "总条数=" +
        result.totalRows +
        " 分片=" +
        result.chunkCount +
        " maxDate=" +
        (result.maxDate || "-") +
        " 推CDN=" +
        (published ? (published.ok ? "成功" : "失败") : "跳过"),
      elapsedSec: Math.round((Date.now() - startedAt) / 1000)
    });
    Logger.log("Manifest URL: " + result.manifestUrl);
    Logger.log(
      "网站检查: https://gallery-cache.zhixianglife.com/promo/manifest.json （看 buildId / maxDate / totalRows）"
    );

    if (state.showUi || opt.showUi) {
      try {
        SpreadsheetApp.getUi().alert(
          formatExportProgressText_("导出完成", state, {
            result:
              "总条数 " +
              result.totalRows +
              "\nmaxDate " +
              (result.maxDate || "-") +
              "\nCDN " +
              (published ? (published.ok ? "成功" : "失败 " + (published.error || "")) : "未推送")
          })
        );
      } catch (e) {}
    }

    return {
      ok: true,
      done: true,
      buildId: state.buildId,
      totalRows: result.totalRows,
      chunkCount: result.chunkCount,
      maxDate: result.maxDate,
      manifestUrl: result.manifestUrl,
      manifestFileId: result.manifestFileId,
      published: published
    };
  } catch (error) {
    state.status = "error";
    state.error = String(error && error.message ? error.message : error);
    state.touchedAt = Date.now();
    try {
      persistQGalleryProgress_(folder, state);
    } catch (e2) {
      Logger.log("保存 checkpoint 也失败: " + (e2.message || e2));
    }
    try {
      scheduleQGalleryExportContinue_();
      ensureQGalleryAutoWorkerTrigger_();
    } catch (e3) {}
    logExportProgress_("导出异常", state, {
      action: "已保存进度，将自动续跑",
      result: "错误: " + state.error
    });
    throw error;
  }
}

/** 进度百分比 0~100，一位小数 */
function exportPct_(state) {
  const last = Number(state.sheetLastRow || 0);
  const next = Number(state.nextRow || 0);
  if (last <= 0) return "0.0";
  const pct = Math.min(100, ((next - 1) / last) * 100);
  return (Math.round(pct * 10) / 10).toFixed(1);
}

function formatExportProgressText_(tag, state, extra) {
  extra = extra || {};
  const last = Number(state.sheetLastRow || 0);
  const next = Number(state.nextRow || 0);
  const remain = Math.max(0, last - next + 1);
  const lines = [
    "========== " + tag + " ==========",
    "时间: " + new Date().toLocaleString(),
    "buildId: " + (state.buildId || "-"),
    "任务状态: " + (state.status || "-"),
    "—— 表格扫描 ——",
    "当前将处理行号: " + next,
    "表格总行数: " + last,
    "剩余约行数: " + remain,
    "扫描进度: " + exportPct_(state) + "%",
    "—— 导出结果（累计）——",
    "有效条数 exportedRows: " + (state.exportedRows || 0),
    "已写分片 chunks: " + ((state.completedChunks && state.completedChunks.length) || 0),
    "当前 buffer 条数: " +
      (Array.isArray(state.bufferAssets) ? state.bufferAssets.length : Number(state.bufferCount || 0)),
    "数据最大日期 maxDate: " + (state.maxDate || "-"),
    "最大水滴时间 maxWaterTime: " + (state.maxWaterTime || "-"),
    "首条 rowNumber: " + (state.firstRowNumber || "-"),
    "末条 rowNumber: " + (state.lastRowNumber || "-"),
    "完成后推 CDN: " + (state.publishAfter ? "是" : "否"),
    "W列图命中: " + (state.nameImageHit || 0) + "  头像命中: " + (state.avatarHit || 0)
  ];
  if (extra.action) lines.push("本轮动作: " + extra.action);
  if (extra.batchFrom != null) {
    lines.push("本批读取行: " + extra.batchFrom + " ~ " + extra.batchTo);
  }
  if (extra.batchHit != null) lines.push("本批有效行: " + extra.batchHit);
  if (extra.chunkIndex != null) lines.push("分片编号: #" + extra.chunkIndex);
  if (extra.elapsedSec != null) lines.push("本轮用时: " + extra.elapsedSec + " 秒");
  if (extra.result) lines.push("结果: " + extra.result);
  if (state.error) lines.push("最近错误: " + state.error);
  lines.push("====================================");
  return lines.join("\n");
}

function logExportProgress_(tag, state, extra) {
  Logger.log(formatExportProgressText_(tag, state, extra));
}

/** 若还没有每分钟 worker，则装上（不拆掉已有 daily） */
function ensureQGalleryAutoWorkerTrigger_() {
  let has = false;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "qGalleryAutoWorker") has = true;
  });
  if (has) return;
  try {
    ScriptApp.newTrigger("qGalleryAutoWorker").timeBased().everyMinutes(1).create();
    Logger.log("已自动补装每分钟触发器 qGalleryAutoWorker");
  } catch (e) {
    Logger.log("补装 qGalleryAutoWorker 失败: " + (e.message || e));
  }
}

function createQGalleryExportState_(sheet, opt) {
  const sheetLastRowRaw = sheet.getLastRow();
  let dataLastRow = sheetLastRowRaw;
  if (Q_GALLERY_DRIVE_EXPORT.useSmartLastRow !== false) {
    try {
      dataLastRow = findQGalleryDataLastRow_(sheet);
    } catch (e) {
      dataLastRow = sheetLastRowRaw;
    }
  }
  // 至少扫到 startRow；智能末行失败时回退 getLastRow
  if (dataLastRow < Number(Q_GALLERY_DRIVE_EXPORT.startRow || 3)) {
    dataLastRow = sheetLastRowRaw;
  }
  // 略留余量，防止末尾几行漏掉
  dataLastRow = Math.min(sheetLastRowRaw, dataLastRow + 20);

  Logger.log(
    "建任务：getLastRow=" +
      sheetLastRowRaw +
      " 智能数据末行≈" +
      dataLastRow +
      "（预计少扫 " +
      Math.max(0, sheetLastRowRaw - dataLastRow) +
      " 空行）"
  );

  const lastColumn = Math.max(sheet.getLastColumn(), 28);
  const avatarAccessToken = getQGalleryAvatarAccessToken_(sheet) || "";
  return {
    status: "running",
    buildId: Utilities.getUuid(),
    startedAt: Date.now(),
    touchedAt: Date.now(),
    updatedAt: new Date().toISOString(),
    nextRow: Number(Q_GALLERY_DRIVE_EXPORT.startRow || 3),
    sheetLastRow: dataLastRow,
    sheetLastRowRaw: sheetLastRowRaw,
    lastColumn: lastColumn,
    sourceLastRow: sheetLastRowRaw,
    avatarAccessToken: avatarAccessToken,
    bufferCount: 0,
    consecutiveEmptyBatches: 0,
    nextChunkIndex: 1,
    completedChunks: [],
    exportedRows: 0,
    nameImageHit: 0,
    avatarHit: 0,
    maxDate: "",
    maxWaterTime: "",
    firstRowNumber: "",
    lastRowNumber: "",
    publishAfter: Boolean(opt.publishAfter),
    showUi: Boolean(opt.showUi),
    error: ""
  };
}

/**
 * 从下往上在关键列找「最后一个非空单元格」所在行。
 * 避免 getLastRow() 因格式/公式把空白算进去导致扫 3 万空行。
 */
function findQGalleryDataLastRow_(sheet) {
  const start = Number(Q_GALLERY_DRIVE_EXPORT.startRow || 3);
  const maxRow = sheet.getLastRow();
  if (maxRow < start) return start - 1;

  // A 名字, C 链接, H 日期, J 点赞, R 作者 — 任一有值即算数据行
  const probeCols = [1, 3, 8, 10, 18];
  const block = 500;

  for (let end = maxRow; end >= start; end -= block) {
    const from = Math.max(start, end - block + 1);
    const num = end - from + 1;
    let best = 0;
    for (let c = 0; c < probeCols.length; c += 1) {
      const vals = sheet.getRange(from, probeCols[c], num, 1).getDisplayValues();
      for (let i = vals.length - 1; i >= 0; i -= 1) {
        const t = textQGallery_(vals[i][0]);
        if (t && t !== "#N/A" && t !== "未找到") {
          const row = from + i;
          if (row > best) best = row;
          break;
        }
      }
    }
    if (best) return best;
  }
  return start - 1;
}

function flushQGalleryChunkFromBuffer_(folder, state) {
  const assets = state.bufferAssets || [];
  if (!assets.length) return;

  const index = state.nextChunkIndex;
  const chunkPayload = {
    ok: true,
    buildId: state.buildId,
    index: index,
    count: 0,
    totalRows: 0,
    updatedAt: state.updatedAt,
    sourceLastRow: state.sourceLastRow,
    exportedRows: 0,
    maxDate: state.maxDate,
    maxWaterTime: state.maxWaterTime,
    assets: assets
  };

  const fileName = [
    Q_GALLERY_DRIVE_EXPORT.chunkPrefix,
    state.buildId,
    String(index).padStart(4, "0") + ".json"
  ].join("_");

  const file = folder.createFile(fileName, JSON.stringify(chunkPayload), "application/json");
  safelyShareQGalleryFile_(file);

  state.completedChunks.push({
    index: index,
    fileId: file.getId(),
    name: file.getName(),
    url: driveDownloadUrlForQGallery_(file.getId()),
    size: file.getSize()
  });

  Logger.log(
    "分片已写 #" +
      index +
      " rows=" +
      assets.length +
      " file=" +
      fileName +
      " size=" +
      file.getSize() +
      " maxDate=" +
      (state.maxDate || "-")
  );

  state.nextChunkIndex = index + 1;
  state.bufferAssets = [];
  state.bufferCount = 0;
  state.touchedAt = Date.now();
  // 分片写出后立刻清 buffer 文件
  saveQGalleryBuffer_(folder, []);
}

function finalizeQGalleryExport_(folder, state) {
  const chunks = (state.completedChunks || []).slice().sort(function (a, b) {
    return Number(a.index || 0) - Number(b.index || 0);
  });
  const totalRows = Number(state.exportedRows || 0);
  const totalChunks = chunks.length;
  // 不再回读/重写各分片 JSON（大文件极易在收尾阶段超时）；前端以 manifest.chunks 为准

  const manifest = {
    ok: true,
    source: "q-gallery-drive-manifest",
    buildId: state.buildId,
    updatedAt: new Date().toISOString(),
    totalRows: totalRows,
    sourceLastRow: state.sourceLastRow,
    exportedRows: totalRows,
    maxDate: state.maxDate,
    maxWaterTime: state.maxWaterTime,
    firstRowNumber: state.firstRowNumber,
    lastRowNumber: state.lastRowNumber,
    chunkCount: totalChunks,
    chunks: chunks
  };

  const manifestFile = upsertQGalleryDriveJsonFile_(
    folder,
    Q_GALLERY_DRIVE_EXPORT.manifestFileName,
    JSON.stringify(manifest)
  );
  safelyShareQGalleryFile_(manifestFile);
  cleanupOldQGalleryChunkFiles_(folder, state.buildId);

  return {
    ok: true,
    manifestFileId: manifestFile.getId(),
    manifestUrl: driveDownloadUrlForQGallery_(manifestFile.getId()),
    totalRows: totalRows,
    chunkCount: totalChunks,
    sourceLastRow: state.sourceLastRow,
    maxDate: state.maxDate,
    maxWaterTime: state.maxWaterTime
  };
}

function publishQGalleryManifestToCdn_(manifestUrl, fbAccessToken) {
  const props = PropertiesService.getScriptProperties();
  const publishUrl =
    props.getProperty("PROMO_PUBLISH_URL") ||
    Q_GALLERY_DRIVE_EXPORT.promoPublishUrl ||
    "https://promo.zhixianglife.com/api/publish-cache";
  const secret =
    props.getProperty("PROMO_PUBLISH_SECRET") ||
    Q_GALLERY_DRIVE_EXPORT.promoPublishSecret ||
    "";

  if (!secret) {
    Logger.log("已导出但未推 CDN：请设置 PROMO_PUBLISH_SECRET");
    return { ok: false, error: "missing PROMO_PUBLISH_SECRET" };
  }

  const payload = { manifestUrl: manifestUrl };
  if (fbAccessToken) payload.fbAccessToken = fbAccessToken;

  const response = UrlFetchApp.fetch(publishUrl, {
    method: "post",
    contentType: "application/json",
    headers: { "x-publish-secret": secret },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
    followRedirects: true
  });
  const code = response.getResponseCode();
  const body = response.getContentText();
  let published;
  try {
    published = JSON.parse(body);
  } catch (e) {
    published = { ok: false, error: body.slice(0, 300), http: code };
  }
  Logger.log("publish-cache: " + JSON.stringify(published));
  return published;
}

// ---------------------------------------------------------------------------
// Checkpoint / 续跑触发器
// ---------------------------------------------------------------------------

function loadQGalleryCheckpoint_(folder) {
  const files = folder.getFilesByName(Q_GALLERY_DRIVE_EXPORT.checkpointFileName);
  if (!files.hasNext()) return null;
  try {
    const text = files.next().getBlob().getDataAsString();
    const state = JSON.parse(text);
    if (!state || !state.buildId) return null;
    if (!Array.isArray(state.completedChunks)) state.completedChunks = [];
    // buffer 在独立文件
    state.bufferAssets = [];
    state.bufferCount = Number(state.bufferCount || 0);
    return state;
  } catch (e) {
    Logger.log("读取 checkpoint 失败: " + (e.message || e));
    return null;
  }
}

/** checkpoint 不含 bufferAssets，体积小、写入快 */
function saveQGalleryCheckpoint_(folder, state) {
  state.touchedAt = Date.now();
  state.bufferCount = Array.isArray(state.bufferAssets)
    ? state.bufferAssets.length
    : Number(state.bufferCount || 0);
  const slim = {};
  const keys = Object.keys(state);
  for (let i = 0; i < keys.length; i += 1) {
    const k = keys[i];
    if (k === "bufferAssets") continue;
    slim[k] = state[k];
  }
  upsertQGalleryDriveJsonFile_(
    folder,
    Q_GALLERY_DRIVE_EXPORT.checkpointFileName,
    JSON.stringify(slim)
  );
}

function loadQGalleryBuffer_(folder) {
  const name = Q_GALLERY_DRIVE_EXPORT.bufferFileName || "q-gallery-export-buffer.json";
  const files = folder.getFilesByName(name);
  if (!files.hasNext()) return [];
  try {
    const data = JSON.parse(files.next().getBlob().getDataAsString());
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.assets)) return data.assets;
    return [];
  } catch (e) {
    Logger.log("读取 buffer 失败: " + (e.message || e));
    return [];
  }
}

function saveQGalleryBuffer_(folder, assets) {
  const name = Q_GALLERY_DRIVE_EXPORT.bufferFileName || "q-gallery-export-buffer.json";
  const list = Array.isArray(assets) ? assets : [];
  upsertQGalleryDriveJsonFile_(
    folder,
    name,
    JSON.stringify({ ok: true, count: list.length, assets: list })
  );
}

function persistQGalleryProgress_(folder, state) {
  saveQGalleryBuffer_(folder, state.bufferAssets || []);
  saveQGalleryCheckpoint_(folder, state);
}

/**
 * 续跑调度（比单纯 after() 更稳）：
 * 1) 立刻挂 after(30s) 一次性触发
 * 2) 再挂 after(90s) 备份（防止第一条被吞）
 * 3) 确保存在 everyMinutes(1) 轮询触发（导出完成时会清掉）
 *
 * 注意：执行日志是「每次运行各自一条」。请到左侧「执行」列表看
 * 最新的 qGalleryExportResume，不要只盯着上一轮日志页面。
 */
function scheduleQGalleryExportContinue_() {
  const after = Math.max(20000, Number(Q_GALLERY_DRIVE_EXPORT.continueAfterMs || 30000));

  // 先清掉一次性 after 触发，保留/重建分钟轮询
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() !== "qGalleryExportResume") return;
    try {
      // 删掉所有，下面统一重建，避免重复狂跑
      ScriptApp.deleteTrigger(trigger);
    } catch (e) {}
  });

  try {
    ScriptApp.newTrigger("qGalleryExportResume").timeBased().after(after).create();
  } catch (e) {
    Logger.log("创建 after 触发失败: " + (e.message || e));
  }
  try {
    ScriptApp.newTrigger("qGalleryExportResume").timeBased().after(after + 60000).create();
  } catch (e) {
    Logger.log("创建 after+60s 备份触发失败: " + (e.message || e));
  }
  try {
    // 每分钟兜底，直到导出完成 clear
    ScriptApp.newTrigger("qGalleryExportResume").timeBased().everyMinutes(1).create();
  } catch (e) {
    Logger.log("创建 everyMinutes 触发失败: " + (e.message || e));
  }

  Logger.log(
    "已安排续跑：after " +
      after +
      "ms + after " +
      (after + 60000) +
      "ms + everyMinutes(1)。请到「执行」列表查看新日志，不要只看本页。"
  );
}

function clearQGalleryExportContinueTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "qGalleryExportResume") {
      try {
        ScriptApp.deleteTrigger(trigger);
      } catch (e) {}
    }
  });
}

function trashQGalleryNamedFile_(folder, name) {
  const files = folder.getFilesByName(name);
  while (files.hasNext()) {
    try {
      files.next().setTrashed(true);
    } catch (e) {}
  }
}

// ---------------------------------------------------------------------------
// 分批读表
// ---------------------------------------------------------------------------

/**
 * 读取 [startRow, startRow+numRows) → assets
 * 加速：整表 display 一次；公式/Value 只读 C/E/T/W（不再整表 getFormulas）
 */
function readQGalleryAssetRowsBatch_(sheet, startRow, numRows, lastColumn, avatarAccessToken) {
  if (numRows <= 0) return [];
  const cols = Math.max(Number(lastColumn) || 28, 28);

  // 1) 显示值（主数据）
  const displayValues = sheet.getRange(startRow, 1, numRows, cols).getDisplayValues();

  // 2) 仅关键列公式（IMAGE/HYPERLINK）— 比 28 列全量公式快很多
  // C=3 链接, E=5 缩略图, T=20 头像, W=23 名图
  const fC = sheet.getRange(startRow, 3, numRows, 1).getFormulas();
  const fE = sheet.getRange(startRow, 5, numRows, 1).getFormulas();
  const fT = sheet.getRange(startRow, 20, numRows, 1).getFormulas();
  const fW = sheet.getRange(startRow, 23, numRows, 1).getFormulas();
  const vE = sheet.getRange(startRow, 5, numRows, 1).getValues();
  const vT = sheet.getRange(startRow, 20, numRows, 1).getValues();
  const vW = sheet.getRange(startRow, 23, numRows, 1).getValues();
  const dW = sheet.getRange(startRow, 23, numRows, 1).getDisplayValues();

  const assets = [];
  for (let offset = 0; offset < displayValues.length; offset += 1) {
    const row = displayValues[offset] || [];
    // 快速跳过整行空白（连名字/链接/日期都没有）
    if (
      !textQGallery_(row[0]) &&
      !textQGallery_(row[1]) &&
      !textQGallery_(row[2]) &&
      !textQGallery_(row[7]) &&
      !textQGallery_(row[9])
    ) {
      continue;
    }

    const formulaC = textQGallery_(fC[offset] && fC[offset][0]);
    const formulaE = textQGallery_(fE[offset] && fE[offset][0]);
    const formulaT = textQGallery_(fT[offset] && fT[offset][0]);
    const formulaW = textQGallery_(fW[offset] && fW[offset][0]);
    const valueE = vE[offset] && vE[offset][0];
    const valueT = vT[offset] && vT[offset][0];
    const valueW = vW[offset] && vW[offset][0];
    const displayW = textQGallery_(dW[offset] && dW[offset][0]);

    const display = function (index) {
      return sanitizeQGalleryDisplay_(row[index]);
    };
    const rowNumber = startRow + offset;
    // 名字 = A 列原样（不要用渠道/小组名顶替）
    const nameRaw = display(0);
    const designerRaw = display(27);
    const name = nameRaw || "";
    const postId = display(1);
    const postLink =
      extractQGalleryUrl_(formulaC) || extractQGalleryUrl_(display(2)) || display(2);
    const postType = display(12);
    const category = display(8);

    const eDisplay = display(4);
    const imageFromE = extractNameImageFromW_(formulaE, eDisplay, valueE, formulaE, eDisplay, valueE);
    const thumbnailUrl = imageFromE || extractQGalleryUrl_(formulaE || eDisplay) || "";

    let nameImage = extractNameImageFromW_(
      formulaW,
      displayW,
      valueW,
      formulaW,
      display(22),
      valueW
    );
    if (!nameImage && /=\s*IMAGE\s*\(\s*\$?[A-Z]{1,3}\$?\d+/i.test(formulaW || "")) {
      nameImage = thumbnailUrl;
    }
    if (!nameImage) nameImage = thumbnailUrl;

    const fallbackThumbnailUrl = nameImage || "";
    const authorId = textQGallery_(display(17)).replace(/[^\d]/g, "");
    const avatarUrl = extractQGalleryAvatarUrl_(
      formulaT,
      display(19),
      valueT,
      authorId,
      avatarAccessToken
    );

    const thumbnailFileId =
      extractQGalleryDriveId_(thumbnailUrl) ||
      extractQGalleryDriveId_(fallbackThumbnailUrl) ||
      extractQGalleryDriveId_(nameImage);
    const postFileId = extractQGalleryDriveId_(postLink);
    const fileId =
      thumbnailFileId ||
      postFileId ||
      extractQGalleryDriveId_(formulaC + " " + formulaE);
    const previewUrl =
      thumbnailUrl ||
      fallbackThumbnailUrl ||
      (fileId ? "https://drive.google.com/uc?export=view&id=" + encodeURIComponent(fileId) : "");

    const asset = {
      id: postId || postLink || fileId || "row-" + rowNumber,
      rowNumber: rowNumber,
      title: name || postType || "素材 " + rowNumber,
      name: name,
      postId: postId,
      postLink: postLink,
      lead: display(3),
      water: display(25),
      waterTime: display(26),
      sourceUrl: postLink,
      fileId: fileId,
      previewUrl: previewUrl,
      thumbnailUrl: previewUrl,
      thumbnailFallbackUrl: fallbackThumbnailUrl,
      viewUrl: postFileId
        ? "https://drive.google.com/file/d/" + encodeURIComponent(postFileId) + "/view"
        : postLink,
      content: display(5),
      rewrite: display(6),
      date: display(7) || findQGalleryDate_(row),
      type: category || postType || "",
      category: category,
      likes: display(9),
      comments: display(10),
      shares: display(11),
      postType: postType,
      ocr: display(13),
      ocrTranslation: display(14),
      audioText: display(15),
      audioTranslation: display(16),
      maker: display(17) || "",
      authorName: display(17),
      group: display(18) || display(20) || "",
      sourceType: display(20),
      sourceChannel: display(21),
      conversionRate: display(23),
      mediaMode: isVideoQGallery_(postType, category) ? "video" : "image",
      pageName: display(24),
      avatarUrl: avatarUrl,
      designer: isPlaceholderQGalleryLabel_(designerRaw) ? "" : designerRaw,
      nameImage: nameImage,
      nameImageFormula: formulaW || ""
    };
    if (Q_GALLERY_DRIVE_EXPORT.includeRaw) {
      asset.raw = row;
    }

    if (isMeaningfulQGalleryAsset_(asset)) assets.push(asset);
  }
  return assets;
}

/** 兼容旧调用：整表读（大表易超时，仅调试用） */
function readQGalleryAssetRows_(sheet) {
  const lastRow = sheet.getLastRow();
  const lastColumn = Math.max(sheet.getLastColumn(), 28);
  if (lastRow < Q_GALLERY_DRIVE_EXPORT.startRow) return [];
  const token = getQGalleryAvatarAccessToken_(sheet);
  return readQGalleryAssetRowsBatch_(
    sheet,
    Q_GALLERY_DRIVE_EXPORT.startRow,
    lastRow - Q_GALLERY_DRIVE_EXPORT.startRow + 1,
    lastColumn,
    token
  );
}

// ---------------------------------------------------------------------------
// 头像 token / 图片 URL 提取
// ---------------------------------------------------------------------------

function getQGalleryAvatarAccessToken_(sheet) {
  try {
    const props = PropertiesService.getScriptProperties();
    const fromProp =
      props.getProperty("FB_ACCESS_TOKEN") ||
      props.getProperty("FACEBOOK_ACCESS_TOKEN") ||
      props.getProperty("FB_GRAPH_ACCESS_TOKEN") ||
      "";
    if (fromProp && String(fromProp).trim()) return String(fromProp).trim();
  } catch (e) {}

  if (!sheet) return "";

  try {
    const maxRows = Math.min(80, sheet.getMaxRows());
    const formulas = sheet.getRange(1, 20, maxRows, 1).getFormulas();
    for (let r = 0; r < formulas.length; r += 1) {
      const token = extractAccessTokenFromText_(formulas[r][0]);
      if (token) return token;
    }
  } catch (e) {}

  try {
    const rows = Math.min(40, sheet.getMaxRows());
    const cols = Math.min(30, sheet.getMaxColumns());
    const formulas = sheet.getRange(1, 1, rows, cols).getFormulas();
    for (let r = 0; r < formulas.length; r += 1) {
      for (let c = 0; c < formulas[r].length; c += 1) {
        const token = extractAccessTokenFromText_(formulas[r][c]);
        if (token) return token;
      }
    }
  } catch (e) {}

  return "";
}

function extractAccessTokenFromText_(value) {
  const text = textQGallery_(value);
  if (!text || text.indexOf("access_token") < 0) return "";
  const m = text.match(/access_token=([^"&\s,)'"<>]+)/i);
  if (!m) return "";
  let token = m[1].replace(/["')\]]+$/g, "").trim();
  token = token.replace(/["']$/g, "");
  if (token.length < 20) return "";
  return token;
}

function extractQGalleryAvatarUrl_(tFormula, tDisplay, tValue, authorId, sheetToken) {
  const cellUrl = cellImageContentUrl_(tValue);
  if (cellUrl) return normalizeImageDisplayUrl_(cellUrl) || cellUrl;

  let url = extractImageUrlFromText_(tFormula) || extractImageUrlFromText_(tDisplay) || "";
  if (url) {
    url = normalizeImageDisplayUrl_(url) || url;
    if (/graph\.facebook\.com/i.test(url) && /\/picture/i.test(url) && !/access_token=/i.test(url)) {
      const token = extractAccessTokenFromText_(tFormula) || sheetToken;
      if (token) {
        url += (url.indexOf("?") >= 0 ? "&" : "?") + "access_token=" + encodeURIComponent(token);
      }
    }
    return url;
  }

  const id = textQGallery_(authorId).replace(/[^\d]/g, "");
  if (!/^\d{8,}$/.test(id)) return "";

  const token = extractAccessTokenFromText_(tFormula) || sheetToken || "";
  let graph =
    "https://graph.facebook.com/" +
    encodeURIComponent(id) +
    "/picture?width=120&height=120";
  if (token) {
    graph += "&access_token=" + encodeURIComponent(token);
  }
  return graph;
}

/**
 * 仅同步 Facebook 头像 token 到网站（无需重导全表）
 */
function qGallerySyncFbAvatarToken() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = spreadsheet.getSheetByName(Q_GALLERY_DRIVE_EXPORT.dataSheetName);
  if (!sheet) throw new Error("Missing sheet: " + Q_GALLERY_DRIVE_EXPORT.dataSheetName);

  const token = getQGalleryAvatarAccessToken_(sheet);
  if (!token) {
    throw new Error(
      "未在表格公式中找到 access_token。请打开 T 列任意带头像的单元格，确认公式含 access_token=...；或在脚本属性设置 FB_ACCESS_TOKEN"
    );
  }

  const props = PropertiesService.getScriptProperties();
  const publishUrl =
    props.getProperty("PROMO_PUBLISH_URL") ||
    Q_GALLERY_DRIVE_EXPORT.promoPublishUrl ||
    "https://promo.zhixianglife.com/api/publish-cache";
  const secret =
    props.getProperty("PROMO_PUBLISH_SECRET") ||
    Q_GALLERY_DRIVE_EXPORT.promoPublishSecret ||
    "";
  if (!secret) {
    throw new Error("请在脚本属性设置 PROMO_PUBLISH_SECRET（= Cloudflare CACHE_PUBLISH_SECRET）");
  }

  const response = UrlFetchApp.fetch(publishUrl, {
    method: "post",
    contentType: "application/json",
    headers: { "x-publish-secret": secret },
    payload: JSON.stringify({ action: "set-fb-token", fbAccessToken: token }),
    muteHttpExceptions: true,
    followRedirects: true
  });
  const code = response.getResponseCode();
  const body = response.getContentText();
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch (e) {
    parsed = { ok: false, error: body.slice(0, 300), http: code };
  }
  Logger.log("sync fb token: " + JSON.stringify(parsed));
  if (!parsed || !parsed.ok) {
    throw new Error((parsed && parsed.error) || "同步 token 失败 HTTP " + code);
  }
  return parsed;
}

function extractNameImageFromW_(wFormula, wDisplay, wValue, formula22, display22, value22) {
  const cellImageUrl = cellImageContentUrl_(wValue) || cellImageContentUrl_(value22);
  if (cellImageUrl) return normalizeImageDisplayUrl_(cellImageUrl);

  const texts = [wFormula, formula22, wDisplay, display22];
  for (let i = 0; i < texts.length; i += 1) {
    const url = extractImageUrlFromText_(texts[i]);
    if (url) return normalizeImageDisplayUrl_(url);
  }

  if (wValue != null && typeof wValue !== "object") {
    const url = extractImageUrlFromText_(String(wValue));
    if (url) return normalizeImageDisplayUrl_(url);
  }
  return "";
}

function cellImageContentUrl_(value) {
  if (value == null || typeof value !== "object") return "";
  try {
    if (typeof value.getContentUrl === "function") {
      const url = value.getContentUrl();
      if (url) return String(url).trim();
    }
    if (typeof value.getUrl === "function") {
      const url2 = value.getUrl();
      if (url2) return String(url2).trim();
    }
  } catch (e) {}
  return "";
}

function extractImageUrlFromText_(value) {
  const text = textQGallery_(value);
  if (!text || /^(#VALUE!|#N\/A|#REF!|#NAME\?|未找到)$/i.test(text)) return "";

  let m = text.match(/=\s*IMAGE\s*\(\s*"((?:[^"]|"")*)"\s*[,;)]/i);
  if (m) return m[1].replace(/""/g, '"').trim();
  m = text.match(/=\s*IMAGE\s*\(\s*'((?:[^']|'')*)'\s*[,;)]/i);
  if (m) return m[1].replace(/''/g, "'").trim();

  m = text.match(/=\s*HYPERLINK\s*\(\s*"((?:[^"]|"")*)"/i);
  if (m) return m[1].replace(/""/g, '"').trim();
  m = text.match(/=\s*HYPERLINK\s*\(\s*'((?:[^']|'')*)'/i);
  if (m) return m[1].replace(/''/g, "'").trim();

  m = text.match(/https?:\/\/[^\s"'<>)]+/i);
  if (m) return m[0].replace(/[),.;]+$/, "");

  if (/^[a-zA-Z0-9_-]{25,}$/.test(text)) return text;
  return "";
}

function normalizeImageDisplayUrl_(urlOrId) {
  const text = textQGallery_(urlOrId);
  if (!text) return "";
  if (/^https?:\/\//i.test(text)) {
    const id = extractQGalleryDriveId_(text);
    if (id && /drive\.google\.com/i.test(text)) {
      return "https://drive.google.com/uc?export=view&id=" + encodeURIComponent(id);
    }
    return text;
  }
  if (/^[a-zA-Z0-9_-]{25,}$/.test(text)) {
    return "https://drive.google.com/uc?export=view&id=" + encodeURIComponent(text);
  }
  return "";
}

function isMeaningfulQGalleryAsset_(asset) {
  return Boolean(
    textQGallery_(asset.name) ||
      textQGallery_(asset.postId) ||
      textQGallery_(asset.postLink) ||
      textQGallery_(asset.thumbnailUrl) ||
      textQGallery_(asset.thumbnailFallbackUrl) ||
      textQGallery_(asset.lead) ||
      textQGallery_(asset.water) ||
      textQGallery_(asset.waterTime) ||
      textQGallery_(asset.date) ||
      textQGallery_(asset.content) ||
      textQGallery_(asset.ocr) ||
      textQGallery_(asset.audioText)
  );
}

function normalizeQGalleryDate_(value) {
  const text = textQGallery_(value);
  const match = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!match) return "";
  return [
    match[1],
    String(match[2]).padStart(2, "0"),
    String(match[3]).padStart(2, "0")
  ].join("-");
}

function upsertQGalleryDriveJsonFile_(folder, name, content) {
  const files = folder.getFilesByName(name);
  if (files.hasNext()) {
    const file = files.next();
    file.setContent(content);
    return file;
  }
  return folder.createFile(name, content, "application/json");
}

function safelyShareQGalleryFile_(file) {
  if (!Q_GALLERY_DRIVE_EXPORT.shareFiles) return;
  try {
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  } catch (error) {
    Logger.log("Skip setSharing for " + file.getName() + ": " + (error.message || error));
  }
}

function cleanupOldQGalleryChunkFiles_(folder, currentBuildId) {
  if (!Q_GALLERY_DRIVE_EXPORT.cleanupOldChunks) return;
  const prefix = Q_GALLERY_DRIVE_EXPORT.chunkPrefix + "_";
  const keepPrefix = prefix + currentBuildId + "_";
  const files = folder.getFiles();
  let removed = 0;
  let scanned = 0;
  const limit = Number(Q_GALLERY_DRIVE_EXPORT.cleanupLimit || 0);
  while (files.hasNext()) {
    if (limit && scanned >= limit) break;
    const file = files.next();
    scanned += 1;
    const name = file.getName();
    if (name.indexOf(prefix) !== 0 || name.indexOf(".json") === -1) continue;
    if (name.indexOf(keepPrefix) === 0) continue;
    try {
      file.setTrashed(true);
      removed += 1;
    } catch (error) {
      Logger.log("Skip old chunk cleanup for " + name + ": " + (error.message || error));
    }
  }
  Logger.log("Old JSON chunks cleanup scanned: " + scanned + ", trashed: " + removed);
}

function driveDownloadUrlForQGallery_(fileId) {
  return "https://drive.google.com/uc?export=download&id=" + encodeURIComponent(fileId);
}

function extractQGalleryDriveId_(value) {
  const text = textQGallery_(value);
  const fileMatch = text.match(/drive\.google\.com\/file\/d\/([^/?#]+)/i);
  if (fileMatch) return fileMatch[1];
  const idMatch = text.match(/[?&]id=([^&#]+)/i);
  return idMatch ? decodeURIComponent(idMatch[1]) : "";
}

function extractQGalleryUrl_(value) {
  return extractImageUrlFromText_(value) || "";
}

function findQGalleryDate_(row) {
  for (let index = 0; index < row.length; index += 1) {
    const value = textQGallery_(row[index]);
    if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(value)) return value;
  }
  return "";
}

function isVideoQGallery_(postType, category) {
  const text = (textQGallery_(postType) + " " + textQGallery_(category)).toLowerCase();
  return /短视频|视频|video|reel|tiktok|douyin|sp-/.test(text);
}

function textQGallery_(value) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function sanitizeQGalleryDisplay_(value) {
  const text = textQGallery_(value);
  return /^=\s*[A-Z_]+\s*\(/i.test(text) ? "" : text;
}

/** VLOOKUP 失败占位（美工等字段用；名字 A 列原样保留「未找到」） */
function isPlaceholderQGalleryLabel_(value) {
  const text = textQGallery_(value);
  if (!text) return true;
  return /^(未找到|#N\/A|N\/A|#VALUE!|#REF!|#NAME\?|-|—|无|null|undefined)$/i.test(text);
}
