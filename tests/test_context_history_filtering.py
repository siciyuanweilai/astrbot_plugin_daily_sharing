import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "daily_sharing_context_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
CONFIG_MODULE_NAME = f"{PACKAGE_NAME}.config"
PLATFORM_MODULE_NAME = f"{CORE_PACKAGE_NAME}.platform"
CONTEXT_MODULE_NAME = f"{CORE_PACKAGE_NAME}.context"


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _ConversationManager:
    def __init__(self, history=None):
        self.history = history or []
        self.added_pairs = []

    async def get_curr_conversation_id(self, unified_msg_origin):
        return "cid"

    async def get_conversation(self, unified_msg_origin, conversation_id):
        return types.SimpleNamespace(history=json.dumps(self.history, ensure_ascii=False))

    async def new_conversation(self, unified_msg_origin):
        return "cid"

    async def add_message_pair(self, cid, user_message, assistant_message):
        self.added_pairs.append((cid, user_message, assistant_message))


class _PlatformHistoryManager:
    def __init__(self, records_by_user=None):
        self.records_by_user = records_by_user or {}
        self.calls = []

    async def get(self, platform_id, user_id, page=1, page_size=200):
        self.calls.append(
            {
                "platform_id": platform_id,
                "user_id": user_id,
                "page": page,
                "page_size": page_size,
            }
        )
        return list(self.records_by_user.get((platform_id, user_id), []))


def _clear_modules():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name.startswith("astrbot"):
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


def _load_context_module():
    _clear_modules()

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    core_package = types.ModuleType(CORE_PACKAGE_NAME)
    core_package.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_package

    _install_stub_module("astrbot", __path__=[])
    _install_stub_module("astrbot.api", logger=_Logger())

    _load_module(CONFIG_MODULE_NAME, ROOT / "config.py")
    _load_module(PLATFORM_MODULE_NAME, ROOT / "core" / "platform.py")
    return _load_module(CONTEXT_MODULE_NAME, ROOT / "core" / "context.py")


def _service(history=None, context_conf=None, platform_records=None):
    context_module = _load_context_module()
    context = types.SimpleNamespace(
        conversation_manager=_ConversationManager(history),
        message_history_manager=_PlatformHistoryManager(platform_records),
        platform_manager=None,
    )
    config = {"context_conf": context_conf or {}}
    return context_module, context_module.ContextService(context, config)


class ContextHistoryFilteringTests(unittest.IsolatedAsyncioTestCase):
    def test_internal_marker_and_memory_prompt_are_chinese_without_square_brackets(self):
        context_module, _ = _service()

        self.assertEqual(context_module.DAILY_SHARING_INTERNAL_TRIGGER, "愿此见闻悄然为我启封")
        self.assertEqual(context_module.DAILY_SHARING_MEMORY_PROMPT, "每日分享记录")
        self.assertNotIn("[", context_module.DAILY_SHARING_INTERNAL_TRIGGER)
        self.assertNotIn("]", context_module.DAILY_SHARING_INTERNAL_TRIGGER)
        self.assertNotIn("[", context_module.DAILY_SHARING_MEMORY_PROMPT)
        self.assertNotIn("]", context_module.DAILY_SHARING_MEMORY_PROMPT)

    def test_old_virtual_prompt_is_not_special_cased(self):
        _, service = _service()

        msg = service._normalize_conversation_history_item(
            {
                "role": "user",
                "content": "请发送今天的每日分享内容。",
            }
        )

        self.assertEqual(msg["role"], "user")
        self.assertEqual(msg["content"], "请发送今天的每日分享内容。")
        self.assertEqual(msg["source"], "chat")

    def test_non_internal_user_text_is_plain_chat(self):
        _, service = _service()

        msg = service._normalize_conversation_history_item(
            {
                "role": "user",
                "content": "普通用户历史",
            }
        )

        self.assertEqual(msg["role"], "user")
        self.assertEqual(msg["content"], "普通用户历史")
        self.assertEqual(msg["source"], "chat")

    def test_removed_active_share_prefix_is_not_special_cased(self):
        _, service = _service()

        msg = service._normalize_conversation_history_item(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "愿此见闻温柔你的日常\n今天适合散步。",
                    }
                ],
            }
        )

        self.assertEqual(msg["source"], "chat")
        self.assertEqual(msg["content"], "愿此见闻温柔你的日常\n今天适合散步。")
        self.assertEqual(msg["timestamp"], "")

    def test_plain_assistant_text_is_not_daily_share_without_internal_trigger(self):
        _, service = _service()

        msg = service._normalize_conversation_history_item(
            {
                "role": "assistant",
                "content": "普通助手历史\n旧分享文本",
            }
        )

        self.assertEqual(msg["source"], "chat")
        self.assertEqual(msg["content"], "普通助手历史\n旧分享文本")

    async def test_new_share_pair_marks_following_assistant_as_daily_share(self):
        context_module, service = _service(
            [
                {
                    "role": "user",
                    "content": "愿此见闻悄然为我启封",
                },
                {"role": "assistant", "content": "新分享内容"},
            ],
            {"deep_history_max_count": 1},
        )

        data = await service._get_conversation_history_data(
            "aiocqhttp:GroupMessage:123",
            is_group=True,
        )

        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["source"], context_module.DAILY_SHARING_SOURCE)
        self.assertEqual(data["messages"][0]["content"], "新分享内容")
        self.assertEqual(data["group_info"], {})

    def test_private_prompt_weakens_daily_share_as_background(self):
        context_module, service = _service()
        config_module = sys.modules[CONFIG_MODULE_NAME]

        prompt = service._format_private_chat_for_prompt(
            [
                {
                    "role": "assistant",
                    "content": "今天适合散步。",
                    "source": context_module.DAILY_SHARING_SOURCE,
                },
                {"role": "user", "content": "确实不错", "source": "chat"},
            ],
            config_module.SharingType.MOOD,
        )

        self.assertIn("背景: 你之前主动分享过：今天适合散步。", prompt)
        self.assertNotIn("你: 今天适合散步。", prompt)

    async def test_record_bot_reply_writes_internal_trigger_and_plain_assistant_content(self):
        context_module, service = _service()
        manager = service.context.conversation_manager

        await service.record_bot_reply_to_history(
            "aiocqhttp:FriendMessage:123",
            "$$happy$$今天适合散步。",
            image_desc="晴天小路",
        )

        self.assertEqual(len(manager.added_pairs), 1)
        _, user_message, assistant_message = manager.added_pairs[0]
        self.assertEqual(
            user_message["content"][0]["text"],
            context_module.DAILY_SHARING_INTERNAL_TRIGGER,
        )
        assistant_text = assistant_message["content"][0]["text"]
        self.assertEqual(assistant_text, "今天适合散步。\n\n[发送了一张配图: 晴天小路]")
        self.assertNotIn("$$happy$$", assistant_text)

    async def test_full_non_onebot_umo_does_not_fall_back_to_numeric_onebot(self):
        _, service = _service(
            [{"role": "user", "content": "这是一条普通历史。"}],
            {"private_history_count": 5},
        )

        def fail_if_onebot_is_used(*args, **kwargs):
            raise AssertionError("full non-OneBot UMO should not use OneBot")

        service._get_onebot_bot = fail_if_onebot_is_used

        data = await service.get_history_data("other:FriendMessage:123", is_group=False)

        self.assertEqual(data["messages"][0]["content"], "这是一条普通历史。")

    async def test_platform_history_tries_raw_webchat_session_id(self):
        record = types.SimpleNamespace(
            content={"type": "user", "message": [{"type": "plain", "text": "刚刚聊到散步。"}]},
            sender_id="alice",
            sender_name="Alice",
            created_at=None,
        )
        _, service = _service(
            platform_records={("webchat", "session-1"): [record]},
            context_conf={"private_history_count": 5},
        )

        data = await service.get_history_data(
            "webchat:FriendMessage:webchat!alice!session-1",
            is_group=False,
        )

        manager = service.context.message_history_manager
        self.assertEqual([call["user_id"] for call in manager.calls], ["webchat!alice!session-1", "session-1"])
        self.assertEqual(data["messages"][0]["content"], "刚刚聊到散步。")
        self.assertEqual(data["messages"][0]["role"], "user")

    async def test_platform_history_daily_share_is_marked_from_conversation_history(self):
        context_module = _load_context_module()
        service = context_module.ContextService(
            types.SimpleNamespace(
                conversation_manager=_ConversationManager(
                    [
                        {"role": "user", "content": context_module.DAILY_SHARING_INTERNAL_TRIGGER},
                        {
                            "role": "assistant",
                            "content": "今天适合散步。\n\n[发送了一张配图: 晴天小路]",
                        },
                    ]
                ),
                message_history_manager=_PlatformHistoryManager(
                    {
                        (
                            "webchat",
                            "session-2",
                        ): [
                            types.SimpleNamespace(
                                content={"type": "bot", "message": [{"type": "plain", "text": "今天适合散步。"}]},
                                sender_id="bot",
                                sender_name="bot",
                                created_at=None,
                            )
                        ]
                    }
                ),
                platform_manager=None,
            ),
            {"context_conf": {"private_history_count": 5}},
        )

        data = await service.get_history_data(
            "webchat:FriendMessage:webchat!alice!session-2",
            is_group=False,
        )

        self.assertEqual(data["messages"][0]["source"], context_module.DAILY_SHARING_SOURCE)
        prompt = service._format_private_chat_for_prompt(data["messages"], sys.modules[CONFIG_MODULE_NAME].SharingType.MOOD)
        self.assertIn("背景: 你之前主动分享过：今天适合散步。", prompt)
        self.assertNotIn("你: 今天适合散步。", prompt)


if __name__ == "__main__":
    unittest.main()
