import html
import json
import re
from typing import Optional, List, Dict, Any


class NewsParserMixin:
    """新闻接口响应解析。"""

    ITEM_CONTAINER_KEYS = ("data", "list", "items", "result")
    NESTED_ITEM_CONTAINER_KEYS = ("list", "items")
    TITLE_KEYS = ("title", "name", "query", "word", "keyword")
    HOT_KEYS = (
        "hot", "hotValue", "hot_value", "hot_value_desc", "heat",
        "hotScore", "like_count", "score_desc", "score",
    )
    URL_KEYS = ("url", "link", "mobileUrl", "mobile_url", "mobile_link")
    DESCRIPTION_KEYS = (
        "description", "desc", "summary", "abstract", "digest",
        "brief", "intro", "detail", "content",
    )

    def _loads_json_payload(self, text: str) -> Any:
        """从被状态文本或调试输出包裹的响应中提取最像新闻载荷的 JSON。"""
        if not text:
            raise json.JSONDecodeError("响应为空", "", 0)

        decoder = json.JSONDecoder()
        clean = text.lstrip("\ufeff \t\r\n")
        candidates = []
        direct_error = json.JSONDecodeError("未找到 JSON 载荷", clean, 0)

        try:
            data, end = decoder.raw_decode(clean)
            candidates.append((0, end, data))
        except json.JSONDecodeError as direct_error:
            pass

        for match in re.finditer(r"[\{\[]", clean):
            start = match.start()
            if start == 0 and candidates:
                continue
            try:
                data, end = decoder.raw_decode(clean[start:])
                candidates.append((start, start + end, data))
            except json.JSONDecodeError:
                continue

        if not candidates:
            raise direct_error

        for _, _, data in candidates:
            if self._has_parseable_news_items(data):
                return data

        return candidates[0][2]

    @staticmethod
    def _is_tencent_style_dict(value: dict) -> bool:
        return any(str(k).startswith("Top_") for k in value.keys())

    def _has_parseable_news_items(self, data: Any) -> bool:
        return any(
            isinstance(item, dict) and self._first_non_empty(item, self.TITLE_KEYS)
            for item in self._extract_news_items(data)
        )

    def _extract_news_items(self, data: Any) -> List[Dict]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []

        if self._is_tencent_style_dict(data):
            return list(data.values())

        for key in self.ITEM_CONTAINER_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                if self._is_tencent_style_dict(value):
                    return list(value.values())
                for nested_key in self.NESTED_ITEM_CONTAINER_KEYS:
                    nested_value = value.get(nested_key)
                    if isinstance(nested_value, list):
                        return nested_value

        return []

    @staticmethod
    def _first_non_empty(item: Dict, keys) -> Any:
        for key in keys:
            value = item.get(key)
            if value:
                return value
        return ""

    def _parse_response(self, data: Any, limit: int = None) -> Optional[List[Dict]]:
        """
        解析响应数据
        支持多层级 JSON 和多种字段名 (hot/heat/hotValue/hot_value)
        支持腾讯新闻这种字典结构的列表 {"Top_1": {...}, "Top_2": {...}}
        """
        items = self._extract_news_items(data)
        
        if not items: return None

        if limit is None:
            limit = self.conf.get("news_items_count", 5)
        try:
            limit = max(1, int(limit))
        except Exception:
            limit = 5

        def clean_text(value: Any, max_len: int = 800) -> str:
            if value is None:
                return ""
            text = html.unescape(str(value))
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if max_len > 0 and len(text) > max_len:
                return text[:max_len].rstrip() + "..."
            return text

        # 3. 提取标题、热度、链接、描述字段
        res = []
        for i in items: 
            # 如果列表非常长，仅在收集满时停止
            if len(res) >= limit: break 

            if not isinstance(i, dict): continue
            
            # 标题提取 (兼容多种字段名)
            title = self._first_non_empty(i, self.TITLE_KEYS)
            if not title: continue
            
            # 热度提取 (兼容多种字段名)
            hot = self._first_non_empty(i, self.HOT_KEYS)
            
            # 链接提取（兼容多种字段名）
            url_link = self._first_non_empty(i, self.URL_KEYS)
            description = self._first_non_empty(i, self.DESCRIPTION_KEYS)
            
            parsed_item = {
                "title": str(title).strip(),
                "hot": str(hot).strip() if hot else "",
                "url": str(url_link).strip() if url_link else ""
            }
            clean_description = clean_text(description)
            if clean_description and clean_description != parsed_item["title"]:
                parsed_item["description"] = clean_description

            for extra_key in ("author", "cover", "created", "created_at", "source"):
                if i.get(extra_key):
                    parsed_item[extra_key] = i.get(extra_key)

            res.append(parsed_item)
            
        return res if res else None

