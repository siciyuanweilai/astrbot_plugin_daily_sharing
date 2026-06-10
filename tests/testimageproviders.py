import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _install_astrbot_stub():
    for name in list(sys.modules):
        if name.startswith("astrbot"):
            sys.modules.pop(name, None)

    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.__path__ = []
    astrbot_api.logger = _Logger()

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api


def _load_providers_module():
    _install_astrbot_stub()
    module_name = "daily_sharing_image_providers_under_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "core" / "image" / "providers.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Star:
    def __init__(self, name, star_cls):
        self.name = name
        self.id = name
        self.module_name = name
        self.star_cls = star_cls


class _Context:
    def __init__(self, stars):
        self._stars = stars

    def get_all_stars(self):
        return self._stars


class ImageProviderManagerTests(unittest.TestCase):
    def test_auto_scan_skips_text_renderers_after_draw_succeeds(self):
        providers = _load_providers_module()
        calls = []

        class DrawService:
            async def generate(self, prompt):
                calls.append(("draw.generate", prompt))
                return {"data": {"image_path": "/tmp/generated.png"}}

        class Plugin:
            draw = DrawService()

            async def text_to_image(self, text):
                calls.append(("text_to_image", text))
                return "/tmp/text-card.png"

        manager = providers.ImageProviderManager(
            _Context([_Star("astrbot_plugin_aiimg_enhanced", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(manager.generate_with_auto_scan("real prompt"))

        self.assertEqual(result, "/tmp/generated.png")
        self.assertEqual(calls, [("draw.generate", "real prompt")])

    def test_manual_generic_provider_resolves_method_and_extra_args(self):
        providers = _load_providers_module()
        calls = []

        class DrawService:
            def generate(self, prompt, size):
                calls.append((prompt, size))
                return types.SimpleNamespace(output=["/tmp/manual.png"])

        class Plugin:
            draw = DrawService()

        manager = providers.ImageProviderManager(
            _Context([_Star("custom_image_plugin", Plugin())]),
            {
                "image_provider": "generic_plugin",
                "generic_image_plugin_name": "custom_image",
                "generic_image_method_path": "draw.generate",
                "generic_image_extra_args": json.dumps({"size": "1024x1024"}),
            },
        )

        result = asyncio.run(manager.generate_with_generic_plugin("manual prompt"))

        self.assertEqual(result, "/tmp/manual.png")
        self.assertEqual(calls, [("manual prompt", "1024x1024")])

    def test_probe_image_generation_reports_matched_candidate(self):
        providers = _load_providers_module()

        class DrawService:
            def generate(self, prompt):
                return {"data": {"image_path": "/tmp/probe.png"}}

        class Plugin:
            draw = DrawService()

        manager = providers.ImageProviderManager(
            _Context([_Star("custom_image_plugin", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(manager.probe_image_generation("probe prompt"))

        self.assertEqual(result["plugin_name"], "custom_image_plugin")
        self.assertEqual(result["method_path"], "draw.generate")
        self.assertEqual(result["prompt_arg"], "prompt")
        self.assertEqual(result["media_ref"], "/tmp/probe.png")

    def test_generic_image_edit_uses_plugin_reference_images(self):
        providers = _load_providers_module()
        calls = []

        class EditService:
            async def edit(self, prompt, images):
                calls.append((prompt, images))
                return {"image_path": "/tmp/selfie.png"}

        class Plugin:
            edit = EditService()

            def _get_config_selfie_reference_paths(self):
                return ["/tmp/ref.png"]

            async def _read_paths_bytes(self, paths):
                return [f"bytes:{path}".encode() for path in paths]

        manager = providers.ImageProviderManager(
            _Context([_Star("custom_image_plugin", Plugin())]),
            {
                "image_provider": "generic_plugin",
                "generic_image_plugin_name": "custom_image",
                "generic_image_method_path": "draw.generate",
                "generic_image_edit_method_path": "edit.edit",
            },
        )

        result = asyncio.run(manager.generate_with_generic_plugin("selfie prompt", use_ref_selfie=True))

        self.assertEqual(result, "/tmp/selfie.png")
        self.assertEqual(calls, [("selfie prompt", [b"bytes:/tmp/ref.png"])])

    def test_image_edit_uses_active_persona_reference_images(self):
        providers = _load_providers_module()
        calls = []

        class EditService:
            async def edit(self, prompt, images):
                calls.append((prompt, images))
                return {"image_path": "/tmp/persona-selfie.png"}

        class PersonaManager:
            def get_active_ref_paths(self):
                return ["/tmp/persona-ref.png"]

        class Plugin:
            edit = EditService()
            persona_mgr = PersonaManager()

            def _get_config_selfie_reference_paths(self):
                return ["/tmp/webui-ref.png"]

            async def _read_paths_bytes(self, paths):
                return [f"bytes:{path}".encode() for path in paths]

        manager = providers.ImageProviderManager(
            _Context([_Star("astrbot_plugin_aiimg_enhanced", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(manager.generate_with_auto_scan("selfie prompt", use_ref_selfie=True))

        self.assertEqual(result, "/tmp/persona-selfie.png")
        self.assertEqual(calls, [("selfie prompt", [b"bytes:/tmp/persona-ref.png"])])

    def test_auto_scan_prefers_edit_method_for_selfie_mode(self):
        providers = _load_providers_module()
        calls = []

        class Plugin:
            def edit_image(self, prompt):
                calls.append(("edit_image", prompt))
                return "/tmp/auto-selfie.png"

            def draw_image(self, prompt):
                calls.append(("draw_image", prompt))
                return "/tmp/auto-draw.png"

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_image_tools", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(manager.generate_with_auto_scan("selfie prompt", use_ref_selfie=True))

        self.assertEqual(result, "/tmp/auto-selfie.png")
        self.assertEqual(calls, [("edit_image", "selfie prompt")])

    def test_auto_scan_selfie_mode_passes_session_to_selfie_tool(self):
        providers = _load_providers_module()
        calls = []

        class Plugin:
            def generate_selfie(self, prompt, session):
                calls.append(("generate_selfie", prompt, session))
                return "/tmp/session-selfie.png"

            def draw_image(self, prompt):
                calls.append(("draw_image", prompt))
                return "/tmp/auto-draw.png"

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_persona_image_tools", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(
            manager.generate_with_auto_scan(
                "selfie prompt",
                use_ref_selfie=True,
                target_umo="FriendMessage:123",
            )
        )

        self.assertEqual(result, "/tmp/session-selfie.png")
        self.assertEqual(calls, [("generate_selfie", "selfie prompt", "FriendMessage:123")])

    def test_auto_scan_finds_generate_method_under_selfie_child(self):
        providers = _load_providers_module()
        calls = []

        class SelfieService:
            def generate(self, prompt, target_umo):
                calls.append(("selfie.generate", prompt, target_umo))
                return "/tmp/child-selfie.png"

        class DrawService:
            def generate(self, prompt):
                calls.append(("draw.generate", prompt))
                return "/tmp/draw.png"

        class Plugin:
            selfie = SelfieService()
            draw = DrawService()

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_image_tools", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(
            manager.generate_with_auto_scan(
                "selfie prompt",
                use_ref_selfie=True,
                target_umo="FriendMessage:456",
            )
        )

        self.assertEqual(result, "/tmp/child-selfie.png")
        self.assertEqual(calls, [("selfie.generate", "selfie prompt", "FriendMessage:456")])

    def test_auto_scan_normal_mode_ignores_selfie_child_generate(self):
        providers = _load_providers_module()
        calls = []

        class SelfieService:
            def generate(self, prompt):
                calls.append(("selfie.generate", prompt))
                return "/tmp/selfie.png"

        class DrawService:
            def generate(self, prompt):
                calls.append(("draw.generate", prompt))
                return "/tmp/draw.png"

        class Plugin:
            selfie = SelfieService()
            draw = DrawService()

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_image_tools", Plugin())]),
            {"image_provider": "auto_scan"},
        )

        result = asyncio.run(manager.generate_with_auto_scan("normal prompt"))

        self.assertEqual(result, "/tmp/draw.png")
        self.assertEqual(calls, [("draw.generate", "normal prompt")])

    def test_generic_selfie_mode_without_edit_method_does_not_fallback_to_draw(self):
        providers = _load_providers_module()
        calls = []

        class DrawService:
            def generate(self, prompt):
                calls.append(("draw.generate", prompt))
                return "/tmp/draw.png"

        class Plugin:
            draw = DrawService()

        manager = providers.ImageProviderManager(
            _Context([_Star("custom_image_plugin", Plugin())]),
            {
                "image_provider": "generic_plugin",
                "generic_image_plugin_name": "custom_image",
                "generic_image_method_path": "draw.generate",
            },
        )

        result = asyncio.run(manager.generate_with_generic_plugin("selfie prompt", use_ref_selfie=True))

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_auto_scan_video_uses_prompt_and_image_path(self):
        providers = _load_providers_module()
        calls = []

        class Plugin:
            def image_to_video(self, prompt, image_path):
                calls.append((prompt, image_path))
                return {"data": {"video_url": "https://example.com/video.mp4"}}

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_video_tools", Plugin())]),
            {"video_provider": "auto_scan"},
        )

        result = asyncio.run(
            manager.generate_video_with_auto_scan("video prompt", "D:/tmp/image.png", b"image-bytes")
        )

        self.assertEqual(result, "https://example.com/video.mp4")
        self.assertEqual(calls, [("video prompt", "D:/tmp/image.png")])

    def test_generic_tts_passes_text_and_emotion(self):
        providers = _load_providers_module()
        calls = []

        class Plugin:
            def text_to_speech(self, text, emotion):
                calls.append((text, emotion))
                return types.SimpleNamespace(audio_path="/tmp/voice.mp3")

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_voice_tools", Plugin())]),
            {
                "tts_provider": "generic_plugin",
                "generic_tts_plugin_name": "voice_tools",
                "generic_tts_method_path": "text_to_speech",
            },
        )

        result = asyncio.run(
            manager.generate_tts_with_generic_plugin("hello", emotion="happy", target_umo="session-1")
        )

        self.assertEqual(result, "/tmp/voice.mp3")
        self.assertEqual(calls, [("hello", "happy")])

    def test_probe_tts_generation_reports_matched_candidate(self):
        providers = _load_providers_module()

        class Plugin:
            def text_to_speech(self, text, emotion):
                return {"audio_path": f"/tmp/{emotion}-{text}.mp3"}

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_voice_tools", Plugin())]),
            {"tts_provider": "auto_scan"},
        )

        result = asyncio.run(manager.probe_tts_generation("hello", emotion="happy"))

        self.assertEqual(result["plugin_name"], "plugin_voice_tools")
        self.assertEqual(result["method_path"], "text_to_speech")
        self.assertEqual(result["prompt_arg"], "text")
        self.assertEqual(result["media_ref"], "/tmp/happy-hello.mp3")

    def test_auto_provider_falls_back_to_scan_without_gitee(self):
        providers = _load_providers_module()

        class Plugin:
            def draw_image(self, prompt):
                return "/tmp/scan.png"

        manager = providers.ImageProviderManager(
            _Context([_Star("plugin_draw_image", Plugin())]),
            {"image_provider": "auto"},
        )

        self.assertEqual(manager.select_provider(), "auto_scan")

    def test_schema_exposes_generic_image_provider_options(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        image_items = schema["image_conf"]["items"]

        self.assertEqual(
            image_items["image_provider"]["options"],
            ["gitee_aiimg", "generic_plugin", "auto_scan", "auto"],
        )
        self.assertIn("generic_image_method_path", image_items)
        self.assertIn("generic_image_result_field", image_items)
        self.assertIn("generic_image_edit_method_path", image_items)
        self.assertIn("video_provider", image_items)

        tts_items = schema["tts_conf"]["items"]
        self.assertEqual(
            tts_items["tts_provider"]["options"],
            ["emotion_router", "generic_plugin", "auto_scan", "auto"],
        )
        self.assertIn("generic_tts_method_path", tts_items)


if __name__ == "__main__":
    unittest.main()
