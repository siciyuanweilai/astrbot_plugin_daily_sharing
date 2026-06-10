from datetime import datetime

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP
from .common import _PAGE_RECENT_SHARE_LIMIT


class DashboardRoutesMixin:
    """仪表盘路由能力。"""

    async def _build_page_status(self) -> dict:
        period = self.task_manager.get_curr_period()
        qzone_plugin = self.ctx_service._find_plugin("qzone")
        targets = await self._page_targets()
        jobs = self._page_jobs(targets)
        target_stats = await self.db.get_target_stats(days=30, briefing=False)
        briefing_target_stats = await self.db.get_target_stats(days=30, briefing=True)
        await self._enrich_page_targets(targets, target_stats, briefing_target_stats)
        history = await self._page_prepare_history_items(
            await self.db.get_recent_history(limit=_PAGE_RECENT_SHARE_LIMIT)
        )
        failures = await self._page_prepare_history_items(await self.db.get_recent_failures(limit=6))
        dynamic_days = self._page_dashboard_dynamic_days()
        history_summary = await self.db.get_history_summary()
        history_summary.update(await self.db.get_dashboard_dynamic_summary(days=dynamic_days))
        history_summary["dashboard_dynamic_days"] = dynamic_days
        media_page = await self._page_media_page(9, days=dynamic_days)
        preferences = await self._load_page_preferences()
        return {
            "ok": True,
            "data": {
                "enabled": bool(self.config.get("enable_auto_sharing", False)),
                "terminated": self._is_terminated,
                "busy": self._is_share_busy(global_scope=True),
                "preferences": preferences,
                "scheduler": {
                    "running": bool(self.scheduler.running),
                    "job_count": len(jobs),
                    "jobs": jobs,
                    "calendar": self._page_calendar(jobs),
                },
                "period": {
                    "key": period.value,
                    "range": self.task_manager.get_period_range_str(period),
                },
                "config": {
                    "trigger_mode": self.basic_conf.get("trigger_mode", "cron"),
                    "sharing_cron": self.basic_conf.get("sharing_cron", "twice"),
                    "sharing_type": self.basic_conf.get("sharing_type", "auto"),
                    "qzone_enabled": bool(self.qzone_conf.get("enable_qzone", False)),
                    "qzone_trigger_mode": self.qzone_conf.get(
                        "qzone_trigger_mode", "cron"
                    ),
                    "qzone_cron": self.qzone_conf.get("qzone_cron", "0 20 * * *"),
                    "ai_image_enabled": bool(self.image_conf.get("enable_ai_image", False)),
                    "ai_video_enabled": bool(self.image_conf.get("enable_ai_video", False)),
                    "tts_enabled": bool(self.tts_conf.get("enable_tts", False)),
                    "web_search_enabled": bool(
                        self.news_conf.get("enable_tavily_search", True)
                    ),
                    "briefing_60s": bool(
                        self.extra_shares_conf.get("enable_60s_news", False)
                    ),
                    "briefing_ai": bool(
                        self.extra_shares_conf.get("enable_ai_news", False)
                    ),
                    "briefing_qzone_sync": bool(
                        self.extra_shares_conf.get("sync_briefing_to_qzone", False)
                    ),
                },
                "targets": targets,
                "states": await self._page_states(),
                "qzone": {
                    "available": bool(qzone_plugin and hasattr(qzone_plugin, "service")),
                    "configured": bool(self.qzone_conf.get("enable_qzone", False)),
                },
                "news_sources": [
                    {
                        "key": key,
                        "name": str(value.get("name") or key),
                    }
                    for key, value in NEWS_SOURCE_MAP.items()
                ],
                "history": history,
                "history_summary": history_summary,
                "failures": failures,
                "media": media_page["items"],
                "media_limit": media_page["limit"],
                "media_has_more": media_page["has_more"],
                "progress": self.task_manager.get_share_progress_snapshot(),
                "target_stats": target_stats,
                "briefing_target_stats": briefing_target_stats,
                "actions": self._page_recent_actions(),
                "recent_shares": self._page_recent_shares(history, targets),
            },
        }

    async def page_status(self):
        return await self._page_json(self._build_page_status)

    async def page_config(self):
        async def handler():
            body = await self._page_json_body()
            saved = bool(body)
            if saved:
                previous_enabled = bool(self.config.get("enable_auto_sharing", False))
                self._apply_page_config_payload(body)
                next_enabled = bool(self.config.get("enable_auto_sharing", False))
                await self._save_config_and_refresh_runtime(
                    clear_pending_when_disabled=previous_enabled and not next_enabled
                )

            data = self._page_config_payload()
            if saved:
                status = await self._build_page_status()
                data["status"] = status["data"]
            return {
                "ok": True,
                "data": data,
                "message": "设置已保存" if saved else "",
            }

        return await self._page_json(handler)

    async def page_preferences(self):
        async def handler():
            preferences = await self._load_page_preferences()
            body = await self._page_json_body()
            should_save = False
            if "sakura_enabled" in body:
                preferences["sakura_enabled"] = bool(body.get("sakura_enabled"))
                should_save = True
            if "active_view" in body:
                active_view = str(body.get("active_view") or "").strip()
                preferences["active_view"] = active_view if active_view in {"dashboard", "settings"} else "dashboard"
                should_save = True
            if should_save:
                preferences = await self._save_page_preferences(preferences)
            return {"ok": True, "data": {"preferences": preferences}}

        return await self._page_json(handler)

    async def page_history(self):
        async def handler():
            params = await self._page_query_params()
            try:
                limit = min(max(int(params.get("limit") or 30), 1), 100)
            except Exception:
                limit = 30
            target_id = str(params.get("target_id") or "").strip()
            history = (
                await self.db.get_recent_history_by_target(target_id, limit=limit)
                if target_id
                else await self.db.get_recent_history(limit=limit)
            )
            return {"ok": True, "data": {"items": await self._page_prepare_history_items(history)}}

        return await self._page_json(handler)

    async def page_failures(self):
        async def handler():
            params = await self._page_query_params()
            try:
                limit = min(max(int(params.get("limit") or 20), 1), 100)
            except Exception:
                limit = 20
            return {
                "ok": True,
                "data": {
                    "items": await self._page_prepare_history_items(
                        await self.db.get_recent_failures(limit=limit)
                    )
                },
            }

        return await self._page_json(handler)

    async def page_failures_clear(self):
        async def handler():
            deleted = await self.db.clear_failures()
            status = await self._build_page_status()
            return {
                "ok": True,
                "data": {
                    **status["data"],
                    "deleted": deleted,
                },
                "message": f"已清空 {deleted} 条失败记录",
            }

        return await self._page_json(handler)

    async def page_media(self):
        async def handler():
            params = await self._page_query_params()
            try:
                limit = min(max(int(params.get("limit") or 24), 1), 100)
            except Exception:
                limit = 24
            return {
                "ok": True,
                "data": await self._page_media_page(
                    limit,
                    media_kind=params.get("kind") or "all",
                    sharing_type=params.get("type") or "all",
                ),
            }

        return await self._page_json(handler)

    async def page_media_view(self):
        async def handler():
            body = await self._page_json_body()
            history_id = body.get("history_id")
            if history_id is None:
                raise RuntimeError("缺少 history_id")
            history_id = int(history_id)

            item = await self.db.get_history_by_id(history_id)
            if not item:
                raise RuntimeError("未找到媒体记录")
            if self._page_media_kind(item) != "image":
                raise RuntimeError("该媒体不是图片")

            return {
                "ok": True,
                "data": {
                    "id": item.get("id"),
                    "media_type": "image",
                    **self._page_view_image_payload(item, history_id),
                },
            }

        return await self._page_json(handler, self._page_media_cache_headers())

    async def page_toggle(self):
        async def handler():
            body = await self._page_json_body()
            enable = bool(body.get("enable"))
            self.config["enable_auto_sharing"] = enable
            await self._save_config_and_refresh_runtime(
                clear_pending_when_disabled=not enable
            )
            status = await self._build_page_status()
            return {
                "ok": True,
                "data": status["data"],
                "message": "自动分享已启用" if enable else "自动分享已停用",
            }

        return await self._page_json(handler)

    async def _run_page_action(
        self,
        run_id: str,
        target: str,
        share_type: str,
        news_source: str,
        specific_target: str = "",
    ) -> None:
        run = self._page_action_runs.get(run_id)
        if not run:
            return
        try:
            force_type = self._page_share_type(share_type)
            source_key = self._page_news_source(news_source)
            success_message = "分享成功"
            async with self._lock:
                if target == "qzone":
                    ok = await self.task_manager.execute_qzone_share(
                        force_type=force_type,
                        news_source=source_key,
                        source_type="manual",
                    )
                    if not ok:
                        raise RuntimeError("QQ 空间分享失败，请查看日志")
                    success_message = "QQ 空间分享成功"
                elif target == "briefing":
                    await self.task_manager.execute_briefing_share(source_type="manual")
                    success_message = "早报分享成功"
                else:
                    target_scope = {
                        "broadcast_groups": "groups",
                        "broadcast_users": "users",
                    }.get(target, "all")
                    await self.task_manager.execute_share(
                        force_type=force_type,
                        news_source=source_key,
                        specific_target=specific_target or None,
                        target_scope=target_scope,
                        source_type="manual",
                    )
                    success_message = {
                        "broadcast_groups": "群聊分享成功",
                        "broadcast_users": "私聊分享成功",
                    }.get(target, "分享成功")
            run["status"] = "done"
            run["message"] = success_message
        except Exception as exc:
            logger.exception("[每日分享] 仪表盘手动分享失败: %s", exc)
            run["status"] = "error"
            run["message"] = str(exc) or "分享失败"
        finally:
            run["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self._page_prune_actions()

    async def page_run(self):
        async def handler():
            body = await self._page_json_body()
            target = str(body.get("target") or "broadcast").strip()
            if target not in {"broadcast", "broadcast_groups", "broadcast_users", "qzone", "briefing"}:
                raise RuntimeError(f"不支持的分享目标: {target}")
            if self._is_share_busy(global_scope=True):
                raise RuntimeError("已有任务正在分享，请稍后再试")

            share_type = str(body.get("share_type") or "auto").strip()
            news_source = str(body.get("news_source") or "").strip()
            self._page_share_type(share_type)
            self._page_news_source(news_source)
            specific_target, specific_kind = self._page_specific_share_target(
                target,
                body.get("specific_target"),
            )
            target_label = (
                await self._resolve_page_target_label(specific_target, specific_kind)
                if specific_target
                else ""
            )

            self._page_action_seq += 1
            run_id = f"dashboard-{self._page_action_seq}"
            run = {
                "id": run_id,
                "target": target,
                "target_id": specific_target,
                "target_label": target_label,
                "kind": specific_kind,
                "share_type": share_type or "auto",
                "news_source": news_source,
                "source_type": "manual",
                "source_label": "手动",
                "status": "running",
                "message": "分享中",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": "",
            }
            self._page_action_runs[run_id] = run
            self._track_task(
                self._run_page_action(run_id, target, share_type, news_source, specific_target)
            )
            return {"ok": True, "data": {"run": run}, "message": "任务已开始"}

        return await self._page_json(handler)

    async def _run_page_retry_action(self, run_id: str, history_item: dict) -> None:
        run = self._page_action_runs.get(run_id)
        if not run:
            return
        try:
            target_id = str(history_item.get("target_id") or "").strip()
            raw_type = str(history_item.get("type") or "auto").strip()
            force_type = self._page_share_type(raw_type)
            async with self._lock:
                if target_id == "qzone_broadcast":
                    await self.task_manager.execute_qzone_share(
                        force_type=force_type,
                        source_type="manual",
                    )
                elif target_id in {"briefing", "briefing_broadcast"}:
                    await self.task_manager.execute_briefing_share(source_type="manual")
                elif target_id == "global":
                    await self.task_manager.execute_share(
                        force_type=force_type,
                        source_type="manual",
                    )
                else:
                    await self.task_manager.execute_share(
                        force_type=force_type,
                        specific_target=target_id,
                        source_type="manual",
                    )
            run["status"] = "done"
            run["message"] = "重试完成"
        except Exception as exc:
            logger.exception("[每日分享] 仪表盘重试失败: %s", exc)
            run["status"] = "error"
            run["message"] = str(exc) or "重试失败"
        finally:
            run["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self._page_prune_actions()

    async def page_retry(self):
        async def handler():
            body = await self._page_json_body()
            history_id = body.get("history_id")
            if history_id is None:
                raise RuntimeError("缺少 history_id")
            item = await self.db.get_history_by_id(int(history_id))
            if not item:
                raise RuntimeError("未找到失败记录")
            if item.get("success"):
                raise RuntimeError("该记录不是失败记录，无需重试")
            if self._is_share_busy(global_scope=True):
                raise RuntimeError("已有任务正在分享，请稍后再试")

            self._page_action_seq += 1
            run_id = f"retry-{self._page_action_seq}"
            run = {
                "id": run_id,
                "target": "retry",
                "target_id": item.get("target_id", ""),
                "target_label": await self._resolve_page_target_label(
                    item.get("target_id", ""),
                    item.get("kind", ""),
                ),
                "kind": item.get("kind", ""),
                "share_type": item.get("type") or "auto",
                "news_source": "",
                "source_type": "manual",
                "source_label": "手动",
                "history_id": item.get("id"),
                "status": "running",
                "message": "重试中",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": "",
            }
            self._page_action_runs[run_id] = run
            self._track_task(self._run_page_retry_action(run_id, item))
            return {"ok": True, "data": {"run": run}, "message": "重试任务已开始"}

        return await self._page_json(handler)

    async def page_targets_update(self):
        async def handler():
            body = await self._page_json_body()
            receiver_conf = self.config.setdefault("receiver", {})
            extra_conf = self.config.setdefault("extra_shares", {})

            receiver_conf["groups"] = self._normalize_page_target_list(body.get("groups", []))
            receiver_conf["users"] = self._normalize_page_target_list(body.get("users", []))
            extra_conf["briefing_groups"] = self._normalize_page_target_list(
                body.get("briefing_groups", []),
                briefing=True,
            )
            extra_conf["briefing_users"] = self._normalize_page_target_list(
                body.get("briefing_users", []),
                briefing=True,
            )

            self.receiver_conf = receiver_conf
            self.extra_shares_conf = extra_conf
            await self._save_config_and_refresh_runtime()

            status = await self._build_page_status()
            return {"ok": True, "data": status["data"], "message": "目标配置已保存"}

        return await self._page_json(handler)

