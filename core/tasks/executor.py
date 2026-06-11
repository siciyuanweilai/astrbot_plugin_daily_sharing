import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..config import NEWS_SOURCE_MAP, SharingType


class TaskExecutorMixin:
    """分享主流程。"""

    async def execute_share(
        self,
        force_type: SharingType = None,
        news_source: str = None,
        specific_target: str = None,
        event: AstrMessageEvent = None,
        target_scope: str = "all",
        source_type: str = "",
    ):
        """分享主流程（支持群聊私聊独立配置与记忆序列）。"""
        if self.plugin._is_terminated: return
        history_source = str(source_type or ("command" if event else "scheduled")).strip()

        period = self.get_curr_period()
        life_ctx = await self.ctx_service.get_life_context()

        targets = []
        
        # 1. 确定分享目标
        if specific_target:
            targets.append(specific_target)
        else:
            # 如果是被全局大定时器唤醒，排除掉那些配置了独立定时的群，绝不打扰它们
            targets = self.get_broadcast_targets(
                exclude_custom_cron=True,
                target_scope=target_scope,
            )

        if not targets:
            logger.warning("[每日分享] 未配置接收对象，且未指定目标，请在配置页填写群号或 QQ 号")
            if event:
                await event.send(event.plain_result("分享失败：未配置接收对象，也没有指定当前会话目标。"))
            return
        abort_on_target_failure = bool(specific_target)

        # 加载并解析带冒号的独立配置
        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

        total_targets = len(targets)
        for target_index, uid in enumerate(targets, 1):
            if self.plugin._is_terminated: break
            progress_id = ""
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower() or "guild" in uid.lower()
                
                adapter_id, real_id = self.ctx_service._parse_umo(uid)
                
                # 读取该群聊、私聊独立的类型策略配置（默认兜底为全局分享类型）
                target_specific_type = self.basic_conf.get("sharing_type", "auto")
                conf = self._get_target_conf(uid, is_group, r_groups, r_users)
                if conf is not None:
                    st = conf.get("seq") if isinstance(conf, dict) else conf
                    if st is not None: target_specific_type = st

                # 为该目标决定当前的分享类型
                if force_type:
                    stype = force_type
                else:
                    stype = await self.decide_type_with_state(period, is_qzone=False, target_id=uid, specific_type=target_specific_type)

                # 展示名用于日志/进度；私聊昵称才参与内容里的对象识别。
                target_label = await self._get_target_display_name(uid, event=event, is_group=is_group)
                nickname = "" if is_group else target_label

                target_display = f"{target_label}({uid})" if target_label else uid
                logger.info(f"[每日分享] 正在为 {target_display} 生成内容... 时段: {period.value}, 类型: {stype.value}")
                progress_id = self._start_share_progress(
                    source_type=history_source,
                    target_id=uid,
                    target_label=target_label,
                    share_type=stype,
                    total_targets=total_targets,
                    current_index=target_index,
                    enabled_steps=["content", "image", "video", "audio", "send"],
                    message=f"准备为 {target_label or real_id or uid} 生成内容",
                )
                
                # 独立获取该目标的新闻数据与去重
                news_data = None
                if stype == SharingType.NEWS:
                    state = await self.db.get_state(f"target_{uid}", {})
                    last_news_source = state.get("last_news_source")
                    
                    current_news_source = news_source
                    if not current_news_source:
                        current_news_source = self.news_service.select_news_source(excluded_source=last_news_source)
                        
                    news_data = await self.news_service.get_hot_news(current_news_source)
                    if news_data:
                        await self.db.update_state_dict(f"target_{uid}", {"last_news_source": news_data[1]})
                        await self._cache_news_snapshot_for_targets(uid, news_data=news_data)
                    else:
                        source_name = NEWS_SOURCE_MAP.get(current_news_source or "", {}).get("name") or "新闻源"
                        logger.warning(f"[每日分享] 获取新闻失败: {source_name} ({current_news_source})")
                        await self.db.add_sent_history(
                            target_id=uid,
                            sharing_type=stype.value,
                            content=f"获取新闻失败: {source_name}",
                            success=False,
                            error_reason=f"获取新闻失败: {source_name}",
                            source_type=history_source,
                        )
                        if event:
                            await event.send(event.plain_result(f"获取【{source_name}】新闻失败，分享已取消。"))
                        self._finish_share_progress(progress_id, success=False, message="获取新闻失败")
                        if abort_on_target_failure:
                            return
                        continue

                self._update_share_progress(progress_id, "content", message="文案生成中")
                hist_data = await self.ctx_service.get_history_data(uid, is_group, event=event)
                if is_group and "group_info" in hist_data:
                    # 手动触发时通常忽略策略检查，但自动触发时需要检查
                    if not specific_target and not self.ctx_service.check_group_strategy(hist_data["group_info"]):
                        logger.info(f"[每日分享] 因策略跳过群组 {uid}")
                        self._finish_share_progress(progress_id, success=True, message="已按群策略跳过")
                        continue

                hist_prompt = self.ctx_service.format_history_prompt(hist_data, stype)
                group_info = hist_data.get("group_info")
                life_prompt = self.ctx_service.format_life_context(life_ctx, stype, is_group, group_info)

                # 获取近期动态记忆
                recent_dynamics_str = await self._format_recent_dynamics(uid)

                content = await self.content_service.generate(
                    stype, period, uid, is_group, life_prompt, hist_prompt, news_data, nickname=nickname, recent_dynamics=recent_dynamics_str
                )
                
                if not content:
                    logger.warning(f"[每日分享] 内容生成失败 {uid}")
                    await self.db.add_sent_history(
                        target_id=uid,
                        sharing_type=stype.value,
                        content="生成失败（大语言模型无响应）",
                        success=False,
                        error_reason="生成失败（大语言模型无响应）",
                        source_type=history_source,
                    )
                    if event:
                        await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                    self._finish_share_progress(progress_id, success=False, message="文案生成失败")
                    if abort_on_target_failure:
                        return
                    continue
                self._complete_share_progress_step(progress_id, "content", "文案已生成")
                
                self.image_service.reset_last_description()

                # 生成多媒体素材 (图片 & 视频 & 语音) 
                
                # 1. 配图生成逻辑
                img_path = None
                send_img_path = None
                video_url = None
                enable_img_global = self.image_conf.get("enable_ai_image", False)
                img_allowed_types = self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
                
                # 新闻类型特殊处理：如果未开启智能配图或当前类型不允许智能配图，但这是新闻，且配置允许附带热搜图，尝试把热搜图带上。
                if stype == SharingType.NEWS and self.image_conf.get("attach_hot_news_image", True):
                    try:
                        # 查找独立目标对应的上一个新闻源
                        state = await self.db.get_state(f"target_{uid}", {})
                        last_source = state.get("last_news_source")
                        if last_source:
                            img_path, _ = self.news_service.get_hot_news_image_url(last_source)
                            if img_path:
                                await self._cache_news_snapshot_for_targets(uid, source_key=last_source, image_url=img_path)
                    except Exception as e:
                        logger.warning(f"[每日分享] 自动任务获取新闻图片失败: {e}")

                if enable_img_global:
                    if stype.value in img_allowed_types:
                        self._update_share_progress(progress_id, "image", message="配图生成中")
                        ai_img_path = await self.image_service.generate_image(content, stype, life_ctx, target_umo=uid)
                        if ai_img_path:
                            # 智能配图覆盖热搜截图。
                            img_path = ai_img_path
                            self._complete_share_progress_step(progress_id, "image", "配图已生成")
                        elif self._calibrated_delivery_succeeded(self.image_service, "image"):
                            self._complete_share_progress_step(progress_id, "image", "配图已由校准工具发送")
                        else:
                            self._fail_share_progress_step(progress_id, "image", "配图生成失败，继续发送文案")
                        
                        if img_path:
                            send_img_path = await self._prepare_image_for_target(uid, img_path)
                            
                        # 尝试生成视频
                        if img_path and self.image_conf.get("enable_ai_video", False):
                            video_allowed = self.image_conf.get("video_enabled_types", ["greeting", "mood"])
                            if stype.value in video_allowed:
                                self._update_share_progress(progress_id, "video", message="视频生成中")
                                video_url = await self.image_service.generate_video_from_image(img_path, content, target_umo=uid)
                                if video_url:
                                    self._complete_share_progress_step(progress_id, "video", "视频已生成")
                                elif self._calibrated_delivery_succeeded(self.image_service, "video"):
                                    self._complete_share_progress_step(progress_id, "video", "视频已由校准工具发送")
                                else:
                                    self._fail_share_progress_step(progress_id, "video", "视频生成失败，继续发送")
                            else:
                                self._skip_share_progress_step(progress_id, "video", "当前类型未开启视频")
                        else:
                            self._skip_share_progress_step(progress_id, "video", "未生成视频")
                    else:
                        logger.info(f"[每日分享] 当前类型 {stype.value} 不在配图允许列表，跳过配图。")
                        self._skip_share_progress_step(progress_id, "image", "当前类型未开启配图")
                        self._skip_share_progress_step(progress_id, "video", "未生成视频")
                else:
                    self._skip_share_progress_step(progress_id, "image", "配图未开启")
                    self._skip_share_progress_step(progress_id, "video", "视频未开启")

                # 2. 语音生成逻辑
                audio_path = None
                enable_tts_global = self.tts_conf.get("enable_tts", False)
                tts_allowed_types = self.tts_conf.get("tts_enabled_types", ["greeting", "mood"])
                
                if enable_tts_global:
                    if stype.value in tts_allowed_types:
                        # 传入分享类型和时段以确定情感
                        self._update_share_progress(progress_id, "audio", message="语音生成中")
                        audio_path = await self.ctx_service.text_to_speech(content, uid, stype, period)
                        if audio_path:
                            self._complete_share_progress_step(progress_id, "audio", "语音已生成")
                        elif self._calibrated_tts_delivery_succeeded():
                            self._complete_share_progress_step(progress_id, "audio", "语音已由校准工具发送")
                        else:
                            self._fail_share_progress_step(progress_id, "audio", "语音生成失败，继续发送")
                    else:
                        logger.info(f"[每日分享] 当前类型 {stype.value} 不在语音允许列表，跳过语音。")
                        self._skip_share_progress_step(progress_id, "audio", "当前类型未开启语音")
                else:
                    self._skip_share_progress_step(progress_id, "audio", "语音未开启")

                # 手动触发当前会话时使用当前事件；定时任务和其它目标走适配器原生会话发送。
                send_event = event if self._event_matches_target(event, uid) else None
                if send_img_path is None:
                    send_img_path = img_path
                media_result = {}
                self._update_share_progress(progress_id, "send", message="发送中")
                sent = await self.send(
                    uid,
                    content,
                    send_img_path,
                    audio_path,
                    video_url,
                    event=send_event,
                    media_result=media_result,
                )
                if not sent:
                    await self.db.add_sent_history(
                        target_id=uid,
                        sharing_type=stype.value,
                        content="发送失败",
                        success=False,
                        error_reason="发送失败",
                        source_type=history_source,
                        **self._sent_visual_history_kwargs(media_result, send_img_path, video_url),
                    )
                    if event:
                        await event.send(event.plain_result("内容已生成，但发送失败，请查看日志或检查平台连接状态。"))
                    self._finish_share_progress(progress_id, success=False, message="发送失败")
                    if abort_on_target_failure:
                        return
                    continue
                
                # 获取图片描述并写入 AstrBot 聊天上下文
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_bot_reply_to_history(uid, content, image_desc=img_desc)

                # 记录与历史
                await self.ctx_service.record_to_memos(uid, content, img_desc)

                # 清洗历史记录内容中的情感标签
                clean_content_for_log = self._strip_emotion_tags(content)

                await self.db.add_sent_history(
                    target_id=uid,
                    sharing_type=stype.value,
                    content=clean_content_for_log,
                    success=True,
                    source_type=history_source,
                    **self._sent_visual_history_kwargs(media_result, send_img_path or img_path, video_url),
                )
                self._log_partial_send_errors(uid, media_result)
                if event and send_event:
                    await self._notify_partial_send_errors(event, media_result)
                self._finish_share_progress(progress_id, success=True, message="分享完成")
                
                await asyncio.sleep(2) 

            except Exception as e:
                logger.error(f"[每日分享] 处理 {uid} 时出错: {e}")
                import traceback
                logger.error(traceback.format_exc())
                if event:
                    await event.send(event.plain_result(f"分享出错: {e}"))
                await self.db.add_sent_history(
                    target_id=uid,
                    sharing_type=locals().get("stype", SharingType.GREETING).value,
                    content=f"分享出错: {e}",
                    success=False,
                    error_reason=str(e),
                    source_type=history_source,
                )
                self._finish_share_progress(progress_id, success=False, message="分享出错")
                if abort_on_target_failure:
                    return
                continue

        return
