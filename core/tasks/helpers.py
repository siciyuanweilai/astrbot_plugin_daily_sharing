import asyncio
import re
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain

from ..config import NEWS_SOURCE_MAP, TimePeriod
from ..constants import CMD_CN_MAP, SOURCE_CN_MAP


class TaskExecutorHelperMixin:
    """分享执行器的通用辅助方法。"""

    def get_curr_period(self) -> TimePeriod:
        h = datetime.now().hour
        if 0 <= h < 6:
            return TimePeriod.DAWN
        if 6 <= h < 9:
            return TimePeriod.MORNING
        if 9 <= h < 12:
            return TimePeriod.FORENOON
        if 12 <= h < 14:
            return TimePeriod.NOON
        if 14 <= h < 16:
            return TimePeriod.AFTERNOON
        if 16 <= h < 19:
            return TimePeriod.EVENING
        if 19 <= h < 22:
            return TimePeriod.NIGHT
        return TimePeriod.LATE_NIGHT

    def get_period_range_str(self, period: TimePeriod) -> str:
        """获取时段对应的时间范围。"""
        return {
            TimePeriod.DAWN: "00:00-06:00",
            TimePeriod.MORNING: "06:00-09:00",
            TimePeriod.FORENOON: "09:00-12:00",
            TimePeriod.NOON: "12:00-14:00",
            TimePeriod.AFTERNOON: "14:00-16:00",
            TimePeriod.EVENING: "16:00-19:00",
            TimePeriod.NIGHT: "19:00-22:00",
            TimePeriod.LATE_NIGHT: "22:00-24:00",
        }.get(period, "")

    def _strip_emotion_tags(self, content: str) -> str:
        return re.sub(
            r"\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$",
            "",
            str(content or ""),
            flags=re.IGNORECASE,
        ).strip()

    def _media_history_kwargs(self, media_type: str, media_ref: str = None) -> dict:
        ref = str(media_ref or "").strip()
        if not ref:
            return {}
        if ref.startswith(("http://", "https://")):
            return {"media_type": media_type, "media_url": ref}
        return {"media_type": media_type, "media_path": ref}

    def _image_history_kwargs(self, media_ref: str = None) -> dict:
        return self._media_history_kwargs("image", media_ref)

    def _video_history_kwargs(self, media_ref: str = None) -> dict:
        return self._media_history_kwargs("video", media_ref)

    def _visual_history_kwargs(self, image_ref: str = None, video_ref: str = None) -> dict:
        if video_ref:
            return self._video_history_kwargs(video_ref)
        return self._image_history_kwargs(image_ref)

    def _sent_visual_history_kwargs(
        self,
        media_result: dict = None,
        image_ref: str = None,
        video_ref: str = None,
    ) -> dict:
        if media_result is None:
            return self._visual_history_kwargs(image_ref, video_ref)
        if media_result.get("video_sent"):
            resolved_video = media_result.get("video_url") or video_ref
            return self._video_history_kwargs(resolved_video)
        if media_result.get("image_sent"):
            resolved_image = (
                media_result.get("downloaded_image_path")
                or media_result.get("image_path")
                or image_ref
            )
            return self._image_history_kwargs(resolved_image)
        return {}

    def _partial_send_error_labels(self, media_result: dict = None) -> list:
        labels = []
        for item in (media_result or {}).get("partial_errors", []):
            if item.get("probable_sent"):
                continue
            label = str(item.get("stage_label") or item.get("stage") or "媒体").strip()
            if label and label not in labels:
                labels.append(label)
        return labels

    def _log_partial_send_errors(self, target_id: str, media_result: dict = None) -> None:
        errors = (media_result or {}).get("partial_errors", [])
        if not errors:
            return
        summary = "；".join(
            f"{item.get('stage_label') or item.get('stage')}: {item.get('message')}"
            for item in errors
        )
        logger.warning(f"[每日分享] {target_id} 部分发送异常: {summary}")

    async def _notify_partial_send_errors(self, event: AstrMessageEvent, media_result: dict = None) -> None:
        labels = self._partial_send_error_labels(media_result)
        if not event or not labels:
            return
        try:
            await event.send(event.plain_result(f"内容已发送，{'、'.join(labels)}未送达，请查看日志。"))
        except Exception as exc:
            logger.debug(f"[每日分享] 部分发送异常提示发送失败: {exc}")

    def _event_history_target(self, event: AstrMessageEvent) -> str:
        if not event:
            return ""
        try:
            sender_id = str(event.get_sender_id() or "").strip()
        except Exception as e:
            logger.debug(f"[每日分享] 无法读取自然语言触发发送者标识: {e}")
            sender_id = ""
        if sender_id and ":" in sender_id:
            return sender_id
        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        return origin or sender_id

    def _map_share_type_arg(self, share_type_text: str):
        if share_type_text in CMD_CN_MAP:
            return CMD_CN_MAP[share_type_text]
        for name, stype in CMD_CN_MAP.items():
            if name in share_type_text:
                return stype
        return None

    def _map_news_source_arg(self, source: str):
        if not source:
            return None
        if source in SOURCE_CN_MAP:
            return SOURCE_CN_MAP[source]
        if source in NEWS_SOURCE_MAP:
            return source
        for name, key in SOURCE_CN_MAP.items():
            if name in source or source in name:
                return key
        return None

    async def _format_recent_dynamics(self, target_id: str) -> str:
        try:
            ref_count = int(self.context_conf.get("reference_history_count", 3))
        except Exception:
            ref_count = 3
        if ref_count <= 0:
            return ""
        recent_hist = await self.db.get_recent_history_by_target(target_id, limit=ref_count)
        if not recent_hist:
            return ""
        return "\n".join(
            f"- [{h.get('type')}] {self._strip_emotion_tags(h.get('content', ''))}"
            for h in reversed(recent_hist)
        )

    async def _cache_news_snapshot_for_targets(
        self,
        *target_uids,
        news_data=None,
        source_key: str = None,
        image_url: str = None,
        event: AstrMessageEvent = None,
    ):
        for target_uid in target_uids:
            if target_uid:
                await self.cache_news_snapshot(
                    target_uid,
                    news_data=news_data,
                    source_key=source_key,
                    image_url=image_url,
                )
        if event:
            current_target = str(getattr(event, "unified_msg_origin", "") or "").strip()
            if current_target and current_target not in target_uids:
                await self.cache_news_snapshot(
                    current_target,
                    news_data=news_data,
                    source_key=source_key,
                    image_url=image_url,
                )

    async def _sync_qzone_result_to_event(self, event: AstrMessageEvent, text: str, img_path: str = None):
        """QQ 空间发布成功后，把结果同步回当前触发会话。"""
        if not event:
            return

        target_uid = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if target_uid and self.ctx_service._is_weixin_platform(target_uid):
            ok = await self.send(target_uid, text, img_path, event=event, image_optional=True)
            if not ok:
                logger.error("[每日分享] 同步发送内容到会话失败")
            return

        text_chain = MessageChain().message(text)
        await event.send(text_chain)

        if img_path:
            await asyncio.sleep(1.0)
            img_chain = MessageChain()
            if img_path.startswith("http"):
                img_chain.url_image(img_path)
            else:
                img_chain.file_image(img_path)
            await event.send(img_chain)
