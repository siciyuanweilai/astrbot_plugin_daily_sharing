const bridge = window.AstrBotPluginPage;
const BRIDGE_READY_TIMEOUT_MS = 5000;
const BRIDGE_REQUEST_TIMEOUT_MS = 20000;

const state = {
  status: null,
  history: [],
  targetTab: "share",
  pollTimer: 0,
};

const typeLabels = {
  auto: "自动",
  greeting: "问候",
  news: "新闻",
  mood: "心情",
  knowledge: "知识",
  recommendation: "推荐",
};

const triggerModeLabels = {
  cron: "定时触发",
  random_period: "随机时段",
};

const periodLabels = {
  dawn: "凌晨",
  morning: "早晨",
  forenoon: "上午",
  afternoon: "下午",
  evening: "傍晚",
  night: "夜间",
  late_night: "深夜",
};

const targetKindLabels = {
  group: "群",
  user: "私",
  briefing_group: "早群",
  briefing_user: "早私",
};

const actionTargetLabels = {
  broadcast: "群聊 / 私聊",
  qzone: "QQ 空间",
  briefing: "早报",
};

const el = {
  autoToggle: document.getElementById("autoToggle"),
  refreshButton: document.getElementById("refreshButton"),
  enabledText: document.getElementById("enabledText"),
  periodText: document.getElementById("periodText"),
  jobsText: document.getElementById("jobsText"),
  targetsText: document.getElementById("targetsText"),
  busyText: document.getElementById("busyText"),
  runForm: document.getElementById("runForm"),
  runTarget: document.getElementById("runTarget"),
  shareType: document.getElementById("shareType"),
  newsSource: document.getElementById("newsSource"),
  runButton: document.getElementById("runButton"),
  notice: document.getElementById("notice"),
  configList: document.getElementById("configList"),
  targetList: document.getElementById("targetList"),
  jobList: document.getElementById("jobList"),
  historyList: document.getElementById("historyList"),
  historyButton: document.getElementById("historyButton"),
  actionList: document.getElementById("actionList"),
  segments: [...document.querySelectorAll(".segment")],
};

function text(value) {
  return String(value ?? "");
}

function clampContent(value, max = 180) {
  const body = text(value).replace(/\s+/g, " ").trim();
  return body.length > max ? `${body.slice(0, max)}...` : body;
}

function withTimeout(promise, timeoutMs, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => window.clearTimeout(timeoutId));
}

function normalizeBridgeResult(result) {
  if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "ok")) {
    if (!result.ok) {
      throw new Error(result.error?.message || result.message || "请求失败");
    }
    return result.data || {};
  }
  return result || {};
}

async function apiGet(endpoint, params = {}) {
  const result = await withTimeout(
    bridge.apiGet(endpoint, params),
    BRIDGE_REQUEST_TIMEOUT_MS,
    "请求超时"
  );
  return normalizeBridgeResult(result);
}

async function apiPost(endpoint, body = {}) {
  const result = await withTimeout(
    bridge.apiPost(endpoint, body),
    BRIDGE_REQUEST_TIMEOUT_MS,
    "请求超时"
  );
  return normalizeBridgeResult(result);
}

function setNotice(message, tone = "info") {
  el.notice.hidden = !message;
  el.notice.textContent = message || "";
  el.notice.className = "notice";
  if (tone) el.notice.classList.add(tone);
}

function formatDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function typeLabel(value) {
  return typeLabels[text(value)] || text(value) || "--";
}

function triggerModeLabel(value) {
  return triggerModeLabels[text(value)] || text(value) || "--";
}

function sourceName(key) {
  const item = state.status?.news_sources?.find((source) => source.key === key);
  return item?.name || key || "自动";
}

function targetLabel(value) {
  const raw = text(value).trim();
  if (!raw) return "全局";
  const knownLabels = {
    qzone_broadcast: "QQ 空间",
    global: "全局分享",
    briefing: "早报",
  };
  if (knownLabels[raw]) return knownLabels[raw];

  const parts = raw.split(":");
  if (parts.length >= 3) {
    const platform = parts[0] || "";
    const messageType = parts[1].toLowerCase();
    const id = parts.slice(2).join(":");
    const platformLabel = platform === "weixin_oc" ? "微信" : "";
    if (messageType.includes("group")) return `${platformLabel}群聊 ${id}`.trim();
    if (messageType.includes("friend") || messageType.includes("private")) {
      return `${platformLabel}私聊 ${id}`.trim();
    }
  }

  return raw;
}

function replaceChildren(target, children) {
  target.replaceChildren(...children.filter(Boolean));
}

function emptyNode(label = "暂无数据") {
  const node = document.createElement("div");
  node.className = "empty";
  node.textContent = label;
  return node;
}

function fillNewsSources() {
  const selected = el.newsSource.value;
  const options = [new Option("自动", "")];
  for (const source of state.status?.news_sources || []) {
    options.push(new Option(source.name || source.key, source.key));
  }
  el.newsSource.replaceChildren(...options);
  el.newsSource.value = selected;
}

function renderMetrics() {
  const status = state.status || {};
  const targets = status.targets?.summary || {};
  const totalTargets = Number(targets.share_targets || 0) + Number(targets.briefing_targets || 0);
  el.autoToggle.checked = Boolean(status.enabled);
  el.enabledText.textContent = status.enabled ? "已启用" : "已停用";
  el.periodText.textContent = `${periodLabels[status.period?.key] || status.period?.key || "--"} ${status.period?.range || ""}`.trim();
  el.jobsText.textContent = `${status.scheduler?.job_count || 0} 个`;
  el.targetsText.textContent = `${totalTargets} 个`;
  el.busyText.textContent = status.busy ? "忙碌" : "空闲";
  el.busyText.className = `badge ${status.busy ? "warn" : "ok"}`;
  el.runButton.disabled = Boolean(status.busy);
}

function configRow(label, value) {
  const row = document.createElement("div");
  row.className = "config-row";
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  row.append(dt, dd);
  return row;
}

function renderConfig() {
  const cfg = state.status?.config || {};
  const qzone = state.status?.qzone || {};
  replaceChildren(el.configList, [
    configRow("全局触发", `${triggerModeLabel(cfg.trigger_mode)} / ${cfg.sharing_cron || "--"}`),
    configRow("全局类型", typeLabel(cfg.sharing_type)),
    configRow("QQ 空间", `${cfg.qzone_enabled ? "定时开启" : "定时关闭"} / ${qzone.available ? "插件可用" : "插件不可用"}`),
    configRow("空间触发", `${triggerModeLabel(cfg.qzone_trigger_mode)} / ${cfg.qzone_cron || "--"}`),
    configRow("早报", `${cfg.briefing_60s ? "60s" : ""}${cfg.briefing_60s && cfg.briefing_ai ? " + " : ""}${cfg.briefing_ai ? "AI" : ""}` || "关闭"),
    configRow("早报同步空间", cfg.briefing_qzone_sync ? "开启" : "关闭"),
  ]);
}

function targetItem(item) {
  const node = document.createElement("article");
  node.className = "target-item";
  const main = document.createElement("div");
  const title = document.createElement("div");
  title.className = "item-title";
  const kind = document.createElement("span");
  kind.className = "target-kind";
  kind.textContent = targetKindLabels[item.kind] || item.kind || "目标";
  const strong = document.createElement("strong");
  strong.textContent = item.id || "--";
  title.append(kind, strong);
  const meta = document.createElement("div");
  meta.className = "item-meta";
  meta.textContent = `序列 ${item.sequence || "auto"}${item.cron ? ` / ${item.cron}` : ""}`;
  main.append(title, meta);
  node.append(main);
  return node;
}

function renderTargets() {
  const targets = state.status?.targets || {};
  const items = state.targetTab === "briefing"
    ? [...(targets.briefing_groups || []), ...(targets.briefing_users || [])]
    : [...(targets.groups || []), ...(targets.users || [])];
  replaceChildren(el.targetList, items.length ? items.map(targetItem) : [emptyNode()]);
  for (const segment of el.segments) {
    segment.classList.toggle("active", segment.dataset.targetTab === state.targetTab);
  }
}

function renderJobs() {
  const jobs = state.status?.scheduler?.jobs || [];
  if (!jobs.length) {
    replaceChildren(el.jobList, [emptyNode()]);
    return;
  }
  replaceChildren(el.jobList, jobs.map((job) => {
    const node = document.createElement("article");
    node.className = "job-item";
    const title = document.createElement("strong");
    title.textContent = job.display_name || job.name || job.id || "任务";
    title.title = job.id || job.name || "";
    const next = document.createElement("span");
    next.className = "item-meta";
    next.textContent = `下次 ${formatDate(job.next_run_time)}`;
    const trigger = document.createElement("span");
    trigger.className = "item-meta";
    trigger.textContent = job.trigger || "";
    node.append(title, next, trigger);
    return node;
  }));
}

function renderHistory() {
  const history = state.history.length ? state.history : state.status?.history || [];
  if (!history.length) {
    replaceChildren(el.historyList, [emptyNode()]);
    return;
  }
  replaceChildren(el.historyList, history.map((item) => {
    const node = document.createElement("article");
    node.className = "history-item";
    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = `${formatDate(item.timestamp)} · ${typeLabel(item.type)}`;
    const content = document.createElement("div");
    content.className = "history-content";
    const strong = document.createElement("strong");
    strong.textContent = targetLabel(item.target_id);
    strong.title = item.target_id || "";
    const body = document.createElement("div");
    body.className = "item-meta";
    body.textContent = clampContent(item.content);
    content.append(strong, body);
    node.append(meta, content);
    return node;
  }));
}

function renderActions() {
  const actions = state.status?.actions || [];
  if (!actions.length) {
    replaceChildren(el.actionList, [emptyNode()]);
    return;
  }
  replaceChildren(el.actionList, actions.map((item) => {
    const node = document.createElement("article");
    node.className = "action-item";
    const title = document.createElement("strong");
    title.textContent = `${actionTargetLabels[item.target] || item.target} · ${typeLabel(item.share_type)}`;
    const meta = document.createElement("span");
    meta.className = "item-meta";
    meta.textContent = `${item.status || "--"} / ${formatDate(item.started_at)}${item.news_source ? ` / ${sourceName(item.news_source)}` : ""}`;
    const message = document.createElement("span");
    message.className = "item-meta";
    message.textContent = item.message || "";
    node.append(title, meta, message);
    return node;
  }));
}

function renderAll() {
  fillNewsSources();
  renderMetrics();
  renderConfig();
  renderTargets();
  renderJobs();
  renderHistory();
  renderActions();
  updateRunFormState();
}

function hasRunningAction() {
  return (state.status?.actions || []).some((item) => item.status === "running");
}

function schedulePoll() {
  window.clearTimeout(state.pollTimer);
  if (hasRunningAction()) {
    state.pollTimer = window.setTimeout(() => loadStatus({ quiet: true }), 5000);
  }
}

async function loadStatus({ quiet = false } = {}) {
  if (!bridge) {
    setNotice("没有检测到 AstrBot Pages bridge，请从 AstrBot WebUI 插件页面进入。", "error");
    return;
  }
  try {
    state.status = await apiGet("page/status");
    if (!state.history.length) {
      state.history = state.status.history || [];
    }
    renderAll();
    if (!quiet) setNotice("");
    schedulePoll();
  } catch (error) {
    setNotice(error.message || "状态加载失败", "error");
  }
}

async function toggleAutoSharing() {
  el.autoToggle.disabled = true;
  try {
    state.status = await apiPost("page/toggle", { enable: el.autoToggle.checked });
    state.history = state.status.history || state.history;
    renderAll();
    setNotice(el.autoToggle.checked ? "自动分享已启用。" : "自动分享已停用。", "success");
  } catch (error) {
    el.autoToggle.checked = !el.autoToggle.checked;
    setNotice(error.message || "切换失败", "error");
  } finally {
    el.autoToggle.disabled = false;
  }
}

async function runShare(event) {
  event.preventDefault();
  el.runButton.disabled = true;
  try {
    await apiPost("page/run", {
      target: el.runTarget.value,
      share_type: el.shareType.value,
      news_source: el.newsSource.value,
    });
    setNotice("任务已开始。", "success");
    state.history = [];
    await loadStatus({ quiet: true });
  } catch (error) {
    setNotice(error.message || "执行失败", "error");
  } finally {
    el.runButton.disabled = Boolean(state.status?.busy);
  }
}

async function loadMoreHistory() {
  el.historyButton.disabled = true;
  try {
    const data = await apiGet("page/history", { limit: 50 });
    state.history = data.items || [];
    renderHistory();
  } catch (error) {
    setNotice(error.message || "历史加载失败", "error");
  } finally {
    el.historyButton.disabled = false;
  }
}

function updateRunFormState() {
  const briefing = el.runTarget.value === "briefing";
  el.shareType.disabled = briefing;
  el.newsSource.disabled = briefing || el.shareType.value !== "news";
}

function bindEvents() {
  el.refreshButton.addEventListener("click", () => {
    state.history = [];
    loadStatus();
  });
  el.autoToggle.addEventListener("change", toggleAutoSharing);
  el.runForm.addEventListener("submit", runShare);
  el.historyButton.addEventListener("click", loadMoreHistory);
  el.runTarget.addEventListener("change", updateRunFormState);
  el.shareType.addEventListener("change", updateRunFormState);
  for (const segment of el.segments) {
    segment.addEventListener("click", () => {
      state.targetTab = segment.dataset.targetTab || "share";
      renderTargets();
    });
  }
}

async function init() {
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
    setNotice(error.message || "桥接初始化失败", "error");
    return;
  }
  bindEvents();
  await loadStatus();
}

init();
