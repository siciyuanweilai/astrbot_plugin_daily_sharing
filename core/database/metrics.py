from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .schema import (
    _BRIEFING_HISTORY_SQL,
    _DYNAMIC_IMAGE_EXTS,
    _DYNAMIC_VIDEO_EXTS,
    _HAS_MEDIA_SQL,
    _HISTORY_SELECT_COLUMNS,
    _MEDIA_REF_SQL,
)


class DatabaseDashboardMixin:
    """仪表盘动态、媒体和目标统计。"""

    @staticmethod
    def _days_cutoff(days: Optional[int]) -> str:
        try:
            days_int = int(days or 0)
        except Exception:
            days_int = 0
        if days_int <= 0:
            return ""
        return (datetime.now() - timedelta(days=days_int)).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _media_kind_clause(media_kind: str) -> tuple:
        kind = str(media_kind or "").strip().lower()
        if kind == "text":
            return (
                """COALESCE(media_path, '') = ''
                AND COALESCE(media_url, '') = ''
                AND LOWER(COALESCE(media_type, '')) NOT LIKE '%image%'
                AND LOWER(COALESCE(media_type, '')) NOT LIKE '%video%'""",
                [],
            )
        if kind not in {"image", "video"}:
            return "", []

        exts = _DYNAMIC_IMAGE_EXTS if kind == "image" else _DYNAMIC_VIDEO_EXTS
        ext_checks = " OR ".join([f"{_MEDIA_REF_SQL} LIKE ?" for _ in exts])
        return (
            f"(LOWER(COALESCE(media_type, '')) LIKE ? OR {ext_checks})",
            [f"%{kind}%", *[f"%{ext}%" for ext in exts]],
        )

    def _dynamic_filter_clause(
        self,
        days: int = 0,
        media_kind: str = "",
        sharing_type: str = "",
    ) -> tuple:
        clauses = []
        params = []

        cutoff = self._days_cutoff(days)
        if cutoff:
            clauses.append("created_at >= ?")
            params.append(cutoff)

        raw_type = str(sharing_type or "").strip()
        normalized_type = raw_type.lower()
        if raw_type and normalized_type != "all":
            clauses.append("sharing_type = ?")
            params.append(raw_type)

        media_clause, media_params = self._media_kind_clause(media_kind)
        if media_clause:
            clauses.append(media_clause)
            params.extend(media_params)

        clause = "".join(f"\n              AND {item}" for item in clauses)
        return clause, params

    def _sync_get_recent_media(self, limit: int, days: int = 0) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        params = []
        days_clause = ""
        cutoff = self._days_cutoff(days)
        if cutoff:
            days_clause = " AND created_at >= ?"
            params.append(cutoff)
        params.append(limit)
        cursor.execute(f'''
            SELECT {_HISTORY_SELECT_COLUMNS}
            FROM sent_history
            WHERE success = 1
              AND {_HAS_MEDIA_SQL}
              {days_clause}
            ORDER BY id DESC LIMIT ?
        ''', tuple(params))
        rows = cursor.fetchall()
        conn.close()
        return [self._history_item_from_row(r) for r in rows]

    async def get_recent_media(self, limit: int = 12, days: int = 0):
        return await self._execute(self._sync_get_recent_media, limit, days)

    def _sync_get_recent_dynamics(
        self,
        limit: int,
        days: int = 0,
        media_kind: str = "",
        sharing_type: str = "",
    ) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        filter_clause, params = self._dynamic_filter_clause(days, media_kind, sharing_type)
        params.append(limit)
        cursor.execute(f'''
            SELECT {_HISTORY_SELECT_COLUMNS}
            FROM sent_history
            WHERE success = 1
              {filter_clause}
            ORDER BY id DESC LIMIT ?
        ''', tuple(params))
        rows = cursor.fetchall()
        conn.close()
        return [self._history_item_from_row(r) for r in rows]

    async def get_recent_dynamics(
        self,
        limit: int = 12,
        days: int = 0,
        media_kind: str = "",
        sharing_type: str = "",
    ):
        return await self._execute(
            self._sync_get_recent_dynamics,
            limit,
            days,
            media_kind,
            sharing_type,
        )

    def _sync_get_dashboard_dynamic_summary(self, days: int = 0) -> Dict:
        conn = self._get_conn()
        cursor = conn.cursor()
        params = []
        days_clause = ""
        cutoff = self._days_cutoff(days)
        text_clause, text_params = self._media_kind_clause("text")
        image_clause, image_params = self._media_kind_clause("image")
        video_clause, video_params = self._media_kind_clause("video")
        params.extend(text_params)
        params.extend(image_params)
        params.extend(video_params)
        if cutoff:
            days_clause = " AND created_at >= ?"
            params.append(cutoff)
        cursor.execute(f'''
            SELECT
                COUNT(*) AS dynamic_count,
                SUM(
                    CASE
                        WHEN {_HAS_MEDIA_SQL}
                        THEN 1 ELSE 0
                    END
                ) AS media_count,
                SUM(CASE WHEN {text_clause} THEN 1 ELSE 0 END) AS text_count,
                SUM(CASE WHEN {image_clause} THEN 1 ELSE 0 END) AS image_count,
                SUM(CASE WHEN {video_clause} THEN 1 ELSE 0 END) AS video_count
            FROM sent_history
            WHERE success = 1
              {days_clause}
        ''', tuple(params))
        row = cursor.fetchone() or (0, 0, 0, 0, 0)
        conn.close()
        dynamic, media, text, image, video = row
        return {
            "dynamic": int(dynamic or 0),
            "media": int(media or 0),
            "text": int(text or 0),
            "image": int(image or 0),
            "video": int(video or 0),
        }

    async def get_dashboard_dynamic_summary(self, days: int = 0):
        return await self._execute(self._sync_get_dashboard_dynamic_summary, days)

    def _sync_get_history_summary(self) -> Dict:
        conn = self._get_conn()
        cursor = conn.cursor()
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(f'''
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN success = 1 AND created_at >= ? THEN 1 ELSE 0 END) AS today_count,
                SUM(
                    CASE
                        WHEN success = 1
                         AND {_HAS_MEDIA_SQL}
                        THEN 1 ELSE 0
                    END
                ) AS media_count
            FROM sent_history
        ''', (today_start,))
        row = cursor.fetchone() or (0, 0, 0, 0, 0)
        conn.close()
        total, success, failed, today, media = row
        return {
            "total": int(total or 0),
            "success": int(success or 0),
            "failed": int(failed or 0),
            "today": int(today or 0),
            "dynamic": int(success or 0),
            "media": int(media or 0),
        }

    async def get_history_summary(self):
        return await self._execute(self._sync_get_history_summary)

    def _target_stats_scope_clause(self, briefing: Optional[bool]) -> str:
        if briefing is True:
            return f"WHERE {_BRIEFING_HISTORY_SQL}"
        if briefing is False:
            return f"WHERE NOT {_BRIEFING_HISTORY_SQL}"
        return ""

    def _target_stats_type_scope_clause(self, briefing: Optional[bool]) -> str:
        if briefing is True:
            return f"AND {_BRIEFING_HISTORY_SQL}"
        if briefing is False:
            return f"AND NOT {_BRIEFING_HISTORY_SQL}"
        return ""

    def _sync_get_target_stats(self, days: int = 30, briefing: Optional[bool] = None) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d %H:%M:%S")
        scope_clause = self._target_stats_scope_clause(briefing)
        type_scope_clause = self._target_stats_type_scope_clause(briefing)

        cursor.execute(f'''
            SELECT
                target_id,
                COUNT(*) AS total_count,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_count,
                MAX(created_at) AS last_at,
                MAX(CASE WHEN success = 1 THEN created_at ELSE NULL END) AS last_success_at,
                SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS recent_count
            FROM sent_history
            {scope_clause}
            GROUP BY target_id
            ORDER BY last_at DESC
        ''', (cutoff,))
        rows = cursor.fetchall()

        cursor.execute(f'''
            SELECT target_id, sharing_type, COUNT(*) AS type_count
            FROM sent_history
            WHERE success = 1
              {type_scope_clause}
            GROUP BY target_id, sharing_type
        ''')
        type_rows = cursor.fetchall()

        type_counts = {}
        for target_id, sharing_type, count in type_rows:
            type_counts.setdefault(target_id, {})[sharing_type] = count

        result = []
        safe_days = max(1, int(days))
        for row in rows:
            target_id, total, success, failed, last_at, last_success_at, recent_count = row
            total = int(total or 0)
            success = int(success or 0)
            failed = int(failed or 0)
            recent_count = int(recent_count or 0)
            result.append({
                "target_id": target_id,
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": round(success / total, 3) if total else 0,
                "last_at": last_at or "",
                "last_success_at": last_success_at or "",
                "recent_count": recent_count,
                "frequency_per_day": round(recent_count / safe_days, 2),
                "types": type_counts.get(target_id, {}),
            })

        conn.close()
        return result

    async def get_target_stats(self, days: int = 30, briefing: Optional[bool] = None):
        return await self._execute(self._sync_get_target_stats, days, briefing)
