import { createDashboardApi, withTimeout } from "./api/api.js?v=20260609-api";
import { createDashboardEffects } from "./ui/effects.js?v=20260609-effects";
import { createCalendarUi } from "./ui/calendar.js?v=20260609-calendar";
import { getDashboardElements } from "./ui/elements.js?v=20260610-provider-probe";
import { createMediaUi } from "./ui/media.js?v=20260610-refactor";
import { createStatusView } from "./ui/status.js?v=20260610-today-after-dynamic";
import { createSettingsEnhancements } from "./ui/enhance.js?v=20260609-enhance";
import { createSettingsConfig } from "./ui/config.js?v=20260610-provider-probe";
import { createSweetControls } from "./ui/controls.js?v=20260609-controls";
import { createDashboardState } from "./ui/state.js?v=20260610-structure";
import { createTargetsUi } from "./ui/targets.js?v=20260609-targets";
import { createSakuraControls } from "./ui/sakura.js?v=20260610-structure";
import { createViewController } from "./ui/view.js?v=20260610-structure";
import { text } from "./ui/format.js?v=20260609-format";

const bridge = window.AstrBotPluginPage;
const BRIDGE_READY_TIMEOUT_MS = 5000;
const BRIDGE_REQUEST_TIMEOUT_MS = 20000;
const { apiGet, apiPost } = createDashboardApi(bridge, BRIDGE_REQUEST_TIMEOUT_MS);
const NOTICE_AUTO_HIDE_MS = 4200;
const STATUS_POLL_RUNNING_DELAY_MS = 1200;
const STATUS_POLL_IDLE_DELAY_MS = 20000;
const PAGE_VIEW_STORAGE_KEY = "daily_sharing_dashboard_view";
const SAKURA_STORAGE_KEY = "daily_sharing_dashboard_sakura";
const TARGET_CAROUSEL_INTERVAL_MS = 5200;

const state = createDashboardState();
const el = getDashboardElements();

const {
  closeSweetSelects,
  initSweetCombos,
  initSweetSelects,
  syncSweetCombo,
  syncSweetSelect,
  syncSweetSelects,
} = createSweetControls({
  selects: el.selects,
  combos: [
    { input: el.cfgLlmProviderId, list: el.cfgLlmProviderOptions },
    { input: el.cfgPersonaId, list: el.cfgPersonaOptions },
  ],
});

const {
  clearSakuraFall,
  hasSakuraFall,
  initDreamCursor,
  initSakuraFall,
  isMotionReduced: isSakuraMotionReduced,
} = createDashboardEffects({
  sakuraLayer: el.sakuraLayer,
  cursorTrailLayer: el.cursorTrailLayer,
});

const {
  applySakuraPreferences,
  initSakuraControls,
} = createSakuraControls({
  state,
  elements: el,
  bridge,
  apiPost,
  setNotice,
  storageKey: SAKURA_STORAGE_KEY,
  clearSakuraFall,
  hasSakuraFall,
  initSakuraFall,
  isMotionReduced: isSakuraMotionReduced,
});

const {
  applySettingsSchemaEnhancements,
  normalizeSettingsSliders,
  syncSettingSlider,
} = createSettingsEnhancements({
  configForm: el.configForm,
  settingsSections: el.settingsSections,
  elements: el,
});

const {
  initCalendarPanelLayout,
  renderCalendar,
  scheduleCalendarCarousel,
  scheduleCalendarPanelLayout,
  stopCalendarCarouselTimer,
} = createCalendarUi({
  state,
  elements: el,
  carouselIntervalMs: TARGET_CAROUSEL_INTERVAL_MS,
});

const {
  applyMediaPage,
  bindMediaEvents,
  clearImageMemoryCache,
  isDefaultMediaFilter,
  reloadMediaPage,
  renderMedia,
  renderMediaFilters,
  resetMediaPage,
  syncDefaultMediaFromStatus,
} = createMediaUi({
  state,
  elements: el,
  apiGet,
  apiPost,
  setNotice,
  syncSweetSelect,
});

const {
  bindTargetEvents,
  renderTargetCarousel,
  renderTargets,
  scheduleTargetCarousel,
  stopTargetCarouselTimer,
} = createTargetsUi({
  state,
  elements: el,
  carouselIntervalMs: TARGET_CAROUSEL_INTERVAL_MS,
  apiPost,
  syncSweetSelect,
  setTargetsDirty,
  setNotice,
  applyMediaPage,
  isDefaultMediaFilter,
  renderAll,
  scheduleCalendarPanelLayout,
});

function hideNotice() {
  window.clearTimeout(state.noticeTimer);
  state.noticeTimer = 0;
  el.notice.hidden = true;
  el.notice.textContent = "";
  el.notice.className = "notice";
}

function setNotice(message, tone = "info", autoHideMs = NOTICE_AUTO_HIDE_MS) {
  window.clearTimeout(state.noticeTimer);
  state.noticeTimer = 0;
  const body = text(message).trim();
  if (!body) {
    hideNotice();
    return;
  }

  const duration = Math.max(1200, Number(autoHideMs) || NOTICE_AUTO_HIDE_MS);
  el.notice.hidden = false;
  el.notice.textContent = body;
  el.notice.className = "notice";
  el.notice.style.setProperty("--notice-duration", `${duration}ms`);
  if (tone) el.notice.classList.add(tone);
  void el.notice.offsetWidth;
  el.notice.classList.add("is-visible");
  state.noticeTimer = window.setTimeout(hideNotice, duration);
}

const {
  bindProviderProbeEvents,
  handleConfigChanged,
  loadConfig,
  saveConfig,
  setSettingsTab,
  updateSettingsTabFromScroll,
} = createSettingsConfig({
  state,
  elements: el,
  bridge,
  apiGet,
  apiPost,
  setNotice,
  loadStatus,
  closeSweetSelects,
  syncSweetCombo,
  syncSweetSelect,
  syncSweetSelects,
  applySettingsSchemaEnhancements,
  normalizeSettingsSliders,
  syncSettingSlider,
});

const {
  fillNewsSources,
  isSpecificRunTarget,
  renderConfig,
  renderMetrics,
  renderRecentActions,
} = createStatusView({
  state,
  elements: el,
  syncSweetSelect,
});

const {
  applyActiveViewPreference,
  initPageSwitchControls,
  markActiveViewReady,
  restoreActiveView,
  writeStoredActiveView,
} = createViewController({
  state,
  elements: el,
  bridge,
  apiPost,
  storageKey: PAGE_VIEW_STORAGE_KEY,
  closeSweetSelects,
  stopTargetCarouselTimer,
  stopCalendarCarouselTimer,
  renderTargetCarousel,
  scheduleCalendarCarousel,
  scheduleCalendarPanelLayout,
  loadConfig,
  setSettingsTab,
  updateSettingsTabFromScroll,
});

function setTargetsDirty(value) {
  state.targetsDirty = value;
  el.saveTargetsButton.disabled = !value;
}

function renderAll() {
  fillNewsSources();
  renderMetrics();
  renderRecentActions();
  renderTargetCarousel();
  renderConfig();
  renderTargets();
  renderCalendar();
  renderMediaFilters();
  renderMedia();
  updateRunFormState();
  syncSweetSelects();
  scheduleCalendarPanelLayout();
}

function hasRunningAction() {
  return (state.status?.actions || []).some((item) => item.status === "running");
}

function hasRunningProgress() {
  return text(state.status?.progress?.status).trim().toLowerCase() === "running";
}

function shouldPollStatus() {
  return hasRunningAction() || hasRunningProgress() || state.watchedRuns.size > 0;
}

function canPollStatus() {
  return state.bridgeReady && document.visibilityState !== "hidden";
}

function watchRun(run, fallbackTarget = "broadcast") {
  const runId = text(run?.id).trim();
  if (!runId) return;
  state.watchedRuns.set(runId, {
    target: text(run?.target || fallbackTarget).trim(),
    startedAt: text(run?.started_at).trim(),
  });
}

function notifyFinishedRuns(actions = []) {
  if (!state.watchedRuns.size) return false;
  let notified = false;
  const byId = new Map(actions.map((item) => [text(item?.id).trim(), item]));
  for (const [runId, watched] of [...state.watchedRuns.entries()]) {
    const action = byId.get(runId);
    if (!action || action.status === "running") continue;
    state.watchedRuns.delete(runId);
    notified = true;
    if (action.status === "error") {
      setNotice(action.message || "分享失败", "error");
    } else {
      setNotice(
        action.message || (watched.target === "retry" ? "重试完成。" : "分享成功。"),
        "success",
      );
    }
  }
  return notified;
}

function schedulePoll() {
  window.clearTimeout(state.pollTimer);
  state.pollTimer = 0;
  if (!canPollStatus()) return;
  const delay = shouldPollStatus()
    ? STATUS_POLL_RUNNING_DELAY_MS
    : STATUS_POLL_IDLE_DELAY_MS;
  state.pollTimer = window.setTimeout(
    () => loadStatus({ quiet: true }),
    delay,
  );
}

function handleStatusVisibilityChange() {
  window.clearTimeout(state.pollTimer);
  state.pollTimer = 0;
  if (canPollStatus()) {
    loadStatus({ quiet: true });
  }
}

async function loadStatus({ quiet = false, reveal = true } = {}) {
  if (!bridge) {
    markActiveViewReady();
    setNotice("没有检测到 AstrBot Pages bridge，请从 AstrBot WebUI 插件页面进入。", "error");
    return;
  }
  try {
    const nextStatus = await apiGet("page/status", { _ts: Date.now() });
    if (state.targetsDirty && state.status?.targets) {
      nextStatus.targets = state.status.targets;
    }
    state.status = nextStatus;
    if (!state.activeViewSaving) applyActiveViewPreference(nextStatus.preferences, { ready: reveal });
    if (reveal) markActiveViewReady();
    if (!state.sakuraSaving) applySakuraPreferences(nextStatus.preferences);
    const showedRunResult = notifyFinishedRuns(nextStatus.actions || []);
    if (showedRunResult) {
      resetMediaPage();
    }
    const defaultMedia = isDefaultMediaFilter();
    const loadFilteredMedia = !defaultMedia && !state.mediaLoaded;
    if (defaultMedia) {
      syncDefaultMediaFromStatus(state.status);
    } else if (loadFilteredMedia) {
      state.media = [];
      state.mediaLoaded = true;
    }
    renderAll();
    if (loadFilteredMedia) {
      await reloadMediaPage({ quiet: true });
    }
    if (!quiet && !showedRunResult) setNotice("");
    schedulePoll();
  } catch (error) {
    if (reveal) markActiveViewReady();
    if (!quiet) {
      setNotice(error.message || "状态加载失败", "error");
    }
    schedulePoll();
  }
}

async function runShare(event) {
  event.preventDefault();
  el.runButton.disabled = true;
  const target = el.runTarget.value;
  try {
    const data = await apiPost("page/run", {
      target,
      share_type: el.shareType.value,
      news_source: el.newsSource.value,
      specific_target: isSpecificRunTarget(target) ? text(el.runSpecificTarget?.value).trim() : "",
    });
    watchRun(data.run, target);
    setNotice("任务已开始。", "success");
    await loadStatus({ quiet: true });
  } catch (error) {
    setNotice(error.message || "分享失败", "error");
  } finally {
    el.runButton.disabled = Boolean(state.status?.busy);
  }
}

function updateRunFormState() {
  const target = el.runTarget.value;
  const briefing = target === "briefing";
  const specificTarget = isSpecificRunTarget(target);
  const showSpecificTarget = target === "broadcast" || specificTarget;
  const specificTargetIsGroup = target !== "broadcast_users";
  el.shareType.disabled = briefing;
  el.newsSource.disabled = briefing || el.shareType.value !== "news";
  if (el.runSpecificTargetField) {
    el.runSpecificTargetField.hidden = !showSpecificTarget;
  }
  if (el.runSpecificTargetLabel) {
    el.runSpecificTargetLabel.textContent = specificTargetIsGroup ? "指定群号" : "指定QQ号";
  }
  if (el.runSpecificTarget) {
    el.runSpecificTarget.placeholder = specificTargetIsGroup
      ? "群号，可留空"
      : "QQ号，可留空";
    el.runSpecificTarget.inputMode = showSpecificTarget ? "numeric" : "text";
    el.runSpecificTarget.disabled = !specificTarget;
    if (specificTarget) {
      if (el.runSpecificTarget.dataset.target && el.runSpecificTarget.dataset.target !== target) {
        el.runSpecificTarget.value = "";
      }
      el.runSpecificTarget.dataset.target = target;
    } else {
      el.runSpecificTarget.value = "";
      el.runSpecificTarget.dataset.target = "";
    }
  }
  syncSweetSelect(el.runTarget);
  syncSweetSelect(el.shareType);
  syncSweetSelect(el.newsSource);
}

function bindEvents() {
  el.configForm?.addEventListener("submit", saveConfig);
  el.configForm?.addEventListener("input", handleConfigChanged);
  el.configForm?.addEventListener("change", handleConfigChanged);
  el.reloadConfigButton?.addEventListener("click", () => loadConfig());
  bindProviderProbeEvents();
  window.addEventListener("scroll", updateSettingsTabFromScroll, { passive: true });
  el.runForm.addEventListener("submit", runShare);
  bindMediaEvents();
  window.addEventListener("beforeunload", () => {
    writeStoredActiveView(state.activeView);
    window.clearTimeout(state.pollTimer);
    clearImageMemoryCache();
    stopTargetCarouselTimer();
    stopCalendarCarouselTimer();
  });
  document.addEventListener("visibilitychange", handleStatusVisibilityChange);
  bindTargetEvents();
  el.calendarPanel?.addEventListener("pointerenter", stopCalendarCarouselTimer);
  el.calendarPanel?.addEventListener("pointerleave", scheduleCalendarCarousel);
  el.calendarPanel?.addEventListener("focusin", stopCalendarCarouselTimer);
  el.calendarPanel?.addEventListener("focusout", scheduleCalendarCarousel);
  el.runTarget.addEventListener("change", updateRunFormState);
  el.shareType.addEventListener("change", updateRunFormState);
}

async function init() {
  initPageSwitchControls();
  restoreActiveView({ ready: !bridge });
  initSakuraControls();
  bindEvents();
  initDreamCursor();
  initSweetSelects();
  initSweetCombos();
  initCalendarPanelLayout();

  if (!bridge) {
    setNotice("没有检测到 AstrBot Pages bridge，请从 AstrBot WebUI 插件页面进入。", "error");
    return;
  }
  try {
    await withTimeout(
      bridge.ready(),
      BRIDGE_READY_TIMEOUT_MS,
      "初始化超时"
    );
  } catch (error) {
    markActiveViewReady();
    setNotice(error.message || "桥接初始化失败", "error");
    return;
  }
  state.bridgeReady = true;
  await loadStatus({ reveal: false });
  if (state.activeView === "settings" && !state.configData) {
    await loadConfig({ quiet: true });
  }
  markActiveViewReady();
}

init();
