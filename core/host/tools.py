from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class PluginToolMixin:
    """大语言模型工具和相关事件的实际处理逻辑。"""

    async def _daily_share_tool_impl(
        self,
        event: AstrMessageEvent,
        share_type: str,
        source: str = None,
        get_image: bool = True,
        need_image: bool = False,
        need_video: bool = False,
        need_voice: bool = False,
        to_qzone: bool = False,
    ):
        if self._is_terminated:
            return ""

        self._remember_event_adapter(event)
        is_admin = self._is_admin_event(event)
        is_configured_receiver = self._is_configured_receiver_event(event)
        if to_qzone and not is_admin:
            await event.send(event.plain_result("分享到QQ空间仅管理员可用。"))
            return None
        if not (is_admin or is_configured_receiver):
            await event.send(self._plain_permission_denied(event))
            return None

        share_target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if self._is_share_busy(share_target, global_scope=to_qzone):
            await event.send(event.plain_result("正如火如荼地准备中，请稍后..."))
            return None

        self._track_task(
            self.task_manager.async_daily_share_task(
                event,
                share_type,
                source,
                get_image,
                need_image,
                need_video,
                need_voice,
                to_qzone,
            )
        )
        return None

    async def _inject_news_link_context_impl(self, event: AstrMessageEvent, req) -> None:
        try:
            tool_set = getattr(req, "func_tool", None)
            tool_names = tool_set.names() if tool_set and hasattr(tool_set, "names") else []
            if "news_link" not in tool_names:
                return

            system_prompt = str(getattr(req, "system_prompt", "") or "")
            if self._NEWS_LINK_CONTEXT_MARKER in system_prompt:
                return

            prompt = await self._build_news_link_context_prompt(
                getattr(event, "unified_msg_origin", "")
            )
            if not prompt:
                return

            req.system_prompt = f"{system_prompt.rstrip()}\n\n{prompt}\n".lstrip()
        except Exception as e:
            logger.debug(f"[每日分享] 注入新闻链接上下文失败: {e}")

    async def _news_link_tool_impl(
        self,
        event: AstrMessageEvent,
        action: str = "link",
        index: str = "",
        query: str = "",
        source: str = None,
        to_qzone: bool = False,
    ):
        if self._is_terminated:
            return ""

        self._remember_event_adapter(event)
        is_admin = self._is_admin_event(event)
        is_configured_receiver = self._is_configured_receiver_event(event)
        if to_qzone and not is_admin:
            return "QQ空间新闻链接仅管理员可查询。"
        if not (is_admin or is_configured_receiver):
            return "权限不足：当前会话不在接收对象配置中。"

        source_key = self._resolve_news_source_name(source)
        target_uid = "qzone_broadcast" if to_qzone else event.unified_msg_origin
        result = await self.task_manager.get_cached_news_link(
            target_uid,
            action=action,
            index=index,
            query=query,
            source_key=source_key,
            refresh_source=False,
        )
        try:
            event.set_extra("daily_sharing_news_link_used", True)
            urls = self._extract_news_link_urls(result)
            if urls:
                event.set_extra("daily_sharing_news_link_urls", urls)
        except Exception as e:
            logger.debug(f"[每日分享] 标记新闻链接状态失败: {e}")
        return result

    async def _clean_news_link_llm_references_impl(self, event: AstrMessageEvent, resp) -> None:
        try:
            used = event.get_extra("daily_sharing_news_link_used")
        except Exception:
            used = None
        if not used or not resp:
            return

        try:
            original = str(resp.completion_text or "")
            cleaned = self._strip_news_link_reference_tail(original)
            urls = event.get_extra("daily_sharing_news_link_urls", []) or []
            cleaned = self._ensure_news_link_urls_in_reply(cleaned, urls)
            if cleaned != original:
                resp.completion_text = cleaned
                logger.debug("[每日分享] 已清理新闻链接模型回复中的参考链接尾部")
        except Exception as e:
            logger.warning(f"[每日分享] 清理新闻链接模型参考链接失败: {e}")

    async def _clean_news_link_decorating_references_impl(self, event: AstrMessageEvent) -> None:
        try:
            used = event.get_extra("daily_sharing_news_link_used")
        except Exception:
            used = None
        if not used:
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        try:
            original = result.get_plain_text()
            cleaned = self._strip_news_link_reference_tail(original)
            urls = event.get_extra("daily_sharing_news_link_urls", []) or []
            cleaned = self._ensure_news_link_urls_in_reply(cleaned, urls)
            if cleaned != original:
                event.set_result(event.plain_result(cleaned))
                logger.debug("[每日分享] 已在发送前清理新闻链接参考链接尾部")
            event.set_extra("daily_sharing_news_link_used", None)
            event.set_extra("daily_sharing_news_link_urls", None)
        except Exception as e:
            logger.warning(f"[每日分享] 发送前清理新闻链接参考链接失败: {e}")
