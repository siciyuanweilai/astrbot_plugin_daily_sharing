from ..config import DEFAULT_KNOWLEDGE_CATS, DEFAULT_REC_CATS
from .common import _PAGE_BASIC_SEQUENCE_DEFAULTS, _PAGE_QZONE_SEQUENCE_DEFAULTS


class DashboardConfigPayloadMixin:
    """设置页配置数据组装。"""

    def _page_config_payload(self) -> dict:
        basic = self.config.setdefault("basic_conf", {})
        extra = self.config.setdefault("extra_shares", {})
        qzone = self.config.setdefault("qzone_conf", {})
        content = self.config.setdefault("content_library", {})
        image = self.config.setdefault("image_conf", {})
        tts = self.config.setdefault("tts_conf", {})
        news = self.config.setdefault("news_conf", {})
        receiver = self.config.setdefault("receiver", {})
        context_conf = self.config.setdefault("context_conf", {})
        llm = self.config.setdefault("llm_conf", {})
        return {
            "enabled": bool(self.config.get("enable_auto_sharing", False)),
            "sections": {
                "target": {
                    "groups": list(receiver.get("groups") or []),
                    "users": list(receiver.get("users") or []),
                    "briefing_groups": list(extra.get("briefing_groups") or []),
                    "briefing_users": list(extra.get("briefing_users") or []),
                    "contact_aliases": list(self.config.get("contact_aliases") or []),
                },
                "basic": {
                    "trigger_mode": basic.get("trigger_mode", "cron"),
                    "random_periods": list(basic.get("random_periods") or ["08:00-10:00", "19:00-21:00"]),
                    "sharing_cron": basic.get("sharing_cron", "twice"),
                    "cron_random_delay": int(basic.get("cron_random_delay", 0) or 0),
                    "sharing_type": basic.get("sharing_type", "auto"),
                    "data_retention_days": int(basic.get("data_retention_days", 60) or 60),
                    "dashboard_dynamic_days": int(basic.get("dashboard_dynamic_days", 60) or 60),
                },
                "sequence": {
                    key: list(basic.get(key) or default)
                    for key, default in _PAGE_BASIC_SEQUENCE_DEFAULTS.items()
                },
                "briefing": {
                    "enable_60s_news": bool(extra.get("enable_60s_news", False)),
                    "enable_ai_news": bool(extra.get("enable_ai_news", False)),
                    "sync_briefing_to_qzone": bool(extra.get("sync_briefing_to_qzone", False)),
                    "cron_briefing": extra.get("cron_briefing", "0 8 * * *"),
                    "briefing_cron_random_delay": int(extra.get("briefing_cron_random_delay", 0) or 0),
                },
                "qzone": {
                    "enable_qzone": bool(qzone.get("enable_qzone", False)),
                    "qzone_trigger_mode": qzone.get("qzone_trigger_mode", "cron"),
                    "qzone_random_periods": list(qzone.get("qzone_random_periods") or ["08:00-10:00", "19:00-21:00"]),
                    "qzone_cron": qzone.get("qzone_cron", "0 20 * * *"),
                    "qzone_sharing_type": qzone.get("qzone_sharing_type", "auto"),
                    "qzone_enable_image": bool(qzone.get("qzone_enable_image", False)),
                    "qzone_attach_hot_news_image": bool(qzone.get("qzone_attach_hot_news_image", True)),
                    "qzone_image_enabled_types": list(qzone.get("qzone_image_enabled_types") or ["greeting", "mood"]),
                },
                "qzone_sequence": {
                    key: list(qzone.get(key) or default)
                    for key, default in _PAGE_QZONE_SEQUENCE_DEFAULTS.items()
                },
                "content": {
                    "knowledge_cats": self._page_category_lines(
                        content.get("knowledge_cats"), DEFAULT_KNOWLEDGE_CATS
                    ),
                    "rec_cats": self._page_category_lines(
                        content.get("rec_cats"), DEFAULT_REC_CATS
                    ),
                    "show_knowledge_type_prefix": bool(content.get("show_knowledge_type_prefix", True)),
                    "show_rec_type_prefix": bool(content.get("show_rec_type_prefix", True)),
                },
                "media": {
                    "enable_ai_image": bool(image.get("enable_ai_image", False)),
                    "image_provider": image.get("image_provider", "gitee_aiimg"),
                    "generic_image_plugin_name": str(image.get("generic_image_plugin_name", "") or ""),
                    "generic_image_method_path": str(image.get("generic_image_method_path", "") or ""),
                    "generic_image_prompt_arg": str(image.get("generic_image_prompt_arg", "prompt") or "prompt"),
                    "generic_image_extra_args": str(image.get("generic_image_extra_args", "") or ""),
                    "generic_image_result_field": str(image.get("generic_image_result_field", "") or ""),
                    "generic_image_edit_method_path": str(image.get("generic_image_edit_method_path", "") or ""),
                    "generic_image_edit_prompt_arg": str(image.get("generic_image_edit_prompt_arg", "prompt") or "prompt"),
                    "generic_image_edit_extra_args": str(image.get("generic_image_edit_extra_args", "") or ""),
                    "generic_image_ref_keys": str(image.get("generic_image_ref_keys", "bot_selfie,selfie,default") or "bot_selfie,selfie,default"),
                    "attach_hot_news_image": bool(image.get("attach_hot_news_image", True)),
                    "news_image_cleanup_max_count": int(image.get("news_image_cleanup_max_count", 200) or 0),
                    "use_gitee_selfie_ref": bool(image.get("use_gitee_selfie_ref", False)),
                    "priority_text_over_schedule": bool(image.get("priority_text_over_schedule", True)),
                    "enable_ai_video": bool(image.get("enable_ai_video", False)),
                    "video_provider": image.get("video_provider", "gitee_aiimg"),
                    "generic_video_plugin_name": str(image.get("generic_video_plugin_name", "") or ""),
                    "generic_video_method_path": str(image.get("generic_video_method_path", "") or ""),
                    "generic_video_extra_args": str(image.get("generic_video_extra_args", "") or ""),
                    "generic_video_result_field": str(image.get("generic_video_result_field", "") or ""),
                    "image_enabled_types": list(image.get("image_enabled_types") or ["greeting", "mood", "knowledge", "recommendation"]),
                    "video_enabled_types": list(image.get("video_enabled_types") or ["greeting", "mood"]),
                    "separate_text_and_image": bool(image.get("separate_text_and_image", True)),
                    "separate_send_delay": str(image.get("separate_send_delay", "1.0-2.0") or "1.0-2.0"),
                    "record_image_description": bool(image.get("record_image_description", True)),
                    "appearance_prompt": str(image.get("appearance_prompt", "") or ""),
                    "image_always_include_self": bool(image.get("image_always_include_self", False)),
                    "image_never_include_self": bool(image.get("image_never_include_self", False)),
                    "enable_tts": bool(tts.get("enable_tts", False)),
                    "tts_provider": tts.get("tts_provider", "emotion_router"),
                    "generic_tts_plugin_name": str(tts.get("generic_tts_plugin_name", "") or ""),
                    "generic_tts_method_path": str(tts.get("generic_tts_method_path", "") or ""),
                    "generic_tts_text_arg": str(tts.get("generic_tts_text_arg", "text") or "text"),
                    "generic_tts_extra_args": str(tts.get("generic_tts_extra_args", "") or ""),
                    "generic_tts_result_field": str(tts.get("generic_tts_result_field", "") or ""),
                    "tts_enabled_types": list(tts.get("tts_enabled_types") or ["greeting", "mood"]),
                    "prefer_audio_only": bool(tts.get("prefer_audio_only", False)),
                },
                "weixin": {
                    "weixin_compress_images": bool(image.get("weixin_compress_images", True)),
                    "weixin_image_max_side": int(image.get("weixin_image_max_side", 4096) or 4096),
                    "weixin_image_max_size_kb": int(image.get("weixin_image_max_size_kb", 10240) or 10240),
                    "weixin_api_timeout_seconds": int(image.get("weixin_api_timeout_seconds", 60) or 60),
                    "weixin_temp_cleanup_max_count": int(image.get("weixin_temp_cleanup_max_count", 10) or 0),
                },
                "context": {
                    "reference_history_count": int(context_conf.get("reference_history_count", 3) or 0),
                    "enable_life_context": bool(context_conf.get("enable_life_context", True)),
                    "life_context_in_group": bool(context_conf.get("life_context_in_group", True)),
                    "group_share_schedule": bool(context_conf.get("group_share_schedule", False)),
                    "enable_chat_history": bool(context_conf.get("enable_chat_history", True)),
                    "enable_deep_history": bool(context_conf.get("enable_deep_history", True)),
                    "deep_history_hours": int(context_conf.get("deep_history_hours", 24) or 24),
                    "deep_history_max_count": int(context_conf.get("deep_history_max_count", 50) or 50),
                    "private_history_count": int(context_conf.get("private_history_count", 20) or 20),
                    "group_intensity_check_count": int(context_conf.get("group_intensity_check_count", 30) or 30),
                    "group_share_strategy": context_conf.get("group_share_strategy", "cautious"),
                    "record_sharing_to_memory": bool(context_conf.get("record_sharing_to_memory", True)),
                },
                "news": {
                    "enable_news_api": bool(news.get("enable_news_api", True)),
                    "nycnm_api_key": str(news.get("nycnm_api_key", "") or ""),
                    "news_random_mode": news.get("news_random_mode", "config"),
                    "news_api_source": news.get("news_api_source", "zhihu"),
                    "news_random_sources": list(news.get("news_random_sources") or ["zhihu", "weibo", "bili"]),
                    "news_items_count": int(news.get("news_items_count", 5) or 5),
                    "news_share_count": str(news.get("news_share_count", "1-2") or "1-2"),
                    "news_api_timeout": int(news.get("news_api_timeout", 30) or 30),
                    "enable_tavily_search": bool(news.get("enable_tavily_search", True)),
                },
                "llm": {
                    "llm_provider_id": str(llm.get("llm_provider_id", "") or ""),
                    "llm_timeout": int(llm.get("llm_timeout", 120) or 120),
                    "use_persona": bool(llm.get("use_persona", True)),
                    "persona_id": str(llm.get("persona_id", "") or ""),
                },
            },
            "options": self._page_config_options(),
            "schema_meta": self._page_config_schema_meta(),
        }
