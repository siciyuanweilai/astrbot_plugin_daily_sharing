class DashboardTargetConfigMixin:
    """仪表盘目标配置、统计和序列化。"""

    async def _page_target_item(self, target_id: str, conf, kind: str) -> dict:
        cron = None
        sequence = None
        if isinstance(conf, dict):
            cron = conf.get("cron")
            sequence = conf.get("seq")
        elif conf:
            sequence = str(conf)
        return {
            "id": str(target_id),
            "target_label": await self._resolve_page_target_label(target_id, kind),
            "kind": kind,
            "cron": cron or "",
            "sequence": sequence or "auto",
        }
    async def _page_targets(self) -> dict:
        r_groups = self.task_manager._parse_targets_config(
            self.receiver_conf.get("groups", [])
        )
        r_users = self.task_manager._parse_targets_config(
            self.receiver_conf.get("users", [])
        )
        briefing_groups = [
            await self._page_target_item(item, None, "briefing_group")
            for item in self.extra_shares_conf.get("briefing_groups", [])
            if str(item or "").strip()
        ]
        briefing_users = [
            await self._page_target_item(item, None, "briefing_user")
            for item in self.extra_shares_conf.get("briefing_users", [])
            if str(item or "").strip()
        ]
        groups = [
            await self._page_target_item(target_id, conf, "group")
            for target_id, conf in r_groups.items()
        ]
        users = [
            await self._page_target_item(target_id, conf, "user")
            for target_id, conf in r_users.items()
        ]
        return {
            "groups": groups,
            "users": users,
            "briefing_groups": briefing_groups,
            "briefing_users": briefing_users,
            "summary": {
                "share_targets": len(groups) + len(users),
                "briefing_targets": len(briefing_groups) + len(briefing_users),
            },
        }
    def _empty_target_stats(self) -> dict:
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "success_rate": 0,
            "recent_count": 0,
            "frequency_per_day": 0,
            "last_at": "",
            "last_success_at": "",
            "types": {},
        }
    def _index_target_stats(self, stats: list) -> dict:
        indexed = {}
        for item in stats:
            target_id = str(item.get("target_id") or "").strip()
            if not target_id:
                continue
            keys = [target_id]
            _, real_id = self.ctx_service._parse_umo(target_id)
            if real_id:
                keys.append(real_id)
            for key in keys:
                indexed[key] = item
        return indexed
    async def _enrich_page_targets(
        self,
        targets: dict,
        target_stats: list,
        briefing_target_stats: list = None,
    ) -> None:
        stats_by_key = self._index_target_stats(target_stats)
        briefing_stats_by_key = self._index_target_stats(briefing_target_stats or [])
        for bucket in ("groups", "users", "briefing_groups", "briefing_users"):
            for item in targets.get(bucket, []):
                target_id = str(item.get("id") or "")
                if bucket.startswith("briefing"):
                    item["stats"] = briefing_stats_by_key.get(target_id, self._empty_target_stats())
                    item["state"] = {}
                    continue
                item["stats"] = stats_by_key.get(target_id, self._empty_target_stats())
                state = await self.db.get_state(f"target_{target_id}", {})
                item["state"] = state if isinstance(state, dict) else {}
    def _clean_target_id_for_page(self, target_id: str) -> str:
        target_id = str(target_id or "").strip()
        if not target_id:
            raise RuntimeError("目标 ID 不能为空")
        if self.task_manager._is_full_umo(target_id):
            _, real_id = self.ctx_service._parse_umo(target_id)
            hint = f"，请改填 {real_id}" if real_id else ""
            raise RuntimeError(f"目标只支持 /sid 输出的纯 UID/Session ID{hint}")
        return target_id
    def _page_specific_share_target(self, target: str, target_id: str) -> tuple:
        raw = str(target_id or "").strip()
        if not raw or target not in {"broadcast_groups", "broadcast_users"}:
            return "", ""
        label = "群号" if target == "broadcast_groups" else "QQ号"
        if any(sep in raw for sep in (",", "，", ";", "；", "\n", "\r")):
            raise RuntimeError(f"一次只能指定一个{label}")
        clean_id = self._clean_target_id_for_page(raw)
        default_adapter_id = self.task_manager._get_default_adapter_id()
        is_group = target == "broadcast_groups"
        return (
            self.task_manager._build_target_umo(clean_id, is_group, default_adapter_id),
            "group" if is_group else "user",
        )
    def _serialize_page_share_target(self, item) -> str:
        if isinstance(item, str):
            raw = item.strip().replace("：", ":")
            if not raw:
                return ""
            parsed = self.task_manager._parse_targets_config([raw])
            if not parsed:
                raise RuntimeError(f"目标配置无效: {raw}")
            return raw

        if not isinstance(item, dict):
            raise RuntimeError("目标配置格式无效")

        target_id = self._clean_target_id_for_page(item.get("id"))
        cron = str(item.get("cron") or "").strip()
        sequence = str(item.get("sequence") or item.get("seq") or "auto").strip()
        sequence = sequence.replace("，", ",") or "auto"

        if cron and not self.task_manager._looks_like_cron(cron):
            raise RuntimeError(f"无效定时表达式: {cron}")
        if sequence and not self.task_manager._looks_like_share_sequence(sequence):
            raise RuntimeError(f"无效类型序列: {sequence}")

        if cron:
            return f"{target_id}:{cron}:{sequence}"
        if sequence and sequence.lower() != "auto":
            return f"{target_id}:{sequence}"
        return target_id
    def _serialize_page_briefing_target(self, item) -> str:
        target_id = item if isinstance(item, str) else item.get("id") if isinstance(item, dict) else ""
        return self._clean_target_id_for_page(target_id)
    def _normalize_page_target_list(self, items, *, briefing: bool = False) -> list:
        if not isinstance(items, list):
            raise RuntimeError("目标列表必须是数组")
        result = []
        seen = set()
        for item in items:
            entry = (
                self._serialize_page_briefing_target(item)
                if briefing
                else self._serialize_page_share_target(item)
            )
            entry = str(entry or "").strip()
            if not entry:
                continue
            key = entry.replace("：", ":")
            if key not in seen:
                result.append(entry)
                seen.add(key)
        return result
