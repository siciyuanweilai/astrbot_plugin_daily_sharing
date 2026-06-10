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


if __name__ == "__main__":
    unittest.main()
