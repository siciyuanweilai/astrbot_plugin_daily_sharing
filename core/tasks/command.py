from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..config import NEWS_SOURCE_MAP, SharingType
from ..reaction import mark_failed, mark_processing, mark_success


class TaskCommandShareMixin:
    """自然语言触发的分享后台任务。"""

    async def async_daily_share_task(
        self,
        event: AstrMessageEvent,
        share_type: str,
        source: str,
        get_image: bool,
        need_image: bool,
        need_video: bool,
        need_voice: bool,
        to_qzone: bool,
    ):
        """自然语言触发的分享后台任务。"""
        if self.plugin._is_terminated:
            return

        feedback_enabled = event is not None
        feedback_success = False
        history_source = "command"

        share_target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        share_global_scope = bool(to_qzone)
        if hasattr(self.plugin, "_is_share_busy"):
            is_busy = self.plugin._is_share_busy(share_target, global_scope=share_global_scope)
            share_lock = self.plugin._get_share_lock(share_target, global_scope=share_global_scope)
        else:
            is_busy = self._lock.locked()
            share_lock = self._lock

        if is_busy:
            if feedback_enabled:
                await mark_failed(event)
            await event.send(event.plain_result("正如火如荼地准备中，请稍后..."))
            return

        lock_acquired = False
        progress_id = ""
        progress_done = False

        def finish_progress(success: bool, message: str) -> None:
            nonlocal progress_done
            if progress_id and not progress_done:
                self._finish_share_progress(progress_id, success=success, message=message)
                progress_done = True

        await share_lock.acquire()
        lock_acquired = True
        try:
            if feedback_enabled:
                await mark_processing(event)

            share_type_text = str(share_type or "auto").strip()
            st_clean = share_type_text.lower().replace(" ", "").replace("　", "")

            if any(k in st_clean for k in ["60s", "六十秒", "读世界"]):
                url = self.news_service.get_60s_image_url()
                if not url:
                    await event.send(event.plain_result("获取每天60s读世界失败，请检查接口密钥配置。"))
                    return

                feedback_success = await self._send_command_briefing_image(
                    event,
                    url=url,
                    to_qzone=to_qzone,
                    qzone_text="【每天60秒读懂世界】",
                    qzone_success_text="每天60s读世界已成功分享到 QQ 空间！",
                    filename_label="每天60s读世界",
                    local_history_text="60s 新闻（自然语言触发）",
                    local_fail_text="60s 新闻图片下载失败。",
                    history_source=history_source,
                )
                return

            if any(k in st_clean for k in ["ai资讯", "ai新闻", "ai日报"]) or st_clean == "ai":
                ai_data = await self.news_service.get_ai_news_json()
                if not ai_data:
                    await event.send(event.plain_result("获取 AI 资讯快报失败，今日暂无更新。"))
                    return

                url = self.news_service.get_ai_news_image_url()
                if not url:
                    await event.send(event.plain_result("获取 AI 资讯快报图片失败，请检查接口密钥配置。"))
                    return

                feedback_success = await self._send_command_briefing_image(
                    event,
                    url=url,
                    to_qzone=to_qzone,
                    qzone_text="【AI资讯快报】",
                    qzone_success_text="AI 资讯快报已成功分享到 QQ 空间！",
                    filename_label="AI资讯快报",
                    local_history_text="AI 资讯（自然语言触发）",
                    local_fail_text="AI 资讯快报图片下载失败。",
                    history_source=history_source,
                )
                return

            target_type_enum = None

            if st_clean in ("自动", "auto", ""):
                target_type_enum = None
            else:
                target_type_enum = self._map_share_type_arg(share_type_text)
                if not target_type_enum:
                    await event.send(event.plain_result(f"不支持的分享类型：{share_type_text}。支持：自动、问候、新闻、心情、知识、推荐、60s 新闻、AI 资讯。"))
                    return

            news_src_key = self._map_news_source_arg(source)

            uid = None
            target_umo = None
            period = None
            if not to_qzone:
                uid = event.get_sender_id()
                if not ":" in str(uid):
                    target_umo = event.unified_msg_origin
                else:
                    target_umo = uid

                period = self.get_curr_period()
                if target_type_enum is None:
                    target_type_enum = await self.decide_type_with_state(
                        period,
                        target_id=target_umo,
                        specific_type="auto",
                    )
                target_is_group = self.ctx_service._is_group_chat(target_umo)
                progress_target_label = await self._get_target_display_name(
                    target_umo,
                    event=event,
                    is_group=target_is_group,
                )
                progress_id = self._start_share_progress(
                    source_type=history_source,
                    target_id=target_umo,
                    target_label=progress_target_label,
                    share_type=target_type_enum,
                    enabled_steps=["content", "image", "video", "audio", "send"],
                    message="准备自然语言分享",
                )

            is_news = target_type_enum == SharingType.NEWS

            if is_news and get_image and not need_image and not need_voice and not need_video:
                if to_qzone and not progress_id:
                    progress_id = self._start_share_progress(
                        source_type=history_source,
                        target_id="qzone_broadcast",
                        share_type=target_type_enum,
                        enabled_steps=["image", "send"],
                        message="准备发送新闻长图",
                    )
                try:
                    self._update_share_progress(progress_id, "image", message="获取新闻长图中")
                    img_url = None
                    src_name = ""
                    actual_source_key = news_src_key
                    if news_src_key:
                        img_url, src_name = self.news_service.get_hot_news_image_url(news_src_key)
                    else:
                        random_src = self.news_service.select_news_source()
                        actual_source_key = random_src
                        img_url, src_name = self.news_service.get_hot_news_image_url(random_src)

                    if img_url:
                        self._complete_share_progress_step(progress_id, "image", "新闻长图已获取")
                        snapshot_data = await self.news_service.get_hot_news(
                            actual_source_key,
                            limit=self.get_news_snapshot_limit(),
                            allow_fallback=False,
                        )
                        await self._cache_news_snapshot_for_targets(
                            "qzone_broadcast" if to_qzone else None,
                            news_data=snapshot_data,
                            source_key=actual_source_key,
                            image_url=img_url,
                            event=event,
                        )

                        if to_qzone:
                            qzone_plugin = self.ctx_service._find_plugin("qzone")
                            if qzone_plugin and hasattr(qzone_plugin, "service"):
                                try:
                                    self._update_share_progress(progress_id, "send", message="发送到 QQ 空间中")
                                    await self.plugin._safe_publish_qzone(qzone_plugin, text=f"【{src_name}】", images=[img_url])
                                    await event.send(event.plain_result(f"[{src_name}] 图片已成功分享到 QQ 空间！"))
                                    await self.db.add_sent_history(
                                        "qzone_broadcast",
                                        "news",
                                        f"【{src_name}】长图（自然语言触发）",
                                        True,
                                        source_type=history_source,
                                        **self._image_history_kwargs(img_url),
                                    )
                                    feedback_success = True
                                    finish_progress(True, "分享完成")
                                except Exception as e:
                                    finish_progress(False, "发送失败")
                                    await event.send(event.plain_result(f"QQ 空间分享失败: {e}"))
                            else:
                                finish_progress(False, "未检测到 QQ 空间插件")
                                await event.send(event.plain_result("未检测到 QQ 空间插件！"))
                        else:
                            filename = self._build_news_image_filename(img_url, src_name)
                            local_path = await self._download_image_to_local(img_url, filename)
                            if local_path:
                                self._update_share_progress(progress_id, "send", message="发送中")
                                await event.send(event.image_result(local_path))
                                await self.db.add_sent_history(
                                    self._event_history_target(event),
                                    "news",
                                    f"{src_name} 热搜长图（自然语言触发）",
                                    True,
                                    source_type=history_source,
                                    **self._image_history_kwargs(local_path),
                                )
                                feedback_success = True
                                finish_progress(True, "分享完成")
                            else:
                                self._fail_share_progress_step(progress_id, "image", "新闻长图下载失败")
                                finish_progress(False, "新闻长图下载失败")
                                await event.send(event.plain_result(f"获取 [{src_name}] 图片下载失败。"))
                    else:
                        self._fail_share_progress_step(progress_id, "image", "获取新闻长图失败")
                        finish_progress(False, "获取新闻长图失败")
                        await event.send(event.plain_result("获取新闻图片失败。"))
                except Exception as e:
                    logger.error(f"[每日分享] 获取新闻图片失败: {e}")
                    finish_progress(False, "获取新闻长图失败")
                    await event.send(event.plain_result("获取新闻图片失败。"))

                return

            if to_qzone:
                feedback_success = bool(
                    await self.execute_qzone_share(
                        force_type=target_type_enum,
                        news_source=news_src_key,
                        event=event,
                        source_type=history_source,
                    )
                )
                return

            life_ctx = await self.ctx_service.get_life_context()
            news_data = None
            img_path = None

            if target_type_enum == SharingType.NEWS:
                if not news_src_key:
                    news_src_key = self.news_service.select_news_source()
                news_data = await self.news_service.get_hot_news(news_src_key)
                if news_data:
                    news_src_key = news_data[1]
                    await self._cache_news_snapshot_for_targets(target_umo, news_data=news_data)
                else:
                    source_name = NEWS_SOURCE_MAP.get(news_src_key or "", {}).get("name") or "新闻源"
                    await event.send(event.plain_result(f"获取【{source_name}】新闻失败，分享已取消。"))
                    finish_progress(False, "获取新闻失败")
                    return

                if get_image and not need_image and self.image_conf.get("attach_hot_news_image", True):
                    try:
                        img_path, _ = self.news_service.get_hot_news_image_url(news_src_key)
                        if img_path and news_data:
                            await self._cache_news_snapshot_for_targets(target_umo, source_key=news_data[1], image_url=img_path)
                    except Exception as e:
                        logger.warning(f"[每日分享] 主流程获取热搜图片失败: {e}")

            is_group = self.ctx_service._is_group_chat(target_umo)
            hist_data = await self.ctx_service.get_history_data(target_umo, is_group, event=event)
            hist_prompt = self.ctx_service.format_history_prompt(hist_data, target_type_enum)
            group_info = hist_data.get("group_info")
            life_prompt = self.ctx_service.format_life_context(life_ctx, target_type_enum, is_group, group_info)

            recent_dynamics_str = await self._format_recent_dynamics(uid)

            nickname = self._get_contact_alias(target_umo, event=event)
            if not is_group:
                nickname = nickname or await self._get_onebot_nickname(target_umo, event=event)
                nickname = nickname or self._clean_nickname_candidate(event.get_sender_name(), target_umo, event=event)

            self._update_share_progress(progress_id, "content", message="文案生成中")
            content = await self.content_service.generate(
                target_type_enum,
                period,
                target_umo,
                is_group,
                life_prompt,
                hist_prompt,
                news_data,
                nickname=nickname,
                recent_dynamics=recent_dynamics_str,
            )

            if not content:
                await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                finish_progress(False, "文案生成失败")
                return
            self._complete_share_progress_step(progress_id, "content", "文案已生成")

            self.image_service.reset_last_description()

            video_url = None
            send_img_path = img_path
            should_gen_visual = False

            if self.image_conf.get("enable_ai_image", False):
                if need_image or need_video:
                    should_gen_visual = True

            if should_gen_visual:
                self._update_share_progress(progress_id, "image", message="配图生成中")
                ai_img_path = await self.image_service.generate_image(content, target_type_enum, life_ctx, target_umo=target_umo)
                if ai_img_path:
                    img_path = ai_img_path
                    send_img_path = img_path
                    self._complete_share_progress_step(progress_id, "image", "配图已生成")
                else:
                    self._fail_share_progress_step(progress_id, "image", "配图生成失败，继续发送文案")

                if img_path:
                    send_img_path = await self._prepare_image_for_target(target_umo, img_path)

                if need_video:
                    if img_path and self.image_conf.get("enable_ai_video", False):
                        self._update_share_progress(progress_id, "video", message="视频生成中")
                        video_url = await self.image_service.generate_video_from_image(img_path, content, target_umo=target_umo)
                        if video_url:
                            self._complete_share_progress_step(progress_id, "video", "视频已生成")
                        else:
                            self._fail_share_progress_step(progress_id, "video", "视频生成失败，继续发送")
                    elif not img_path:
                        self._skip_share_progress_step(progress_id, "video", "缺少配图，跳过视频")
                    else:
                        self._skip_share_progress_step(progress_id, "video", "视频未开启")
                else:
                    self._skip_share_progress_step(progress_id, "video", "未请求视频")
            else:
                self._skip_share_progress_step(progress_id, "image", "未请求配图")
                self._skip_share_progress_step(progress_id, "video", "未请求视频")

            audio_path = None
            if self.tts_conf.get("enable_tts", False):
                should_gen_voice = False
                if need_voice:
                    should_gen_voice = True

                if should_gen_voice:
                    self._update_share_progress(progress_id, "audio", message="语音生成中")
                    audio_path = await self.ctx_service.text_to_speech(content, target_umo, target_type_enum, period)
                    if audio_path:
                        self._complete_share_progress_step(progress_id, "audio", "语音已生成")
                    else:
                        self._fail_share_progress_step(progress_id, "audio", "语音生成失败，继续发送")
                else:
                    self._skip_share_progress_step(progress_id, "audio", "未请求语音")
            else:
                self._skip_share_progress_step(progress_id, "audio", "语音未开启")

            media_result = {}
            self._update_share_progress(progress_id, "send", message="发送中")
            sent = await self.send(
                target_umo,
                content,
                send_img_path,
                audio_path,
                video_url,
                event=event,
                media_result=media_result,
            )
            if not sent:
                await event.send(event.plain_result("内容已生成，但发送失败，请查看日志或检查平台连接状态。"))
                finish_progress(False, "发送失败")
                return

            img_desc = self.image_service.get_last_description()
            await self.ctx_service.record_bot_reply_to_history(target_umo, content, image_desc=img_desc)
            await self.ctx_service.record_to_memos(target_umo, content, img_desc)
            clean_content_for_log = self._strip_emotion_tags(content)
            await self.db.add_sent_history(
                target_id=target_umo,
                sharing_type=target_type_enum.value,
                content=clean_content_for_log,
                success=True,
                source_type=history_source,
                **self._sent_visual_history_kwargs(media_result, img_path, video_url),
            )
            self._log_partial_send_errors(target_umo, media_result)
            await self._notify_partial_send_errors(event, media_result)
            feedback_success = True
            finish_progress(True, "分享完成")

        except Exception as e:
            logger.error(f"[每日分享] 异步任务错误: {e}")
            import traceback

            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"分享出错: {str(e)}"))
            finish_progress(False, "分享出错")
        finally:
            finish_progress(feedback_success, "分享完成" if feedback_success else "分享未完成")
            if feedback_enabled:
                if feedback_success:
                    await mark_success(event)
                else:
                    await mark_failed(event)
            if lock_acquired and share_lock.locked():
                share_lock.release()
            if not share_global_scope and hasattr(self.plugin, "_release_idle_share_lock"):
                self.plugin._release_idle_share_lock(share_target)
