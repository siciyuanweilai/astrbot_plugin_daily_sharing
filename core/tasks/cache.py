import re
from datetime import datetime
from typing import Optional

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP


class TaskNewsCacheMixin:
    """新闻快照缓存和缓存链接查询辅助方法。"""

    def get_news_snapshot_limit(self) -> int:
        """缓存新闻长图对应 JSON 时尽量保留完整列表。"""
        return 50

    def _news_snapshot_key(self, target_uid: str) -> str:
        target = str(target_uid or "").strip() or "global"
        return f"news_snapshot:{target}"

    def _news_snapshot_source_key(self, target_uid: str, source_key: str) -> str:
        source = str(source_key or "").strip() or "unknown"
        return f"{self._news_snapshot_key(target_uid)}:source:{source}"

    def _is_news_snapshot(self, snapshot) -> bool:
        return isinstance(snapshot, dict) and bool(snapshot.get("items"))

    def _clean_snapshot_text(self, value, max_len: int = 300) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if max_len > 0 and len(text) > max_len:
            return text[:max_len].rstrip() + "..."
        return text

    def _normalize_news_snapshot_items(self, items) -> list:
        normalized = []
        for item in list(items or [])[: self.get_news_snapshot_limit()]:
            if not isinstance(item, dict):
                continue

            title = self._clean_snapshot_text(item.get("title") or item.get("name"), 180)
            if not title:
                continue

            entry = {
                "title": title,
                "url": self._clean_snapshot_text(
                    item.get("url")
                    or item.get("link")
                    or item.get("mobile_link")
                    or item.get("mobile_url")
                    or item.get("mobileUrl"),
                    500
                ),
                "hot": self._clean_snapshot_text(
                    item.get("hot")
                    or item.get("hotValue")
                    or item.get("hot_value")
                    or item.get("hot_value_desc")
                    or item.get("score_desc")
                    or item.get("score"),
                    80
                ),
                "description": self._clean_snapshot_text(
                    item.get("description")
                    or item.get("summary")
                    or item.get("desc")
                    or item.get("content")
                    or item.get("detail"),
                    300
                ),
            }

            for extra_key in ("author", "cover", "created", "created_at"):
                if item.get(extra_key):
                    entry[extra_key] = item.get(extra_key)

            normalized.append(entry)
        return normalized

    async def cache_news_snapshot(self, target_uid: str, news_data=None, source_key: str = None, image_url: str = None) -> bool:
        """
        缓存一次新闻热搜 JSON 快照，用来把长图里的序号反查到原文链接。
        发送长图时传 source_key 会重新取同源 JSON；失败时不切到备用源，避免图文错位。
        """
        try:
            target = str(target_uid or "").strip()
            if not target:
                return False

            items = None
            actual_source = source_key

            if news_data:
                if isinstance(news_data, tuple) and len(news_data) >= 2:
                    items = news_data[0]
                    actual_source = news_data[1] or actual_source
                elif isinstance(news_data, list):
                    items = news_data

            snapshot_limit = self.get_news_snapshot_limit()
            item_count = len(items) if isinstance(items, list) else 0
            if actual_source and item_count < snapshot_limit:
                fetched = await self.news_service.get_hot_news(
                    actual_source,
                    limit=snapshot_limit,
                    allow_fallback=False
                )
                if fetched:
                    fetched_items, fetched_source = fetched
                    if isinstance(fetched_items, list) and len(fetched_items) > item_count:
                        items = fetched_items
                        actual_source = fetched_source or actual_source

            snapshot_items = self._normalize_news_snapshot_items(items)
            if not snapshot_items:
                return False

            source_name = NEWS_SOURCE_MAP.get(actual_source or "", {}).get("name") or "新闻热搜"
            snapshot = {
                "source_key": actual_source,
                "source_name": source_name,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "image_url": image_url or "",
                "items": snapshot_items,
            }

            await self.db.set_state(self._news_snapshot_key(target), snapshot)
            if actual_source:
                await self.db.set_state(self._news_snapshot_source_key(target, actual_source), snapshot)
            logger.info(f"[每日分享] 已缓存 {target} 的新闻快照: {source_name} {len(snapshot_items)} 条")
            return True
        except Exception as e:
            logger.warning(f"[每日分享] 缓存新闻快照失败: {e}")
            return False

    def _normalize_news_link_action(self, action: str) -> str:
        text = str(action or "").strip().lower()
        if text in {"summary", "detail", "details", "摘要", "详情", "详细", "详细说明", "详细说说", "介绍"}:
            return "summary"
        if text in {"source", "origin", "from", "出处", "来源", "新闻源"}:
            return "source"
        if text in {"list", "preview", "items", "列表", "清单", "目录", "可查列表"}:
            return "list"
        return "link"

    def _coerce_news_tool_index(self, index) -> Optional[int]:
        text = "".join(
            str(index or "")
            .strip()
            .translate(str.maketrans("０１２３４５６７８９", "0123456789"))
            .split()
        )
        return int(text) if text.isdigit() else None

    def _news_snapshot_focus_key(self, target_uid: str) -> str:
        return f"{self._news_snapshot_key(target_uid)}:focus"

    async def _remember_news_focus(self, target_uid: str, snapshot_key: str, snapshot: dict, index: int) -> None:
        focus = {
            "source_key": snapshot.get("source_key") or "",
            "index": index,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        focused_snapshot = dict(snapshot)
        focused_snapshot["last_focus_index"] = index
        focused_snapshot["last_focus_at"] = focus["updated_at"]
        await self.db.set_state(snapshot_key, focused_snapshot)
        await self.db.set_state(self._news_snapshot_focus_key(target_uid), focus)

        source_key = focused_snapshot.get("source_key")
        if source_key:
            source_state_key = self._news_snapshot_source_key(target_uid, source_key)
            if source_state_key != snapshot_key:
                source_snapshot = await self.db.get_state(source_state_key, {})
                if self._is_news_snapshot(source_snapshot):
                    source_snapshot = dict(source_snapshot)
                    source_snapshot["last_focus_index"] = index
                    source_snapshot["last_focus_at"] = focus["updated_at"]
                    await self.db.set_state(source_state_key, source_snapshot)

    async def _shorten_news_url(self, url: str) -> str:
        original_url = self._clean_snapshot_text(url, 500)
        if not original_url:
            return ""

        shortener = getattr(self.news_service, "shorten_url", None)
        if not callable(shortener):
            return original_url

        try:
            short_url = await shortener(original_url)
            return self._clean_snapshot_text(short_url, 500) or original_url
        except Exception as e:
            logger.debug(f"[每日分享] 生成新闻短链接失败，保留原链接: {e}")
            return original_url

    async def _format_news_link_item(self, snapshot: dict, item: dict, index: int, action: str = "link") -> str:
        source_name = snapshot.get("source_name") or "新闻热搜"
        source_key = snapshot.get("source_key") or ""
        title = item.get("title") or "未命名新闻"
        url = item.get("url") or ""
        if not url:
            return f"【{source_name}】第 {index} 条暂时没有可用原文链接。\n{title}"

        link = await self._shorten_news_url(url)
        if action == "source":
            lines = [f"【{source_name}】第 {index} 条", f"标题：{title}", f"来源：{source_name}"]
            if source_key:
                lines.append(f"来源标识：{source_key}")
            lines.append(f"短链接：{link}")
            return "\n".join(lines)

        lines = [f"【{source_name}】第 {index} 条", f"标题：{title}", f"短链接：{link}"]
        desc = self._clean_snapshot_text(item.get("description"), 160)
        if desc and desc != title:
            lines.append(f"摘要：{desc}")
        elif action == "summary":
            lines.append("摘要：当前缓存里暂时没有更详细的摘要，可以打开短链接查看原文。")
        return "\n".join(lines)

    def _format_news_link_preview(self, snapshot: dict, items: list, limit: int = 10) -> str:
        preview = "\n".join(
            f"{idx}. {item.get('title', '未命名新闻')}"
            for idx, item in enumerate(items[:limit], start=1)
        )
        return (
            f"工具内部提示：当前可查新闻列表为【{snapshot.get('source_name', '新闻热搜')}】，共 {len(items)} 条。"
            "如果用户想查链接、来源或详情，你需要把理解出的序号用阿拉伯数字填入 index 后再次调用本工具；"
            "不要向用户提及缓存命中或工具状态。\n"
            f"{preview}"
        )

    async def get_cached_news_link(
        self,
        target_uid: str,
        query: str = "",
        action: str = "link",
        index: str = "",
        source_key: str = None,
        refresh_source: bool = True
    ) -> str:
        """从最近一次新闻快照中按大语言模型结构化参数取链接、摘要或来源。"""
        target = str(target_uid or "").strip()
        if not target:
            return "工具内部提示：没有当前会话信息。请自然说明暂时查不到刚才那条新闻链接，不要提及工具状态。"

        if source_key and refresh_source:
            ok = await self.cache_news_snapshot(target, source_key=source_key)
            if not ok:
                source_name = NEWS_SOURCE_MAP.get(source_key, {}).get("name", source_key)
                return f"工具内部提示：获取【{source_name}】新闻列表失败。请自然说明暂时拿不到原文链接，不要提及工具状态。"

        snapshot_key = self._news_snapshot_key(target)
        snapshot = await self.db.get_state(snapshot_key, {})
        if source_key:
            source_snapshot_key = self._news_snapshot_source_key(target, source_key)
            source_snapshot = await self.db.get_state(source_snapshot_key, {})
            if self._is_news_snapshot(source_snapshot):
                snapshot_key = source_snapshot_key
                snapshot = source_snapshot

        if not self._is_news_snapshot(snapshot):
            return "工具内部提示：还没有可用于反查的新闻列表。请自然提醒用户先分享一次新闻，再问“第3条链接”，不要提及工具状态。"

        if source_key and not refresh_source and snapshot.get("source_key") != source_key:
            wanted_name = NEWS_SOURCE_MAP.get(source_key, {}).get("name", source_key)
            current_name = snapshot.get("source_name") or "新闻热搜"
            return f"工具内部提示：当前可用新闻源是【{current_name}】，不是用户指定的【{wanted_name}】。请自然提醒用户先分享对应新闻源，或直接问刚才新闻的第几条链接；不要提及工具状态。"

        action_key = self._normalize_news_link_action(action)
        items = snapshot.get("items") or []
        if action_key == "list":
            return self._format_news_link_preview(snapshot, items)

        index_text = str(index or "").strip()
        item_index = self._coerce_news_tool_index(index_text)
        if index_text and item_index is None:
            return (
                "工具内部提示：index 参数不是纯数字。请你自己理解用户要第几条，"
                "把阿拉伯数字字符串填入 index 后再次调用本工具；不要向用户提及工具状态。"
            )

        if item_index is None:
            focus = await self.db.get_state(self._news_snapshot_focus_key(target), {})
            focus_source = str((focus or {}).get("source_key") or "")
            focus_index = self._coerce_news_tool_index((focus or {}).get("index"))
            if focus_index and (not source_key or focus_source == (snapshot.get("source_key") or "")):
                item_index = focus_index

        if item_index is not None:
            if item_index < 1 or item_index > len(items):
                return f"工具内部提示：当前新闻列表共有 {len(items)} 条，用户请求的序号超出范围。请自然提醒换个 1-{len(items)} 范围内的序号，不要提及工具状态。"
            await self._remember_news_focus(target, snapshot_key, snapshot, item_index)
            return await self._format_news_link_item(snapshot, items[item_index - 1], item_index, action_key)

        text = str(query or "").strip()
        if not text:
            return self._format_news_link_preview(snapshot, items)

        keyword = text.lower()
        for idx, item in enumerate(items, start=1):
            haystack = f"{item.get('title', '')} {item.get('description', '')}".lower()
            if keyword in haystack:
                await self._remember_news_focus(target, snapshot_key, snapshot, idx)
                return await self._format_news_link_item(snapshot, item, idx, action_key)

        return f"工具内部提示：新闻列表里没找到“{text}”。请自然提醒用户换个关键词；如果用户表达的是第几条，请你把序号转成阿拉伯数字填入 index 后再次调用本工具。不要提及工具状态。"
