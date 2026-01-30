import sqlite3
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from astrbot.api import logger

class DatabaseManager:
    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "data.db"
        self._init_db()

    def _get_conn(self):
        # 允许在多线程中使用连接
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 1. 发送历史记录表 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT,
                sharing_type TEXT,
                content TEXT,
                success INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 2. 话题去重表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS topic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT,
                category TEXT,
                content_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 3. 插件状态表 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS plugin_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()

    # ========== 异步执行辅助方法 ==========
    
    async def _execute(self, func, *args, **kwargs):
        """在线程池中运行同步的 SQL 操作，防止阻塞 Bot"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)

    # ========== 状态管理 ==========

    def _sync_get_state(self, key: str, default: Any = None) -> Any:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM plugin_state WHERE key = ?', (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            try:
                return json.loads(row[0])
            except:
                return row[0]
        return default

    def _sync_set_state(self, key: str, value: Any):
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 获取本地当前时间
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
        """更新字典类型的状态"""
        current = await self.get_state(key, {})
        if not isinstance(current, dict): current = {}
        current.update(updates)
        await self.set_state(key, current)

    # ========== 历史记录 ==========

    def _sync_add_history(self, target_id, sharing_type, content, success):
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute('''
            INSERT INTO sent_history (target_id, sharing_type, content, success, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (str(target_id), str(sharing_type), str(content), 1 if success else 0, now_str))
        conn.commit()
        conn.close()

    async def add_sent_history(self, target_id: str, sharing_type: str, content: str, success: bool = True):
        await self._execute(self._sync_add_history, target_id, sharing_type, content, success)

    def _sync_get_recent_history(self, limit: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT created_at, sharing_type, content 
            FROM sent_history 
            ORDER BY id DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [{"timestamp": r[0], "type": r[1], "content": r[2]} for r in rows]

    async def get_recent_history(self, limit: int = 5):
        return await self._execute(self._sync_get_recent_history, limit)

    # ========== 话题去重 ==========

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
        """记录使用过的话题"""
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
        """获取某人最近使用过的话题列表"""
        return await self._execute(self._sync_get_used_topics, target_id, category, days_limit)

    # ========== 清理过期数据 ==========

    def _sync_clean_expired_data(self, days_limit: int):
        """同步清理方法"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cutoff_date = (datetime.now() - timedelta(days=days_limit)).strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. 清理去重记录表
        cursor.execute('DELETE FROM topic_history WHERE created_at < ?', (cutoff_date,))
        deleted_topic = cursor.rowcount
        
        # 2. 清理发送日志表
        cursor.execute('DELETE FROM sent_history WHERE created_at < ?', (cutoff_date,))
        deleted_history = cursor.rowcount
        
        if deleted_topic > 0 or deleted_history > 0:
            logger.info(f"[DailySharing] 自动清理历史记录: 删除了 {deleted_topic} 条话题, {deleted_history} 条日志 (早于 {days_limit} 天)")
            
        conn.commit()
        conn.close()

    async def clean_expired_data(self, days_limit: int):
        """清理早于 days_limit 天的数据"""
        await self._execute(self._sync_clean_expired_data, days_limit)
