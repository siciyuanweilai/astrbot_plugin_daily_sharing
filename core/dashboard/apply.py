import re

from ..config import NEWS_SOURCE_MAP
from .common import (
    _PAGE_BASIC_SEQUENCE_DEFAULTS,
    _PAGE_CONTEXT_STRATEGY_OPTIONS,
    _PAGE_NEWS_RANDOM_MODE_OPTIONS,
    _PAGE_QZONE_SEQUENCE_DEFAULTS,
    _PAGE_SHARE_TYPE_OPTIONS,
    _PAGE_TRIGGER_MODE_OPTIONS,
)


class DashboardConfigApplyMixin:
    """设置页配置提交处理。"""

    @staticmethod
    def _page_payload_section(sections: dict, name: str) -> dict:
        section = sections.get(name) if isinstance(sections, dict) else {}
        return section if isinstance(section, dict) else {}

    @staticmethod
    def _page_apply_bool_fields(target: dict, source: dict, keys: tuple) -> None:
        for key in keys:
            if key in source:
                target[key] = bool(source.get(key))

    def _apply_page_config_payload(self, body: dict) -> None:
        sections = body.get("sections") if isinstance(body.get("sections"), dict) else body
        if "enabled" in body:
            self.config["enable_auto_sharing"] = bool(body.get("enabled"))

        target_body = self._page_payload_section(sections, "target")
        receiver = self.config.setdefault("receiver", {})
        extra = self.config.setdefault("extra_shares", {})
        if "groups" in target_body:
            receiver["groups"] = self._normalize_page_target_list(target_body.get("groups", []))
        if "users" in target_body:
            receiver["users"] = self._normalize_page_target_list(target_body.get("users", []))
        if "briefing_groups" in target_body:
            extra["briefing_groups"] = self._normalize_page_target_list(
                target_body.get("briefing_groups", []),
                briefing=True,
            )
        if "briefing_users" in target_body:
            extra["briefing_users"] = self._normalize_page_target_list(
                target_body.get("briefing_users", []),
                briefing=True,
            )
        if "contact_aliases" in target_body:
            aliases = self._page_contact_aliases_value(target_body.get("contact_aliases"))
            self.config["contact_aliases"] = aliases
            self.contact_aliases = aliases

        basic_body = self._page_payload_section(sections, "basic")
        basic = self.config.setdefault("basic_conf", {})
        if "trigger_mode" in basic_body:
            basic["trigger_mode"] = self._page_choice_value(
                basic_body.get("trigger_mode"), _PAGE_TRIGGER_MODE_OPTIONS, "cron", "全局触发模式"
            )
        if "random_periods" in basic_body:
            basic["random_periods"] = self._page_random_periods_value(
                basic_body.get("random_periods"), ["08:00-10:00", "19:00-21:00"], "全局随机时段"
            )
        if basic.get("trigger_mode") == "random_period" and not basic.get("random_periods"):
            raise RuntimeError("全局随机时段不能为空")
        if "sharing_cron" in basic_body:
            basic["sharing_cron"] = self._page_cron_value(
                basic_body.get("sharing_cron"), "twice", "全局定时"
            )
        if "cron_random_delay" in basic_body:
            basic["cron_random_delay"] = self._page_int_value(
                basic_body.get("cron_random_delay"), 0, min_value=0, max_value=60
            )
        if "sharing_type" in basic_body:
            basic["sharing_type"] = self._page_choice_value(
                basic_body.get("sharing_type"), _PAGE_SHARE_TYPE_OPTIONS, "auto", "全局分享类型"
            )
        if "data_retention_days" in basic_body:
            basic["data_retention_days"] = self._page_int_value(
                basic_body.get("data_retention_days"), 60, min_value=7, max_value=365
            )
        if "dashboard_dynamic_days" in basic_body:
            basic["dashboard_dynamic_days"] = self._page_int_value(
                basic_body.get("dashboard_dynamic_days"), 60, min_value=0, max_value=365
            )

        sequence_body = self._page_payload_section(sections, "sequence")
        for key, default in _PAGE_BASIC_SEQUENCE_DEFAULTS.items():
            if key in sequence_body:
                basic[key] = self._page_sequence_value(sequence_body.get(key), default, f"全局{key}")

        briefing_body = self._page_payload_section(sections, "briefing")
        extra = self.config.setdefault("extra_shares", {})
        self._page_apply_bool_fields(
            extra,
            briefing_body,
            ("enable_60s_news", "enable_ai_news", "sync_briefing_to_qzone"),
        )
        if "cron_briefing" in briefing_body:
            extra["cron_briefing"] = self._page_cron_value(
                briefing_body.get("cron_briefing"), "0 8 * * *", "早报定时"
            )
        if "briefing_cron_random_delay" in briefing_body:
            extra["briefing_cron_random_delay"] = self._page_int_value(
                briefing_body.get("briefing_cron_random_delay"), 0, min_value=0, max_value=60
            )

        qzone_body = self._page_payload_section(sections, "qzone")
        qzone = self.config.setdefault("qzone_conf", {})
        self._page_apply_bool_fields(
            qzone,
            qzone_body,
            ("enable_qzone", "qzone_enable_image", "qzone_attach_hot_news_image"),
        )
        if "qzone_trigger_mode" in qzone_body:
            qzone["qzone_trigger_mode"] = self._page_choice_value(
                qzone_body.get("qzone_trigger_mode"), _PAGE_TRIGGER_MODE_OPTIONS, "cron", "空间触发模式"
            )
        if "qzone_random_periods" in qzone_body:
            qzone["qzone_random_periods"] = self._page_random_periods_value(
                qzone_body.get("qzone_random_periods"), ["08:00-10:00", "19:00-21:00"], "空间随机时段"
            )
        if qzone.get("qzone_trigger_mode") == "random_period" and not qzone.get("qzone_random_periods"):
            raise RuntimeError("空间随机时段不能为空")
        if "qzone_cron" in qzone_body:
            qzone["qzone_cron"] = self._page_cron_value(
                qzone_body.get("qzone_cron"), "0 20 * * *", "空间定时"
            )
        if "qzone_sharing_type" in qzone_body:
            qzone["qzone_sharing_type"] = self._page_choice_value(
                qzone_body.get("qzone_sharing_type"), _PAGE_SHARE_TYPE_OPTIONS, "auto", "空间分享类型"
            )
        if "qzone_image_enabled_types" in qzone_body:
            qzone["qzone_image_enabled_types"] = self._page_type_list_value(
                qzone_body.get("qzone_image_enabled_types"), "空间配图类型"
            )

        qzone_sequence_body = self._page_payload_section(sections, "qzone_sequence")
        for key, default in _PAGE_QZONE_SEQUENCE_DEFAULTS.items():
            if key in qzone_sequence_body:
                qzone[key] = self._page_sequence_value(qzone_sequence_body.get(key), default, f"空间{key}")

        content_body = self._page_payload_section(sections, "content")
        content = self.config.setdefault("content_library", {})
        if "knowledge_cats" in content_body:
            content["knowledge_cats"] = self._page_list_value(
                content_body.get("knowledge_cats"), max_items=300, item_max_len=500
            )
        if "rec_cats" in content_body:
            content["rec_cats"] = self._page_list_value(
                content_body.get("rec_cats"), max_items=300, item_max_len=500
            )
        self._page_apply_bool_fields(
            content,
            content_body,
            ("show_knowledge_type_prefix", "show_rec_type_prefix"),
        )

        media_body = self._page_payload_section(sections, "media")
        image = self.config.setdefault("image_conf", {})
        tts = self.config.setdefault("tts_conf", {})
        self._page_apply_bool_fields(
            image,
            media_body,
            (
                "enable_ai_image",
                "attach_hot_news_image",
                "use_gitee_selfie_ref",
                "priority_text_over_schedule",
                "enable_ai_video",
                "separate_text_and_image",
                "record_image_description",
                "image_always_include_self",
                "image_never_include_self",
            ),
        )
        if "news_image_cleanup_max_count" in media_body:
            image["news_image_cleanup_max_count"] = self._page_int_value(
                media_body.get("news_image_cleanup_max_count"), 200, min_value=0, max_value=1000
            )
        if "image_enabled_types" in media_body:
            image["image_enabled_types"] = self._page_type_list_value(
                media_body.get("image_enabled_types"), "配图类型"
            )
        if "video_enabled_types" in media_body:
            image["video_enabled_types"] = self._page_type_list_value(
                media_body.get("video_enabled_types"), "视频类型"
            )
        if "separate_send_delay" in media_body:
            image["separate_send_delay"] = self._page_delay_range_value(
                media_body.get("separate_send_delay"), "1.0-2.0"
            )
        if "appearance_prompt" in media_body:
            image["appearance_prompt"] = self._page_clean_text(
                media_body.get("appearance_prompt"), max_len=2000
            )
        self._page_apply_bool_fields(tts, media_body, ("enable_tts", "prefer_audio_only"))
        if "tts_enabled_types" in media_body:
            tts["tts_enabled_types"] = self._page_type_list_value(
                media_body.get("tts_enabled_types"), "语音类型"
            )

        weixin_body = self._page_payload_section(sections, "weixin")
        self._page_apply_bool_fields(image, weixin_body, ("weixin_compress_images",))
        if "weixin_image_max_side" in weixin_body:
            image["weixin_image_max_side"] = self._page_int_value(
                weixin_body.get("weixin_image_max_side"), 4096, min_value=1600, max_value=8192
            )
        if "weixin_image_max_size_kb" in weixin_body:
            image["weixin_image_max_size_kb"] = self._page_int_value(
                weixin_body.get("weixin_image_max_size_kb"), 10240, min_value=512, max_value=40960
            )
        if "weixin_api_timeout_seconds" in weixin_body:
            image["weixin_api_timeout_seconds"] = self._page_int_value(
                weixin_body.get("weixin_api_timeout_seconds"), 60, min_value=1, max_value=180
            )
        if "weixin_temp_cleanup_max_count" in weixin_body:
            image["weixin_temp_cleanup_max_count"] = self._page_int_value(
                weixin_body.get("weixin_temp_cleanup_max_count"), 10, min_value=0, max_value=100
            )

        context_body = self._page_payload_section(sections, "context")
        context_conf = self.config.setdefault("context_conf", {})
        self._page_apply_bool_fields(
            context_conf,
            context_body,
            (
                "enable_life_context",
                "life_context_in_group",
                "group_share_schedule",
                "enable_chat_history",
                "enable_deep_history",
                "record_sharing_to_memory",
            ),
        )
        if "reference_history_count" in context_body:
            context_conf["reference_history_count"] = self._page_int_value(
                context_body.get("reference_history_count"), 3, min_value=0, max_value=10
            )
        if "deep_history_hours" in context_body:
            context_conf["deep_history_hours"] = self._page_int_value(
                context_body.get("deep_history_hours"), 24, min_value=1, max_value=168
            )
        if "deep_history_max_count" in context_body:
            context_conf["deep_history_max_count"] = self._page_int_value(
                context_body.get("deep_history_max_count"), 50, min_value=20, max_value=200
            )
        if "private_history_count" in context_body:
            context_conf["private_history_count"] = self._page_int_value(
                context_body.get("private_history_count"), 20, min_value=5, max_value=100
            )
        if "group_intensity_check_count" in context_body:
            context_conf["group_intensity_check_count"] = self._page_int_value(
                context_body.get("group_intensity_check_count"), 30, min_value=10, max_value=100
            )
        if "group_share_strategy" in context_body:
            context_conf["group_share_strategy"] = self._page_choice_value(
                context_body.get("group_share_strategy"),
                _PAGE_CONTEXT_STRATEGY_OPTIONS,
                "cautious",
                "群聊分享策略",
            )

        news_body = self._page_payload_section(sections, "news")
        news = self.config.setdefault("news_conf", {})
        self._page_apply_bool_fields(news, news_body, ("enable_news_api", "enable_tavily_search"))
        if "nycnm_api_key" in news_body:
            news["nycnm_api_key"] = self._page_clean_text(news_body.get("nycnm_api_key"), max_len=200)
        if "news_random_mode" in news_body:
            news["news_random_mode"] = self._page_choice_value(
                news_body.get("news_random_mode"), _PAGE_NEWS_RANDOM_MODE_OPTIONS, "config", "新闻源模式"
            )
        if "news_api_source" in news_body:
            news["news_api_source"] = self._page_choice_value(
                news_body.get("news_api_source"), set(NEWS_SOURCE_MAP), "zhihu", "固定新闻源"
            )
        if "news_random_sources" in news_body:
            news["news_random_sources"] = self._page_news_source_list_value(
                news_body.get("news_random_sources"), "随机新闻源"
            )
        if news.get("news_random_mode") in {"config", "time_based"} and not news.get("news_random_sources"):
            raise RuntimeError("随机新闻源列表不能为空")
        if "news_items_count" in news_body:
            news["news_items_count"] = self._page_int_value(
                news_body.get("news_items_count"), 5, min_value=1, max_value=20
            )
        if "news_share_count" in news_body:
            share_count = self._page_clean_text(news_body.get("news_share_count"), max_len=16)
            if not re.match(r"^\d{1,2}(?:-\d{1,2})?$", share_count):
                raise RuntimeError("新闻分享条数格式应为数字或范围，例如 1-2")
            news["news_share_count"] = share_count
        if "news_api_timeout" in news_body:
            news["news_api_timeout"] = self._page_int_value(
                news_body.get("news_api_timeout"), 30, min_value=1, max_value=60
            )

        llm_body = self._page_payload_section(sections, "llm")
        llm = self.config.setdefault("llm_conf", {})
        if "llm_provider_id" in llm_body:
            llm["llm_provider_id"] = self._page_clean_text(llm_body.get("llm_provider_id"), max_len=160)
        if "llm_timeout" in llm_body:
            llm["llm_timeout"] = self._page_int_value(
                llm_body.get("llm_timeout"), 120, min_value=1, max_value=180
            )
        self._page_apply_bool_fields(llm, llm_body, ("use_persona",))
        if "persona_id" in llm_body:
            llm["persona_id"] = self._page_clean_text(llm_body.get("persona_id"), max_len=160)
