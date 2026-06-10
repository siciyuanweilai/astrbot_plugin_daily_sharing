from datetime import datetime, timedelta
from typing import List

from astrbot.api import logger


class DatabaseTopicMixin:
    """话题去重和过期清理。"""

    def _sync_record_topic(self, target_id, category, content_key):
        conn = self._get_conn()
        cursor = conn.cursor()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT INTO topic_history (target_id, category, content_key, created_at)
            VALUES (?, ?, ?, ?)
        ''', (str(target_id), str(category), str(content_key), now_str))
        conn.commit()
        conn.close()

    async def record_topic(self, target_id: str, category: str, content_key: str):
        await self._execute(self._sync_record_topic, target_id, category, content_key)

    def _sync_get_used_topics(self, target_id, category, days_limit=60) -> List[str]:
        conn = self._get_conn()
        cursor = conn.cursor()

        date_limit = (datetime.now() - timedelta(days=days_limit)).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            SELECT content_key FROM topic_history
            WHERE target_id = ? AND category = ? AND created_at > ?
        ''', (str(target_id), str(category), date_limit))

        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    async def get_used_topics(self, target_id: str, category: str, days_limit: int = 60) -> List[str]:
        return await self._execute(self._sync_get_used_topics, target_id, category, days_limit)

    def _sync_clean_expired_data(self, days_limit: int):
        conn = self._get_conn()
        cursor = conn.cursor()

        cutoff_date = (datetime.now() - timedelta(days=days_limit)).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('DELETE FROM topic_history WHERE created_at < ?', (cutoff_date,))
        deleted_topic = cursor.rowcount

        if deleted_topic > 0:
            logger.debug(f"[每日分享] 自动清理话题去重记录: 删除了 {deleted_topic} 条记录 (早于 {days_limit} 天)")

        conn.commit()
        conn.close()

    async def clean_expired_data(self, days_limit: int):
        await self._execute(self._sync_clean_expired_data, days_limit)
