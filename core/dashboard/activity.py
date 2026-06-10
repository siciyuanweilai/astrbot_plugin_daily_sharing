from .common import (
    _PAGE_RECENT_ACTION_LIMIT,
    _PAGE_RECENT_SHARE_LIMIT,
    _PAGE_SHARE_SOURCE_LABELS,
)


class DashboardActivityMixin:
    """仪表盘状态和最近动态。"""

    async def _page_states(self) -> dict:
        states = {}
        for key in ("global", "qzone", "briefing"):
            value = await self.db.get_state(key, {})
            states[key] = value if isinstance(value, dict) else {}
        return states
    def _page_recent_actions(self) -> list:
        runs = sorted(
            self._page_action_runs.values(),
            key=lambda item: item.get("started_at", ""),
            reverse=True,
        )
        return runs[:_PAGE_RECENT_ACTION_LIMIT]
    def _page_history_share_message(self, item: dict) -> str:
        success = bool(item.get("success"))
        suffix = "成功" if success else "失败"
        target_id = str(item.get("target_id") or "").strip()
        sharing_type = str(item.get("type") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if target_id == "qzone_broadcast":
            return f"QQ 空间分享{suffix}"
        if sharing_type == "briefing" or target_id in {"briefing", "briefing_broadcast"}:
            return f"早报分享{suffix}"
        if target_id == "global":
            return f"全局分享{suffix}"
        if "group" in kind:
            return f"群聊分享{suffix}"
        if "user" in kind or "friend" in kind or "private" in kind:
            return f"私聊分享{suffix}"
        try:
            _raw, _adapter_id, _probe_id, is_group = self._page_target_probe(target_id)
        except Exception:
            is_group = False
        return f"{'群聊' if is_group else '私聊'}分享{suffix}"
    def _page_history_share_action(self, item: dict) -> dict:
        success = bool(item.get("success"))
        source_type = str(item.get("source_type") or "").strip()
        return {
            "id": f"history-{item.get('id', '')}",
            "source": "history",
            "target": "",
            "target_id": item.get("target_id", ""),
            "target_label": item.get("target_label", ""),
            "kind": item.get("kind", ""),
            "share_type": item.get("type") or "auto",
            "news_source": "",
            "status": "success" if success else "error",
            "message": self._page_history_share_message(item),
            "started_at": item.get("timestamp", ""),
            "finished_at": item.get("timestamp", ""),
            "content": item.get("content", ""),
            "error_reason": item.get("error_reason", ""),
            "media_type": item.get("media_type", ""),
            "source_type": source_type,
            "source_label": _PAGE_SHARE_SOURCE_LABELS.get(source_type, ""),
        }
    def _page_recent_shares(self, history: list, targets: dict = None) -> list:
        kind_by_target = self._page_target_kind_map(targets or {})
        shares = []
        for item in history[:_PAGE_RECENT_SHARE_LIMIT]:
            item = dict(item)
            target_id = str(item.get("target_id") or "").strip()
            if target_id and not item.get("kind"):
                item["kind"] = kind_by_target.get(target_id, "")
            shares.append(self._page_history_share_action(item))
        return shares
    def _page_prune_actions(self) -> None:
        runs = sorted(
            self._page_action_runs.values(),
            key=lambda item: item.get("started_at", ""),
            reverse=True,
        )
        self._page_action_runs = {
            item["id"]: item for item in runs[:_PAGE_RECENT_ACTION_LIMIT]
        }
