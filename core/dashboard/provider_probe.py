import json
import uuid
from typing import Any


class DashboardProviderProbeMixin:
    """Pages LLM-tool provider probing helpers."""

    _PROBE_TOOL_KEYWORDS = {
        "image": (
            "aiimg",
            "image",
            "img",
            "draw",
            "paint",
            "photo",
            "picture",
            "generate",
            "生图",
            "绘图",
            "画图",
            "图片",
        ),
        "selfie": (
            "aiimg",
            "selfie",
            "ref",
            "reference",
            "persona",
            "image",
            "photo",
            "自拍",
            "参考",
            "人设",
            "图片",
        ),
        "tts": (
            "tts",
            "voice",
            "audio",
            "speech",
            "synthesize",
            "语音",
            "音频",
            "朗读",
        ),
        "video": (
            "aiimg",
            "video",
            "i2v",
            "image_to_video",
            "img2video",
            "movie",
            "motion",
            "视频",
            "动图",
        ),
    }

    def _page_probe_target_umo(self, body: dict) -> str:
        raw = str(body.get("target_umo") or "").strip()
        if raw.count(":") >= 2:
            return raw
        for source in (
            self.receiver_conf.get("users", []),
            self.receiver_conf.get("groups", []),
            self.extra_shares_conf.get("briefing_users", []),
            self.extra_shares_conf.get("briefing_groups", []),
        ):
            if not isinstance(source, list):
                continue
            for item in source:
                value = str(item or "").strip()
                if value.count(":") >= 2:
                    return value
        return "daily_sharing_probe:FriendMessage:provider_probe"

    async def _page_probe_provider_id(self, target_umo: str) -> str:
        configured = str(self.llm_conf.get("llm_provider_id", "") or "").strip()
        if configured:
            return configured
        getter = getattr(self.context, "get_current_chat_provider_id", None)
        if callable(getter):
            provider_id = await getter(target_umo)
            if provider_id:
                return provider_id
        cfg_getter = getattr(self.context, "get_config", None)
        if callable(cfg_getter):
            cfg = cfg_getter(umo=target_umo)
            provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            provider_id = str(provider_settings.get("default_provider_id") or "").strip()
            if provider_id:
                return provider_id
        raise RuntimeError("未找到可用于 LLM 工具探测的模型 provider")

    def _page_probe_toolset(self, kind: str):
        from astrbot.core.agent.tool import ToolSet

        getter = getattr(self.context, "get_llm_tool_manager", None)
        tool_mgr = getter() if callable(getter) else None
        tools = list(getattr(tool_mgr, "func_list", []) or [])
        keywords = self._PROBE_TOOL_KEYWORDS.get(kind, ())
        toolset = ToolSet()
        for tool in tools:
            if not getattr(tool, "active", True):
                continue
            name = str(getattr(tool, "name", "") or "")
            desc = str(getattr(tool, "description", "") or "")
            haystack = f"{name}\n{desc}".lower()
            if any(keyword.lower() in haystack for keyword in keywords):
                toolset.add_tool(tool)
        if toolset.empty():
            raise RuntimeError("未找到可供 LLM 选择的媒体工具")
        return toolset

    def _page_probe_prompt(self, kind: str, body: dict) -> tuple[str, str]:
        if kind == "selfie":
            prompt = str(body.get("prompt") or "").strip() or "生成一张日常自拍，使用当前人设/参考图，不要文字和水印。"
            return (
                "你正在执行每日分享的工具探测。必须调用一个最适合生成自拍、参考图或人设照片的工具；"
                "如果工具有 mode 参数，请使用 selfie_ref。工具完成后只简短说明工具已调用。",
                prompt,
            )
        if kind == "tts":
            text = str(body.get("text") or body.get("prompt") or "").strip() or "每日分享语音测试。"
            return (
                "你正在执行每日分享的工具探测。必须调用一个最适合文本转语音或朗读的工具；"
                "工具完成后只简短说明工具已调用。",
                text,
            )
        if kind == "video":
            prompt = str(body.get("prompt") or "").strip() or "把一张日常照片转成 3 秒自然生活视频，轻微镜头运动。"
            return (
                "你正在执行每日分享的工具探测。必须调用一个最适合图生视频或视频生成的工具；"
                "如工具需要图片路径，可使用占位路径 /tmp/daily_sharing_probe.png。工具完成后只简短说明工具已调用。",
                prompt,
            )
        prompt = str(body.get("prompt") or "").strip() or "生成一张桌面上的咖啡杯日常照片，不要文字和水印。"
        return (
            "你正在执行每日分享的工具探测。必须调用一个最适合文生图或图片生成的工具；"
            "如果工具有 mode 参数，请使用 text。工具完成后只简短说明工具已调用。",
            prompt,
        )

    def _page_probe_event(self, target_umo: str, prompt: str):
        from astrbot.core.message.components import Plain
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.platform.astr_message_event import AstrMessageEvent
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.platform.platform_metadata import PlatformMetadata

        parts = target_umo.split(":", 2)
        platform_id = parts[0] if len(parts) == 3 else "daily_sharing_probe"
        message_type_raw = parts[1] if len(parts) == 3 else "FriendMessage"
        session_id = parts[2] if len(parts) == 3 else "provider_probe"
        try:
            message_type = MessageType(message_type_raw)
        except Exception:
            message_type = MessageType.FRIEND_MESSAGE

        message = AstrBotMessage()
        message.type = message_type
        message.self_id = "daily_sharing_probe"
        message.session_id = session_id
        message.message_id = f"daily-sharing-probe-{uuid.uuid4().hex}"
        message.sender = MessageMember(session_id, "DailySharingProbe")
        message.message = [Plain(prompt)]
        message.message_str = prompt
        message.raw_message = {"daily_sharing_provider_probe": True}

        platform = PlatformMetadata(
            name=platform_id,
            description="DailySharing provider probe",
            id=platform_id,
        )

        class SilentProbeEvent(AstrMessageEvent):
            def __init__(self):
                super().__init__(prompt, message, platform, session_id)
                self.sent_messages = []

            async def send(self, message_chain: MessageChain) -> None:
                self.sent_messages.append(message_chain)

        event = SilentProbeEvent()
        event.unified_msg_origin = target_umo
        event.should_call_llm(False)
        return event

    def _page_probe_extract_tool_result(self, tool_result: Any) -> str:
        if tool_result is None:
            return ""
        content = getattr(tool_result, "content", None)
        if isinstance(content, list):
            chunks = []
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    chunks.append(str(text))
            if chunks:
                return "\n".join(chunks)
        return str(tool_result)

    def _page_probe_tool_result_failed(self, result: str) -> bool:
        text = str(result or "").strip().lower()
        if not text:
            return True
        return (
            text.startswith("error:")
            or " execution timeout " in text
            or "生成失败" in text
            or "调用失败" in text
            or "failed" in text
            or "timeout" in text
        )

    async def _page_probe_llm_tool(self, kind: str, body: dict) -> dict:
        from astrbot.core.agent.hooks import BaseAgentRunHooks

        kind = str(kind or "").strip().lower()
        if kind not in {"image", "selfie", "tts", "video"}:
            raise RuntimeError("不支持的 LLM 工具探测类型")

        target_umo = self._page_probe_target_umo(body)
        provider_id = await self._page_probe_provider_id(target_umo)
        system_prompt, prompt = self._page_probe_prompt(kind, body)
        event = self._page_probe_event(target_umo, prompt)
        tools = self._page_probe_toolset(kind)
        calls = []

        class ProbeHooks(BaseAgentRunHooks):
            async def on_tool_start(self, run_context, tool, tool_args):
                calls.append(
                    {
                        "tool_name": str(getattr(tool, "name", "") or ""),
                        "tool_args": dict(tool_args or {}),
                        "result": "",
                        "ended": False,
                    }
                )

            async def on_tool_end(self, run_context, tool, tool_args, tool_result):
                item = calls[-1] if calls else {
                    "tool_name": str(getattr(tool, "name", "") or ""),
                    "tool_args": dict(tool_args or {}),
                    "result": "",
                    "ended": False,
                }
                item["result"] = self_outer._page_probe_extract_tool_result(tool_result)
                item["ended"] = True
                if not calls:
                    calls.append(item)

        self_outer = self
        try:
            response = await self.context.tool_loop_agent(
                event=event,
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                tools=tools,
                max_steps=3,
                tool_call_timeout=int(body.get("tool_call_timeout") or 300),
                agent_hooks=ProbeHooks(),
            )
        except Exception as exc:
            if not calls:
                raise RuntimeError(f"{kind} 校准生成失败: {exc}") from exc
            tool_call = calls[0]
            if not tool_call.get("ended"):
                raise RuntimeError(f"{kind} 校准生成失败: {exc}") from exc
            if self._page_probe_tool_result_failed(tool_call.get("result", "")):
                raise RuntimeError(f"{kind} 校准生成失败: {tool_call.get('result') or exc}") from exc
            response = None

        if not calls:
            raise RuntimeError("LLM 没有调用任何媒体工具，无法记录工具")

        tool_call = calls[0]
        if not tool_call.get("ended"):
            raise RuntimeError("工具调用尚未完成，无法记录工具")
        if self._page_probe_tool_result_failed(tool_call.get("result", "")):
            raise RuntimeError(f"{kind} 校准生成失败: {tool_call.get('result') or '工具没有返回结果'}")
        return {
            "provider_type": kind,
            "tool_name": tool_call["tool_name"],
            "tool_args": tool_call["tool_args"],
            "tool_result": tool_call.get("result", ""),
            "final_text": str(getattr(response, "completion_text", "") or ""),
            "target_umo": target_umo,
            "provider_id": provider_id,
            "sent_count": len(getattr(event, "sent_messages", []) or []),
        }

    def _apply_page_probe_result(self, kind: str, result: dict) -> list[str]:
        tool_args = json.dumps(result.get("tool_args") or {}, ensure_ascii=False)
        if kind in {"image", "selfie"}:
            prefix = "llm_selfie" if kind == "selfie" else "llm_image"
            self.image_conf["image_provider"] = "calibrated_tool"
            self.image_conf[f"{prefix}_tool_name"] = str(result.get("tool_name") or "")
            self.image_conf[f"{prefix}_tool_args"] = tool_args
            self.image_conf[f"{prefix}_tool_provider_id"] = str(result.get("provider_id") or "")
            if kind == "selfie":
                self.image_conf["use_gitee_selfie_ref"] = True
                return ["image_provider", f"{prefix}_tool_name", f"{prefix}_tool_args", f"{prefix}_tool_provider_id", "use_gitee_selfie_ref"]
            return ["image_provider", f"{prefix}_tool_name", f"{prefix}_tool_args", f"{prefix}_tool_provider_id"]
        if kind == "tts":
            self.tts_conf["tts_provider"] = "calibrated_tool"
            self.tts_conf["llm_tts_tool_name"] = str(result.get("tool_name") or "")
            self.tts_conf["llm_tts_tool_args"] = tool_args
            self.tts_conf["llm_tts_tool_provider_id"] = str(result.get("provider_id") or "")
            return ["tts_provider", "llm_tts_tool_name", "llm_tts_tool_args", "llm_tts_tool_provider_id"]
        if kind == "video":
            self.image_conf["video_provider"] = "calibrated_tool"
            self.image_conf["llm_video_tool_name"] = str(result.get("tool_name") or "")
            self.image_conf["llm_video_tool_args"] = tool_args
            self.image_conf["llm_video_tool_provider_id"] = str(result.get("provider_id") or "")
            return ["video_provider", "llm_video_tool_name", "llm_video_tool_args", "llm_video_tool_provider_id"]
        return []

    async def _page_probe_provider(self, kind: str, body: dict) -> dict:
        kind = str(kind or "").strip().lower()
        result = await self._page_probe_llm_tool(kind, body)
        applied = bool(body.get("apply", True))
        applied_fields = self._apply_page_probe_result(kind, result) if applied else []
        if applied:
            await self._save_config_and_refresh_runtime()

        return {
            "ok": True,
            "data": {
                "kind": kind,
                "applied": applied,
                "applied_fields": applied_fields,
                "result": result,
                "config": self._page_config_payload(),
            },
        }

    async def page_provider_probe(self):
        async def handler():
            body = await self._page_json_body()
            return await self._page_probe_provider(body.get("kind"), body)

        return await self._page_json(handler)
