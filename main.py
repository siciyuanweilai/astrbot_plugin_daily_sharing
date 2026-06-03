import asyncio
import importlib
import json
import random
import os
import re 
from typing import Optional
from pathlib import Path
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig

from .config import TimePeriod, SharingType, NEWS_SOURCE_MAP
from .core.constants import CMD_CN_MAP, SOURCE_CN_MAP, TYPE_CN_MAP
from .core.news import NewsService
from .core.image import ImageService
from .core.content import ContentService
from .core.context import ContextService
from .core.db import DatabaseManager 
from .core.tasks import TaskManager
from .core.commands import CommandHandler
from .core.args import find_invalid_non_news_args

try:
    from quart import jsonify as _quart_jsonify
    from quart import request as _quart_request
except Exception:
    _quart_jsonify = None
    _quart_request = None

class DailySharingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config 
        self.scheduler = AsyncIOScheduler()
        
        # 配置引用
        self.basic_conf = self.config.get("basic_conf", {})
        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.qzone_conf = self.config.get('qzone_conf', {})
        self.receiver_conf = self.config.get("receiver", {})
        self.extra_shares_conf = self.config.get("extra_shares", {})
        self.context_conf = self.config.get("context_conf", {})
        self.contact_aliases = self.config.get("contact_aliases", [])
        
        # 分享内容记录条数 
        self.history_limit = 100
        
        # 锁与防抖
        self._lock = asyncio.Lock()
        self._target_locks = {}
        self._last_share_time = None
        
        # 生命周期标志位 
        self._is_terminated = False
        
        # 缓存 Adapter ID 
        self._cached_adapter_id = None 
        self._cached_qq_adapter_id = None
        self._cached_weixin_adapter_id = None

        # 临时降级第一个模型缓存
        self._temp_fallback_provider = None
        self._temp_fallback_until = 0.0
        self._fallback_ttl_seconds = 600

        # 任务追踪 (用于生命周期清理)
        self._bg_tasks = set()
        
        # 数据路径
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_daily_sharing")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置文件路径
        config_dir = self.data_dir.parent.parent / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = config_dir / "astrbot_plugin_daily_sharing_config.json"
        
        # 数据库初始化
        self.db = DatabaseManager(self.data_dir)
        
        # 初始化服务层
        self.ctx_service = ContextService(context, config)
        self.news_service = NewsService(config)
        self.image_service = ImageService(context, config, self._call_llm_wrapper)
        
        # 初始化内容服务
        self.content_service = ContentService(
            config, 
            self._call_llm_wrapper, 
            context,
            self.db, 
            self.news_service
        )
        
        # 核心逻辑解耦器
        self.task_manager = TaskManager(self)
        self.command_handler = CommandHandler(self)
        self._page_action_seq = 0
        self._page_action_runs = {}
        self._register_page_web_apis()
        
        # 启动延迟初始化 Bot 缓存的任务
        self._track_task(self._delayed_init_bots())

    def _register_page_web_apis(self) -> None:
        routes = (
            ("page/status", self.page_status, ["GET"], "Daily sharing dashboard status"),
            ("page/history", self.page_history, ["GET"], "Daily sharing history"),
            ("page/toggle", self.page_toggle, ["POST"], "Daily sharing toggle"),
            ("page/run", self.page_run, ["POST"], "Daily sharing manual run"),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/astrbot_plugin_daily_sharing/{endpoint}",
                handler,
                methods,
                desc,
            )

    async def _page_response(self, payload: dict, status: int = 200):
        if _quart_jsonify is None:
            return payload
        response = _quart_jsonify(payload)
        response.status_code = status
        return response

    async def _page_json(self, callback):
        try:
            payload = await callback()
            status = 200
        except Exception as exc:
            logger.exception("[DailySharing] dashboard api failed: %s", exc)
            payload = {
                "ok": False,
                "error": {"message": str(exc) or "请求失败"},
            }
            status = 200
        return await self._page_response(payload, status)

    async def _page_query_params(self) -> dict:
        if _quart_request is None:
            return {}
        args = getattr(_quart_request, "args", {}) or {}
        return {str(key): value for key, value in args.items()}

    async def _page_json_body(self) -> dict:
        if _quart_request is None:
            return {}
        try:
            data = await _quart_request.get_json(silent=True)
        except TypeError:
            data = await _quart_request.get_json()
        return data if isinstance(data, dict) else {}

    def _page_job_display_name(self, job_id: str, job_name: str = "") -> str:
        job_id = str(job_id or "")
        job_name = str(job_name or "")
        static_names = {
            "auto_share": "全局定时分享",
            "qzone_share": "QQ空间定时分享",
            "share_briefing": "早报分享",
            "weixin_temp_cleanup": "微信临时图片清理",
            "daily_random_scheduler": "每日随机分享排程",
            "daily_qzone_random_scheduler": "每日QQ空间随机排程",
            "delayed_auto_share": "全局分享延迟执行",
            "delayed_qzone_share": "QQ空间延迟执行",
            "delayed_briefing_share": "早报延迟执行",
            "resume_auto_share": "恢复全局延迟分享",
            "resume_qzone_share": "恢复QQ空间延迟分享",
            "resume_briefing_share": "恢复早报延迟分享",
        }
        if job_id in static_names:
            return static_names[job_id]

        patterns = (
            (r"^random_share_(\d+)$", "今日随机分享 {}", 1),
            (r"^qzone_random_share_(\d+)$", "今日QQ空间随机分享 {}", 1),
            (r"^custom_share_(.+)$", "独立定时分享 {}", 0),
            (r"^delayed_custom_share_(.+)$", "独立分享延迟执行 {}", 0),
            (r"^resume_custom_share_(.+)$", "恢复独立延迟分享 {}", 0),
        )
        for pattern, label, offset in patterns:
            match = re.match(pattern, job_id)
            if not match:
                continue
            value = match.group(1)
            if offset:
                try:
                    value = str(int(value) + offset)
                except ValueError:
                    pass
            return label.format(value)

        return job_name or job_id or "任务"

    def _page_jobs(self) -> list:
        jobs = []
        for job in self.scheduler.get_jobs():
            next_run_time = getattr(job, "next_run_time", None)
            job_id = str(getattr(job, "id", ""))
            job_name = str(getattr(job, "name", ""))
            jobs.append(
                {
                    "id": job_id,
                    "name": job_name,
                    "display_name": self._page_job_display_name(job_id, job_name),
                    "trigger": str(getattr(job, "trigger", "")),
                    "next_run_time": (
                        next_run_time.isoformat(timespec="seconds")
                        if next_run_time
                        else ""
                    ),
                }
            )
        return sorted(jobs, key=lambda item: item["next_run_time"] or "9999")

    def _page_target_item(self, target_id: str, conf, kind: str) -> dict:
        cron = None
        sequence = None
        if isinstance(conf, dict):
            cron = conf.get("cron")
            sequence = conf.get("seq")
        elif conf:
            sequence = str(conf)
        return {
            "id": str(target_id),
            "kind": kind,
            "cron": cron or "",
            "sequence": sequence or "auto",
        }

    def _page_targets(self) -> dict:
        r_groups = self.task_manager._parse_targets_config(
            self.receiver_conf.get("groups", [])
        )
        r_users = self.task_manager._parse_targets_config(
            self.receiver_conf.get("users", [])
        )
        briefing_groups = [
            self._page_target_item(item, None, "briefing_group")
            for item in self.extra_shares_conf.get("briefing_groups", [])
            if str(item or "").strip()
        ]
        briefing_users = [
            self._page_target_item(item, None, "briefing_user")
            for item in self.extra_shares_conf.get("briefing_users", [])
            if str(item or "").strip()
        ]
        groups = [
            self._page_target_item(target_id, conf, "group")
            for target_id, conf in r_groups.items()
        ]
        users = [
            self._page_target_item(target_id, conf, "user")
            for target_id, conf in r_users.items()
        ]
        return {
            "groups": groups,
            "users": users,
            "briefing_groups": briefing_groups,
            "briefing_users": briefing_users,
            "summary": {
                "share_targets": len(groups) + len(users),
                "briefing_targets": len(briefing_groups) + len(briefing_users),
            },
        }

    async def _page_states(self) -> dict:
        states = {}
        for key in ("global", "qzone", "briefing"):
            value = await self.db.get_state(key, {})
            states[key] = value if isinstance(value, dict) else {}
        return states

    def _page_recent_actions(self) -> list:
        runs = sorted(
            self._page_action_runs.values(),
            key=lambda item: item.get("started_at", ""),
            reverse=True,
        )
        return runs[:10]

    def _page_prune_actions(self) -> None:
        runs = sorted(
            self._page_action_runs.values(),
            key=lambda item: item.get("started_at", ""),
            reverse=True,
        )
        self._page_action_runs = {item["id"]: item for item in runs[:20]}

    def _page_share_type(self, value):
        raw = str(value or "auto").strip()
        if not raw or raw.lower() == "auto" or raw == "自动":
            return None
        if raw in CMD_CN_MAP:
            return CMD_CN_MAP[raw]
        try:
            return SharingType(raw)
        except ValueError as exc:
            raise RuntimeError(f"不支持的分享类型: {raw}") from exc

    def _page_news_source(self, value: str):
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw in NEWS_SOURCE_MAP:
            return raw
        if raw in SOURCE_CN_MAP:
            return SOURCE_CN_MAP[raw]
        raise RuntimeError(f"不支持的新闻源: {raw}")

    async def _build_page_status(self) -> dict:
        period = self.task_manager.get_curr_period()
        qzone_plugin = self.ctx_service._find_plugin("qzone")
        jobs = self._page_jobs()
        targets = self._page_targets()
        history = await self.db.get_recent_history(limit=8)
        return {
            "ok": True,
            "data": {
                "enabled": bool(self.config.get("enable_auto_sharing", False)),
                "terminated": self._is_terminated,
                "busy": self._is_share_busy(global_scope=True),
                "scheduler": {
                    "running": bool(self.scheduler.running),
                    "job_count": len(jobs),
                    "jobs": jobs,
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
                "actions": self._page_recent_actions(),
            },
        }

    async def page_status(self):
        return await self._page_json(self._build_page_status)

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
            return {"ok": True, "data": {"items": history}}

        return await self._page_json(handler)

    async def page_toggle(self):
        async def handler():
            body = await self._page_json_body()
            enable = bool(body.get("enable"))
            self.config["enable_auto_sharing"] = enable
            await self._save_config_file()
            self.scheduler.remove_all_jobs()
            if enable:
                self.task_manager.setup_tasks()
                if self.scheduler.get_jobs() and not self.scheduler.running:
                    self.scheduler.start()
            else:
                await self.task_manager.clear_pending_delay_jobs()
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
    ) -> None:
        run = self._page_action_runs.get(run_id)
        if not run:
            return
        try:
            force_type = self._page_share_type(share_type)
            source_key = self._page_news_source(news_source)
            async with self._lock:
                if target == "qzone":
                    await self.task_manager.execute_qzone_share(
                        force_type=force_type,
                        news_source=source_key,
                    )
                elif target == "briefing":
                    await self.task_manager.execute_briefing_share()
                else:
                    await self.task_manager.execute_share(
                        force_type=force_type,
                        news_source=source_key,
                    )
            run["status"] = "done"
            run["message"] = "执行完成"
        except Exception as exc:
            logger.exception("[DailySharing] dashboard action failed: %s", exc)
            run["status"] = "error"
            run["message"] = str(exc) or "执行失败"
        finally:
            run["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self._page_prune_actions()

    async def page_run(self):
        async def handler():
            body = await self._page_json_body()
            target = str(body.get("target") or "broadcast").strip()
            if target not in {"broadcast", "qzone", "briefing"}:
                raise RuntimeError(f"不支持的执行目标: {target}")
            if self._is_share_busy(global_scope=True):
                raise RuntimeError("已有分享任务正在执行，请稍后再试")

            share_type = str(body.get("share_type") or "auto").strip()
            news_source = str(body.get("news_source") or "").strip()
            self._page_share_type(share_type)
            self._page_news_source(news_source)

            self._page_action_seq += 1
            run_id = f"dashboard-{self._page_action_seq}"
            run = {
                "id": run_id,
                "target": target,
                "share_type": share_type or "auto",
                "news_source": news_source,
                "status": "running",
                "message": "执行中",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": "",
            }
            self._page_action_runs[run_id] = run
            self._track_task(
                self._run_page_action(run_id, target, share_type, news_source)
            )
            return {"ok": True, "data": {"run": run}, "message": "任务已开始"}

        return await self._page_json(handler)

    def _normalize_contact_aliases(self) -> dict:
        raw_aliases = self.contact_aliases
        aliases = {}
        if isinstance(raw_aliases, list):
            for item in raw_aliases:
                item_s = str(item or "").strip().replace("：", ":", 1)
                if ":" not in item_s:
                    continue
                key_s, value_s = [part.strip() for part in item_s.split(":", 1)]
                if key_s and value_s:
                    aliases[key_s] = value_s
        return aliases

    def _serialize_contact_aliases(self, aliases: dict) -> list:
        return [f"{key}:{value}" for key, value in aliases.items() if key and value]

    def _target_alias_keys(self, target_uid: str, event: AstrMessageEvent = None) -> list:
        keys = []
        target_s = str(target_uid or "").strip()
        if target_s:
            keys.append(target_s)
            _, real_id = self.ctx_service._parse_umo(target_s)
            if real_id:
                keys.append(real_id)
        if event:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if origin:
                keys.append(origin)
                _, origin_real_id = self.ctx_service._parse_umo(origin)
                if origin_real_id:
                    keys.append(origin_real_id)
            try:
                sender_id = str(event.get_sender_id() or "").strip()
                if sender_id:
                    keys.append(sender_id)
            except Exception as e:
                logger.debug(f"[DailySharing] 读取发送者 ID 失败: {e}")
        return list(dict.fromkeys(k for k in keys if k))

    def get_contact_alias(self, target_uid: str, event: AstrMessageEvent = None) -> str:
        aliases = self._normalize_contact_aliases()
        for key in self._target_alias_keys(target_uid, event):
            alias = str(aliases.get(key, "") or "").strip()
            if alias:
                return alias
        return ""

    def set_contact_alias(self, target_uid: str, alias: str, event: AstrMessageEvent = None) -> str:
        aliases = self._normalize_contact_aliases()
        keys = self._target_alias_keys(target_uid, event)
        save_key = ""
        for key in keys:
            if not self.task_manager._is_full_umo(key):
                save_key = key
                break
        if not save_key and keys:
            _, real_id = self.ctx_service._parse_umo(keys[0])
            save_key = real_id or keys[0]
        if not save_key:
            return ""
        aliases[save_key] = str(alias or "").strip()
        serialized_aliases = self._serialize_contact_aliases(aliases)
        self.config["contact_aliases"] = serialized_aliases
        self.contact_aliases = serialized_aliases
        return save_key

    def remove_contact_alias(self, target_uid: str, event: AstrMessageEvent = None) -> list:
        aliases = self._normalize_contact_aliases()
        removed = []
        for key in self._target_alias_keys(target_uid, event):
            if key in aliases:
                aliases.pop(key, None)
                removed.append(key)
        serialized_aliases = self._serialize_contact_aliases(aliases)
        self.config["contact_aliases"] = serialized_aliases
        self.contact_aliases = serialized_aliases
        return removed

    def _track_task(self, coro):
        """创建并追踪后台任务，避免插件重载后留下未管理任务。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)

        def _cleanup(done_task):
            self._bg_tasks.discard(done_task)
            if self._is_terminated or done_task.cancelled():
                return
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            if exc:
                logger.error(
                    f"[DailySharing] 后台任务异常: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__)
                )

        task.add_done_callback(_cleanup)
        return task

    def _get_share_lock(self, target_uid: str = None, *, global_scope: bool = False):
        """获取分享锁：广播/空间/定时用全局锁，当前会话分享用会话级锁。"""
        if global_scope or not target_uid:
            return self._lock
        key = str(target_uid or "").strip()
        lock = self._target_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._target_locks[key] = lock
        return lock

    def _is_share_busy(self, target_uid: str = None, *, global_scope: bool = False) -> bool:
        if global_scope:
            return self._lock.locked() or any(lock.locked() for lock in self._target_locks.values())
        if self._lock.locked():
            return True
        return self._get_share_lock(target_uid).locked()

    def _release_idle_share_lock(self, target_uid: str = None):
        key = str(target_uid or "").strip()
        lock = self._target_locks.get(key)
        if lock and not lock.locked():
            self._target_locks.pop(key, None)

    def _inject_qzone_client(self, qzone_plugin):
        """尝试为 QQ空间 插件注入 CQHttp 客户端，解决自动任务时没有 client 的报错"""
        try:
            if qzone_plugin and hasattr(qzone_plugin, "cfg") and not qzone_plugin.cfg.client:
                if self.ctx_service.bot_map:
                    # 优先寻找 aiocqhttp 适配器
                    aiocqhttp_bot = None
                    for pid, bot in self.ctx_service.bot_map.items():
                        if "aiocqhttp" in pid.lower():
                            aiocqhttp_bot = bot
                            break
                    bot_client = aiocqhttp_bot or list(self.ctx_service.bot_map.values())[0]
                    if bot_client:
                        qzone_plugin.cfg.client = bot_client
                        logger.debug(f"[DailySharing] QQ空间插件注入客户端成功！")
        except Exception as e:
            logger.warning(f"[DailySharing] QQ空间插件注入客户端失败: {e}")        

    def _remember_event_adapter(self, event: AstrMessageEvent):
        """记录最近见过的平台 ID，供纯 ID 配置选择 QQ/微信适配器。"""
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if not origin:
                return

            adapter_id = origin.split(":", 1)[0].strip()
            if adapter_id:
                self._cached_adapter_id = adapter_id
                if (
                    self.ctx_service._is_weixin_oc_event(event)
                    and self.ctx_service._is_weixin_platform(origin)
                ):
                    self._cached_weixin_adapter_id = adapter_id
                else:
                    try:
                        sender_id = str(event.get_sender_id() or "").strip()
                    except Exception:
                        sender_id = ""
                    if sender_id.isdigit():
                        self._cached_qq_adapter_id = adapter_id
        except Exception as e:
            logger.debug(f"[DailySharing] 记录事件平台失败: {e}")

    async def _safe_publish_qzone(self, qzone_plugin, text: str = "", images: list = None):
        """调用QQ空间发布接口（附带登录过期自动重试机制）"""
        self._inject_qzone_client(qzone_plugin)
        images = images or []
        qzone_api_mod = None
        orig_normalize_images = None

        if any(isinstance(img, str) and img.startswith("local_path::") for img in images):
            try:
                qzone_api = getattr(getattr(qzone_plugin, "service", None), "qzone", None)
                if qzone_api:
                    qzone_api_mod = importlib.import_module(qzone_api.__class__.__module__)
                    orig_normalize_images = getattr(qzone_api_mod, "normalize_images", None)

                    async def normalize_images_with_local(image_items):
                        cleaned = []
                        for item in image_items or []:
                            if isinstance(item, str) and item.startswith("local_path::"):
                                real_path = item.split("::", 1)[1]
                                try:
                                    cleaned.append(await asyncio.to_thread(Path(real_path).read_bytes))
                                except Exception as ex:
                                    logger.warning(f"[DailySharing] 读取QQ空间本地配图失败: {ex}")
                            elif orig_normalize_images:
                                cleaned.extend(await orig_normalize_images([item]))
                        return cleaned

                    qzone_api_mod.normalize_images = normalize_images_with_local
            except Exception as e:
                logger.warning(f"[DailySharing] QQ空间本地配图适配失败: {e}")

        try:
            return await qzone_plugin.service.publish_post(text=text, images=images)
        except Exception as e:
            err_msg = str(e)
            if "登录" in err_msg or "-100" in err_msg or "-3000" in err_msg or "失效" in err_msg:
                logger.debug(f"[DailySharing] err_msg，正在尝试重新登录并重试...")
                try:
                    if hasattr(qzone_plugin, "session"):
                        await qzone_plugin.session.invalidate()
                    if hasattr(qzone_plugin, "cfg"):
                        qzone_plugin.cfg.update_cookies("")
                    # 尝试调用查询触发 qzone 内部逻辑拉取新 Cookie
                    if hasattr(qzone_plugin, "service"):
                        await qzone_plugin.service.query_feeds(pos=0, num=1)
                except Exception as ex:
                    logger.debug(f"[DailySharing] 预检 QQ 空间登录态完成: {ex}")
                    
                # 再次尝试发布
                return await qzone_plugin.service.publish_post(text=text, images=images)
            else:
                raise e
        finally:
            if qzone_api_mod and orig_normalize_images:
                qzone_api_mod.normalize_images = orig_normalize_images

    async def initialize(self):
        """初始化插件"""
        self._track_task(self._delayed_init())

    async def terminate(self):
        """插件卸载/重载时的清理逻辑"""
        self._is_terminated = True 
        try:
            # 1. 停止调度器
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
            
            # 2. 取消所有后台任务
            for task in list(self._bg_tasks):
                if not task.done():
                    task.cancel()
            
            logger.info("[DailySharing] 插件已停止，清理资源完成")
        except Exception as e:
            logger.error(f"[DailySharing] 停止插件出错: {e}")        

    async def _delayed_init(self):
        """延迟初始化逻辑 (调度器)"""
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            return 
        
        # 再次检查终止状态，防止僵尸实例启动调度器
        if self._is_terminated:
            return

        # 启动时清理一次过期数据
        try:
            days_limit = self.content_service.dedup_days
            await self.db.clean_expired_data(days_limit)
        except Exception as e:
            logger.warning(f"[DailySharing] 启动清理过期数据失败: {e}")

        if self.config.get("enable_auto_sharing", False):
            has_targets = False
            if self.receiver_conf:
                if self.receiver_conf.get("groups") or self.receiver_conf.get("users"):
                    has_targets = True
            
            if not has_targets:
                logger.warning("[DailySharing] 未配置接收对象 (receiver)")

        # 通过 TaskManager 挂载所有定时任务
        self.task_manager.setup_tasks()
        
        # 启动调度器 
        if not self._is_terminated and not self.scheduler.running:
            if self.scheduler.get_jobs():
                self.scheduler.start()

    async def _delayed_init_bots(self):
        """延迟初始化 Bot 缓存"""
        try:
            # 等待 30 秒，确保 AstrBot 核心和适配器完全加载
            await asyncio.sleep(30)
            if self._is_terminated: return
            
            # 调用 ContextService 进行 Bot 扫描
            await self.ctx_service.init_bots()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[DailySharing] Bot 初始化任务出错: {e}")

    @staticmethod
    def _write_json_sync(path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def _save_config_file(self):
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.config_file, self.config)
        except Exception as e:
            logger.error(f"[DailySharing] 保存配置失败: {e}")

    def _is_admin_event(self, event: AstrMessageEvent) -> bool:
        """尽量兼容 AstrBot 管理员配置，供插件内部权限判断使用。"""
        try:
            candidates = set()
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if origin:
                candidates.add(origin)
                _, real_id = self.ctx_service._parse_umo(origin)
                if real_id:
                    candidates.add(str(real_id))

            try:
                sender_id = str(event.get_sender_id() or "").strip()
                if sender_id:
                    candidates.add(sender_id)
            except Exception as e:
                logger.debug(f"[DailySharing] 管理员检查读取发送者 ID 失败: {e}")

            cfg = self.context.get_config() or {}
            admins = cfg.get("admins_id", []) or cfg.get("admins", []) or []
            return any(str(admin).strip() in candidates for admin in admins)
        except Exception as e:
            logger.debug(f"[DailySharing] 管理员检查失败: {e}")
            return False

    def _target_entry_matches(self, entry, origin: str, real_id: str, extra_candidates=None) -> bool:
        s = str(entry).strip().replace("：", ":")
        if not s:
            return False

        candidates = {str(c).strip() for c in [origin, real_id] + list(extra_candidates or []) if str(c or "").strip()}
        if s in candidates:
            return True

        parsed = self.task_manager._parse_targets_config([s])
        for target_id in parsed.keys():
            if target_id in candidates:
                return True
            _, target_real_id = self.ctx_service._parse_umo(target_id)
            if target_real_id and target_real_id in candidates:
                return True
        return False

    def _is_configured_receiver_event(self, event: AstrMessageEvent) -> bool:
        """当前会话在接收对象配置中时，允许使用手动分享类命令。"""
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if not origin:
                return False

            is_group = self.ctx_service._is_group_chat(origin)
            if (
                self.ctx_service._is_weixin_oc_event(event)
                and self.ctx_service._is_weixin_platform(origin)
            ):
                is_group = False
            _, real_id = self.ctx_service._parse_umo(origin)
            try:
                sender_id = str(event.get_sender_id() or "").strip()
            except Exception:
                sender_id = ""
            receiver_map = self.task_manager._parse_targets_config(
                self.receiver_conf.get("groups" if is_group else "users", [])
            )
            if (
                origin in receiver_map
                or (real_id and real_id in receiver_map)
                or (sender_id and sender_id in receiver_map)
            ):
                return True
            for entry in receiver_map.keys():
                if self._target_entry_matches(entry, origin, real_id, [sender_id]):
                    return True

            extra_key = "briefing_groups" if is_group else "briefing_users"
            for entry in self.extra_shares_conf.get(extra_key, []):
                if self._target_entry_matches(entry, origin, real_id, [sender_id]):
                    return True

            return False
        except Exception as e:
            logger.warning(f"[DailySharing] 接收对象权限判断失败: {e}")
            return False

    def _plain_permission_denied(self, event: AstrMessageEvent, reason: str = ""):
        suffix = f"\n{reason}" if reason else ""
        return event.plain_result(
            "权限不足：当前会话不在接收对象配置中。"
            "请先把当前会话加入群聊、私聊或早报接收目标。"
            f"{suffix}"
        )

    def _strip_news_link_reference_tail(self, text: str) -> str:
        """移除 news_link 自然回复末尾由模型补出的参考链接列表。"""
        if not text:
            return text

        match = re.search(
            r"\n\s*(?:#{1,6}\s*)?(?:参考链接|参考来源|参考资料|引用来源|References?)\s*[:：]?\s*\n",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return text

        tail = text[match.end():]
        if not re.search(r"https?://", tail, flags=re.IGNORECASE):
            return text

        return text[:match.start()].rstrip()

    def _resolve_news_source_name(self, source: str = None):
        token = str(source or "").strip()
        if not token:
            return None

        token_lower = token.lower()
        if token in SOURCE_CN_MAP:
            return SOURCE_CN_MAP[token]
        if token_lower in NEWS_SOURCE_MAP:
            return token_lower

        for name, key in SOURCE_CN_MAP.items():
            if token in name or name in token:
                return key
        return None

    def _has_reply_component(self, event: AstrMessageEvent) -> bool:
        try:
            messages = event.get_messages()
        except Exception:
            messages = getattr(getattr(event, "message_obj", None), "message", []) or []
        for comp in messages or []:
            if comp.__class__.__name__ == "Reply":
                return True
            if str(getattr(comp, "type", "")).lower().endswith("reply"):
                return True
        return False

    async def _call_llm_wrapper(
        self,
        prompt: str,
        system_prompt: str = None,
        timeout: int = 60,
        max_retries: int = 2,
        tools: list = None,
        umo: str = None,
    ) -> Optional[str]:
        """LLM 调用包装器（支持失败重试与自动降级）"""
        if self._is_terminated: return None
        
        def _get_system_default_provider() -> str:
            # 如果没指定，默认使用第一个模型
            try:
                cfg = self.context.get_config()
                if cfg:
                    pid = cfg.get("provider_settings", {}).get("default_provider_id", "")
                    if pid: return pid
                    for p in cfg.get("provider", []):
                        if p.get("enable", False) and "chat" in p.get("provider_type", "chat"):
                            return p.get("id")
            except Exception as e:
                logger.debug(f"[DailySharing] 读取默认 LLM Provider 失败: {e}")
            return ""

        async def _get_session_provider(umo_value: str) -> str:
            if not umo_value:
                return ""
            try:
                getter = getattr(self.context, "get_current_chat_provider_id", None)
                if callable(getter):
                    return await getter(umo_value)
            except Exception as e:
                logger.debug(f"[DailySharing] 读取会话 LLM Provider 失败: {e}")
            return ""

        configured_provider_id = str(self.llm_conf.get("llm_provider_id", "") or "").strip()
        session_provider_id = ""
        if not configured_provider_id:
            session_provider_id = await _get_session_provider(umo)
        primary_provider_id = configured_provider_id or session_provider_id or _get_system_default_provider()
        current_provider_id = primary_provider_id

        # 临时降级只保留一段时间，避免指定模型恢复后仍长期被跳过。
        now = asyncio.get_running_loop().time()
        if configured_provider_id and self._temp_fallback_provider:
            if now < self._temp_fallback_until:
                current_provider_id = self._temp_fallback_provider
            else:
                logger.info("[DailySharing] LLM 临时降级已过期，恢复尝试指定模型。")
                self._temp_fallback_provider = None
                self._temp_fallback_until = 0.0
                current_provider_id = primary_provider_id

        try:
            config_timeout = int(self.llm_conf.get("llm_timeout", 60))
        except Exception:
            config_timeout = 60
        actual_timeout = max(int(timeout or 60), config_timeout)
        if tools:
            logger.debug("[DailySharing] 当前 AstrBot llm_generate 不支持工具名列表，已忽略 tools 参数。")
        if not current_provider_id:
            logger.error("[DailySharing] 未找到可用的 LLM Provider，无法生成内容。")
            return None

        for attempt in range(max_retries + 1):
            if self._is_terminated: return None
            
            # 降级逻辑 1
            is_last_attempt = (attempt == max_retries)
            if is_last_attempt and attempt > 0 and primary_provider_id and current_provider_id == primary_provider_id:
                default_pid = _get_system_default_provider()
                if default_pid and default_pid != current_provider_id:
                    logger.info(f"[DailySharing] 指定 LLM 已达到重试次数，降级使用默认的第一个模型({default_pid})...")
                    current_provider_id = default_pid
                    if configured_provider_id:
                        self._temp_fallback_provider = default_pid
                        self._temp_fallback_until = asyncio.get_running_loop().time() + self._fallback_ttl_seconds

            try:
                kwargs = {"prompt": prompt}
                if system_prompt is not None and system_prompt != "":
                    kwargs["system_prompt"] = system_prompt
                if current_provider_id:
                    kwargs["chat_provider_id"] = current_provider_id

                resp = await asyncio.wait_for(
                    self.context.llm_generate(**kwargs),
                    timeout=actual_timeout
                )
                
                if resp and hasattr(resp, 'completion_text'):
                    result = resp.completion_text.strip()
                    if result:
                        return result
                    
            except asyncio.TimeoutError:
                logger.warning(f"[DailySharing] LLM 超时 ({actual_timeout}s) (尝试 {attempt+1}/{max_retries+1})")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
            except Exception as e:
                err_str = str(e)
                if "PROHIBITED_CONTENT" in err_str or "blocked" in err_str:
                    logger.error(f"[DailySharing] 内容被模型安全策略拦截 (敏感词): {prompt[:50]}...")
                    return None 

                if "401" in err_str:
                    logger.error(f"[DailySharing] LLM 失败。请检查 API Key。")
                    # 降级逻辑 2                    
                    if attempt < max_retries and primary_provider_id and current_provider_id == primary_provider_id:
                        default_pid = _get_system_default_provider()
                        if default_pid and default_pid != current_provider_id:
                            logger.info(f"[DailySharing] 遇到 401 错误，降级使用默认的第一个模型({default_pid})...")
                            current_provider_id = default_pid
                            if configured_provider_id:
                                self._temp_fallback_provider = default_pid
                                self._temp_fallback_until = asyncio.get_running_loop().time() + self._fallback_ttl_seconds
                            await asyncio.sleep(2)
                            continue
                        else:
                            return None
                    else:
                        return None
                
                logger.error(f"[DailySharing] LLM异常 (尝试 {attempt+1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue

        logger.error(f"[DailySharing] LLM调用失败（已重试{max_retries}次）")
        return None

    @filter.llm_tool(name="daily_share")
    async def daily_share_tool(
        self, 
        event: AstrMessageEvent, 
        share_type: str, 
        source: str = None, 
        get_image: bool = True,
        need_image: bool = False,
        need_video: bool = False,
        need_voice: bool = False,
        to_qzone: bool = False
    ):
        """
        主动分享日常内容、新闻热搜、获取热搜图片等。
        当用户想要看新闻、热搜、早安、晚安、知识、心情或推荐时调用此工具。
        也支持获取"每天60s读世界"或"AI资讯快报"图片。

        Args:
            share_type(string): 分享类型。支持：'自动', '问候', '新闻', '心情', '知识', '推荐', '60s新闻', 'AI资讯'。当用户没有明确指出发什么类型的内容（比如只说“发个说说”、“分享一下”）时，请务必将其设为 '自动'。
            source(string): 仅当 share_type 为'新闻'时有效。指定新闻平台。支持：微博, 知乎, B站, 抖音, 头条, 百度, 腾讯, 小红书, 夸克, 36氪, 51CTO, A站, 爱范儿, 网易, 新浪, 澎湃, 第一财经, 财联社。如果不指定则留空。
            get_image(boolean): 仅当 share_type 为'新闻'时有效。默认为 True (优先分享热搜长图)。只有当用户明确要求“文字版”、“文本”、“不要图片”或“写一段新闻”时，才将其设为 False。
            need_image(boolean): 是否需要AI为这段文案配图。默认为 False。仅当用户明确说“配图”、“带图”、“发张图”时，才将其设为 True。
            need_video(boolean): 是否需要AI为这段文案生成视频。默认为 False。仅当用户明确说“视频”、“动态图”、“动起来”时，才将其设为 True。
            need_voice(boolean): 是否需要将文案转为语音(TTS)分享。默认为 False。仅当用户明确提到“语音”、“朗读”、“念给我听”时，设为 True。
            to_qzone(boolean): 是否需要将内容作为说说分享到QQ空间。默认为 False。仅当用户明确要求“发说说”、“发空间”、“分享到空间”时，必须设为 True。
        """
        if self._is_terminated: return ""

        self._remember_event_adapter(event)
        is_admin = self._is_admin_event(event)
        is_configured_receiver = self._is_configured_receiver_event(event)
        if to_qzone and not is_admin:
            await event.send(event.plain_result("分享到QQ空间仅管理员可用。"))
            return None
        if not (is_admin or is_configured_receiver):
            await event.send(self._plain_permission_denied(event))
            return None

        # 1. 防抖检查
        share_target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if self._is_share_busy(share_target, global_scope=to_qzone):
            await event.send(event.plain_result("正如火如荼地准备中，请稍后..."))
            return None

        # 2. 启动后台异步任务
        task = self._track_task(
            self.task_manager.async_daily_share_task(
                event, share_type, source, get_image, need_image, need_video, need_voice, to_qzone
            )
        )

        # 3. 直接返回空字符串，让 LLM 闭嘴，不再生成回复
        return None

    @filter.llm_tool(name="news_link")
    async def news_link_tool(
        self,
        event: AstrMessageEvent,
        index: str = "",
        query: str = "",
        source: str = None,
        to_qzone: bool = False
    ):
        """
        获取最近一次新闻热搜长图或新闻分享中某条新闻的链接。
        当用户用自然语言询问“刚才热搜图第三条是什么链接”、“把上面第3条新闻链接发我”、“第十二条网址”、“财联社第三条链接”等需求时调用。
        只负责按序号或标题关键词查链接；不要用它重新生成新闻分享。      
        

        Args:
            index(string): 用户要看的新闻序号，1 表示第 1 条。用户问“第十八条链接”等序号请求时，必须由你识别并填写此参数，优先使用阿拉伯数字字符串，例如 "18"。
            query(string): 没有明确序号时填写标题关键词；不要把“第三条”“第3条链接”等序号原句片段填到这里。
            source(string): 可选新闻源，如 财联社、微博、知乎、抖音。只有用户明确指定某个新闻源时填写；追问刚才长图时留空。
            to_qzone(boolean): 是否查询最近一次 QQ 空间新闻缓存。只有用户明确说“空间/QQ空间那条”时设为 True。
        """
        if self._is_terminated:
            return ""

        self._remember_event_adapter(event)
        is_admin = self._is_admin_event(event)
        is_configured_receiver = self._is_configured_receiver_event(event)
        if to_qzone and not is_admin:
            return "QQ空间新闻链接仅管理员可查询。"
        if not (is_admin or is_configured_receiver):
            return "权限不足：当前会话不在接收对象配置中。"

        lookup_query = ""
        index_text = str(index or "").strip()
        parsed_index = self.task_manager._parse_news_query_index(index_text)
        if parsed_index:
            lookup_query = str(parsed_index)

        if not lookup_query:
            lookup_query = str(query or "").strip()

        source_key = self._resolve_news_source_name(source)
        target_uid = "qzone_broadcast" if to_qzone else event.unified_msg_origin
        result = await self.task_manager.get_cached_news_link(
            target_uid,
            lookup_query,
            source_key=source_key,
            refresh_source=False
        )
        try:
            event.set_extra("daily_sharing_news_link_used", True)
        except Exception as e:
            logger.debug(f"[DailySharing] 标记 news_link 状态失败: {e}")
        return result

    @filter.on_llm_response(priority=-10000)
    async def clean_news_link_llm_references(self, event: AstrMessageEvent, resp):
        """保留 LLM 自然回复，只移除 news_link 场景下模型补出的参考链接尾巴。"""
        try:
            used = event.get_extra("daily_sharing_news_link_used")
        except Exception:
            used = None
        if not used or not resp:
            return

        try:
            original = str(resp.completion_text or "")
            cleaned = self._strip_news_link_reference_tail(original)
            if cleaned != original:
                resp.completion_text = cleaned
                logger.debug("[DailySharing] 已清理 news_link LLM 回复中的参考链接尾部")
        except Exception as e:
            logger.warning(f"[DailySharing] 清理 news_link LLM 参考链接失败: {e}")

    @filter.on_decorating_result(priority=-10000)
    async def clean_news_link_decorating_references(self, event: AstrMessageEvent):
        """发送前兜底清理参考链接尾部，但不覆盖 LLM 正文。"""
        try:
            used = event.get_extra("daily_sharing_news_link_used")
        except Exception:
            used = None
        if not used:
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        try:
            original = result.get_plain_text()
            cleaned = self._strip_news_link_reference_tail(original)
            if cleaned != original:
                event.set_result(event.plain_result(cleaned))
                logger.debug("[DailySharing] 已在发送前清理 news_link 参考链接尾部")
            event.set_extra("daily_sharing_news_link_used", None)
        except Exception as e:
            logger.warning(f"[DailySharing] 发送前清理 news_link 参考链接失败: {e}")

    async def _handle_static_news_image_share(
        self,
        event: AstrMessageEvent,
        *,
        url: str,
        display_name: str,
        broadcast_name: str,
        history_text: str,
        current_filename: str,
        broadcast_filename: str,
        download_fail_message: str,
        is_broadcast: bool,
        is_qzone_target: bool,
    ):
        if is_qzone_target:
            yield event.plain_result(f"正在分享{display_name}到QQ空间...")
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if qzone_plugin and hasattr(qzone_plugin, "service"):
                try:
                    await self._safe_publish_qzone(qzone_plugin, text=history_text, images=[url])
                    yield event.plain_result(f"{display_name}已成功分享到QQ空间！")
                    await self.db.add_sent_history("qzone_broadcast", "news", f"{history_text}(手动)", True)
                except Exception as e:
                    yield event.plain_result(f"QQ空间分享失败: {e}")
            else:
                yield event.plain_result("未检测到QQ空间插件！")
            return

        target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
        yield event.plain_result(f"正在向{target_desc}分享{broadcast_name}...")
        if not is_broadcast:
            local_path = await self.task_manager._download_image_to_local(url, current_filename)
            if local_path:
                yield event.image_result(local_path)
            else:
                yield event.plain_result(download_fail_message)
            return

        targets = self.task_manager.get_broadcast_targets()
        local_path = await self.task_manager._download_image_to_local(url, broadcast_filename)
        if not local_path:
            yield event.plain_result(download_fail_message)
            return

        success_count = 0
        fail_count = 0
        for target in targets:
            try:
                prepared_path = await self.task_manager._prepare_image_for_target(target, local_path)
                await self.task_manager._send_message_chain(
                    target,
                    MessageChain().file_image(prepared_path),
                )
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.error(f"[DailySharing] 分享{broadcast_name}到 {target} 失败: {e}")
            await asyncio.sleep(1)
        yield event.plain_result(f"{broadcast_name}广播完成：成功 {success_count} 个，失败 {fail_count} 个。")

    @filter.command("分享")
    async def handle_share_main(self, event: AstrMessageEvent):
        """
        每日分享统一命令入口
        """
        msg = event.message_str.strip()
        parts = msg.split()

        self._remember_event_adapter(event)
        
        if len(parts) == 1:
            yield event.plain_result("指令格式错误，请指定参数。\n示例：/分享 新闻\n可加后缀：广播、空间")
            return
            
        arg = parts[1].lower()
        
        # 判断后缀模式
        is_broadcast = "广播" in parts
        is_qzone_target = "空间" in parts  # 判断是否指向QQ空间
        is_admin = self._is_admin_event(event)
        is_configured_receiver = self._is_configured_receiver_event(event)
        admin_only_args = {"开启", "关闭", "早报空间", "添加当前", "昵称"}

        if arg in admin_only_args or is_broadcast or is_qzone_target:
            if not is_admin:
                yield event.plain_result("权限不足：该操作会修改全局配置、广播或发布QQ空间，仅管理员可用。")
                return
        elif not (is_admin or is_configured_receiver):
            yield self._plain_permission_denied(event)
            return
        
        current_uid = event.unified_msg_origin
        specific_target = None if is_broadcast else current_uid
        share_global_scope = is_broadcast or is_qzone_target

        # =============== 手动触发 60s 新闻 ===============
        if arg == "60s":
            url = self.news_service.get_60s_image_url()
            if not url:
                yield event.plain_result("获取60s新闻失败，请检查API Key配置。")
                return
                
            async for res in self._handle_static_news_image_share(
                event,
                url=url,
                display_name="每天60s读世界",
                broadcast_name="60s新闻",
                history_text="【每天60秒读懂世界】",
                current_filename="manual_60s.png",
                broadcast_filename="manual_60s_broadcast.png",
                download_fail_message="60s新闻图片下载失败。",
                is_broadcast=is_broadcast,
                is_qzone_target=is_qzone_target,
            ):
                yield res
            return

        # =============== 手动触发AI资讯 ===============
        if arg == "ai":
            # 先拦截检测
            ai_data = await self.news_service.get_ai_news_json()
            if not ai_data:
                yield event.plain_result("获取AI资讯失败或今日暂无更新。")
                return

            url = self.news_service.get_ai_news_image_url()
            if not url:
                yield event.plain_result("获取AI资讯图片失败，请检查API Key配置。")
                return

            async for res in self._handle_static_news_image_share(
                event,
                url=url,
                display_name="AI资讯快报",
                broadcast_name="AI资讯",
                history_text="【AI资讯快报】",
                current_filename="manual_ai_news.png",
                broadcast_filename="manual_ai_news_broadcast.png",
                download_fail_message="AI资讯快报图片下载失败。",
                is_broadcast=is_broadcast,
                is_qzone_target=is_qzone_target,
            ):
                yield res
            return
        
        # =============== 配置命令 ===============
        if arg == "早报空间":
            async for res in self.command_handler.cmd_briefing_qzone_sync(event, parts): yield res
            return
        elif arg == "昵称":
            async for res in self.command_handler.cmd_contact_alias(event, parts): yield res
            return
        elif arg == "添加当前":
            async for res in self.command_handler.cmd_add_current(event, parts): yield res
            return
        elif arg == "状态":
            async for res in self.command_handler.cmd_status(event): yield res
            return
        elif arg == "开启":
            async for res in self.command_handler.cmd_enable(event): yield res
            return
        elif arg == "关闭":
            async for res in self.command_handler.cmd_disable(event): yield res
            return
        elif arg == "重置序列":
            async for res in self.command_handler.cmd_reset_seq(event): yield res
            return
        elif arg == "查看序列":
            async for res in self.command_handler.cmd_view_seq(event): yield res
            return
        elif arg == "帮助":
            async for res in self.command_handler.cmd_help(event): yield res
            return
        elif arg == "指定序列":
            async for res in self.command_handler.cmd_set_seq(event, parts): yield res
            return

        # =============== 自动或具体类型生成 ===============
        if arg in ["自动", "auto"]:
            invalid_args = find_invalid_non_news_args(parts)
            if invalid_args:
                yield event.plain_result(f"无效参数: {' '.join(invalid_args)}。非新闻类型仅支持后缀：广播、空间。")
                return

            if self._is_share_busy(specific_target, global_scope=share_global_scope):
                yield event.plain_result("正如火如荼地准备中，请稍后...")
                return
            share_lock = self._get_share_lock(specific_target, global_scope=share_global_scope)
            if is_qzone_target:
                yield event.plain_result("正在向QQ空间生成并分享内容(自动类型)...")
                async with share_lock:
                    await self.task_manager.execute_qzone_share(None, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享内容(自动类型)...")
                async with share_lock:
                    await self.task_manager.execute_share(None, specific_target=specific_target, event=event)
            if not share_global_scope:
                self._release_idle_share_lock(specific_target)
            return

        else:
            force_type = None
            if arg in CMD_CN_MAP:
                force_type = CMD_CN_MAP[arg]
            else:
                try:
                    force_type = SharingType(arg)
                except ValueError:
                    yield event.plain_result(f"未知指令或无效类型: {arg}\n可用: 问候, 新闻, 心情, 知识, 推荐, 60s, ai")
                    return

            type_cn = TYPE_CN_MAP.get(force_type.value, arg)
            
            if force_type == SharingType.NEWS:
                news_src = None
                is_image_mode = "图片" in parts
                
                for p in parts[2:]:
                    if p in ["图片", "广播", "空间"]: continue 
                    if p in SOURCE_CN_MAP:
                        news_src = SOURCE_CN_MAP[p]
                        break
                    elif p in NEWS_SOURCE_MAP:
                        news_src = p
                        break
                        
                if is_image_mode:
                    if not news_src: news_src = self.news_service.select_news_source()
                    img_url, src_name = self.news_service.get_hot_news_image_url(news_src)
                    snapshot_data = await self.news_service.get_hot_news(
                        news_src,
                        limit=self.task_manager.get_news_snapshot_limit(),
                        allow_fallback=False
                    )
                    
                    if is_qzone_target:
                        await self.task_manager.cache_news_snapshot("qzone_broadcast", news_data=snapshot_data, source_key=news_src, image_url=img_url)
                        await self.task_manager.cache_news_snapshot(current_uid, news_data=snapshot_data, source_key=news_src, image_url=img_url)
                        yield event.plain_result(f"正在获取[{src_name}]图片并分享到QQ空间...")
                        qzone_plugin = self.ctx_service._find_plugin("qzone")
                        if qzone_plugin and hasattr(qzone_plugin, "service"):
                            try:
                                await self._safe_publish_qzone(qzone_plugin, text=f"【{src_name}】", images=[img_url])
                                yield event.plain_result("QQ空间分享成功！")
                                await self.db.add_sent_history("qzone_broadcast", "news", f"【{src_name}】长图(手动)", True)
                            except Exception as e:
                                yield event.plain_result(f"QQ空间分享失败: {e}")
                        else:
                            yield event.plain_result("未检测到QQ空间插件！")
                        return

                    await self.task_manager.cache_news_snapshot(current_uid, news_data=snapshot_data, source_key=news_src, image_url=img_url)
                    yield event.plain_result(f"正在获取 [{src_name}] 图片...")
                    local_path = await self.task_manager._download_image_to_local(img_url, "manual_hot_news.png")
                    if local_path:
                        yield event.image_result(local_path)
                    else:
                        yield event.plain_result(f"获取 [{src_name}] 图片下载失败。")
                    return
                    
                src_info = f" ({NEWS_SOURCE_MAP[news_src]['name']})" if news_src else ""
                
                if self._is_share_busy(specific_target, global_scope=share_global_scope):
                    yield event.plain_result("正如火如荼地准备中，请稍后...")
                    return
                share_lock = self._get_share_lock(specific_target, global_scope=share_global_scope)

                if is_qzone_target:
                    yield event.plain_result(f"正在向QQ空间生成并分享{type_cn}{src_info} ...")
                    async with share_lock:
                        await self.task_manager.execute_qzone_share(force_type, news_source=news_src, event=event)
                else:
                    target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                    yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn}{src_info} ...")
                    async with share_lock:
                        await self.task_manager.execute_share(force_type, news_source=news_src, specific_target=specific_target, event=event)
                if not share_global_scope:
                    self._release_idle_share_lock(specific_target)
                return

            invalid_args = find_invalid_non_news_args(parts)
            if invalid_args:
                yield event.plain_result(f"无效参数: {' '.join(invalid_args)}。非新闻类型仅支持后缀：广播、空间。")
                return
                 
            if self._is_share_busy(specific_target, global_scope=share_global_scope):
                yield event.plain_result("正如火如荼地准备中，请稍后...")
                return
            share_lock = self._get_share_lock(specific_target, global_scope=share_global_scope)

            if is_qzone_target:
                yield event.plain_result(f"正在向QQ空间生成并分享{type_cn} ...")
                async with share_lock:
                    await self.task_manager.execute_qzone_share(force_type, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn} ...")
                async with share_lock:
                    await self.task_manager.execute_share(force_type, specific_target=specific_target, event=event)
            if not share_global_scope:
                self._release_idle_share_lock(specific_target)
                
