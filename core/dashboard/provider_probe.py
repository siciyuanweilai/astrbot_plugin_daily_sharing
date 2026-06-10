import asyncio
import contextlib
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

    async def _page_probe_llm_tool(self, kind: str, body: dict) -> dict:
        from astrbot.core.agent.hooks import BaseAgentRunHooks

        kind = str(kind or "").strip().lower()
        if kind not in {"image", "selfie", "tts"}:
            raise RuntimeError("不支持的 LLM 工具探测类型")

        target_umo = self._page_probe_target_umo(body)
        provider_id = await self._page_probe_provider_id(target_umo)
        system_prompt, prompt = self._page_probe_prompt(kind, body)
        event = self._page_probe_event(target_umo, prompt)
        tools = self._page_probe_toolset(kind)
        calls = []
        first_tool_started = asyncio.Event()

        class ProbeHooks(BaseAgentRunHooks):
            async def on_tool_start(self, run_context, tool, tool_args):
                calls.append(
                    {
                        "tool_name": str(getattr(tool, "name", "") or ""),
                        "tool_args": dict(tool_args or {}),
                        "result": "",
                    }
                )
                first_tool_started.set()

            async def on_tool_end(self, run_context, tool, tool_args, tool_result):
                item = calls[-1] if calls else {
                    "tool_name": str(getattr(tool, "name", "") or ""),
                    "tool_args": dict(tool_args or {}),
                    "result": "",
                }
                item["result"] = self_outer._page_probe_extract_tool_result(tool_result)
                if not calls:
                    calls.append(item)

        self_outer = self
        response = None
        probe_error = ""
        selection_timeout = int(body.get("selection_timeout") or 90)
        result_grace_seconds = int(body.get("result_grace_seconds") or 3)
        agent_task = asyncio.create_task(
            self.context.tool_loop_agent(
                event=event,
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                tools=tools,
                max_steps=3,
                tool_call_timeout=int(body.get("tool_call_timeout") or 300),
                agent_hooks=ProbeHooks(),
            )
        )
        try:
            start_task = asyncio.create_task(first_tool_started.wait())
            done, _pending = await asyncio.wait(
                {start_task, agent_task},
                timeout=selection_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                agent_task.cancel()
                start_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
                with contextlib.suppress(asyncio.CancelledError):
                    await start_task
                raise RuntimeError("LLM 未在限定时间内调用媒体工具，无法记录工具")
            if agent_task in done and not calls:
                with contextlib.suppress(BaseException):
                    response = agent_task.result()
                if not calls:
                    raise RuntimeError("LLM 没有调用任何媒体工具，无法记录工具")
            start_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await start_task
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(agent_task),
                    timeout=result_grace_seconds,
                )
            except asyncio.TimeoutError:
                probe_error = "已捕获工具调用，未等待媒体生成完成"
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
        except asyncio.TimeoutError:
            if calls:
                probe_error = "已捕获工具调用，等待工具结果超时"
            else:
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
                raise RuntimeError("LLM 未在限定时间内调用媒体工具，无法记录工具")
        except Exception as exc:
            if not agent_task.done():
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
            probe_error = str(exc) or type(exc).__name__
            if not calls:
                raise
        finally:
            if calls and not agent_task.done():
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
        if agent_task.done() and not agent_task.cancelled() and not response:
            with contextlib.suppress(Exception):
                response = agent_task.result()
        if not calls:
            raise RuntimeError("LLM 没有调用任何媒体工具，无法记录工具")

        tool_call = calls[0]
        return {
            "provider_type": kind,
            "tool_name": tool_call["tool_name"],
            "tool_args": tool_call["tool_args"],
            "tool_result": tool_call.get("result", ""),
            "final_text": str(getattr(response, "completion_text", "") or ""),
            "probe_error": probe_error,
            "target_umo": target_umo,
            "provider_id": provider_id,
            "sent_count": len(getattr(event, "sent_messages", []) or []),
        }

    def _apply_page_probe_result(self, kind: str, result: dict) -> list[str]:
        if kind in {"image", "selfie"}:
            prefix = "llm_selfie" if kind == "selfie" else "llm_image"
            self.image_conf[f"{prefix}_tool_name"] = str(result.get("tool_name") or "")
            self.image_conf[f"{prefix}_tool_args"] = result.get("tool_args") or {}
            self.image_conf[f"{prefix}_tool_provider_id"] = str(result.get("provider_id") or "")
            if kind == "selfie":
                self.image_conf["use_gitee_selfie_ref"] = True
                return [f"{prefix}_tool_name", f"{prefix}_tool_args", f"{prefix}_tool_provider_id", "use_gitee_selfie_ref"]
            return [f"{prefix}_tool_name", f"{prefix}_tool_args", f"{prefix}_tool_provider_id"]
        if kind == "tts":
            self.tts_conf["llm_tts_tool_name"] = str(result.get("tool_name") or "")
            self.tts_conf["llm_tts_tool_args"] = result.get("tool_args") or {}
            self.tts_conf["llm_tts_tool_provider_id"] = str(result.get("provider_id") or "")
            return ["llm_tts_tool_name", "llm_tts_tool_args", "llm_tts_tool_provider_id"]
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
