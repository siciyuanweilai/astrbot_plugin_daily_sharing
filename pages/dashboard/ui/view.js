function normalizeActiveView(value) {
  return value === "settings" || value === "dashboard" ? value : "";
}

function readStoredActiveView(storageKey) {
  try {
    return normalizeActiveView(localStorage.getItem(storageKey)) || "dashboard";
  } catch (_) {
    return "dashboard";
  }
}

function writeStoredActiveView(storageKey, view) {
  try {
    localStorage.setItem(storageKey, view === "settings" ? "settings" : "dashboard");
  } catch (_) {
    // 本地页签只做兜底，服务端偏好会在桥接可用后同步。
  }
}

export function createViewController({
  state,
  elements: el,
  bridge,
  apiPost,
  storageKey,
  closeSweetSelects,
  stopTargetCarouselTimer,
  stopCalendarCarouselTimer,
  renderTargetCarousel,
  scheduleCalendarCarousel,
  scheduleCalendarPanelLayout,
  loadConfig,
  setSettingsTab,
  updateSettingsTabFromScroll,
} = {}) {
  function markActiveViewReady() {
    document.documentElement.dataset.viewReady = "true";
  }

  async function saveActiveViewPreference(view) {
    const nextView = view === "settings" ? "settings" : "dashboard";
    writeStoredActiveView(storageKey, nextView);
    if (!bridge || !state.bridgeReady) return;
    const saveSeq = state.activeViewSaveSeq + 1;
    state.activeViewSaveSeq = saveSeq;
    state.activeViewSaving = true;
    try {
      await apiPost("page/preferences", { active_view: nextView });
    } catch (_) {
      // 页面桥接不可用时，本地兜底仍保留当前页签。
    } finally {
      if (state.activeViewSaveSeq === saveSeq) {
        state.activeViewSaving = false;
      }
    }
  }

  function setActiveView(view, { persist = true, scroll = true, behavior = "smooth", ready = true } = {}) {
    const nextView = view === "settings" ? "settings" : "dashboard";
    state.activeView = nextView;
    if (persist) void saveActiveViewPreference(nextView);
    el.dashboardView.hidden = nextView !== "dashboard";
    el.settingsView.hidden = nextView !== "settings";
    document.body.classList.toggle("is-settings-view", nextView === "settings");
    if (ready) markActiveViewReady();
    if (nextView !== "dashboard") {
      stopTargetCarouselTimer();
      stopCalendarCarouselTimer();
    }
    closeSweetSelects();
    if (scroll) window.scrollTo({ top: 0, behavior });
  }

  function restoreActiveView({ ready = true } = {}) {
    setActiveView(readStoredActiveView(storageKey), { persist: false, scroll: false, behavior: "auto", ready });
  }

  function applyActiveViewPreference(preferences, { force = false, ready = true } = {}) {
    const preferred = normalizeActiveView(preferences?.active_view);
    if (!preferred) return false;
    if (!force && state.activeViewSyncedFromServer) return false;
    state.activeViewSyncedFromServer = true;
    setActiveView(preferred, { persist: false, scroll: false, behavior: "auto", ready });
    if (preferred === "settings") {
      setSettingsTab("target", { scroll: false });
    }
    return true;
  }

  async function openSettingsPage() {
    setActiveView("settings");
    setSettingsTab("target", { scroll: false });
    if (!state.configData) {
      await loadConfig({ quiet: true });
    }
    window.setTimeout(updateSettingsTabFromScroll, 220);
  }

  function openDashboardPage() {
    setActiveView("dashboard");
    renderTargetCarousel();
    scheduleCalendarCarousel();
    scheduleCalendarPanelLayout();
  }

  function initPageSwitchControls() {
    if (state.pageSwitchBound) return;
    state.pageSwitchBound = true;
    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("#settingsPageButton, #settingsBackButton");
      if (!(button instanceof HTMLButtonElement) || button.disabled) return;
      event.preventDefault();
      if (button.id === "settingsPageButton") {
        openSettingsPage();
      } else {
        openDashboardPage();
      }
    }, true);
  }

  return {
    applyActiveViewPreference,
    initPageSwitchControls,
    markActiveViewReady,
    restoreActiveView,
    setActiveView,
    writeStoredActiveView: (view) => writeStoredActiveView(storageKey, view),
  };
}
