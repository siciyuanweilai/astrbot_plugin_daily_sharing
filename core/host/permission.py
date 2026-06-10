from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class PluginPermissionMixin:
    def _remember_event_adapter(self, event: AstrMessageEvent):
        """记录最近见过的平台标识，供纯标识配置选择 QQ 或微信适配器。"""
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if not origin:
                return

            adapter_id = origin.split(":", 1)[0].strip()
            if adapter_id:
                self._cached_adapter_id = adapter_id
                if (
                    self.ctx_service._is_weixin_oc_event(event)
                    and self.ctx_service._is_weixin_platform(origin)
                ):
                    self._cached_weixin_adapter_id = adapter_id
                else:
                    try:
                        sender_id = str(event.get_sender_id() or "").strip()
                    except Exception:
                        sender_id = ""
                    if sender_id.isdigit():
                        self._cached_qq_adapter_id = adapter_id
        except Exception as e:
            logger.debug(f"[每日分享] 记录事件平台失败: {e}")

    def _is_admin_event(self, event: AstrMessageEvent) -> bool:
        """尽量兼容 AstrBot 管理员配置，供插件内部权限判断使用。"""
        try:
            candidates = set()
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if origin:
                candidates.add(origin)
                _, real_id = self.ctx_service._parse_umo(origin)
                if real_id:
                    candidates.add(str(real_id))

            try:
                sender_id = str(event.get_sender_id() or "").strip()
                if sender_id:
                    candidates.add(sender_id)
            except Exception as e:
                logger.debug(f"[每日分享] 管理员检查读取发送者标识失败: {e}")

            cfg = self.context.get_config() or {}
            admins = cfg.get("admins_id", []) or cfg.get("admins", []) or []
            return any(str(admin).strip() in candidates for admin in admins)
        except Exception as e:
            logger.debug(f"[每日分享] 管理员检查失败: {e}")
            return False

    def _target_entry_matches(self, entry, origin: str, real_id: str, extra_candidates=None) -> bool:
        s = str(entry).strip().replace("：", ":")
        if not s:
            return False

        candidates = {str(c).strip() for c in [origin, real_id] + list(extra_candidates or []) if str(c or "").strip()}
        if s in candidates:
            return True

        parsed = self.task_manager._parse_targets_config([s])
        for target_id in parsed.keys():
            if target_id in candidates:
                return True
            _, target_real_id = self.ctx_service._parse_umo(target_id)
            if target_real_id and target_real_id in candidates:
                return True
        return False

    def _is_configured_receiver_event(self, event: AstrMessageEvent) -> bool:
        """当前会话在接收对象配置中时，允许使用手动分享类命令。"""
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if not origin:
                return False

            is_group = self.ctx_service._is_group_chat(origin)
            if (
                self.ctx_service._is_weixin_oc_event(event)
                and self.ctx_service._is_weixin_platform(origin)
            ):
                is_group = False
            _, real_id = self.ctx_service._parse_umo(origin)
            try:
                sender_id = str(event.get_sender_id() or "").strip()
            except Exception:
                sender_id = ""
            receiver_map = self.task_manager._parse_targets_config(
                self.receiver_conf.get("groups" if is_group else "users", [])
            )
            if (
                origin in receiver_map
                or (real_id and real_id in receiver_map)
                or (sender_id and sender_id in receiver_map)
            ):
                return True
            for entry in receiver_map.keys():
                if self._target_entry_matches(entry, origin, real_id, [sender_id]):
                    return True

            extra_key = "briefing_groups" if is_group else "briefing_users"
            for entry in self.extra_shares_conf.get(extra_key, []):
                if self._target_entry_matches(entry, origin, real_id, [sender_id]):
                    return True

            return False
        except Exception as e:
            logger.warning(f"[每日分享] 接收对象权限判断失败: {e}")
            return False

    def _plain_permission_denied(self, event: AstrMessageEvent, reason: str = ""):
        suffix = f"\n{reason}" if reason else ""
        return event.plain_result(
            "权限不足：当前会话不在接收对象配置中。"
            "请先把当前会话加入群聊、私聊或早报接收目标。"
            f"{suffix}"
        )

    def _has_reply_component(self, event: AstrMessageEvent) -> bool:
        try:
            messages = event.get_messages()
        except Exception:
            messages = getattr(getattr(event, "message_obj", None), "message", []) or []
        for comp in messages or []:
            if comp.__class__.__name__ == "Reply":
                return True
            if str(getattr(comp, "type", "")).lower().endswith("reply"):
                return True
        return False
