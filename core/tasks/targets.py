from .common import *  # noqa: F401,F403
from ..platform import (
    find_platform_instance_by_keywords,
    get_platform_id,
    get_platform_meta,
    get_platform_type,
    is_weixin_oc_instance,
    iter_platform_instances,
    platform_match_text,
)


class TaskTargetMixin:
    """Target parsing, platform selection, and display-name helpers."""

    def _is_full_umo(self, value: str) -> bool:
        """判断是否为 AstrBot 运行时的 unified_msg_origin。"""
        if not value or not isinstance(value, str):
            return False
        parts = value.split(":")
        return len(parts) >= 3 and "message" in parts[1].lower()

    def _looks_like_share_sequence(self, value: str) -> bool:
        """判断字符串是否像分享类型序列。"""
        if not value:
            return False
        valid = {"auto"} | {t.value for t in SharingType}
        parts = [p.strip().lower() for p in value.replace("，", ",").split(",") if p.strip()]
        return bool(parts) and all(p in valid for p in parts)

    def _looks_like_cron(self, value: str) -> bool:
        """判断字符串是否像 cron 或预设名。"""
        if not value:
            return False
        return value in CRON_TEMPLATES or self._parse_cron_to_kwargs(CRON_TEMPLATES.get(value, value)) is not None

    def _iter_platform_instances(self):
        return iter_platform_instances(self.plugin.context)

    def _get_platform_meta(self, inst):
        return get_platform_meta(inst)

    def _get_platform_id(self, inst) -> str:
        return get_platform_id(inst)

    def _get_platform_type(self, inst) -> str:
        return get_platform_type(inst)

    def _platform_match_text(self, inst) -> str:
        return platform_match_text(inst)

    def _find_platform_instance_by_keywords(self, keywords):
        return find_platform_instance_by_keywords(self.plugin.context, keywords)

    def _is_weixin_oc_instance(self, inst) -> bool:
        return is_weixin_oc_instance(inst)

    def _find_weixin_oc_platform_instance(self):
        for inst in self._iter_platform_instances():
            if self._is_weixin_oc_instance(inst):
                return inst
        return None

    def _find_weixin_oc_adapter_id(self) -> str:
        inst = self._find_weixin_oc_platform_instance()
        return self._get_platform_id(inst) if inst else ""

    def _find_adapter_id_by_keywords(self, keywords):
        """从平台实例信息里尽量按适配器类型找 bot id。"""
        try:
            inst = self._find_platform_instance_by_keywords(keywords)
            if inst:
                return self._get_platform_id(inst)
        except Exception as e:
            logger.debug(f"[DailySharing] 按类型查找平台 ID 失败: {e}")
        return None

    def _get_default_adapter_id(self, *, warn_on_fallback: bool = True) -> str:
        default_adapter_id = getattr(self.plugin, "_cached_adapter_id", None)
        if default_adapter_id:
            return default_adapter_id

        for inst in self._iter_platform_instances():
            p_id = self._get_platform_id(inst)
            if p_id:
                self.plugin._cached_adapter_id = p_id
                logger.debug(f"[DailySharing] 自动发现并缓存 Bot ID: {p_id}")
                return p_id

        if warn_on_fallback:
            logger.warning("[DailySharing] 尚未缓存 Adapter ID，使用默认值 'aiocqhttp'。")
        return "aiocqhttp"

    def _select_adapter_id_for_target(self, target_id: str, is_group: bool, default_adapter_id: str) -> str:
        """纯 ID 配置时，按 ID 形态选择更合理的平台，避免 QQ/微信串台。"""
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
        """将 /sid 获取的纯会话 ID 按平台和聊天类型拼成运行时 UMO。"""
        adapter_id = self._select_adapter_id_for_target(target_id, is_group, default_adapter_id)
        return f"{adapter_id}:{'GroupMessage' if is_group else 'FriendMessage'}:{target_id}"

    def _select_platform_instance_for_target(self, target_umo: str):
        """按目标 ID 形态选择平台实例，避开不同适配器共用同一个 platform id 的歧义。"""
        target_s = str(target_umo or "")
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        probe = real_id or target_s

        if adapter_id:
            get_platform_inst = getattr(self.plugin.context, "get_platform_inst", None)
            if callable(get_platform_inst):
                inst = get_platform_inst(adapter_id)
                if inst:
                    return inst
            for inst in self._iter_platform_instances():
                if self._get_platform_id(inst) == adapter_id:
                    return inst

        if self.ctx_service._is_weixin_platform(target_s):
            return self._find_weixin_oc_platform_instance()

        if probe.isdigit():
            return self._find_platform_instance_by_keywords(["aiocqhttp", "onebot"])

        return None

    def _build_message_session_for_target(self, target_umo: str, platform_inst=None):
        if not MessageSesion or not MessageType:
            return None

        target_s = str(target_umo or "").strip()
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        session_id = real_id or target_s
        platform_id = self._get_platform_id(platform_inst) if platform_inst else adapter_id
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
                logger.debug(f"[DailySharing] 清理昵称候选时读取发送者 ID 失败: {e}")
        if name in keys or name.endswith("@im.wechat"):
            return ""
        return name

    async def _get_onebot_nickname(self, target_uid: str, event: AstrMessageEvent = None) -> str:
        target_s = str(target_uid or "").strip()
        adapter_id, real_id = self.ctx_service._parse_umo(target_s)
        probe_id = real_id or target_s
        if not str(probe_id).isdigit():
            return ""

        bot = self.ctx_service._get_onebot_bot(target_s, event=event, adapter_id=adapter_id)
        if not bot:
            if event and self.ctx_service._is_onebot_event(event):
                return self._clean_nickname_candidate(event.get_sender_name(), target_s, event=event)
            return ""

        try:
            ret = await self.ctx_service._bot_call_action(bot, "get_stranger_info", user_id=int(probe_id))
            if ret and isinstance(ret, dict):
                remark = str(ret.get("remark", "") or "").strip()
                if remark:
                    logger.info(f"[DailySharing] 获取到用户备注: {remark}")
                    return remark
                nickname = str(ret.get("nickname", "") or "").strip()
                if nickname:
                    logger.info(f"[DailySharing] 获取到用户昵称: {nickname}")
                    return nickname
        except Exception as e:
            logger.warning(f"[DailySharing] 获取 QQ 昵称失败: {e}")

        if event and self.ctx_service._is_onebot_event(event):
            return self._clean_nickname_candidate(event.get_sender_name(), target_s, event=event)
        return ""

    def _get_target_conf(self, target_umo: str, is_group: bool, r_groups: dict, r_users: dict):
        """用运行时目标查找独立配置；配置表本身只保存纯会话 ID。"""
        adapter_id, real_id = self.ctx_service._parse_umo(target_umo)
        conf_map = r_groups if is_group else r_users
        if target_umo in conf_map:
            return conf_map[target_umo]
        if real_id in conf_map:
            return conf_map[real_id]
        return None

    def _is_unsupported_weixin_group_target(self, target_umo: str, is_group: bool) -> bool:
        """个人微信适配器基于 openclaw-weixin，只支持一对一私聊。"""
        return bool(is_group and self.ctx_service._is_weixin_platform(target_umo))

    def _parse_targets_config(self, conf_list):
        """核心解析器：配置项只接受 /sid 获取的纯 UID/Session ID。"""
        if isinstance(conf_list, dict): return conf_list
        res = {}
        if isinstance(conf_list, list):
            for item in conf_list:
                s = str(item).strip()
                if not s: continue
                # 支持中英文冒号混用                
                s = s.replace("：", ":")
                parts = [p.strip() for p in s.split(":")]

                target_id = s
                cron_str = None
                seq_str = None

                if len(parts) == 1:
                    target_id = parts[0]
                elif self._looks_like_share_sequence(parts[-1]):
                    seq_str = parts[-1]
                    if len(parts) >= 3 and self._looks_like_cron(parts[-2]):
                        cron_str = parts[-2]
                        target_id = ":".join(parts[:-2]).strip()
                    else:
                        target_id = ":".join(parts[:-1]).strip()
                else:
                    target_id = s

                if target_id:
                    if self._is_full_umo(target_id):
                        _, real_id = self.ctx_service._parse_umo(target_id)
                        hint = f"请改填 /sid 输出的 UID/Session ID：{real_id}" if real_id else "请改填 /sid 输出的 UID/Session ID"
                        logger.warning(f"[DailySharing] 配置项只支持纯 UID/Session ID，已跳过完整 UMO: {target_id}。{hint}")
                        continue
                    res[target_id] = {"cron": cron_str, "seq": seq_str}
        return res

    def get_broadcast_targets(self, exclude_custom_cron=False):
        """辅助方法：获取需要广播的目标列表。exclude_custom_cron 启用时会跳过有独立时间的群"""
        targets = []
        default_adapter_id = self._get_default_adapter_id()

        if default_adapter_id:
            # 解析配置为字典（支持冒号写法）
            r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
            r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

            for gid, conf in r_groups.items():
                if gid:
                    target_umo = self._build_target_umo(gid, True, default_adapter_id)
                    if self._is_unsupported_weixin_group_target(target_umo, True):
                        logger.warning(f"[DailySharing] weixin_oc 不支持群聊，已跳过广播目标: {gid}")
                        continue
                    # 如果全局广播开启了排除，且这个群有独立定时，跳过！
                    if exclude_custom_cron and isinstance(conf, dict) and conf.get("cron"):
                        continue
                    targets.append(target_umo)
            for uid, conf in r_users.items():
                if uid:
                    if exclude_custom_cron and isinstance(conf, dict) and conf.get("cron"):
                        continue
                    target_umo = self._build_target_umo(uid, False, default_adapter_id)
                    targets.append(target_umo)
        
        return targets

    def get_briefing_targets(self):
        """获取早报的独立广播目标，不填则不发"""
        targets = []
        default_adapter_id = self._get_default_adapter_id(warn_on_fallback=False)

        if default_adapter_id:
            b_groups = self.extra_shares_conf.get("briefing_groups", [])
            b_users = self.extra_shares_conf.get("briefing_users", [])

            for gid in b_groups:
                gid_clean = str(gid).strip()
                if gid_clean:
                    target_umo = self._build_target_umo(gid_clean, True, default_adapter_id)
                    if self._is_unsupported_weixin_group_target(target_umo, True):
                        logger.warning(f"[DailySharing] weixin_oc 不支持群聊，已跳过早报群聊目标: {gid_clean}")
                        continue
                    targets.append(target_umo)
            for uid in b_users:
                uid_clean = str(uid).strip()
                if uid_clean:
                    target_umo = self._build_target_umo(uid_clean, False, default_adapter_id)
                    targets.append(target_umo)
        
        return targets
