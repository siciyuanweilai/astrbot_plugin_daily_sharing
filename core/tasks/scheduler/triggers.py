from datetime import datetime

from astrbot.api import logger


class TaskSchedulerTriggerMixin:
    """定时任务触发入口。"""

    async def _task_wrapper(self):
        """主任务触发器（处理防抖与随机延迟记录）"""
        if self.plugin._is_terminated: return

        # 数据库自动清理        
        try:
            days_limit = self.content_service.dedup_days
            await self.db.clean_expired_data(days_limit)
        except Exception as e:
            logger.warning(f"[每日分享] 数据库清理失败: {e}")

        # 随机延迟逻辑
        trigger_mode = self.basic_conf.get("trigger_mode", "cron")
        random_delay_min = (
            self._read_delay_minutes(self.basic_conf, "cron_random_delay")
            if trigger_mode == "cron"
            else 0
        )
        await self._schedule_or_execute_delayed(
            state_key="global",
            delay_minutes=random_delay_min,
            delayed_func=self._execute_delayed_task,
            delayed_job_id="delayed_auto_share",
            log_label="定时任务",
        )

    async def _execute_delayed_task(self):
        """主分享任务入口。"""
        async def before_share():
            now = datetime.now()
            if self.plugin._last_share_time:
                if (now - self.plugin._last_share_time).total_seconds() < 60:
                    logger.debug("[每日分享] 检测到近期已分享，跳过本次触发。")
                    return False
            self.plugin._last_share_time = now
            return True

        async def run_share():
            logger.info("[每日分享] 开始分享任务...")
            await self.execute_share()

        await self._run_tracked_pending_job(
            "global",
            run_share,
            lock=self._lock,
            locked_warning="[每日分享] 上一个任务正在进行中，本次触发将排队等待...",
            before_action=before_share,
        )

    async def _task_wrapper_briefing(self):
        """早报任务触发器（处理随机延迟记录）"""
        if self.plugin._is_terminated: return
        await self._schedule_or_execute_delayed(
            state_key="briefing",
            delay_minutes=self._read_delay_minutes(self.extra_shares_conf, "briefing_cron_random_delay"),
            delayed_func=self._execute_delayed_briefing_task,
            delayed_job_id="delayed_briefing_share",
            log_label="早报任务",
        )

    async def _execute_delayed_briefing_task(self):
        """早报分享任务入口。"""
        async def run_briefing_share():
            await self.execute_briefing_share()

        await self._run_tracked_pending_job("briefing", run_briefing_share)

    async def _task_wrapper_qzone(self):
        """QQ 空间任务触发器（处理防抖与随机延迟记录）。"""
        if self.plugin._is_terminated: return
        
        trigger_mode = self.qzone_conf.get("qzone_trigger_mode", "cron")
        random_delay_min = (
            self._read_delay_minutes(self.basic_conf, "cron_random_delay")
            if trigger_mode == "cron"
            else 0
        )
        await self._schedule_or_execute_delayed(
            state_key="qzone",
            delay_minutes=random_delay_min,
            delayed_func=self._execute_delayed_qzone_task,
            delayed_job_id="delayed_qzone_share",
            log_label="QQ 空间任务",
        )

    async def _execute_delayed_qzone_task(self):
        """QQ 空间分享任务入口。"""
        async def run_qzone_share():
            logger.info("[每日分享] 开始 QQ 空间分享任务...")
            await self.execute_qzone_share()

        await self._run_tracked_pending_job("qzone", run_qzone_share, lock=self._lock)
