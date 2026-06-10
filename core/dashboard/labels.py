from astrbot.api import logger


class DashboardLabelsMixin:
    """仪表盘目标显示名解析。"""

    def _page_target_label_map(self, targets: dict) -> dict:
        labels = {}
        for bucket in ("groups", "users", "briefing_groups", "briefing_users"):
            for item in (targets or {}).get(bucket, []):
                target_id = str(item.get("id") or "").strip()
                label = str(item.get("target_label") or "").strip()
                if target_id and label:
                    labels[target_id] = label
        return labels
    def _page_target_kind_map(self, targets: dict) -> dict:
        kinds = {}
        bucket_kinds = {
            "groups": "group",
            "users": "user",
            "briefing_groups": "briefing_group",
            "briefing_users": "briefing_user",
        }
        for bucket, kind in bucket_kinds.items():
            for item in (targets or {}).get(bucket, []):
                target_id = str(item.get("id") or "").strip()
                if not target_id:
                    continue
                keys = [target_id]
                try:
                    _adapter_id, real_id = self.ctx_service._parse_umo(target_id)
                except Exception:
                    real_id = ""
                if real_id:
                    keys.append(real_id)
                for key in keys:
                    kinds[key] = kind
        return kinds
    def _page_random_share_target_label(self, targets: dict) -> str:
        labels = []
        seen = set()
        for bucket in ("groups", "users"):
            for item in (targets or {}).get(bucket, []):
                if str(item.get("cron") or "").strip():
                    continue
                label = str(item.get("target_label") or item.get("id") or "").strip()
                if label and label not in seen:
                    seen.add(label)
                    labels.append(label)
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        return f"{labels[0]}等 {len(labels)} 个目标"
    def _page_target_label(self, target_id: str) -> str:
        raw = str(target_id or "").strip()
        if not raw:
            return "\u5168\u5c40"
        known_labels = {
            "qzone_broadcast": "QQ \u7a7a\u95f4",
            "global": "\u5168\u5c40\u5206\u4eab",
            "briefing": "\u65e9\u62a5",
            "briefing_broadcast": "\u65e9\u62a5",
        }
        if raw in known_labels:
            return known_labels[raw]
        try:
            return self.get_contact_alias(raw)
        except Exception as exc:
            logger.debug(f"[每日分享] 构建仪表盘目标显示名失败: {raw}, {exc}")
            return ""
    def _page_target_probe(self, target_id: str, kind: str = "") -> tuple:
        raw = str(target_id or "").strip()
        try:
            adapter_id, real_id = self.ctx_service._parse_umo(raw)
        except Exception as exc:
            logger.debug(f"[每日分享] 解析仪表盘目标失败: {raw}, {exc}")
            adapter_id, real_id = "", ""
        probe_id = str(real_id or raw).strip()
        kind_s = str(kind or "").strip().lower()
        parts = raw.split(":")
        message_type = parts[1].lower() if len(parts) >= 2 else ""
        is_group = (
            kind_s in {"group", "briefing_group"}
            or any(key in message_type for key in ("group", "guild", "channel", "room"))
            or probe_id.endswith("@chatroom")
        )
        return raw, adapter_id, probe_id, is_group
    def _get_page_target_label_cache(self) -> dict:
        cache = getattr(self, "_page_target_label_cache_data", None)
        if not isinstance(cache, dict):
            cache = {}
            self._page_target_label_cache_data = cache
        return cache
    def _page_target_label_cache_keys(self, target_id: str, kind: str = "") -> list:
        raw, _adapter_id, probe_id, _is_group = self._page_target_probe(target_id, kind)
        return list(dict.fromkeys(key for key in (raw, probe_id) if key))
    async def _fetch_page_target_label(self, target_id: str, kind: str = "") -> str:
        raw, adapter_id, probe_id, is_group = self._page_target_probe(target_id, kind)
        if not probe_id or not str(probe_id).isdigit():
            return ""

        try:
            bot = self.ctx_service._get_onebot_bot(raw, adapter_id=adapter_id)
        except Exception as exc:
            logger.debug(f"[每日分享] 查找仪表盘 OneBot 客户端失败: {raw}, {exc}")
            return ""
        if not bot:
            return ""

        try:
            if is_group:
                result = await self.ctx_service._bot_call_action(
                    bot,
                    "get_group_info",
                    group_id=int(probe_id),
                )
                if isinstance(result, dict) and isinstance(result.get("data"), dict):
                    result = result["data"]
                if isinstance(result, dict):
                    return str(
                        result.get("group_name")
                        or result.get("group_remark")
                        or result.get("name")
                        or ""
                    ).strip()

            result = await self.ctx_service._bot_call_action(
                bot,
                "get_stranger_info",
                user_id=int(probe_id),
            )
            if isinstance(result, dict) and isinstance(result.get("data"), dict):
                result = result["data"]
            if isinstance(result, dict):
                return str(
                    result.get("remark")
                    or result.get("nickname")
                    or result.get("user_name")
                    or ""
                ).strip()
        except Exception as exc:
            logger.debug(f"[每日分享] 解析仪表盘目标显示名失败: {raw}, {exc}")
        return ""
    async def _resolve_page_target_label(self, target_id: str, kind: str = "") -> str:
        label = self._page_target_label(target_id)
        if label:
            return label

        cache = self._get_page_target_label_cache()
        keys = self._page_target_label_cache_keys(target_id, kind)
        for key in keys:
            label = str(cache.get(key) or "").strip()
            if label:
                return label

        label = await self._fetch_page_target_label(target_id, kind)
        if label:
            for key in keys:
                cache[key] = label
        return label
    async def _page_prepare_history_items(self, items: list) -> list:
        prepared = []
        for item in items:
            item = dict(item)
            label = await self._resolve_page_target_label(item.get("target_id", ""))
            if label:
                item["target_label"] = label
            prepared.append(item)
        return prepared
