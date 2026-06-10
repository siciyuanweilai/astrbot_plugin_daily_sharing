import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..config import NEWS_SOURCE_MAP, SharingType


class TaskQzoneMixin:
    """QQ 空间独立分享流程。"""

    async def execute_qzone_share(
        self,
        force_type: SharingType = None,
        news_source: str = None,
        event: AstrMessageEvent = None,
        source_type: str = "",
    ) -> bool:
        """完全独立的 QQ 空间分享主流程。"""
        if self.plugin._is_terminated:
            return False
        history_source = str(source_type or ("command" if event else "scheduled")).strip()
        progress_id = ""

        try:
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if not qzone_plugin or not hasattr(qzone_plugin, "service"):
                logger.warning("[每日分享] QQ 空间任务触发，但未检测到 astrbot_plugin_qzone 插件")
                if event:
                    await event.send(event.plain_result("未检测到 astrbot_plugin_qzone 插件"))
                return False

            self.plugin._inject_qzone_client(qzone_plugin)
            period = self.get_curr_period()
            stype = force_type if force_type else await self.decide_type_with_state(period, is_qzone=True)
            logger.info(f"[每日分享] QQ 空间时段: {period.value}, 类型: {stype.value}")
            progress_id = self._start_share_progress(
                source_type=history_source,
                target_id="qzone_broadcast",
                share_type=stype,
                enabled_steps=["content", "image", "send"],
                message="准备分享到 QQ 空间",
            )

            life_ctx = await self.ctx_service.get_life_context()
            news_data = None

            if stype == SharingType.NEWS:
                state = await self.db.get_state("qzone", {})
                last_news_source = state.get("last_news_source")

                actual_source = news_source
                if not actual_source:
                    actual_source = self.news_service.select_news_source(excluded_source=last_news_source)

                news_data = await self.news_service.get_hot_news(actual_source)
                if news_data:
                    await self.db.update_state_dict("qzone", {"last_news_source": news_data[1]})
                    await self._cache_news_snapshot_for_targets("qzone_broadcast", news_data=news_data, event=event)
                else:
                    source_name = NEWS_SOURCE_MAP.get(actual_source or "", {}).get("name") or "新闻源"
                    logger.warning(f"[每日分享] QQ 空间获取新闻失败: {source_name} ({actual_source})")
                    await self.db.add_sent_history(
                        "qzone_broadcast",
                        "news",
                        f"获取新闻失败: {source_name}",
                        False,
                        error_reason=f"获取新闻失败: {source_name}",
                        source_type=history_source,
                    )
                    if event:
                        await event.send(event.plain_result(f"获取【{source_name}】新闻失败，QQ空间分享已取消。"))
                    self._finish_share_progress(progress_id, success=False, message="获取新闻失败")
                    return False

            qzone_life_prompt = self.ctx_service.format_life_context(life_ctx, stype, False, None)
            qzone_life_prompt += (
                "\n\n【最高优先级覆盖指令】\n"
                "这是一条个人QQ空间社交平台的动态说说\n"
                "当前任务是以纯粹的【个人日记或心情独白】的口吻来写。\n"
                "1. 请以你的人设性格说话，真实自然\n"
                "2. 只能专注描绘自己的状态，就像自己在自言自语一样。"
            )

            qzone_recent_dynamics_str = await self._format_recent_dynamics("qzone_broadcast")

            logger.info("[每日分享] 正在为 QQ 空间生成文案...")
            self._update_share_progress(progress_id, "content", message="QQ 空间文案生成中")
            qzone_content = await self.content_service.generate(
                stype,
                period,
                "qzone_broadcast",
                False,
                qzone_life_prompt,
                "",
                news_data,
                nickname="",
                recent_dynamics=qzone_recent_dynamics_str,
            )

            if not qzone_content:
                logger.error("[每日分享] QQ 空间文案生成失败")
                if event:
                    await event.send(event.plain_result("QQ空间文案生成失败"))
                self._finish_share_progress(progress_id, success=False, message="文案生成失败")
                return False
            self._complete_share_progress_step(progress_id, "content", "文案已生成")

            clean_qzone_content = self._strip_emotion_tags(qzone_content)

            self.image_service.reset_last_description()
            qzone_images = []
            target_local_img = None

            enable_img_qzone = self.qzone_conf.get("qzone_enable_image", False)
            enable_img_global = self.image_conf.get("enable_ai_image", False)

            qzone_img_allowed_types = self.qzone_conf.get(
                "qzone_image_enabled_types",
                self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"]),
            )

            if enable_img_qzone and enable_img_global:
                if stype.value in qzone_img_allowed_types:
                    logger.info("[每日分享] 正在为 QQ 空间生成配图...")
                    self._update_share_progress(progress_id, "image", message="QQ 空间配图生成中")
                    try:
                        new_img_path = await self.image_service.generate_image(
                            clean_qzone_content,
                            stype,
                            life_ctx,
                            target_umo="qzone_broadcast",
                        )
                        if new_img_path:
                            target_local_img = new_img_path
                            self._complete_share_progress_step(progress_id, "image", "配图已生成")
                        else:
                            self._fail_share_progress_step(progress_id, "image", "配图生成失败，继续发送")
                    except Exception as e:
                        logger.error(f"[每日分享] QQ 空间配图生成失败: {e}")
                        self._fail_share_progress_step(progress_id, "image", "配图生成失败，继续发送")
                else:
                    logger.info(f"[每日分享] 当前类型 {stype.value} 不在 QQ 空间配图允许列表，跳过配图。")
                    self._skip_share_progress_step(progress_id, "image", "当前类型未开启配图")
            else:
                self._skip_share_progress_step(progress_id, "image", "配图未开启")

            if stype == SharingType.NEWS and not target_local_img and self.qzone_conf.get("qzone_attach_hot_news_image", True):
                try:
                    if news_data:
                        self._update_share_progress(progress_id, "image", message="获取新闻配图中")
                        img_url, _ = self.news_service.get_hot_news_image_url(news_data[1])
                        target_local_img = img_url
                        if target_local_img:
                            self._complete_share_progress_step(progress_id, "image", "新闻配图已获取")
                        snapshot_data = await self.news_service.get_hot_news(
                            news_data[1],
                            limit=self.get_news_snapshot_limit(),
                            allow_fallback=False,
                        )
                        await self._cache_news_snapshot_for_targets(
                            "qzone_broadcast",
                            news_data=snapshot_data,
                            source_key=news_data[1],
                            image_url=img_url,
                            event=event,
                        )
                except Exception as e:
                    logger.warning(f"[每日分享] QQ 空间获取新闻配图失败: {e}")
                    self._fail_share_progress_step(progress_id, "image", "新闻配图获取失败，继续发送")

            if target_local_img:
                prepared_image = await self._prepare_qzone_image(target_local_img)
                if prepared_image:
                    qzone_images.append(prepared_image)

            self._update_share_progress(progress_id, "send", message="发送到 QQ 空间中")
            await self.plugin._safe_publish_qzone(
                qzone_plugin,
                text=clean_qzone_content,
                images=qzone_images,
            )
            logger.info("[每日分享] 成功分享内容到 QQ 空间！")

            await self.db.add_sent_history(
                target_id="qzone_broadcast",
                sharing_type=stype.value,
                content=clean_qzone_content,
                success=True,
                source_type=history_source,
                **self._image_history_kwargs(target_local_img),
            )

            if event:
                try:
                    await self._sync_qzone_result_to_event(event, clean_qzone_content, target_local_img)
                except Exception as e:
                    logger.error(f"[每日分享] 同步发送内容到会话失败: {e}")

            self._finish_share_progress(progress_id, success=True, message="QQ 空间分享完成")
            return True

        except Exception as e:
            logger.error(f"[每日分享] 生成并分享到 QQ 空间失败: {e}")
            try:
                await self.db.add_sent_history(
                    "qzone_broadcast",
                    locals().get("stype", SharingType.GREETING).value,
                    f"生成并分享到QQ空间失败: {e}",
                    False,
                    error_reason=str(e),
                    source_type=history_source,
                )
            except Exception as record_error:
                logger.debug(f"[每日分享] 记录 QQ 空间失败历史失败: {record_error}")
            if event:
                try:
                    await event.send(event.plain_result(f"生成并分享到QQ空间失败: {e}"))
                except Exception as send_error:
                    logger.debug(f"[每日分享] 发送 QQ 空间失败提示失败: {send_error}")
            self._finish_share_progress(progress_id, success=False, message="QQ 空间分享失败")
            return False
