from .shared import Optional, SharingType, TimePeriod, asyncio, logger, re


class ContextTtsMixin:
    async def _resolve_llm_provider_id(self, target_umo: str = None) -> str:
        configured_provider_id = str(self.llm_conf.get("llm_provider_id", "") or "").strip()
        if configured_provider_id:
            return configured_provider_id

        if target_umo:
            try:
                getter = getattr(self.context, "get_current_chat_provider_id", None)
                if callable(getter):
                    provider_id = await getter(target_umo)
                    if provider_id:
                        return provider_id
            except Exception as e:
                logger.debug(f"[每日分享] 读取会话大语言模型服务提供商失败: {e}")

        try:
            cfg = self.context.get_config()
            if cfg:
                provider_id = cfg.get("provider_settings", {}).get("default_provider_id", "")
                if provider_id:
                    return provider_id
                for provider in cfg.get("provider", []):
                    if provider.get("enable", False) and "chat" in provider.get("provider_type", "chat"):
                        return provider.get("id") or ""
        except Exception as e:
            logger.debug(f"[每日分享] 读取默认大语言模型服务提供商失败: {e}")
        return ""

    async def _agent_analyze_sentiment(self, content: str, sharing_type: SharingType, target_umo: str = None) -> str:
        """
        使用智能体分析文本情感
        """
        if not content: return "neutral"
        
        # 1. 如果内容太短，不浪费调用成本，直接使用简单兜底
        if len(content) < 5: return "neutral"

        # 2. 构造提示词
        system_prompt = """你是一个情感分析专家。
任务：分析文本的情感基调，并从以下列表中选择最匹配的一个标签返回。
标签列表：[happy, sad, angry, neutral, surprise]

定义：
- happy: 开心、兴奋、推荐、积极、治愈、期待、早安
- sad: 难过、遗憾、深夜emo、疲惫、怀念、低落、晚安
- angry: 生气、愤怒、吐槽、不爽、谴责
- surprise: 震惊、不可思议、没想到、吃瓜
- neutral: 客观陈述、平淡、普通问候、科普知识

只输出标签单词，不要任何解释。"""

        user_prompt = f"文本内容：{content[:300]}\n\n请分析情感标签："
        
        try:
            provider_id = await self._resolve_llm_provider_id(target_umo)
            if not provider_id:
                return "neutral"
            
            # 设置较长的超时时间 (15秒)
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    prompt=user_prompt, 
                    system_prompt=system_prompt,
                    chat_provider_id=provider_id
                ),
                timeout=15 
            )
            
            if resp and hasattr(resp, 'completion_text'):
                emotion = resp.completion_text.strip().lower()
                # 清洗结果
                for valid in ["happy", "sad", "angry", "surprise", "neutral"]:
                    if valid in emotion:
                        return valid
                        
        except Exception as e:
            logger.debug(f"[上下文] 情感智能分析超时或出错: {e}，回退到默认逻辑")
        
        # 3. 兜底逻辑（如果智能分析失败）
        if sharing_type == SharingType.RECOMMENDATION: return "happy"
        if sharing_type == SharingType.GREETING: return "happy"
        return "neutral"

    async def text_to_speech(self, text: str, target_umo: str, sharing_type: SharingType = None, period: TimePeriod = None) -> Optional[str]:
        """
        调用语音合成插件将文本转换为语音文件路径。
        """
        self.reset_last_external_tts_delivery()
        # 1. 检查开关
        if not self.tts_conf.get("enable_tts", False):
            return None

        # 个人微信适配器目前不支持发送语音，自动降级为文字。
        if self._is_weixin_platform(target_umo):
            logger.info("[每日分享] 当前平台为个人微信，目前不支持发送语音，跳过语音发送。")
            return None

        # 优先提取情感标签
        target_emotion = "neutral"
        
        # 正则匹配内置情感标签格式。
        emotion_match = re.search(r'\$\$(?:EMO:)?(happy|sad|angry|neutral|surprise)\$\$', text, flags=re.IGNORECASE)
        if emotion_match:
            target_emotion = emotion_match.group(1).lower()
            logger.debug(f"[每日分享] 检测到内置情感标签: {target_emotion}")
        else:
            # 如果没有标签，再尝试智能分析（仅作为后备）
            if sharing_type:
                target_emotion = await self._agent_analyze_sentiment(text, sharing_type, target_umo=target_umo)

        # 3. 文本清洗
        final_text = text
        # 正则替换：彻底清洗文本中可能存在的任何标签，只保留纯文本给语音合成
        final_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', final_text, flags=re.IGNORECASE).strip()
        
        # 5. 调用生成
        try:
            provider = self.tts_provider_manager.select_tts_provider()
            session_state = None

            tts_plugin = None
            if provider == "emotion_router":
                tts_plugin = self._get_tts_plugin_inst()
                if not tts_plugin:
                    logger.warning("[每日分享] 未找到语音合成插件 (astrbot_plugin_tts_emotion_router)，无法生成语音。")
                    return None
            
            if tts_plugin and hasattr(tts_plugin, "_get_session_state"):
                session_state = tts_plugin._get_session_state(target_umo)
                
                # 注入情感
                if target_emotion:
                    if hasattr(session_state, "pending_emotion"):
                        session_state.pending_emotion = target_emotion
                        logger.debug(f"[每日分享] 语音合成注入情绪: {target_emotion}")

            logger.info(f"[每日分享] 正在请求语音合成: {final_text[:20]}... (情绪: {target_emotion})")

            if provider == "generic_plugin":
                return await self.tts_provider_manager.generate_tts_with_generic_plugin(
                    final_text,
                    emotion=target_emotion,
                    target_umo=target_umo,
                    session_state=session_state,
                )

            if provider == "calibrated_tool":
                return await self.tts_provider_manager.generate_tts_with_calibrated_tool(
                    final_text,
                    emotion=target_emotion,
                    target_umo=target_umo,
                    session_state=session_state,
                )
            
            # 调用语音合成处理器的处理方法
            result = await tts_plugin.tts_processor.process(final_text, session_state)

            if result and result.success and result.audio_path:
                logger.info(f"[每日分享] 语音合成成功: {result.audio_path}")
                return str(result.audio_path)
            else:
                logger.warning(f"[每日分享] 语音合成失败: {getattr(result, 'error', '未知错误')}")
                return None

        except Exception as e:
            logger.error(f"[每日分享] 调用语音合成插件出错: {e}")
            return None
