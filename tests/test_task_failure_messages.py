import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "daily_sharing_tasks_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
CONFIG_MODULE_NAME = f"{PACKAGE_NAME}.config"
CONSTANTS_MODULE_NAME = f"{CORE_PACKAGE_NAME}.constants"
TASKS_MODULE_NAME = f"{CORE_PACKAGE_NAME}.tasks"

YICAI_NAME = "\u7b2c\u4e00\u8d22\u7ecf\u70ed\u641c"


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _MessageChain:
    def __init__(self, *items):
        self.items = list(items)

    @classmethod
    def chain(cls):
        return cls()

    def message(self, *items):
        self.items.extend(items)
        return self


class _Event:
    def __init__(self):
        self.sent = []

    def plain_result(self, text):
        return text

    async def send(self, message):
        self.sent.append(message)


class _Db:
    def __init__(self):
        self.history = []

    async def get_state(self, key, default=None):
        return default if default is not None else {}

    async def update_state_dict(self, key, updates):
        return None

    async def add_sent_history(self, *args, **kwargs):
        self.history.append((args, kwargs))

    async def get_recent_history_by_target(self, target_id, limit=3):
        return []


class _CtxService:
    async def get_life_context(self):
        return {}

    def _parse_umo(self, target):
        return "aiocqhttp", str(target).split(":")[-1]

    def _is_group_chat(self, target):
        return "group" in str(target).lower()

    def _find_plugin(self, name):
        return types.SimpleNamespace(service=object())


class _NewsService:
    def select_news_source(self, excluded_source=None):
        return "yicai"

    async def get_hot_news(self, source=None):
        return None


class _ImageService:
    def reset_last_description(self):
        return None

    def get_last_description(self):
        return ""


class _ContentService:
    async def generate(self, *args, **kwargs):
        return "content"


class _Plugin:
    def __init__(self):
        self.scheduler = types.SimpleNamespace()
        self.db = _Db()
        self.ctx_service = _CtxService()
        self.news_service = _NewsService()
        self.image_service = _ImageService()
        self.content_service = _ContentService()
        self._lock = asyncio.Lock()
        self.basic_conf = {"sharing_type": "auto"}
        self.extra_shares_conf = {}
        self.qzone_conf = {}
        self.image_conf = {}
        self.tts_conf = {}
        self.context_conf = {}
        self.receiver_conf = {"groups": [], "users": []}
        self.config = {}
        self.data_dir = ROOT
        self.context = types.SimpleNamespace()
        self._cached_adapter_id = "aiocqhttp"
        self._is_terminated = False

    def _inject_qzone_client(self, qzone_plugin):
        return None


def _clear_modules():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name in {
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.message_components",
            "aiofiles",
            "aiohttp",
        }:
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


def _load_tasks_module():
    _clear_modules()

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    core_package = types.ModuleType(CORE_PACKAGE_NAME)
    core_package.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_package

    _install_stub_module("astrbot")
    _install_stub_module("astrbot.api", logger=_Logger())
    _install_stub_module(
        "astrbot.api.event",
        AstrMessageEvent=type("AstrMessageEvent", (), {}),
        MessageChain=_MessageChain,
    )
    _install_stub_module(
        "astrbot.api.message_components",
        Record=type("Record", (), {}),
        Video=type("Video", (), {}),
    )
    _install_stub_module("aiofiles")
    _install_stub_module("aiohttp")

    _load_module(CONFIG_MODULE_NAME, ROOT / "config.py")
    _load_module(CONSTANTS_MODULE_NAME, ROOT / "core" / "constants.py")
    return _load_module(TASKS_MODULE_NAME, ROOT / "core" / "tasks.py")


def _manager(mod):
    manager = mod.TaskManager(_Plugin())
    manager.get_curr_period = lambda: mod.TimePeriod.NIGHT
    return manager


class TaskFailureMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_share_sends_plain_news_failure_message(self):
        mod = _load_tasks_module()
        event = _Event()

        await _manager(mod).execute_share(
            force_type=mod.SharingType.NEWS,
            news_source="yicai",
            specific_target="aiocqhttp:GroupMessage:123",
            event=event,
        )

        self.assertEqual(
            event.sent,
            [
                f"\u83b7\u53d6\u3010{YICAI_NAME}\u3011"
                "\u65b0\u95fb\u5931\u8d25\uff0c\u5206\u4eab\u5df2\u53d6\u6d88\u3002"
            ],
        )

    async def test_execute_qzone_share_keeps_qzone_news_failure_message(self):
        mod = _load_tasks_module()
        event = _Event()

        await _manager(mod).execute_qzone_share(
            force_type=mod.SharingType.NEWS,
            news_source="yicai",
            event=event,
        )

        self.assertEqual(
            event.sent,
            [
                f"\u83b7\u53d6\u3010{YICAI_NAME}\u3011"
                "\u65b0\u95fb\u5931\u8d25\uff0cQQ\u7a7a\u95f4"
                "\u5206\u4eab\u5df2\u53d6\u6d88\u3002"
            ],
        )


if __name__ == "__main__":
    unittest.main()
