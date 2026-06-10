from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.core.platform.astr_message_event import AstrMessageEvent


class EmojiID:
    PROCESSING = 125
    SUCCESS = 79
    FAILED = 106


async def _get_message_id(event: AstrMessageEvent) -> int | None:
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    try:
        if isinstance(raw, dict) and raw.get("message_id") is not None:
            return int(raw["message_id"])
        message_id = getattr(message_obj, "message_id", None)
        if message_id is not None:
            return int(message_id)
    except Exception as exc:
        logger.debug(f"[每日分享][表情标记] 获取消息标识失败: {exc}")
    return None


async def _get_bot(event: AstrMessageEvent) -> Any | None:
    return getattr(event, "bot", None)


async def set_emoji(
    event: AstrMessageEvent,
    emoji_id: int,
    emoji_type: str = "1",
) -> bool:
    message_id = await _get_message_id(event)
    if message_id is None:
        logger.debug("[每日分享][表情标记] 跳过：没有消息标识")
        return False

    bot = await _get_bot(event)
    set_msg_emoji_like = getattr(bot, "set_msg_emoji_like", None)
    if not callable(set_msg_emoji_like):
        logger.debug("[每日分享][表情标记] 跳过：不支持设置消息贴表情")
        return False

    try:
        await set_msg_emoji_like(
            message_id=message_id,
            emoji_id=emoji_id,
            emoji_type=emoji_type,
            set=True,
        )
        logger.debug(
            f"[每日分享][表情标记] 已标记消息，消息标识={message_id}, 表情标识={emoji_id}"
        )
        return True
    except Exception as exc:
        logger.debug(f"[每日分享][表情标记] 标记失败: {exc}")
        return False


async def mark_processing(event: AstrMessageEvent) -> bool:
    return await set_emoji(event, EmojiID.PROCESSING)


async def mark_success(event: AstrMessageEvent) -> bool:
    return await set_emoji(event, EmojiID.SUCCESS)


async def mark_failed(event: AstrMessageEvent) -> bool:
    return await set_emoji(event, EmojiID.FAILED)
