import { text } from "./format.js?v=20260609-format";

const sliderGestureThresholdPx = 8;
const sliderGestureAxisRatio = 1.25;
const sliderLockReleaseMs = 260;
const sliderGestures = new WeakMap();

const settingsSectionSchema = {
  target: { section: "receiver", title: "分享目标设置" },
  basic: { section: "basic_conf" },
  sequence: {
    section: "basic_conf",
    title: "全局时段序列",
    hint: "当全局默认分享类型为 auto 时，会按当前时段的序列循环选择分享类型。",
  },
  briefing: { section: "extra_shares" },
  content: { section: "content_library" },
  context: { section: "context_conf" },
  news: { section: "news_conf" },
  media: { section: "image_conf" },
  tts: { section: "tts_conf" },
  weixin: {
    section: "image_conf",
    title: "个人微信图片",
    hint: "这些选项只影响个人微信图片发送前的压缩、超时和临时文件清理。",
  },
  qzone: { section: "qzone_conf" },
  qzoneSequence: {
    section: "qzone_conf",
    title: "QQ空间时段序列",
    hint: "当说说分享模式为 auto 时，会按当前时段的序列循环选择说说类型。",
  },
  llm: { section: "llm_conf" },
};

const settingsFieldSchema = {
  cfgEnabled: { root: "enable_auto_sharing" },
  cfgContactAliases: { root: "contact_aliases" },
  cfgBasicTriggerMode: { section: "basic_conf", field: "trigger_mode" },
  cfgBasicSharingCron: { section: "basic_conf", field: "sharing_cron" },
  cfgBasicRandomPeriods: { section: "basic_conf", field: "random_periods" },
  cfgBasicCronDelay: { section: "basic_conf", field: "cron_random_delay" },
  cfgBasicSharingType: { section: "basic_conf", field: "sharing_type" },
  cfgBasicRetentionDays: { section: "basic_conf", field: "data_retention_days" },
  cfgBasicDynamicDays: { section: "basic_conf", field: "dashboard_dynamic_days" },
  cfgDawnSequence: { section: "basic_conf", field: "dawn_sequence" },
  cfgMorningSequence: { section: "basic_conf", field: "morning_sequence" },
  cfgForenoonSequence: { section: "basic_conf", field: "forenoon_sequence" },
  cfgNoonSequence: { section: "basic_conf", field: "noon_sequence" },
  cfgAfternoonSequence: { section: "basic_conf", field: "afternoon_sequence" },
  cfgEveningSequence: { section: "basic_conf", field: "evening_sequence" },
  cfgNightSequence: { section: "basic_conf", field: "night_sequence" },
  cfgLateNightSequence: { section: "basic_conf", field: "late_night_sequence" },
  cfgReferenceHistoryCount: { section: "context_conf", field: "reference_history_count" },
  cfgLifeContext: { section: "context_conf", field: "enable_life_context" },
  cfgLifeContextGroup: { section: "context_conf", field: "life_context_in_group" },
  cfgGroupSchedule: { section: "context_conf", field: "group_share_schedule" },
  cfgChatHistory: { section: "context_conf", field: "enable_chat_history" },
  cfgDeepHistory: { section: "context_conf", field: "enable_deep_history" },
  cfgDeepHistoryHours: { section: "context_conf", field: "deep_history_hours" },
  cfgDeepHistoryMaxCount: { section: "context_conf", field: "deep_history_max_count" },
  cfgPrivateHistoryCount: { section: "context_conf", field: "private_history_count" },
  cfgGroupIntensityCount: { section: "context_conf", field: "group_intensity_check_count" },
  cfgGroupShareStrategy: { section: "context_conf", field: "group_share_strategy" },
  cfgRecordMemory: { section: "context_conf", field: "record_sharing_to_memory" },
  cfgBriefing60s: { section: "extra_shares", field: "enable_60s_news" },
  cfgBriefingAi: { section: "extra_shares", field: "enable_ai_news" },
  cfgBriefingQzoneSync: { section: "extra_shares", field: "sync_briefing_to_qzone" },
  cfgBriefingCron: { section: "extra_shares", field: "cron_briefing" },
  cfgBriefingDelay: { section: "extra_shares", field: "briefing_cron_random_delay" },
  cfgQzoneEnabled: { section: "qzone_conf", field: "enable_qzone" },
  cfgQzoneTriggerMode: { section: "qzone_conf", field: "qzone_trigger_mode" },
  cfgQzoneCron: { section: "qzone_conf", field: "qzone_cron" },
  cfgQzoneRandomPeriods: { section: "qzone_conf", field: "qzone_random_periods" },
  cfgQzoneSharingType: { section: "qzone_conf", field: "qzone_sharing_type" },
  cfgQzoneImage: { section: "qzone_conf", field: "qzone_enable_image" },
  cfgQzoneHotImage: { section: "qzone_conf", field: "qzone_attach_hot_news_image" },
  cfgQzoneImageTypes: { section: "qzone_conf", field: "qzone_image_enabled_types" },
  cfgQzoneDawnSequence: { section: "qzone_conf", field: "qzone_dawn_sequence" },
  cfgQzoneMorningSequence: { section: "qzone_conf", field: "qzone_morning_sequence" },
  cfgQzoneForenoonSequence: { section: "qzone_conf", field: "qzone_forenoon_sequence" },
  cfgQzoneNoonSequence: { section: "qzone_conf", field: "qzone_noon_sequence" },
  cfgQzoneAfternoonSequence: { section: "qzone_conf", field: "qzone_afternoon_sequence" },
  cfgQzoneEveningSequence: { section: "qzone_conf", field: "qzone_evening_sequence" },
  cfgQzoneNightSequence: { section: "qzone_conf", field: "qzone_night_sequence" },
  cfgQzoneLateNightSequence: { section: "qzone_conf", field: "qzone_late_night_sequence" },
  cfgKnowledgePrefix: { section: "content_library", field: "show_knowledge_type_prefix" },
  cfgRecPrefix: { section: "content_library", field: "show_rec_type_prefix" },
  cfgKnowledgeCats: { section: "content_library", field: "knowledge_cats" },
  cfgRecCats: { section: "content_library", field: "rec_cats" },
  cfgAiImage: { section: "image_conf", field: "enable_ai_image" },
  cfgHotImage: { section: "image_conf", field: "attach_hot_news_image" },
  cfgNewsImageCleanupMax: { section: "image_conf", field: "news_image_cleanup_max_count" },
  cfgGiteeSelfieRef: { section: "image_conf", field: "use_gitee_selfie_ref" },
  cfgPriorityText: { section: "image_conf", field: "priority_text_over_schedule" },
  cfgAiVideo: { section: "image_conf", field: "enable_ai_video" },
  cfgSeparateMedia: { section: "image_conf", field: "separate_text_and_image" },
  cfgSeparateDelay: { section: "image_conf", field: "separate_send_delay" },
  cfgRecordImageDesc: { section: "image_conf", field: "record_image_description" },
  cfgAlwaysSelf: { section: "image_conf", field: "image_always_include_self" },
  cfgNeverSelf: { section: "image_conf", field: "image_never_include_self" },
  cfgImageTypes: { section: "image_conf", field: "image_enabled_types" },
  cfgVideoTypes: { section: "image_conf", field: "video_enabled_types" },
  cfgAppearancePrompt: { section: "image_conf", field: "appearance_prompt" },
  cfgTtsEnabled: { section: "tts_conf", field: "enable_tts" },
  cfgAudioOnly: { section: "tts_conf", field: "prefer_audio_only" },
  cfgTtsTypes: { section: "tts_conf", field: "tts_enabled_types" },
  cfgWeixinCompress: { section: "image_conf", field: "weixin_compress_images" },
  cfgWeixinMaxSide: { section: "image_conf", field: "weixin_image_max_side" },
  cfgWeixinMaxSize: { section: "image_conf", field: "weixin_image_max_size_kb" },
  cfgWeixinTimeout: { section: "image_conf", field: "weixin_api_timeout_seconds" },
  cfgWeixinCleanupMax: { section: "image_conf", field: "weixin_temp_cleanup_max_count" },
  cfgNewsApiEnabled: { section: "news_conf", field: "enable_news_api" },
  cfgNewsApiKey: { section: "news_conf", field: "nycnm_api_key" },
  cfgNewsMode: { section: "news_conf", field: "news_random_mode" },
  cfgNewsFixedSource: { section: "news_conf", field: "news_api_source" },
  cfgNewsItemsCount: { section: "news_conf", field: "news_items_count" },
  cfgNewsShareCount: { section: "news_conf", field: "news_share_count" },
  cfgNewsApiTimeout: { section: "news_conf", field: "news_api_timeout" },
  cfgNewsWebSearch: { section: "news_conf", field: "enable_tavily_search" },
  cfgNewsRandomSources: { section: "news_conf", field: "news_random_sources" },
  cfgLlmProviderId: { section: "llm_conf", field: "llm_provider_id" },
  cfgLlmTimeout: { section: "llm_conf", field: "llm_timeout" },
  cfgUsePersona: { section: "llm_conf", field: "use_persona" },
  cfgPersonaId: { section: "llm_conf", field: "persona_id" },
};

function schemaMetaForMapping(schema, mapping = {}) {
  if (mapping.root) {
    return schema.root?.[mapping.root] || {};
  }
  return schema.sections?.[mapping.section]?.fields?.[mapping.field] || {};
}

function cleanSchemaLabel(value) {
  return text(value)
    .replace(/^[^\w\u4e00-\u9fff【】]+/u, "")
    .trim();
}

function fieldCaptionNode(field) {
  return [...field.children].find((child) => child.tagName === "SPAN") || null;
}

function ensureFieldHint(field) {
  let hint = field.querySelector(":scope > .setting-hint");
  if (!hint) {
    hint = document.createElement("p");
    hint.className = "setting-hint";
    field.append(hint);
  }
  return hint;
}

function ensureSectionNote(section) {
  let note = section.querySelector(":scope > .settings-section-note");
  if (!note) {
    note = document.createElement("p");
    note.className = "settings-section-note";
    const title = section.querySelector(":scope > .settings-section-title");
    title?.insertAdjacentElement("afterend", note);
  }
  return note;
}

function settingSliderEventPoint(event) {
  const touch = event.touches?.[0] || event.changedTouches?.[0];
  const x = Number(event.clientX ?? touch?.clientX);
  const y = Number(event.clientY ?? touch?.clientY);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

function shouldGuardSettingSliderGesture(event) {
  const pointerType = text(event?.pointerType).toLowerCase();
  if (pointerType === "mouse") return false;
  if (pointerType === "touch" || pointerType === "pen") return true;
  return Boolean(window.matchMedia?.("(pointer: coarse)")?.matches || window.innerWidth <= 720);
}

function restoreSettingSliderGesture(range, gesture) {
  if (!gesture) return;
  range.value = gesture.rangeValue;
  gesture.input.value = gesture.inputValue;
}

function releaseSettingSliderGesture(range, gesture) {
  window.clearTimeout(gesture?.releaseTimer);
  if (!gesture) return;
  gesture.releaseTimer = window.setTimeout(() => {
    if (sliderGestures.get(range) === gesture) sliderGestures.delete(range);
  }, sliderLockReleaseMs);
}

function settingSliderBinding(input) {
  return input?.closest?.(".setting-field")?.querySelector(":scope > .setting-slider-control input[type='range']") || null;
}

function sliderNumber(value, fallback = 0) {
  const raw = text(value).trim();
  if (raw === "") return fallback;
  const number = Number(raw);
  return Number.isFinite(number) ? number : fallback;
}

export function createSettingsEnhancements({
  configForm,
  settingsSections = [],
  elements = {},
} = {}) {
  function syncSettingSlider(input) {
    if (!(input instanceof HTMLInputElement)) return;
    const range = settingSliderBinding(input);
    if (!range) return;
    const min = sliderNumber(range.min, 0);
    const max = sliderNumber(range.max, min);
    const value = sliderNumber(input.value, sliderNumber(range.value, min));
    range.value = String(Math.min(max, Math.max(min, value)));
  }

  function normalizeSettingSliderInput(input) {
    if (!(input instanceof HTMLInputElement)) return;
    const range = settingSliderBinding(input);
    if (!range) return;
    const min = sliderNumber(range.min, 0);
    const max = sliderNumber(range.max, min);
    const value = sliderNumber(input.value, sliderNumber(input.defaultValue || range.min, min));
    const clamped = Math.min(max, Math.max(min, value));
    input.value = String(clamped);
    range.value = String(clamped);
  }

  function syncSettingsSliders() {
    for (const input of configForm?.querySelectorAll(".setting-field.has-slider input[type='number']") || []) {
      syncSettingSlider(input);
    }
  }

  function normalizeSettingsSliders() {
    for (const input of configForm?.querySelectorAll(".setting-field.has-slider input[type='number']") || []) {
      normalizeSettingSliderInput(input);
    }
  }

  function beginSettingSliderGesture(range, input, event) {
    if (!shouldGuardSettingSliderGesture(event)) return;
    const point = settingSliderEventPoint(event);
    if (!point) return;
    const previous = sliderGestures.get(range);
    if (previous) window.clearTimeout(previous.releaseTimer);
    sliderGestures.set(range, {
      input,
      startX: point.x,
      startY: point.y,
      inputValue: input.value,
      rangeValue: range.value,
      mode: "pending",
      releaseTimer: 0,
    });
  }

  function commitSettingSliderRange(input, range, eventName = "input") {
    const previous = input.value;
    input.value = range.value;
    syncSettingSlider(input);
    if (input.value !== previous) {
      input.dispatchEvent(new Event(eventName, { bubbles: true }));
    }
  }

  function updateSettingSliderGesture(range, input, event) {
    const gesture = sliderGestures.get(range);
    if (!gesture || gesture.mode !== "pending") return;
    const point = settingSliderEventPoint(event);
    if (!point) return;
    const dx = Math.abs(point.x - gesture.startX);
    const dy = Math.abs(point.y - gesture.startY);
    if (dy > sliderGestureThresholdPx && dy > dx * sliderGestureAxisRatio) {
      gesture.mode = "locked";
      restoreSettingSliderGesture(range, gesture);
    } else if (dx > sliderGestureThresholdPx && dx > dy * sliderGestureAxisRatio) {
      gesture.mode = "adjusting";
      commitSettingSliderRange(input, range);
    }
  }

  function endSettingSliderGesture(range) {
    const gesture = sliderGestures.get(range);
    if (!gesture) return;
    if (gesture.mode === "adjusting") {
      sliderGestures.delete(range);
      return;
    }
    gesture.mode = "locked";
    restoreSettingSliderGesture(range, gesture);
    releaseSettingSliderGesture(range, gesture);
  }

  function settingSliderInputBlocked(range) {
    const gesture = sliderGestures.get(range);
    if (!gesture || gesture.mode === "adjusting") return false;
    restoreSettingSliderGesture(range, gesture);
    return true;
  }

  function enhanceSettingSlider(input, slider = {}) {
    if (!(input instanceof HTMLInputElement) || input.type !== "number") return;
    const field = input.closest(".setting-field");
    if (!field || field.querySelector(":scope > .setting-slider-control")) {
      syncSettingSlider(input);
      return;
    }

    const min = slider.min ?? input.min ?? 0;
    const max = slider.max ?? input.max ?? 100;
    const step = slider.step ?? input.step ?? 1;
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    input.inputMode = String(step).includes(".") || String(step) === "any" ? "decimal" : "numeric";
    input.classList.add("setting-number-input");
    field.classList.add("has-slider");

    const control = document.createElement("div");
    control.className = "setting-slider-control";

    const range = document.createElement("input");
    range.type = "range";
    range.min = String(min);
    range.max = String(max);
    range.step = String(step);
    range.setAttribute("aria-label", `${fieldCaptionNode(field)?.textContent || "数值"}滑块`);

    input.insertAdjacentElement("beforebegin", control);
    control.append(range, input);

    range.addEventListener("pointerdown", (event) => beginSettingSliderGesture(range, input, event), { passive: true });
    range.addEventListener("pointermove", (event) => updateSettingSliderGesture(range, input, event), { passive: true });
    range.addEventListener("pointerup", () => endSettingSliderGesture(range), { passive: true });
    range.addEventListener("pointercancel", () => endSettingSliderGesture(range), { passive: true });
    if (!window.PointerEvent) {
      range.addEventListener("touchstart", (event) => beginSettingSliderGesture(range, input, event), { passive: true });
      range.addEventListener("touchmove", (event) => updateSettingSliderGesture(range, input, event), { passive: true });
      range.addEventListener("touchend", () => endSettingSliderGesture(range), { passive: true });
      range.addEventListener("touchcancel", () => endSettingSliderGesture(range), { passive: true });
    }
    range.addEventListener("input", (event) => {
      event.stopPropagation();
      if (settingSliderInputBlocked(range)) return;
      commitSettingSliderRange(input, range);
    });
    range.addEventListener("change", (event) => {
      event.stopPropagation();
      if (settingSliderInputBlocked(range)) return;
      commitSettingSliderRange(input, range, "change");
    });
    input.addEventListener("input", () => syncSettingSlider(input));
    input.addEventListener("change", () => normalizeSettingSliderInput(input));
    syncSettingSlider(input);
  }

  function enhanceSettingField(input, meta = {}) {
    const field = input?.closest?.(".setting-field, .setting-switch");
    if (!field) return;
    const caption = fieldCaptionNode(field);
    const label = cleanSchemaLabel(meta.description || meta.title);
    if (caption && label) caption.textContent = label;

    const hintText = text(meta.hint).trim();
    if (hintText) {
      field.classList.add("has-schema-hint");
      const hint = ensureFieldHint(field);
      hint.textContent = hintText;
      if (input.id) {
        hint.id = `${input.id}Hint`;
        input.setAttribute("aria-describedby", hint.id);
      }
    }

    if (meta.slider) {
      enhanceSettingSlider(input, meta.slider);
    }
  }

  function applyTargetSchemaGuide(schema = {}) {
    const fields = schema.sections?.receiver?.fields || {};
    const pairs = [
      [fields.groups, elements.targetGroupsGuideTitle, elements.targetGroupsGuideHint],
      [fields.users, elements.targetUsersGuideTitle, elements.targetUsersGuideHint],
    ];
    for (const [meta, titleNode, hintNode] of pairs) {
      if (!meta) continue;
      const title = cleanSchemaLabel(meta.title || meta.description);
      const hint = text(meta.hint).trim();
      if (titleNode && title) titleNode.textContent = title;
      if (hintNode) hintNode.textContent = hint;
    }
  }

  function applySettingsSchemaEnhancements(data = {}) {
    const schema = data.schema_meta || {};
    if (!schema.sections && !schema.root) return;

    for (const section of settingsSections) {
      const key = section.dataset.settingsSection || "";
      const mapping = settingsSectionSchema[key] || {};
      const meta = schema.sections?.[mapping.section] || {};
      const title = cleanSchemaLabel(mapping.title || meta.description || meta.title);
      const noteText = text(mapping.hint || meta.hint || meta.description).trim();
      const titleNode = section.querySelector(":scope > .settings-section-title");
      if (titleNode && title) titleNode.textContent = title;
      if (noteText) ensureSectionNote(section).textContent = noteText;
    }

    for (const [id, mapping] of Object.entries(settingsFieldSchema)) {
      const input = elements[id] || document.getElementById(id);
      if (!input) continue;
      const meta = schemaMetaForMapping(schema, mapping);
      if (Object.keys(meta).length) enhanceSettingField(input, meta);
    }
    applyTargetSchemaGuide(schema);
    syncSettingsSliders();
  }

  return {
    applySettingsSchemaEnhancements,
    normalizeSettingsSliders,
    syncSettingSlider,
  };
}
