from astrbot.api.event import AstrMessageEvent

from ..constants import TYPE_CN_MAP


class CommandBasicMixin:
    async def cmd_enable(self, event: AstrMessageEvent):
        """启用插件"""
        self.config["enable_auto_sharing"] = True
        await self.plugin._save_config_and_refresh_runtime()
        yield event.plain_result("自动分享已启用")

    async def cmd_disable(self, event: AstrMessageEvent):
        """禁用插件"""
        self.config["enable_auto_sharing"] = False
        await self.plugin._save_config_and_refresh_runtime(clear_pending_when_disabled=True)
        yield event.plain_result("自动分享已禁用")

    async def cmd_status(self, event: AstrMessageEvent):
        """查看详细状态"""
        target_uid = event.unified_msg_origin
        state_key = f"target_{target_uid}"
        state = await self.db.get_state(state_key, {})
        
        enabled = self.config.get("enable_auto_sharing", False)
        cron = self.basic_conf.get("sharing_cron")
        
        last_type_raw = state.get('last_type', '无')
        last_type_cn = TYPE_CN_MAP.get(last_type_raw, last_type_raw)
        
        period = self.plugin.task_manager.get_curr_period()
        time_range = self.plugin.task_manager.get_period_range_str(period)

        recent_history = await self.db.get_recent_history_by_target(target_uid, limit=5)
        hist_txt = "无记录"
        if recent_history:
            lines = []
            for h in recent_history:
                ts = str(h.get("timestamp", ""))
                content_preview = h.get('content', '') or ""
                t_raw = h.get('type')
                t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                lines.append(f"• {ts} [{t_cn}] {content_preview}")
            hist_txt = "\n".join(lines)
            
        # 解析独立配置，识别出当前会话是否脱离了全局控制
        adapter_id, real_id = self.plugin.ctx_service._parse_umo(target_uid)
        is_group = self.plugin.ctx_service._is_group_chat(target_uid)
        
        r_groups = self.plugin.task_manager._parse_targets_config(self.plugin.receiver_conf.get("groups", []))
        r_users = self.plugin.task_manager._parse_targets_config(self.plugin.receiver_conf.get("users", []))
        
        custom_cron = "无"
        target_specific_type = "auto"
        conf = self.plugin.task_manager._get_target_conf(target_uid, is_group, r_groups, r_users)
        if isinstance(conf, dict):
            custom_cron = conf.get("cron") or "无"
            target_specific_type = conf.get("seq", "auto")

        # 判定指针读取位置
        is_custom_seq = target_specific_type != "auto" and target_specific_type != "auto"
        idx_display = state.get('custom_sequence_index', 0) if is_custom_seq else state.get('sequence_index', 0)

        msg = f"""每日分享状态
================
运行状态: {'启用' if enabled else '禁用'}
全局触发: {self.basic_conf.get("trigger_mode", "cron")} ({cron})

【当前会话独立配置】
独立定时: {custom_cron}
分享类型: {target_specific_type}

【当前会话分享状态】
当前时段: {period.value} ({time_range})
上次类型: {last_type_cn}
上次时间: {state.get('last_timestamp', '无')[5:16].replace('T', ' ')}
当前指针: {idx_display}

【最近记录】
{hist_txt}
"""
        yield event.plain_result(msg)

    async def cmd_briefing_qzone_sync(self, event: AstrMessageEvent, parts: list):
        """开启/关闭分享早报到 QQ 空间。"""
        if len(parts) > 2 and parts[2] in ["开启", "关闭"]:
            enable = (parts[2] == "开启")
            self.extra_shares_conf["sync_briefing_to_qzone"] = enable
            self.config["extra_shares"] = self.extra_shares_conf
            await self.plugin._save_config_file()
            yield event.plain_result(f"✅ 定时早报自动同步 QQ 空间功能已【{parts[2]}】。")
        else:
            status = "开启" if self.extra_shares_conf.get("sync_briefing_to_qzone", False) else "关闭"
            yield event.plain_result(f"ℹ️ 当前分享早报到 QQ 空间状态为: 【{status}】\n提示：发送 /分享 早报空间 开启/关闭 来切换。")

    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result("""每日分享插件帮助:
/分享 [类型] - 立即在当前会话生成分享 (默认文字模式)
支持类型: 问候、新闻、心情、知识、推荐、60s、ai

【可用后缀】
 1. 广播：/分享 [类型] 广播 - 向所有配置的群聊、私聊发送
 2. 空间：/分享 [类型] 空间 - 单独生成文案并分享到 QQ 空间
 3. 图片：/分享 新闻 [源] 图片 - 直接分享热搜图片
 
【配置指令】
/分享 添加当前 [类型序列] - 将当前会话加入接收对象，例如 /分享 添加当前 mood,news
/分享 添加当前 早报 - 将当前会话加入定时早报接收对象
/分享 昵称 [名称] - 为当前会话设置本地昵称映射
/分享 开启/关闭 - 启停自动分享
/分享 早报空间 开启/关闭 - 启停自动分享早报到 QQ 空间
/分享 状态 - 查看本会话的运行状态
/分享 查看序列 - 查看本会话当前时段序列及指针
/分享 指定序列 [序号] - 调整本会话分享内容指针位置 (支持加后缀 空间)
/分享 重置序列 - 重置本会话分享内容序列到开头""")
