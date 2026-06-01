import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "daily_sharing_news_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
CONFIG_MODULE_NAME = f"{PACKAGE_NAME}.config"
NEWS_MODULE_NAME = f"{CORE_PACKAGE_NAME}.news"


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
        if name.startswith(PACKAGE_NAME) or name in {"astrbot", "astrbot.api", "aiohttp"}:
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


def _load_news_module():
    _clear_modules()

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    core_package = types.ModuleType(CORE_PACKAGE_NAME)
    core_package.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_package

    _install_stub_module("astrbot")
    _install_stub_module("astrbot.api", logger=_Logger())
    _install_stub_module("aiohttp", ClientError=Exception)

    _load_module(CONFIG_MODULE_NAME, ROOT / "config.py")
    return _load_module(NEWS_MODULE_NAME, ROOT / "core" / "news.py")


class NewsPayloadDecodingTests(unittest.TestCase):
    def test_decode_payload_accepts_trailing_debug_marker(self):
        mod = _load_news_module()
        service = mod.NewsService({"news_conf": {}})
        payload = (
            '{"code":200,"message":"success","data":[{"title":"第一条","hot_value":1}]}'
            "// === LEMON_API_USER_CODE_END ==="
        )

        data = service._loads_json_payload(payload)

        self.assertEqual(data["code"], 200)
        self.assertEqual(data["data"][0]["title"], "第一条")

    def test_decode_payload_accepts_status_prefix(self):
        mod = _load_news_module()
        service = mod.NewsService({"news_conf": {}})
        payload = (
            "HTTP Status: 200\n\n"
            '{"code":200,"message":"success","data":[{"title":"第一条","hot_value":1}]}'
        )

        data = service._loads_json_payload(payload)

        self.assertEqual(data["message"], "success")
        self.assertEqual(data["data"][0]["hot_value"], 1)

    def test_parse_response_limits_and_normalizes_common_fields(self):
        mod = _load_news_module()
        service = mod.NewsService({"news_conf": {"news_items_count": 5}})

        parsed = service._parse_response(
            {
                "data": [
                    {
                        "title": "标题一",
                        "hot_value": 123,
                        "link": "https://example.com/a",
                        "description": "<b>这是摘要</b>",
                    },
                    {"title": "标题二", "hot": "热", "mobile_link": "https://example.com/b"},
                ]
            },
            limit=1,
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "标题一")
        self.assertEqual(parsed[0]["hot"], "123")
        self.assertEqual(parsed[0]["url"], "https://example.com/a")
        self.assertEqual(parsed[0]["description"], "这是摘要")


if __name__ == "__main__":
    unittest.main()
