from .common import *  # noqa: F401,F403


class TaskDeliveryMixin:
    """Image preparation, platform sending, and delivery cleanup helpers."""

    async def _download_image_to_local(self, url: str, filename: str) -> Optional[str]:
        """将图片预先下载到本地的 Temp 文件夹再发送"""
        try:
            # 统一存放至插件目录下的 Temp 文件夹
            temp_dir = os.path.join(self.plugin.data_dir, "Temp")
            os.makedirs(temp_dir, exist_ok=True)
            temp_path = os.path.join(temp_dir, filename)

            # 读取面板中的新闻热搜 API 超时配置
            news_conf = self.plugin.config.get("news_conf", {})
            timeout_sec = int(news_conf.get("news_api_timeout", 30))            
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout_sec) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        async with aiofiles.open(temp_path, "wb") as f:
                            await f.write(img_bytes)
                        return temp_path
                    else:
                        logger.warning(f"[DailySharing] 图片下载失败，HTTP 状态码: {resp.status}")
        except Exception as e:
            logger.warning(f"[DailySharing] 图片下载异常: {e}")
        return None

    async def _prepare_qzone_image(self, image_ref):
        """将 QQ 空间图片参数整理为 URL 或本地路径标记。"""
        if not image_ref:
            return None

        image_ref = str(image_ref)
        if image_ref.startswith(("http://", "https://")):
            return image_ref

        if os.path.exists(image_ref):
            return f"local_path::{image_ref}"

        logger.warning(f"[DailySharing] QQ空间配图路径不存在: {image_ref}")
        return None

    def _get_weixin_temp_cleanup_max_count(self) -> int:
        try:
            max_count = int(self.image_conf.get("weixin_temp_cleanup_max_count", 10))
        except Exception:
            max_count = 10
        return max(0, min(max_count, 1000))

    def setup_weixin_temp_cleanup(self):
        """注册 weixin_oc 压缩图片临时文件清理任务。"""
        job_id = "weixin_temp_cleanup"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.debug(f"[DailySharing] 移除旧微信临时图清理任务失败: {e}")

        max_count = self._get_weixin_temp_cleanup_max_count()
        if max_count <= 0:
            logger.debug("[DailySharing] 个人微信压缩图自动清理已关闭")
            return

        self._setup_cron_job_custom(job_id, "20 3 * * *", self.cleanup_weixin_temp_images)
        self.plugin._track_task(self.cleanup_weixin_temp_images())

    def _cleanup_weixin_temp_images_sync(self, max_count: int):
        temp_dir = os.path.join(str(self.plugin.data_dir), "Temp")
        if not os.path.isdir(temp_dir):
            return

        files = []

        for name in os.listdir(temp_dir):
            if not name.startswith("weixin_send_") or not name.lower().endswith(".jpg"):
                continue

            path = os.path.join(temp_dir, name)
            try:
                if not os.path.isfile(path):
                    continue
                files.append((os.path.getmtime(path), path, os.path.getsize(path)))
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.debug(f"[DailySharing] 扫描微信压缩临时图失败: {path}, {e}")

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
                logger.debug(f"[DailySharing] 清理微信压缩临时图失败: {path}, {e}")

        if deleted > 0:
            logger.debug(
                f"[DailySharing] 已清理个人微信压缩临时图 {deleted} 张，释放 "
                f"{freed_bytes / 1024 / 1024:.2f}MB (保留最新 {max_count} 张)"
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
            logger.warning(f"[DailySharing] 个人微信压缩图清理失败: {e}")

    async def _send_message_chain(self, uid, chain: MessageChain, event: AstrMessageEvent = None):
        if self.ctx_service._is_weixin_platform(uid):
            self._apply_weixin_timeout(getattr(event, "platform", None) if event else None)

        if event:
            await event.send(chain)
            return

        platform_inst = self._select_platform_instance_for_target(uid)
        session = self._build_message_session_for_target(uid, platform_inst)
        if platform_inst and session:
            if self.ctx_service._is_weixin_platform(uid):
                self._apply_weixin_timeout(platform_inst)
            if self.ctx_service._is_weixin_platform(uid) and not self._has_weixin_context_token(uid, platform_inst):
                logger.warning(
                    f"[DailySharing] weixin_oc 主动发送目标 {uid} 暂无 context_token。"
                    "需要个人微信私聊发一条消息，AstrBot 收到后会保存 weixin_oc_context_tokens。"
                )
            await platform_inst.send_by_session(session, chain)
            return

        await self.plugin.context.send_message(uid, chain)

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
            logger.debug(f"[DailySharing] 设置 weixin_oc 超时失败: {e}")

    def _compress_image_for_weixin_sync(
        self,
        img_path: str,
    ) -> str:
        """为 weixin_oc 发送创建轻量图片副本，降低 CDN 上传超时概率。"""
        if not img_path or not os.path.exists(img_path):
            return img_path

        try:
            from PIL import Image as PILImage
            from PIL import ImageOps
        except Exception as e:
            logger.debug(f"[DailySharing] Pillow 不可用，跳过微信图片压缩: {e}")
            return img_path

        try:
            max_side = int(self.image_conf.get("weixin_image_max_side", 4096))
        except Exception:
            max_side = 4096
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
                if raw_size <= target_bytes and max(width, height) <= max_side:
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
                digest_src = f"{img_path}:{raw_size}:{os.path.getmtime(img_path)}:{max_side}:{max_kb}".encode("utf-8", errors="ignore")
                digest = hashlib.md5(digest_src).hexdigest()[:12]
                out_path = os.path.join(temp_dir, f"weixin_send_{digest}.jpg")

                for quality in (95, 93, 90, 88, 85, 82, 78, 74, 70):
                    im.save(
                        out_path,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                        subsampling=0 if quality >= 90 else -1,
                    )
                    if os.path.getsize(out_path) <= target_bytes:
                        break

                out_size = os.path.getsize(out_path)
                if out_size < raw_size:
                    logger.info(
                        f"[DailySharing] 已为 weixin_oc 优化图片: {raw_size / 1024 / 1024:.2f}MB -> "
                        f"{out_size / 1024 / 1024:.2f}MB, 分辨率 {width}x{height} -> {im.size[0]}x{im.size[1]}"
                    )
                    max_count = self._get_weixin_temp_cleanup_max_count()
                    if max_count > 0:
                        self._cleanup_weixin_temp_images_sync(max_count)
                    return out_path

                try:
                    os.remove(out_path)
                except Exception as e:
                    logger.debug(f"[DailySharing] 删除未压缩成功的微信临时图失败: {e}")
        except Exception as e:
            logger.warning(f"[DailySharing] 微信图片压缩失败，继续发送原图: {e}")

        return img_path

    async def _prepare_image_for_target(self, uid: str, img_path: str) -> str:
        if not img_path:
            return img_path
        if self.ctx_service._is_weixin_platform(uid) and self.image_conf.get("weixin_compress_images", True):
            return await asyncio.to_thread(self._compress_image_for_weixin_sync, img_path)
        return img_path

    async def send(self, uid, text, img_path, audio_path=None, video_url=None, event: AstrMessageEvent = None) -> bool:
        """分享内容（支持分开分享，支持语音和视频）"""
        if self.plugin._is_terminated: return False

        try:
            separate_img = self.image_conf.get("separate_text_and_image", True)
            prefer_audio_only = self.tts_conf.get("prefer_audio_only", False)
            
            # 清洗情感标签
            clean_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', text, flags=re.IGNORECASE).strip()
            
            # 判断是否应该分享文字
            # 如果有语音，且开启了“仅发语音”，则不发文字
            should_send_text = True
            if audio_path and prefer_audio_only:
                should_send_text = False

            # 全局拦截发送的网络图片，转为本地图片 (无安全降级机制，失败则跳过图片)
            if img_path and img_path.startswith("http"):
                dl_path = await self._download_image_to_local(img_path, "global_hot_news.png")
                if dl_path:
                    img_path = dl_path
                else:
                    logger.warning(f"[DailySharing] 图片下载失败，已跳过发送该图片。")
                    img_path = None

            img_path = await self._prepare_image_for_target(uid, img_path)

            # 1. 分享文字（如果需要）
            if should_send_text and clean_text: 
                text_chain = MessageChain().message(clean_text) 
                # 如果图片不分开分享，且没有语音，且没有视频（视频无法合并），则合并图片
                if img_path and not video_url and not separate_img and not audio_path:
                    if img_path.startswith("http"): text_chain.url_image(img_path)
                    else: text_chain.file_image(img_path)
                
                await self._send_message_chain(uid, text_chain, event)
                
                # 如果后续还有消息，进行随机延迟
                if audio_path or ((img_path or video_url) and separate_img):
                    await self.random_sleep()

            # 2. 分享语音（如果有）
            if audio_path:
                audio_chain = MessageChain()
                audio_chain.chain.append(Record(file=audio_path))
                await self._send_message_chain(uid, audio_chain, event)
                
                # 如果后续还有视觉媒体，延迟
                if (img_path or video_url) and separate_img:
                    await self.random_sleep()
            
            # 3. 分享视觉媒体（视频优先，其次图片）
            if video_url:
                # 分享视频
                video_chain = MessageChain()
                # 判断是本地文件还是网络URL
                if video_url.startswith("http"):
                    video_chain.chain.append(Video.fromURL(video_url))
                else:
                    # 如果是本地路径，使用 fromFile
                    video_chain.chain.append(Video.fromFileSystem(video_url))              
                await self._send_message_chain(uid, video_chain, event)
            elif img_path:
                # 分享图片（如果视频没生成，或者视频关闭）
                img_not_sent_yet = separate_img or audio_path
                if img_not_sent_yet:
                    img_chain = MessageChain()
                    if img_path.startswith("http"): img_chain.url_image(img_path)
                    else: img_chain.file_image(img_path)
                    await self._send_message_chain(uid, img_chain, event)

            return True

        except Exception as e:
            logger.error(f"[DailySharing] 分享内容给 {uid} 失败: {e}")
            return False

    async def random_sleep(self):
        """随机延迟"""
        if self.plugin._is_terminated: return

        delay_str = self.image_conf.get("separate_send_delay", "1.0-2.0")
        try:
            if "-" in str(delay_str):
                d_min, d_max = map(float, str(delay_str).split("-"))
                await asyncio.sleep(random.uniform(d_min, d_max))
            else:
                await asyncio.sleep(float(delay_str))
        except (TypeError, ValueError):
            await asyncio.sleep(1.5)
