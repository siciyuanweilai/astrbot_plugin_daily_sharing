import re
from typing import Any

from ..config import CRON_TEMPLATES, NEWS_SOURCE_MAP, SharingType
from ..constants import CMD_CN_MAP, SOURCE_CN_MAP
from .common import _PAGE_RANDOM_PERIOD_RE, _PAGE_SHARE_TYPE_OPTIONS


class DashboardConfigValidationMixin:
    """仪表盘配置输入校验。"""

    def _page_share_type(self, value):
        raw = str(value or "auto").strip()
        if not raw or raw.lower() == "auto" or raw == "自动":
            return None
        if raw in CMD_CN_MAP:
            return CMD_CN_MAP[raw]
        try:
            return SharingType(raw)
        except ValueError as exc:
            raise RuntimeError(f"不支持的分享类型: {raw}") from exc

    def _page_news_source(self, value: str):
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw in NEWS_SOURCE_MAP:
            return raw
        if raw in SOURCE_CN_MAP:
            return SOURCE_CN_MAP[raw]
        raise RuntimeError(f"不支持的新闻源: {raw}")

    @staticmethod
    def _page_clean_text(value: Any, *, max_len: int = 500) -> str:
        text_value = str(value or "").strip()
        if len(text_value) > max_len:
            raise RuntimeError(f"配置内容过长，最多 {max_len} 个字符")
        return text_value

    @staticmethod
    def _page_int_value(value: Any, default: int, *, min_value: int, max_value: int) -> int:
        if value in (None, ""):
            value = default
        try:
            parsed = int(value)
        except Exception as exc:
            raise RuntimeError(f"配置值必须是数字: {value}") from exc
        return max(min_value, min(parsed, max_value))

    @staticmethod
    def _page_list_value(
        value: Any,
        *,
        max_items: int = 200,
        item_max_len: int = 300,
        split_commas: bool = False,
    ) -> list:
        return DashboardConfigValidationMixin._page_ordered_list_value(
            value,
            max_items=max_items,
            item_max_len=item_max_len,
            split_commas=split_commas,
            dedupe=True,
        )

    @staticmethod
    def _page_ordered_list_value(
        value: Any,
        *,
        max_items: int = 200,
        item_max_len: int = 300,
        split_commas: bool = False,
        dedupe: bool = False,
    ) -> list:
        if isinstance(value, str):
            normalized = value.replace("，", ",")
            raw_items = re.split(r"[\r\n,]+", normalized) if split_commas else normalized.splitlines()
        elif isinstance(value, list):
            raw_items = []
            for item in value:
                if split_commas and isinstance(item, str):
                    raw_items.extend(item.replace("，", ",").split(","))
                else:
                    raw_items.append(item)
        elif value in (None, ""):
            raw_items = []
        else:
            raise RuntimeError("列表配置格式无效")

        result = []
        seen = set()
        for item in raw_items:
            item_s = str(item or "").strip()
            if not item_s:
                continue
            if len(item_s) > item_max_len:
                raise RuntimeError(f"列表项过长，最多 {item_max_len} 个字符")
            if not dedupe or item_s not in seen:
                result.append(item_s)
                seen.add(item_s)
            if len(result) > max_items:
                raise RuntimeError(f"列表项过多，最多 {max_items} 条")
        return result

    def _page_choice_value(self, value: Any, allowed: set, default: str, label: str) -> str:
        raw = self._page_clean_text(value if value not in (None, "") else default, max_len=80)
        if raw not in allowed:
            raise RuntimeError(f"{label} 不支持: {raw}")
        return raw

    def _page_cron_value(self, value: Any, default: str, label: str) -> str:
        raw = self._page_clean_text(value if value not in (None, "") else default, max_len=80)
        actual = CRON_TEMPLATES.get(raw, raw)
        if self.task_manager._parse_cron_to_kwargs(actual) is None:
            raise RuntimeError(f"{label} 无效，需填写预设或 5/6/7 位定时表达式: {raw}")
        return raw

    def _page_random_periods_value(self, value: Any, default: list, label: str) -> list:
        items = self._page_list_value(
            value if value not in (None, "") else default,
            max_items=24,
            item_max_len=32,
            split_commas=True,
        )
        for item in items:
            if not _PAGE_RANDOM_PERIOD_RE.match(item):
                raise RuntimeError(f"{label} 时间段格式无效: {item}")
            start, end = item.split("-", 1)
            sh, sm = [int(part) for part in start.split(":", 1)]
            eh, em = [int(part) for part in end.split(":", 1)]
            if eh * 60 + em <= sh * 60 + sm:
                raise RuntimeError(f"{label} 结束时间必须晚于开始时间: {item}")
        return items

    def _page_type_list_value(self, value: Any, label: str) -> list:
        allowed = _PAGE_SHARE_TYPE_OPTIONS - {"auto"}
        items = self._page_list_value(value, max_items=len(allowed), item_max_len=32, split_commas=True)
        invalid = [item for item in items if item not in allowed]
        if invalid:
            raise RuntimeError(f"{label} 包含不支持的类型: {', '.join(invalid)}")
        return items

    def _page_sequence_value(self, value: Any, default: list, label: str) -> list:
        allowed = _PAGE_SHARE_TYPE_OPTIONS - {"auto"}
        items = self._page_ordered_list_value(
            value if value not in (None, "") else default,
            max_items=24,
            item_max_len=32,
            split_commas=True,
            dedupe=False,
        )
        invalid = [item for item in items if item not in allowed]
        if invalid:
            raise RuntimeError(f"{label} 包含不支持的类型: {', '.join(invalid)}")
        if not items:
            raise RuntimeError(f"{label} 不能为空")
        return items

    def _page_news_source_list_value(self, value: Any, label: str) -> list:
        items = self._page_list_value(value, max_items=len(NEWS_SOURCE_MAP), item_max_len=40, split_commas=True)
        invalid = [item for item in items if item not in NEWS_SOURCE_MAP]
        if invalid:
            raise RuntimeError(f"{label} 包含不支持的新闻源: {', '.join(invalid)}")
        return items

    def _page_contact_aliases_value(self, value: Any) -> list:
        items = self._page_list_value(value, max_items=300, item_max_len=300)
        result = []
        for item in items:
            normalized = str(item or "").strip().replace("：", ":", 1)
            if not normalized:
                continue
            if ":" not in normalized:
                raise RuntimeError("用户称呼映射格式应为 UID/Session ID:昵称")
            key, alias = [part.strip() for part in normalized.split(":", 1)]
            if not key or not alias:
                raise RuntimeError("用户称呼映射格式应为 UID/Session ID:昵称")
            result.append(f"{key}:{alias}")
        return result

    def _page_delay_range_value(self, value: Any, default: str = "1.0-2.0") -> str:
        raw = self._page_clean_text(value if value not in (None, "") else default, max_len=32)
        parts = raw.split("-", 1)
        try:
            if len(parts) == 2:
                start, end = [float(part.strip()) for part in parts]
                if start < 0 or end < start or end > 120:
                    raise ValueError
            else:
                delay = float(raw)
                if delay < 0 or delay > 120:
                    raise ValueError
        except Exception as exc:
            raise RuntimeError("发送延迟格式应为秒数或范围，例如 1.0-2.0") from exc
        return raw

    @staticmethod
    def _page_category_lines(value: Any, fallback: dict) -> list:
        raw = value or fallback
        if isinstance(raw, dict):
            lines = []
            for name, tags in raw.items():
                if isinstance(tags, list):
                    tags_text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
                else:
                    tags_text = str(tags or "").strip()
                name_text = str(name or "").strip()
                if name_text and tags_text:
                    lines.append(f"{name_text}: {tags_text}")
                elif name_text:
                    lines.append(name_text)
            return lines
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item or "").strip()]
        raw_text = str(raw or "").strip()
        return [raw_text] if raw_text else []
