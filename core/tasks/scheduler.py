from .common import *  # noqa: F401,F403


class TaskSchedulerMixin:
    """Cron registration, random-delay scheduling, and pending-job recovery."""

    def _parse_cron_to_kwargs(self, cron_str: str) -> Optional[dict]:
        """
        兼容解析 5/6/7 位的 Cron 表达式
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
            delay_seconds = random.randint(0, delay_minutes * 60)
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
                    f"[DailySharing] {log_label}已触发，将随机延迟 "
                    f"{delay_seconds/60:.1f} 分钟，预计于 {time_str} 执行..."
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

    def setup_tasks(self):
        self.setup_weixin_temp_cleanup()

        if not self.plugin.config.get("enable_auto_sharing", False):
            logger.debug("[DailySharing] 分享内容已禁用")
            return

        cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
        self.setup_cron(cron)
        logger.debug(f"[DailySharing] 分享内容定时任务已启动 ({cron})")
        
        # 扫描并注册所有带独立时间的独立定时任务
        self.setup_custom_target_crons()

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
        self.plugin._track_task(self._recover_pending_jobs())

    async def clear_pending_delay_jobs(self):
        """清理已记录但尚未执行的延迟任务，确保关闭后不会补发旧任务。"""
        await self.db.update_state_dict("global", {"pending_delay_job": None})
        await self.db.update_state_dict("qzone", {"pending_delay_job": None})
        await self.db.update_state_dict("briefing", {"pending_delay_job": None})

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))
        for target_id in list(r_groups.keys()) + list(r_users.keys()):
            if target_id:
                await self.db.update_state_dict(f"target_{target_id}", {"pending_delay_job": None})

    def setup_custom_target_crons(self):
        """解析并为写了独立时间的群聊、私聊挂载独立定时 (支持随机延迟)"""
        default_adapter_id = self._get_default_adapter_id(warn_on_fallback=False)

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

        # 清除旧的 custom_share 任务
        job_ids = [job.id for job in self.scheduler.get_jobs() if job.id.startswith("custom_share_")]
        for jid in job_ids:
            self.scheduler.remove_job(jid)

        def add_custom_job(target_id, is_group, cron_str):
            job_id = f"custom_share_{target_id}"
            target_umo = self._build_target_umo(target_id, is_group, default_adapter_id)
            if self._is_unsupported_weixin_group_target(target_umo, is_group):
                logger.warning(f"[DailySharing] weixin_oc 不支持群聊，已跳过独立定时目标: {target_id}")
                return
            
            async def delayed_custom_execute():
                async def run_custom_share():
                    logger.debug(f"[DailySharing] 独立时间到达，开始执行独立分享任务: {target_id}")
                    await self.execute_share(specific_target=target_umo)

                await self._run_tracked_pending_job(
                    f"target_{target_id}",
                    run_custom_share,
                    lock=self._lock,
                    locked_warning=f"[DailySharing] 独立任务 {target_id} 触发，系统繁忙排队中...",
                )

            async def custom_wrapper():
                if self.plugin._is_terminated: return
                
                # 独立群聊、私聊配置本身就是Cron触发，强制读取随机延迟配置
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
                logger.debug(f"[DailySharing] 独立群聊、私聊任务 [{target_id}] 已挂载独立定时: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 独立群聊、私聊任务 [{target_id}] 无效的Cron表达式 (支持5/6/7位): {cron_str}")

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

        # 早报任务恢复
        briefing_state = await self.db.get_state("briefing", {})
        b_pending = briefing_state.get("pending_delay_job")
        if b_pending:
            target_ts = b_pending.get("target_time", 0)
            if target_ts > now_ts:
                run_time = datetime.fromtimestamp(target_ts)
                self.scheduler.add_job(
                    self._execute_delayed_briefing_task, 'date',
                    run_date=run_time,
                    id="resume_briefing_share",
                    replace_existing=True
                )
                logger.debug(f"[DailySharing] 已恢复未完成的早报延迟任务，将在 {run_time.strftime('%H:%M:%S')} 执行")
            elif 0 <= now_ts - target_ts < 3600:
                run_time = now + timedelta(seconds=10)
                self.scheduler.add_job(
                    self._execute_delayed_briefing_task, 'date',
                    run_date=run_time,
                    id="resume_briefing_share",
                    replace_existing=True
                )
                logger.debug("[DailySharing] 检测到近期错过的早报延迟任务，即将执行补偿分享")
            else:
                await self.db.update_state_dict("briefing", {"pending_delay_job": None})

        # 独立群聊、私聊任务的延迟恢复
        default_adapter_id = self._get_default_adapter_id(warn_on_fallback=False)

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))
        all_targets = []
        for gid in r_groups.keys():
            if gid:
                all_targets.append((gid, True))
        for uid in r_users.keys():
            if uid:
                all_targets.append((uid, False))
        
        def recover_custom_job(tid, is_group):
            target_umo = self._build_target_umo(tid, is_group, default_adapter_id)
            async def delayed_recover():
                if self.plugin._is_terminated: return
                await self.db.update_state_dict(f"target_{tid}", {"pending_delay_job": None})
                async with self._lock:
                    logger.debug(f"[DailySharing] 补偿恢复，执行独立分享任务: {tid}")
                    await self.execute_share(specific_target=target_umo)
            return delayed_recover

        for tid, is_group in all_targets:
            target_umo = self._build_target_umo(tid, is_group, default_adapter_id)
            if self._is_unsupported_weixin_group_target(target_umo, is_group):
                logger.warning(f"[DailySharing] weixin_oc 不支持群聊，已跳过恢复目标: {tid}")
                await self.db.update_state_dict(f"target_{tid}", {"pending_delay_job": None})
                continue
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
            self.plugin._track_task(self._schedule_daily_random_jobs())
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
            self.plugin._track_task(self._schedule_daily_qzone_random_jobs())
            logger.debug(f"[DailySharing] QQ空间已启用多时间段随机生成模式")

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
                    run_time = self._get_random_run_time(now, period_str)
                    if run_time is None:
                        continue
                    
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
            cron_kwargs = self._parse_cron_to_kwargs(actual_cron)
            
            if cron_kwargs:
                self.scheduler.add_job(
                    func, 'cron',
                    **cron_kwargs,
                    id=job_id,
                    replace_existing=True,
                    max_instances=1
                )
                logger.debug(f"[DailySharing] 任务[{job_id}]已设定: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 任务[{job_id}]无效的 Cron 表达式 (支持5/6/7位): {cron_str}")
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
        """实际执行主分享任务"""
        async def before_share():
            now = datetime.now()
            if self.plugin._last_share_time:
                if (now - self.plugin._last_share_time).total_seconds() < 60:
                    logger.debug("[DailySharing] 检测到近期已执行任务，跳过本次触发。")
                    return False
            self.plugin._last_share_time = now
            return True

        async def run_share():
            logger.info("[DailySharing] 开始执行分享任务...")
            await self.execute_share()

        await self._run_tracked_pending_job(
            "global",
            run_share,
            lock=self._lock,
            locked_warning="[DailySharing] 上一个任务正在进行中，本次触发将排队等待...",
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
        """实际执行早报分享任务"""
        async def run_briefing_share():
            await self.execute_briefing_share()

        await self._run_tracked_pending_job("briefing", run_briefing_share)

    async def _task_wrapper_qzone(self):
        """QQ空间任务触发器（处理防抖与随机延迟记录）"""
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
            log_label="QQ空间任务",
        )

    async def _execute_delayed_qzone_task(self):
        """实际执行QQ空间分享任务"""
        async def run_qzone_share():
            logger.info("[DailySharing] 开始执行QQ空间分享任务...")
            await self.execute_qzone_share()

        await self._run_tracked_pending_job("qzone", run_qzone_share, lock=self._lock)
