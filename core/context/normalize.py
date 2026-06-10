from .shared import (
    DAILY_SHARING_INTERNAL_TRIGGER,
    DAILY_SHARING_SOURCE,
    Any,
    Dict,
    List,
    Optional,
    datetime,
)


class ContextHistoryNormalizeMixin:
    def _get_platform_history_user_ids(self, adapter_id: str, real_id: str) -> List[str]:
        ids = []
        real_id = str(real_id or "").strip()
        if real_id:
            ids.append(real_id)

        if str(adapter_id or "").strip().lower().startswith("webchat") and real_id.startswith("webchat!"):
            parts = real_id.split("!", 2)
            if len(parts) == 3 and parts[2]:
                ids.append(parts[2])

        return list(dict.fromkeys(ids))

    def _normalize_platform_history_item(self, record: Any) -> Optional[Dict[str, str]]:
        role_content = self._extract_platform_history_role_content(record)
        if not role_content:
            return None

        role, content = role_content
        if self._is_internal_share_trigger(role, content):
            return None

        created_at = getattr(record, "created_at", None)
        try:
            if isinstance(created_at, datetime.datetime):
                ts_str = created_at.isoformat()
            elif created_at:
                ts_str = str(created_at)
            else:
                ts_str = ""
        except Exception:
            ts_str = ""

        sender_id = getattr(record, "sender_id", None)
        sender_name = getattr(record, "sender_name", None)
        return {
            "role": role,
            "content": content,
            "timestamp": ts_str,
            "user_id": str(sender_id or sender_name or role),
            "source": "chat",
        }

    def _extract_platform_history_role_content(self, record: Any) -> Optional[tuple[str, str]]:
        content_obj = getattr(record, "content", None)
        content_type = ""
        content = ""

        if isinstance(content_obj, dict):
            content_type = str(content_obj.get("type") or "").lower()
            message_parts = content_obj.get("message", content_obj.get("content", ""))
            if isinstance(message_parts, list):
                content = self._extract_text_from_parts(message_parts)
            elif isinstance(message_parts, dict):
                content = self._extract_text_from_parts([message_parts])
            else:
                content = str(
                    content_obj.get("text")
                    or content_obj.get("data")
                    or message_parts
                    or ""
                )
        elif content_obj is not None:
            content = str(content_obj)

        content = content.strip()
        if not content:
            return None

        sender_id = str(getattr(record, "sender_id", "") or "").lower()
        if content_type:
            role = "assistant" if content_type in ("bot", "assistant") else "user"
        else:
            role = "assistant" if sender_id in ("bot", "assistant") else "user"
        return role, content

    def _mark_daily_share_sources(self, messages: List[Dict[str, str]], reference_messages: List[Dict[str, str]]) -> None:
        daily_contents = [
            str(msg.get("content") or "").strip()
            for msg in reference_messages
            if msg.get("source") == DAILY_SHARING_SOURCE and str(msg.get("content") or "").strip()
        ]
        if not daily_contents:
            return

        for msg in messages:
            if msg.get("role") != "assistant" or msg.get("source") == DAILY_SHARING_SOURCE:
                continue
            content = str(msg.get("content") or "").strip()
            if any(self._is_same_daily_share_content(content, ref) for ref in daily_contents):
                msg["source"] = DAILY_SHARING_SOURCE

    def _is_same_daily_share_content(self, content: str, reference: str) -> bool:
        content = str(content or "").strip()
        reference = str(reference or "").strip()
        return bool(content and reference and (content == reference or content.startswith(reference) or reference.startswith(content)))

    def _normalize_conversation_history_item(self, item: Any) -> Optional[Dict[str, str]]:
        """把 AstrBot conversation.history 中的不同结构归一成 prompt 可用消息。"""
        role_content = self._extract_conversation_item_role_content(item)
        if not role_content:
            return None

        role, content = role_content
        if self._is_internal_share_trigger(role, content):
            return None

        ts = item.get("timestamp") or item.get("time")
        try:
            if isinstance(ts, (int, float)):
                ts_str = datetime.datetime.fromtimestamp(ts).isoformat()
            elif ts:
                ts_str = str(ts)
            else:
                ts_str = ""
        except Exception:
            ts_str = ""

        return {
            "role": role,
            "content": content,
            "timestamp": ts_str,
            "user_id": str(item.get("user_id") or item.get("name") or role),
            "source": "chat",
        }

    def _extract_conversation_item_role_content(self, item: Any) -> Optional[tuple[str, str]]:
        if not isinstance(item, dict):
            return None

        role = str(item.get("role") or item.get("type") or "user").lower()
        if role not in ("user", "assistant"):
            role = "assistant" if role in ("ai", "bot") else "user"

        content = item.get("content", "")
        if isinstance(content, list):
            content = self._extract_text_from_parts(content)
        elif isinstance(content, dict):
            content = self._extract_text_from_parts([content])
        else:
            content = str(content or "")

        content = content.strip()
        if not content:
            return None
        return role, content

    def _is_internal_share_trigger(self, role: str, content: str) -> bool:
        return role == "user" and content.startswith(DAILY_SHARING_INTERNAL_TRIGGER)

    def _extract_text_from_parts(self, parts: List[Any]) -> str:
        texts = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                if "text" in part:
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "plain":
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "text":
                    data = part.get("data") or {}
                    texts.append(str(data.get("text") or part.get("content") or ""))
                elif "message" in part:
                    nested = part.get("message")
                    if isinstance(nested, list):
                        texts.append(self._extract_text_from_parts(nested))
                    elif isinstance(nested, dict):
                        texts.append(self._extract_text_from_parts([nested]))
                    else:
                        texts.append(str(nested or ""))
                elif "content" in part:
                    nested = part.get("content")
                    if isinstance(nested, list):
                        texts.append(self._extract_text_from_parts(nested))
                    else:
                        texts.append(str(nested or ""))
                elif part.get("type") in ("image", "img"):
                    texts.append("[图片]")
                elif part.get("type") in ("record", "audio"):
                    texts.append("[语音]")
                elif part.get("type") == "video":
                    texts.append("[视频]")
                elif part.get("type") == "file":
                    texts.append("[文件]")
            else:
                text = getattr(part, "text", None)
                if text:
                    texts.append(str(text))
        return " ".join(t for t in texts if t).strip()
