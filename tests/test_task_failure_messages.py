import asyncio
import importlib
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta
from enum import Enum
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

    def file_image(self, item):
        self.items.append(("file_image", item))
        return self

    def url_image(self, item):
        self.items.append(("url_image", item))
        return self


class _MessageType(Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"


class _MessageSession:
    def __init__(self, platform_name, message_type, session_id):
        self.platform_name = platform_name
        self.platform_id = platform_name
        self.message_type = message_type
        self.session_id = session_id


class _Event:
    def __init__(self, sender_id="123", unified_msg_origin="aiocqhttp:GroupMessage:123"):
        self.sent = []
        self._sender_id = sender_id
        self.unified_msg_origin = unified_msg_origin

    def plain_result(self, text):
        return text

    def image_result(self, image):
        return ("image", image)

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return "sender"

    async def send(self, message):
        self.sent.append(message)


class _Db:
    def __init__(self):
        self.history = []
        self.state = {}

    async def get_state(self, key, default=None):
        return self.state.get(key, default if default is not None else {})

    async def update_state_dict(self, key, updates):
        current = self.state.setdefault(key, {})
        current.update(updates)
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

    def _is_weixin_platform(self, target):
        return str(target).endswith("@im.wechat")

    def _find_plugin(self, name):
        return types.SimpleNamespace(service=object())

    async def get_history_data(self, *args, **kwargs):
        return {}

    def check_group_strategy(self, *args, **kwargs):
        return True

    def format_history_prompt(self, *args, **kwargs):
        return ""

    def format_life_context(self, *args, **kwargs):
        return ""

    async def record_bot_reply_to_history(self, *args, **kwargs):
        return None

    async def record_to_memos(self, *args, **kwargs):
        return None


class _NewsService:
    def select_news_source(self, excluded_source=None):
        return "yicai"

    async def get_hot_news(self, source=None):
        return None


class _ImageService:
    def __init__(self):
        self.generated = []

    def reset_last_description(self):
        return None

    def get_last_description(self):
        return ""

    async def generate_image(self, content, sharing_type, life_context=None):
        if sharing_type is None:
            raise AssertionError("sharing_type should be resolved before image generation")
        self.generated.append(
            {
                "content": content,
                "sharing_type": sharing_type,
                "life_context": life_context,
            }
        )
        return "generated.png"

    async def generate_video_from_image(self, image_path, content):
        return "generated.mp4"


class _ContentService:
    def __init__(self):
        self.calls = []

    async def generate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "content"


class _Scheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


class _Plugin:
    def __init__(self):
        self.scheduler = _Scheduler()
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
        self._bg_tasks = set()

    def _inject_qzone_client(self, qzone_plugin):
        return None


def _clear_modules():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name in {
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.message_components",
            "astrbot.core",
            "astrbot.core.platform",
            "astrbot.core.platform.message_session",
            "astrbot.core.platform.message_type",
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
    _install_stub_module("astrbot.core")
    _install_stub_module("astrbot.core.platform")
    _install_stub_module(
        "astrbot.core.platform.message_session",
        MessageSesion=_MessageSession,
    )
    _install_stub_module(
        "astrbot.core.platform.message_type",
        MessageType=_MessageType,
    )
    _install_stub_module("aiofiles")
    _install_stub_module("aiohttp")

    _load_module(CONFIG_MODULE_NAME, ROOT / "config.py")
    _load_module(CONSTANTS_MODULE_NAME, ROOT / "core" / "constants.py")
    return importlib.import_module(TASKS_MODULE_NAME)


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

    async def test_execute_share_continues_after_one_target_send_failure(self):
        mod = _load_tasks_module()
        plugin = _Plugin()
        plugin.receiver_conf = {"groups": ["111", "222"], "users": []}
        manager = mod.TaskManager(plugin)
        calls = []

        async def send(uid, *args, **kwargs):
            calls.append(uid)
            return len(calls) > 1

        manager.send = send

        await manager.execute_share(force_type=mod.SharingType.MOOD)

        self.assertEqual(
            calls,
            [
                "aiocqhttp:GroupMessage:111",
                "aiocqhttp:GroupMessage:222",
            ],
        )
        self.assertTrue(
            any(item[1].get("success") is False for item in plugin.db.history)
        )
        self.assertTrue(
            any(item[1].get("success") is True for item in plugin.db.history)
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

    async def test_async_daily_share_resolves_auto_before_requested_video(self):
        mod = _load_tasks_module()
        plugin = _Plugin()
        plugin.image_conf = {"enable_ai_image": True, "enable_ai_video": False}
        manager = mod.TaskManager(plugin)
        manager.get_curr_period = lambda: mod.TimePeriod.NIGHT
        sent = []

        async def send(target, content, image_path=None, audio_path=None, video_url=None, event=None):
            sent.append(
                {
                    "target": target,
                    "content": content,
                    "image_path": image_path,
                    "audio_path": audio_path,
                    "video_url": video_url,
                }
            )
            return True

        async def prepare_image_for_target(target, image_path):
            return image_path

        manager.send = send
        manager._prepare_image_for_target = prepare_image_for_target
        event = _Event()

        await manager.async_daily_share_task(
            event,
            share_type="\u81ea\u52a8",
            source=None,
            get_image=True,
            need_image=False,
            need_video=True,
            need_voice=False,
            to_qzone=False,
        )

        self.assertEqual(event.sent, [])
        self.assertEqual(plugin.content_service.calls[0][0][0], mod.SharingType.RECOMMENDATION)
        self.assertEqual(plugin.image_service.generated[0]["sharing_type"], mod.SharingType.RECOMMENDATION)
        self.assertEqual(sent[0]["image_path"], "generated.png")

    async def test_briefing_wrapper_schedules_random_delay(self):
        mod = _load_tasks_module()
        plugin = _Plugin()
        plugin.extra_shares_conf = {"briefing_cron_random_delay": 5}
        manager = mod.TaskManager(plugin)
        called = False

        async def execute_briefing_share(*args, **kwargs):
            nonlocal called
            called = True

        manager.execute_briefing_share = execute_briefing_share
        old_randint = mod.random.randint
        mod.random.randint = lambda start, end: 120
        try:
            await manager._task_wrapper_briefing()
        finally:
            mod.random.randint = old_randint

        self.assertFalse(called)
        self.assertEqual(len(plugin.scheduler.jobs), 1)
        job = plugin.scheduler.jobs[0]
        self.assertEqual(job["trigger"], "date")
        self.assertEqual(job["kwargs"]["id"], "delayed_briefing_share")
        self.assertIn("run_date", job["kwargs"])
        self.assertGreater(
            plugin.db.state["briefing"]["pending_delay_job"]["target_time"],
            datetime.now().timestamp(),
        )

    async def test_recover_pending_briefing_delay_job(self):
        mod = _load_tasks_module()
        plugin = _Plugin()
        target_time = datetime.now() + timedelta(minutes=3)
        plugin.db.state["briefing"] = {
            "pending_delay_job": {"target_time": target_time.timestamp()}
        }
        manager = mod.TaskManager(plugin)

        await manager._recover_pending_jobs()

        self.assertEqual(len(plugin.scheduler.jobs), 1)
        job = plugin.scheduler.jobs[0]
        self.assertEqual(job["trigger"], "date")
        self.assertEqual(job["kwargs"]["id"], "resume_briefing_share")
        self.assertEqual(job["kwargs"]["run_date"].replace(microsecond=0), target_time.replace(microsecond=0))


if __name__ == "__main__":
    unittest.main()
