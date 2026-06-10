from ..image.providers import ImageProviderManager


class DashboardProviderProbeMixin:
    """Pages provider probing helpers."""

    def _page_probe_manager(self, kind: str) -> ImageProviderManager:
        if kind == "tts":
            return ImageProviderManager(self.context, self.tts_conf)
        return ImageProviderManager(self.context, self.image_conf)

    def _apply_page_probe_result(self, kind: str, result: dict) -> list[str]:
        fields = []
        plugin_name = str(result.get("plugin_name") or "").strip()
        method_path = str(result.get("method_path") or "").strip()
        prompt_arg = str(result.get("prompt_arg") or "").strip()

        if kind == "image":
            self.image_conf["image_provider"] = "generic_plugin"
            self.image_conf["generic_image_plugin_name"] = plugin_name
            self.image_conf["generic_image_method_path"] = method_path
            self.image_conf["generic_image_prompt_arg"] = prompt_arg or "prompt"
            fields.extend(
                [
                    "image_provider",
                    "generic_image_plugin_name",
                    "generic_image_method_path",
                    "generic_image_prompt_arg",
                ]
            )
        elif kind == "selfie":
            self.image_conf["image_provider"] = "generic_plugin"
            self.image_conf["generic_image_plugin_name"] = plugin_name
            self.image_conf["generic_image_edit_method_path"] = method_path
            self.image_conf["generic_image_edit_prompt_arg"] = prompt_arg or "prompt"
            self.image_conf["use_gitee_selfie_ref"] = True
            fields.extend(
                [
                    "image_provider",
                    "generic_image_plugin_name",
                    "generic_image_edit_method_path",
                    "generic_image_edit_prompt_arg",
                    "use_gitee_selfie_ref",
                ]
            )
        elif kind == "tts":
            self.tts_conf["tts_provider"] = "generic_plugin"
            self.tts_conf["generic_tts_plugin_name"] = plugin_name
            self.tts_conf["generic_tts_method_path"] = method_path
            self.tts_conf["generic_tts_text_arg"] = prompt_arg or "text"
            fields.extend(
                [
                    "tts_provider",
                    "generic_tts_plugin_name",
                    "generic_tts_method_path",
                    "generic_tts_text_arg",
                ]
            )
        return fields

    async def _page_probe_provider(self, kind: str, body: dict) -> dict:
        kind = str(kind or "").strip().lower()
        manager = self._page_probe_manager(kind)
        target_umo = str(body.get("target_umo") or "").strip()

        if kind == "image":
            result = await manager.probe_image_generation(body.get("prompt") or "")
        elif kind == "selfie":
            result = await manager.probe_image_selfie(
                body.get("prompt") or "",
                target_umo=target_umo,
            )
        elif kind == "tts":
            result = await manager.probe_tts_generation(
                body.get("text") or body.get("prompt") or "",
                emotion=str(body.get("emotion") or "neutral").strip() or "neutral",
                target_umo=target_umo,
            )
        else:
            raise RuntimeError("不支持的 provider 探测类型")

        if not result:
            raise RuntimeError("未找到可用工具，或测试调用没有返回可识别的媒体路径")

        applied = bool(body.get("apply", True))
        applied_fields = self._apply_page_probe_result(kind, result) if applied else []
        if applied:
            await self._save_config_and_refresh_runtime()

        return {
            "ok": True,
            "data": {
                "kind": kind,
                "applied": applied,
                "applied_fields": applied_fields,
                "result": result,
                "config": self._page_config_payload(),
            },
        }

    async def page_provider_probe(self):
        async def handler():
            body = await self._page_json_body()
            kind = body.get("kind")
            return await self._page_probe_provider(kind, body)

        return await self._page_json(handler)
