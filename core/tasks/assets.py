import asyncio
import os
import random
import re
from typing import Optional

import aiofiles
import aiohttp
from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP


class TaskDeliveryAssetsMixin:
    """图片资源命名、下载、QQ 空间读取与临时文件清理。"""

    _NEWS_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    _NEWS_IMAGE_RANDOM_PATTERN = re.compile(r"^([A-Za-z0-9_-]+)_([0-9a-f]{12})(\.[A-Za-z0-9]+)$")

    def _safe_news_image_source_name(self, source_name: str = None) -> str:
        source = str(source_name or "").strip()
        if not source:
            source = "news"

        source = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source)
        source = re.sub(r"_+", "_", source).strip(" ._")
        return source[:80] or "news"

    def _infer_news_image_source_key(self, url: str = None, source_name: str = None) -> str:
        if source_name:
            source_name = str(source_name).strip()
            if source_name in NEWS_SOURCE_MAP:
                return source_name
            for key, info in NEWS_SOURCE_MAP.items():
                if source_name == str(info.get("name", "")).strip():
                    return key
            static_sources = {
                "每天60s读世界": "60s",
                "60s新闻": "60s",
                "AI资讯快报": "ai",
                "AI资讯": "ai",
            }
            if source_name in static_sources:
                return static_sources[source_name]

        try:
            from urllib.parse import urlsplit

            url_parts = urlsplit(str(url or ""))
            url_path = url_parts.path.rstrip("/")
            for key, info in NEWS_SOURCE_MAP.items():
                base_url = info.get("url", "")
                base_parts = urlsplit(base_url)
                if (
                    url_parts.netloc == base_parts.netloc
                    and url_path == base_parts.path.rstrip("/")
                ):
                    return key
        except Exception:
            pass

        return source_name or "news"

    def _news_image_extension_from_url(self, url: str = None) -> str:
        try:
            from urllib.parse import urlsplit

            ext = os.path.splitext(urlsplit(str(url or "")).path)[1].lower()
            if ext in self._NEWS_IMAGE_EXTENSIONS:
                return ext
        except Exception:
            pass
        return ".png"

    def _build_news_image_filename(self, url: str = None, source_name: str = None) -> str:
        source_key = self._infer_news_image_source_key(url, source_name)
        safe_source = self._safe_news_image_source_name(source_key)
        random_suffix = f"{random.getrandbits(48):012x}"
        ext = self._news_image_extension_from_url(url)
        return f"{safe_source}_{random_suffix}{ext}"

    def _is_managed_news_image_filename(self, filename: str) -> bool:
        match = self._NEWS_IMAGE_RANDOM_PATTERN.match(str(filename or ""))
        if not match:
            return False
        source_key, _, ext = match.groups()
        if ext.lower() not in self._NEWS_IMAGE_EXTENSIONS:
            return False
        return source_key in NEWS_SOURCE_MAP or source_key in {"60s", "ai", "news"}

    async def _download_image_to_local(self, url: str, filename: str) -> Optional[str]:
        """将图片预先下载到本地 Temp 文件夹再发送。"""
        temp_path = None
        try:
            news_conf = self.plugin.config.get("news_conf", {})
            timeout_sec = int(news_conf.get("news_api_timeout", 30))

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout_sec) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        temp_dir = os.path.join(str(self.plugin.data_dir), "Temp")
                        os.makedirs(temp_dir, exist_ok=True)
                        temp_path = os.path.join(temp_dir, filename)
                        async with aiofiles.open(temp_path, "wb") as f:
                            await f.write(img_bytes)
                        self._cleanup_news_source_images_after_download()
                        return temp_path
                    logger.warning(f"[每日分享] 图片下载失败，HTTP 状态码: {resp.status}")
        except Exception as e:
            if temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            logger.warning(f"[每日分享] 图片下载异常: {e}")
        return None

    async def _prepare_qzone_image(self, image_ref):
        """将 QQ 空间图片整理为 qzone 插件支持的链接或字节数据。"""
        if not image_ref:
            return None

        image_ref = str(image_ref)
        if image_ref.startswith(("http://", "https://")):
            return image_ref

        if os.path.exists(image_ref):
            try:
                async with aiofiles.open(image_ref, "rb") as file:
                    return await file.read()
            except Exception as e:
                logger.warning(f"[每日分享] 读取 QQ 空间本地配图失败: {e}")
                return None

        logger.warning(f"[每日分享] QQ 空间配图路径不存在: {image_ref}")
        return None

    def _get_weixin_temp_cleanup_max_count(self) -> int:
        try:
            max_count = int(self.image_conf.get("weixin_temp_cleanup_max_count", 10))
        except Exception:
            max_count = 10
        return max(0, min(max_count, 1000))

    def _get_news_image_cleanup_max_count(self) -> int:
        try:
            max_count = int(self.image_conf.get("news_image_cleanup_max_count", 200))
        except Exception:
            max_count = 200
        return max(0, min(max_count, 10000))

    def _cleanup_temp_files_sync(self, matcher, max_count: int, scan_label: str, cleanup_label: str):
        temp_dir = os.path.join(str(self.plugin.data_dir), "Temp")
        if not os.path.isdir(temp_dir):
            return

        files = []
        for name in os.listdir(temp_dir):
            if not matcher(name):
                continue

            path = os.path.join(temp_dir, name)
            try:
                if os.path.isfile(path):
                    files.append((os.path.getmtime(path), path, os.path.getsize(path)))
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug(f"[每日分享] 扫描{scan_label}失败: {path}, {e}")

        if len(files) <= max_count:
            return

        files.sort(key=lambda item: item[0], reverse=True)
        deleted = 0
        freed_bytes = 0
        for _, path, size in files[max_count:]:
            try:
                os.remove(path)
                deleted += 1
                freed_bytes += size
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug(f"[每日分享] 清理{cleanup_label}失败: {path}, {e}")

        if deleted > 0:
            logger.debug(
                f"[每日分享] 已清理{cleanup_label} {deleted} 张，释放 "
                f"{freed_bytes / 1024 / 1024:.2f}MB (保留最新 {max_count} 张)"
            )

    def setup_news_image_cleanup(self):
        """注册新闻源长图临时文件清理任务。"""
        job_id = "news_image_cleanup"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.debug(f"[每日分享] 移除旧新闻源图片清理任务失败: {e}")

        max_count = self._get_news_image_cleanup_max_count()
        if max_count <= 0:
            logger.debug("[每日分享] 新闻源图片自动清理已关闭")
            return

        self._setup_cron_job_custom(job_id, "30 3 * * *", self.cleanup_news_source_images)
        self.plugin._track_task(self.cleanup_news_source_images())

    def _cleanup_news_source_images_sync(self, max_count: int):
        self._cleanup_temp_files_sync(
            self._is_managed_news_image_filename,
            max_count,
            "新闻源图片",
            "新闻源图片",
        )

    async def cleanup_news_source_images(self):
        """清理下载到 Temp 的新闻源长图。"""
        if self.plugin._is_terminated:
            return

        max_count = self._get_news_image_cleanup_max_count()
        if max_count <= 0:
            return

        try:
            await asyncio.to_thread(self._cleanup_news_source_images_sync, max_count)
        except Exception as e:
            logger.warning(f"[每日分享] 新闻源图片清理失败: {e}")

    def _cleanup_news_source_images_after_download(self):
        max_count = self._get_news_image_cleanup_max_count()
        if max_count <= 0:
            return
        try:
            self._cleanup_news_source_images_sync(max_count)
        except Exception as e:
            logger.debug(f"[每日分享] 下载后清理新闻源图片失败: {e}")

    def setup_weixin_temp_cleanup(self):
        """注册 weixin_oc 压缩图片临时文件清理任务。"""
        job_id = "weixin_temp_cleanup"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.debug(f"[每日分享] 移除旧微信临时图清理任务失败: {e}")

        max_count = self._get_weixin_temp_cleanup_max_count()
        if max_count <= 0:
            logger.debug("[每日分享] 个人微信压缩图自动清理已关闭")
            return

        self._setup_cron_job_custom(job_id, "20 3 * * *", self.cleanup_weixin_temp_images)
        self.plugin._track_task(self.cleanup_weixin_temp_images())

    def _cleanup_weixin_temp_images_sync(self, max_count: int):
        self._cleanup_temp_files_sync(
            lambda name: name.startswith("weixin_send_") and name.lower().endswith(".jpg"),
            max_count,
            "微信压缩临时图",
            "个人微信压缩临时图",
        )

    async def cleanup_weixin_temp_images(self):
        """清理发送前压缩生成的 weixin_oc 图片副本。"""
        if self.plugin._is_terminated:
            return

        max_count = self._get_weixin_temp_cleanup_max_count()
        if max_count <= 0:
            return

        try:
            await asyncio.to_thread(self._cleanup_weixin_temp_images_sync, max_count)
        except Exception as e:
            logger.warning(f"[每日分享] 个人微信压缩图清理失败: {e}")
