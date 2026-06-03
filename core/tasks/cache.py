from .common import *  # noqa: F401,F403


class TaskNewsCacheMixin:
    """News snapshot cache and cached-link lookup helpers."""

    def get_news_snapshot_limit(self) -> int:
        """缓存新闻长图对应 JSON 时尽量保留完整列表。"""
        return 50

    def _news_snapshot_key(self, target_uid: str) -> str:
        target = str(target_uid or "").strip() or "global"
        return f"news_snapshot:{target}"

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

            if not items and source_key:
                fetched = await self.news_service.get_hot_news(
                    source_key,
                    limit=self.get_news_snapshot_limit(),
                    allow_fallback=False
                )
                if fetched:
                    items, actual_source = fetched

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
            logger.info(f"[DailySharing] 已缓存 {target} 的新闻快照: {source_name} {len(snapshot_items)} 条")
            return True
        except Exception as e:
            logger.warning(f"[DailySharing] 缓存新闻快照失败: {e}")
            return False

    def _parse_chinese_number(self, value: str) -> Optional[int]:
        text = "".join(
            str(value or "")
            .strip()
            .translate(str.maketrans("０１２３４５６７８９", "0123456789"))
            .split()
        )
        if not text:
            return None
        if text.isdigit():
            return int(text)

        digits = {
            "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        }
        if text in digits:
            return digits[text]
        if "十" in text:
            left, right = text.split("十", 1)
            tens = 1 if not left else digits.get(left)
            ones = 0 if not right else digits.get(right)
            if tens is not None and ones is not None:
                return tens * 10 + ones
        return None

    def _parse_news_query_index(self, query: str) -> Optional[int]:
        text = "".join(
            str(query or "")
            .strip()
            .translate(str.maketrans("０１２３４５６７８９", "0123456789"))
            .split()
        )
        if not text:
            return None
        if text.startswith("第"):
            text = text[1:]
        for suffix in ("条", "个", "则", "篇"):
            if text.endswith(suffix):
                text = text[:-len(suffix)]
                break
        return self._parse_chinese_number(text)

    def _format_news_link_item(self, snapshot: dict, item: dict, index: int) -> str:
        source_name = snapshot.get("source_name") or "新闻热搜"
        title = item.get("title") or "未命名新闻"
        url = item.get("url") or ""
        if not url:
            return f"【{source_name}】第 {index} 条暂时没有可用原文链接。\n{title}"

        lines = [f"【{source_name}】第 {index} 条", title, url]
        desc = self._clean_snapshot_text(item.get("description"), 160)
        if desc and desc != title:
            lines.append(f"摘要：{desc}")
        return "\n".join(lines)

    async def get_cached_news_link(
        self,
        target_uid: str,
        query: str = "",
        source_key: str = None,
        refresh_source: bool = True
    ) -> str:
        """从最近一次新闻快照中按序号或关键词取原文链接。"""
        target = str(target_uid or "").strip()
        if not target:
            return "没找到当前会话，暂时无法读取新闻链接缓存。"

        if source_key and refresh_source:
            ok = await self.cache_news_snapshot(target, source_key=source_key)
            if not ok:
                source_name = NEWS_SOURCE_MAP.get(source_key, {}).get("name", source_key)
                return f"获取【{source_name}】新闻列表失败，暂时拿不到原文链接。"

        snapshot = await self.db.get_state(self._news_snapshot_key(target), {})
        if not isinstance(snapshot, dict) or not snapshot.get("items"):
            return "还没有可用的新闻列表缓存。先发送一次新闻热搜长图或新闻分享，再问“第3条链接”就可以。"

        if source_key and not refresh_source and snapshot.get("source_key") != source_key:
            wanted_name = NEWS_SOURCE_MAP.get(source_key, {}).get("name", source_key)
            current_name = snapshot.get("source_name") or "新闻热搜"
            return f"最近缓存的是【{current_name}】，不是【{wanted_name}】。请先发送对应新闻源长图，再直接问第几条链接。"

        items = snapshot.get("items") or []
        text = str(query or "").strip()
        if not text:
            preview = "\n".join(
                f"{idx}. {item.get('title', '未命名新闻')}"
                for idx, item in enumerate(items[:5], start=1)
            )
            return (
                f"最近缓存的是【{snapshot.get('source_name', '新闻热搜')}】，共 {len(items)} 条。\n"
                "请带上序号，例如：第3条链接\n"
                f"{preview}"
            )

        index = self._parse_news_query_index(text)
        if index is not None:
            if index < 1 or index > len(items):
                return f"这次缓存里只有 {len(items)} 条，换个序号试试。"
            return self._format_news_link_item(snapshot, items[index - 1], index)

        keyword = text.lower()
        for idx, item in enumerate(items, start=1):
            if keyword in str(item.get("title", "")).lower():
                return self._format_news_link_item(snapshot, item, idx)

        return f"最近缓存的【{snapshot.get('source_name', '新闻热搜')}】里没找到“{text}”。可以直接用序号，比如“第3条链接”。"
