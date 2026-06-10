from astrbot.api import logger

from ...config import CRON_TEMPLATES, SharingType


class TaskTargetConfigMixin:
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

    def _get_target_conf(self, target_umo: str, is_group: bool, r_groups: dict, r_users: dict):
        """用运行时目标查找独立配置；配置表本身只保存纯会话标识。"""
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
        """核心解析器：配置项只接受 /sid 获取的纯会话标识。"""
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
                        hint = f"请改填 /sid 输出的纯会话标识：{real_id}" if real_id else "请改填 /sid 输出的纯会话标识"
                        logger.warning(f"[每日分享] 配置项只支持纯会话标识，已跳过完整 UMO: {target_id}。{hint}")
                        continue
                    res[target_id] = {"cron": cron_str, "seq": seq_str}
        return res

    def get_broadcast_targets(self, exclude_custom_cron=False, target_scope: str = "all"):
        """辅助方法：获取需要广播的目标列表。exclude_custom_cron 启用时会跳过有独立时间的群"""
        targets = []
        default_adapter_id = self._get_default_adapter_id()
        scope = str(target_scope or "all").strip().lower()
        include_groups = scope in {"all", "groups", "group"}
        include_users = scope in {"all", "users", "user", "private"}

        if default_adapter_id:
            # 解析配置为字典（支持冒号写法）
            r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
            r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

            if include_groups:
                for gid, conf in r_groups.items():
                    if gid:
                        target_umo = self._build_target_umo(gid, True, default_adapter_id)
                        if self._is_unsupported_weixin_group_target(target_umo, True):
                            logger.warning(f"[每日分享] 个人微信平台(weixin_oc)不支持群聊，已跳过广播目标: {gid}")
                            continue
                        # 如果全局广播开启了排除，且这个群有独立定时，跳过！
                        if exclude_custom_cron and isinstance(conf, dict) and conf.get("cron"):
                            continue
                        targets.append(target_umo)
            if include_users:
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
                        logger.warning(f"[每日分享] 个人微信平台(weixin_oc)不支持群聊，已跳过早报群聊目标: {gid_clean}")
                        continue
                    targets.append(target_umo)
            for uid in b_users:
                uid_clean = str(uid).strip()
                if uid_clean:
                    target_umo = self._build_target_umo(uid_clean, False, default_adapter_id)
                    targets.append(target_umo)
        
        return targets
