import random
from datetime import datetime, timedelta

from astrbot.api import logger


class TaskSchedulerRecoveryMixin:
    """延迟任务清理和重启恢复。"""

    async def clear_pending_delay_jobs(self):
        """清理已记录但尚未完成的延迟任务，确保关闭后不会补发旧任务。"""
        await self.db.update_state_dict("global", {"pending_delay_job": None})
        await self.db.update_state_dict("qzone", {"pending_delay_job": None})
        await self.db.update_state_dict("briefing", {"pending_delay_job": None})

        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))
        for target_id in list(r_groups.keys()) + list(r_users.keys()):
            if target_id:
                await self.db.update_state_dict(f"target_{target_id}", {"pending_delay_job": None})

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
                logger.debug(f"[每日分享] 已恢复未完成的延迟分享任务，将在 {run_time.strftime('%H:%M:%S')} 分享")
            elif 0 <= now_ts - target_ts < 3600:  
                run_time = now + timedelta(seconds=5)
                self.scheduler.add_job(
                    self._execute_delayed_task, 'date', run_date=run_time, id="resume_auto_share", replace_existing=True
                )
                logger.debug("[每日分享] 检测到近期错过的延迟分享任务，即将补偿分享")
            else:
                await self.db.update_state_dict("global", {"pending_delay_job": None})

        # QQ 空间任务恢复。
        qzone_state = await self.db.get_state("qzone", {})
        q_pending = qzone_state.get("pending_delay_job")
        if q_pending:
            target_ts = q_pending.get("target_time", 0)
            if target_ts > now_ts:
                run_time = datetime.fromtimestamp(target_ts)
                self.scheduler.add_job(
                    self._execute_delayed_qzone_task, 'date', run_date=run_time, id="resume_qzone_share", replace_existing=True
                )
                logger.debug(f"[每日分享] 已恢复未完成的 QQ 空间延迟分享任务，将在 {run_time.strftime('%H:%M:%S')} 分享")
            elif 0 <= now_ts - target_ts < 3600:
                run_time = now + timedelta(seconds=10)
                self.scheduler.add_job(
                    self._execute_delayed_qzone_task, 'date', run_date=run_time, id="resume_qzone_share", replace_existing=True
                )
                logger.debug("[每日分享] 检测到近期错过的 QQ 空间延迟任务，即将补偿分享")
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
                logger.debug(f"[每日分享] 已恢复未完成的早报延迟分享任务，将在 {run_time.strftime('%H:%M:%S')} 分享")
            elif 0 <= now_ts - target_ts < 3600:
                run_time = now + timedelta(seconds=10)
                self.scheduler.add_job(
                    self._execute_delayed_briefing_task, 'date',
                    run_date=run_time,
                    id="resume_briefing_share",
                    replace_existing=True
                )
                logger.debug("[每日分享] 检测到近期错过的早报延迟任务，即将补偿分享")
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
                    logger.debug(f"[每日分享] 补偿恢复，开始独立分享任务: {tid}")
                    await self.execute_share(specific_target=target_umo)
            return delayed_recover

        for tid, is_group in all_targets:
            target_umo = self._build_target_umo(tid, is_group, default_adapter_id)
            if self._is_unsupported_weixin_group_target(target_umo, is_group):
                logger.warning(f"[每日分享] 个人微信平台(weixin_oc)不支持群聊，已跳过恢复目标: {tid}")
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

