import inspect
import json
import os
from collections.abc import Iterable
from typing import Any, Optional

from astrbot.api import logger


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
        "aiimg",
        "生图",
        "绘图",
        "画图",
        "图片",
    )
    IMAGE_METHOD_KEYWORDS = (
        "generate_image",
        "draw_image",
        "txt2img",
        "text2img",
        "t2i",
        "create_image",
        "make_image",
        "generate",
        "draw",
        "paint",
    )
    IMAGE_EDIT_METHOD_KEYWORDS = (
        "edit_image",
        "image_to_image",
        "img2img",
        "i2i",
        "generate_selfie",
        "selfie",
        "take_selfie",
        "selfie_image",
        "generate_portrait",
        "portrait",
        "persona_image",
        "persona",
        "generate_with_ref",
        "generate_with_reference",
        "draw_with_ref",
        "reference_image",
        "with_reference",
        "edit",
    )
    IMAGE_SELFIE_PRIORITY_KEYWORDS = (
        "selfie",
        "persona",
        "portrait",
        "reference",
        "with_ref",
        "with_reference",
    )
    IMAGE_EDIT_CHILD_ATTRS = (
        "selfie",
        "selfies",
        "portrait",
        "persona",
        "personas",
        "ref",
        "refs",
    )
    VIDEO_PLUGIN_KEYWORDS = (
        "video",
        "i2v",
        "image_to_video",
        "grok",
        "可灵",
        "视频",
    )
    VIDEO_METHOD_KEYWORDS = (
        "generate_video",
        "image_to_video",
        "img2video",
        "i2v",
        "create_video",
        "make_video",
        "generate_video_url",
        "video",
    )
    TTS_PLUGIN_KEYWORDS = (
        "tts",
        "voice",
        "audio",
        "speech",
        "语音",
        "音频",
        "朗读",
    )
    TTS_METHOD_KEYWORDS = (
        "text_to_speech",
        "tts",
        "synthesize",
        "synthesise",
        "generate_audio",
        "generate_voice",
        "create_audio",
        "make_audio",
        "process",
    )
    GENERIC_METHOD_NAMES = {"generate", "draw", "paint"}
    GENERIC_VIDEO_METHOD_NAMES = {"video"}
    GENERIC_TTS_METHOD_NAMES = {"process"}
    TEXT_RENDER_METHOD_KEYWORDS = (
        "text_to_image",
        "text2image",
        "markdown_to_image",
        "html_to_image",
        "render_text",
        "render_markdown",
        "render_html",
        "text_renderer",
        "markdown_renderer",
    )
    PROMPT_ARG_NAMES = ("prompt", "text", "query", "description", "positive_prompt")
    IMAGE_ARG_NAMES = ("images", "image", "ref_images", "reference_images", "init_images", "input_images")
    IMAGE_PATH_ARG_NAMES = ("image_path", "path", "file_path", "input_path", "source_image")
    VIDEO_IMAGE_BYTES_ARG_NAMES = ("image_bytes", "image", "input_image", "image_data")
    VIDEO_IMAGE_PATH_ARG_NAMES = ("image_path", "path", "file_path", "input_path", "source_image")
    VIDEO_PROMPT_ARG_NAMES = ("prompt", "video_prompt", "description", "text")
    TTS_TEXT_ARG_NAMES = ("text", "content", "prompt", "sentence")
    TTS_EMOTION_ARG_NAMES = ("emotion", "target_emotion", "style", "mood")
    SESSION_ARG_NAMES = ("session", "session_id", "target_umo", "umo")
    TTS_SESSION_ARG_NAMES = SESSION_ARG_NAMES
    COMMON_CHILD_ATTRS = (
        "draw",
        "edit",
        "image",
        "images",
        "img",
        "video",
        "videos",
        "voice",
        "audio",
        "tts",
        "speech",
        "service",
        "generator",
        "client",
        "api",
        "backend",
        "model",
    )
    RESULT_FIELDS = (
        "path",
        "file",
        "file_path",
        "image_path",
        "url",
        "image_url",
        "local_path",
        "output",
        "outputs",
        "result",
        "data",
    )
    VIDEO_RESULT_FIELDS = (
        "video_url",
        "url",
        "path",
        "file",
        "file_path",
        "video_path",
        "local_path",
        "output",
        "outputs",
        "result",
        "data",
    )
    AUDIO_RESULT_FIELDS = (
        "audio_path",
        "voice_path",
        "path",
        "file",
        "file_path",
        "url",
        "audio_url",
        "local_path",
        "output",
        "outputs",
        "result",
        "data",
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
            logger.debug(f"[DailySharing] Failed to list plugins: {exc}")
            return []

    def _star_names(self, star) -> list[str]:
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

    def _find_star(self, plugin_name: str):
        plugin_name = str(plugin_name or "").strip()
        if not plugin_name:
            return None
        plugin_name_lower = plugin_name.lower()
        for star in self._iter_stars():
            for name in self._star_names(star):
                name_lower = name.lower()
                if plugin_name_lower == name_lower or plugin_name_lower in name_lower:
                    return getattr(star, "star_cls", None)
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
        return self._read_json_args("generic_image_extra_args", "generic image extra args")

    def _read_json_args(self, config_key: str, label: str) -> dict:
        raw = self.image_conf.get(config_key, "")
        if isinstance(raw, dict):
            return raw.copy()
        raw_s = str(raw or "").strip()
        if not raw_s:
            return {}
        try:
            parsed = json.loads(raw_s)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.warning(f"[DailySharing] Failed to parse {label} JSON: {exc}")
            return {}

    def _method_param_info(self, method) -> tuple[set[str], bool]:
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return set(), True

        names = set()
        has_kwargs = False
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                has_kwargs = True
            elif param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                names.add(param.name)
        return names, has_kwargs

    def _select_supported_arg(self, method, arg_names: tuple[str, ...]) -> Optional[str]:
        names, has_kwargs = self._method_param_info(method)
        for arg_name in arg_names:
            if has_kwargs or arg_name in names:
                return arg_name
        return None

    def _build_supported_kwargs(self, method, extra_args: dict, values: list[tuple[tuple[str, ...], Any]]) -> Optional[dict]:
        kwargs = extra_args.copy()
        for arg_names, value in values:
            if value is None:
                continue
            arg_name = self._select_supported_arg(method, arg_names)
            if arg_name:
                kwargs[arg_name] = value

        required = self._method_required_params(method)
        names, has_kwargs = self._method_param_info(method)
        if has_kwargs:
            return kwargs
        if not all(name in kwargs or name not in names for name in required):
            return None
        if any(name not in kwargs for name in required):
            return None
        return kwargs

    def _split_config_list(self, config_key: str, default: tuple[str, ...]) -> list[str]:
        raw = self.image_conf.get(config_key, "")
        if isinstance(raw, (list, tuple)):
            items = raw
        else:
            items = str(raw or "").replace("，", ",").split(",")
        result = [str(item or "").strip() for item in items if str(item or "").strip()]
        return result or list(default)

    def _method_required_params(self, method) -> list[str]:
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

    def _iter_named_candidate_methods(
        self,
        method_keywords: tuple[str, ...],
        plugin_keywords: tuple[str, ...],
        generic_method_names: set[str],
        *,
        require_prompt: bool = True,
        extra_child_attrs: tuple[str, ...] = (),
        allow_selfie_child_generate: bool = False,
    ):
        seen = set()
        for star in self._iter_stars():
            if self._is_daily_sharing_star(star):
                continue
            plugin = getattr(star, "star_cls", None)
            if not plugin:
                continue
            plugin_text = " ".join(self._star_names(star)).lower()
            plugin_looks_media = any(keyword in plugin_text for keyword in plugin_keywords)

            roots = [("", plugin)]
            for attr in self.COMMON_CHILD_ATTRS + extra_child_attrs:
                try:
                    child = getattr(plugin, attr, None)
                except Exception:
                    child = None
                if child is not None:
                    roots.append((attr, child))

            for prefix, obj in roots:
                prefix_text = prefix.lower()
                root_looks_media = any(keyword in prefix_text for keyword in plugin_keywords)
                attr_names = set(method_keywords)
                try:
                    attr_names.update(name for name in dir(obj) if not name.startswith("_"))
                except Exception:
                    pass

                for attr in attr_names:
                    attr_lower = attr.lower()
                    if any(keyword in attr_lower for keyword in self.TEXT_RENDER_METHOD_KEYWORDS):
                        continue
                    method_matches = any(keyword in attr_lower for keyword in method_keywords)
                    selfie_root_matches = (
                        allow_selfie_child_generate
                        and
                        any(keyword in prefix_text for keyword in self.IMAGE_SELFIE_PRIORITY_KEYWORDS)
                        and attr_lower in {"generate", "draw", "create", "make", "process"}
                    )
                    if not (method_matches or selfie_root_matches):
                        continue
                    method_looks_specific = attr_lower not in generic_method_names
                    if not (method_looks_specific or plugin_looks_media or root_looks_media):
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

                    prompt_arg = self._select_prompt_arg(method) if require_prompt else None
                    if require_prompt and not prompt_arg:
                        continue

                    score = 0
                    if plugin_looks_media:
                        score += 20
                    if root_looks_media:
                        score += 10
                    if method_path in {"draw.generate", "generate_image", "draw_image", "txt2img"}:
                        score += 10
                    if method_looks_specific:
                        score += 5
                    if any(keyword in attr_lower or keyword in prefix_text for keyword in self.IMAGE_SELFIE_PRIORITY_KEYWORDS):
                        score += 15

                    yield {
                        "score": score,
                        "star": star,
                        "method": method,
                        "method_path": method_path,
                        "prompt_arg": prompt_arg,
                    }

    def _iter_candidate_methods(self):
        yield from self._iter_named_candidate_methods(
            self.IMAGE_METHOD_KEYWORDS,
            self.IMAGE_PLUGIN_KEYWORDS,
            self.GENERIC_METHOD_NAMES,
        )

    def _iter_image_edit_candidate_methods(self):
        yield from self._iter_named_candidate_methods(
            self.IMAGE_EDIT_METHOD_KEYWORDS,
            self.IMAGE_PLUGIN_KEYWORDS,
            {"edit", "selfie"},
            extra_child_attrs=self.IMAGE_EDIT_CHILD_ATTRS,
            allow_selfie_child_generate=True,
        )

    def _iter_video_candidate_methods(self):
        yield from self._iter_named_candidate_methods(
            self.VIDEO_METHOD_KEYWORDS,
            self.VIDEO_PLUGIN_KEYWORDS + self.IMAGE_PLUGIN_KEYWORDS,
            self.GENERIC_VIDEO_METHOD_NAMES,
        )

    def _iter_tts_candidate_methods(self):
        yield from self._iter_named_candidate_methods(
            self.TTS_METHOD_KEYWORDS,
            self.TTS_PLUGIN_KEYWORDS,
            self.GENERIC_TTS_METHOD_NAMES,
            require_prompt=False,
        )

    def discover_image_methods(self) -> list[dict]:
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

    def discover_image_edit_methods(self) -> list[dict]:
        candidates = list(self._iter_image_edit_candidate_methods())
        candidates.sort(
            key=lambda item: (
                item["score"],
                self._star_display_name(item["star"]),
                item["method_path"],
            ),
            reverse=True,
        )
        return candidates

    def discover_video_methods(self) -> list[dict]:
        candidates = list(self._iter_video_candidate_methods())
        candidates.sort(
            key=lambda item: (
                item["score"],
                self._star_display_name(item["star"]),
                item["method_path"],
            ),
            reverse=True,
        )
        return candidates

    def discover_tts_methods(self) -> list[dict]:
        candidates = list(self._iter_tts_candidate_methods())
        candidates.sort(
            key=lambda item: (
                item["score"],
                self._star_display_name(item["star"]),
                item["method_path"],
            ),
            reverse=True,
        )
        return candidates

    def _extract_field_path(
        self,
        result: Any,
        field_path: str,
        result_keys: tuple[str, ...] = None,
    ) -> Optional[str]:
        current = result
        for part in field_path.split("."):
            part = part.strip()
            if not part:
                continue
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return self._extract_result(current, result_keys=result_keys)

    def _extract_result(
        self,
        result: Any,
        result_field: str = "",
        result_keys: tuple[str, ...] = None,
    ) -> Optional[str]:
        if result is None:
            return None

        if not result_field:
            result_field = str(self.image_conf.get("generic_image_result_field", "") or "").strip()
        result_keys = result_keys or self.RESULT_FIELDS
        if result_field:
            image_ref = self._extract_field_path(result, result_field, result_keys=result_keys)
            if image_ref:
                return image_ref

        if isinstance(result, (str, os.PathLike)):
            return str(result)

        if isinstance(result, tuple):
            for item in result:
                image_ref = self._extract_result(item, result_keys=result_keys)
                if image_ref:
                    return image_ref
            return None

        if isinstance(result, dict):
            for key in result_keys:
                image_ref = self._extract_result(result.get(key), result_keys=result_keys)
                if image_ref:
                    return image_ref
            return None

        if isinstance(result, Iterable) and not isinstance(result, (bytes, bytearray)):
            for item in result:
                image_ref = self._extract_result(item, result_keys=result_keys)
                if image_ref:
                    return image_ref
            return None

        for attr in result_keys:
            image_ref = self._extract_result(getattr(result, attr, None), result_keys=result_keys)
            if image_ref:
                return image_ref
        return None

    async def _get_plugin_reference_images(self, plugin) -> list:
        if not plugin:
            return []
        ref_keys = self._split_config_list("generic_image_ref_keys", ("bot_selfie", "selfie", "default"))
        try:
            persona_mgr = getattr(plugin, "persona_mgr", None)
            active_ref_getter = getattr(persona_mgr, "get_active_ref_paths", None)
            if callable(active_ref_getter):
                ref_paths = await self._maybe_await(active_ref_getter())
                if ref_paths and hasattr(plugin, "_read_paths_bytes"):
                    return await self._maybe_await(plugin._read_paths_bytes(ref_paths))
                if ref_paths:
                    return list(ref_paths)

            if hasattr(plugin, "_get_config_selfie_reference_paths") and hasattr(plugin, "_read_paths_bytes"):
                ref_paths = plugin._get_config_selfie_reference_paths()
                if ref_paths:
                    return await self._maybe_await(plugin._read_paths_bytes(ref_paths))

            refs = getattr(plugin, "refs", None)
            if refs:
                for key in ref_keys:
                    getter = getattr(refs, "get_paths", None)
                    if not callable(getter):
                        continue
                    ref_paths = await self._maybe_await(getter(key))
                    if ref_paths and hasattr(plugin, "_read_paths_bytes"):
                        return await self._maybe_await(plugin._read_paths_bytes(ref_paths))
                    if ref_paths:
                        return list(ref_paths)
        except Exception as exc:
            logger.debug(f"[DailySharing] Failed to read plugin reference images: {exc}")
        return []

    async def _call_configured_method(
        self,
        plugin_name_key: str,
        method_path_key: str,
        *,
        default_plugin_key: str = "",
        values: list[tuple[tuple[str, ...], Any]],
        extra_args_key: str,
        result_field_key: str,
        result_keys: tuple[str, ...],
        label: str,
    ) -> Optional[str]:
        plugin_name = str(self.image_conf.get(plugin_name_key, "") or self.image_conf.get(default_plugin_key, "") or "").strip()
        method_path = str(self.image_conf.get(method_path_key, "") or "").strip()
        if not plugin_name or not method_path:
            logger.warning(f"[DailySharing] {label} provider is missing plugin name or method path")
            return None

        plugin = self._find_star(plugin_name)
        if not plugin:
            logger.error(f"[DailySharing] {label} plugin not found: {plugin_name}")
            return None

        method = self._resolve_method(plugin, method_path)
        if not method:
            logger.error(f"[DailySharing] {label} method not found: {plugin_name}.{method_path}")
            return None

        kwargs = self._build_supported_kwargs(
            method,
            self._read_json_args(extra_args_key, f"{label} extra args"),
            values,
        )
        if kwargs is None:
            logger.error(f"[DailySharing] {label} method required arguments are not satisfied: {plugin_name}.{method_path}")
            return None

        try:
            result = await self._maybe_await(method(**kwargs))
            media_ref = self._extract_result(
                result,
                result_field=str(self.image_conf.get(result_field_key, "") or "").strip(),
                result_keys=result_keys,
            )
            if not media_ref:
                logger.error(f"[DailySharing] {label} provider returned no recognizable media path or URL")
                return None
            return media_ref
        except TypeError as exc:
            logger.error(f"[DailySharing] {label} provider argument mismatch: {exc}")
            return None
        except Exception as exc:
            logger.error(f"[DailySharing] {label} provider failed: {exc}")
            return None

    async def _try_auto_candidates(
        self,
        candidates: list[dict],
        *,
        values: list[tuple[tuple[str, ...], Any]],
        extra_args_key: str,
        result_field_key: str,
        result_keys: tuple[str, ...],
        label: str,
    ) -> Optional[str]:
        if not candidates:
            logger.warning(f"[DailySharing] Auto scan found no usable {label} method")
            return None

        extra_args = self._read_json_args(extra_args_key, f"{label} extra args")
        for candidate in candidates:
            plugin_name = self._star_display_name(candidate["star"])
            method_path = candidate["method_path"]
            kwargs = self._build_supported_kwargs(candidate["method"], extra_args, values)
            if kwargs is None:
                logger.debug(f"[DailySharing] Auto scan candidate required args missing: {plugin_name}.{method_path}")
                continue
            try:
                logger.info(f"[DailySharing] Auto scan trying {label}: {plugin_name}.{method_path}")
                result = await self._maybe_await(candidate["method"](**kwargs))
                media_ref = self._extract_result(
                    result,
                    result_field=str(self.image_conf.get(result_field_key, "") or "").strip(),
                    result_keys=result_keys,
                )
                if media_ref:
                    logger.info(f"[DailySharing] Auto scan {label} succeeded: {plugin_name}.{method_path}")
                    return media_ref
                logger.debug(f"[DailySharing] Auto scan candidate returned no media: {plugin_name}.{method_path}")
            except TypeError as exc:
                logger.debug(f"[DailySharing] Auto scan candidate argument mismatch: {plugin_name}.{method_path}: {exc}")
            except Exception as exc:
                logger.warning(f"[DailySharing] Auto scan candidate failed: {plugin_name}.{method_path}: {exc}")

        logger.error(f"[DailySharing] Auto scan found {label} candidates, but all calls failed")
        return None

    async def _try_auto_image_edit_candidates(
        self,
        candidates: list[dict],
        *,
        prompt: str,
        target_umo: str = "",
    ) -> Optional[str]:
        if not candidates:
            logger.warning("[DailySharing] Auto scan found no usable image selfie/reference method")
            return None

        extra_args = self._read_json_args("generic_image_edit_extra_args", "image selfie/reference extra args")
        for candidate in candidates:
            plugin_name = self._star_display_name(candidate["star"])
            method_path = candidate["method_path"]
            plugin = getattr(candidate["star"], "star_cls", None)
            refs = await self._get_plugin_reference_images(plugin)
            kwargs = self._build_supported_kwargs(
                candidate["method"],
                extra_args,
                [
                    (self.PROMPT_ARG_NAMES, prompt),
                    (self.IMAGE_ARG_NAMES, refs or None),
                    (self.IMAGE_PATH_ARG_NAMES, refs[0] if refs else None),
                    (self.SESSION_ARG_NAMES, target_umo or None),
                ],
            )
            if kwargs is None:
                logger.debug(f"[DailySharing] Auto scan image selfie/reference required args missing: {plugin_name}.{method_path}")
                continue
            try:
                logger.info(f"[DailySharing] Auto scan trying image selfie/reference: {plugin_name}.{method_path}")
                result = await self._maybe_await(candidate["method"](**kwargs))
                media_ref = self._extract_result(
                    result,
                    result_field=str(self.image_conf.get("generic_image_result_field", "") or "").strip(),
                    result_keys=self.RESULT_FIELDS,
                )
                if media_ref:
                    logger.info(f"[DailySharing] Auto scan image selfie/reference succeeded: {plugin_name}.{method_path}")
                    return media_ref
                logger.debug(f"[DailySharing] Auto scan image selfie/reference returned no media: {plugin_name}.{method_path}")
            except TypeError as exc:
                logger.debug(f"[DailySharing] Auto scan image selfie/reference argument mismatch: {plugin_name}.{method_path}: {exc}")
            except Exception as exc:
                logger.warning(f"[DailySharing] Auto scan image selfie/reference failed: {plugin_name}.{method_path}: {exc}")

        logger.error("[DailySharing] Auto scan found image selfie/reference candidates, but all calls failed")
        return None

    async def generate_with_generic_plugin(
        self,
        prompt: str,
        use_ref_selfie: bool = False,
        target_umo: str = "",
    ) -> Optional[str]:
        if use_ref_selfie and self.image_conf.get("generic_image_edit_method_path"):
            refs = []
            plugin_name = str(self.image_conf.get("generic_image_plugin_name", "") or "").strip()
            plugin = self._find_star(plugin_name)
            if plugin:
                refs = await self._get_plugin_reference_images(plugin)
            prompt_arg = str(
                self.image_conf.get("generic_image_edit_prompt_arg", "")
                or self.image_conf.get("generic_image_prompt_arg", "prompt")
                or "prompt"
            ).strip()
            return await self._call_configured_method(
                "generic_image_plugin_name",
                "generic_image_edit_method_path",
                values=[
                    ((prompt_arg,), prompt),
                    (self.PROMPT_ARG_NAMES, prompt),
                    (self.IMAGE_ARG_NAMES, refs or None),
                    (self.IMAGE_PATH_ARG_NAMES, refs[0] if refs else None),
                    (self.SESSION_ARG_NAMES, target_umo or None),
                ],
                extra_args_key="generic_image_edit_extra_args",
                result_field_key="generic_image_result_field",
                result_keys=self.RESULT_FIELDS,
                label="generic image edit",
            )
        if use_ref_selfie:
            logger.warning("[DailySharing] Generic image provider is in selfie/reference mode but no edit method is configured")
            return None

        plugin_name = str(self.image_conf.get("generic_image_plugin_name", "") or "").strip()
        method_path = str(self.image_conf.get("generic_image_method_path", "") or "").strip()
        prompt_arg = str(self.image_conf.get("generic_image_prompt_arg", "prompt") or "prompt").strip()
        if not plugin_name or not method_path:
            logger.warning("[DailySharing] Generic image provider is missing plugin name or method path")
            return None

        plugin = self._find_star(plugin_name)
        if not plugin:
            logger.error(f"[DailySharing] Generic image plugin not found: {plugin_name}")
            return None

        method = self._resolve_method(plugin, method_path)
        if not method:
            logger.error(f"[DailySharing] Generic image method not found: {plugin_name}.{method_path}")
            return None

        kwargs = self._build_supported_kwargs(
            method,
            self._read_extra_args(),
            [((prompt_arg,), prompt), (self.PROMPT_ARG_NAMES, prompt)],
        )
        if kwargs is None:
            logger.error(f"[DailySharing] Generic image method required arguments are not satisfied: {plugin_name}.{method_path}")
            return None

        try:
            result = await self._maybe_await(method(**kwargs))
            image_ref = self._extract_result(result)
            if not image_ref:
                logger.error("[DailySharing] Generic image provider returned no recognizable image path or URL")
                return None
            return image_ref
        except TypeError as exc:
            logger.error(f"[DailySharing] Generic image provider argument mismatch: {exc}")
            return None
        except Exception as exc:
            logger.error(f"[DailySharing] Generic image provider failed: {exc}")
            return None

    async def generate_with_auto_scan(
        self,
        prompt: str,
        use_ref_selfie: bool = False,
        target_umo: str = "",
    ) -> Optional[str]:
        if use_ref_selfie:
            return await self._try_auto_image_edit_candidates(
                self.discover_image_edit_methods(),
                prompt=prompt,
                target_umo=target_umo,
            )

        candidates = self.discover_image_methods()
        if not candidates:
            logger.warning("[DailySharing] Auto scan found no usable image generation method")
            return None

        extra_args = self._read_extra_args()
        for candidate in candidates:
            plugin_name = self._star_display_name(candidate["star"])
            method_path = candidate["method_path"]
            prompt_arg = candidate["prompt_arg"]
            kwargs = extra_args.copy()
            kwargs[prompt_arg] = prompt
            try:
                logger.info(f"[DailySharing] Auto scan trying image generation: {plugin_name}.{method_path}")
                result = await self._maybe_await(candidate["method"](**kwargs))
                image_ref = self._extract_result(result)
                if image_ref:
                    logger.info(f"[DailySharing] Auto scan image generation succeeded: {plugin_name}.{method_path}")
                    return image_ref
                logger.debug(f"[DailySharing] Auto scan candidate returned no image: {plugin_name}.{method_path}")
            except TypeError as exc:
                logger.debug(f"[DailySharing] Auto scan candidate argument mismatch: {plugin_name}.{method_path}: {exc}")
            except Exception as exc:
                logger.warning(f"[DailySharing] Auto scan candidate failed: {plugin_name}.{method_path}: {exc}")

        logger.error("[DailySharing] Auto scan found candidates, but all calls failed")
        return None

    async def generate_video_with_generic_plugin(self, prompt: str, image_path: str, image_bytes: bytes = None) -> Optional[str]:
        return await self._call_configured_method(
            "generic_video_plugin_name",
            "generic_video_method_path",
            default_plugin_key="generic_image_plugin_name",
            values=[
                (self.VIDEO_PROMPT_ARG_NAMES, prompt),
                (self.VIDEO_IMAGE_PATH_ARG_NAMES, image_path),
                (self.VIDEO_IMAGE_BYTES_ARG_NAMES, image_bytes),
            ],
            extra_args_key="generic_video_extra_args",
            result_field_key="generic_video_result_field",
            result_keys=self.VIDEO_RESULT_FIELDS,
            label="generic video",
        )

    async def generate_video_with_auto_scan(self, prompt: str, image_path: str, image_bytes: bytes = None) -> Optional[str]:
        return await self._try_auto_candidates(
            self.discover_video_methods(),
            values=[
                (self.VIDEO_PROMPT_ARG_NAMES, prompt),
                (self.VIDEO_IMAGE_PATH_ARG_NAMES, image_path),
                (self.VIDEO_IMAGE_BYTES_ARG_NAMES, image_bytes),
            ],
            extra_args_key="generic_video_extra_args",
            result_field_key="generic_video_result_field",
            result_keys=self.VIDEO_RESULT_FIELDS,
            label="video generation",
        )

    async def generate_tts_with_generic_plugin(
        self,
        text: str,
        *,
        emotion: str = "",
        target_umo: str = "",
        session_state=None,
    ) -> Optional[str]:
        text_arg = str(self.image_conf.get("generic_tts_text_arg", "text") or "text").strip()
        return await self._call_configured_method(
            "generic_tts_plugin_name",
            "generic_tts_method_path",
            values=[
                ((text_arg,), text),
                (self.TTS_TEXT_ARG_NAMES, text),
                (self.TTS_EMOTION_ARG_NAMES, emotion or None),
                (self.TTS_SESSION_ARG_NAMES, target_umo or None),
                (("session_state", "state"), session_state),
            ],
            extra_args_key="generic_tts_extra_args",
            result_field_key="generic_tts_result_field",
            result_keys=self.AUDIO_RESULT_FIELDS,
            label="generic TTS",
        )

    async def generate_tts_with_auto_scan(
        self,
        text: str,
        *,
        emotion: str = "",
        target_umo: str = "",
        session_state=None,
    ) -> Optional[str]:
        return await self._try_auto_candidates(
            self.discover_tts_methods(),
            values=[
                (self.TTS_TEXT_ARG_NAMES, text),
                (self.TTS_EMOTION_ARG_NAMES, emotion or None),
                (self.TTS_SESSION_ARG_NAMES, target_umo or None),
                (("session_state", "state"), session_state),
            ],
            extra_args_key="generic_tts_extra_args",
            result_field_key="generic_tts_result_field",
            result_keys=self.AUDIO_RESULT_FIELDS,
            label="TTS generation",
        )

    def select_video_provider(self) -> str:
        provider = str(self.image_conf.get("video_provider", "gitee_aiimg") or "gitee_aiimg").strip().lower()
        if provider == "auto":
            self._ensure_gitee_plugin()
            if self._gitee_plugin:
                return "gitee_aiimg"
            if self.image_conf.get("generic_video_plugin_name") and self.image_conf.get("generic_video_method_path"):
                return "generic_plugin"
            return "auto_scan"
        if provider in {"generic", "plugin", "custom"}:
            return "generic_plugin"
        if provider in {"scan", "auto_scan", "tool_scan"}:
            return "auto_scan"
        return provider

    def select_tts_provider(self) -> str:
        provider = str(self.image_conf.get("tts_provider", "emotion_router") or "emotion_router").strip().lower()
        if provider == "auto":
            if self._find_star("tts_emotion"):
                return "emotion_router"
            if self.image_conf.get("generic_tts_plugin_name") and self.image_conf.get("generic_tts_method_path"):
                return "generic_plugin"
            return "auto_scan"
        if provider in {"generic", "plugin", "custom"}:
            return "generic_plugin"
        if provider in {"scan", "auto_scan", "tool_scan"}:
            return "auto_scan"
        return provider

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
