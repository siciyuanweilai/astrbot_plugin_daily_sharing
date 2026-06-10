from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class PluginAliasMixin:
    def _normalize_contact_aliases(self) -> dict:
        raw_aliases = getattr(self, "contact_aliases", [])
        aliases = {}
        if isinstance(raw_aliases, list):
            for item in raw_aliases:
                item_s = str(item or "").strip().replace("：", ":", 1)
                if ":" not in item_s:
                    continue
                key_s, value_s = [part.strip() for part in item_s.split(":", 1)]
                if key_s and value_s:
                    aliases[key_s] = value_s
        return aliases

    def _serialize_contact_aliases(self, aliases: dict) -> list:
        return [f"{key}:{value}" for key, value in aliases.items() if key and value]

    def _target_alias_keys(self, target_uid: str, event: AstrMessageEvent = None) -> list:
        keys = []

        def append_real_id(value: str) -> None:
            try:
                _, real_id = self.ctx_service._parse_umo(value)
                if real_id:
                    keys.append(real_id)
            except Exception as e:
                logger.debug(f"[每日分享] 解析昵称映射目标失败: {e}")

        target_s = str(target_uid or "").strip()
        if target_s:
            keys.append(target_s)
            append_real_id(target_s)
        if event:
            origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if origin:
                keys.append(origin)
                append_real_id(origin)
            try:
                sender_id = str(event.get_sender_id() or "").strip()
                if sender_id:
                    keys.append(sender_id)
            except Exception as e:
                logger.debug(f"[每日分享] 读取发送者标识失败: {e}")
        return list(dict.fromkeys(k for k in keys if k))

    def get_contact_alias(self, target_uid: str, event: AstrMessageEvent = None) -> str:
        aliases = self._normalize_contact_aliases()
        for key in self._target_alias_keys(target_uid, event):
            alias = str(aliases.get(key, "") or "").strip()
            if alias:
                return alias
        return ""

    def set_contact_alias(self, target_uid: str, alias: str, event: AstrMessageEvent = None) -> str:
        aliases = self._normalize_contact_aliases()
        keys = self._target_alias_keys(target_uid, event)
        save_key = ""
        for key in keys:
            if not self.task_manager._is_full_umo(key):
                save_key = key
                break
        if not save_key and keys:
            _, real_id = self.ctx_service._parse_umo(keys[0])
            save_key = real_id or keys[0]
        if not save_key:
            return ""
        aliases[save_key] = str(alias or "").strip()
        serialized_aliases = self._serialize_contact_aliases(aliases)
        self.config["contact_aliases"] = serialized_aliases
        self.contact_aliases = serialized_aliases
        return save_key

    def remove_contact_alias(self, target_uid: str, event: AstrMessageEvent = None) -> list:
        aliases = self._normalize_contact_aliases()
        removed = []
        for key in self._target_alias_keys(target_uid, event):
            if key in aliases:
                aliases.pop(key, None)
                removed.append(key)
        serialized_aliases = self._serialize_contact_aliases(aliases)
        self.config["contact_aliases"] = serialized_aliases
        self.contact_aliases = serialized_aliases
        return removed
