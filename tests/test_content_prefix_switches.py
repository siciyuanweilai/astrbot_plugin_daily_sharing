import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "daily_sharing_content_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
CONFIG_MODULE_NAME = f"{PACKAGE_NAME}.config"
CONTENT_MODULE_NAME = f"{CORE_PACKAGE_NAME}.content"


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _Db:
    def __init__(self):
        self.recorded = []

    async def get_used_topics(self, target_id, category, days_limit=60):
        return []

    async def record_topic(self, target_id, category, keyword):
        self.recorded.append((target_id, category, keyword))


class _NewsService:
    async def get_baike_info(self, keyword):
        return f"{keyword} 的百科资料"


def _clear_modules():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name in {"astrbot", "astrbot.api", "aiofiles", "aiohttp"}:
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


def _load_content_module():
    _clear_modules()

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    core_package = types.ModuleType(CORE_PACKAGE_NAME)
    core_package.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_package

    _install_stub_module("astrbot")
    _install_stub_module("astrbot.api", logger=_Logger())
    _install_stub_module("aiofiles")
    _install_stub_module("aiohttp")

    _load_module(CONFIG_MODULE_NAME, ROOT / "config.py")
    return _load_module(CONTENT_MODULE_NAME, ROOT / "core" / "content.py")


def _ctx():
    return {
        "target_id": "test_target",
        "is_group": False,
        "nickname": "",
        "detect_name": "",
        "persona": "测试人格",
        "period_label": "下午",
        "date_str": "2026年05月31日",
        "time_str": "15:00",
        "life_hint": "",
        "chat_hint": "",
        "recent_dynamics": "",
    }


def _config(**content_library):
    return {
        "content_library": {
            "knowledge_cats": ["科学小发现: 蜂蜜"],
            "rec_cats": ["好物: 电脑"],
            **content_library,
        },
        "news_conf": {"enable_tavily_search": False},
        "basic_conf": {"data_retention_days": 60},
        "context_conf": {},
    }


def _service(response: str, **content_library):
    content_module = _load_content_module()

    async def call_llm(prompt, system_prompt=""):
        return response

    service = content_module.ContentService(
        _config(**content_library),
        call_llm,
        context=None,
        db_manager=_Db(),
        news_service=_NewsService(),
    )

    async def brainstorm(category_type, sub_category, target_id):
        return "电脑" if category_type == "好物" else "蜂蜜"

    service._agent_brainstorm_topic = brainstorm
    return service


class ContentPrefixSwitchesTests(unittest.IsolatedAsyncioTestCase):
    def test_default_content_library_survives_missing_schema_defaults(self):
        content_module = _load_content_module()

        async def call_llm(prompt, system_prompt=""):
            return ""

        service = content_module.ContentService(
            {"news_conf": {}, "basic_conf": {}, "context_conf": {}},
            call_llm,
            context=None,
            db_manager=_Db(),
            news_service=_NewsService(),
        )

        self.assertTrue(service.knowledge_cats)
        self.assertTrue(service.rec_cats)
        self.assertIn("有趣的冷知识", service.knowledge_cats)
        self.assertIn("书籍", service.rec_cats)

    async def test_knowledge_prefix_is_enabled_by_default(self):
        service = _service("【蜂蜜】不会轻易变质。$$happy$$")

        text = await service._gen_knowledge(_ctx())

        self.assertTrue(text.startswith("知识类型: 科学小发现 - 蜂蜜\n\n"))

    async def test_knowledge_prefix_can_be_hidden(self):
        service = _service(
            "【蜂蜜】不会轻易变质。$$happy$$",
            show_knowledge_type_prefix=False,
        )

        text = await service._gen_knowledge(_ctx())

        self.assertEqual(text, "【蜂蜜】不会轻易变质。$$happy$$")

    async def test_recommendation_prefix_is_enabled_by_default(self):
        service = _service("推荐【电脑】作为效率工具。$$happy$$")

        text = await service._gen_rec(_ctx())

        self.assertTrue(text.startswith("推荐类型: 好物 - 电脑\n\n"))

    async def test_recommendation_prefix_can_be_hidden(self):
        service = _service(
            "推荐【电脑】作为效率工具。$$happy$$",
            show_rec_type_prefix=False,
        )

        text = await service._gen_rec(_ctx())

        self.assertEqual(text, "推荐【电脑】作为效率工具。$$happy$$")


if __name__ == "__main__":
    unittest.main()
