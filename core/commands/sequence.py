from astrbot.api.event import AstrMessageEvent

from ..config import SHARING_TYPE_SEQUENCES, TimePeriod
from ..constants import TYPE_CN_MAP


class CommandSequenceMixin:
    async def cmd_reset_seq(self, event: AstrMessageEvent):
        """重置序列（支持分离当前会话和 QQ 空间）"""
        is_qzone = "空间" in event.message_str
        
        if is_qzone:
            # 仅重置 QQ 空间的指针。
            qzone_updates = {"sequence_index": 0, "custom_sequence_index": 0, "last_period": None}
            for p in TimePeriod: 
                qzone_updates[f"index_{p.value}"] = 0
            await self.db.update_state_dict("qzone", qzone_updates)
            yield event.plain_result("QQ 空间的序列指针已重置")
            
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
            # QQ 空间走独立配置，普通会话看群聊、私聊独立配置。
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
                
                target_desc = "QQ 空间" if is_qzone else "当前会话"
                
                # 拼接时段信息！
                txt = f"当前时段: {period.value} ({time_range})\n"
                txt += f"{target_desc}: 独立时段序列\n"
                for i, t_raw in enumerate(custom_seq):
                    mark = "👉 " if i == idx else "   "
                    t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                    txt += f"{mark}{i}. {t_cn}\n"
                yield event.plain_result(txt)
                return

        # 如果没用独立时段序列（即自动模式），则走全局时段序列逻辑
        prefix = "qzone_" if is_qzone else ""
        conf_node = self.qzone_conf if is_qzone else self.basic_conf
        config_key = f"{prefix}{period.value}_sequence"
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
                        target_desc = "QQ 空间" if is_qzone else "当前独立序列"
                        yield event.plain_result(f"已切换[{target_desc}]下一次自动分享：{target_idx}. {t_cn}")
                    else:
                        yield event.plain_result(f"序号无效，独立序列范围: 0 ~ {len(custom_seq)-1}")
                    return

            # 全局模式，调整时段指针
            period = self.plugin.task_manager.get_curr_period()
            conf_node = self.qzone_conf if is_qzone else self.basic_conf
            prefix = "qzone_" if is_qzone else ""
            
            config_key = f"{prefix}{period.value}_sequence"
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
                target_desc = "QQ 空间" if is_qzone else "当前时段"
                yield event.plain_result(f"已切换[{target_desc}]下一次自动分享：{target_idx}. {t_cn}")
            else:
                yield event.plain_result(f"序号无效，当前时段[{period.value}] 范围: 0 ~ {len(seq)-1}")
        else:
            yield event.plain_result("格式错误。例如：/分享 指定序列 1\n可加后缀：空间")
