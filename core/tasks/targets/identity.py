from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class TaskTargetIdentityMixin:
    def _get_contact_alias(self, target_uid: str, event: AstrMessageEvent = None) -> str:
        if hasattr(self.plugin, "get_contact_alias"):
            return self.plugin.get_contact_alias(target_uid, event=event)
        return ""

    def _clean_nickname_candidate(self, nickname: str, target_uid: str, event: AstrMessageEvent = None) -> str:
        name = str(nickname or "").strip()
        if not name:
            return ""
        keys = set()
        target_s = str(target_uid or "").strip()
        if target_s:
            keys.add(target_s)
            _, real_id = self.ctx_service._parse_umo(target_s)
            if real_id:
                keys.add(real_id)
        if event:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if origin:
                keys.add(origin)
                _, real_id = self.ctx_service._parse_umo(origin)
                if real_id:
                    keys.add(real_id)
            try:
                sender_id = str(event.get_sender_id() or "").strip()
                if sender_id:
                    keys.add(sender_id)
            except Exception as e:
                logger.debug(f"[每日分享] 清理昵称候选时读取发送者标识失败: {e}")
        if name in keys or name.endswith("@im.wechat"):
            return ""
        return name

    async def _get_target_display_name(
        self,
        target_uid: str,
        event: AstrMessageEvent = None,
        is_group: bool = None,
    ) -> str:
        """获取任务进度里展示用的目标名称。"""
        target_s = str(target_uid or "").strip()
        label = self._get_contact_alias(target_s, event=event)
        if label:
            return label

        if is_group is None:
            is_group = self.ctx_service._is_group_chat(target_s)

        if is_group:
            return await self._get_onebot_group_name(target_s, event=event)

        label = await self._get_onebot_nickname(target_s, event=event)
        if label:
            return label

        if event:
            try:
                return self._clean_nickname_candidate(event.get_sender_name(), target_s, event=event)
            except Exception as e:
                logger.debug(f"[每日分享] 获取事件发送者昵称失败: {e}")
        return ""

    async def _get_onebot_nickname(self, target_uid: str, event: AstrMessageEvent = None) -> str:
        target_s = str(target_uid or "").strip()
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        probe_id = real_id or target_s
        if not str(probe_id).isdigit():
            return ""

        get_bot = getattr(self.ctx_service, "_get_onebot_bot", None)
        call_action = getattr(self.ctx_service, "_bot_call_action", None)
        if not callable(get_bot) or not callable(call_action):
            if event and self.ctx_service._is_onebot_event(event):
                return self._clean_nickname_candidate(event.get_sender_name(), target_s, event=event)
            return ""

        bot = get_bot(target_s, event=event, adapter_id=adapter_id)
        if not bot:
            if event and self.ctx_service._is_onebot_event(event):
                return self._clean_nickname_candidate(event.get_sender_name(), target_s, event=event)
            return ""

        try:
            ret = await call_action(bot, "get_stranger_info", user_id=int(probe_id))
            if isinstance(ret, dict) and isinstance(ret.get("data"), dict):
                ret = ret["data"]
            if ret and isinstance(ret, dict):
                remark = str(ret.get("remark", "") or "").strip()
                if remark:
                    logger.info(f"[每日分享] 获取到用户备注: {remark}")
                    return remark
                nickname = str(ret.get("nickname", "") or "").strip()
                if nickname:
                    logger.info(f"[每日分享] 获取到用户昵称: {nickname}")
                    return nickname
        except Exception as e:
            logger.warning(f"[每日分享] 获取 QQ 昵称失败: {e}")

        if event and self.ctx_service._is_onebot_event(event):
            return self._clean_nickname_candidate(event.get_sender_name(), target_s, event=event)
        return ""

    async def _get_onebot_group_name(self, target_uid: str, event: AstrMessageEvent = None) -> str:
        target_s = str(target_uid or "").strip()
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        probe_id = real_id or target_s
        if not str(probe_id).isdigit():
            return ""

        get_bot = getattr(self.ctx_service, "_get_onebot_bot", None)
        call_action = getattr(self.ctx_service, "_bot_call_action", None)
        if not callable(get_bot) or not callable(call_action):
            return ""

        bot = get_bot(target_s, event=event, adapter_id=adapter_id)
        if not bot:
            return ""

        try:
            ret = await call_action(bot, "get_group_info", group_id=int(probe_id))
            if isinstance(ret, dict) and isinstance(ret.get("data"), dict):
                ret = ret["data"]
            if isinstance(ret, dict):
                group_name = str(
                    ret.get("group_name")
                    or ret.get("group_remark")
                    or ret.get("name")
                    or ""
                ).strip()
                if group_name:
                    logger.info(f"[每日分享] 获取到群名称: {group_name}")
                    return group_name
        except Exception as e:
            logger.warning(f"[每日分享] 获取 QQ 群名称失败: {e}")
        return ""
