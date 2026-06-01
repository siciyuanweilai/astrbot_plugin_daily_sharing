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

    def _get_sendable_current_target(self, event: AstrMessageEvent, target_uid: str) -> str:
        """配置里优先保存纯会话 ID：QQ 保存 QQ 号，weixin_oc 保存 openid。"""
        _, real_id = self.plugin.ctx_service._parse_umo(target_uid)
        if real_id:
            return real_id

        try:
            sender_id = str(event.get_sender_id() or "").strip()
        except Exception:
            sender_id = ""

        return sender_id or target_uid

    def _find_matching_target_index(self, target_list: list, origin: str, real_id: str, candidates: list) -> int:
        for idx, item in enumerate(target_list):
            if self.plugin._target_entry_matches(item, origin, real_id, candidates):
                return idx
        return -1

    async def cmd_enable(self, event: AstrMessageEvent):
        """启用插件"""
        self.config["enable_auto_sharing"] = True
        await self.plugin._save_config_file()
        self.plugin.scheduler.remove_all_jobs()
        self.plugin.task_manager.setup_tasks()
        if self.plugin.scheduler.get_jobs() and not self.plugin.scheduler.running: 
            self.plugin.scheduler.start()
        yield event.plain_result("自动分享已启用")

    async def cmd_disable(self, event: AstrMessageEvent):
        """禁用插件"""
        self.config["enable_auto_sharing"] = False
        await self.plugin._save_config_file()
        self.plugin.scheduler.remove_all_jobs()
        await self.plugin.task_manager.clear_pending_delay_jobs()
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

【当前会话执行状态】
当前时段: {period.value} ({time_range})
上次类型: {last_type_cn}
上次时间: {state.get('last_timestamp', '无')[5:16].replace('T', ' ')}
当前指针: {idx_display}

【最近记录】
{hist_txt}
"""
        yield event.plain_result(msg)

    async def cmd_reset_seq(self, event: AstrMessageEvent):
        """重置序列 (支持分离当前会话和QQ空间)"""
        is_qzone = "空间" in event.message_str
        
        if is_qzone:
            # 仅重置QQ空间的指针
            qzone_updates = {"sequence_index": 0, "custom_sequence_index": 0, "last_period": None}
            for p in TimePeriod: 
                qzone_updates[f"index_{p.value}"] = 0
            await self.db.update_state_dict("qzone", qzone_updates)
            yield event.plain_result("QQ空间的序列指针已重置")
            
        else:
            # 仅重置当前会话的指针
            target_uid = event.unified_msg_origin
            state_key = f"target_{target_uid}"
            
            updates = {"sequence_index": 0, "custom_sequence_index": 0, "last_period": None}
            for p in TimePeriod: 
                updates[f"index_{p.value}"] = 0
            await self.db.update_state_dict(state_key, updates)
            yield event.plain_result("当前会话的序列指针已重置")
            
    async def cmd_view_seq(self, event: AstrMessageEvent):
        """查看序列详情"""
        target_uid = event.unified_msg_origin
        is_qzone = "空间" in event.message_str

        # 获取当前时段信息
        period = self.plugin.task_manager.get_curr_period()
        time_range = self.plugin.task_manager.get_period_range_str(period)

        # 尝试获取该会话的独立配置类型
        adapter_id, real_id = self.plugin.ctx_service._parse_umo(target_uid)
        is_group = self.plugin.ctx_service._is_group_chat(target_uid)
        
        r_groups = self.plugin.task_manager._parse_targets_config(self.plugin.receiver_conf.get("groups", []))
        r_users = self.plugin.task_manager._parse_targets_config(self.plugin.receiver_conf.get("users", []))
        
        target_specific_type = "auto"
        if not is_qzone:
            # QQ空间走独立配置，普通会话看群聊、私聊独立配置
            conf = self.plugin.task_manager._get_target_conf(target_uid, is_group, r_groups, r_users)
            if conf is not None:
                target_specific_type = conf.get("seq", "auto") if isinstance(conf, dict) else conf
        else:
            target_specific_type = self.qzone_conf.get("qzone_sharing_type", "auto")

        state_key = "qzone" if is_qzone else f"target_{target_uid}"
        state = await self.db.get_state(state_key, {})

        # 优先判断是否使用了独立时段序列
        if target_specific_type and target_specific_type.lower() != "auto":
            seq_str = target_specific_type.replace("，", ",")
            custom_seq = [s.strip().lower() for s in seq_str.split(",") if s.strip()]
            
            if custom_seq and custom_seq != ["auto"]:
                idx = state.get("custom_sequence_index", 0)
                if idx >= len(custom_seq): idx = 0
                
                target_desc = "QQ空间" if is_qzone else "当前会话"
                
                # 拼接时段信息！
                txt = f"当前时段: {period.value} ({time_range})\n"
                txt += f"{target_desc}: 独立时段序列\n"
                for i, t_raw in enumerate(custom_seq):
                    mark = "👉 " if i == idx else "   "
                    t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                    txt += f"{mark}{i}. {t_cn}\n"
                yield event.plain_result(txt)
                return

        # 如果没用独立时段序列（即 auto 模式），则走全局时段序列逻辑
        prefix = "qzone_" if is_qzone else ""
        conf_node = self.qzone_conf if is_qzone else self.basic_conf
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

        idx_key = f"index_{period.value}"
        idx = state.get(idx_key, 0)
        if idx >= len(seq): idx = 0
        
        txt = f"当前时段: {period.value} ({time_range})\n"
        txt += f"当前会话: 全局时段序列\n"
        for i, t_raw in enumerate(seq):
            mark = "👉 " if i == idx else "   "
            t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
            txt += f"{mark}{i}. {t_cn}\n"
        yield event.plain_result(txt)

    async def cmd_set_seq(self, event, parts):
        """指定序列子命令"""
        if len(parts) > 2 and parts[2].isdigit():
            target_idx = int(parts[2])
            is_qzone = "空间" in parts
            target_uid = event.unified_msg_origin
            
            # 检测独立配置
            adapter_id, real_id = self.plugin.ctx_service._parse_umo(target_uid)
            is_group = self.plugin.ctx_service._is_group_chat(target_uid)
            
            r_groups = self.plugin.task_manager._parse_targets_config(self.plugin.receiver_conf.get("groups", []))
            r_users = self.plugin.task_manager._parse_targets_config(self.plugin.receiver_conf.get("users", []))
            
            target_specific_type = "auto"
            if not is_qzone:
                conf = self.plugin.task_manager._get_target_conf(target_uid, is_group, r_groups, r_users)
                if conf is not None:
                    target_specific_type = conf.get("seq", "auto") if isinstance(conf, dict) else conf
            else:
                target_specific_type = self.qzone_conf.get("qzone_sharing_type", "auto")
            
            state_key = "qzone" if is_qzone else f"target_{target_uid}"
            
            # 如果有独立序列，调整独立序列的指针
            if target_specific_type and target_specific_type.lower() != "auto":
                seq_str = target_specific_type.replace("，", ",")
                custom_seq = [s.strip().lower() for s in seq_str.split(",") if s.strip()]
                if custom_seq and custom_seq != ["auto"]:
                    if 0 <= target_idx < len(custom_seq):
                        await self.db.update_state_dict(state_key, {
                            "custom_sequence_index": target_idx,
                            "sequence_index": target_idx
                        })
                        t_raw = custom_seq[target_idx]
                        t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                        target_desc = "QQ空间" if is_qzone else "当前独立序列"
                        yield event.plain_result(f"已切换[{target_desc}]下一次自动分享：{target_idx}. {t_cn}")
                    else:
                        yield event.plain_result(f"序号无效，独立序列范围: 0 ~ {len(custom_seq)-1}")
                    return

            # 全局模式，调整时段指针
            period = self.plugin.task_manager.get_curr_period()
            conf_node = self.qzone_conf if is_qzone else self.basic_conf
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
                idx_key = f"index_{period.value}"
                await self.db.update_state_dict(state_key, {
                    idx_key: target_idx, 
                    "sequence_index": target_idx, 
                    "last_period": period.value 
                })
                t_raw = seq[target_idx]
                t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                target_desc = "QQ空间" if is_qzone else "当前时段"
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

    async def cmd_contact_alias(self, event: AstrMessageEvent, parts: list):
        """设置当前会话的本地昵称映射。"""
        target_uid = str(event.unified_msg_origin or "").strip()
        sendable_target_uid = self._get_sendable_current_target(event, target_uid)

        if len(parts) <= 2 or parts[2] in {"查看", "show", "list"}:
            alias = self.plugin.get_contact_alias(target_uid, event=event)
            if alias:
                yield event.plain_result(f"当前会话昵称映射：{sendable_target_uid} -> {alias}")
            else:
                yield event.plain_result(f"当前会话暂未设置昵称映射。\n设置示例：/分享 昵称 李知恬")
            return

        if parts[2] in {"删除", "清除", "移除", "delete", "remove"}:
            removed = self.plugin.remove_contact_alias(target_uid, event=event)
            await self.plugin._save_config_file()
            if removed:
                yield event.plain_result("已删除当前会话昵称映射。")
            else:
                yield event.plain_result("当前会话没有可删除的昵称映射。")
            return

        alias = " ".join(parts[2:]).strip()
        if not alias:
            yield event.plain_result("昵称不能为空。示例：/分享 昵称 李知恬")
            return

        save_key = self.plugin.set_contact_alias(sendable_target_uid, alias, event=event)
        if not save_key:
            yield event.plain_result("设置失败：无法获取当前会话 UID/Session ID。")
            return

        await self.plugin._save_config_file()
        yield event.plain_result(f"已设置当前会话昵称映射：{save_key} -> {alias}")

    async def cmd_add_current(self, event: AstrMessageEvent, parts: list):
        """把当前会话加入接收对象配置。"""
        target_uid = str(event.unified_msg_origin or "").strip()
        if not target_uid:
            yield event.plain_result("添加失败：无法获取当前会话标识。")
            return

        sendable_target_uid = self._get_sendable_current_target(event, target_uid)
        mode = parts[2].strip().lower() if len(parts) > 2 else ""
        is_briefing_mode = mode in {"早报", "briefing", "brief", "60s", "ai"}
        is_group = self.plugin.ctx_service._is_group_chat(target_uid)
        if self.plugin.ctx_service._is_weixin_platform(target_uid):
            is_group = False

        if is_briefing_mode:
            if len(parts) > 3:
                yield event.plain_result("早报接收对象不需要类型序列。示例：/分享 添加当前 早报")
                return

            extra_conf = self.config.setdefault("extra_shares", {})
            groups = extra_conf.setdefault("briefing_groups", [])
            users = extra_conf.setdefault("briefing_users", [])
            target_list = groups if is_group else users

            _, real_id = self.plugin.ctx_service._parse_umo(target_uid)
            _, sendable_real_id = self.plugin.ctx_service._parse_umo(sendable_target_uid)
            existing_idx = self._find_matching_target_index(
                target_list,
                target_uid,
                real_id,
                [sendable_target_uid, sendable_real_id],
            )
            if existing_idx >= 0:
                if str(target_list[existing_idx]).strip().replace("：", ":") != sendable_target_uid:
                    target_list[existing_idx] = sendable_target_uid
                    msg = f"当前会话已在早报接收对象中，已更新为简写 ID：{sendable_target_uid}"
                else:
                    msg = "当前会话已经在早报接收对象配置中。"
            else:
                target_list.append(sendable_target_uid)
                msg = f"已添加当前{'群聊' if is_group else '私聊'}到早报接收对象。"

            self.config["extra_shares"] = extra_conf
            self.plugin.extra_shares_conf = extra_conf
            self.plugin.task_manager.extra_shares_conf = extra_conf
            await self.plugin._save_config_file()
            yield event.plain_result(msg)
            return

        seq = None
        if len(parts) > 2:
            seq_candidate = parts[2].strip().replace("，", ",")
            if not self.plugin.task_manager._looks_like_share_sequence(seq_candidate):
                yield event.plain_result("类型序列格式不正确。示例：/分享 添加当前 mood,news\n添加早报示例：/分享 添加当前 早报")
                return
            seq = seq_candidate

        receiver_conf = self.config.setdefault("receiver", {})
        groups = receiver_conf.setdefault("groups", [])
        users = receiver_conf.setdefault("users", [])
        target_list = groups if is_group else users

        # 配置保存纯会话 ID：QQ 为 QQ 号，weixin_oc 为 openid，实际发送时再按平台拼 UMO。
        new_entry = f"{sendable_target_uid}:{seq}" if seq else sendable_target_uid
        _, real_id = self.plugin.ctx_service._parse_umo(target_uid)
        _, sendable_real_id = self.plugin.ctx_service._parse_umo(sendable_target_uid)
        existing_idx = self._find_matching_target_index(
            target_list,
            target_uid,
            real_id,
            [sendable_target_uid, sendable_real_id],
        )

        if existing_idx >= 0:
            if seq or str(target_list[existing_idx]).strip().replace("：", ":") != new_entry:
                target_list[existing_idx] = new_entry
                msg = "当前会话已在接收对象中，已更新为简写 ID"
                if seq:
                    msg += f"并设置分享类型序列为：{seq}"
                else:
                    msg += f"：{sendable_target_uid}"
            else:
                msg = "当前会话已经在接收对象配置中。"
        else:
            target_list.append(new_entry)
            msg = f"已添加当前{'群聊' if is_group else '私聊'}到接收对象。"
            if seq:
                msg += f"\n分享类型序列：{seq}"

        self.config["receiver"] = receiver_conf
        self.plugin.receiver_conf = receiver_conf
        self.plugin.task_manager.receiver_conf = receiver_conf
        await self.plugin._save_config_file()
        yield event.plain_result(msg)

    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result("""每日分享插件帮助:
/分享 [类型] - 立即在当前会话生成分享 (默认文字模式)
支持类型: 问候、新闻、心情、知识、推荐、60s、ai

【可用后缀】
 1. 广播：/分享 [类型] 广播 - 向所有配置的群聊、私聊发送
 2. 空间：/分享 [类型] 空间 - 单独生成文案并分享到QQ空间
 3. 图片：/分享 新闻 [源] 图片 -直接分享热搜图片
 
【配置指令】
/分享 添加当前 [类型序列] - 将当前会话加入接收对象，例如 /分享 添加当前 mood,news
/分享 添加当前 早报 - 将当前会话加入定时早报接收对象
/分享 昵称 [名称] - 为当前会话设置本地昵称映射
/分享 开启/关闭 - 启停自动分享
/分享 早报空间 开启/关闭 - 启停自动分享早报到QQ空间
/分享 状态 - 查看本会话的运行状态
/分享 查看序列 - 查看本会话当前时段序列及指针
/分享 指定序列 [序号] - 调整本会话分享内容指针位置 (支持加后缀 空间)
/分享 重置序列 - 重置本会话分享内容序列到开头""")
