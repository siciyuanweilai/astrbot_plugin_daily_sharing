import asyncio
import hashlib
import os

from astrbot.api import logger


class TaskDeliveryWeixinMixin:
    """个人微信平台发送前的超时与图片压缩处理。"""

    def _get_weixin_timeout_ms(self) -> int:
        try:
            timeout_seconds = int(self.image_conf.get("weixin_api_timeout_seconds", 60))
        except Exception:
            timeout_seconds = 60
        timeout_ms = timeout_seconds * 1000
        return max(15000, min(timeout_ms, 300000))

    def _apply_weixin_timeout(self, platform_inst):
        """按插件配置调高 weixin_oc API/CDN 上传超时，避免大图上传被 15 秒默认值截断。"""
        if not platform_inst:
            return
        timeout_ms = self._get_weixin_timeout_ms()
        try:
            old_timeout = getattr(platform_inst, "api_timeout_ms", None)
            if old_timeout != timeout_ms:
                setattr(platform_inst, "api_timeout_ms", timeout_ms)

            client = getattr(platform_inst, "client", None)
            if client and getattr(client, "api_timeout_ms", None) != timeout_ms:
                setattr(client, "api_timeout_ms", timeout_ms)
        except Exception as e:
            logger.debug(f"[每日分享] 设置个人微信平台(weixin_oc)超时失败: {e}")

    def _compress_image_for_weixin_sync(
        self,
        img_path: str,
        max_side: int = None,
        max_kb: int = None,
        force: bool = False,
    ) -> str:
        """为 weixin_oc 发送创建轻量图片副本，降低 CDN 上传超时概率。"""
        if not img_path or not os.path.exists(img_path):
            return img_path

        try:
            from PIL import Image as PILImage
            from PIL import ImageOps
        except Exception as e:
            logger.debug(f"[每日分享] Pillow 不可用，跳过微信图片压缩: {e}")
            return img_path

        if max_side is None:
            try:
                max_side = int(self.image_conf.get("weixin_image_max_side", 4096))
            except Exception:
                max_side = 4096
        if max_kb is None:
            try:
                max_kb = int(self.image_conf.get("weixin_image_max_size_kb", 10240))
            except Exception:
                max_kb = 10240

        max_side = max(1600, min(max_side, 8192))
        target_bytes = max(512, max_kb) * 1024
        raw_size = os.path.getsize(img_path)

        try:
            with PILImage.open(img_path) as im:
                im = ImageOps.exif_transpose(im)
                width, height = im.size
                if not force and raw_size <= target_bytes and max(width, height) <= max_side:
                    return img_path

                if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                    bg = PILImage.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")

                if max(width, height) > max_side:
                    im.thumbnail((max_side, max_side), PILImage.Resampling.LANCZOS)

                temp_dir = os.path.join(str(self.plugin.data_dir), "Temp")
                os.makedirs(temp_dir, exist_ok=True)
                digest_src = f"{img_path}:{raw_size}:{os.path.getmtime(img_path)}:{max_side}:{max_kb}:{force}".encode("utf-8", errors="ignore")
                digest = hashlib.md5(digest_src).hexdigest()[:12]
                out_path = os.path.join(temp_dir, f"weixin_send_{digest}.jpg")

                for quality in (95, 93, 90, 88, 85, 82, 78, 74, 70):
                    im.save(
                        out_path,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=False,
                        subsampling=0 if quality >= 90 else -1,
                    )
                    if os.path.getsize(out_path) <= target_bytes:
                        break

                out_size = os.path.getsize(out_path)
                if out_size < raw_size:
                    logger.info(
                        f"[每日分享] 已为个人微信平台(weixin_oc)优化图片: {raw_size / 1024 / 1024:.2f}MB -> "
                        f"{out_size / 1024 / 1024:.2f}MB，分辨率 {width}x{height} -> {im.size[0]}x{im.size[1]}"
                    )
                    max_count = self._get_weixin_temp_cleanup_max_count()
                    if max_count > 0:
                        self._cleanup_weixin_temp_images_sync(max_count)
                    return out_path

                try:
                    os.remove(out_path)
                except Exception as e:
                    logger.debug(f"[每日分享] 删除未压缩成功的微信临时图失败: {e}")
        except Exception as e:
            logger.warning(f"[每日分享] 微信图片压缩失败，继续发送原图: {e}")

        return img_path

    async def _prepare_image_for_target(self, uid: str, img_path: str) -> str:
        if not img_path:
            return img_path
        if self.ctx_service._is_weixin_platform(uid) and self.image_conf.get("weixin_compress_images", True):
            return await asyncio.to_thread(self._compress_image_for_weixin_sync, img_path)
        return img_path

    async def _prepare_weixin_retry_image(self, img_path: str) -> str:
        if not img_path or img_path.startswith("http") or not os.path.exists(img_path):
            return img_path
        if not self.image_conf.get("weixin_compress_images", True):
            return img_path

        try:
            configured_side = int(self.image_conf.get("weixin_image_max_side", 4096))
        except Exception:
            configured_side = 4096
        try:
            configured_kb = int(self.image_conf.get("weixin_image_max_size_kb", 10240))
        except Exception:
            configured_kb = 10240

        retry_side = min(max(1600, configured_side), 2048)
        retry_kb = min(max(512, configured_kb), 1024)
        return await asyncio.to_thread(
            self._compress_image_for_weixin_sync,
            img_path,
            max_side=retry_side,
            max_kb=retry_kb,
            force=True,
        )
