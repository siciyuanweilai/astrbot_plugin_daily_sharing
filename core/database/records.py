from datetime import datetime
from typing import Dict, List, Optional

from .schema import _HISTORY_SELECT_COLUMNS


class DatabaseHistoryMixin:
    """分享历史和失败记录。"""

    def _sync_add_history(
        self,
        target_id,
        sharing_type,
        content,
        success,
        error_reason="",
        media_type="",
        media_url="",
        media_path="",
        source_type="",
    ):
        conn = self._get_conn()
        cursor = conn.cursor()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT INTO sent_history (
                target_id, sharing_type, content, success, created_at,
                error_reason, media_type, media_url, media_path, source_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(target_id),
            str(sharing_type),
            str(content),
            1 if success else 0,
            now_str,
            str(error_reason or ""),
            str(media_type or ""),
            str(media_url or ""),
            str(media_path or ""),
            str(source_type or ""),
        ))
        conn.commit()
        conn.close()

    async def add_sent_history(
        self,
        target_id: str,
        sharing_type: str,
        content: str,
        success: bool = True,
        *,
        error_reason: str = "",
        media_type: str = "",
        media_url: str = "",
        media_path: str = "",
        source_type: str = "",
    ):
        await self._execute(
            self._sync_add_history,
            target_id,
            sharing_type,
            content,
            success,
            error_reason,
            media_type,
            media_url,
            media_path,
            source_type,
        )

    def _history_item_from_row(self, row) -> Dict:
        return {
            "id": row[0],
            "timestamp": row[1],
            "target_id": row[2],
            "type": row[3],
            "content": row[4],
            "success": bool(row[5]),
            "error_reason": row[6] or "",
            "media_type": row[7] or "",
            "media_url": row[8] or "",
            "media_path": row[9] or "",
            "source_type": row[10] or "",
        }

    def _sync_get_recent_history(self, limit: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT {_HISTORY_SELECT_COLUMNS}
            FROM sent_history
            ORDER BY id DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [self._history_item_from_row(r) for r in rows]

    async def get_recent_history(self, limit: int = 5):
        return await self._execute(self._sync_get_recent_history, limit)

    def _sync_get_recent_history_by_target(self, target_id: str, limit: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT {_HISTORY_SELECT_COLUMNS}
            FROM sent_history
            WHERE target_id = ? AND success = 1
            ORDER BY id DESC LIMIT ?
        ''', (str(target_id), limit))
        rows = cursor.fetchall()
        conn.close()
        return [self._history_item_from_row(r) for r in rows]

    async def get_recent_history_by_target(self, target_id: str, limit: int = 3):
        return await self._execute(self._sync_get_recent_history_by_target, target_id, limit)

    def _sync_get_history_by_id(self, history_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT {_HISTORY_SELECT_COLUMNS}
            FROM sent_history
            WHERE id = ?
        ''', (int(history_id),))
        row = cursor.fetchone()
        conn.close()
        return self._history_item_from_row(row) if row else None

    async def get_history_by_id(self, history_id: int):
        return await self._execute(self._sync_get_history_by_id, history_id)

    def _sync_get_recent_failures(self, limit: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT {_HISTORY_SELECT_COLUMNS}
            FROM sent_history
            WHERE success = 0
            ORDER BY id DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [self._history_item_from_row(r) for r in rows]

    async def get_recent_failures(self, limit: int = 10):
        return await self._execute(self._sync_get_recent_failures, limit)

    def _sync_clear_failures(self) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sent_history WHERE success = 0")
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return int(deleted or 0)

    async def clear_failures(self) -> int:
        return await self._execute(self._sync_clear_failures)
