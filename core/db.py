import asyncio
import sqlite3
from pathlib import Path

from .database.metrics import DatabaseDashboardMixin
from .database.records import DatabaseHistoryMixin
from .database.state import DatabaseStateMixin
from .database.topics import DatabaseTopicMixin


class DatabaseManager(
    DatabaseStateMixin,
    DatabaseHistoryMixin,
    DatabaseDashboardMixin,
    DatabaseTopicMixin,
):
    """聚合数据库连接、建表和各类数据访问能力。"""

    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "data.db"
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT,
                sharing_type TEXT,
                content TEXT,
                success INTEGER,
                error_reason TEXT,
                media_type TEXT,
                media_url TEXT,
                media_path TEXT,
                source_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for column in ("error_reason", "media_type", "media_url", "media_path", "source_type"):
            self._ensure_column(cursor, "sent_history", column, "TEXT")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT,
                category TEXT,
                content_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS plugin_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        conn.close()

    def _ensure_column(self, cursor, table: str, column: str, definition: str):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {str(row[1]) for row in cursor.fetchall()}
        if column not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def _execute(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)
