import inspect
import json
import os
from typing import Any, Optional

from astrbot.api import logger


class ImageProviderError(RuntimeError):
    pass


class ImageProviderManager:
    IMAGE_PLUGIN_KEYWORDS = (
        "image",
        "img",
        "draw",
        "paint",
        "photo",
        "picture",
        "sd",
        "flux",
        "dalle",
        "gitee_aiimg",
        "生图",
        "绘图",
        "画图",
        "图片",
    )
    IMAGE_METHOD_KEYWORDS = (
        "generate_image",
        "draw_image",
        "text_to_image",
        "txt2img",
        "t2i",
        "create_image",
        "make_image",
        "generate",
        "draw",
        "paint",
    )
    GENERIC_METHOD_NAMES = {"generate", "draw", "paint"}
    PROMPT_ARG_NAMES = ("prompt", "text", "query", "description", "positive_prompt")
    COMMON_CHILD_ATTRS = (
        "draw",
        "image",
        "images",
        "img",
        "service",
        "generator",
        "client",
        "api",
        "backend",
        "model",
    )

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

    def _star_names(self, star) -> list:
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
                    getattr(getattr(star_cls, "__class__", None), "__name__", ""),
                ]
            )
        return [str(name or "") for name in names if str(name or "").strip()]

    def _star_display_name(self, star) -> str:
        names = self._star_names(star)
        return names[0] if names else "<unknown>"

    def _is_daily_sharing_star(self, star) -> bool:
        return any("daily_sharing" in name.lower() for name in self._star_names(star))

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

    def _method_accepts_prompt(self, method, prompt_arg: str) -> bool:
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return True

        params = list(sig.parameters.values())
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params):
            return True

        names = {
            param.name
            for param in params
            if param.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        return prompt_arg in names

    def _method_required_params(self, method) -> list:
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return []

        required = []
        for param in sig.parameters.values():
            if param.name == "self":
                continue
            if param.default is not inspect.Signature.empty:
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            required.append(param.name)
        return required

    def _select_prompt_arg(self, method) -> Optional[str]:
        required = self._method_required_params(method)
        for name in self.PROMPT_ARG_NAMES:
            if name in required or self._method_accepts_prompt(method, name):
                return name
        return None

    def _can_call_with_prompt(self, method, prompt_arg: str) -> bool:
        required = self._method_required_params(method)
        extra_args = self._read_extra_args()
        available = set(extra_args.keys())
        if prompt_arg:
            available.add(prompt_arg)
        return all(name in available for name in required)

    def _iter_candidate_methods(self):
        seen = set()
        for star in self._iter_stars():
            if self._is_daily_sharing_star(star):
                continue
            plugin = getattr(star, "star_cls", None)
            if not plugin:
                continue
            plugin_names = self._star_names(star)
            plugin_text = " ".join(plugin_names).lower()

            roots = [("", plugin)]
            for attr in self.COMMON_CHILD_ATTRS:
                try:
                    child = getattr(plugin, attr, None)
                except Exception:
                    child = None
                if child is not None:
                    roots.append((attr, child))

            for prefix, obj in roots:
                prefix_text = prefix.lower()
                root_looks_image = any(keyword in prefix_text for keyword in self.IMAGE_PLUGIN_KEYWORDS)
                attr_names = set(self.IMAGE_METHOD_KEYWORDS)
                try:
                    attr_names.update(name for name in dir(obj) if not name.startswith("_"))
                except Exception:
                    pass

                for attr in attr_names:
                    attr_lower = attr.lower()
                    if not any(keyword in attr_lower for keyword in self.IMAGE_METHOD_KEYWORDS):
                        continue
                    method_looks_specific = attr_lower not in self.GENERIC_METHOD_NAMES
                    plugin_looks_image = any(keyword in plugin_text for keyword in self.IMAGE_PLUGIN_KEYWORDS)
                    if not (method_looks_specific or plugin_looks_image or root_looks_image):
                        continue
                    try:
                        method = getattr(obj, attr, None)
                    except Exception:
                        continue
                    if not callable(method):
                        continue

                    method_path = f"{prefix}.{attr}" if prefix else attr
                    dedupe_key = (id(plugin), method_path)
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)

                    prompt_arg = self._select_prompt_arg(method)
                    if not prompt_arg or not self._can_call_with_prompt(method, prompt_arg):
                        continue

                    method_text = method_path.lower()
                    score = 0
                    if plugin_looks_image:
                        score += 20
                    if root_looks_image:
                        score += 10
                    if any(keyword in method_text for keyword in self.IMAGE_METHOD_KEYWORDS):
                        score += 10
                    if method_path in {"draw.generate", "generate_image", "draw_image", "txt2img"}:
                        score += 10

                    yield {
                        "score": score,
                        "star": star,
                        "plugin": plugin,
                        "method": method,
                        "method_path": method_path,
                        "prompt_arg": prompt_arg,
                    }

    def discover_image_methods(self) -> list:
        candidates = list(self._iter_candidate_methods())
        candidates.sort(
            key=lambda item: (
                item["score"],
                self._star_display_name(item["star"]),
                item["method_path"],
            ),
            reverse=True,
        )
        return candidates

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

    async def generate_with_auto_scan(self, prompt: str) -> Optional[str]:
        candidates = self.discover_image_methods()
        if not candidates:
            logger.warning("[DailySharing] 自动扫描未发现可用生图方法")
            return None

        extra_args = self._read_extra_args()
        for candidate in candidates:
            plugin_name = self._star_display_name(candidate["star"])
            method_path = candidate["method_path"]
            prompt_arg = candidate["prompt_arg"]
            kwargs = extra_args.copy()
            kwargs[prompt_arg] = prompt
            try:
                logger.info(f"[DailySharing] 自动扫描尝试生图: {plugin_name}.{method_path}")
                result = await self._maybe_await(candidate["method"](**kwargs))
                image_ref = self._extract_result(result)
                if image_ref:
                    logger.info(f"[DailySharing] 自动扫描生图成功: {plugin_name}.{method_path}")
                    return image_ref
                logger.debug(f"[DailySharing] 自动扫描候选未返回图片: {plugin_name}.{method_path}")
            except TypeError as exc:
                logger.debug(f"[DailySharing] 自动扫描候选参数不匹配: {plugin_name}.{method_path}: {exc}")
            except Exception as exc:
                logger.warning(f"[DailySharing] 自动扫描候选调用失败: {plugin_name}.{method_path}: {exc}")

        logger.error("[DailySharing] 自动扫描发现了候选方法，但全部调用失败")
        return None

    def select_provider(self) -> str:
        provider = str(self.image_conf.get("image_provider", "gitee_aiimg") or "gitee_aiimg").strip().lower()
        if provider == "auto":
            self._ensure_gitee_plugin()
            if self._gitee_plugin:
                return "gitee_aiimg"
            if self.image_conf.get("generic_image_plugin_name") and self.image_conf.get("generic_image_method_path"):
                return "generic_plugin"
            return "auto_scan"
        if provider in {"generic", "plugin", "custom"}:
            return "generic_plugin"
        if provider in {"scan", "auto_scan", "tool_scan"}:
            return "auto_scan"
        return provider
