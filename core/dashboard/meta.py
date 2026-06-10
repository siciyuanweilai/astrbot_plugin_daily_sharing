import json

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP, SharingType
from ..constants import TYPE_CN_MAP
from .common import _PAGE_CONF_SCHEMA_PATH


class DashboardConfigMetaMixin:
    """仪表盘配置选项和结构元信息。"""

    def _page_provider_options(self) -> list:
        options = [{"value": "", "label": "跟随会话默认"}]
        seen = {""}

        def add_option(value, label=None) -> None:
            value_s = str(value or "").strip()
            if not value_s or value_s in seen:
                return
            label_s = str(label or value_s).strip() or value_s
            options.append({"value": value_s, "label": label_s})
            seen.add(value_s)

        try:
            cfg = self.context.get_config() or {}
            for item in cfg.get("provider", []) or []:
                if not isinstance(item, dict):
                    continue
                provider_id = item.get("id") or item.get("provider_id")
                model = item.get("model") or item.get("model_name") or ""
                label = f"{provider_id} · {model}" if model else provider_id
                add_option(provider_id, label)
        except Exception as exc:
            logger.debug(f"[每日分享] 读取模型服务提供商配置失败: {exc}")

        try:
            provider_mgr = getattr(self.context, "provider_manager", None)
            inst_map = getattr(provider_mgr, "inst_map", {}) or {}
            for provider_id in inst_map.keys():
                add_option(provider_id)
        except Exception as exc:
            logger.debug(f"[每日分享] 读取模型服务提供商实例失败: {exc}")

        return options

    def _page_persona_options(self) -> list:
        options = [{"value": "", "label": "跟随默认人设"}]
        seen = {""}

        def add_option(value, label=None) -> None:
            value_s = str(value or "").strip()
            if not value_s or value_s in seen:
                return
            label_s = str(label or value_s).strip() or value_s
            options.append({"value": value_s, "label": label_s})
            seen.add(value_s)

        try:
            persona_mgr = getattr(self.context, "persona_manager", None)
            for item in getattr(persona_mgr, "personas_v3", []) or []:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("persona_id")
                    label = item.get("name") or item.get("persona_id")
                else:
                    name = getattr(item, "name", "") or getattr(item, "persona_id", "")
                    label = name
                add_option(name, label)
            for item in getattr(persona_mgr, "personas", []) or []:
                persona_id = getattr(item, "persona_id", "") or getattr(item, "name", "")
                add_option(persona_id)
        except Exception as exc:
            logger.debug(f"[每日分享] 读取人设配置失败: {exc}")

        return options

    def _page_config_options(self) -> dict:
        return {
            "trigger_modes": [
                {"value": "cron", "label": "定时触发"},
                {"value": "random_period", "label": "随机时段"},
            ],
            "share_types": [
                {"value": "auto", "label": "自动"},
                *[
                    {"value": item.value, "label": TYPE_CN_MAP.get(item.value, item.value)}
                    for item in SharingType
                ],
            ],
            "cron_presets": [
                {"value": key, "label": label}
                for key, label in (
                    ("morning", "早上 8 点"),
                    ("noon", "中午 12 点"),
                    ("afternoon", "下午 3 点"),
                    ("evening", "晚上 7 点"),
                    ("night", "晚上 10 点"),
                    ("twice", "早晚各一次"),
                    ("three_times", "早中晚"),
                )
            ],
            "news_random_modes": [
                {"value": "fixed", "label": "固定新闻源"},
                {"value": "random", "label": "全量随机"},
                {"value": "config", "label": "配置列表随机"},
                {"value": "time_based", "label": "按时段智能选择"},
            ],
            "news_sources": [
                {"value": key, "label": str(value.get("name") or key)}
                for key, value in NEWS_SOURCE_MAP.items()
            ],
            "context_strategies": [
                {"value": "cautious", "label": "谨慎模式"},
                {"value": "active", "label": "主动模式"},
                {"value": "minimal", "label": "最小模式"},
            ],
            "providers": self._page_provider_options(),
            "personas": self._page_persona_options(),
        }

    @staticmethod
    def _page_schema_meta_item(item: dict) -> dict:
        if not isinstance(item, dict):
            return {}
        meta = {}
        for key in ("title", "description", "hint", "type", "options"):
            value = item.get(key)
            if value not in (None, ""):
                meta[key] = value
        slider = item.get("slider")
        if isinstance(slider, dict):
            meta["slider"] = {
                key: slider[key]
                for key in ("min", "max", "step")
                if key in slider
            }
        return meta

    def _page_config_schema_meta(self) -> dict:
        try:
            stat_result = _PAGE_CONF_SCHEMA_PATH.stat()
            schema_version = (stat_result.st_mtime_ns, stat_result.st_size)
            if (
                self._page_config_schema_meta_cache is not None
                and self._page_config_schema_meta_version == schema_version
            ):
                return self._page_config_schema_meta_cache
            raw_schema = json.loads(_PAGE_CONF_SCHEMA_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug(f"[每日分享] 读取仪表盘配置结构失败: {exc}")
            return self._page_config_schema_meta_cache or {}

        root_fields = {}
        sections = {}
        for section_key, section_value in raw_schema.items():
            if not isinstance(section_value, dict):
                continue

            section_items = section_value.get("items")
            if section_value.get("type") == "object" and isinstance(section_items, dict):
                section_meta = self._page_schema_meta_item(section_value)
                section_meta["fields"] = {
                    field_key: self._page_schema_meta_item(field_value)
                    for field_key, field_value in section_items.items()
                    if isinstance(field_value, dict)
                }
                sections[section_key] = section_meta
            else:
                root_fields[section_key] = self._page_schema_meta_item(section_value)

        meta = {
            "root": root_fields,
            "sections": sections,
        }
        self._page_config_schema_meta_cache = meta
        self._page_config_schema_meta_version = schema_version
        return meta
