import random
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger


class TaskSchedulerRandomMixin:
    """随机时段排程。"""

    def _parse_random_period(self, base_dt: datetime, period_str: str) -> tuple[datetime, datetime]:
        start_str, end_str = period_str.split('-', 1)
        start_h, start_m = map(int, start_str.split(':'))
        end_h, end_m = map(int, end_str.split(':'))

        start_dt = base_dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end_dt = base_dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        return start_dt, end_dt

    def _get_random_run_time(self, base_dt: datetime, period_str: str) -> Optional[datetime]:
        start_dt, end_dt = self._parse_random_period(base_dt, period_str)
        total_seconds = int((end_dt - start_dt).total_seconds())
        if total_seconds <= 0:
            return None

        return start_dt + timedelta(seconds=random.randrange(total_seconds))

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
                    run_time = self._get_random_run_time(now, period_str)
                    if run_time is None:
                        continue 
                    
                    jobs[period_str] = run_time.timestamp()
                    is_modified = True
                except Exception as e:
                    logger.error(f"[每日分享] 解析时间段 {period_str} 失败: {e}")
                    
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
                logger.debug(f"[每日分享] 今日随机任务 [{period_str}] 已安排在: {run_time.strftime('%H:%M:%S')} 分享")

    async def _schedule_daily_qzone_random_jobs(self):
        """QQ 空间随机时间计算。"""
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
                    run_time = self._get_random_run_time(now, period_str)
                    if run_time is None:
                        continue
                    
                    jobs[period_str] = run_time.timestamp()
                    is_modified = True
                except Exception as e:
                    logger.error(f"[每日分享] 解析 QQ 空间时间段 {period_str} 失败: {e}")
                    
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
                logger.debug(f"[每日分享] 今日 QQ 空间随机任务 [{period_str}] 已安排在: {run_time.strftime('%H:%M:%S')} 分享")

