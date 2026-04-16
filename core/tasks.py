import asyncio
import random
import re
import sys
import aiofiles
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Record, Video 

from ..config import TimePeriod, SharingType, SHARING_TYPE_SEQUENCES, CRON_TEMPLATES, NEWS_SOURCE_MAP
from .constants import CMD_CN_MAP, SOURCE_CN_MAP

class TaskManager:
    def __init__(self, plugin):
        self.plugin = plugin
        self.scheduler = plugin.scheduler
        self.db = plugin.db
        self.ctx_service = plugin.ctx_service
        self.news_service = plugin.news_service
        self.image_service = plugin.image_service
        self.content_service = plugin.content_service
        self._lock = plugin._lock
        
        self.basic_conf = plugin.basic_conf
        self.extra_shares_conf = plugin.extra_shares_conf
        self.qzone_conf = plugin.qzone_conf
        self.image_conf = plugin.image_conf
        self.tts_conf = plugin.tts_conf
        self.context_conf = plugin.context_conf
        self.receiver_conf = plugin.receiver_conf

    def setup_tasks(self):
        if self.plugin.config.get("enable_auto_sharing", False):
            cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
            self.setup_cron(cron)
            logger.debug(f"[DailySharing] 分享内容定时任务已启动 ({cron})")
            
            # 扫描并注册所有带独立时间的独立定时任务
            self.setup_custom_target_crons()
        else:
            logger.debug("[DailySharing] 分享内容已禁用")

        enable_60s = self.extra_shares_conf.get("enable_60s_news", False)
        enable_ai = self.extra_shares_conf.get("enable_ai_news", False)
        
        # 只要有一个开启，就注册定时任务
        if enable_60s or enable_ai:
            cron_briefing = self.extra_shares_conf.get("cron_briefing", "0 8 * * *")
            self._setup_cron_job_custom("share_briefing", cron_briefing, self._task_wrapper_briefing)
            logger.debug(f"[DailySharing] 早报定时任务已启动 ({cron_briefing})")

        if self.qzone_conf.get("enable_qzone", False):
            self.setup_qzone_cron()

        # 启动时恢复因为重启而中断的延迟任务
        asyncio.create_task(self._recover_pending_jobs())

    def setup_custom_target_crons(self):
        """解析并为写了独立时间的群聊、私聊挂载独立定时 (支持随机延迟)"""
        default_adapter_id = self.plugin._cached_adapter_id
        if not default_adapter_id:
            try:
                if hasattr(self.plugin.context, "platform_manager"):
                    insts = self.plugin.context.platform_manager.get_insts()
                    for inst in insts:
                        if hasattr(inst, "metadata") and inst.metadata.id:
                            default_adapter_id = inst.metadata.id
                            self.plugin._cached_adapter_id = default_adapter_id
                            break
            except Exception: pass
        if not default_adapter_id:
            default_adapter_id = "aiocqhttp"

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

        # 清除旧的 custom_share 任务
        job_ids = [job.id for job in self.scheduler.get_jobs() if job.id.startswith("custom_share_")]
        for jid in job_ids:
            self.scheduler.remove_job(jid)

        def add_custom_job(target_id, is_group, cron_str):
            job_id = f"custom_share_{target_id}"
            target_umo = f"{default_adapter_id}:{'GroupMessage' if is_group else 'FriendMessage'}:{target_id}"
            
            async def delayed_custom_execute():
                if self.plugin._is_terminated: return
                task = asyncio.current_task()
                self.plugin._bg_tasks.add(task)
                try:
                    await self.db.update_state_dict(f"target_{target_id}", {"pending_delay_job": None})
                    if self._lock.locked():
                        logger.warning(f"[DailySharing] 独立任务 {target_id} 触发，系统繁忙排队中...")
                    async with self._lock:
                        logger.debug(f"[DailySharing] 独立时间到达，开始执行独立分享任务: {target_id}")
                        await self.execute_share(specific_target=target_umo)
                finally:
                    self.plugin._bg_tasks.discard(task)

            async def custom_wrapper():
                if self.plugin._is_terminated: return
                
                # 独立群聊、私聊配置本身就是Cron触发，强制读取随机延迟配置
                random_delay_min = 0
                try:
                    random_delay_min = int(self.basic_conf.get("cron_random_delay", 0))
                except Exception: 
                    pass

                if random_delay_min > 0:
                    delay_seconds = random.randint(0, random_delay_min * 60)
                    if delay_seconds > 0:
                        target_time = datetime.now() + timedelta(seconds=delay_seconds)
                        time_str = target_time.strftime('%H:%M:%S')
                        
                        await self.db.update_state_dict(f"target_{target_id}", {
                            "pending_delay_job": {"target_time": target_time.timestamp()}
                        })
                        
                        self.scheduler.add_job(
                            delayed_custom_execute, 'date',
                            run_date=target_time,
                            id=f"delayed_custom_share_{target_id}",
                            replace_existing=True
                        )
                        logger.debug(f"[DailySharing] 独立任务 [{target_id}] 已触发，将随机延迟 {delay_seconds/60:.1f} 分钟，预计于 {time_str} 执行...")
                        return
                
                # 如果没配置延迟或延迟为0，立刻执行
                await delayed_custom_execute()

            actual_cron = CRON_TEMPLATES.get(cron_str, cron_str)
            parts = actual_cron.split()
            if len(parts) == 5:
                self.scheduler.add_job(
                    custom_wrapper, 'cron',
                    minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
                    id=job_id, replace_existing=True, max_instances=1
                )
                logger.debug(f"[DailySharing] 独立群聊、私聊任务 [{target_id}] 已挂载独立定时: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 独立群聊、私聊任务 [{target_id}] 无效的Cron表达式: {cron_str}")

        for gid, conf in r_groups.items():
            if isinstance(conf, dict) and conf.get("cron"):
                add_custom_job(gid, True, conf["cron"])
                
        for uid, conf in r_users.items():
            if isinstance(conf, dict) and conf.get("cron"):
                add_custom_job(uid, False, conf["cron"])

    async def _recover_pending_jobs(self):
        """恢复因重启中断的延迟任务"""
        if self.plugin._is_terminated: return
        
        now = datetime.now()
        now_ts = now.timestamp()
        
        # 主任务恢复
        global_state = await self.db.get_state("global", {})
        pending = global_state.get("pending_delay_job")
        if pending:
            target_ts = pending.get("target_time", 0)
            if target_ts > now_ts:
                run_time = datetime.fromtimestamp(target_ts)
                self.scheduler.add_job(
                    self._execute_delayed_task, 'date', run_date=run_time, id="resume_auto_share", replace_existing=True
                )
                logger.debug(f"[DailySharing] 已恢复未完成的延迟分享任务，将在 {run_time.strftime('%H:%M:%S')} 执行")
            elif 0 <= now_ts - target_ts < 3600:  
                run_time = now + timedelta(seconds=5)
                self.scheduler.add_job(
                    self._execute_delayed_task, 'date', run_date=run_time, id="resume_auto_share", replace_existing=True
                )
                logger.debug("[DailySharing] 检测到近期错过的延迟分享任务，即将执行补偿分享")
            else:
                await self.db.update_state_dict("global", {"pending_delay_job": None})

        # QQ空间任务恢复
        qzone_state = await self.db.get_state("qzone", {})
        q_pending = qzone_state.get("pending_delay_job")
        if q_pending:
            target_ts = q_pending.get("target_time", 0)
            if target_ts > now_ts:
                run_time = datetime.fromtimestamp(target_ts)
                self.scheduler.add_job(
                    self._execute_delayed_qzone_task, 'date', run_date=run_time, id="resume_qzone_share", replace_existing=True
                )
                logger.debug(f"[DailySharing] 已恢复未完成的QQ空间延迟任务，将在 {run_time.strftime('%H:%M:%S')} 执行")
            elif 0 <= now_ts - target_ts < 3600:
                run_time = now + timedelta(seconds=10)
                self.scheduler.add_job(
                    self._execute_delayed_qzone_task, 'date', run_date=run_time, id="resume_qzone_share", replace_existing=True
                )
                logger.debug("[DailySharing] 检测到近期错过的QQ空间延迟任务，即将执行补偿分享")
            else:
                await self.db.update_state_dict("qzone", {"pending_delay_job": None})

        # 独立群聊、私聊任务的延迟恢复
        default_adapter_id = self.plugin._cached_adapter_id
        if not default_adapter_id:
            try:
                if hasattr(self.plugin.context, "platform_manager"):
                    for inst in self.plugin.context.platform_manager.get_insts():
                        if hasattr(inst, "metadata") and inst.metadata.id:
                            default_adapter_id = inst.metadata.id
                            break
            except Exception: pass
        if not default_adapter_id: default_adapter_id = "aiocqhttp"

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))
        all_targets = [(gid, True) for gid in r_groups.keys() if gid] + [(uid, False) for uid in r_users.keys() if uid]
        
        def recover_custom_job(tid, is_group):
            target_umo = f"{default_adapter_id}:{'GroupMessage' if is_group else 'FriendMessage'}:{tid}"
            async def delayed_recover():
                if self.plugin._is_terminated: return
                await self.db.update_state_dict(f"target_{tid}", {"pending_delay_job": None})
                async with self._lock:
                    logger.debug(f"[DailySharing] 补偿恢复，执行独立分享任务: {tid}")
                    await self.execute_share(specific_target=target_umo)
            return delayed_recover

        for tid, is_group in all_targets:
            t_state = await self.db.get_state(f"target_{tid}", {})
            t_pending = t_state.get("pending_delay_job")
            if t_pending:
                target_ts = t_pending.get("target_time", 0)
                if target_ts > now_ts:
                    run_time = datetime.fromtimestamp(target_ts)
                    self.scheduler.add_job(
                        recover_custom_job(tid, is_group), 'date', run_date=run_time, 
                        id=f"resume_custom_share_{tid}", replace_existing=True
                    )
                elif 0 <= now_ts - target_ts < 3600:
                    run_time = now + timedelta(seconds=random.randint(10, 30))
                    self.scheduler.add_job(
                        recover_custom_job(tid, is_group), 'date', run_date=run_time, 
                        id=f"resume_custom_share_{tid}", replace_existing=True
                    )
                else:
                    await self.db.update_state_dict(f"target_{tid}", {"pending_delay_job": None})

    def setup_cron(self, cron_str):
        """设置自动分享触发器 (支持 cron 和 random_period)"""
        trigger_mode = self.basic_conf.get("trigger_mode", "cron")
        
        if trigger_mode == "cron":
            self._setup_cron_job_custom("auto_share", cron_str, self._task_wrapper)
        elif trigger_mode == "random_period":
            # 每天凌晨 00:00 重新生成当天的随机任务
            self._setup_cron_job_custom("daily_random_scheduler", "0 0 * * *", self._schedule_daily_random_jobs)
            # 启动时立刻安排一次今天的任务
            asyncio.create_task(self._schedule_daily_random_jobs())
            logger.debug(f"[DailySharing] 已启用多时间段随机生成模式")

    def setup_qzone_cron(self):
        """设置 QQ 空间自动分享触发器"""
        trigger_mode = self.qzone_conf.get("qzone_trigger_mode", "cron")
        
        if trigger_mode == "cron":
            q_cron = self.qzone_conf.get("qzone_cron", "0 20 * * *")
            actual_q_cron = CRON_TEMPLATES.get(q_cron, q_cron)
            self._setup_cron_job_custom("qzone_share", actual_q_cron, self._task_wrapper_qzone)
            logger.debug(f"[DailySharing] QQ空间定时任务已启动 ({actual_q_cron})")
        elif trigger_mode == "random_period":
            # 每天凌晨 00:00 重新生成当天的QQ空间随机任务
            self._setup_cron_job_custom("daily_qzone_random_scheduler", "0 0 * * *", self._schedule_daily_qzone_random_jobs)
            # 启动时立刻安排一次今天的任务
            asyncio.create_task(self._schedule_daily_qzone_random_jobs())
            logger.debug(f"[DailySharing] QQ空间已启用多时间段随机生成模式")

    async def _schedule_daily_random_jobs(self):
        """每天计算并在 scheduler 中添加当天的随机时间点任务"""
        if self.plugin._is_terminated: return
        
        job_ids = [job.id for job in self.scheduler.get_jobs() if job.id.startswith("random_share_")]
        for jid in job_ids:
            self.scheduler.remove_job(jid)
            
        periods = self.basic_conf.get("random_periods", ["08:00-10:00", "19:00-21:00"])
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        
        state = await self.db.get_state("global", {})
        random_schedule = state.get("random_schedule", {})
        
        is_modified = False
        if random_schedule.get("date") != date_str:
            random_schedule = {"date": date_str, "jobs": {}}
            is_modified = True
            
        jobs = random_schedule.get("jobs", {})
        
        stale_periods = [p for p in jobs.keys() if p not in periods]
        for p in stale_periods:
            del jobs[p]
            is_modified = True
            
        for period_str in periods:
            if period_str not in jobs:
                try:
                    start_str, end_str = period_str.split('-')
                    start_h, start_m = map(int, start_str.split(':'))
                    end_h, end_m = map(int, end_str.split(':'))
                    
                    start_dt = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
                    end_dt = now.replace(hour=end_h, minute=end_m, second=59, microsecond=0)
                    
                    if end_dt <= start_dt:
                        continue 
                    
                    random_seconds = random.randint(0, int((end_dt - start_dt).total_seconds()))
                    run_time = start_dt + timedelta(seconds=random_seconds)
                    
                    jobs[period_str] = run_time.timestamp()
                    is_modified = True
                except Exception as e:
                    logger.error(f"[DailySharing] 解析时间段 {period_str} 失败: {e}")
                    
        if is_modified:
            random_schedule["jobs"] = jobs
            await self.db.update_state_dict("global", {"random_schedule": random_schedule})
        
        for idx, (period_str, timestamp) in enumerate(jobs.items()):
            run_time = datetime.fromtimestamp(timestamp)
            if run_time > now:
                job_id = f"random_share_{idx}"
                self.scheduler.add_job(
                    self._task_wrapper, 'date',
                    run_date=run_time,
                    id=job_id,
                    replace_existing=True
                )
                logger.debug(f"[DailySharing] 今日随机任务 [{period_str}] 已安排在: {run_time.strftime('%H:%M:%S')} 执行")

    async def _schedule_daily_qzone_random_jobs(self):
        """QQ空间随机时间计算"""
        if self.plugin._is_terminated: return
        
        job_ids = [job.id for job in self.scheduler.get_jobs() if job.id.startswith("qzone_random_share_")]
        for jid in job_ids:
            self.scheduler.remove_job(jid)
            
        periods = self.qzone_conf.get("qzone_random_periods", ["08:00-10:00", "19:00-21:00"])
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        
        state = await self.db.get_state("qzone", {})
        qzone_random_schedule = state.get("random_schedule", {})
        
        is_modified = False
        if qzone_random_schedule.get("date") != date_str:
            qzone_random_schedule = {"date": date_str, "jobs": {}}
            is_modified = True
            
        jobs = qzone_random_schedule.get("jobs", {})
        
        stale_periods = [p for p in jobs.keys() if p not in periods]
        for p in stale_periods:
            del jobs[p]
            is_modified = True
            
        for period_str in periods:
            if period_str not in jobs:
                try:
                    start_str, end_str = period_str.split('-')
                    start_h, start_m = map(int, start_str.split(':'))
                    end_h, end_m = map(int, end_str.split(':'))
                    
                    start_dt = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
                    end_dt = now.replace(hour=end_h, minute=end_m, second=59, microsecond=0)
                    if end_dt <= start_dt: 
                        continue
                    
                    random_seconds = random.randint(0, int((end_dt - start_dt).total_seconds()))
                    run_time = start_dt + timedelta(seconds=random_seconds)
                    
                    jobs[period_str] = run_time.timestamp()
                    is_modified = True
                except Exception as e:
                    logger.error(f"[DailySharing] 解析QQ空间时间段 {period_str} 失败: {e}")
                    
        if is_modified:
            qzone_random_schedule["jobs"] = jobs
            await self.db.update_state_dict("qzone", {"random_schedule": qzone_random_schedule})
        
        for idx, (period_str, timestamp) in enumerate(jobs.items()):
            run_time = datetime.fromtimestamp(timestamp)
            if run_time > now:
                job_id = f"qzone_random_share_{idx}"
                self.scheduler.add_job(
                    self._task_wrapper_qzone, 'date', run_date=run_time, id=job_id, replace_existing=True
                )
                logger.debug(f"[DailySharing] 今日QQ空间随机任务 [{period_str}] 已安排在: {run_time.strftime('%H:%M:%S')} 执行")

    def _setup_cron_job_custom(self, job_id: str, cron_str: str, func):
        """通用 Cron 设置方法"""
        if self.plugin._is_terminated: return
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            actual_cron = CRON_TEMPLATES.get(cron_str, cron_str)
            parts = actual_cron.split()
            
            if len(parts) == 5:
                self.scheduler.add_job(
                    func, 'cron',
                    minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
                    id=job_id,
                    replace_existing=True,
                    max_instances=1
                )
                logger.debug(f"[DailySharing] 任务[{job_id}]已设定: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 任务[{job_id}]无效的 Cron 表达式: {cron_str}")
        except Exception as e:
            logger.error(f"[DailySharing] 任务[{job_id}]设置失败: {e}")

    async def _task_wrapper(self):
        """主任务触发器（处理防抖与随机延迟记录）"""
        if self.plugin._is_terminated: return

        # 执行数据库自动清理        
        try:
            days_limit = self.content_service.dedup_days
            await self.db.clean_expired_data(days_limit)
        except Exception as e:
            logger.warning(f"[DailySharing] 数据库清理失败: {e}")

        # 随机延迟逻辑
        trigger_mode = self.basic_conf.get("trigger_mode", "cron")
        random_delay_min = 0
        if trigger_mode == "cron":
            try:
                # 从配置获取随机延迟分钟数，默认为 0            
                random_delay_min = int(self.basic_conf.get("cron_random_delay", 0))
            except Exception:
                pass

        if random_delay_min > 0:
            delay_seconds = random.randint(0, random_delay_min * 60)
            if delay_seconds > 0:
                target_time = datetime.now() + timedelta(seconds=delay_seconds)
                time_str = target_time.strftime('%H:%M:%S')
                
                await self.db.update_state_dict("global", {
                    "pending_delay_job": {"target_time": target_time.timestamp()}
                })
                
                self.scheduler.add_job(
                    self._execute_delayed_task, 'date',
                    run_date=target_time,
                    id="delayed_auto_share",
                    replace_existing=True
                )
                
                logger.debug(f"[DailySharing] 定时任务已触发，启用随机延迟策略。")
                logger.debug(f"[DailySharing] 将延迟 {delay_seconds/60:.1f} 分钟，预计于 {time_str} 执行...")
                return

        await self._execute_delayed_task()

    async def _execute_delayed_task(self):
        """实际执行主分享任务"""
        if self.plugin._is_terminated: return
        task = asyncio.current_task()
        self.plugin._bg_tasks.add(task)
        
        try:
            await self.db.update_state_dict("global", {"pending_delay_job": None})

            # 核心执行逻辑            
            now = datetime.now()

            # 防抖检查            
            if self.plugin._last_share_time:
                if (now - self.plugin._last_share_time).total_seconds() < 60:
                    logger.debug("[DailySharing] 检测到近期已执行任务，跳过本次触发。")
                    return
            
            if self._lock.locked():
                logger.warning("[DailySharing] 上一个任务正在进行中，跳过本次触发。")
                return

            async with self._lock:
                self.plugin._last_share_time = now
                logger.info("[DailySharing] 开始执行分享任务...")
                await self.execute_share()
                
        finally:
            self.plugin._bg_tasks.discard(task)

    async def _task_wrapper_briefing(self):
        """早报任务回调"""
        if self.plugin._is_terminated: return
        task = asyncio.current_task()
        self.plugin._bg_tasks.add(task)
        try:
            await self.execute_briefing_share()
        finally:
            self.plugin._bg_tasks.discard(task)

    async def _task_wrapper_qzone(self):
        """QQ空间任务触发器（处理防抖与随机延迟记录）"""
        if self.plugin._is_terminated: return
        
        trigger_mode = self.qzone_conf.get("qzone_trigger_mode", "cron")
        random_delay_min = 0
        if trigger_mode == "cron":
            try:
                random_delay_min = int(self.basic_conf.get("cron_random_delay", 0))
            except Exception:
                pass

        if random_delay_min > 0:
            delay_seconds = random.randint(0, random_delay_min * 60)
            if delay_seconds > 0:
                target_time = datetime.now() + timedelta(seconds=delay_seconds)
                time_str = target_time.strftime('%H:%M:%S')
                
                await self.db.update_state_dict("qzone", {
                    "pending_delay_job": {"target_time": target_time.timestamp()}
                })
                
                self.scheduler.add_job(
                    self._execute_delayed_qzone_task, 'date',
                    run_date=target_time,
                    id="delayed_qzone_share",
                    replace_existing=True
                )
                logger.debug(f"[DailySharing] QQ空间任务已触发，将随机延迟 {delay_seconds/60:.1f} 分钟，预计于 {time_str} 执行...")
                return

        await self._execute_delayed_qzone_task()

    async def _execute_delayed_qzone_task(self):
        """实际执行QQ空间分享任务"""
        if self.plugin._is_terminated: return
        task = asyncio.current_task()
        self.plugin._bg_tasks.add(task)
        
        try:
            await self.db.update_state_dict("qzone", {"pending_delay_job": None})

            # 为了安全，这里也加上互斥锁，防止和群聊同时生成触发大模型并发限制            
            async with self._lock:
                logger.info("[DailySharing] 开始执行QQ空间分享任务...")
                await self.execute_qzone_share()
                
        finally:
            self.plugin._bg_tasks.discard(task)

    def get_curr_period(self) -> TimePeriod:
        h = datetime.now().hour
        if 0 <= h < 6: return TimePeriod.DAWN
        if 6 <= h < 9: return TimePeriod.MORNING
        if 9 <= h < 12: return TimePeriod.FORENOON
        if 12 <= h < 16: return TimePeriod.AFTERNOON
        if 16 <= h < 19: return TimePeriod.EVENING
        if 19 <= h < 22: return TimePeriod.NIGHT
        return TimePeriod.LATE_NIGHT

    def get_period_range_str(self, period: TimePeriod) -> str:
        """获取时段对应的时间范围字符串"""
        return {
            TimePeriod.DAWN: "00:00-06:00",            
            TimePeriod.MORNING: "06:00-09:00",
            TimePeriod.FORENOON: "09:00-12:00",
            TimePeriod.AFTERNOON: "12:00-16:00",
            TimePeriod.EVENING: "16:00-19:00",
            TimePeriod.NIGHT: "19:00-22:00",
            TimePeriod.LATE_NIGHT: "22:00-24:00"
        }.get(period, "")

    async def decide_type_with_state(self, current_period: TimePeriod, is_qzone: bool = False, target_id: str = None, specific_type: str = "auto") -> SharingType:
        """带目标ID状态的分享类型决定，支持自定义列表轮换"""
        # 获取状态存储的 Key。QQ空间用 "qzone"；普通会话根据 ID 存储独立状态
        if is_qzone:
            state_key = "qzone"
        else:
            state_key = f"target_{target_id}" if target_id else "global"
            
        state = await self.db.get_state(state_key, {})

        # 处理用户填写的逗号自定义序列
        if specific_type and specific_type.lower() != "auto":
            # 兼容中英文字符
            seq_str = specific_type.replace("，", ",")
            custom_seq = [s.strip().lower() for s in seq_str.split(",") if s.strip()]
            
            # 如果解析出来的列表不仅仅只有一个 "auto"
            if custom_seq and custom_seq != ["auto"]:
                idx_key = "custom_sequence_index"
                idx = state.get(idx_key, 0)
                if idx >= len(custom_seq): idx = 0
                
                selected_str = custom_seq[idx]
                next_idx = (idx + 1) % len(custom_seq)
                
                # 保存这个群独立的序列进度
                await self.db.update_state_dict(state_key, {
                    idx_key: next_idx, 
                    "last_timestamp": datetime.now().isoformat()
                })
                
                # 如果当前轮到的单词不是 auto，直接返回该类型
                if selected_str != "auto":
                    try: 
                        return SharingType(selected_str)
                    except ValueError:
                        pass # 如果用户拼写错误导致无法识别，忽略并进入下方兜底
                
                # 如果轮到的单词刚好是 "auto"，系统会直接无视上面的返回，
                # 顺滑地进入下方的“按当前时间段智能选择”代码块！

        # 原有的按时间段智能判断序列（兜底与 Auto 专用）
        conf_node = self.qzone_conf if is_qzone else self.basic_conf
        
        # 映射序列前缀
        prefix = "qzone_" if is_qzone else ""
        config_key_map = {
            TimePeriod.MORNING: f"{prefix}morning_sequence",
            TimePeriod.FORENOON: f"{prefix}forenoon_sequence",
            TimePeriod.AFTERNOON: f"{prefix}afternoon_sequence",
            TimePeriod.EVENING: f"{prefix}evening_sequence",
            TimePeriod.NIGHT: f"{prefix}night_sequence",
            TimePeriod.LATE_NIGHT: f"{prefix}late_night_sequence",
            TimePeriod.DAWN: f"{prefix}dawn_sequence"
        }
        
        config_key = config_key_map.get(current_period)
        seq = conf_node.get(config_key, [])
        
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])
        
        idx_key = f"index_{current_period.value}"
        idx = state.get(idx_key, 0)
        
        if idx >= len(seq): idx = 0
        selected = seq[idx]
        next_idx = (idx + 1) % len(seq)
        
        updates = {
            "last_period": current_period.value,
            idx_key: next_idx,            
            "sequence_index": next_idx,  
            "last_timestamp": datetime.now().isoformat(),
            "last_type": selected
        }
        await self.db.update_state_dict(state_key, updates)
        
        try: return SharingType(selected)
        except: return SharingType.GREETING

    def _parse_targets_config(self, conf_list):
        """核心解析器：支持 群号:Cron时间:类型 这种三段式复杂写法"""
        if isinstance(conf_list, dict): return conf_list
        res = {}
        if isinstance(conf_list, list):
            for item in conf_list:
                s = str(item).strip()
                if not s: continue
                # 支持中英文冒号混用                
                s = s.replace("：", ":")
                parts = [p.strip() for p in s.split(":")]
                
                target_id = parts[0]
                if len(parts) == 1:
                    # 只有群号
                    res[target_id] = {"cron": None, "seq": None}
                elif len(parts) == 2:
                    # 只有群号和类型
                    res[target_id] = {"cron": None, "seq": parts[1]}
                elif len(parts) >= 3:
                    # 群号 : 时间 : 类型 (例如 123456:0 7 * * *:news)
                    cron_str = ":".join(parts[1:-1]).strip()
                    seq_str = parts[-1].strip()
                    res[target_id] = {"cron": cron_str, "seq": seq_str}
        return res

    def get_broadcast_targets(self, exclude_custom_cron=False):
        """辅助方法：获取需要广播的目标列表。exclude_custom_cron 启用时会跳过有独立时间的群"""
        targets = []
        default_adapter_id = self.plugin._cached_adapter_id
        
        # 1. 从上下文获取平台管理器，找到第一个有 ID 的平台实例
        if not default_adapter_id:
            try:
                if hasattr(self.plugin.context, "platform_manager"):
                    insts = self.plugin.context.platform_manager.get_insts()
                    for inst in insts:
                        if hasattr(inst, "metadata") and inst.metadata.id:
                            default_adapter_id = inst.metadata.id
                            self.plugin._cached_adapter_id = default_adapter_id
                            logger.debug(f"[DailySharing] 自动发现并缓存 Bot ID: {default_adapter_id}")
                            break
            except Exception as e:
                logger.warning(f"[DailySharing] 尝试自动发现 Bot ID 失败: {e}")

        # 2. 如果还是没找到，才使用默认值兜底
        if not default_adapter_id:
             default_adapter_id = "aiocqhttp"
             logger.warning("[DailySharing] 尚未缓存 Adapter ID，使用默认值 'aiocqhttp'。")

        if default_adapter_id:
            # 解析配置为字典（支持冒号写法）
            r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
            r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

            for gid, conf in r_groups.items():
                if gid:
                    # 如果全局广播开启了排除，且这个群有独立定时，跳过！
                    if exclude_custom_cron and isinstance(conf, dict) and conf.get("cron"):
                        continue
                    targets.append(f"{default_adapter_id}:GroupMessage:{gid}")
            for uid, conf in r_users.items():
                if uid:
                    if exclude_custom_cron and isinstance(conf, dict) and conf.get("cron"):
                        continue
                    targets.append(f"{default_adapter_id}:FriendMessage:{uid}")
        
        return targets

    def get_briefing_targets(self):
        """获取早报的独立广播目标，不填则不发"""
        targets = []
        default_adapter_id = self.plugin._cached_adapter_id
        
        if not default_adapter_id:
            try:
                if hasattr(self.plugin.context, "platform_manager"):
                    insts = self.plugin.context.platform_manager.get_insts()
                    for inst in insts:
                        if hasattr(inst, "metadata") and inst.metadata.id:
                            default_adapter_id = inst.metadata.id
                            self.plugin._cached_adapter_id = default_adapter_id
                            break
            except Exception: pass

        if not default_adapter_id:
             default_adapter_id = "aiocqhttp"

        if default_adapter_id:
            b_groups = self.extra_shares_conf.get("briefing_groups", [])
            b_users = self.extra_shares_conf.get("briefing_users", [])

            for gid in b_groups:
                # 只取纯数字，防止用户误填冒号
                gid_clean = str(gid).split(":")[0].strip()
                if gid_clean: 
                    targets.append(f"{default_adapter_id}:GroupMessage:{gid_clean}")
            for uid in b_users:
                uid_clean = str(uid).split(":")[0].strip()
                if uid_clean: 
                    targets.append(f"{default_adapter_id}:FriendMessage:{uid_clean}")
        
        return targets

    async def async_daily_share_task(
        self,
        event: AstrMessageEvent,
        share_type: str,
        source: str,
        get_image: bool,
        need_image: bool,
        need_video: bool,
        need_voice: bool,
        to_qzone: bool
    ):
        """实际执行分享逻辑的后台任务 (LLM 触发)"""
        try:
            # 特殊图片类型处理 (60s / AI) 
            st_clean = share_type.lower().replace(" ", "")
            
            # 60s新闻
            if any(k in st_clean for k in ["60s", "六十秒", "读世界"]):
                url = self.news_service.get_60s_image_url()
                if not url:
                    await event.send(event.plain_result("获取 每天60s读世界 失败，请检查API Key配置。"))
                    return 
                    
                if to_qzone:
                    qzone_plugin = self.ctx_service._find_plugin("qzone")
                    if qzone_plugin and hasattr(qzone_plugin, "service"):
                        self.plugin._inject_qzone_client(qzone_plugin)
                        try:
                            await qzone_plugin.service.publish_post(text="【每天60秒读懂世界】", images=[url])
                            await event.send(event.plain_result("每天60s读世界 已成功分享到QQ空间！"))
                            await self.db.add_sent_history("qzone_broadcast", "news", "【每天60秒读懂世界】", True)
                        except Exception as e:
                            await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                    else:
                        await event.send(event.plain_result("未检测到QQ空间插件！"))
                else:
                    await event.send(event.image_result(url))
                return 

            # AI资讯
            if any(k in st_clean for k in ["ai资讯", "ai新闻", "ai日报"]) or st_clean == "ai":
                ai_data = await self.news_service.get_ai_news_json()
                if not ai_data:
                    await event.send(event.plain_result("获取 AI资讯快报 失败，今日暂无更新。"))
                    return 

                url = self.news_service.get_ai_news_image_url()
                if not url:
                    await event.send(event.plain_result("获取 AI资讯快报 图片失败，请检查API Key配置。"))
                    return 
                    
                if to_qzone:
                    qzone_plugin = self.ctx_service._find_plugin("qzone")
                    if qzone_plugin and hasattr(qzone_plugin, "service"):
                        self.plugin._inject_qzone_client(qzone_plugin)
                        try:
                            await qzone_plugin.service.publish_post(text="【AI资讯快报】", images=[url])
                            await event.send(event.plain_result("AI资讯快报 已成功分享到QQ空间！"))
                            await self.db.add_sent_history("qzone_broadcast", "news", "【AI资讯快报】", True)
                        except Exception as e:
                            await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                    else:
                        await event.send(event.plain_result("未检测到QQ空间插件！"))
                else:
                    await event.send(event.image_result(url))
                return 

            # === 常规流程 ===
            # 参数清洗与映射
            target_type_enum = None
            
            if share_type == "自动" or share_type == "auto":
                target_type_enum = None  
            else:
                # 映射分享类型 (中文 -> 枚举)
                if share_type in CMD_CN_MAP:
                    target_type_enum = CMD_CN_MAP[share_type]
                else:
                    # 模糊匹配尝试
                    for k, v in CMD_CN_MAP.items():
                        if k in share_type:
                            target_type_enum = v
                            break
                if not target_type_enum:
                    await event.send(event.plain_result(f"不支持的分享类型：{share_type}。支持：自动, 问候, 新闻, 心情, 知识, 推荐, 60s新闻, AI资讯。"))
                    return

            # 映射新闻源 (中文 -> key)
            news_src_key = None
            if target_type_enum == SharingType.NEWS and source:
                if source in SOURCE_CN_MAP:
                    news_src_key = SOURCE_CN_MAP[source]
                elif source in NEWS_SOURCE_MAP:
                    news_src_key = source
                else:
                    for name, key in SOURCE_CN_MAP.items():
                        if name in source or source in name:
                            news_src_key = key
                            break
            
            # 逻辑判定：新闻默认发静态图
            is_news = (target_type_enum == SharingType.NEWS)
            
            # 触发静态图发送的条件：
            if is_news and get_image and not need_image and not need_voice and not need_video:
                try:
                    img_url = None
                    src_name = ""
                    # 优先使用指定的源热搜
                    if news_src_key:
                        img_url, src_name = self.news_service.get_hot_news_image_url(news_src_key)
                    else:
                        # 如果没有指定，则随机选择一个已启用的新闻源发送
                        random_src = self.news_service.select_news_source()
                        img_url, src_name = self.news_service.get_hot_news_image_url(random_src)

                    if img_url:
                        if to_qzone:
                            qzone_plugin = self.ctx_service._find_plugin("qzone")
                            if qzone_plugin and hasattr(qzone_plugin, "service"):
                                self.plugin._inject_qzone_client(qzone_plugin)
                                try:
                                    await qzone_plugin.service.publish_post(text=f"【{src_name}】", images=[img_url])
                                    await event.send(event.plain_result(f"[{src_name}] 图片已成功分享到QQ空间！"))
                                    await self.db.add_sent_history("qzone_broadcast", "news", f"【{src_name}】长图(LLM)", True)
                                except Exception as e:
                                    await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                            else:
                                await event.send(event.plain_result("未检测到QQ空间插件！"))
                        else:
                            await event.send(event.image_result(img_url))
                    else:
                        await event.send(event.plain_result("获取新闻图片失败。"))
                except Exception as e:
                    logger.error(f"[DailySharing] 获取新闻图片失败: {e}")
                    await event.send(event.plain_result(f"获取新闻图片失败。"))
                
                return

            # 如果用户要求发QQ空间文案说说
            if to_qzone:
                await self.execute_qzone_share(force_type=target_type_enum, news_source=news_src_key, event=event)
                return

            # 场景 B: 标准 LLM 生成流程
            
            # 获取上下文 ID
            uid = event.get_sender_id()
            if not ":" in str(uid):
                target_umo = event.unified_msg_origin
            else:
                target_umo = uid

            # 重新计算时段
            period = self.get_curr_period()
            
            # 准备数据
            life_ctx = await self.ctx_service.get_life_context()
            news_data = None
            
            # 初始化 img_path (可能用于存放热搜截图)
            img_path = None
            
            if target_type_enum == SharingType.NEWS:
                # 这里的 news_src_key 如果是 None 会自动选择
                if not news_src_key:
                    news_src_key = self.news_service.select_news_source()
                news_data = await self.news_service.get_hot_news(news_src_key)
                
                # 如果在主流程中且配置允许带上新闻图
                if get_image and not need_image and self.image_conf.get("attach_hot_news_image", True):
                    try:
                        img_path, _ = self.news_service.get_hot_news_image_url(news_src_key)
                    except Exception as e:
                        logger.warning(f"[DailySharing] 主流程获取热搜图片失败: {e}")

            # 获取历史
            is_group = self.ctx_service._is_group_chat(target_umo)
            hist_data = await self.ctx_service.get_history_data(target_umo, is_group)
            hist_prompt = self.ctx_service.format_history_prompt(hist_data, target_type_enum)
            group_info = hist_data.get("group_info")
            life_prompt = self.ctx_service.format_life_context(life_ctx, target_type_enum, is_group, group_info)
            
            # 获取近期动态记忆
            recent_dynamics_str = ""
            ref_count = self.context_conf.get("reference_history_count", 3)
            if ref_count > 0:
                recent_hist = await self.db.get_recent_history_by_target(uid, limit=ref_count)
                if recent_hist:
                    lines = []
                    for h in reversed(recent_hist):
                        clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', h.get('content', ''), flags=re.IGNORECASE).strip()
                        lines.append(f"- [{h.get('type')}] {clean_content}")
                    recent_dynamics_str = "\n".join(lines)

            # 获取昵称
            nickname = ""
            if not is_group:
                nickname = event.get_sender_name()

            # 生成内容
            content = await self.content_service.generate(
                target_type_enum, period, target_umo, is_group, life_prompt, hist_prompt, news_data, nickname=nickname, recent_dynamics=recent_dynamics_str
            )
            
            if not content:
                await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                return
            
            # ================= 视觉生成逻辑 =================
            video_url = None
            should_gen_visual = False
            
            if self.image_conf.get("enable_ai_image", False):
                if need_image or need_video:
                    should_gen_visual = True

            if should_gen_visual:
                # 生成图片 (注意：如果生成了AI图片，会覆盖上面的热搜截图 img_path)
                ai_img_path = await self.image_service.generate_image(content, target_type_enum, life_ctx)
                if ai_img_path:
                    img_path = ai_img_path
                
                # 生成视频 (如果明确要求视频)
                if img_path and self.image_conf.get("enable_ai_video", False):
                    if need_video:
                        video_url = await self.image_service.generate_video_from_image(img_path, content)

            # ================= 语音生成逻辑 =================
            audio_path = None
            if self.tts_conf.get("enable_tts", False):
                should_gen_voice = False
                if need_voice:
                    should_gen_voice = True
                        
                if should_gen_voice:
                    audio_path = await self.ctx_service.text_to_speech(content, target_umo, target_type_enum, period)

            # 发送 (img_path 可能是热搜截图，也可能是AI画的图)
            await self.send(target_umo, content, img_path, audio_path, video_url)
            
            # 记录上下文
            img_desc = self.image_service.get_last_description()
            await self.ctx_service.record_bot_reply_to_history(target_umo, content, image_desc=img_desc)
            await self.ctx_service.record_to_memos(target_umo, content, img_desc)
                
        except Exception as e:
            logger.error(f"[DailySharing] 异步任务错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"执行出错: {str(e)}"))

    async def execute_briefing_share(self, specific_target: str = None):
        """执行早报分享：依次发送开启的 60s 和 AI 资讯"""
        if self.plugin._is_terminated: return
        
        logger.info("[DailySharing] 开始执行早报分享任务")
        
        # 1. 收集需要分享的图片 URL
        images_to_send = [] 
        
        check_60s = self.extra_shares_conf.get("enable_60s_news", False)
        if specific_target: check_60s = True 
        
        if self.extra_shares_conf.get("enable_60s_news", False):
            url = self.news_service.get_60s_image_url()
            if url: images_to_send.append(("每天60s读世界", url))

        if self.extra_shares_conf.get("enable_ai_news", False):
            ai_data = await self.news_service.get_ai_news_json()
            if ai_data:
                url = self.news_service.get_ai_news_image_url()
                if url: images_to_send.append(("AI资讯快报", url))
            else:
                logger.info("[DailySharing] 获取 AI资讯快报 失败，今日暂无更新，跳过分享图片")

        if not images_to_send:
            logger.warning("[DailySharing] 早报任务触发，发现没有开启的早报发送或获取图片失败")
            return

        # 定时早报自动同步到QQ空间
        if specific_target is None and self.extra_shares_conf.get("sync_briefing_to_qzone", False):
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if qzone_plugin and hasattr(qzone_plugin, "service"):
                self.plugin._inject_qzone_client(qzone_plugin)
                logger.info("[DailySharing] 分享早报到QQ空间已开启...")
                for name, url in images_to_send:
                    try:
                        title = "【每天60秒读懂世界】" if "60s" in name else "【AI资讯快报】"
                        await qzone_plugin.service.publish_post(text=title, images=[url])
                        await self.db.add_sent_history("qzone_broadcast", "news", f"{title}(定时自动)", True)
                        await asyncio.sleep(3) 
                        logger.info(f"[DailySharing] 分享早报 {name} 到QQ空间成功！")
                    except Exception as e:
                        logger.error(f"[DailySharing] 分享早报 {name} 到QQ空间失败: {e}")
            else:
                logger.warning("[DailySharing] 分享早报到QQ空间开启，但未检测到 astrbot_plugin_qzone 插件")

        # 2. 确定目标 (使用全新的独立列表)
        targets = []
        if specific_target:
            targets.append(specific_target)
        else:
            targets = self.get_briefing_targets()
            logger.info(f"[DailySharing] 早报将分享到 {len(targets)} 个目标会话")

        if not targets:
            logger.info("[DailySharing] 未配置任何早报接收目标，已跳过分享。")
            return

        # 3. 分享循环
        for uid in targets:
            if self.plugin._is_terminated: break
            try:
                for name, url in images_to_send:
                    # 构建消息链
                    msg = MessageChain().url_image(url)
                    logger.info(f"[DailySharing] 正在分享 {name} 到 {uid}")
                    await self.plugin.context.send_message(uid, msg)
                    # 每张图之间间隔 1 秒
                    await asyncio.sleep(1)
                
                # 每个群之间间隔 2 秒
                await asyncio.sleep(2) 
            except Exception as e:
                logger.error(f"[DailySharing] 分享早报到 {uid} 失败: {e}")

    async def execute_share(self, force_type: SharingType = None, news_source: str = None, specific_target: str = None):
        """执行分享的主流程（支持群聊私聊独立配置与记忆序列）"""
        if self.plugin._is_terminated: return

        period = self.get_curr_period()
        life_ctx = await self.ctx_service.get_life_context()

        targets = []
        
        # 1. 确定分享目标
        if specific_target:
            targets.append(specific_target)
        else:
            # 如果是被全局大定时器唤醒，排除掉那些配置了独立定时的群，绝不打扰它们
            targets = self.get_broadcast_targets(exclude_custom_cron=True)

        if not targets:
            logger.warning("[DailySharing] 未配置接收对象，且未指定目标，请在配置页填写群号或QQ号")
            return

        # 加载并解析带冒号的独立配置
        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

        for uid in targets:
            if self.plugin._is_terminated: break
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower() or "guild" in uid.lower()
                
                # 提取纯数字ID用于读取字典配置
                adapter_id, real_id = self.ctx_service._parse_umo(uid)
                
                # 读取该群聊、私聊独立的类型策略配置（默认 fallback 为 global 设定的 sharing_type）
                target_specific_type = self.basic_conf.get("sharing_type", "auto")
                if is_group and real_id in r_groups:
                    conf = r_groups[real_id]
                    st = conf.get("seq") if isinstance(conf, dict) else conf
                    if st is not None: target_specific_type = st
                elif not is_group and real_id in r_users:
                    conf = r_users[real_id]
                    st = conf.get("seq") if isinstance(conf, dict) else conf
                    if st is not None: target_specific_type = st

                # 为该目标决定当前的分享类型
                if force_type:
                    stype = force_type
                else:
                    stype = await self.decide_type_with_state(period, is_qzone=False, target_id=uid, specific_type=target_specific_type)

                logger.info(f"[DailySharing] 正在为 {uid} 生成内容... 时段: {period.value}, 类型: {stype.value}")
                
                # 独立获取该目标的新闻数据与去重
                news_data = None
                if stype == SharingType.NEWS:
                    state = await self.db.get_state(f"target_{uid}", {})
                    last_news_source = state.get("last_news_source")
                    
                    current_news_source = news_source
                    if not current_news_source:
                        current_news_source = self.news_service.select_news_source(excluded_source=last_news_source)
                        
                    news_data = await self.news_service.get_hot_news(current_news_source)
                    if news_data:
                        await self.db.update_state_dict(f"target_{uid}", {"last_news_source": news_data[1]})

                # 尝试获取用户昵称 (仅限私聊) 
                nickname = ""
                if not is_group:
                    try:
                        if adapter_id and real_id:
                            bot = self.ctx_service._get_bot_instance(adapter_id)
                            if bot:
                                ret = await bot.api.call_action("get_stranger_info", user_id=int(real_id))
                                if ret and isinstance(ret, dict):
                                    nickname = ret.get("remark")
                                    logger.info(f"[DailySharing] 获取到用户备注: {nickname}")
                                    if nickname != "":
                                        logger.info(f"[DailySharing] 获取到用户备注: {nickname}")
                                    else:
                                        nickname = ret.get("nickname", "")
                                        logger.info(f"[DailySharing] 获取到用户昵称: {nickname}")
                    except Exception as e:
                         logger.warning(f"[DailySharing] 获取昵称失败: {e}")

                hist_data = await self.ctx_service.get_history_data(uid, is_group)
                if is_group and "group_info" in hist_data:
                    # 手动触发时通常忽略策略检查，但自动触发时需要检查
                    if not specific_target and not self.ctx_service.check_group_strategy(hist_data["group_info"]):
                        logger.info(f"[DailySharing] 因策略跳过群组 {uid}")
                        continue

                hist_prompt = self.ctx_service.format_history_prompt(hist_data, stype)
                group_info = hist_data.get("group_info")
                life_prompt = self.ctx_service.format_life_context(life_ctx, stype, is_group, group_info)

                # 获取近期动态记忆
                recent_dynamics_str = ""
                ref_count = self.context_conf.get("reference_history_count", 3)
                if ref_count > 0:
                    recent_hist = await self.db.get_recent_history_by_target(uid, limit=ref_count)
                    if recent_hist:
                        lines = []
                        for h in reversed(recent_hist):  
                            clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', h.get('content', ''), flags=re.IGNORECASE).strip()
                            lines.append(f"- [{h.get('type')}] {clean_content}")
                        recent_dynamics_str = "\n".join(lines)

                content = await self.content_service.generate(
                    stype, period, uid, is_group, life_prompt, hist_prompt, news_data, nickname=nickname, recent_dynamics=recent_dynamics_str
                )
                
                if not content:
                    logger.warning(f"[DailySharing] 内容生成失败 {uid}")
                    await self.db.add_sent_history(
                        target_id=uid,
                        sharing_type=stype.value,
                        content="生成失败 (LLM无响应)",
                        success=False
                    )
                    continue
                
                # 生成多媒体素材 (图片 & 视频 & 语音) 
                
                # 1. 配图生成逻辑
                img_path = None
                video_url = None
                enable_img_global = self.image_conf.get("enable_ai_image", False)
                img_allowed_types = self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
                
                # 【新闻类型特殊处理】如果未开启AI配图或当前类型不允许AI配图，但这是新闻，且配置允许附带热搜图，尝试把热搜图带上
                if stype == SharingType.NEWS and self.image_conf.get("attach_hot_news_image", True):
                    try:
                        # 查找独立目标对应的上一个新闻源
                        state = await self.db.get_state(f"target_{uid}", {})
                        last_source = state.get("last_news_source")
                        if last_source:
                            img_path, _ = self.news_service.get_hot_news_image_url(last_source)
                    except Exception as e:
                        logger.warning(f"[DailySharing] 自动任务获取新闻图片失败: {e}")

                if enable_img_global:
                    if stype.value in img_allowed_types:
                        ai_img_path = await self.image_service.generate_image(content, stype, life_ctx)
                        if ai_img_path:
                            # AI 图片覆盖热搜截图
                            img_path = ai_img_path
                            
                        # 尝试生成视频
                        if img_path and self.image_conf.get("enable_ai_video", False):
                            video_allowed = self.image_conf.get("video_enabled_types", ["greeting", "mood"])
                            if stype.value in video_allowed:
                                video_url = await self.image_service.generate_video_from_image(img_path, content)
                    else:
                         logger.info(f"[DailySharing] 当前类型 {stype.value} 不在配图允许列表，跳过配图。")

                # 2. 语音生成逻辑
                audio_path = None
                enable_tts_global = self.tts_conf.get("enable_tts", False)
                tts_allowed_types = self.tts_conf.get("tts_enabled_types", ["greeting", "mood"])
                
                if enable_tts_global:
                    if stype.value in tts_allowed_types:
                        # 传入 stype 和 period 以确定情感
                        audio_path = await self.ctx_service.text_to_speech(content, uid, stype, period)
                    else:
                        logger.info(f"[DailySharing] 当前类型 {stype.value} 不在语音允许列表，跳过语音。")

                # 分享内容
                await self.send(uid, content, img_path, audio_path, video_url)
                
                # 获取图片描述并写入 AstrBot 聊天上下文
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_bot_reply_to_history(uid, content, image_desc=img_desc)

                # 记录与历史
                await self.ctx_service.record_to_memos(uid, content, img_desc)

                # 清洗历史记录内容中的情感标签
                clean_content_for_log = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()

                await self.db.add_sent_history(
                    target_id=uid,
                    sharing_type=stype.value,
                    content=clean_content_for_log[:100] + "...",
                    success=True
                )
                
                await asyncio.sleep(2) 

            except Exception as e:
                logger.error(f"[DailySharing] 处理 {uid} 时出错: {e}")
                import traceback
                logger.error(traceback.format_exc())               

    async def execute_qzone_share(self, force_type: SharingType = None, news_source: str = None, event: AstrMessageEvent = None):
        """完全独立的 QQ 空间执行主流程"""
        if self.plugin._is_terminated: return
        
        try:
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if not qzone_plugin or not hasattr(qzone_plugin, "service"):
                logger.warning("[DailySharing] QQ空间任务触发，但未检测到 astrbot_plugin_qzone 插件")
                if event:
                    await event.send(event.plain_result("未检测到 astrbot_plugin_qzone 插件"))
                return

            self.plugin._inject_qzone_client(qzone_plugin)
            period = self.get_curr_period()
            # 注意这里传入 is_qzone=True，使用独立序列
            stype = force_type if force_type else await self.decide_type_with_state(period, is_qzone=True) 
            logger.info(f"[DailySharing] QQ空间时段: {period.value}, 类型: {stype.value}")

            # 获取生活上下文
            life_ctx = await self.ctx_service.get_life_context()
            news_data = None
            
            # 如果是发新闻，单独获取热搜（支持手动指定源）
            if stype == SharingType.NEWS:
                state = await self.db.get_state("qzone", {})
                last_news_source = state.get("last_news_source")

                actual_source = news_source
                if not actual_source:
                    actual_source = self.news_service.select_news_source(excluded_source=last_news_source)
                    
                news_data = await self.news_service.get_hot_news(actual_source)
                if news_data:
                    await self.db.update_state_dict("qzone", {"last_news_source": news_data[1]})

            # 屏蔽历史记录，使用纯净的提示词让LLM写说说
            qzone_life_prompt = self.ctx_service.format_life_context(life_ctx, stype, False, None)
            qzone_life_prompt += (
                "\n\n【最高优先级覆盖指令】\n"
                "这是一条个人QQ空间社交平台的动态说说\n"
                "当前任务是以纯粹的【个人日记或心情独白】的口吻来写。\n"
                "1. 请以你的人设性格说话，真实自然\n"
                "2. 只能专注描绘自己的状态，就像自己在自言自语一样。"
            )
            
            # 获取近期动态记忆 (QQ空间)
            qzone_recent_dynamics_str = ""
            ref_count = self.context_conf.get("reference_history_count", 3)
            if ref_count > 0:
                q_recent_hist = await self.db.get_recent_history_by_target("qzone_broadcast", limit=ref_count)
                if q_recent_hist:
                    lines = []
                    for h in reversed(q_recent_hist):
                        clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', h.get('content', ''), flags=re.IGNORECASE).strip()
                        lines.append(f"- [{h.get('type')}] {clean_content}")
                    qzone_recent_dynamics_str = "\n".join(lines)

            logger.info("[DailySharing] 正在为QQ空间生成文案...")
            qzone_content = await self.content_service.generate(
                stype, period, "qzone_broadcast", False, qzone_life_prompt, "", news_data, nickname="", recent_dynamics=qzone_recent_dynamics_str
            )
            
            if not qzone_content:
                logger.error("[DailySharing] QQ空间文案生成失败")
                if event:
                    await event.send(event.plain_result("QQ空间文案生成失败"))
                return

            # 清洗情感标签
            clean_qzone_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', qzone_content, flags=re.IGNORECASE).strip()

            # 处理配图逻辑
            qzone_images = []
            target_local_img = None
            
            enable_img_qzone = self.qzone_conf.get("qzone_enable_image", False)
            enable_img_global = self.image_conf.get("enable_ai_image", False)
            
            # 获取QQ空间配图允许类型，如果没配置，默认复用群聊分享的配置
            qzone_img_allowed_types = self.qzone_conf.get(
                "qzone_image_enabled_types", 
                self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
            )

            if enable_img_qzone and enable_img_global:
                if stype.value in qzone_img_allowed_types:
                    logger.info("[DailySharing] 正在为QQ空间生成配图...")
                    try:
                        new_img_path = await self.image_service.generate_image(clean_qzone_content, stype, life_ctx)
                        if new_img_path:
                            target_local_img = new_img_path
                    except Exception as e:
                        logger.error(f"[DailySharing] QQ空间配图生成失败: {e}")
                else:
                    logger.info(f"[DailySharing] 当前类型 {stype.value} 不在QQ空间配图允许列表，跳过配图。")
            
            # 如果是新闻类型，且没有开启画图，且配置允许附带热搜图，尝试贴热搜图
            if stype == SharingType.NEWS and not target_local_img and self.qzone_conf.get("qzone_attach_hot_news_image", True):
                try:
                    if news_data:
                        img_url, _ = self.news_service.get_hot_news_image_url(news_data[1])
                        target_local_img = img_url
                except Exception as e:
                    pass

            if target_local_img:
                if target_local_img.startswith("http"):
                    qzone_images.append(target_local_img)
                else:
                    qzone_images.append(f"local_path::{target_local_img}")
                            
            import sys
            import aiofiles
            qzone_utils_mod = None
            for mod_name, mod in sys.modules.items():
                if "qzone" in mod_name and "utils" in mod_name and hasattr(mod, "download_file"):
                    qzone_utils_mod = mod
                    break
                    
            if qzone_utils_mod:
                orig_download_file = qzone_utils_mod.download_file
                async def patched_download_file(url: str):
                    if isinstance(url, str) and url.startswith("local_path::"):
                        real_path = url.split("::", 1)[1]
                        try:
                            async with aiofiles.open(real_path, "rb") as f:
                                return await f.read()  
                        except Exception:
                            return None
                    return await orig_download_file(url)
                qzone_utils_mod.download_file = patched_download_file
                
            try:
                await qzone_plugin.service.publish_post(
                    text=clean_qzone_content,
                    images=qzone_images
                )
                logger.info("[DailySharing] 成功分享内容到QQ空间！")
                
                await self.db.add_sent_history(
                    target_id="qzone_broadcast",
                    sharing_type=stype.value,
                    content=clean_qzone_content[:100] + "...",
                    success=True
                )
                
                if event:
                    try:
                        text_chain = MessageChain().message(clean_qzone_content)
                        await event.send(text_chain)
                        
                        if target_local_img:
                            await asyncio.sleep(1.0) 
                            img_chain = MessageChain()
                            if target_local_img.startswith("http"):
                                img_chain.url_image(target_local_img)
                            else:
                                img_chain.file_image(target_local_img)
                            await event.send(img_chain)
                    except Exception as e:
                        logger.error(f"[DailySharing] 同步发送内容到会话失败: {e}")
                
            finally:
                if qzone_utils_mod:
                    qzone_utils_mod.download_file = orig_download_file

        except Exception as e:
            logger.error(f"[DailySharing] 生成并分享到QQ空间失败: {e}")
            if event:
                try:
                    await event.send(event.plain_result(f"生成并分享到QQ空间失败: {e}"))
                except:
                    pass

    async def send(self, uid, text, img_path, audio_path=None, video_url=None):
        """分享内容（支持分开分享，支持语音和视频）"""
        if self.plugin._is_terminated: return

        try:
            separate_img = self.image_conf.get("separate_text_and_image", True)
            prefer_audio_only = self.tts_conf.get("prefer_audio_only", False)
            
            # 清洗情感标签
            clean_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', text, flags=re.IGNORECASE).strip()
            
            # 判断是否应该分享文字
            # 如果有语音，且开启了“仅发语音”，则不发文字
            should_send_text = True
            if audio_path and prefer_audio_only:
                should_send_text = False

            # 1. 分享文字（如果需要）
            if should_send_text and clean_text: 
                text_chain = MessageChain().message(clean_text) 
                # 如果图片不分开分享，且没有语音，且没有视频（视频无法合并），则合并图片
                if img_path and not video_url and not separate_img and not audio_path:
                    if img_path.startswith("http"): text_chain.url_image(img_path)
                    else: text_chain.file_image(img_path)
                
                await self.plugin.context.send_message(uid, text_chain)
                
                # 如果后续还有消息，进行随机延迟
                if audio_path or ((img_path or video_url) and separate_img):
                    await self.random_sleep()

            # 2. 分享语音（如果有）
            if audio_path:
                audio_chain = MessageChain()
                audio_chain.chain.append(Record(file=audio_path))
                await self.plugin.context.send_message(uid, audio_chain)
                
                # 如果后续还有视觉媒体，延迟
                if (img_path or video_url) and separate_img:
                    await self.random_sleep()
            
            # 3. 分享视觉媒体（视频优先，其次图片）
            if video_url:
                # 分享视频
                video_chain = MessageChain()
                # 判断是本地文件还是网络URL
                if video_url.startswith("http"):
                    video_chain.chain.append(Video.fromURL(video_url))
                else:
                    # 如果是本地路径，使用 fromFile
                    video_chain.chain.append(Video.fromFileSystem(video_url))              
                await self.plugin.context.send_message(uid, video_chain)
            elif img_path:
                # 分享图片（如果视频没生成，或者视频关闭）
                img_not_sent_yet = separate_img or audio_path
                if img_not_sent_yet:
                    img_chain = MessageChain()
                    if img_path.startswith("http"): img_chain.url_image(img_path)
                    else: img_chain.file_image(img_path)
                    await self.plugin.context.send_message(uid, img_chain)

        except Exception as e:
            logger.error(f"[DailySharing] 分享内容给 {uid} 失败: {e}")

    async def random_sleep(self):
        """随机延迟"""
        if self.plugin._is_terminated: return

        delay_str = self.image_conf.get("separate_send_delay", "1.0-2.0")
        try:
            if "-" in str(delay_str):
                d_min, d_max = map(float, str(delay_str).split("-"))
                await asyncio.sleep(random.uniform(d_min, d_max))
            else:
                await asyncio.sleep(float(delay_str))
        except:
            await asyncio.sleep(1.5)
