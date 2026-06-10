import json
from datetime import datetime
from typing import Any, Dict


class DatabaseStateMixin:
    """插件状态读写。"""

    def _sync_get_state(self, key: str, default: Any = None) -> Any:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM plugin_state WHERE key = ?', (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return row[0]
        return default

    def _sync_set_state(self, key: str, value: Any):
        conn = self._get_conn()
        cursor = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        json_val = json.dumps(value, ensure_ascii=False)

        cursor.execute('''
            INSERT INTO plugin_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        ''', (key, json_val, now_str))
        conn.commit()
        conn.close()

    async def get_state(self, key: str = "global", default: Any = None):
        return await self._execute(self._sync_get_state, key, default)

    async def set_state(self, key: str, value: Any):
        return await self._execute(self._sync_set_state, key, value)

    async def update_state_dict(self, key: str, updates: Dict):
        current = await self.get_state(key, {})
        if not isinstance(current, dict):
            current = {}
        current.update(updates)
        await self.set_state(key, current)
