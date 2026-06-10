export function text(value) {
  return String(value ?? "");
}

export function displayContent(value) {
  return text(value).replace(/模型触发/g, "自然语言触发");
}

export function clampContent(value, max = 180) {
  const body = displayContent(value).replace(/\s+/g, " ").trim();
  return body.length > max ? `${body.slice(0, max)}...` : body;
}

export function fullContent(value) {
  return displayContent(value).trim();
}

const typeLabels = {
  auto: "自动",
  greeting: "问候",
  news: "新闻",
  briefing: "早报",
  mood: "心情",
  knowledge: "知识",
  recommendation: "推荐",
};

const triggerModeLabels = {
  cron: "定时触发",
  random_period: "随机时段",
};

const weekdayLabels = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
const dayMs = 86400000;

function dayStart(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function clockText(date) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function shortMonthDay(date) {
  return `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function formatDate(value) {
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

export function formatTime(value) {
  if (!value) return "--";
  const raw = text(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    const matched = raw.match(/\d{1,2}:\d{2}/);
    return matched?.[0] || raw || "--";
  }
  return clockText(date);
}

export function formatFullDateTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const weekday = weekdayLabels[date.getDay()];
  return `${year}年${month}月${day}日 ${weekday} ${clockText(date)}`;
}

export function formatMediaTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  const dayDiff = Math.round((dayStart(new Date()) - dayStart(date)) / dayMs);
  if (dayDiff === 0) return `今天 ${clockText(date)}`;
  if (dayDiff === 1) return `昨天 ${clockText(date)}`;
  if (dayDiff === 2) return `前天 ${clockText(date)}`;
  return `${shortMonthDay(date)} ${clockText(date)}`;
}

export function formatScheduleDayTime(value, todayLabel = "今天") {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  const dayDiff = Math.round((dayStart(date) - dayStart(new Date())) / dayMs);
  if (dayDiff === 0) return todayLabel ? `${todayLabel} ${clockText(date)}` : clockText(date);
  if (dayDiff === 1) return `明天 ${clockText(date)}`;
  if (dayDiff === 2) return `后天 ${clockText(date)}`;
  return `${shortMonthDay(date)} ${clockText(date)}`;
}

export function formatNextShareTime(value) {
  return formatScheduleDayTime(value, "");
}

export function formatRelativeActionTime(value) {
  return formatMediaTime(value);
}

export function formatDateOnly(value) {
  if (!value) return "--";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return text(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${weekdayLabels[date.getDay()]}`;
}

export function typeLabel(value) {
  return typeLabels[text(value)] || text(value) || "--";
}

export function enabledShortLabel(value) {
  return value ? "开" : "关";
}

export function triggerModeLabel(value) {
  return triggerModeLabels[text(value)] || text(value) || "--";
}

export function triggerSummary(modeValue) {
  return triggerModeLabel(text(modeValue) || "cron");
}

export function targetChatKind(value, kind = "") {
  const explicitKind = text(kind).toLowerCase();
  if (explicitKind.includes("group")) return "群聊";
  if (explicitKind.includes("user") || explicitKind.includes("friend") || explicitKind.includes("private")) {
    return "私聊";
  }

  const raw = text(value).trim();
  const parts = raw.split(":");
  if (parts.length >= 3) {
    const platform = parts[0] || "";
    const messageType = parts[1].toLowerCase();
    const platformLabel = platform === "weixin_oc" ? "微信" : "";
    if (messageType.includes("group")) return `${platformLabel}群聊`;
    if (messageType.includes("friend") || messageType.includes("private")) return `${platformLabel}私聊`;
  }
  if (raw.endsWith("@chatroom")) return "群聊";
  if (raw.endsWith("@im.wechat")) return "微信私聊";
  return "";
}

export function targetLabel(value, label = "", kind = "") {
  const raw = text(value).trim();
  if (!raw) return "全局";
  const knownLabels = {
    qzone_broadcast: "QQ 空间",
    global: "全局分享",
    briefing: "早报",
    briefing_broadcast: "早报",
  };
  if (knownLabels[raw]) return knownLabels[raw];

  const display = text(label).trim();
  const chatKind = targetChatKind(raw, kind);
  if (display) return chatKind ? `${chatKind} ${display}` : display;

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

export function itemTargetLabel(item) {
  return targetLabel(item?.target_id, item?.target_label, item?.kind);
}

export function targetItemLabel(item) {
  const raw = text(item?.id).trim();
  const display = text(item?.target_label).trim();
  if (!raw && !display) return "未填写目标";
  return targetLabel(raw, display);
}

export function replaceChildren(target, children) {
  target.replaceChildren(...children.filter(Boolean));
}

export function emptyNode(label = "暂无数据") {
  const node = document.createElement("div");
  node.className = "empty";
  node.textContent = label;
  return node;
}
