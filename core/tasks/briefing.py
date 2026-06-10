import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain


class TaskBriefingMixin:
    """60s 与 AI 资讯早报发送流程。"""

    async def _send_command_briefing_image(
        self,
        event: AstrMessageEvent,
        *,
        url: str,
        to_qzone: bool,
        qzone_text: str,
        qzone_success_text: str,
        filename_label: str,
        local_history_text: str,
        local_fail_text: str,
        history_source: str,
    ) -> bool:
        target_id = "qzone_broadcast" if to_qzone else self._event_history_target(event)
        target_label = "" if to_qzone else await self._get_target_display_name(target_id, event=event)
        progress_id = self._start_share_progress(
            source_type=history_source,
            target_id=target_id,
            target_label=target_label,
            share_type="briefing",
            enabled_steps=["image", "send"],
            message=f"准备发送 {filename_label}",
        )
        if to_qzone:
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if not (qzone_plugin and hasattr(qzone_plugin, "service")):
                await event.send(event.plain_result("未检测到 QQ 空间插件！"))
                self._finish_share_progress(progress_id, success=False, message="未检测到 QQ 空间插件")
                return False
            try:
                self._complete_share_progress_step(progress_id, "image", "早报图片已获取")
                self._update_share_progress(progress_id, "send", message="发送到 QQ 空间中")
                await self.plugin._safe_publish_qzone(qzone_plugin, text=qzone_text, images=[url])
                await event.send(event.plain_result(qzone_success_text))
                await self.db.add_sent_history(
                    "qzone_broadcast",
                    "briefing",
                    qzone_text,
                    True,
                    source_type=history_source,
                    **self._image_history_kwargs(url),
                )
                self._finish_share_progress(progress_id, success=True, message="早报分享完成")
                return True
            except Exception as e:
                await event.send(event.plain_result(f"QQ 空间分享失败: {e}"))
                self._finish_share_progress(progress_id, success=False, message="早报分享失败")
                return False

        self._update_share_progress(progress_id, "image", message="下载早报图片中")
        filename = self._build_news_image_filename(url, filename_label)
        local_path = await self._download_image_to_local(url, filename)
        if not local_path:
            await event.send(event.plain_result(local_fail_text))
            self._finish_share_progress(progress_id, success=False, message="早报图片下载失败")
            return False
        self._complete_share_progress_step(progress_id, "image", "早报图片已下载")
        self._update_share_progress(progress_id, "send", message="发送中")
        await event.send(event.image_result(local_path))
        await self.db.add_sent_history(
            self._event_history_target(event),
            "briefing",
            local_history_text,
            True,
            source_type=history_source,
            **self._image_history_kwargs(local_path),
        )
        self._finish_share_progress(progress_id, success=True, message="早报分享完成")
        return True

    async def execute_briefing_share(self, specific_target: str = None, source_type: str = "scheduled"):
        """分享早报：依次发送开启的 60s 和 AI 资讯。"""
        if self.plugin._is_terminated:
            return
        history_source = str(source_type or "scheduled").strip()

        logger.info("[每日分享] 开始分享早报任务")

        images_to_send = []
        progress_target_label = (
            await self._get_target_display_name(specific_target)
            if specific_target
            else ""
        )
        progress_id = self._start_share_progress(
            source_type=history_source,
            target_id=specific_target or "briefing_broadcast",
            target_label=progress_target_label,
            share_type="briefing",
            enabled_steps=["image", "send"],
            message="准备早报",
        )

        self._update_share_progress(progress_id, "image", message="获取早报图片中")
        if self.extra_shares_conf.get("enable_60s_news", False):
            url = self.news_service.get_60s_image_url()
            if url:
                filename = self._build_news_image_filename(url, "每天60s读世界")
                local_path = await self._download_image_to_local(url, filename)
                if local_path:
                    images_to_send.append(("每天60s读世界", url, local_path))

        if self.extra_shares_conf.get("enable_ai_news", False):
            ai_data = await self.news_service.get_ai_news_json()
            if ai_data:
                url = self.news_service.get_ai_news_image_url()
                if url:
                    filename = self._build_news_image_filename(url, "AI资讯快报")
                    local_path = await self._download_image_to_local(url, filename)
                    if local_path:
                        images_to_send.append(("AI资讯快报", url, local_path))
            else:
                logger.info("[每日分享] 获取智能资讯快报失败，今日暂无更新，跳过分享图片")

        if not images_to_send:
            logger.warning("[每日分享] 早报任务触发，发现没有开启的早报发送或获取图片失败")
            self._finish_share_progress(progress_id, success=False, message="未获取到早报图片")
            return
        self._complete_share_progress_step(progress_id, "image", "早报图片已获取")

        if specific_target is None and self.extra_shares_conf.get("sync_briefing_to_qzone", False):
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if qzone_plugin and hasattr(qzone_plugin, "service"):
                logger.info("[每日分享] 分享早报到 QQ 空间已开启...")
                for name, original_url, local_path in images_to_send:
                    title = "【每天60秒读懂世界】" if "60s" in name else "【AI资讯快报】"
                    try:
                        self._update_share_progress(
                            progress_id,
                            "send",
                            message=f"发送{name}到 QQ 空间中",
                        )
                        await self.plugin._safe_publish_qzone(qzone_plugin, text=title, images=[original_url])
                        await self.db.add_sent_history(
                            "qzone_broadcast",
                            "briefing",
                            f"{title}(定时自动)",
                            True,
                            source_type=history_source,
                            **self._image_history_kwargs(original_url),
                        )
                        await asyncio.sleep(3)
                        logger.info(f"[每日分享] 分享早报 {name} 到 QQ 空间成功！")
                    except Exception as e:
                        logger.error(f"[每日分享] 分享早报 {name} 到 QQ 空间失败: {e}")
                        self._fail_share_progress_step(progress_id, "send", f"{name} 发送到 QQ 空间失败")
                        await self.db.add_sent_history(
                            "qzone_broadcast",
                            "briefing",
                            f"{title}(定时自动)失败",
                            False,
                            error_reason=str(e),
                            source_type=history_source,
                            **self._image_history_kwargs(original_url),
                        )
            else:
                logger.warning("[每日分享] 分享早报到 QQ 空间开启，但未检测到 astrbot_plugin_qzone 插件")

        if specific_target:
            targets = [specific_target]
        else:
            targets = self.get_briefing_targets()
            logger.info(f"[每日分享] 早报将分享到 {len(targets)} 个目标会话")

        if not targets:
            logger.info("[每日分享] 未配置任何早报接收目标，已跳过分享。")
            self._finish_share_progress(progress_id, success=True, message="未配置早报接收目标")
            return

        total_targets = len(targets)
        sent_any = False
        for target_index, uid in enumerate(targets, 1):
            if self.plugin._is_terminated:
                break
            try:
                send_event = None
                target_label = await self._get_target_display_name(uid)
                for name, original_url, local_path in images_to_send:
                    msg = MessageChain().file_image(local_path)
                    logger.info(f"[每日分享] 正在分享 {name} 到 {uid}")
                    self._update_share_progress(
                        progress_id,
                        "send",
                        message=f"发送{name}中",
                        extra={
                            "target_id": uid,
                            "target_label": self._progress_target_label(uid, target_label),
                            "total_targets": total_targets,
                            "current_index": target_index,
                        },
                    )
                    await self._send_message_chain(uid, msg, send_event)
                    await self.db.add_sent_history(
                        uid,
                        "briefing",
                        f"【{name}】早报",
                        True,
                        source_type=history_source,
                        **self._image_history_kwargs(local_path),
                    )
                    sent_any = True
                    await asyncio.sleep(1)

                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"[每日分享] 分享早报到 {uid} 失败: {e}")
                self._fail_share_progress_step(progress_id, "send", "早报发送失败")
                await self.db.add_sent_history(
                    uid,
                    "briefing",
                    f"早报发送失败: {e}",
                    False,
                    error_reason=str(e),
                    source_type=history_source,
                )
        self._finish_share_progress(
            progress_id,
            success=sent_any,
            message="早报分享完成" if sent_any else "早报分享失败",
        )
