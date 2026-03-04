import asyncio
from datetime import datetime
from astrbot.api.event import AstrMessageEvent
from ..config import SharingType, TimePeriod, SHARING_TYPE_SEQUENCES
from .constants import TYPE_CN_MAP

class CommandHandler:
    def __init__(self, plugin):
        self.plugin = plugin
        self.db = plugin.db
        self.config = plugin.config
        self.basic_conf = plugin.basic_conf
        self.extra_shares_conf = plugin.extra_shares_conf
        self.qzone_conf = plugin.qzone_conf

    async def cmd_enable(self, event: AstrMessageEvent):
        """启用插件"""
        self.config["enable_auto_sharing"] = True
        await self.plugin._save_config_file()
        cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
        self.plugin.task_manager.setup_cron(cron)
        if not self.plugin.scheduler.running: 
            self.plugin.scheduler.start()
        yield event.plain_result("自动分享已启用")

    async def cmd_disable(self, event: AstrMessageEvent):
        """禁用插件"""
        self.config["enable_auto_sharing"] = False
        await self.plugin._save_config_file()
        self.plugin.scheduler.remove_all_jobs()
        yield event.plain_result("自动分享已禁用")

    async def cmd_status(self, event: AstrMessageEvent):
        """查看详细状态"""
        state = await self.db.get_state("global", {})
        enabled = self.config.get("enable_auto_sharing", True)
        cron = self.basic_conf.get("sharing_cron")
        
        last_type_raw = state.get('last_type', '无')
        last_type_cn = TYPE_CN_MAP.get(last_type_raw, last_type_raw)
        
        period = self.plugin.task_manager.get_curr_period()
        time_range = self.plugin.task_manager.get_period_range_str(period)

        recent_history = await self.db.get_recent_history(5)
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

        msg = f"""每日分享状态
================
运行状态: {'启用' if enabled else '禁用'}
Cron规则: {cron}
当前时段: {period.value} ({time_range})

【序列状态】
上次类型: {last_type_cn}
上次时间: {state.get('last_timestamp', '无')[5:16].replace('T', ' ')}
序列索引: {state.get('sequence_index', 0)}

【最近记录】
{hist_txt}
"""
        yield event.plain_result(msg)

    async def cmd_reset_seq(self, event: AstrMessageEvent):
        """重置序列"""
        # 重置群聊/私聊的指针
        updates = {"sequence_index": 0, "last_period": None}
        for p in TimePeriod: 
            updates[f"index_{p.value}"] = 0
        await self.db.update_state_dict("global", updates)
        
        # 重置QQ空间的指针
        qzone_updates = {"sequence_index": 0, "last_period": None}
        for p in TimePeriod: 
            qzone_updates[f"index_{p.value}"] = 0
        await self.db.update_state_dict("qzone", qzone_updates)
        
        yield event.plain_result("群聊与空间的序列指针均已重置")

    async def cmd_view_seq(self, event: AstrMessageEvent):
        """查看序列详情"""
        period = self.plugin.task_manager.get_curr_period()
        time_range = self.plugin.task_manager.get_period_range_str(period)
        
        config_key_map = {
            TimePeriod.MORNING: "morning_sequence", 
            TimePeriod.FORENOON: "forenoon_sequence",
            TimePeriod.AFTERNOON: "afternoon_sequence", 
            TimePeriod.EVENING: "evening_sequence",
            TimePeriod.NIGHT: "night_sequence", 
            TimePeriod.LATE_NIGHT: "late_night_sequence",
            TimePeriod.DAWN: "dawn_sequence"
        }
        config_key = config_key_map.get(period)
        seq = self.basic_conf.get(config_key, [])
        if not seq: 
            seq = SHARING_TYPE_SEQUENCES.get(period, [])

        state = await self.db.get_state("global", {})
        
        # 读取当前时段的独立索引
        idx_key = f"index_{period.value}"
        idx = state.get(idx_key, 0)
        
        txt = f"当前时段: {period.value} ({time_range})\n"
        for i, t_raw in enumerate(seq):
            mark = "👉 " if i == idx else "   "
            t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
            txt += f"{mark}{i}. {t_cn}\n"
        yield event.plain_result(txt)

    async def cmd_set_seq(self, event, parts):
        """指定序列子命令"""
        if len(parts) > 2 and parts[2].isdigit():
            target_idx = int(parts[2])
            period = self.plugin.task_manager.get_curr_period()
            
            # 判断是否是在调QQ空间的序列
            is_qzone = "空间" in parts
            conf_node = self.qzone_conf if is_qzone else self.basic_conf
            state_key = "qzone" if is_qzone else "global"
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
            config_key = config_key_map.get(period)
            seq = conf_node.get(config_key, [])
            if not seq: 
                seq = SHARING_TYPE_SEQUENCES.get(period, [])

            if 0 <= target_idx < len(seq):
                # 更新当前时段的独立索引
                idx_key = f"index_{period.value}"
                await self.db.update_state_dict(state_key, {
                    idx_key: target_idx, 
                    "sequence_index": target_idx, 
                    "last_period": period.value 
                })
                t_raw = seq[target_idx]
                t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                target_desc = "QQ空间" if is_qzone else "日常分享"
                yield event.plain_result(f"已切换[{target_desc}]下一次自动分享：{target_idx}. {t_cn}")
            else:
                yield event.plain_result(f"序号无效，当前时段[{period.value}] 范围: 0 ~ {len(seq)-1}")
        else:
            yield event.plain_result("格式错误。例如：/分享 指定序列 1\n可加后缀：空间")

    async def cmd_briefing_qzone_sync(self, event: AstrMessageEvent, parts: list):
        """开启/关闭 分享早报到QQ空间"""
        if len(parts) > 2 and parts[2] in ["开启", "关闭"]:
            enable = (parts[2] == "开启")
            self.extra_shares_conf["sync_briefing_to_qzone"] = enable
            self.config["extra_shares"] = self.extra_shares_conf
            await self.plugin._save_config_file()
            yield event.plain_result(f"✅ 定时早报自动同步QQ空间功能已【{parts[2]}】。")
        else:
            status = "开启" if self.extra_shares_conf.get("sync_briefing_to_qzone", False) else "关闭"
            yield event.plain_result(f"ℹ️ 当前分享早报到QQ空间状态为: 【{status}】\n提示：发送 /分享 早报空间 开启/关闭 来切换。")

    async def cmd_help(self, event: AstrMessageEvent):
        """帮助菜单"""
        yield event.plain_result("""每日分享插件帮助:
/分享 [类型] - 立即在当前会话生成分享 (默认文字模式)
支持类型: 问候、新闻、心情、知识、推荐、60s、ai

【可用后缀】
 1. 广播：/分享 [类型] 广播 - 向所有配置的群聊、私聊发送
 2. 空间：/分享 [类型] 空间 - 单独生成文案并分享到QQ空间
 3. 图片：/分享 新闻 [源] 图片 -直接分享热搜图片
 
【配置指令】
/分享 开启/关闭 - 启停自动分享
/分享 早报空间 开启/关闭 - 启停自动分享早报到QQ空间
/分享 状态 - 查看运行状态
/分享 查看序列 - 查看当前时段序列及指针
/分享 指定序列 [序号] - 调整分享内容指针位置 (支持加后缀 空间)
/分享 重置序列 - 重置当前分享内容序列到开头""")
