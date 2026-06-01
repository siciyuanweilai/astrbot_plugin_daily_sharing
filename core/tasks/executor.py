from .common import *  # noqa: F401,F403


class TaskExecutorMixin:
    """High-level share execution flows."""

    def get_curr_period(self) -> TimePeriod:
        h = datetime.now().hour
        if 0 <= h < 6: return TimePeriod.DAWN
        if 6 <= h < 9: return TimePeriod.MORNING
        if 9 <= h < 12: return TimePeriod.FORENOON
        if 12 <= h < 16: return TimePeriod.AFTERNOON
        if 16 <= h < 19: return TimePeriod.EVENING
        if 19 <= h < 22: return TimePeriod.NIGHT
        return TimePeriod.LATE_NIGHT

    def get_period_range_str(self, period: TimePeriod) -> str:
        """获取时段对应的时间范围字符串"""
        return {
            TimePeriod.DAWN: "00:00-06:00",            
            TimePeriod.MORNING: "06:00-09:00",
            TimePeriod.FORENOON: "09:00-12:00",
            TimePeriod.AFTERNOON: "12:00-16:00",
            TimePeriod.EVENING: "16:00-19:00",
            TimePeriod.NIGHT: "19:00-22:00",
            TimePeriod.LATE_NIGHT: "22:00-24:00"
        }.get(period, "")

    def _strip_emotion_tags(self, content: str) -> str:
        return re.sub(
            r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$',
            '',
            str(content or ''),
            flags=re.IGNORECASE,
        ).strip()

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

    async def decide_type_with_state(self, current_period: TimePeriod, is_qzone: bool = False, target_id: str = None, specific_type: str = "auto") -> SharingType:
        """带目标ID状态的分享类型决定，支持自定义列表轮换"""
        # 获取状态存储的 Key。QQ空间用 "qzone"；普通会话根据 ID 存储独立状态
        if is_qzone:
            state_key = "qzone"
        else:
            state_key = f"target_{target_id}" if target_id else "global"
            
        state = await self.db.get_state(state_key, {})

        # 处理用户填写的逗号自定义序列
        if specific_type and specific_type.lower() != "auto":
            # 兼容中英文字符
            seq_str = specific_type.replace("，", ",")
            custom_seq = [s.strip().lower() for s in seq_str.split(",") if s.strip()]
            
            # 如果解析出来的列表不仅仅只有一个 "auto"
            if custom_seq and custom_seq != ["auto"]:
                idx_key = "custom_sequence_index"
                idx = state.get(idx_key, 0)
                if idx >= len(custom_seq): idx = 0
                
                selected_str = custom_seq[idx]
                next_idx = (idx + 1) % len(custom_seq)
                
                # 保存这个群独立的序列进度
                await self.db.update_state_dict(state_key, {
                    idx_key: next_idx, 
                    "last_timestamp": datetime.now().isoformat()
                })
                
                # 如果当前轮到的单词不是 auto，直接返回该类型
                if selected_str != "auto":
                    try: 
                        return SharingType(selected_str)
                    except ValueError:
                        logger.warning(f"[DailySharing] 自定义序列包含无效分享类型 {selected_str!r}，使用时段序列兜底。")
                
                # 如果轮到的单词刚好是 "auto"，系统会直接无视上面的返回，
                # 顺滑地进入下方的“按当前时间段智能选择”代码块！

        # 原有的按时间段智能判断序列（兜底与 Auto 专用）
        conf_node = self.qzone_conf if is_qzone else self.basic_conf
        
        # 映射序列前缀
        prefix = "qzone_" if is_qzone else ""
        config_key_map = {
            TimePeriod.MORNING: f"{prefix}morning_sequence",
            TimePeriod.FORENOON: f"{prefix}forenoon_sequence",
            TimePeriod.AFTERNOON: f"{prefix}afternoon_sequence",
            TimePeriod.EVENING: f"{prefix}evening_sequence",
            TimePeriod.NIGHT: f"{prefix}night_sequence",
            TimePeriod.LATE_NIGHT: f"{prefix}late_night_sequence",
            TimePeriod.DAWN: f"{prefix}dawn_sequence"
        }
        
        config_key = config_key_map.get(current_period)
        seq = conf_node.get(config_key, [])
        
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])
        
        idx_key = f"index_{current_period.value}"
        idx = state.get(idx_key, 0)
        
        if idx >= len(seq): idx = 0
        selected = seq[idx]
        next_idx = (idx + 1) % len(seq)
        
        updates = {
            "last_period": current_period.value,
            idx_key: next_idx,            
            "sequence_index": next_idx,  
            "last_timestamp": datetime.now().isoformat(),
            "last_type": selected
        }
        await self.db.update_state_dict(state_key, updates)
        
        try:
            return SharingType(selected)
        except ValueError:
            logger.warning(f"[DailySharing] 无效分享类型 {selected!r}，回退到问候。")
            return SharingType.GREETING

    async def async_daily_share_task(
        self,
        event: AstrMessageEvent,
        share_type: str,
        source: str,
        get_image: bool,
        need_image: bool,
        need_video: bool,
        need_voice: bool,
        to_qzone: bool
    ):
        """实际执行分享逻辑的后台任务 (LLM 触发)"""
        if self.plugin._is_terminated:
            return

        share_target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        share_global_scope = bool(to_qzone)
        if hasattr(self.plugin, "_is_share_busy"):
            is_busy = self.plugin._is_share_busy(share_target, global_scope=share_global_scope)
            share_lock = self.plugin._get_share_lock(share_target, global_scope=share_global_scope)
        else:
            is_busy = self._lock.locked()
            share_lock = self._lock

        if is_busy:
            await event.send(event.plain_result("正如火如荼地准备中，请稍后..."))
            return

        lock_acquired = False
        await share_lock.acquire()
        lock_acquired = True
        try:
            # 特殊图片类型处理 (60s / AI) 
            share_type_text = str(share_type or "auto").strip()
            st_clean = share_type_text.lower().replace(" ", "").replace("　", "")
            
            # 60s新闻
            if any(k in st_clean for k in ["60s", "六十秒", "读世界"]):
                url = self.news_service.get_60s_image_url()
                if not url:
                    await event.send(event.plain_result("获取 每天60s读世界 失败，请检查API Key配置。"))
                    return 
                    
                if to_qzone:
                    qzone_plugin = self.ctx_service._find_plugin("qzone")
                    if qzone_plugin and hasattr(qzone_plugin, "service"):
                        try:
                            await self.plugin._safe_publish_qzone(qzone_plugin, text="【每天60秒读懂世界】", images=[url])
                            await event.send(event.plain_result("每天60s读世界 已成功分享到QQ空间！"))
                            await self.db.add_sent_history("qzone_broadcast", "news", "【每天60秒读懂世界】", True)
                        except Exception as e:
                            await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                    else:
                        await event.send(event.plain_result("未检测到QQ空间插件！"))
                else:
                    # 群聊/私聊：强制下载到本地发
                    local_path = await self._download_image_to_local(url, "60s.png")
                    if local_path:
                        await event.send(event.image_result(local_path))
                    else:
                        await event.send(event.plain_result("60s新闻图片下载失败。"))
                return 

            # AI资讯
            if any(k in st_clean for k in ["ai资讯", "ai新闻", "ai日报"]) or st_clean == "ai":
                ai_data = await self.news_service.get_ai_news_json()
                if not ai_data:
                    await event.send(event.plain_result("获取 AI资讯快报 失败，今日暂无更新。"))
                    return 

                url = self.news_service.get_ai_news_image_url()
                if not url:
                    await event.send(event.plain_result("获取 AI资讯快报 图片失败，请检查API Key配置。"))
                    return 
                    
                if to_qzone:
                    qzone_plugin = self.ctx_service._find_plugin("qzone")
                    if qzone_plugin and hasattr(qzone_plugin, "service"):
                        try:
                            await self.plugin._safe_publish_qzone(qzone_plugin, text="【AI资讯快报】", images=[url])
                            await event.send(event.plain_result("AI资讯快报 已成功分享到QQ空间！"))
                            await self.db.add_sent_history("qzone_broadcast", "news", "【AI资讯快报】", True)
                        except Exception as e:
                            await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                    else:
                        await event.send(event.plain_result("未检测到QQ空间插件！"))
                else:
                    # 群聊/私聊：强制下载到本地发
                    local_path = await self._download_image_to_local(url, "ainews.png")
                    if local_path:
                        await event.send(event.image_result(local_path))
                    else:
                        await event.send(event.plain_result("AI资讯快报图片下载失败。"))
                return 

            # === 常规流程 ===
            # 参数清洗与映射
            target_type_enum = None
            
            if st_clean in ("自动", "auto", ""):
                target_type_enum = None  
            else:
                target_type_enum = self._map_share_type_arg(share_type_text)
                if not target_type_enum:
                    await event.send(event.plain_result(f"不支持的分享类型：{share_type_text}。支持：自动, 问候, 新闻, 心情, 知识, 推荐, 60s新闻, AI资讯。"))
                    return

            # 映射新闻源 (中文 -> key)
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
            
            # 逻辑判定：新闻默认发静态图
            is_news = (target_type_enum == SharingType.NEWS)
            
            # 触发静态图发送的条件：
            if is_news and get_image and not need_image and not need_voice and not need_video:
                try:
                    img_url = None
                    src_name = ""
                    actual_source_key = news_src_key
                    # 优先使用指定的源热搜
                    if news_src_key:
                        img_url, src_name = self.news_service.get_hot_news_image_url(news_src_key)
                    else:
                        # 如果没有指定，则随机选择一个已启用的新闻源发送
                        random_src = self.news_service.select_news_source()
                        actual_source_key = random_src
                        img_url, src_name = self.news_service.get_hot_news_image_url(random_src)

                    if img_url:
                        snapshot_data = await self.news_service.get_hot_news(
                            actual_source_key,
                            limit=self.get_news_snapshot_limit(),
                            allow_fallback=False
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
                                    await self.plugin._safe_publish_qzone(qzone_plugin, text=f"【{src_name}】", images=[img_url])
                                    await event.send(event.plain_result(f"[{src_name}] 图片已成功分享到QQ空间！"))
                                    await self.db.add_sent_history("qzone_broadcast", "news", f"【{src_name}】长图(LLM)", True)
                                except Exception as e:
                                    await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                            else:
                                await event.send(event.plain_result("未检测到QQ空间插件！"))
                        else:
                            # 群聊/私聊：强制下载到本地发
                            local_path = await self._download_image_to_local(img_url, "hot_news.png")
                            if local_path:
                                await event.send(event.image_result(local_path))
                            else:
                                await event.send(event.plain_result(f"获取 [{src_name}] 图片下载失败。"))
                    else:
                        await event.send(event.plain_result("获取新闻图片失败。"))
                except Exception as e:
                    logger.error(f"[DailySharing] 获取新闻图片失败: {e}")
                    await event.send(event.plain_result(f"获取新闻图片失败。"))
                
                return

            # 如果用户要求发QQ空间文案说说
            if to_qzone:
                await self.execute_qzone_share(force_type=target_type_enum, news_source=news_src_key, event=event)
                return

            # 场景 B: 标准 LLM 生成流程
            
            # 准备数据
            life_ctx = await self.ctx_service.get_life_context()
            news_data = None
            
            # 初始化 img_path (可能用于存放热搜截图)
            img_path = None
            
            if target_type_enum == SharingType.NEWS:
                # 这里的 news_src_key 如果是 None 会自动选择
                if not news_src_key:
                    news_src_key = self.news_service.select_news_source()
                news_data = await self.news_service.get_hot_news(news_src_key)
                if news_data:
                    news_src_key = news_data[1]
                    await self._cache_news_snapshot_for_targets(target_umo, news_data=news_data)
                else:
                    source_name = NEWS_SOURCE_MAP.get(news_src_key or "", {}).get("name") or "新闻源"
                    await event.send(event.plain_result(f"获取【{source_name}】新闻失败，分享已取消。"))
                    return
                
                # 如果在主流程中且配置允许带上新闻图
                if get_image and not need_image and self.image_conf.get("attach_hot_news_image", True):
                    try:
                        img_path, _ = self.news_service.get_hot_news_image_url(news_src_key)
                        if img_path and news_data:
                            await self._cache_news_snapshot_for_targets(target_umo, source_key=news_data[1], image_url=img_path)
                    except Exception as e:
                        logger.warning(f"[DailySharing] 主流程获取热搜图片失败: {e}")

            # 获取历史
            is_group = self.ctx_service._is_group_chat(target_umo)
            hist_data = await self.ctx_service.get_history_data(target_umo, is_group, event=event)
            hist_prompt = self.ctx_service.format_history_prompt(hist_data, target_type_enum)
            group_info = hist_data.get("group_info")
            life_prompt = self.ctx_service.format_life_context(life_ctx, target_type_enum, is_group, group_info)
            
            # 获取近期动态记忆
            recent_dynamics_str = await self._format_recent_dynamics(uid)

            # 获取昵称
            nickname = self._get_contact_alias(target_umo, event=event)
            if not is_group:
                nickname = nickname or await self._get_onebot_nickname(target_umo, event=event)
                nickname = nickname or self._clean_nickname_candidate(event.get_sender_name(), target_umo, event=event)

            # 生成内容
            content = await self.content_service.generate(
                target_type_enum, period, target_umo, is_group, life_prompt, hist_prompt, news_data, nickname=nickname, recent_dynamics=recent_dynamics_str
            )
            
            if not content:
                await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                return
            
            self.image_service.reset_last_description()

            # ================= 视觉生成逻辑 =================
            video_url = None
            send_img_path = img_path
            should_gen_visual = False
            
            if self.image_conf.get("enable_ai_image", False):
                if need_image or need_video:
                    should_gen_visual = True

            if should_gen_visual:
                # 生成图片 (注意：如果生成了AI图片，会覆盖上面的热搜截图 img_path)
                ai_img_path = await self.image_service.generate_image(content, target_type_enum, life_ctx)
                if ai_img_path:
                    img_path = ai_img_path
                    send_img_path = img_path
                
                if img_path:
                    send_img_path = await self._prepare_image_for_target(target_umo, img_path)
                
                # 生成视频 (如果明确要求视频)
                if img_path and self.image_conf.get("enable_ai_video", False):
                    if need_video:
                        video_url = await self.image_service.generate_video_from_image(img_path, content)

            # ================= 语音生成逻辑 =================
            audio_path = None
            if self.tts_conf.get("enable_tts", False):
                should_gen_voice = False
                if need_voice:
                    should_gen_voice = True
                        
                if should_gen_voice:
                    audio_path = await self.ctx_service.text_to_speech(content, target_umo, target_type_enum, period)

            # 发送 (img_path 可能是热搜截图，也可能是AI画的图)
            await self.send(target_umo, content, send_img_path, audio_path, video_url, event=event)
            
            # 记录上下文
            img_desc = self.image_service.get_last_description()
            await self.ctx_service.record_bot_reply_to_history(target_umo, content, image_desc=img_desc)
            await self.ctx_service.record_to_memos(target_umo, content, img_desc)
                
        except Exception as e:
            logger.error(f"[DailySharing] 异步任务错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"执行出错: {str(e)}"))
        finally:
            if lock_acquired and share_lock.locked():
                share_lock.release()
            if not share_global_scope and hasattr(self.plugin, "_release_idle_share_lock"):
                self.plugin._release_idle_share_lock(share_target)

    async def execute_briefing_share(self, specific_target: str = None):
        """执行早报分享：依次发送开启的 60s 和 AI 资讯"""
        if self.plugin._is_terminated: return
        
        logger.info("[DailySharing] 开始执行早报分享任务")
        
        # 1. 收集需要分享的图片 URL
        images_to_send = [] 
        
        check_60s = self.extra_shares_conf.get("enable_60s_news", False)
        if specific_target: check_60s = True 
        
        if self.extra_shares_conf.get("enable_60s_news", False):
            url = self.news_service.get_60s_image_url()
            if url: 
                local_path = await self._download_image_to_local(url, "briefing_60s.png")
                if local_path: images_to_send.append(("每天60s读世界", url, local_path))

        if self.extra_shares_conf.get("enable_ai_news", False):
            ai_data = await self.news_service.get_ai_news_json()
            if ai_data:
                url = self.news_service.get_ai_news_image_url()
                if url: 
                    local_path = await self._download_image_to_local(url, "briefing_ai.png")
                    if local_path: images_to_send.append(("AI资讯快报", url, local_path))
            else:
                logger.info("[DailySharing] 获取 AI资讯快报 失败，今日暂无更新，跳过分享图片")

        if not images_to_send:
            logger.warning("[DailySharing] 早报任务触发，发现没有开启的早报发送或获取图片失败")
            return

        # 定时早报自动同步到QQ空间
        if specific_target is None and self.extra_shares_conf.get("sync_briefing_to_qzone", False):
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if qzone_plugin and hasattr(qzone_plugin, "service"):
                logger.info("[DailySharing] 分享早报到QQ空间已开启...")
                for name, original_url, local_path in images_to_send:
                    try:
                        title = "【每天60秒读懂世界】" if "60s" in name else "【AI资讯快报】"
                        await self.plugin._safe_publish_qzone(qzone_plugin, text=title, images=[original_url])
                        await self.db.add_sent_history("qzone_broadcast", "news", f"{title}(定时自动)", True)
                        await asyncio.sleep(3) 
                        logger.info(f"[DailySharing] 分享早报 {name} 到QQ空间成功！")
                    except Exception as e:
                        logger.error(f"[DailySharing] 分享早报 {name} 到QQ空间失败: {e}")
            else:
                logger.warning("[DailySharing] 分享早报到QQ空间开启，但未检测到 astrbot_plugin_qzone 插件")

        # 2. 确定目标 (使用全新的独立列表)
        targets = []
        if specific_target:
            targets.append(specific_target)
        else:
            targets = self.get_briefing_targets()
            logger.info(f"[DailySharing] 早报将分享到 {len(targets)} 个目标会话")

        if not targets:
            logger.info("[DailySharing] 未配置任何早报接收目标，已跳过分享。")
            return

        # 3. 分享循环
        for uid in targets:
            if self.plugin._is_terminated: break
            try:
                send_event = None
                for name, original_url, local_path in images_to_send:
                    # 普通会话发送下载到本地的文件
                    msg = MessageChain().file_image(local_path)
                    logger.info(f"[DailySharing] 正在分享 {name} 到 {uid}")
                    await self._send_message_chain(uid, msg, send_event)
                    # 每张图之间间隔 1 秒
                    await asyncio.sleep(1)
                
                # 每个群之间间隔 2 秒
                await asyncio.sleep(2) 
            except Exception as e:
                logger.error(f"[DailySharing] 分享早报到 {uid} 失败: {e}")

    async def execute_share(
        self,
        force_type: SharingType = None,
        news_source: str = None,
        specific_target: str = None,
        event: AstrMessageEvent = None,
    ):
        """执行分享的主流程（支持群聊私聊独立配置与记忆序列）"""
        if self.plugin._is_terminated: return

        period = self.get_curr_period()
        life_ctx = await self.ctx_service.get_life_context()

        targets = []
        
        # 1. 确定分享目标
        if specific_target:
            targets.append(specific_target)
        else:
            # 如果是被全局大定时器唤醒，排除掉那些配置了独立定时的群，绝不打扰它们
            targets = self.get_broadcast_targets(exclude_custom_cron=True)

        if not targets:
            logger.warning("[DailySharing] 未配置接收对象，且未指定目标，请在配置页填写群号或QQ号")
            if event:
                await event.send(event.plain_result("分享失败：未配置接收对象，也没有指定当前会话目标。"))
            return
        abort_on_target_failure = bool(specific_target)

        # 加载并解析带冒号的独立配置
        r_groups = self._parse_targets_config(self.receiver_conf.get("groups", []))
        r_users = self._parse_targets_config(self.receiver_conf.get("users", []))

        for uid in targets:
            if self.plugin._is_terminated: break
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower() or "guild" in uid.lower()
                
                adapter_id, real_id = self.ctx_service._parse_umo(uid)
                
                # 读取该群聊、私聊独立的类型策略配置（默认 fallback 为 global 设定的 sharing_type）
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

                # 优先使用本地昵称映射；QQ/OneBot 可通过接口取备注/昵称。
                nickname = self._get_contact_alias(uid, event=event)
                if not nickname and not is_group:
                    nickname = await self._get_onebot_nickname(uid, event=event)

                target_display = f"{nickname}({uid})" if nickname else uid
                logger.info(f"[DailySharing] 正在为 {target_display} 生成内容... 时段: {period.value}, 类型: {stype.value}")
                
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
                        logger.warning(f"[DailySharing] 获取新闻失败: {source_name} ({current_news_source})")
                        await self.db.add_sent_history(
                            target_id=uid,
                            sharing_type=stype.value,
                            content=f"获取新闻失败: {source_name}",
                            success=False
                        )
                        if event:
                            await event.send(event.plain_result(f"获取【{source_name}】新闻失败，分享已取消。"))
                        if abort_on_target_failure:
                            return
                        continue

                hist_data = await self.ctx_service.get_history_data(uid, is_group, event=event)
                if is_group and "group_info" in hist_data:
                    # 手动触发时通常忽略策略检查，但自动触发时需要检查
                    if not specific_target and not self.ctx_service.check_group_strategy(hist_data["group_info"]):
                        logger.info(f"[DailySharing] 因策略跳过群组 {uid}")
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
                    logger.warning(f"[DailySharing] 内容生成失败 {uid}")
                    await self.db.add_sent_history(
                        target_id=uid,
                        sharing_type=stype.value,
                        content="生成失败 (LLM无响应)",
                        success=False
                    )
                    if event:
                        await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                    if abort_on_target_failure:
                        return
                    continue
                
                self.image_service.reset_last_description()

                # 生成多媒体素材 (图片 & 视频 & 语音) 
                
                # 1. 配图生成逻辑
                img_path = None
                send_img_path = None
                video_url = None
                enable_img_global = self.image_conf.get("enable_ai_image", False)
                img_allowed_types = self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
                
                # 【新闻类型特殊处理】如果未开启AI配图或当前类型不允许AI配图，但这是新闻，且配置允许附带热搜图，尝试把热搜图带上
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
                        logger.warning(f"[DailySharing] 自动任务获取新闻图片失败: {e}")

                if enable_img_global:
                    if stype.value in img_allowed_types:
                        ai_img_path = await self.image_service.generate_image(content, stype, life_ctx)
                        if ai_img_path:
                            # AI 图片覆盖热搜截图
                            img_path = ai_img_path
                        
                        if img_path:
                            send_img_path = await self._prepare_image_for_target(uid, img_path)
                            
                        # 尝试生成视频
                        if img_path and self.image_conf.get("enable_ai_video", False):
                            video_allowed = self.image_conf.get("video_enabled_types", ["greeting", "mood"])
                            if stype.value in video_allowed:
                                video_url = await self.image_service.generate_video_from_image(img_path, content)
                    else:
                         logger.info(f"[DailySharing] 当前类型 {stype.value} 不在配图允许列表，跳过配图。")

                # 2. 语音生成逻辑
                audio_path = None
                enable_tts_global = self.tts_conf.get("enable_tts", False)
                tts_allowed_types = self.tts_conf.get("tts_enabled_types", ["greeting", "mood"])
                
                if enable_tts_global:
                    if stype.value in tts_allowed_types:
                        # 传入 stype 和 period 以确定情感
                        audio_path = await self.ctx_service.text_to_speech(content, uid, stype, period)
                    else:
                        logger.info(f"[DailySharing] 当前类型 {stype.value} 不在语音允许列表，跳过语音。")

                # 手动触发当前会话时使用当前事件；定时任务和其它目标走适配器原生 send_by_session。
                send_event = event if self._event_matches_target(event, uid) else None
                if send_img_path is None:
                    send_img_path = img_path
                sent = await self.send(uid, content, send_img_path, audio_path, video_url, event=send_event)
                if not sent:
                    await self.db.add_sent_history(
                        target_id=uid,
                        sharing_type=stype.value,
                        content="发送失败",
                        success=False
                    )
                    if event:
                        await event.send(event.plain_result("内容已生成，但发送失败，请查看日志或检查平台连接状态。"))
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
                    content=clean_content_for_log[:100] + "...",
                    success=True
                )
                
                await asyncio.sleep(2) 

            except Exception as e:
                logger.error(f"[DailySharing] 处理 {uid} 时出错: {e}")
                import traceback
                logger.error(traceback.format_exc())
                if event:
                    await event.send(event.plain_result(f"分享执行出错: {e}"))
                if abort_on_target_failure:
                    return
                continue

        return

    async def execute_qzone_share(self, force_type: SharingType = None, news_source: str = None, event: AstrMessageEvent = None):
        """完全独立的 QQ 空间执行主流程"""
        if self.plugin._is_terminated: return
        
        try:
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if not qzone_plugin or not hasattr(qzone_plugin, "service"):
                logger.warning("[DailySharing] QQ空间任务触发，但未检测到 astrbot_plugin_qzone 插件")
                if event:
                    await event.send(event.plain_result("未检测到 astrbot_plugin_qzone 插件"))
                return

            self.plugin._inject_qzone_client(qzone_plugin)
            period = self.get_curr_period()
            # 注意这里传入 is_qzone=True，使用独立序列
            stype = force_type if force_type else await self.decide_type_with_state(period, is_qzone=True) 
            logger.info(f"[DailySharing] QQ空间时段: {period.value}, 类型: {stype.value}")

            # 获取生活上下文
            life_ctx = await self.ctx_service.get_life_context()
            news_data = None
            
            # 如果是发新闻，单独获取热搜（支持手动指定源）
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
                    logger.warning(f"[DailySharing] QQ空间获取新闻失败: {source_name} ({actual_source})")
                    await self.db.add_sent_history("qzone_broadcast", "news", f"获取新闻失败: {source_name}", False)
                    if event:
                        await event.send(event.plain_result(f"获取【{source_name}】新闻失败，QQ空间分享已取消。"))
                    return

            # 屏蔽历史记录，使用纯净的提示词让LLM写说说
            qzone_life_prompt = self.ctx_service.format_life_context(life_ctx, stype, False, None)
            qzone_life_prompt += (
                "\n\n【最高优先级覆盖指令】\n"
                "这是一条个人QQ空间社交平台的动态说说\n"
                "当前任务是以纯粹的【个人日记或心情独白】的口吻来写。\n"
                "1. 请以你的人设性格说话，真实自然\n"
                "2. 只能专注描绘自己的状态，就像自己在自言自语一样。"
            )
            
            # 获取近期动态记忆 (QQ空间)
            qzone_recent_dynamics_str = await self._format_recent_dynamics("qzone_broadcast")

            logger.info("[DailySharing] 正在为QQ空间生成文案...")
            qzone_content = await self.content_service.generate(
                stype, period, "qzone_broadcast", False, qzone_life_prompt, "", news_data, nickname="", recent_dynamics=qzone_recent_dynamics_str
            )
            
            if not qzone_content:
                logger.error("[DailySharing] QQ空间文案生成失败")
                if event:
                    await event.send(event.plain_result("QQ空间文案生成失败"))
                return

            # 清洗情感标签
            clean_qzone_content = self._strip_emotion_tags(qzone_content)

            # 处理配图逻辑
            self.image_service.reset_last_description()
            qzone_images = []
            target_local_img = None
            
            enable_img_qzone = self.qzone_conf.get("qzone_enable_image", False)
            enable_img_global = self.image_conf.get("enable_ai_image", False)
            
            # 获取QQ空间配图允许类型，如果没配置，默认复用群聊分享的配置
            qzone_img_allowed_types = self.qzone_conf.get(
                "qzone_image_enabled_types", 
                self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
            )

            if enable_img_qzone and enable_img_global:
                if stype.value in qzone_img_allowed_types:
                    logger.info("[DailySharing] 正在为QQ空间生成配图...")
                    try:
                        new_img_path = await self.image_service.generate_image(clean_qzone_content, stype, life_ctx)
                        if new_img_path:
                            target_local_img = new_img_path
                    except Exception as e:
                        logger.error(f"[DailySharing] QQ空间配图生成失败: {e}")
                else:
                    logger.info(f"[DailySharing] 当前类型 {stype.value} 不在QQ空间配图允许列表，跳过配图。")
            
            # 如果是新闻类型，且没有开启画图，且配置允许附带热搜图，尝试贴热搜图
            if stype == SharingType.NEWS and not target_local_img and self.qzone_conf.get("qzone_attach_hot_news_image", True):
                try:
                    if news_data:
                        img_url, _ = self.news_service.get_hot_news_image_url(news_data[1])
                        target_local_img = img_url
                        snapshot_data = await self.news_service.get_hot_news(
                            news_data[1],
                            limit=self.get_news_snapshot_limit(),
                            allow_fallback=False
                        )
                        await self._cache_news_snapshot_for_targets(
                            "qzone_broadcast",
                            news_data=snapshot_data,
                            source_key=news_data[1],
                            image_url=img_url,
                            event=event,
                        )
                except Exception as e:
                    logger.warning(f"[DailySharing] QQ空间获取新闻配图失败: {e}")

            if target_local_img:
                prepared_image = await self._prepare_qzone_image(target_local_img)
                if prepared_image:
                    qzone_images.append(prepared_image)
                
            await self.plugin._safe_publish_qzone(
                qzone_plugin,
                text=clean_qzone_content,
                images=qzone_images
            )
            logger.info("[DailySharing] 成功分享内容到QQ空间！")
            
            await self.db.add_sent_history(
                target_id="qzone_broadcast",
                sharing_type=stype.value,
                content=clean_qzone_content[:100] + "...",
                success=True
            )
            
            if event:
                try:
                    text_chain = MessageChain().message(clean_qzone_content)
                    await event.send(text_chain)
                    
                    if target_local_img:
                        await asyncio.sleep(1.0) 
                        img_chain = MessageChain()
                        if target_local_img.startswith("http"):
                            img_chain.url_image(target_local_img)
                        else:
                            img_chain.file_image(target_local_img)
                        await event.send(img_chain)
                except Exception as e:
                    logger.error(f"[DailySharing] 同步发送内容到会话失败: {e}")

            return None

        except Exception as e:
            logger.error(f"[DailySharing] 生成并分享到QQ空间失败: {e}")
            if event:
                try:
                    await event.send(event.plain_result(f"生成并分享到QQ空间失败: {e}"))
                except Exception as send_error:
                    logger.debug(f"[DailySharing] 发送QQ空间失败提示失败: {send_error}")
            return
