import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "core" / "db.py"
PACKAGE_NAME = "dashboard_db_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
DB_MODULE_NAME = f"{CORE_PACKAGE_NAME}.db"


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def _install_stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_db_module():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name in {"astrbot", "astrbot.api"}:
            sys.modules.pop(name, None)
    _install_stub_module("astrbot")
    _install_stub_module("astrbot.api", logger=_Logger())

    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = package

    core_package = types.ModuleType(CORE_PACKAGE_NAME)
    core_package.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_package

    spec = importlib.util.spec_from_file_location(DB_MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DashboardDbMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_sent_history_table_gets_new_metadata_columns(self):
        mod = _load_db_module()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = sqlite3.connect(data_dir / "data.db")
            conn.execute(
                """
                CREATE TABLE sent_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT,
                    sharing_type TEXT,
                    content TEXT,
                    success INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                INSERT INTO sent_history (
                    target_id, sharing_type, content, success, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("group-legacy", "mood", "legacy row", 1, "2026-06-10 19:00:00"),
            )
            conn.commit()
            conn.close()

            db = mod.DatabaseManager(data_dir)

            recent = await db.get_recent_history(limit=5)
            dynamics = await db.get_recent_dynamics(limit=5)
            await db.add_sent_history(
                "group-legacy",
                "news",
                "new failed row",
                False,
                error_reason="send failed",
                media_type="image",
                media_path="/tmp/share.png",
            )
            failures = await db.get_recent_failures(limit=5)

            self.assertEqual(recent[0]["content"], "legacy row")
            self.assertEqual(recent[0]["error_reason"], "")
            self.assertEqual(recent[0]["media_type"], "")
            self.assertEqual(dynamics[0]["content"], "legacy row")
            self.assertEqual(failures[0]["error_reason"], "send failed")
            self.assertEqual(failures[0]["media_path"], "/tmp/share.png")

    async def test_history_metadata_failures_media_and_stats(self):
        mod = _load_db_module()
        with tempfile.TemporaryDirectory() as tmp:
            db = mod.DatabaseManager(Path(tmp))

            await db.add_sent_history(
                "group-1",
                "news",
                "sent with image",
                True,
                media_type="image",
                media_path="D:/tmp/news.png",
            )
            await db.add_sent_history(
                "group-1",
                "mood",
                "text only dynamic",
                True,
            )
            await db.add_sent_history(
                "group-1",
                "news",
                "failed",
                False,
                error_reason="upload failed",
            )

            recent = await db.get_recent_history(limit=5)
            failures = await db.get_recent_failures(limit=5)
            media = await db.get_recent_media(limit=5)
            dynamics = await db.get_recent_dynamics(limit=5)
            stats = await db.get_target_stats(days=30)
            summary = await db.get_history_summary()

            self.assertEqual(recent[0]["error_reason"], "upload failed")
            self.assertEqual(failures[0]["target_id"], "group-1")
            self.assertEqual(failures[0]["error_reason"], "upload failed")
            self.assertEqual(media[0]["media_type"], "image")
            self.assertEqual(media[0]["media_path"], "D:/tmp/news.png")
            self.assertEqual([item["content"] for item in dynamics], ["text only dynamic", "sent with image"])
            self.assertEqual(stats[0]["target_id"], "group-1")
            self.assertEqual(stats[0]["total"], 3)
            self.assertEqual(stats[0]["success"], 2)
            self.assertEqual(stats[0]["failed"], 1)
            self.assertEqual(summary["total"], 3)
            self.assertEqual(summary["success"], 2)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["today"], 2)
            self.assertEqual(summary["dynamic"], 2)
            self.assertEqual(summary["media"], 1)

            deleted = await db.clear_failures()
            recent_after_clear = await db.get_recent_history(limit=5)
            failures_after_clear = await db.get_recent_failures(limit=5)
            summary_after_clear = await db.get_history_summary()

            self.assertEqual(deleted, 1)
            self.assertEqual(failures_after_clear, [])
            self.assertEqual(len(recent_after_clear), 2)
            self.assertTrue(recent_after_clear[0]["success"])
            self.assertEqual(summary_after_clear["total"], 2)
            self.assertEqual(summary_after_clear["failed"], 0)
            self.assertEqual(summary_after_clear["today"], 2)

    async def test_dashboard_dynamic_days_filters_display_without_deleting_history(self):
        mod = _load_db_module()
        with tempfile.TemporaryDirectory() as tmp:
            db = mod.DatabaseManager(Path(tmp))

            await db.add_sent_history(
                "group-1",
                "mood",
                "new dynamic",
                True,
            )
            await db.add_sent_history(
                "group-1",
                "news",
                "old dynamic with image",
                True,
                media_type="image",
                media_path="D:/tmp/old-news.png",
            )
            await db.record_topic("group-1", "news", "old-topic")

            old_time = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(db.db_path)
            conn.execute("UPDATE sent_history SET created_at = ? WHERE content = ?", (old_time, "old dynamic with image"))
            conn.execute("UPDATE topic_history SET created_at = ? WHERE content_key = ?", (old_time, "old-topic"))
            conn.commit()
            conn.close()

            visible_dynamics = await db.get_recent_dynamics(limit=5, days=1)
            all_dynamics = await db.get_recent_dynamics(limit=5, days=0)
            visible_summary = await db.get_dashboard_dynamic_summary(days=1)

            self.assertEqual([item["content"] for item in visible_dynamics], ["new dynamic"])
            self.assertEqual({item["content"] for item in all_dynamics}, {"new dynamic", "old dynamic with image"})
            self.assertEqual(visible_summary["dynamic"], 1)
            self.assertEqual(visible_summary["text"], 1)
            self.assertEqual(visible_summary["media"], 0)
            self.assertEqual(visible_summary["image"], 0)
            self.assertEqual(visible_summary["video"], 0)

            await db.clean_expired_data(days_limit=1)
            history_after_cleanup = await db.get_recent_history(limit=5)
            used_topics_after_cleanup = await db.get_used_topics("group-1", "news", days_limit=60)

            self.assertEqual({item["content"] for item in history_after_cleanup}, {"new dynamic", "old dynamic with image"})
            self.assertEqual(used_topics_after_cleanup, [])

    async def test_recent_dynamics_can_filter_media_kind_and_sharing_type(self):
        mod = _load_db_module()
        with tempfile.TemporaryDirectory() as tmp:
            db = mod.DatabaseManager(Path(tmp))

            await db.add_sent_history(
                "group-1",
                "mood",
                "text mood",
                True,
            )
            await db.add_sent_history(
                "group-1",
                "news",
                "typed image news",
                True,
                media_type="image",
                media_path="D:/tmp/news.png",
            )
            await db.add_sent_history(
                "group-1",
                "greeting",
                "typed video greeting",
                True,
                media_type="video",
                media_url="https://example.com/hello.mp4",
            )
            await db.add_sent_history(
                "group-1",
                "recommendation",
                "typed extension image",
                True,
                media_type="image",
                media_path="D:/tmp/poster.WEBP",
            )
            await db.add_sent_history(
                "group-1",
                "news",
                "failed image",
                False,
                media_type="image",
                media_path="D:/tmp/failed.png",
            )

            text_items = await db.get_recent_dynamics(limit=10, media_kind="text")
            image_items = await db.get_recent_dynamics(limit=10, media_kind="image")
            video_items = await db.get_recent_dynamics(limit=10, media_kind="video")
            news_images = await db.get_recent_dynamics(
                limit=10,
                media_kind="image",
                sharing_type="news",
            )

            self.assertEqual([item["content"] for item in text_items], ["text mood"])
            self.assertEqual(
                [item["content"] for item in image_items],
                ["typed extension image", "typed image news"],
            )
            self.assertEqual([item["content"] for item in video_items], ["typed video greeting"])
            self.assertEqual([item["content"] for item in news_images], ["typed image news"])

            summary = await db.get_dashboard_dynamic_summary(days=0)
            self.assertEqual(summary["text"], 1)
            self.assertEqual(summary["image"], 2)
            self.assertEqual(summary["video"], 1)

    async def test_target_stats_use_sharing_type_to_separate_briefing(self):
        mod = _load_db_module()
        with tempfile.TemporaryDirectory() as tmp:
            db = mod.DatabaseManager(Path(tmp))

            await db.add_sent_history("group-1", "news", "normal news", True)
            await db.add_sent_history("group-1", "mood", "normal mood", True)
            await db.add_sent_history(
                "group-1",
                "news",
                "normal news failed",
                False,
                error_reason="send failed",
            )
            await db.add_sent_history("group-1", "briefing", "【每天60秒读懂世界】早报", True)
            await db.add_sent_history("group-1", "news", "manual news", True)
            await db.add_sent_history(
                "group-1",
                "briefing",
                "AI 资讯早报发送失败",
                False,
                error_reason="briefing failed",
            )

            regular_stats = await db.get_target_stats(days=30, briefing=False)
            briefing_stats = await db.get_target_stats(days=30, briefing=True)
            merged_stats = await db.get_target_stats(days=30)
            news_dynamics = await db.get_recent_dynamics(limit=10, sharing_type="news")
            briefing_dynamics = await db.get_recent_dynamics(limit=10, sharing_type="briefing")

            regular = next(item for item in regular_stats if item["target_id"] == "group-1")
            briefing = next(item for item in briefing_stats if item["target_id"] == "group-1")
            merged = next(item for item in merged_stats if item["target_id"] == "group-1")

            self.assertEqual(regular["total"], 4)
            self.assertEqual(regular["success"], 3)
            self.assertEqual(regular["failed"], 1)
            self.assertEqual(regular["types"], {"mood": 1, "news": 2})

            self.assertEqual(briefing["total"], 2)
            self.assertEqual(briefing["success"], 1)
            self.assertEqual(briefing["failed"], 1)
            self.assertEqual(briefing["types"], {"briefing": 1})

            self.assertEqual(merged["total"], 6)
            self.assertEqual(merged["success"], 4)
            self.assertEqual(merged["failed"], 2)

            self.assertEqual(
                [item["content"] for item in news_dynamics],
                ["manual news", "normal news"],
            )
            self.assertEqual(
                [item["content"] for item in briefing_dynamics],
                ["【每天60秒读懂世界】早报"],
            )

if __name__ == "__main__":
    unittest.main()
