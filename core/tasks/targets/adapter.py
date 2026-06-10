from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ...platform import (
    find_platform_instance_by_keywords,
    get_platform_id,
    is_weixin_oc_instance,
    iter_platform_instances,
)

try:
    from astrbot.core.platform.message_session import MessageSesion
    from astrbot.core.platform.message_type import MessageType
except Exception:
    MessageSesion = None
    MessageType = None


class TaskTargetPlatformMixin:
    def _find_weixin_oc_platform_instance(self):
        for inst in iter_platform_instances(self.plugin.context):
            if is_weixin_oc_instance(inst):
                return inst
        return None

    def _find_weixin_oc_adapter_id(self) -> str:
        inst = self._find_weixin_oc_platform_instance()
        return get_platform_id(inst) if inst else ""

    def _find_adapter_id_by_keywords(self, keywords):
        """从平台实例信息里尽量按适配器类型找机器人标识。"""
        try:
            inst = find_platform_instance_by_keywords(self.plugin.context, keywords)
            if inst:
                return get_platform_id(inst)
        except Exception as e:
            logger.debug(f"[每日分享] 按类型查找平台标识失败: {e}")
        return None

    def _get_default_adapter_id(self, *, warn_on_fallback: bool = True) -> str:
        default_adapter_id = getattr(self.plugin, "_cached_adapter_id", None)
        if default_adapter_id:
            return default_adapter_id

        for inst in iter_platform_instances(self.plugin.context):
            p_id = get_platform_id(inst)
            if p_id:
                self.plugin._cached_adapter_id = p_id
                logger.debug(f"[每日分享] 自动发现并缓存机器人标识: {p_id}")
                return p_id

        if warn_on_fallback:
            logger.warning("[每日分享] 尚未缓存适配器标识，使用默认值 'aiocqhttp'。")
        return "aiocqhttp"

    def _select_adapter_id_for_target(self, target_id: str, is_group: bool, default_adapter_id: str) -> str:
        """纯标识配置时，按标识形态选择更合理的平台，避免 QQ/微信串台。"""
        target_s = str(target_id or "")
        if self.ctx_service._is_weixin_platform(target_s):
            return (
                getattr(self.plugin, "_cached_weixin_adapter_id", None)
                or self._find_weixin_oc_adapter_id()
                or default_adapter_id
            )

        if target_s.isdigit():
            return (
                getattr(self.plugin, "_cached_qq_adapter_id", None)
                or self._find_adapter_id_by_keywords(["aiocqhttp", "onebot", "qq"])
                or default_adapter_id
            )

        return default_adapter_id

    def _build_target_umo(self, target_id: str, is_group: bool, default_adapter_id: str) -> str:
        """将 /sid 获取的纯会话标识按平台和聊天类型拼成运行时 UMO。"""
        adapter_id = self._select_adapter_id_for_target(target_id, is_group, default_adapter_id)
        return f"{adapter_id}:{'GroupMessage' if is_group else 'FriendMessage'}:{target_id}"

    def _select_platform_instance_for_target(self, target_umo: str):
        """按目标标识形态选择平台实例，避开不同适配器共用同一个平台标识的歧义。"""
        target_s = str(target_umo or "")
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        probe = real_id or target_s

        if adapter_id:
            get_platform_inst = getattr(self.plugin.context, "get_platform_inst", None)
            if callable(get_platform_inst):
                inst = get_platform_inst(adapter_id)
                if inst:
                    return inst
            for inst in iter_platform_instances(self.plugin.context):
                if get_platform_id(inst) == adapter_id:
                    return inst

        if self.ctx_service._is_weixin_platform(target_s):
            return self._find_weixin_oc_platform_instance()

        if probe.isdigit():
            return find_platform_instance_by_keywords(self.plugin.context, ["aiocqhttp", "onebot"])

        return None

    def _build_message_session_for_target(self, target_umo: str, platform_inst=None):
        if not MessageSesion or not MessageType:
            return None

        target_s = str(target_umo or "").strip()
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        session_id = real_id or target_s
        platform_id = get_platform_id(platform_inst) if platform_inst else adapter_id
        if not platform_id or not session_id:
            return None

        is_group = self.ctx_service._is_group_chat(target_s)
        try:
            message_type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        except AttributeError:
            message_type = MessageType("GroupMessage" if is_group else "FriendMessage")
        return MessageSesion(
            platform_name=platform_id,
            message_type=message_type,
            session_id=session_id,
        )

    def _has_weixin_context_token(self, target_umo: str, platform_inst=None) -> bool:
        if not self.ctx_service._is_weixin_platform(target_umo):
            return True
        _, real_id = self.ctx_service._parse_umo(str(target_umo or ""))
        user_id = real_id or str(target_umo or "").strip()
        inst = platform_inst or self._select_platform_instance_for_target(target_umo)
        tokens = getattr(inst, "_context_tokens", {}) if inst else {}
        return bool(user_id and isinstance(tokens, dict) and tokens.get(user_id))

    def _event_matches_target(self, event: AstrMessageEvent, target_umo: str) -> bool:
        if not event:
            return False
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if origin == target_umo:
            return True
        _, origin_real_id = self.ctx_service._parse_umo(origin)
        _, target_real_id = self.ctx_service._parse_umo(str(target_umo or ""))
        target_probe = target_real_id or str(target_umo or "").strip()
        return bool(origin_real_id and target_probe and origin_real_id == target_probe)
