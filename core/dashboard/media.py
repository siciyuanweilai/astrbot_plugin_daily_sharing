import base64
import mimetypes
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from ..config import SharingType
from .common import (
    _PAGE_IMAGE_EXTS,
    _PAGE_INLINE_PREVIEW_MAX_BYTES,
    _PAGE_THUMBNAIL_MAX_SIDE,
    _PAGE_VIDEO_EXTS,
    _PAGE_VIEW_IMAGE_MAX_SIDE,
)


class DashboardMediaMixin:
    """仪表盘媒体能力。"""

    def _page_media_kind_from_ref(self, ref: str) -> str:
        ref = str(ref or "").strip()
        if not ref:
            return ""
        lower = ref.lower()
        if lower.startswith("data:image/"):
            return "image"
        if lower.startswith("data:video/"):
            return "video"
        clean_ref = ref.split("?", 1)[0].split("#", 1)[0]
        mime = mimetypes.guess_type(clean_ref)[0] or ""
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("video/"):
            return "video"
        suffix = Path(clean_ref).suffix.lower()
        if suffix in _PAGE_IMAGE_EXTS:
            return "image"
        if suffix in _PAGE_VIDEO_EXTS:
            return "video"
        return ""

    def _page_media_kind(self, item: dict) -> str:
        raw_type = str(item.get("media_type") or "").strip().lower()
        if "image" in raw_type:
            return "image"
        if "video" in raw_type:
            return "video"
        return (
            self._page_media_kind_from_ref(item.get("media_url", ""))
            or self._page_media_kind_from_ref(item.get("media_path", ""))
        )

    def _page_resolve_media_path(self, media_path: str) -> Optional[Path]:
        media_path = str(media_path or "").strip()
        if not media_path:
            return None

        candidates = [Path(media_path)]
        raw_path = Path(media_path)
        if not raw_path.is_absolute():
            candidates.extend(
                [
                    self.data_dir / raw_path,
                    self.data_dir / "Temp" / raw_path,
                    Path.cwd() / raw_path,
                ]
            )

        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=False)
                if resolved.is_file():
                    return resolved
            except Exception:
                continue
        return None

    @staticmethod
    def _page_media_file_version(path: Path, stat_result=None) -> str:
        stat_result = stat_result or path.stat()
        return f"{int(stat_result.st_mtime_ns)}-{int(stat_result.st_size)}"

    def _page_image_data_url(self, path: Path, max_side: int = _PAGE_THUMBNAIL_MAX_SIDE, quality: int = 86) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        stat_result = path.stat()
        size = stat_result.st_size

        try:
            from PIL import Image as PILImage
            from PIL import ImageOps
            from io import BytesIO

            with PILImage.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail(
                    (max_side, max_side),
                    PILImage.Resampling.LANCZOS,
                )
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    background = PILImage.new("RGB", image.size, (255, 255, 255))
                    background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
                    image = background
                else:
                    image = image.convert("RGB")
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                encoded = base64.b64encode(output.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception as exc:
            logger.debug(f"[每日分享] 生成媒体缩略图失败: {path}, {exc}")
            if size <= _PAGE_INLINE_PREVIEW_MAX_BYTES * 2:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{encoded}"
            return ""

    def _page_view_image_payload(self, item: dict, history_id: int) -> dict:
        media_url = str(item.get("media_url") or "").strip()
        path = self._page_resolve_media_path(item.get("media_path", ""))
        if not path and media_url:
            return {"delivery": "url", "view_url": media_url}
        if not path:
            raise RuntimeError("查看图文件不存在")
        return {
            "delivery": "data",
            "view_url": self._page_image_data_url(path, _PAGE_VIEW_IMAGE_MAX_SIDE, 90),
            "version": self._page_media_file_version(path),
        }

    def _page_media_preview_url(self, item: dict) -> str:
        media_url = str(item.get("media_url") or "").strip()
        kind = self._page_media_kind(item)
        if kind != "image":
            return ""

        path = self._page_resolve_media_path(item.get("media_path", ""))
        if not path:
            return media_url

        try:
            return self._page_image_data_url(path)
        except Exception as exc:
            logger.debug(f"[每日分享] 构建媒体预览失败: {path}, {exc}")
            return ""

    async def _page_prepare_media_items(self, items: list) -> list:
        prepared = []
        for item in await self._page_prepare_history_items(items):
            item = dict(item)
            item["media_type"] = self._page_media_kind(item) or str(item.get("media_type") or "")
            item["preview_url"] = self._page_media_preview_url(item)
            prepared.append(item)
        return prepared

    def _page_dashboard_dynamic_days(self) -> int:
        try:
            days = int(self.basic_conf.get("dashboard_dynamic_days", 60))
        except Exception:
            days = 60
        return max(0, min(days, 3650))

    @staticmethod
    def _page_dynamic_media_kind(value: str) -> str:
        raw = str(value or "all").strip().lower()
        return raw if raw in {"all", "text", "image", "video"} else "all"

    @staticmethod
    def _page_dynamic_sharing_type(value: str) -> str:
        raw = str(value or "all").strip().lower()
        allowed = {"all", "auto", "briefing", *(item.value for item in SharingType)}
        return raw if raw in allowed else "all"

    async def _page_media_page(
        self,
        limit: int,
        days: int = None,
        media_kind: str = "all",
        sharing_type: str = "all",
    ) -> dict:
        limit = min(max(int(limit), 1), 100)
        if days is None:
            days = self._page_dashboard_dynamic_days()
        media_kind = self._page_dynamic_media_kind(media_kind)
        sharing_type = self._page_dynamic_sharing_type(sharing_type)
        rows = await self.db.get_recent_dynamics(
            limit=limit + 1,
            days=days,
            media_kind=media_kind,
            sharing_type=sharing_type,
        )
        return {
            "items": await self._page_prepare_media_items(rows[:limit]),
            "limit": limit,
            "has_more": len(rows) > limit,
            "dynamic_days": days,
            "media_kind": media_kind,
            "sharing_type": sharing_type,
        }

