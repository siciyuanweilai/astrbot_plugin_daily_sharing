import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from astrbot.api import logger

_DYNAMIC_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_DYNAMIC_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")
_HISTORY_SELECT_COLUMNS = """
    id, created_at, target_id, sharing_type, content, success,
    error_reason, media_type, media_url, media_path, source_type
"""
_MEDIA_REF_SQL = "LOWER(COALESCE(media_path, '') || ' ' || COALESCE(media_url, ''))"
_HAS_MEDIA_SQL = "(COALESCE(media_path, '') <> '' OR COALESCE(media_url, '') <> '')"
_BRIEFING_HISTORY_SQL = "COALESCE(sharing_type, '') = 'briefing'"

__all__ = [name for name in globals() if not name.startswith("__")]
