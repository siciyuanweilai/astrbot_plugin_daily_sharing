import asyncio
import json

from astrbot.api import logger


class PluginRuntimeMixin:
    """主插件的生命周期、后台任务和分享锁能力。"""

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
                    f"[每日分享] 后台任务异常: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
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

    async def initialize(self):
        """初始化插件。"""
        await self._ensure_share_command_outer_permission()
        self._track_task(self._delayed_init())

    async def terminate(self):
        """插件卸载/重载时的清理逻辑。"""
        self._is_terminated = True
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)

            for task in list(self._bg_tasks):
                if not task.done():
                    task.cancel()

            logger.info("[每日分享] 插件已停止，清理资源完成")
        except Exception as e:
            logger.error(f"[每日分享] 停止插件出错: {e}")

    async def _ensure_share_command_outer_permission(self) -> None:
        """让 /分享 先进入插件内部，再由插件返回更具体的权限提示。"""
        try:
            from astrbot.core import sp
            from astrbot.core.star.filter.permission import (
                PermissionType,
                PermissionTypeFilter,
            )
            from astrbot.core.star.star_handler import star_handlers_registry
        except Exception as exc:
            logger.debug(f"[每日分享] 跳过命令外层权限修正: {exc}")
            return

        try:
            for handler in star_handlers_registry.get_handlers_by_module_name(
                type(self).__module__,
            ):
                if handler.handler_name != "handle_share_main":
                    continue
                for handler_filter in handler.event_filters:
                    if isinstance(handler_filter, PermissionTypeFilter):
                        handler_filter.permission_type = PermissionType.MEMBER
                        logger.debug("[每日分享] 已将 /分享 外层命令权限修正为普通成员")
                        break

            alter_cmd = await sp.global_get("alter_cmd", {})
            if not isinstance(alter_cmd, dict):
                return

            changed = False
            plugin_keys = {
                str(getattr(self, "name", "") or "").strip(),
                "astrbot_plugin_daily_sharing",
            }
            for plugin_key in {key for key in plugin_keys if key}:
                plugin_cmds = alter_cmd.get(plugin_key)
                if not isinstance(plugin_cmds, dict):
                    continue
                cmd_cfg = plugin_cmds.get("handle_share_main")
                if not isinstance(cmd_cfg, dict):
                    continue
                if cmd_cfg.get("permission") != "member":
                    cmd_cfg["permission"] = "member"
                    changed = True
            if changed:
                await sp.global_put("alter_cmd", alter_cmd)
                logger.debug("[每日分享] 已同步 /分享 外层命令权限配置为普通成员")
        except Exception as exc:
            logger.warning(f"[每日分享] 修正 /分享 外层命令权限失败: {exc}")

    async def _delayed_init(self):
        """延迟初始化逻辑（调度器）。"""
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            return

        if self._is_terminated:
            return

        try:
            days_limit = self.content_service.dedup_days
            await self.db.clean_expired_data(days_limit)
        except Exception as e:
            logger.warning(f"[每日分享] 启动清理过期数据失败: {e}")

        if self.config.get("enable_auto_sharing", False):
            has_targets = False
            if self.receiver_conf:
                if self.receiver_conf.get("groups") or self.receiver_conf.get("users"):
                    has_targets = True

            if not has_targets:
                logger.warning("[每日分享] 未配置接收对象")

        self.task_manager.setup_tasks()

        if not self._is_terminated and not self.scheduler.running:
            if self.scheduler.get_jobs():
                self.scheduler.start()

    async def _delayed_init_bots(self):
        """延迟初始化机器人缓存。"""
        try:
            await asyncio.sleep(30)
            if self._is_terminated:
                return

            await self.ctx_service.init_bots()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[每日分享] 机器人初始化任务出错: {e}")

    @staticmethod
    def _write_json_sync(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def _save_config_file(self):
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.config_file, self.config)
        except Exception as e:
            logger.error(f"[每日分享] 保存配置失败: {e}")
