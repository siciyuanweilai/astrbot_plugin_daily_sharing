import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "daily_sharing_image_provider_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
PROVIDERS_MODULE_NAME = f"{CORE_PACKAGE_NAME}.image_providers"


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _clear_modules():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name in {"astrbot", "astrbot.api"}:
            sys.modules.pop(name, None)


def _install_stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _load_provider_module():
    _clear_modules()

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    core_package = types.ModuleType(CORE_PACKAGE_NAME)
    core_package.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_package

    _install_stub_module("astrbot")
    _install_stub_module("astrbot.api", logger=_Logger())
    return _load_module(PROVIDERS_MODULE_NAME, ROOT / "core" / "image_providers.py")


class _Star:
    def __init__(self, name, star_cls):
        self.name = name
        self.star_cls = star_cls


class _Context:
    def __init__(self, stars):
        self._stars = stars

    def get_all_stars(self):
        return self._stars


class _Draw:
    def __init__(self):
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return {"data": {"url": "https://example.test/generated.png"}}


class _Plugin:
    def __init__(self):
        self.draw = _Draw()


class ImageProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_plugin_calls_configured_method(self):
        mod = _load_provider_module()
        plugin = _Plugin()
        manager = mod.ImageProviderManager(
            _Context([_Star("astrbot_plugin_any_image", plugin)]),
            {
                "image_provider": "generic_plugin",
                "generic_image_plugin_name": "any_image",
                "generic_image_method_path": "draw.generate",
                "generic_image_prompt_arg": "text",
                "generic_image_extra_args": '{"size":"1024x1024"}',
                "generic_image_result_field": "data.url",
            },
        )

        result = await manager.generate_with_generic_plugin("a warm room")

        self.assertEqual(result, "https://example.test/generated.png")
        self.assertEqual(
            plugin.draw.calls,
            [{"size": "1024x1024", "text": "a warm room"}],
        )

    async def test_generic_plugin_autodetects_common_result_keys(self):
        mod = _load_provider_module()

        class Plugin:
            async def generate(self, **kwargs):
                return {"path": "C:/tmp/image.png"}

        manager = mod.ImageProviderManager(
            _Context([_Star("custom_drawer", Plugin())]),
            {
                "generic_image_plugin_name": "custom_drawer",
                "generic_image_method_path": "generate",
            },
        )

        result = await manager.generate_with_generic_plugin("prompt")

        self.assertEqual(result, "C:/tmp/image.png")

    def test_auto_prefers_gitee_when_available(self):
        mod = _load_provider_module()
        manager = mod.ImageProviderManager(
            _Context([_Star("astrbot_plugin_gitee_aiimg", object())]),
            {"image_provider": "auto"},
        )

        self.assertEqual(manager.select_provider(), "gitee_aiimg")

    def test_auto_falls_back_to_generic_plugin(self):
        mod = _load_provider_module()
        manager = mod.ImageProviderManager(_Context([]), {"image_provider": "auto"})

        self.assertEqual(manager.select_provider(), "generic_plugin")


if __name__ == "__main__":
    unittest.main()
