import {
  emptyNode,
  formatDate,
  replaceChildren,
  targetItemLabel,
  text,
  typeLabel,
} from "./format.js?v=20260609-format";

const targetKindLabels = {
  group: "群聊",
  user: "私聊",
  briefing_group: "早报·群聊",
  briefing_user: "早报·私聊",
};

const targetBuckets = {
  group: "groups",
  user: "users",
  briefing_group: "briefing_groups",
  briefing_user: "briefing_users",
};

const targetKindSelectLabels = {
  group: "群聊",
  user: "私聊",
  briefing_group: "早报群聊",
  briefing_user: "早报私聊",
};

const targetKindOptionsByTab = {
  share: ["group", "user"],
  briefing: ["briefing_group", "briefing_user"],
};

const targetCarouselBuckets = ["groups", "users", "briefing_groups", "briefing_users"];

const targetKindByBucket = {
  groups: "group",
  users: "user",
  briefing_groups: "briefing_group",
  briefing_users: "briefing_user",
};

function topType(types = {}) {
  const pairs = Object.entries(types || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
  return pairs.length ? typeLabel(pairs[0][0]) : "暂无";
}

function targetStatTexts(stats = {}, kind = "") {
  const successRate = Number(stats.success_rate || 0);
  const totalCount = Number(stats.total || 0);
  const failedCount = Number(stats.failed || 0);
  const failureRate = totalCount ? failedCount / totalCount : 0;
  if (text(kind).startsWith("briefing")) {
    return [
      `成功率 ${Math.round(successRate * 100)}%`,
      `失败率 ${Math.round(failureRate * 100)}%`,
      `30天 ${stats.recent_count || 0} 次`,
      `频率 ${stats.frequency_per_day || 0}/天`,
      `最近 ${formatDate(stats.last_at)}`,
    ];
  }
  return [
    `成功率 ${Math.round(successRate * 100)}%`,
    `失败率 ${Math.round(failureRate * 100)}%`,
    `30天 ${stats.recent_count || 0} 次`,
    `频率 ${stats.frequency_per_day || 0}/天`,
    `偏好 ${topType(stats.types)}`,
    `最近 ${formatDate(stats.last_at)}`,
  ];
}

function targetStatNodes(stats = {}, kind = "") {
  return targetStatTexts(stats, kind).map((label) => {
    const span = document.createElement("span");
    span.textContent = label;
    return span;
  });
}

export function createTargetsUi({
  state,
  elements: el,
  carouselIntervalMs = 5200,
  apiPost,
  syncSweetSelect,
  setTargetsDirty,
  setNotice,
  applyMediaPage,
  isDefaultMediaFilter,
  renderAll,
  scheduleCalendarPanelLayout,
} = {}) {
  function ensureTargetArrays() {
    if (!state.status) state.status = {};
    if (!state.status.targets) state.status.targets = {};
    for (const bucket of Object.values(targetBuckets)) {
      if (!Array.isArray(state.status.targets[bucket])) {
        state.status.targets[bucket] = [];
      }
    }
  }

  function dashboardTargetItems() {
    ensureTargetArrays();
    return targetCarouselBuckets.flatMap((bucket) => {
      const fallbackKind = targetKindByBucket[bucket] || "group";
      return (state.status.targets[bucket] || []).map((item, index) => {
        const kind = item.kind || fallbackKind;
        return {
          bucket,
          index,
          kind,
          item: { ...item, kind },
        };
      });
    });
  }

  function targetCarouselSignature(items) {
    return items
      .map(({ bucket, item }) => [bucket, text(item.kind), text(item.id), text(item.target_label)].join(":"))
      .join("|");
  }

  function stopTargetCarouselTimer() {
    window.clearTimeout(state.targetCarouselTimer);
    state.targetCarouselTimer = 0;
  }

  function scheduleTargetCarousel() {
    stopTargetCarouselTimer();
    if (
      state.activeView !== "dashboard" ||
      el.dashboardView?.hidden ||
      el.targetCarousel?.matches(":hover") ||
      el.targetCarousel?.contains(document.activeElement)
    ) {
      return;
    }
    if (dashboardTargetItems().length <= 1) return;
    state.targetCarouselTimer = window.setTimeout(() => {
      setTargetCarouselIndex(state.targetCarouselIndex + 1);
    }, carouselIntervalMs);
  }

  function setTargetCarouselIndex(index) {
    const items = dashboardTargetItems();
    if (!items.length) {
      renderTargetCarousel();
      return;
    }
    state.targetCarouselIndex = ((index % items.length) + items.length) % items.length;
    renderTargetCarousel();
  }

  function renderTargetCarousel() {
    if (!el.targetCarousel || !el.targetCarouselStats) return;
    const items = dashboardTargetItems();
    const signature = targetCarouselSignature(items);
    if (signature !== state.targetCarouselSignature) {
      state.targetCarouselSignature = signature;
      state.targetCarouselIndex = Math.min(Math.max(0, state.targetCarouselIndex), Math.max(0, items.length - 1));
    }

    if (!items.length) {
      stopTargetCarouselTimer();
      state.targetCarouselIndex = 0;
      el.targetCarousel.classList.add("is-empty");
      el.targetCarouselKind.textContent = "--";
      el.targetCarouselName.textContent = "暂无目标";
      const empty = document.createElement("span");
      empty.textContent = "暂无目标数据";
      replaceChildren(el.targetCarouselStats, [empty]);
      return;
    }

    state.targetCarouselIndex = Math.min(Math.max(0, state.targetCarouselIndex), items.length - 1);
    const current = items[state.targetCarouselIndex];
    el.targetCarousel.classList.remove("is-empty");
    el.targetCarouselKind.textContent = targetKindLabels[current.kind] || current.kind || "目标";
    el.targetCarouselName.textContent = targetItemLabel(current.item);
    replaceChildren(el.targetCarouselStats, targetStatNodes(current.item.stats || {}, current.kind));
    scheduleTargetCarousel();
  }

  function targetCollections() {
    ensureTargetArrays();
    const targets = state.status.targets;
    if (state.targetTab === "briefing") {
      return [
        ["briefing_groups", targets.briefing_groups],
        ["briefing_users", targets.briefing_users],
      ];
    }
    return [
      ["groups", targets.groups],
      ["users", targets.users],
    ];
  }

  function targetKindOptions() {
    return targetKindOptionsByTab[state.targetTab] || targetKindOptionsByTab.share;
  }

  function syncTargetKindSelect() {
    const kinds = targetKindOptions();
    const previous = el.targetKindSelect.value;
    const options = kinds.map((kind) => {
      const option = document.createElement("option");
      option.value = kind;
      option.textContent = targetKindSelectLabels[kind] || targetKindLabels[kind] || kind;
      return option;
    });
    el.targetKindSelect.replaceChildren(...options);
    el.targetKindSelect.value = kinds.includes(previous) ? previous : kinds[0];
    syncSweetSelect(el.targetKindSelect);
  }

  function updateTargetItem(bucket, index, key, value) {
    ensureTargetArrays();
    const item = state.status.targets[bucket]?.[index];
    if (!item) return;
    item[key] = value;
    if (key === "id") item.target_label = "";
    setTargetsDirty(true);
  }

  function removeTargetItem(bucket, index) {
    ensureTargetArrays();
    state.status.targets[bucket].splice(index, 1);
    setTargetsDirty(true);
    renderTargets();
  }

  function targetItem(item, bucket, index) {
    const node = document.createElement("article");
    node.className = "target-item editable";

    const header = document.createElement("div");
    header.className = "target-edit-head";

    const kind = document.createElement("span");
    kind.className = "target-kind";
    kind.textContent = targetKindLabels[item.kind] || item.kind || "目标";

    const targetTitle = document.createElement("div");
    targetTitle.className = "target-title";
    const name = document.createElement("strong");
    name.textContent = targetItemLabel(item);
    targetTitle.append(kind, name);

    const removeButton = document.createElement("button");
    removeButton.className = "icon-button small danger-button";
    removeButton.type = "button";
    removeButton.setAttribute("aria-label", "删除目标");
    removeButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="m6 6 1 16h10l1-16"/><path d="M10 11v6M14 11v6"/></svg>';
    removeButton.addEventListener("click", () => removeTargetItem(bucket, index));

    header.append(targetTitle, removeButton);

    const fields = document.createElement("div");
    fields.className = "target-fields";

    const idLabel = document.createElement("label");
    idLabel.className = "target-field";
    const idText = document.createElement("span");
    idText.textContent = "目标 ID";
    const idInput = document.createElement("input");
    idInput.value = item.id || "";
    idInput.placeholder = bucket.includes("groups") ? "群号 / Session ID" : "QQ号 / UID / Session ID";
    idInput.addEventListener("input", () => updateTargetItem(bucket, index, "id", idInput.value));
    idLabel.append(idText, idInput);
    fields.append(idLabel);

    if (!bucket.startsWith("briefing")) {
      const seqLabel = document.createElement("label");
      seqLabel.className = "target-field";
      const seqText = document.createElement("span");
      seqText.textContent = "独立序列";
      const seqInput = document.createElement("input");
      seqInput.value = item.sequence || "auto";
      seqInput.placeholder = "auto / news,mood";
      seqInput.addEventListener("input", () => updateTargetItem(bucket, index, "sequence", seqInput.value));
      seqLabel.append(seqText, seqInput);
      fields.append(seqLabel);

      const cronLabel = document.createElement("label");
      cronLabel.className = "target-field";
      const cronText = document.createElement("span");
      cronText.textContent = "独立定时";
      const cronInput = document.createElement("input");
      cronInput.value = item.cron || "";
      cronInput.placeholder = "留空跟随全局，可填 morning/noon/twice 或 Cron";
      cronInput.addEventListener("input", () => updateTargetItem(bucket, index, "cron", cronInput.value));
      cronLabel.append(cronText, cronInput);
      fields.append(cronLabel);
    }

    node.append(header, fields);
    return node;
  }

  function renderTargets() {
    syncTargetKindSelect();
    const items = targetCollections().flatMap(([bucket, list]) =>
      list.map((item, index) => ({ item, bucket, index }))
    );
    replaceChildren(
      el.targetList,
      items.length ? items.map(({ item, bucket, index }) => targetItem(item, bucket, index)) : [emptyNode()]
    );
    for (const segment of el.targetSegments) {
      segment.classList.toggle("active", segment.dataset.targetTab === state.targetTab);
    }
    scheduleCalendarPanelLayout({ rerender: true });
  }

  function addTarget() {
    ensureTargetArrays();
    syncTargetKindSelect();
    const kind = el.targetKindSelect.value || "group";
    const bucket = targetBuckets[kind] || "groups";
    const item = {
      id: "",
      kind,
      cron: "",
      sequence: "auto",
      stats: {
        total: 0,
        success: 0,
        failed: 0,
        success_rate: 0,
        recent_count: 0,
        frequency_per_day: 0,
        types: {},
      },
      state: {},
    };
    state.status.targets[bucket].push(item);
    state.targetTab = bucket.startsWith("briefing") ? "briefing" : "share";
    setTargetsDirty(true);
    renderTargets();
    el.targetList.querySelector("input")?.focus();
  }

  function targetPayloadList(bucket) {
    ensureTargetArrays();
    return (state.status.targets[bucket] || [])
      .filter((item) => text(item.id).trim())
      .map((item) => ({
        id: text(item.id).trim(),
        cron: text(item.cron).trim(),
        sequence: text(item.sequence || "auto").trim() || "auto",
      }));
  }

  async function saveTargets() {
    el.saveTargetsButton.disabled = true;
    try {
      state.status = await apiPost("page/targets", {
        groups: targetPayloadList("groups"),
        users: targetPayloadList("users"),
        briefing_groups: targetPayloadList("briefing_groups"),
        briefing_users: targetPayloadList("briefing_users"),
      });
      setTargetsDirty(false);
      if (isDefaultMediaFilter()) {
        applyMediaPage(state.status);
      }
      renderAll();
      setNotice("目标配置已保存。", "success");
    } catch (error) {
      el.saveTargetsButton.disabled = false;
      setNotice(error.message || "目标保存失败", "error");
    }
  }

  function bindTargetEvents() {
    el.addTargetButton.addEventListener("click", addTarget);
    el.saveTargetsButton.addEventListener("click", saveTargets);
    el.targetCarousel?.addEventListener("pointerenter", stopTargetCarouselTimer);
    el.targetCarousel?.addEventListener("pointerleave", scheduleTargetCarousel);
    el.targetCarousel?.addEventListener("focusin", stopTargetCarouselTimer);
    el.targetCarousel?.addEventListener("focusout", scheduleTargetCarousel);
    for (const segment of el.targetSegments) {
      segment.addEventListener("click", () => {
        state.targetTab = segment.dataset.targetTab || "share";
        renderTargets();
      });
    }
  }

  return {
    bindTargetEvents,
    renderTargetCarousel,
    renderTargets,
    scheduleTargetCarousel,
    stopTargetCarouselTimer,
  };
}
