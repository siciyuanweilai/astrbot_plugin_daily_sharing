from ..config import DEFAULT_KNOWLEDGE_CATS, DEFAULT_REC_CATS


class DashboardConfigRefreshMixin:
    """配置保存后刷新运行时引用和定时任务。"""

    def _refresh_config_refs(self) -> None:
        self.basic_conf = self.config.setdefault("basic_conf", {})
        self.image_conf = self.config.setdefault("image_conf", {})
        self.tts_conf = self.config.setdefault("tts_conf", {})
        self.llm_conf = self.config.setdefault("llm_conf", {})
        self.qzone_conf = self.config.setdefault("qzone_conf", {})
        self.receiver_conf = self.config.setdefault("receiver", {})
        self.extra_shares_conf = self.config.setdefault("extra_shares", {})
        self.context_conf = self.config.setdefault("context_conf", {})
        self.news_conf = self.config.setdefault("news_conf", {})
        self.contact_aliases = self.config.get("contact_aliases", [])

        self.ctx_service.config = self.config
        self.ctx_service.life_conf = self.context_conf
        self.ctx_service.history_conf = self.context_conf
        self.ctx_service.memory_conf = self.context_conf
        self.ctx_service.image_conf = self.image_conf
        self.ctx_service.tts_conf = self.tts_conf
        self.ctx_service.llm_conf = self.llm_conf

        self.news_service.config = self.config
        self.news_service.conf = self.news_conf

        self.image_service.config = self.config
        self.image_service.img_conf = self.image_conf
        self.image_service.llm_conf = self.llm_conf

        self.content_service.config = self.config
        self.content_service.content_lib_conf = self.config.setdefault("content_library", {})
        raw_knowledge = self.content_service.content_lib_conf.get("knowledge_cats", DEFAULT_KNOWLEDGE_CATS)
        raw_rec = self.content_service.content_lib_conf.get("rec_cats", DEFAULT_REC_CATS)
        self.content_service.knowledge_cats = self.content_service._parse_category_config(raw_knowledge or DEFAULT_KNOWLEDGE_CATS)
        self.content_service.rec_cats = self.content_service._parse_category_config(raw_rec or DEFAULT_REC_CATS)
        self.content_service.basic_conf = self.basic_conf
        self.content_service.news_conf = self.news_conf
        self.content_service.llm_conf = self.llm_conf
        self.content_service.context_conf = self.context_conf
        try:
            self.content_service.dedup_days = int(self.basic_conf.get("data_retention_days", 60))
        except Exception:
            self.content_service.dedup_days = 60

        self.task_manager.basic_conf = self.basic_conf
        self.task_manager.extra_shares_conf = self.extra_shares_conf
        self.task_manager.qzone_conf = self.qzone_conf
        self.task_manager.image_conf = self.image_conf
        self.task_manager.tts_conf = self.tts_conf
        self.task_manager.context_conf = self.context_conf
        self.task_manager.receiver_conf = self.receiver_conf

        self.command_handler.config = self.config
        self.command_handler.basic_conf = self.basic_conf
        self.command_handler.extra_shares_conf = self.extra_shares_conf
        self.command_handler.qzone_conf = self.qzone_conf

    async def _rebuild_scheduler_after_config(self, *, clear_pending_when_disabled: bool = False) -> None:
        self.scheduler.remove_all_jobs()
        if self.config.get("enable_auto_sharing", False):
            self.task_manager.setup_tasks()
        else:
            if clear_pending_when_disabled:
                await self.task_manager.clear_pending_delay_jobs()
            self.task_manager.setup_cleanup_tasks()
        if self.scheduler.get_jobs() and not self.scheduler.running:
            self.scheduler.start()

    async def _save_config_and_refresh_runtime(self, *, clear_pending_when_disabled: bool = False) -> None:
        self._refresh_config_refs()
        await self._save_config_file()
        await self._rebuild_scheduler_after_config(
            clear_pending_when_disabled=clear_pending_when_disabled
        )
