import asyncio
import random
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Record, Video


class TaskDeliveryMixin:
    """平台发送与投递结果处理。"""

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
                    f"[每日分享] 个人微信平台(weixin_oc)主动发送目标 {uid} 暂无上下文令牌(context_token)。"
                    "需要个人微信私聊发一条消息，AstrBot 收到后会保存上下文令牌(weixin_oc_context_tokens)。"
                )
            await platform_inst.send_by_session(session, chain)
            return

        await self.plugin.context.send_message(uid, chain)

    def _is_probable_delivery_timeout(self, error: Exception) -> bool:
        detail = f"{type(error).__name__}: {error}".lower()
        if "timeout" not in detail:
            return False
        return any(
            marker in detail
            for marker in (
                "retcode=1200",
                "retcode': 1200",
                '"retcode": 1200',
                "sendmsg",
                "ntevent",
            )
        )

    def _record_send_stage_error(
        self,
        media_result: dict,
        stage: str,
        error: Exception,
        *,
        probable_sent: bool = False,
    ) -> None:
        if media_result is None:
            return
        media_result.setdefault("partial_errors", []).append(
            {
                "stage": stage,
                "stage_label": self._send_stage_label(stage),
                "message": str(error),
                "probable_sent": probable_sent,
            }
        )

    def _send_stage_label(self, stage: str) -> str:
        return {
            "text": "文字",
            "audio": "语音",
            "image": "图片",
            "video": "视频",
        }.get(str(stage or ""), str(stage or "消息"))

    def _mark_send_stage_success(
        self,
        media_result: dict,
        stage: str,
        *,
        probable_sent: bool = False,
    ) -> None:
        if media_result is None:
            return
        media_result[f"{stage}_sent"] = True
        if probable_sent:
            media_result[f"{stage}_probable_sent"] = True

    def _has_sent_stage(self, media_result: dict) -> bool:
        if not media_result:
            return False
        return any(
            bool(media_result.get(f"{stage}_sent"))
            for stage in ("text", "audio", "image", "video")
        )

    async def _send_chain_stage(
        self,
        uid,
        chain: MessageChain,
        stage: str,
        event: AstrMessageEvent = None,
        media_result: dict = None,
    ) -> bool:
        try:
            await self._send_message_chain(uid, chain, event)
            self._mark_send_stage_success(media_result, stage)
            return True
        except Exception as error:
            if self._is_probable_delivery_timeout(error):
                self._record_send_stage_error(
                    media_result,
                    stage,
                    error,
                    probable_sent=True,
                )
                self._mark_send_stage_success(
                    media_result,
                    stage,
                    probable_sent=True,
                )
                logger.warning(
                    f"[每日分享] {self._send_stage_label(stage)}发送回执超时，消息可能已送达，继续后续流程: {error}"
                )
                return True
            self._record_send_stage_error(media_result, stage, error)
            raise

    async def _send_image_chain(
        self,
        uid: str,
        img_path: str,
        event: AstrMessageEvent = None,
        media_result: dict = None,
    ):
        img_chain = MessageChain()
        if img_path.startswith("http"):
            img_chain.url_image(img_path)
        else:
            img_chain.file_image(img_path)
        await self._send_chain_stage(uid, img_chain, "image", event, media_result)

    async def _send_image_chain_with_retry(
        self,
        uid: str,
        img_path: str,
        event: AstrMessageEvent = None,
        media_result: dict = None,
    ):
        errors_before = (
            len(media_result.get("partial_errors", []))
            if isinstance(media_result, dict)
            else 0
        )
        try:
            await self._send_image_chain(uid, img_path, event, media_result)
            return
        except Exception as first_error:
            if not self.ctx_service._is_weixin_platform(uid):
                raise

            retry_path = await self._prepare_weixin_retry_image(img_path)
            if not retry_path or retry_path == img_path:
                raise

            logger.warning(f"[每日分享] 个人微信平台(weixin_oc)图片发送失败，改用更小副本重试: {first_error}")
            await self._send_image_chain(uid, retry_path, event, media_result)
            if isinstance(media_result, dict):
                errors = media_result.get("partial_errors", [])
                if len(errors) > errors_before:
                    del errors[errors_before:]

    async def send(
        self,
        uid,
        text,
        img_path,
        audio_path=None,
        video_url=None,
        event: AstrMessageEvent = None,
        image_optional: bool = False,
        media_result: dict = None,
    ) -> bool:
        """分享内容（支持分开分享，支持语音和视频）"""
        if self.plugin._is_terminated: return False

        sent_any = False
        try:
            if media_result is not None:
                media_result.clear()

            separate_img = self.image_conf.get("separate_text_and_image", True)
            if image_optional:
                separate_img = True
            prefer_audio_only = self.tts_conf.get("prefer_audio_only", False)
            
            # 清洗情感标签
            clean_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', text, flags=re.IGNORECASE).strip()
            
            # 判断是否应该分享文字
            # 如果有语音，且开启了“仅发语音”，则不发文字
            should_send_text = True
            if audio_path and prefer_audio_only:
                should_send_text = False

            text_sent = False

            # 全局拦截发送的网络图片，转为本地图片 (无安全降级机制，失败则跳过图片)
            downloaded_img_path = None
            if img_path and img_path.startswith("http"):
                filename = self._build_news_image_filename(img_path)
                dl_path = await self._download_image_to_local(img_path, filename)
                if dl_path:
                    img_path = dl_path
                    downloaded_img_path = dl_path
                else:
                    logger.warning(f"[每日分享] 图片下载失败，已跳过发送该图片。")
                    img_path = None

            img_path = await self._prepare_image_for_target(uid, img_path)
            if image_optional and img_path and self.ctx_service._is_weixin_platform(uid):
                img_path = await self._prepare_weixin_retry_image(img_path)

            if media_result is not None:
                media_result.update(
                    {
                        "text_sent": False,
                        "audio_sent": False,
                        "image_sent": False,
                        "video_sent": False,
                    }
                )
                if downloaded_img_path:
                    media_result["downloaded_image_path"] = downloaded_img_path
                if img_path:
                    media_result["image_path"] = img_path
                if video_url:
                    media_result["video_url"] = video_url

            # 1. 分享文字（如果需要）
            if should_send_text and clean_text: 
                text_chain = MessageChain().message(clean_text) 
                # 如果图片不分开分享，且没有语音，且没有视频（视频无法合并），则合并图片
                image_attached_to_text = bool(img_path and not video_url and not separate_img and not audio_path)
                if image_attached_to_text:
                    if img_path.startswith("http"): text_chain.url_image(img_path)
                    else: text_chain.file_image(img_path)
                
                await self._send_chain_stage(uid, text_chain, "text", event, media_result)
                text_sent = True
                sent_any = True
                if image_attached_to_text:
                    self._mark_send_stage_success(media_result, "image")
                
                # 如果后续还有消息，进行随机延迟
                if audio_path or ((img_path or video_url) and separate_img):
                    await self.random_sleep()

            # 2. 分享语音（如果有）
            if audio_path:
                audio_chain = MessageChain()
                audio_chain.chain.append(Record(file=audio_path))
                await self._send_chain_stage(uid, audio_chain, "audio", event, media_result)
                sent_any = True
                
                # 如果后续还有视觉媒体，延迟
                if (img_path or video_url) and separate_img:
                    await self.random_sleep()
            
            # 3. 分享视觉媒体（视频优先，其次图片）
            if video_url:
                # 分享视频
                video_chain = MessageChain()
                # 判断是本地文件还是网络链接
                if video_url.startswith("http"):
                    video_chain.chain.append(Video.fromURL(video_url))
                else:
                    # 如果是本地路径，使用本地文件发送
                    video_chain.chain.append(Video.fromFileSystem(video_url))              
                await self._send_chain_stage(uid, video_chain, "video", event, media_result)
                sent_any = True
            elif img_path:
                # 分享图片（如果视频没生成，或者视频关闭）
                img_not_sent_yet = separate_img or audio_path
                if img_not_sent_yet:
                    try:
                        await self._send_image_chain_with_retry(uid, img_path, event, media_result)
                        sent_any = True
                    except Exception as image_error:
                        if image_optional and (text_sent or sent_any):
                            logger.warning(f"[每日分享] 图片发送失败，已保留已发送内容: {image_error}")
                            return True
                        raise

            return sent_any

        except Exception as e:
            if sent_any or self._has_sent_stage(media_result):
                logger.warning(f"[每日分享] 分享内容给 {uid} 部分已发送，后续阶段失败: {e}")
                return True
            logger.error(f"[每日分享] 分享内容给 {uid} 失败: {e}")
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
