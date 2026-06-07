import inspect
import json
import os
from typing import Any, Optional

from astrbot.api import logger


class ImageProviderError(RuntimeError):
    pass


class ImageProviderManager:
    def __init__(self, context, image_conf: dict):
        self.context = context
        self.image_conf = image_conf
        self._gitee_plugin = None
        self._gitee_plugin_not_found = False

    def _iter_stars(self):
        getter = getattr(self.context, "get_all_stars", None)
        if not callable(getter):
            return []
        try:
            return getter() or []
        except Exception as exc:
            logger.debug(f"[DailySharing] 获取插件列表失败: {exc}")
            return []

    def _find_star(self, plugin_name: str):
        plugin_name = str(plugin_name or "").strip()
        if not plugin_name:
            return None
        plugin_name_lower = plugin_name.lower()
        for star in self._iter_stars():
            names = [
                getattr(star, "name", ""),
                getattr(star, "id", ""),
                getattr(star, "module_name", ""),
            ]
            star_cls = getattr(star, "star_cls", None)
            if star_cls:
                names.extend(
                    [
                        getattr(star_cls, "name", ""),
                        getattr(star_cls, "__class__", type("", (), {})).__name__,
                    ]
                )
            for name in names:
                name_s = str(name or "").lower()
                if name_s and (plugin_name_lower == name_s or plugin_name_lower in name_s):
                    return star_cls
        return None

    def _ensure_gitee_plugin(self):
        if self._gitee_plugin or self._gitee_plugin_not_found:
            return
        self._gitee_plugin = self._find_star("astrbot_plugin_gitee_aiimg")
        if not self._gitee_plugin:
            self._gitee_plugin_not_found = True

    def get_gitee_plugin(self):
        self._ensure_gitee_plugin()
        return self._gitee_plugin

    def _resolve_method(self, target: Any, method_path: str):
        current = target
        for part in str(method_path or "").split("."):
            part = part.strip()
            if not part:
                continue
            current = getattr(current, part, None)
            if current is None:
                return None
        return current if callable(current) else None

    async def _maybe_await(self, value):
        if inspect.isawaitable(value):
            return await value
        return value

    def _read_extra_args(self) -> dict:
        raw = self.image_conf.get("generic_image_extra_args", "")
        if isinstance(raw, dict):
            return raw.copy()
        raw_s = str(raw or "").strip()
        if not raw_s:
            return {}
        try:
            parsed = json.loads(raw_s)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.warning(f"[DailySharing] 通用生图额外参数 JSON 解析失败: {exc}")
            return {}

    def _extract_result(self, result: Any) -> Optional[str]:
        if result is None:
            return None

        result_field = str(self.image_conf.get("generic_image_result_field", "") or "").strip()
        if result_field:
            current = result
            for part in result_field.split("."):
                part = part.strip()
                if not part:
                    continue
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    current = getattr(current, part, None)
                if current is None:
                    break
            if current:
                return str(current)

        if isinstance(result, (str, os.PathLike)):
            return str(result)
        if isinstance(result, dict):
            for key in ("path", "file", "file_path", "image_path", "url", "image_url", "result"):
                value = result.get(key)
                if value:
                    return str(value)
        for attr in ("path", "file", "file_path", "image_path", "url", "image_url"):
            value = getattr(result, attr, None)
            if value:
                return str(value)
        return None

    async def generate_with_generic_plugin(self, prompt: str) -> Optional[str]:
        plugin_name = str(self.image_conf.get("generic_image_plugin_name", "") or "").strip()
        method_path = str(self.image_conf.get("generic_image_method_path", "") or "").strip()
        prompt_arg = str(self.image_conf.get("generic_image_prompt_arg", "prompt") or "prompt").strip()
        if not plugin_name or not method_path:
            logger.warning("[DailySharing] 通用生图 provider 未配置插件名或方法路径")
            return None

        plugin = self._find_star(plugin_name)
        if not plugin:
            logger.error(f"[DailySharing] 未找到通用生图插件: {plugin_name}")
            return None

        method = self._resolve_method(plugin, method_path)
        if not method:
            logger.error(f"[DailySharing] 通用生图方法不存在: {plugin_name}.{method_path}")
            return None

        kwargs = self._read_extra_args()
        if prompt_arg:
            kwargs[prompt_arg] = prompt

        try:
            result = await self._maybe_await(method(**kwargs))
            image_ref = self._extract_result(result)
            if not image_ref:
                logger.error("[DailySharing] 通用生图 provider 未返回可识别的图片路径或 URL")
                return None
            return image_ref
        except TypeError as exc:
            logger.error(f"[DailySharing] 通用生图参数不匹配: {exc}")
            return None
        except Exception as exc:
            logger.error(f"[DailySharing] 通用生图调用失败: {exc}")
            return None

    def select_provider(self) -> str:
        provider = str(self.image_conf.get("image_provider", "gitee_aiimg") or "gitee_aiimg").strip().lower()
        if provider == "auto":
            self._ensure_gitee_plugin()
            return "gitee_aiimg" if self._gitee_plugin else "generic_plugin"
        if provider in {"generic", "plugin", "custom"}:
            return "generic_plugin"
        return provider
