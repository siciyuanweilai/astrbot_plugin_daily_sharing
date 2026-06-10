from astrbot.api.event import AstrMessageEvent


class CommandTargetsMixin:
    def _get_sendable_current_target(self, event: AstrMessageEvent, target_uid: str) -> str:
        """配置里优先保存纯会话标识：QQ 保存 QQ 号，weixin_oc 保存 openid。"""
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
            yield event.plain_result("设置失败：无法获取当前会话标识。")
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
                    msg = f"当前会话已在早报接收对象中，已更新为简写标识：{sendable_target_uid}"
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

        # 配置保存纯会话标识：QQ 为 QQ 号，weixin_oc 为 openid，实际发送时再按平台拼完整会话标识。
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
                msg = "当前会话已在接收对象中，已更新为简写标识"
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
