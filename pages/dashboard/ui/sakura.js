function readLocalSakuraEnabled(storageKey) {
  try {
    return localStorage.getItem(storageKey) !== "off";
  } catch {
    return true;
  }
}

function writeLocalSakuraEnabled(storageKey, enabled) {
  try {
    localStorage.setItem(storageKey, enabled ? "on" : "off");
  } catch {
    // 本地存储只是兜底，失败时仍更新当前页面状态。
  }
}

export function createSakuraControls({
  state,
  elements: el,
  bridge,
  apiPost,
  setNotice,
  storageKey,
  clearSakuraFall,
  hasSakuraFall,
  initSakuraFall,
  isMotionReduced,
} = {}) {
  function renderSakuraToggle() {
    if (!el.sakuraToggles.length) return;

    const reducedMotion = isMotionReduced();
    const waitingForServer = Boolean(bridge) && !state.sakuraSynced;
    const enabled = state.sakuraEnabled && !reducedMotion && !waitingForServer;
    const disabled = reducedMotion || state.sakuraSaving || waitingForServer;
    for (const toggle of el.sakuraToggles) {
      toggle.classList.toggle("is-on", enabled);
      toggle.disabled = disabled;
      toggle.setAttribute("aria-checked", enabled ? "true" : "false");
      toggle.removeAttribute("title");
    }
  }

  function setSakuraEnabled(enabled, { force = false, persistLocal = false, synced } = {}) {
    const nextEnabled = Boolean(enabled);
    const changed = state.sakuraEnabled !== nextEnabled;
    state.sakuraEnabled = nextEnabled;
    if (typeof synced === "boolean") state.sakuraSynced = synced;
    if (persistLocal) writeLocalSakuraEnabled(storageKey, state.sakuraEnabled);

    const shouldRender = state.sakuraEnabled && !isMotionReduced() && (!bridge || state.sakuraSynced);
    if (force || changed || (shouldRender && !hasSakuraFall()) || (!shouldRender && hasSakuraFall())) {
      clearSakuraFall();
      if (shouldRender) initSakuraFall();
    }
    renderSakuraToggle();
  }

  function applySakuraPreferences(preferences, options = {}) {
    if (!preferences || typeof preferences !== "object") return false;
    if (!Object.prototype.hasOwnProperty.call(preferences, "sakura_enabled")) return false;
    setSakuraEnabled(Boolean(preferences.sakura_enabled), {
      force: Boolean(options.force),
      synced: true,
    });
    return true;
  }

  async function saveSakuraPreference(enabled) {
    const previousEnabled = state.sakuraEnabled;
    setSakuraEnabled(enabled, {
      persistLocal: !bridge,
      synced: true,
    });

    if (!bridge) return;

    state.sakuraSaving = true;
    renderSakuraToggle();
    try {
      const data = await apiPost("page/preferences", { sakura_enabled: Boolean(enabled) });
      if (!applySakuraPreferences(data.preferences, { force: true })) {
        setSakuraEnabled(enabled, { force: true, synced: true });
      }
    } catch (error) {
      setSakuraEnabled(previousEnabled, { force: true, synced: true });
      setNotice(error.message || "樱花开关保存失败", "error");
    } finally {
      state.sakuraSaving = false;
      renderSakuraToggle();
    }
  }

  function initSakuraControls() {
    if (bridge) {
      setSakuraEnabled(false, { force: true, synced: false });
    } else {
      setSakuraEnabled(readLocalSakuraEnabled(storageKey), { force: true, synced: true });
    }

    if (!el.sakuraToggles.length || state.sakuraToggleBound) return;
    state.sakuraToggleBound = true;
    for (const toggle of el.sakuraToggles) {
      toggle.addEventListener("click", () => {
        if (isMotionReduced() || state.sakuraSaving || (bridge && !state.sakuraSynced)) return;
        saveSakuraPreference(!state.sakuraEnabled);
      });
    }
  }

  return {
    applySakuraPreferences,
    initSakuraControls,
    renderSakuraToggle,
    setSakuraEnabled,
  };
}
