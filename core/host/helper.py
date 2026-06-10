import re

from ..config import NEWS_SOURCE_MAP
from ..constants import SOURCE_CN_MAP


class PluginNewsHelperMixin:
    _NEWS_LINK_CONTEXT_MARKER = "# 每日分享新闻缓存上下文"

    def _strip_news_link_reference_tail(self, text: str) -> str:
        """移除 news_link 自然回复末尾由模型补出的参考链接列表。"""
        if not text:
            return text

        match = re.search(
            r"\n\s*(?:#{1,6}\s*)?(?:参考链接|参考来源|参考资料|引用来源|References?)\s*[:：]?\s*\n",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return text

        tail = text[match.end():]
        if not re.search(r"https?://", tail, flags=re.IGNORECASE):
            return text

        return text[:match.start()].rstrip()

    def _extract_news_link_urls(self, text: str) -> list[str]:
        """提取工具结果中已生成的链接，用于防止最终回复漏掉链接。"""
        urls = []
        for match in re.finditer(r"https?://[^\s<>\]）)。,，；;]+", str(text or ""), flags=re.IGNORECASE):
            url = match.group(0).rstrip(".,，。；;:：")
            if url and url not in urls:
                urls.append(url)
        return urls

    def _ensure_news_link_urls_in_reply(self, reply: str, urls: list[str]) -> str:
        """如果大语言模型最终回复漏掉 news_link 返回的链接，在末尾补齐。"""
        text = str(reply or "")
        missing = [url for url in urls or [] if url and url not in text]
        if not missing:
            return text
        suffix = "\n".join(missing)
        if text.strip():
            return f"{text.rstrip()}\n{suffix}"
        return suffix

    def _resolve_news_source_name(self, source: str = None):
        token = str(source or "").strip()
        if not token:
            return None

        token_lower = token.lower()
        if token in SOURCE_CN_MAP:
            return SOURCE_CN_MAP[token]
        if token_lower in NEWS_SOURCE_MAP:
            return token_lower

        for name, key in SOURCE_CN_MAP.items():
            if token in name or name in token:
                return key
        return None

    async def _build_news_link_context_prompt(self, target_uid: str) -> str:
        """为大语言模型追加最近新闻缓存状态，帮助它更稳地调用 news_link。"""
        manager = getattr(self, "task_manager", None)
        db = getattr(self, "db", None)
        if not manager or not db:
            return ""

        target = str(target_uid or "").strip()
        if not target:
            return ""

        try:
            snapshot = await db.get_state(manager._news_snapshot_key(target), {})
            if not manager._is_news_snapshot(snapshot):
                return ""

            items = snapshot.get("items") or []
            source_name = snapshot.get("source_name") or "新闻热搜"
            source_key = snapshot.get("source_key") or ""
            focus = await db.get_state(manager._news_snapshot_focus_key(target), {})
            focus_index = manager._coerce_news_tool_index((focus or {}).get("index"))
            focus_title = ""
            if focus_index and 1 <= focus_index <= len(items):
                focus_item = items[focus_index - 1] or {}
                focus_title = self._clean_news_context_text(focus_item.get("title"), 72)

            preview = []
            for idx, item in enumerate(items[:10], start=1):
                title = self._clean_news_context_text((item or {}).get("title"), 72)
                if title:
                    preview.append(f"{idx}. {title}")

            lines = [
                self._NEWS_LINK_CONTEXT_MARKER,
                "当前会话存在最近新闻缓存。用户追问新闻链接、原文、来源、出处、详情、摘要或“这个/刚才那条”时，优先调用 news_link 工具。",
                f"最近新闻源：{source_name}" + (f"（source={source_key}）" if source_key else ""),
                f"可查条目数：{len(items)}",
                "调用规则：用户说第几条时，你自己理解序号并把阿拉伯数字字符串填入 index；不要把“第十条链接”整句填入 query。",
                "调用规则：用户问链接/原文时 action=link；问详情/详细说说/摘要时 action=summary；问来源/出处时 action=source。",
                "调用规则：用户只说“这个/刚才那条/上面那条”且没有明确序号时，index 留空，news_link 会使用最近关注新闻。",
                "回复规则：news_link 返回短链接后，最终回复必须原样包含该短链接，不要省略、不要改写成原始长链接。",
            ]
            if focus_title:
                lines.append(f"最近关注：第 {focus_index} 条《{focus_title}》")
            if preview:
                lines.append("可查列表预览：")
                lines.extend(preview)
            return "\n".join(lines)
        except Exception:
            return ""

    @staticmethod
    def _clean_news_context_text(value, max_len: int = 80) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if max_len > 0 and len(text) > max_len:
            return text[:max_len].rstrip() + "..."
        return text
