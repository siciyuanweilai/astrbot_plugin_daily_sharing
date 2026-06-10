import { text } from "./format.js?v=20260609-format";

const CONFIG_AUTO_SAVE_FAST_DELAY_MS = 360;
const CONFIG_AUTO_SAVE_TEXT_DELAY_MS = 900;
const CONFIG_AUTO_SAVE_RETRY_DELAY_MS = 600;
const PROVIDER_PROBE_TIMEOUT_MS = 900000;

export function createSettingsConfig({
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
} = {}) {
  function arrayToLines(value) {
    return Array.isArray(value) ? value.join("\n") : "";
  }

  function linesToArray(value) {
    return text(value)
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function setInputValue(input, value) {
    if (!input) return;
    input.value = value ?? "";
    syncSettingSlider(input);
  }

  function setInputChecked(input, value) {
    if (!input) return;
    input.checked = Boolean(value);
  }

  function setProviderProbeButtonsDisabled(value) {
    for (const button of [
      el.probeImageProviderButton,
      el.probeSelfieProviderButton,
      el.probeTtsProviderButton,
    ]) {
      if (button) button.disabled = Boolean(value);
    }
  }

  function setProviderProbeResult(message = "") {
    if (!el.providerProbeResult) return;
    el.providerProbeResult.textContent = message;
  }

  function providerProbePayload(kind) {
    if (kind === "selfie") {
      return {
        kind,
        apply: true,
        prompt: "a natural daily selfie photo in a cozy room, no text, no watermark",
      };
    }
    if (kind === "tts") {
      return {
        kind,
        apply: true,
        text: "每日分享语音测试",
        emotion: "neutral",
      };
    }
    return {
      kind: "image",
      apply: true,
      prompt: "a clean daily life photo of a cup on a desk, no text, no watermark",
    };
  }

  function providerProbeLabel(kind) {
    if (kind === "selfie") return "自拍";
    if (kind === "tts") return "语音";
    return "生图";
  }

  function formatProviderProbeResult(data = {}) {
    const result = data.result || {};
    const tool = text(result.tool_name).trim() || "未知工具";
    const argKeys = Object.keys(result.tool_args || {});
    const suffix = argKeys.length ? `，参数 ${argKeys.join(", ")}` : "";
    const errorNote = result.probe_error ? "，最终回复异常但已记录" : "";
    return `${providerProbeLabel(data.kind)}命中 LLM 工具：${tool}${suffix}${errorNote}`;
  }

  async function runProviderProbe(kind) {
    if (!bridge) return;
    window.clearTimeout(state.configAutoSaveTimer);
    state.configAutoSaveTimer = 0;
    setProviderProbeButtonsDisabled(true);
    setProviderProbeResult(`${providerProbeLabel(kind)}校准中...`);
    setNotice(`${providerProbeLabel(kind)}校准中，可能需要等待工具调用完成。`, "info", 7000);
    try {
      const data = await apiPost(
        "page/provider/probe",
        providerProbePayload(kind),
        PROVIDER_PROBE_TIMEOUT_MS,
      );
      if (data.config) {
        applyConfigData(data.config);
      }
      const message = formatProviderProbeResult(data);
      setProviderProbeResult(message);
      setNotice(`${message}，配置已保存。`, "success", 7000);
      await loadStatus({ quiet: true });
    } catch (error) {
      const message = error.message || `${providerProbeLabel(kind)}校准失败`;
      setProviderProbeResult(message);
      setNotice(message, "error", 9000);
    } finally {
      setProviderProbeButtonsDisabled(false);
    }
  }

  function configSection(name) {
    return state.configData?.sections?.[name] || {};
  }

  function setConfigDirty(value) {
    state.configDirty = Boolean(value);
    if (el.saveConfigButton) {
      el.saveConfigButton.disabled = !state.configDirty || state.configSaving || !state.configData;
    }
  }

  function configAutoSaveDelay(event) {
    const target = event?.target;
    if (event?.type === "change") return CONFIG_AUTO_SAVE_FAST_DELAY_MS;
    if (target instanceof HTMLSelectElement) return CONFIG_AUTO_SAVE_FAST_DELAY_MS;
    if (!(target instanceof HTMLInputElement)) return CONFIG_AUTO_SAVE_TEXT_DELAY_MS;
    if (target.type === "range" || target.type === "checkbox" || target.type === "radio") {
      return CONFIG_AUTO_SAVE_FAST_DELAY_MS;
    }
    return CONFIG_AUTO_SAVE_TEXT_DELAY_MS;
  }

  function scheduleConfigAutoSave(eventOrDelay) {
    window.clearTimeout(state.configAutoSaveTimer);
    const delay = typeof eventOrDelay === "number" ? eventOrDelay : configAutoSaveDelay(eventOrDelay);
    const changeSeq = state.configChangeSeq;
    state.configAutoSaveTimer = window.setTimeout(() => {
      state.configAutoSaveTimer = 0;
      void commitConfigSave({ auto: true, changeSeq });
    }, delay);
  }

  function handleConfigChanged(event) {
    if (state.configApplying || isTargetEditorEvent(event)) return;
    state.configChangeSeq += 1;
    setConfigDirty(true);
    scheduleConfigAutoSave(event);
  }

  function numberValue(input, fallback = 0) {
    const raw = text(input?.value).trim();
    return raw === "" ? fallback : Number(raw);
  }

  function populateDatalist(list, options = [], selected = "") {
    if (!list) return;
    const seen = new Set();
    const nodes = [];
    for (const option of options) {
      const value = text(option?.value).trim();
      if (!value || seen.has(value)) continue;
      seen.add(value);
      const node = document.createElement("option");
      node.value = value;
      node.label = text(option?.label).trim() || value;
      nodes.push(node);
    }
    const selectedValue = text(selected).trim();
    if (selectedValue && !seen.has(selectedValue)) {
      const node = document.createElement("option");
      node.value = selectedValue;
      node.label = selectedValue;
      nodes.push(node);
    }
    list.replaceChildren(...nodes);
    if (list === el.cfgLlmProviderOptions) syncSweetCombo(el.cfgLlmProviderId);
    if (list === el.cfgPersonaOptions) syncSweetCombo(el.cfgPersonaId);
  }

  function populateNewsSourceSelect(options = [], selected = "zhihu") {
    if (!el.cfgNewsFixedSource) return;
    const nextOptions = options.length
      ? options.map((source) => new Option(source.label || source.value, source.value))
      : [new Option("知乎热搜", "zhihu")];
    el.cfgNewsFixedSource.replaceChildren(...nextOptions);
    el.cfgNewsFixedSource.value = selected || nextOptions[0]?.value || "";
    syncSweetSelect(el.cfgNewsFixedSource);
  }

  function applyConfigData(data = {}) {
    state.configApplying = true;
    state.configData = data;
    const target = configSection("target");
    const basic = configSection("basic");
    const sequence = configSection("sequence");
    const context = configSection("context");
    const briefing = configSection("briefing");
    const qzone = configSection("qzone");
    const qzoneSequence = configSection("qzone_sequence");
    const content = configSection("content");
    const media = configSection("media");
    const weixin = configSection("weixin");
    const news = configSection("news");
    const llm = configSection("llm");

    setInputValue(el.cfgTargetGroups, arrayToLines(target.groups));
    setInputValue(el.cfgTargetUsers, arrayToLines(target.users));
    setInputValue(el.cfgBriefingGroups, arrayToLines(target.briefing_groups));
    setInputValue(el.cfgBriefingUsers, arrayToLines(target.briefing_users));
    setInputValue(el.cfgContactAliases, arrayToLines(target.contact_aliases));

    setInputChecked(el.cfgEnabled, data.enabled);
    setInputValue(el.cfgBasicTriggerMode, basic.trigger_mode || "cron");
    setInputValue(el.cfgBasicSharingCron, basic.sharing_cron || "twice");
    setInputValue(el.cfgBasicRandomPeriods, arrayToLines(basic.random_periods));
    setInputValue(el.cfgBasicCronDelay, basic.cron_random_delay ?? 0);
    setInputValue(el.cfgBasicSharingType, basic.sharing_type || "auto");
    setInputValue(el.cfgBasicRetentionDays, basic.data_retention_days ?? 60);
    setInputValue(el.cfgBasicDynamicDays, basic.dashboard_dynamic_days ?? 60);

    setInputValue(el.cfgDawnSequence, arrayToLines(sequence.dawn_sequence));
    setInputValue(el.cfgMorningSequence, arrayToLines(sequence.morning_sequence));
    setInputValue(el.cfgForenoonSequence, arrayToLines(sequence.forenoon_sequence));
    setInputValue(el.cfgNoonSequence, arrayToLines(sequence.noon_sequence));
    setInputValue(el.cfgAfternoonSequence, arrayToLines(sequence.afternoon_sequence));
    setInputValue(el.cfgEveningSequence, arrayToLines(sequence.evening_sequence));
    setInputValue(el.cfgNightSequence, arrayToLines(sequence.night_sequence));
    setInputValue(el.cfgLateNightSequence, arrayToLines(sequence.late_night_sequence));

    setInputValue(el.cfgReferenceHistoryCount, context.reference_history_count ?? 3);
    setInputChecked(el.cfgLifeContext, context.enable_life_context);
    setInputChecked(el.cfgLifeContextGroup, context.life_context_in_group);
    setInputChecked(el.cfgGroupSchedule, context.group_share_schedule);
    setInputChecked(el.cfgChatHistory, context.enable_chat_history);
    setInputChecked(el.cfgDeepHistory, context.enable_deep_history);
    setInputValue(el.cfgDeepHistoryHours, context.deep_history_hours ?? 24);
    setInputValue(el.cfgDeepHistoryMaxCount, context.deep_history_max_count ?? 50);
    setInputValue(el.cfgPrivateHistoryCount, context.private_history_count ?? 20);
    setInputValue(el.cfgGroupIntensityCount, context.group_intensity_check_count ?? 30);
    setInputValue(el.cfgGroupShareStrategy, context.group_share_strategy || "cautious");
    setInputChecked(el.cfgRecordMemory, context.record_sharing_to_memory);

    setInputChecked(el.cfgBriefing60s, briefing.enable_60s_news);
    setInputChecked(el.cfgBriefingAi, briefing.enable_ai_news);
    setInputChecked(el.cfgBriefingQzoneSync, briefing.sync_briefing_to_qzone);
    setInputValue(el.cfgBriefingCron, briefing.cron_briefing || "0 8 * * *");
    setInputValue(el.cfgBriefingDelay, briefing.briefing_cron_random_delay ?? 0);

    setInputChecked(el.cfgQzoneEnabled, qzone.enable_qzone);
    setInputValue(el.cfgQzoneTriggerMode, qzone.qzone_trigger_mode || "cron");
    setInputValue(el.cfgQzoneCron, qzone.qzone_cron || "0 20 * * *");
    setInputValue(el.cfgQzoneRandomPeriods, arrayToLines(qzone.qzone_random_periods));
    setInputValue(el.cfgQzoneSharingType, qzone.qzone_sharing_type || "auto");
    setInputChecked(el.cfgQzoneImage, qzone.qzone_enable_image);
    setInputChecked(el.cfgQzoneHotImage, qzone.qzone_attach_hot_news_image);
    setInputValue(el.cfgQzoneImageTypes, arrayToLines(qzone.qzone_image_enabled_types));

    setInputValue(el.cfgQzoneDawnSequence, arrayToLines(qzoneSequence.qzone_dawn_sequence));
    setInputValue(el.cfgQzoneMorningSequence, arrayToLines(qzoneSequence.qzone_morning_sequence));
    setInputValue(el.cfgQzoneForenoonSequence, arrayToLines(qzoneSequence.qzone_forenoon_sequence));
    setInputValue(el.cfgQzoneNoonSequence, arrayToLines(qzoneSequence.qzone_noon_sequence));
    setInputValue(el.cfgQzoneAfternoonSequence, arrayToLines(qzoneSequence.qzone_afternoon_sequence));
    setInputValue(el.cfgQzoneEveningSequence, arrayToLines(qzoneSequence.qzone_evening_sequence));
    setInputValue(el.cfgQzoneNightSequence, arrayToLines(qzoneSequence.qzone_night_sequence));
    setInputValue(el.cfgQzoneLateNightSequence, arrayToLines(qzoneSequence.qzone_late_night_sequence));

    setInputChecked(el.cfgKnowledgePrefix, content.show_knowledge_type_prefix);
    setInputChecked(el.cfgRecPrefix, content.show_rec_type_prefix);
    setInputValue(el.cfgKnowledgeCats, arrayToLines(content.knowledge_cats));
    setInputValue(el.cfgRecCats, arrayToLines(content.rec_cats));

    setInputChecked(el.cfgAiImage, media.enable_ai_image);
    setInputValue(el.cfgImageProvider, media.image_provider || "gitee_aiimg");
    setInputValue(el.cfgGenericImagePlugin, media.generic_image_plugin_name || "");
    setInputValue(el.cfgGenericImageMethod, media.generic_image_method_path || "");
    setInputValue(el.cfgGenericImagePromptArg, media.generic_image_prompt_arg || "prompt");
    setInputValue(el.cfgGenericImageExtraArgs, media.generic_image_extra_args || "");
    setInputValue(el.cfgGenericImageResultField, media.generic_image_result_field || "");
    setInputValue(el.cfgGenericImageEditMethod, media.generic_image_edit_method_path || "");
    setInputValue(el.cfgGenericImageEditPromptArg, media.generic_image_edit_prompt_arg || "prompt");
    setInputValue(el.cfgGenericImageEditExtraArgs, media.generic_image_edit_extra_args || "");
    setInputValue(el.cfgGenericImageRefKeys, media.generic_image_ref_keys || "bot_selfie,selfie,default");
    setInputChecked(el.cfgHotImage, media.attach_hot_news_image);
    setInputValue(el.cfgNewsImageCleanupMax, media.news_image_cleanup_max_count ?? 200);
    setInputChecked(el.cfgGiteeSelfieRef, media.use_gitee_selfie_ref);
    setInputChecked(el.cfgPriorityText, media.priority_text_over_schedule);
    setInputChecked(el.cfgAiVideo, media.enable_ai_video);
    setInputValue(el.cfgVideoProvider, media.video_provider || "gitee_aiimg");
    setInputValue(el.cfgGenericVideoPlugin, media.generic_video_plugin_name || "");
    setInputValue(el.cfgGenericVideoMethod, media.generic_video_method_path || "");
    setInputValue(el.cfgGenericVideoExtraArgs, media.generic_video_extra_args || "");
    setInputValue(el.cfgGenericVideoResultField, media.generic_video_result_field || "");
    setInputChecked(el.cfgSeparateMedia, media.separate_text_and_image);
    setInputValue(el.cfgSeparateDelay, media.separate_send_delay || "1.0-2.0");
    setInputChecked(el.cfgRecordImageDesc, media.record_image_description);
    setInputChecked(el.cfgAlwaysSelf, media.image_always_include_self);
    setInputChecked(el.cfgNeverSelf, media.image_never_include_self);
    setInputChecked(el.cfgTtsEnabled, media.enable_tts);
    setInputValue(el.cfgTtsProvider, media.tts_provider || "emotion_router");
    setInputValue(el.cfgGenericTtsPlugin, media.generic_tts_plugin_name || "");
    setInputValue(el.cfgGenericTtsMethod, media.generic_tts_method_path || "");
    setInputValue(el.cfgGenericTtsTextArg, media.generic_tts_text_arg || "text");
    setInputValue(el.cfgGenericTtsExtraArgs, media.generic_tts_extra_args || "");
    setInputValue(el.cfgGenericTtsResultField, media.generic_tts_result_field || "");
    setInputChecked(el.cfgAudioOnly, media.prefer_audio_only);
    setInputValue(el.cfgImageTypes, arrayToLines(media.image_enabled_types));
    setInputValue(el.cfgVideoTypes, arrayToLines(media.video_enabled_types));
    setInputValue(el.cfgTtsTypes, arrayToLines(media.tts_enabled_types));
    setInputValue(el.cfgAppearancePrompt, media.appearance_prompt || "");

    setInputChecked(el.cfgWeixinCompress, weixin.weixin_compress_images);
    setInputValue(el.cfgWeixinMaxSide, weixin.weixin_image_max_side ?? 4096);
    setInputValue(el.cfgWeixinMaxSize, weixin.weixin_image_max_size_kb ?? 10240);
    setInputValue(el.cfgWeixinTimeout, weixin.weixin_api_timeout_seconds ?? 60);
    setInputValue(el.cfgWeixinCleanupMax, weixin.weixin_temp_cleanup_max_count ?? 10);

    setInputChecked(el.cfgNewsApiEnabled, news.enable_news_api);
    setInputValue(el.cfgNewsApiKey, news.nycnm_api_key || "");
    setInputValue(el.cfgNewsMode, news.news_random_mode || "config");
    populateNewsSourceSelect(data.options?.news_sources || [], news.news_api_source || "zhihu");
    setInputValue(el.cfgNewsItemsCount, news.news_items_count ?? 5);
    setInputValue(el.cfgNewsShareCount, news.news_share_count || "1-2");
    setInputValue(el.cfgNewsApiTimeout, news.news_api_timeout ?? 30);
    setInputChecked(el.cfgNewsWebSearch, news.enable_tavily_search);
    setInputValue(el.cfgNewsRandomSources, arrayToLines(news.news_random_sources));

    populateDatalist(el.cfgLlmProviderOptions, data.options?.providers || [], llm.llm_provider_id);
    populateDatalist(el.cfgPersonaOptions, data.options?.personas || [], llm.persona_id);
    setInputValue(el.cfgLlmProviderId, llm.llm_provider_id || "");
    setInputValue(el.cfgLlmTimeout, llm.llm_timeout ?? 120);
    setInputChecked(el.cfgUsePersona, llm.use_persona);
    setInputValue(el.cfgPersonaId, llm.persona_id || "");

    applySettingsSchemaEnhancements(data);
    state.configApplying = false;
    setConfigDirty(false);
    syncSweetSelects();
  }

  function collectConfigPayload() {
    normalizeSettingsSliders();
    const targetPayload = {
      contact_aliases: linesToArray(el.cfgContactAliases?.value),
    };
    if (el.cfgTargetGroups) targetPayload.groups = linesToArray(el.cfgTargetGroups.value);
    if (el.cfgTargetUsers) targetPayload.users = linesToArray(el.cfgTargetUsers.value);
    if (el.cfgBriefingGroups) targetPayload.briefing_groups = linesToArray(el.cfgBriefingGroups.value);
    if (el.cfgBriefingUsers) targetPayload.briefing_users = linesToArray(el.cfgBriefingUsers.value);

    return {
      enabled: Boolean(el.cfgEnabled?.checked),
      sections: {
        target: targetPayload,
        basic: {
          trigger_mode: el.cfgBasicTriggerMode?.value || "cron",
          sharing_cron: text(el.cfgBasicSharingCron?.value).trim(),
          random_periods: linesToArray(el.cfgBasicRandomPeriods?.value),
          cron_random_delay: numberValue(el.cfgBasicCronDelay, 0),
          sharing_type: el.cfgBasicSharingType?.value || "auto",
          data_retention_days: numberValue(el.cfgBasicRetentionDays, 60),
          dashboard_dynamic_days: numberValue(el.cfgBasicDynamicDays, 60),
        },
        sequence: {
          dawn_sequence: linesToArray(el.cfgDawnSequence?.value),
          morning_sequence: linesToArray(el.cfgMorningSequence?.value),
          forenoon_sequence: linesToArray(el.cfgForenoonSequence?.value),
          noon_sequence: linesToArray(el.cfgNoonSequence?.value),
          afternoon_sequence: linesToArray(el.cfgAfternoonSequence?.value),
          evening_sequence: linesToArray(el.cfgEveningSequence?.value),
          night_sequence: linesToArray(el.cfgNightSequence?.value),
          late_night_sequence: linesToArray(el.cfgLateNightSequence?.value),
        },
        context: {
          reference_history_count: numberValue(el.cfgReferenceHistoryCount, 3),
          enable_life_context: Boolean(el.cfgLifeContext?.checked),
          life_context_in_group: Boolean(el.cfgLifeContextGroup?.checked),
          group_share_schedule: Boolean(el.cfgGroupSchedule?.checked),
          enable_chat_history: Boolean(el.cfgChatHistory?.checked),
          enable_deep_history: Boolean(el.cfgDeepHistory?.checked),
          deep_history_hours: numberValue(el.cfgDeepHistoryHours, 24),
          deep_history_max_count: numberValue(el.cfgDeepHistoryMaxCount, 50),
          private_history_count: numberValue(el.cfgPrivateHistoryCount, 20),
          group_intensity_check_count: numberValue(el.cfgGroupIntensityCount, 30),
          group_share_strategy: el.cfgGroupShareStrategy?.value || "cautious",
          record_sharing_to_memory: Boolean(el.cfgRecordMemory?.checked),
        },
        briefing: {
          enable_60s_news: Boolean(el.cfgBriefing60s?.checked),
          enable_ai_news: Boolean(el.cfgBriefingAi?.checked),
          sync_briefing_to_qzone: Boolean(el.cfgBriefingQzoneSync?.checked),
          cron_briefing: text(el.cfgBriefingCron?.value).trim(),
          briefing_cron_random_delay: numberValue(el.cfgBriefingDelay, 0),
        },
        qzone: {
          enable_qzone: Boolean(el.cfgQzoneEnabled?.checked),
          qzone_trigger_mode: el.cfgQzoneTriggerMode?.value || "cron",
          qzone_cron: text(el.cfgQzoneCron?.value).trim(),
          qzone_random_periods: linesToArray(el.cfgQzoneRandomPeriods?.value),
          qzone_sharing_type: el.cfgQzoneSharingType?.value || "auto",
          qzone_enable_image: Boolean(el.cfgQzoneImage?.checked),
          qzone_attach_hot_news_image: Boolean(el.cfgQzoneHotImage?.checked),
          qzone_image_enabled_types: linesToArray(el.cfgQzoneImageTypes?.value),
        },
        qzone_sequence: {
          qzone_dawn_sequence: linesToArray(el.cfgQzoneDawnSequence?.value),
          qzone_morning_sequence: linesToArray(el.cfgQzoneMorningSequence?.value),
          qzone_forenoon_sequence: linesToArray(el.cfgQzoneForenoonSequence?.value),
          qzone_noon_sequence: linesToArray(el.cfgQzoneNoonSequence?.value),
          qzone_afternoon_sequence: linesToArray(el.cfgQzoneAfternoonSequence?.value),
          qzone_evening_sequence: linesToArray(el.cfgQzoneEveningSequence?.value),
          qzone_night_sequence: linesToArray(el.cfgQzoneNightSequence?.value),
          qzone_late_night_sequence: linesToArray(el.cfgQzoneLateNightSequence?.value),
        },
        content: {
          show_knowledge_type_prefix: Boolean(el.cfgKnowledgePrefix?.checked),
          show_rec_type_prefix: Boolean(el.cfgRecPrefix?.checked),
          knowledge_cats: linesToArray(el.cfgKnowledgeCats?.value),
          rec_cats: linesToArray(el.cfgRecCats?.value),
        },
        media: {
          enable_ai_image: Boolean(el.cfgAiImage?.checked),
          image_provider: el.cfgImageProvider?.value || "gitee_aiimg",
          generic_image_plugin_name: text(el.cfgGenericImagePlugin?.value).trim(),
          generic_image_method_path: text(el.cfgGenericImageMethod?.value).trim(),
          generic_image_prompt_arg: text(el.cfgGenericImagePromptArg?.value).trim() || "prompt",
          generic_image_extra_args: text(el.cfgGenericImageExtraArgs?.value).trim(),
          generic_image_result_field: text(el.cfgGenericImageResultField?.value).trim(),
          generic_image_edit_method_path: text(el.cfgGenericImageEditMethod?.value).trim(),
          generic_image_edit_prompt_arg: text(el.cfgGenericImageEditPromptArg?.value).trim() || "prompt",
          generic_image_edit_extra_args: text(el.cfgGenericImageEditExtraArgs?.value).trim(),
          generic_image_ref_keys: text(el.cfgGenericImageRefKeys?.value).trim() || "bot_selfie,selfie,default",
          attach_hot_news_image: Boolean(el.cfgHotImage?.checked),
          news_image_cleanup_max_count: numberValue(el.cfgNewsImageCleanupMax, 200),
          use_gitee_selfie_ref: Boolean(el.cfgGiteeSelfieRef?.checked),
          priority_text_over_schedule: Boolean(el.cfgPriorityText?.checked),
          enable_ai_video: Boolean(el.cfgAiVideo?.checked),
          video_provider: el.cfgVideoProvider?.value || "gitee_aiimg",
          generic_video_plugin_name: text(el.cfgGenericVideoPlugin?.value).trim(),
          generic_video_method_path: text(el.cfgGenericVideoMethod?.value).trim(),
          generic_video_extra_args: text(el.cfgGenericVideoExtraArgs?.value).trim(),
          generic_video_result_field: text(el.cfgGenericVideoResultField?.value).trim(),
          separate_text_and_image: Boolean(el.cfgSeparateMedia?.checked),
          separate_send_delay: text(el.cfgSeparateDelay?.value).trim() || "1.0-2.0",
          record_image_description: Boolean(el.cfgRecordImageDesc?.checked),
          appearance_prompt: text(el.cfgAppearancePrompt?.value).trim(),
          image_always_include_self: Boolean(el.cfgAlwaysSelf?.checked),
          image_never_include_self: Boolean(el.cfgNeverSelf?.checked),
          enable_tts: Boolean(el.cfgTtsEnabled?.checked),
          tts_provider: el.cfgTtsProvider?.value || "emotion_router",
          generic_tts_plugin_name: text(el.cfgGenericTtsPlugin?.value).trim(),
          generic_tts_method_path: text(el.cfgGenericTtsMethod?.value).trim(),
          generic_tts_text_arg: text(el.cfgGenericTtsTextArg?.value).trim() || "text",
          generic_tts_extra_args: text(el.cfgGenericTtsExtraArgs?.value).trim(),
          generic_tts_result_field: text(el.cfgGenericTtsResultField?.value).trim(),
          prefer_audio_only: Boolean(el.cfgAudioOnly?.checked),
          image_enabled_types: linesToArray(el.cfgImageTypes?.value),
          video_enabled_types: linesToArray(el.cfgVideoTypes?.value),
          tts_enabled_types: linesToArray(el.cfgTtsTypes?.value),
        },
        weixin: {
          weixin_compress_images: Boolean(el.cfgWeixinCompress?.checked),
          weixin_image_max_side: numberValue(el.cfgWeixinMaxSide, 4096),
          weixin_image_max_size_kb: numberValue(el.cfgWeixinMaxSize, 10240),
          weixin_api_timeout_seconds: numberValue(el.cfgWeixinTimeout, 60),
          weixin_temp_cleanup_max_count: numberValue(el.cfgWeixinCleanupMax, 10),
        },
        news: {
          enable_news_api: Boolean(el.cfgNewsApiEnabled?.checked),
          nycnm_api_key: text(el.cfgNewsApiKey?.value).trim(),
          news_random_mode: el.cfgNewsMode?.value || "config",
          news_api_source: el.cfgNewsFixedSource?.value || "zhihu",
          news_items_count: numberValue(el.cfgNewsItemsCount, 5),
          news_share_count: text(el.cfgNewsShareCount?.value).trim() || "1-2",
          news_api_timeout: numberValue(el.cfgNewsApiTimeout, 30),
          enable_tavily_search: Boolean(el.cfgNewsWebSearch?.checked),
          news_random_sources: linesToArray(el.cfgNewsRandomSources?.value),
        },
        llm: {
          llm_provider_id: text(el.cfgLlmProviderId?.value).trim(),
          llm_timeout: numberValue(el.cfgLlmTimeout, 120),
          use_persona: Boolean(el.cfgUsePersona?.checked),
          persona_id: text(el.cfgPersonaId?.value).trim(),
        },
      },
    };
  }

  function setSettingsTab(tab, { scroll = true, sync = true } = {}) {
    state.settingsTab = tab || "target";
    for (const section of el.settingsSections) {
      section.classList.toggle("active", section.dataset.settingsSection === state.settingsTab);
    }
    if (sync) {
      closeSweetSelects();
      syncSweetSelects();
    }
    if (!scroll) return;
    const section = el.settingsSections.find((item) => item.dataset.settingsSection === state.settingsTab);
    if (section) {
      state.settingsScrollLockUntil = Date.now() + 720;
      const top = Math.max(0, section.getBoundingClientRect().top + window.scrollY - 16);
      window.scrollTo({ top, behavior: "smooth" });
    }
  }

  function settingsVisiblePixels(section, viewportTop, viewportBottom) {
    const rect = section.getBoundingClientRect();
    return Math.max(0, Math.min(rect.bottom, viewportBottom) - Math.max(rect.top, viewportTop));
  }

  function resolveSettingsTabFromViewport() {
    if (state.activeView !== "settings" || !el.settingsSections.length) return;
    if (Date.now() < state.settingsScrollLockUntil) return;
    const viewportTop = Math.min(180, Math.max(96, window.innerHeight * 0.14));
    const viewportBottom = window.innerHeight - 24;
    let active = state.settingsTab || el.settingsSections[0].dataset.settingsSection || "target";
    let activeVisible = 0;
    let best = active;
    let bestVisible = 0;

    for (const section of el.settingsSections) {
      const tab = section.dataset.settingsSection || "";
      const visible = settingsVisiblePixels(section, viewportTop, viewportBottom);
      if (tab === active) activeVisible = visible;
      if (visible > bestVisible) {
        best = tab || best;
        bestVisible = visible;
      }
    }

    if (best && best !== state.settingsTab && (activeVisible < 64 || bestVisible - activeVisible > 96)) {
      setSettingsTab(best, { scroll: false, sync: false });
    }
  }

  function updateSettingsTabFromScroll() {
    if (state.settingsScrollFrame) return;
    state.settingsScrollFrame = window.requestAnimationFrame(() => {
      state.settingsScrollFrame = 0;
      resolveSettingsTabFromViewport();
    });
  }

  async function loadConfig({ quiet = false } = {}) {
    if (!bridge) return;
    try {
      const data = await apiGet("page/config");
      applyConfigData(data);
      if (!quiet) setNotice("");
    } catch (error) {
      setNotice(error.message || "设置加载失败", "error");
    }
  }

  async function commitConfigSave({ auto = false, changeSeq = state.configChangeSeq } = {}) {
    if (!state.configDirty) return;
    if (state.configSaving) {
      state.configSaveQueued = true;
      return;
    }

    window.clearTimeout(state.configAutoSaveTimer);
    state.configAutoSaveTimer = 0;
    state.configSaving = true;
    state.configSaveQueued = false;
    setConfigDirty(true);
    if (el.reloadConfigButton) el.reloadConfigButton.disabled = true;

    let shouldQueueNextSave = false;
    try {
      const data = await apiPost("page/config", collectConfigPayload());
      shouldQueueNextSave = state.configSaveQueued || state.configChangeSeq !== changeSeq;
      if (shouldQueueNextSave) {
        state.configData = data;
        setConfigDirty(true);
      } else if (auto) {
        state.configData = data;
        setConfigDirty(false);
      } else {
        applyConfigData(data);
      }
      await loadStatus({ quiet: true });
      if (!auto) setNotice("设置已保存。", "success");
    } catch (error) {
      shouldQueueNextSave = false;
      setConfigDirty(true);
      setNotice(error.message || "设置保存失败", "error");
    } finally {
      state.configSaving = false;
      if (shouldQueueNextSave || state.configSaveQueued) {
        state.configSaveQueued = false;
        setConfigDirty(true);
        scheduleConfigAutoSave(CONFIG_AUTO_SAVE_RETRY_DELAY_MS);
      }
      if (el.reloadConfigButton) el.reloadConfigButton.disabled = false;
      setConfigDirty(state.configDirty);
    }
  }

  async function saveConfig(event) {
    event?.preventDefault();
    if (event && !event.submitter && isTargetEditorElement(document.activeElement)) return;
    await commitConfigSave({ auto: false, changeSeq: state.configChangeSeq });
  }

  function isTargetEditorElement(node) {
    return Boolean(node?.closest?.(".settings-target-editor"));
  }

  function isTargetEditorEvent(event) {
    return isTargetEditorElement(event?.target);
  }

  function bindProviderProbeEvents() {
    el.probeImageProviderButton?.addEventListener("click", () => {
      void runProviderProbe("image");
    });
    el.probeSelfieProviderButton?.addEventListener("click", () => {
      void runProviderProbe("selfie");
    });
    el.probeTtsProviderButton?.addEventListener("click", () => {
      void runProviderProbe("tts");
    });
  }

  return {
    bindProviderProbeEvents,
    handleConfigChanged,
    loadConfig,
    saveConfig,
    setSettingsTab,
    updateSettingsTabFromScroll,
  };
}
