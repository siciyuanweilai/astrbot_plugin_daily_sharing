import asyncio
import random as random_module
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger

from ...config import CRON_TEMPLATES
from .random import TaskSchedulerRandomMixin
from .recovery import TaskSchedulerRecoveryMixin
from .triggers import TaskSchedulerTriggerMixin


class TaskSchedulerMixin(
    TaskSchedulerRecoveryMixin,
    TaskSchedulerRandomMixin,
    TaskSchedulerTriggerMixin,
):
    """定时任务注册、随机延迟排程和未完成任务恢复。"""

    def _parse_cron_to_kwargs(self, cron_str: str) -> Optional[dict]:
        """
        兼容解析 5/6/7 位的定时表达式。
        5位: 分 时 日 月 周
        6位: 秒 分 时 日 月 周
        7位: 秒 分 时 日 月 周 年
        """
        parts = cron_str.strip().split()
        if len(parts) == 5:
            return {
                "minute": parts[0], "hour": parts[1], 
                "day": parts[2], "month": parts[3], "day_of_week": parts[4]
            }
        elif len(parts) == 6:
            return {
                "second": parts[0], "minute": parts[1], "hour": parts[2], 
                "day": parts[3], "month": parts[4], "day_of_week": parts[5]
            }
        elif len(parts) == 7:
            return {
                "second": parts[0], "minute": parts[1], "hour": parts[2], 
                "day": parts[3], "month": parts[4], "day_of_week": parts[5], 
                "year": parts[6]
            }
        return None

    def _read_delay_minutes(self, conf: dict, key: str) -> int:
        try:
            return max(0, int(conf.get(key, 0)))
        except Exception:
            return 0

    async def _schedule_or_execute_delayed(
        self,
        *,
        state_key: str,
        delay_minutes: int,
        delayed_func,
        delayed_job_id: str,
        log_label: str,
    ):
        if delay_minutes > 0:
            delay_seconds = random_module.randint(0, delay_minutes * 60)
            if delay_seconds > 0:
                target_time = datetime.now() + timedelta(seconds=delay_seconds)
                time_str = target_time.strftime('%H:%M:%S')
                await self.db.update_state_dict(state_key, {
                    "pending_delay_job": {"target_time": target_time.timestamp()}
                })
                self.scheduler.add_job(
                    delayed_func,
                    'date',
                    run_date=target_time,
                    id=delayed_job_id,
                    replace_existing=True,
                )
                logger.debug(
                    f"[每日分享] {log_label}已触发，将随机延迟 "
                    f"{delay_seconds/60:.1f} 分钟，预计于 {time_str} 分享..."
                )
                return

        await delayed_func()

    async def _run_tracked_pending_job(
        self,
        state_key: str,
        action,
        *,
        lock=None,
        locked_warning: str = "",
        before_action=None,
    ):
        if self.plugin._is_terminated:
            return

        task = asyncio.current_task()
        self.plugin._bg_tasks.add(task)
        try:
            await self.db.update_state_dict(state_key, {"pending_delay_job": None})

            if lock:
                if lock.locked() and locked_warning:
                    logger.warning(locked_warning)
                async with lock:
                    if before_action and not await before_action():
                        return
                    await action()
                return

            if before_action and not await before_action():
                return
            await action()
        finally:
            self.plugin._bg_tasks.discard(task)

    def setup_cleanup_tasks(self):
        self.setup_weixin_temp_cleanup()
        self.setup_news_image_cleanup()

    def setup_tasks(self):
        self.setup_cleanup_tasks()

        if not self.plugin.config.get("enable_auto_sharing", False):
            logger.debug("[每日分享] 分享内容已禁用")
            return

        cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
        self.setup_cron(cron)
        logger.debug(f"[每日分享] 分享内容定时任务已启动 ({cron})")
        
        # 扫描并注册所有带独立时间的独立定时任务
        self.setup_custom_target_crons()

        enable_60s = self.extra_shares_conf.get("enable_60s_news", False)
        enable_ai = self.extra_shares_conf.get("enable_ai_news", False)
        
        # 只要有一个开启，就注册定时任务
        if enable_60s or enable_ai:
            cron_briefing = self.extra_shares_conf.get("cron_briefing", "0 8 * * *")
            self._setup_cron_job_custom("share_briefing", cron_briefing, self._task_wrapper_briefing)
            logger.debug(f"[每日分享] 早报定时任务已启动 ({cron_briefing})")

        if self.qzone_conf.get("enable_qzone", False):
            self.setup_qzone_cron()

        # 启动时恢复因为重启而中断的延迟任务
        self.plugin._track_task(self._recover_pending_jobs())

    def setup_custom_target_crons(self):
        """解析并为写了独立时间的群聊、私聊挂载独立定时 (支持随机延迟)"""
        default_adapter_id = self._get_default_adapter_id(warn_on_fallback=False)

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

        # 清除旧的独立分享任务
        job_ids = [job.id for job in self.scheduler.get_jobs() if job.id.startswith("custom_share_")]
        for jid in job_ids:
            self.scheduler.remove_job(jid)

        def add_custom_job(target_id, is_group, cron_str):
            job_id = f"custom_share_{target_id}"
            target_umo = self._build_target_umo(target_id, is_group, default_adapter_id)
            if self._is_unsupported_weixin_group_target(target_umo, is_group):
                logger.warning(f"[每日分享] 个人微信平台(weixin_oc)不支持群聊，已跳过独立定时目标: {target_id}")
                return
            
            async def delayed_custom_execute():
                async def run_custom_share():
                    logger.debug(f"[每日分享] 独立时间到达，开始独立分享任务: {target_id}")
                    await self.execute_share(specific_target=target_umo)

                await self._run_tracked_pending_job(
                    f"target_{target_id}",
                    run_custom_share,
                    lock=self._lock,
                    locked_warning=f"[每日分享] 独立任务 {target_id} 触发，系统繁忙排队中...",
                )

            async def custom_wrapper():
                if self.plugin._is_terminated: return
                
                # 独立群聊、私聊配置本身就是定时触发，强制读取随机延迟配置
                await self._schedule_or_execute_delayed(
                    state_key=f"target_{target_id}",
                    delay_minutes=self._read_delay_minutes(self.basic_conf, "cron_random_delay"),
                    delayed_func=delayed_custom_execute,
                    delayed_job_id=f"delayed_custom_share_{target_id}",
                    log_label=f"独立任务 [{target_id}] ",
                )

            actual_cron = CRON_TEMPLATES.get(cron_str, cron_str)
            cron_kwargs = self._parse_cron_to_kwargs(actual_cron)
            
            if cron_kwargs:
                self.scheduler.add_job(
                    custom_wrapper, 'cron',
                    **cron_kwargs,
                    id=job_id, replace_existing=True, max_instances=1
                )
                logger.debug(f"[每日分享] 独立群聊、私聊任务 [{target_id}] 已挂载独立定时: {actual_cron}")
            else:
                logger.error(f"[每日分享] 独立群聊、私聊任务 [{target_id}] 无效的定时表达式（支持 5/6/7 位）: {cron_str}")

        for gid, conf in r_groups.items():
            if isinstance(conf, dict) and conf.get("cron"):
                add_custom_job(gid, True, conf["cron"])
                
        for uid, conf in r_users.items():
            if isinstance(conf, dict) and conf.get("cron"):
                add_custom_job(uid, False, conf["cron"])

    def setup_cron(self, cron_str):
        """设置自动分享触发器。"""
        trigger_mode = self.basic_conf.get("trigger_mode", "cron")
        
        if trigger_mode == "cron":
            self._setup_cron_job_custom("auto_share", cron_str, self._task_wrapper)
        elif trigger_mode == "random_period":
            # 每天凌晨 00:00 重新生成当天的随机任务
            self._setup_cron_job_custom("daily_random_scheduler", "0 0 * * *", self._schedule_daily_random_jobs)
            # 启动时立刻安排一次今天的任务
            self.plugin._track_task(self._schedule_daily_random_jobs())
            logger.debug(f"[每日分享] 已启用多时间段随机生成模式")

    def setup_qzone_cron(self):
        """设置 QQ 空间自动分享触发器"""
        trigger_mode = self.qzone_conf.get("qzone_trigger_mode", "cron")
        
        if trigger_mode == "cron":
            q_cron = self.qzone_conf.get("qzone_cron", "0 20 * * *")
            actual_q_cron = CRON_TEMPLATES.get(q_cron, q_cron)
            self._setup_cron_job_custom("qzone_share", actual_q_cron, self._task_wrapper_qzone)
            logger.debug(f"[每日分享] QQ 空间定时任务已启动 ({actual_q_cron})")
        elif trigger_mode == "random_period":
            # 每天凌晨 00:00 重新生成当天的 QQ 空间随机任务。
            self._setup_cron_job_custom("daily_qzone_random_scheduler", "0 0 * * *", self._schedule_daily_qzone_random_jobs)
            # 启动时立刻安排一次今天的任务
            self.plugin._track_task(self._schedule_daily_qzone_random_jobs())
            logger.debug("[每日分享] QQ 空间已启用多时间段随机生成模式")

    def _setup_cron_job_custom(self, job_id: str, cron_str: str, func):
        """通用定时表达式设置方法。"""
        if self.plugin._is_terminated: return
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            actual_cron = CRON_TEMPLATES.get(cron_str, cron_str)
            cron_kwargs = self._parse_cron_to_kwargs(actual_cron)
            
            if cron_kwargs:
                self.scheduler.add_job(
                    func, 'cron',
                    **cron_kwargs,
                    id=job_id,
                    replace_existing=True,
                    max_instances=1
                )
                logger.debug(f"[每日分享] 任务[{job_id}]已设定: {actual_cron}")
            else:
                logger.error(f"[每日分享] 任务[{job_id}]无效的定时表达式（支持 5/6/7 位）: {cron_str}")
        except Exception as e:
            logger.error(f"[每日分享] 任务[{job_id}]设置失败: {e}")

