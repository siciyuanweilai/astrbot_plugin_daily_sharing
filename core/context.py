import datetime
import time
import re
import json
import asyncio
from typing import Optional, Dict, Any, List
from astrbot.api import logger
from ..config import SharingType, TimePeriod
from .platform import (
    find_platform_instance_by_keywords,
    get_platform_client,
    get_platform_id,
    get_platform_meta,
    get_platform_type,
    is_weixin_oc_instance,
    iter_platform_instances,
    platform_match_text,
)

DAILY_SHARING_INTERNAL_TRIGGER = "愿此见闻悄然为我启封"
DAILY_SHARING_MEMORY_PROMPT = "每日分享记录"
DAILY_SHARING_SOURCE = "daily_sharing"


class ContextService:
    def __init__(self, context_obj, config):
        self.context = context_obj
        self.config = config
        self.bot_map = {} 

        self._life_plugin = None
        self._memos_plugin = None
        self._tts_plugin = None
        
        unified_conf = self.config.get("context_conf", {})
        
        self.life_conf = unified_conf
        self.history_conf = unified_conf
        self.memory_conf = unified_conf

        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {}) 
        self.llm_conf = self.config.get("llm_conf", {}) 

    # ==================== 基础辅助方法 ====================

    def _find_plugin(self, keyword: str):
        """按 AstrBot 插件元数据查找已加载实例。"""
        try:
            plugins = self.context.get_all_stars()
            
            for plugin in plugins:
                p_id = getattr(plugin, "root_dir_name", "") or getattr(plugin, "module_path", "") or ""
                p_name = getattr(plugin, "name", "") or ""
                display_name = getattr(plugin, "display_name", "") or ""
                
                if (keyword in p_id) or (keyword in p_name) or (keyword in display_name):
                    return getattr(plugin, "star_cls", None)
                    
        except Exception as e:
            logger.warning(f"[上下文] 查找插件 '{keyword}' 错误: {e}")
        return None

    def _get_memos_plugin(self):
        """获取 Memos 插件"""
        if not self._memos_plugin:
            self._memos_plugin = self._find_plugin("memos")
        return self._memos_plugin

    def _get_tts_plugin_inst(self):
        """获取 TTS 插件实例"""
        if not self._tts_plugin:
            self._tts_plugin = self._find_plugin("tts_emotion")
        return self._tts_plugin

    def _is_group_chat(self, target_umo: str) -> bool:
        """判断是否为群聊"""
        try:
            if not target_umo or not isinstance(target_umo, str):
                return False
            
            parts = target_umo.split(':')
            if len(parts) < 2:
                return False
            
            message_type = parts[1].lower()
            group_keywords = ['group', 'guild', 'channel', 'room']
            return any(keyword in message_type for keyword in group_keywords)
        except Exception as e:
            return False

    def _parse_umo(self, target_umo: str):
        """解析 UMO ID"""
        try:
            parts = target_umo.split(':')
            if len(parts) >= 3:
                return parts[0], ":".join(parts[2:])
            return None, None
        except Exception as e:
            logger.debug(f"[DailySharing] 解析 UMO 失败: {e}")
            return None, None

    def _is_onebot_platform(self, adapter_id: str) -> bool:
        if not adapter_id:
            return False
        adapter = adapter_id.lower()
        return "aiocqhttp" in adapter or "onebot" in adapter

    def _is_onebot_event(self, event) -> bool:
        try:
            return self._is_onebot_platform(event.get_platform_name())
        except Exception:
            return False

    def _iter_platform_instances(self) -> List[Any]:
        return iter_platform_instances(self.context)

    def _get_platform_meta(self, inst):
        return get_platform_meta(inst)

    def _get_platform_id(self, inst) -> str:
        return get_platform_id(inst)

    def _get_platform_type(self, inst) -> str:
        return get_platform_type(inst)

    def _get_platform_client(self, inst):
        return get_platform_client(inst)

    def _platform_match_text(self, inst) -> str:
        return platform_match_text(inst)

    def _find_platform_instance_by_keywords(self, keywords: List[str]):
        return find_platform_instance_by_keywords(self.context, keywords)

    def _is_weixin_oc_instance(self, inst) -> bool:
        return is_weixin_oc_instance(inst)

    def _get_history_max_count(self, is_group: bool, group_default: int = 50, private_default: int = 20) -> int:
        default = group_default if is_group else private_default
        key = "deep_history_max_count" if is_group else "private_history_count"
        try:
            return max(0, int(self.history_conf.get(key, default)))
        except Exception:
            return default

    def _is_weixin_oc_event(self, event) -> bool:
        if not event:
            return False
        names = []
        try:
            names.append(str(event.get_platform_name() or ""))
        except Exception as e:
            logger.debug(f"[DailySharing] 读取事件平台名失败: {e}")
        platform_inst = getattr(event, "platform", None)
        if platform_inst:
            names.extend(
                [
                    self._get_platform_type(platform_inst),
                    self._get_platform_id(platform_inst),
                ]
            )
        return any(str(name).strip().lower() == "weixin_oc" for name in names)

    def _get_onebot_bot(self, target_umo: str = "", event=None, adapter_id: str = ""):
        """获取 OneBot/CQHttp 客户端。UMO 第一段是平台 ID，不能当成平台类型判断。"""
        if event and self._is_onebot_event(event):
            bot = getattr(event, "bot", None)
            if bot:
                return bot

        if adapter_id and self._is_onebot_platform(adapter_id):
            bot = self._get_bot_instance(adapter_id)
            if bot:
                return bot

        target_s = str(target_umo or "").strip()
        umo_adapter_id, real_id = self._parse_umo(target_s)
        if umo_adapter_id:
            for inst in self._iter_platform_instances():
                if self._get_platform_id(inst) == umo_adapter_id and self._is_onebot_platform(self._get_platform_type(inst)):
                    bot = self._get_platform_client(inst)
                    if bot:
                        return bot

        probe = real_id or target_s
        if str(probe).isdigit():
            inst = self._find_platform_instance_by_keywords(["aiocqhttp", "onebot"])
            bot = self._get_platform_client(inst)
            if bot:
                return bot

        return None

    async def _bot_call_action(self, bot, action: str, **params):
        api = getattr(bot, "api", None)
        if api and hasattr(api, "call_action"):
            return await api.call_action(action, **params)
        if hasattr(bot, "call_action"):
            return await bot.call_action(action, **params)
        raise AttributeError(f"Bot 客户端不支持 call_action: {type(bot).__name__}")

    def _is_weixin_platform(self, target_umo: str) -> bool:
        raw = str(target_umo or "").lower()
        adapter_id, real_id = self._parse_umo(raw)
        session_id = real_id or raw
        return (
            session_id.endswith("@im.wechat")
            or session_id.endswith("@chatroom")
            or bool(adapter_id and adapter_id.strip().lower() == "weixin_oc")
        )

    # ==================== Bot 实例管理 ====================

    async def init_bots(self):
        """
        初始化 Bot 实例缓存
        """
        logger.debug("[DailySharing] 正在初始化 Bot 实例缓存...")
        try:
            count = 0
            for platform in self._iter_platform_instances():
                bot_client = self._get_platform_client(platform)
                p_id = self._get_platform_id(platform)
                if bot_client and p_id:
                    self.bot_map[str(p_id)] = bot_client
                    count += 1
                    logger.debug(f"[DailySharing] 发现并缓存 Bot 实例: {p_id} -> {type(bot_client).__name__}")
            
            logger.debug(f"[DailySharing] Bot 缓存初始化完成，共发现 {count} 个实例。")
            
        except Exception as e:
            logger.error(f"[DailySharing] Bot 初始化失败: {e}", exc_info=True)

    def _get_bot_instance(self, adapter_id: str):
        """
        从缓存中获取 Bot 实例
        """
        if adapter_id:
            return self.bot_map.get(adapter_id)

        if self.bot_map:
            if len(self.bot_map) == 1:
                return list(self.bot_map.values())[0]

            logger.error(
                f"[DailySharing] 存在多个 Bot 实例 {list(self.bot_map.keys())} 但未指定 adapter_id，"
                "无法确定使用哪个实例。"
            )
            return None

        logger.warning("[DailySharing] 没有任何可用的 Bot 实例。")
        return None

    # ==================== TTS 集成 ====================

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
                logger.debug(f"[DailySharing] 读取会话 LLM Provider 失败: {e}")

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
            logger.debug(f"[DailySharing] 读取默认 LLM Provider 失败: {e}")
        return ""

    async def _agent_analyze_sentiment(self, content: str, sharing_type: SharingType, target_umo: str = None) -> str:
        """
        使用 Agent 分析文本情感
        """
        if not content: return "neutral"
        
        # 1. 如果内容太短，不浪费 Token，直接用简单的 fallback
        if len(content) < 5: return "neutral"

        # 2. 构造 Prompt
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
            logger.debug(f"[Context] 情感分析 Agent 超时或出错: {e}，回退到默认逻辑")
        
        # 3. 兜底逻辑 (如果 Agent 失败)
        if sharing_type == SharingType.RECOMMENDATION: return "happy"
        if sharing_type == SharingType.GREETING: return "happy"
        return "neutral"

    async def text_to_speech(self, text: str, target_umo: str, sharing_type: SharingType = None, period: TimePeriod = None) -> Optional[str]:
        """
        调用 TTS 插件将文本转换为语音文件路径
        """
        # 1. 检查开关
        if not self.tts_conf.get("enable_tts", False):
            return None

        # 个人微信适配器目前不支持发送语音，自动降级为文字。
        if self._is_weixin_platform(target_umo):
            logger.info("[DailySharing] 当前平台为个人微信，目前不支持发送语音，跳过语音发送。")
            return None

        # 2. 获取插件
        tts_plugin = self._get_tts_plugin_inst()
        if not tts_plugin:
            logger.warning("[DailySharing] 未找到 TTS 插件 (astrbot_plugin_tts_emotion_router)，无法生成语音。")
            return None

        # 优先提取情感标签
        target_emotion = "neutral"
        
        # 正则匹配 $$happy$$ 格式
        emotion_match = re.search(r'\$\$(?:EMO:)?(happy|sad|angry|neutral|surprise)\$\$', text, flags=re.IGNORECASE)
        if emotion_match:
            target_emotion = emotion_match.group(1).lower()
            logger.debug(f"[DailySharing] 检测到内置情感标签: {target_emotion}")
        else:
            # 如果没有标签，再尝试使用 Agent 分析 (仅作为后备)
            if sharing_type:
                target_emotion = await self._agent_analyze_sentiment(text, sharing_type, target_umo=target_umo)

        # 3. 文本清洗
        final_text = text
        # 正则替换：彻底清洗文本中可能存在的任何标签，只保留纯文本给 TTS
        final_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', final_text, flags=re.IGNORECASE).strip()
        
        # 5. 调用生成
        try:
            session_state = None
            
            if hasattr(tts_plugin, "_get_session_state"):
                session_state = tts_plugin._get_session_state(target_umo)
                
                # 注入情感
                if target_emotion:
                    if hasattr(session_state, "pending_emotion"):
                        session_state.pending_emotion = target_emotion
                        logger.debug(f"[DailySharing] TTS 注入情绪: {target_emotion}")

            logger.info(f"[DailySharing] 正在请求 TTS 生成: {final_text[:20]}... (情绪: {target_emotion})")
            
            # 调用 TTS 处理器的 process 方法
            result = await tts_plugin.tts_processor.process(final_text, session_state)

            if result and result.success and result.audio_path:
                logger.info(f"[DailySharing] TTS 生成成功: {result.audio_path}")
                return str(result.audio_path)
            else:
                logger.warning(f"[DailySharing] TTS 生成失败: {getattr(result, 'error', '未知错误')}")
                return None

        except Exception as e:
            logger.error(f"[DailySharing] 调用 TTS 插件出错: {e}")
            return None
    
    # ==================== 生活上下文 (Life Scheduler) ====================
    
    async def get_life_context(self) -> Optional[str]:
        """获取生活上下文 (支持解析 JSON 数据)"""
        if not self.life_conf.get("enable_life_context", True): 
            return None
            
        if not self._life_plugin: 
            # 尝试用 "life_scheduler" 关键字查找
            self._life_plugin = self._find_plugin("life_scheduler")
        
        plugin = self._life_plugin
        if not plugin:
            return None

        # 调用插件接口
        if hasattr(plugin, 'get_life_context'):
            try: 
                raw_data = await plugin.get_life_context()
                
                if isinstance(raw_data, dict):
                    return self._parse_life_data(raw_data)
                
            except Exception as e: 
                logger.warning(f"[上下文] Life Scheduler 方法调用出错: {e}")
        
        return None

    def _parse_life_data(self, data: dict) -> str:
        """解析 Life Scheduler 返回的 JSON 数据为自然语言"""
        try:
            parts = []
            
            # 1. 天气
            weather = data.get("weather", "")
            if weather: parts.append(f"【今日天气】{weather}")
            
            # 2. 穿搭
            outfit = data.get("outfit", "")
            if outfit: parts.append(f"【今日穿搭】{outfit}")
            
            # 3. 完整元数据 (Meta)
            meta = data.get("meta", {})
            theme = meta.get("theme", "")
            mood = meta.get("mood", "")
            style = meta.get("style", "")
            schedule_type = meta.get("schedule_type", "")
            
            meta_str = []
            if theme: meta_str.append(f"主题: {theme}")
            if mood: meta_str.append(f"心情: {mood}")
            if style: meta_str.append(f"风格: {style}")
            if schedule_type: meta_str.append(f"定位: {schedule_type}")
            if meta_str:
                parts.append(f"【今日基调】{' | '.join(meta_str)}")
                
            # 4. 提取当前活动
            timeline = data.get("timeline", [])
            if timeline:
                import datetime
                now = datetime.datetime.now()
                now_mins = now.hour * 60 + now.minute
                current_act = None
                for item in timeline:
                    try:
                        h, m = map(int, item.get("time", "00:00").split(':'))
                        if h * 60 + m <= now_mins:
                            current_act = item
                    except (TypeError, ValueError) as e:
                        logger.debug(f"[DailySharing] 跳过无效时间线条目 {item}: {e}")
                if current_act:
                    parts.append(f"【当前活动】{current_act.get('activity')} (状态: {current_act.get('status', '未知')})")

            # 5. 提取备忘录和长期记忆
            memo = data.get("memo", "")
            if memo: 
                parts.append(f"【今日备忘录】\n{memo}")
                
            memories = data.get("long_term_memory", [])
            if memories:
                parts.append(f"【你的近期记忆 (可用于丰富话题)】\n" + "\n".join(f"- {m}" for m in memories))

            # 6. 日程详情及完整时间轴
            schedule = data.get("schedule", "")
            if schedule: parts.append(f"【今日完整时间轴及计划】\n{schedule}")
            
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"[上下文] 解析生活数据失败: {e}")
            return str(data)

    def format_life_context(self, context: str, sharing_type: SharingType, is_group: bool, group_info: dict = None) -> str:
        """格式化生活上下文"""
        if not context: return ""
        
        if is_group:
            return self._format_life_context_for_group(context, sharing_type, group_info)
        else:
            return self._format_life_context_for_private(context, sharing_type)

    def _format_life_context_for_group(self, context: str, sharing_type: SharingType, group_info: dict = None) -> str:
        """格式化群聊生活上下文"""
        if not self.life_conf.get("life_context_in_group", True): return ""
        
        # 如果是心情分享，且群聊热度高，则不带生活状态
        if sharing_type == SharingType.MOOD and group_info and group_info.get("chat_intensity") == "high":
            return ""

        # 检查配置开关：是否允许分享细节
        allow_detail = self.life_conf.get("group_share_schedule", False)

        if allow_detail:
            # 如果允许细节，直接返回完整上下文
            return f"\n\n【你的当前状态与记忆】\n{context}\n(注意：这是群聊，你可以提及上述状态，但请保持自然，不要像汇报工作一样)\n"

        # --- 以下为默认隐私模式（脱敏） ---

        # 解析上下文中的关键信息
        lines = context.split('\n')
        weather, period, busy, curr_act, mood_str = None, None, False, None, None
        for line in lines:
            if '天气' in line or '温度' in line: weather = line.strip()
            elif '时段' in line: period = line.strip()
            elif '今日基调' in line: mood_str = line.strip()
            elif '今日计划' in line: busy = True 
            elif '【当前活动】' in line: curr_act = line.strip()
        
        # 构建状态描述列表
        status_parts = []
        if weather: status_parts.append(weather)
        if mood_str: status_parts.append(mood_str)
        if period: status_parts.append(period) 
        if curr_act: status_parts.append(curr_act)
        elif busy: status_parts.append("（今日状态：比较忙碌）")
        
        full_status = "\n".join(status_parts) if status_parts else "未知"
        
        # === 针对不同类型的 Prompt ===
        
        if sharing_type == SharingType.GREETING:
            return f"\n\n【你的状态】\n{full_status}\n结合天气、时段(早/晚)和状态，自然地向大家打招呼\n"
            
        elif sharing_type == SharingType.NEWS:
            return f"\n\n【当前场景】\n{full_status}\n结合你当前的状态(如所处环境/休闲/天气)自然地分享新闻\n"
            
        elif sharing_type in (SharingType.KNOWLEDGE, SharingType.RECOMMENDATION):
            return f"\n\n【当前场景】\n{full_status}\n结合你当前的状态来切入分享\n"

        elif sharing_type == SharingType.MOOD:
            return f"\n\n【你的状态】\n{full_status}\n可以简单分享心情（结合天气或当前活动），但不要过于私人\n"
            
        return ""

    def _format_life_context_for_private(self, context: str, sharing_type: SharingType) -> str:
        """格式化私聊生活上下文"""
        # 私聊直接使用完整上下文 (context)，让 LLM 知道所有细节
        
        if sharing_type == SharingType.GREETING:
            return f"\n\n【你的真实状态】\n{context}\n\n请根据上面的真实日程（天气、穿搭、正在做什么）来打招呼\n"
            
        elif sharing_type == SharingType.MOOD:
            return f"\n\n【你现在的状态】\n{context}\n\n可以结合当前的穿搭、天气、具体心情、约会/工作安排等分享感受\n"
            
        elif sharing_type == SharingType.NEWS:
            return f"\n\n【你当前真实状态】\n{context}\n\n你正在这个状态下偷闲刷手机，请根据当前状态合理描述（例如：工作时间就说是忙里偷闲；休息时间可以随意些）。\n"
            
        elif sharing_type in (SharingType.KNOWLEDGE, SharingType.RECOMMENDATION):
            return (
                f"\n\n【你当前真实状态】\n{context}\n\n"
                "💡 请结合你【当前正在做的事】来自然地引出这个分享。\n"
                "   (例如：如果正在工作，可以是为了解决工作问题；如果正在运动，可以是间隙的思考。)\n"
            )
            
        return ""

    async def _fetch_deep_history(self, bot, target_id: int, is_group: bool, hours: int = 24, max_count: int = 100) -> List[Dict]:
        """深度回溯获取更早的聊天历史记录"""
        all_messages = []
        seen_ids = set()
        per_page = min(max_count + 20, 100)
        cursor_seq = 0
        try:
            effective_hours = max(1, min(int(hours), 168))
        except Exception:
            effective_hours = 24
        cutoff_time = time.time() - (effective_hours * 3600)
        max_rounds = 20
        
        action = "get_group_msg_history" if is_group else "get_friend_msg_history"
        id_key = "group_id" if is_group else "user_id"

        for round_idx in range(max_rounds):
            if len(all_messages) >= max_count:
                break
            
            try:
                if round_idx > 0:
                    await asyncio.sleep(0.5)

                params = {
                    id_key: target_id,
                    "count": per_page
                }
                if cursor_seq > 0:
                    params["message_seq"] = cursor_seq

                resp = await self._bot_call_action(bot, action, **params)
                
                if isinstance(resp, dict):
                    batch_msgs = resp.get("messages", [])
                elif isinstance(resp, list):
                    batch_msgs = resp
                else:
                    break
                    
                if not batch_msgs:
                    break

                batch_seqs = []
                # 记录本轮是否添加了新消息
                added_count = 0 
                
                for msg in batch_msgs:
                    # 1. 收集 SEQ (优先用 message_seq，没有则用 message_id)
                    seq = msg.get("message_seq") or msg.get("message_id")
                    if seq is not None:
                        try:
                            batch_seqs.append(int(seq))
                        except (TypeError, ValueError):
                            logger.debug(f"[DailySharing] 跳过无法解析的消息序号: {seq}")

                    # 2. 去重入库
                    mid = msg.get("message_id")
                    if mid is None:
                        mid = f"{msg.get('time')}-{msg.get('sender',{}).get('user_id')}"
                    
                    mid_str = str(mid)
                    
                    if mid_str not in seen_ids:
                        seen_ids.add(mid_str)
                        msg_time = int(msg.get("time", 0))
                        if msg_time >= cutoff_time:
                            all_messages.append(msg)
                            added_count += 1

                # 3. 翻页逻辑
                if not batch_seqs:
                    break 
                
                min_seq_in_batch = min(batch_seqs)
                
                # 如果这一轮没有任何新消息入库（说明全是重复的），强制停止，防止死循环
                if added_count == 0 and round_idx > 0:
                    break
                
                # 如果游标没有向前推进，停止
                if cursor_seq != 0 and min_seq_in_batch >= cursor_seq:
                    break
                
                # 更新游标：直接使用存在的最小 seq，允许下一页有一条重叠
                cursor_seq = min_seq_in_batch
                
            except Exception as e:
                # 即使使用了重叠策略，依然保留这个捕获作为最后一道防线
                err_str = str(e)
                if "不存在" in err_str or getattr(e, 'retcode', 0) == 1200:
                    logger.debug(f"[DailySharing] 历史记录翻到底了: {err_str}")
                else:
                    logger.warning(f"[DailySharing] 获取历史中断: {e}")
                break
        
        # 结果排序与截取
        all_messages.sort(key=lambda x: x.get("time", 0))
        final_msgs = all_messages[-max_count:]
        
        return final_msgs

    async def get_history_data(self, target_umo: str, is_group: bool = None, event=None) -> Dict[str, Any]:
        """
        获取聊天历史记录
        """
        # 1. 基础开关检查
        if not self.history_conf.get("enable_chat_history", True):
            return {}
            
        if is_group is None:
            is_group = self._is_group_chat(target_umo)
        adapter_id, real_id = self._parse_umo(target_umo)
        if not real_id:
            target_s = str(target_umo or "").strip()
            if target_s.isdigit():
                real_id = target_s
            else:
                logger.warning(f"[DailySharing] 无法解析目标ID: {target_umo}")
                return {}

        is_onebot_target = (
            self._is_onebot_platform(adapter_id)
            or self._is_onebot_event(event)
            or (not adapter_id and str(real_id or target_umo).strip().isdigit())
        )
        if not is_onebot_target:
            return await self._get_astrbot_saved_history_data(target_umo, is_group)

        bot = self._get_onebot_bot(target_umo, event=event, adapter_id=adapter_id)
        if not bot:
            return await self._get_astrbot_saved_history_data(target_umo, is_group)
        
        enable_deep = self.history_conf.get("enable_deep_history", True)
        history_hours = int(self.history_conf.get("deep_history_hours", 24))
        if history_hours > 168:
            history_hours = 168
        
        if is_group:
            # 群聊使用 deep_history_max_count (默认80)
            max_count = int(self.history_conf.get("deep_history_max_count", 80))
        else:
            # 私聊使用 private_history_count (默认20)
            max_count = int(self.history_conf.get("private_history_count", 20))
            
        try:
            logger.info(f"[DailySharing] 正在获取 {real_id} 的聊天历史记录 (模式: {'群聊' if is_group else '私聊'}, 目标: {max_count}条)...")
            messages = []
            raw_msgs = []

            try:
                if enable_deep:
                    raw_msgs = await self._fetch_deep_history(
                        bot, 
                        int(real_id), 
                        is_group=is_group,
                        hours=history_hours, 
                        max_count=max_count
                    )
                    logger.info(f"[DailySharing] 聊天历史记录获取成功: {len(raw_msgs)} 条")
                else:
                    action = "get_group_msg_history" if is_group else "get_friend_msg_history"
                    key = "group_id" if is_group else "user_id"

                    req_count = max_count 
                    
                    payloads = {key: int(real_id), "count": req_count}
                    
                    result = await self._bot_call_action(bot, action, **payloads)
                    raw_msgs = result.get("messages", []) if isinstance(result, dict) else (result or [])

            except Exception as e:
                logger.warning(f"[DailySharing] 获取聊天历史记录失败: {e}")
                return await self._get_astrbot_saved_history_data(target_umo, is_group)

            bot_qq = ""
            try:
                login_info = await self._bot_call_action(bot, "get_login_info")
                if login_info and isinstance(login_info, dict):
                    bot_qq = str(login_info.get("user_id", ""))
            except Exception as e:
                logger.debug(f"[DailySharing] 获取 login_info 失败: {e}")

            for msg in raw_msgs:
                sender_data = msg.get("sender", {})
                msg_uid = str(sender_data.get("user_id", ""))
                
                raw_content = ""
                if "message" in msg and isinstance(msg["message"], list):
                    raw_content = "".join(
                        seg["data"]["text"] for seg in msg["message"] if seg["type"] == "text"
                    ).strip()
                elif "raw_message" in msg:
                    raw_content = str(msg["raw_message"])

                if not raw_content: continue
                
                role = "assistant" if (bot_qq and msg_uid == bot_qq) else "user"
                ts = msg.get("time")
                try:
                    ts_str = datetime.datetime.fromtimestamp(ts).isoformat() if isinstance(ts, (int, float)) else ""
                except Exception:
                    ts_str = ""
                messages.append({"role": role, "content": raw_content, "timestamp": ts_str, "user_id": msg_uid})

            if not messages: return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                result["group_info"] = self._analyze_group_chat(messages)
            
            return result

        except Exception as e:
            logger.warning(f"[DailySharing] API 获取历史出错: {e}")
            return await self._get_astrbot_saved_history_data(target_umo, is_group)

    async def _get_astrbot_saved_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """优先读取 AstrBot 平台消息历史；没有可用记录时再读取会话历史。"""
        platform_data = await self._get_platform_message_history_data(target_umo, is_group)
        if not platform_data:
            return await self._get_conversation_history_data(target_umo, is_group)

        if any(msg.get("role") == "assistant" for msg in platform_data.get("messages", [])):
            conversation_data = await self._get_conversation_history_data(target_umo, is_group)
            self._mark_daily_share_sources(
                platform_data.get("messages", []),
                conversation_data.get("messages", []) if conversation_data else [],
            )
            if platform_data.get("is_group"):
                analysis_messages = [
                    msg for msg in platform_data["messages"]
                    if msg.get("source") != DAILY_SHARING_SOURCE
                ]
                platform_data["group_info"] = self._analyze_group_chat(analysis_messages)

        return platform_data

    async def _get_platform_message_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """读取 AstrBot 保存的平台消息记录表，用于 WebChat 等平台。"""
        if is_group is None:
            is_group = self._is_group_chat(target_umo)

        adapter_id, real_id = self._parse_umo(str(target_umo or ""))
        if not adapter_id or not real_id:
            return {}

        history_manager = getattr(self.context, "message_history_manager", None)
        get_history = getattr(history_manager, "get", None)
        if not callable(get_history):
            return {}

        max_count = self._get_history_max_count(is_group)
        if max_count <= 0:
            return {}

        try:
            records = []
            for user_id in self._get_platform_history_user_ids(adapter_id, real_id):
                records = await get_history(
                    platform_id=adapter_id,
                    user_id=user_id,
                    page=1,
                    page_size=max_count,
                )
                if records:
                    break

            messages = []
            next_assistant_is_daily_share = False
            for record in records or []:
                role_content = self._extract_platform_history_role_content(record)
                if role_content and self._is_internal_share_trigger(*role_content):
                    next_assistant_is_daily_share = True
                    continue

                msg = self._normalize_platform_history_item(record)
                if msg:
                    if next_assistant_is_daily_share:
                        if msg.get("role") == "assistant":
                            msg["source"] = DAILY_SHARING_SOURCE
                        next_assistant_is_daily_share = False
                    messages.append(msg)

            messages = messages[-max_count:]
            if not messages:
                return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                analysis_messages = [
                    msg for msg in messages
                    if msg.get("source") != DAILY_SHARING_SOURCE
                ]
                result["group_info"] = self._analyze_group_chat(analysis_messages)
            logger.debug(f"[DailySharing] 已读取 AstrBot 平台消息历史: {target_umo} ({len(messages)} 条)")
            return result
        except Exception as e:
            logger.warning(f"[DailySharing] 读取 AstrBot 平台消息历史失败: {e}")
            return {}

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

    async def _get_conversation_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """读取 AstrBot 已保存的会话历史，用于个人微信不支持主动拉取历史的平台。"""
        if is_group is None:
            is_group = self._is_group_chat(target_umo)

        conv_manager = getattr(self.context, "conversation_manager", None)
        if not conv_manager:
            return {}

        try:
            conversation_id = await conv_manager.get_curr_conversation_id(target_umo)
            if not conversation_id:
                return {}
            conversation = await conv_manager.get_conversation(target_umo, conversation_id)
            if not conversation:
                return {}

            history_raw = getattr(conversation, "history", "[]")
            if isinstance(history_raw, list):
                history = history_raw
            else:
                try:
                    history = json.loads(history_raw or "[]")
                except json.JSONDecodeError as e:
                    logger.debug(f"[DailySharing] 会话历史 JSON 解析失败: {e}")
                    history = []

            if not isinstance(history, list):
                return {}

            max_count = self._get_history_max_count(is_group)
            if max_count <= 0:
                return {}

            messages = []
            next_assistant_is_daily_share = False
            history_window = history[-(max_count + 1):]
            for item in history_window:
                role_content = self._extract_conversation_item_role_content(item)
                if role_content and self._is_internal_share_trigger(*role_content):
                    next_assistant_is_daily_share = True
                    continue

                msg = self._normalize_conversation_history_item(item)
                if msg:
                    if next_assistant_is_daily_share:
                        if msg.get("role") == "assistant":
                            msg["source"] = DAILY_SHARING_SOURCE
                        next_assistant_is_daily_share = False
                    messages.append(msg)
            messages = messages[-max_count:]

            if not messages:
                return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                analysis_messages = [
                    msg for msg in messages
                    if msg.get("source") != DAILY_SHARING_SOURCE
                ]
                result["group_info"] = self._analyze_group_chat(analysis_messages)
            logger.debug(f"[DailySharing] 已读取 AstrBot 会话历史: {target_umo} ({len(messages)} 条)")
            return result
        except Exception as e:
            logger.warning(f"[DailySharing] 读取 AstrBot 会话历史失败: {e}")
            return {}

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

    def _analyze_group_chat(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """分析群聊热度"""
        if not messages: return {}
        try:
            # 1. 读取配置的“判断基准数” (例如 30)
            check_count = int(self.history_conf.get("group_intensity_check_count", 30))
            
            # 2. 设定“有效时间窗口” (例如最近 20 分钟)
            active_window_seconds = 20 * 60 
            now = time.time()
            cutoff_time = now - active_window_seconds

            # 3. 统计有效消息
            active_msgs_count = 0
            user_count = {}
            topics = []
            
            # 只看最近 N 条，减少计算量，但后续要过滤时间
            consideration_msgs = messages[- (check_count * 2):] if len(messages) > (check_count * 2) else messages

            last_msg_time = 0

            for msg in consideration_msgs:
                # 解析时间
                ts_str = msg.get("timestamp", "")
                try:
                    ts = datetime.datetime.fromisoformat(ts_str).timestamp()
                except (TypeError, ValueError):
                    ts = 0
                
                if ts > last_msg_time: last_msg_time = ts

                # 只有在最近 20 分钟内的消息才计入热度
                if ts >= cutoff_time:
                    active_msgs_count += 1
                    
                    # 统计活跃用户
                    if msg.get("role") == "user":
                        uid = msg.get("user_id", "unknown")
                        user_count[uid] = user_count.get(uid, 0) + 1
                    
                    # 收集话题
                    content = msg.get("content", "")
                    if len(content) > 5: topics.append(content[:50])

            # 4. 排序活跃用户
            active_users = sorted(user_count.items(), key=lambda x: x[1], reverse=True)[:3]
            
            # 5. 动态阈值判定
            # 如果配置是 30：
            # High:   20分钟内消息 > 15 条 (30 * 0.5)
            threshold_high = check_count * 0.5 
            # Medium: 20分钟内消息 > 5 条  (30 * 0.16)
            threshold_medium = check_count * 0.16 
            
            if active_msgs_count > threshold_high:
                intensity = "high"
            elif active_msgs_count > threshold_medium:
                intensity = "medium"
            else:
                intensity = "low"
            
            # 6. 辅助判断：是否正在讨论 (最后一条消息在 10 分钟内)
            is_discussing = False
            if last_msg_time > 0 and (now - last_msg_time) < 600:
                is_discussing = True
            
            return {
                "recent_topics": topics[-5:], 
                "active_users": [u for u, c in active_users],
                "chat_intensity": intensity,
                "message_count": active_msgs_count, 
                "is_discussing": is_discussing,
            }
        except Exception as e:
            logger.warning(f"[DailySharing] 分析群聊热度出错: {e}")
            return {}

    def format_history_prompt(self, history_data: Dict, sharing_type: SharingType) -> str:
        """格式化 Prompt"""
        if not history_data or not history_data.get("messages"): return ""
        is_group = history_data.get("is_group", False)
        messages = history_data["messages"]
        if is_group:
            return self._format_group_chat_for_prompt(messages, history_data.get("group_info", {}), sharing_type)
        else:
            return self._format_private_chat_for_prompt(messages, sharing_type)

    def _format_group_chat_for_prompt(self, messages: List[Dict], group_info: Dict, sharing_type: SharingType) -> str:
        intensity = group_info.get("chat_intensity", "low")
        discussing = group_info.get("is_discussing", False)
        topics = group_info.get("recent_topics", [])
        
        if sharing_type == SharingType.GREETING:
            hint = "群里正在热烈讨论，简短打个招呼即可" if discussing else "可以活跃一下气氛"
        elif sharing_type == SharingType.NEWS: hint = "选择可能引起群内讨论的新闻"
        elif sharing_type == SharingType.MOOD: hint = "可以简单分享心情，但不要过于私人"
        else: hint = ""
        
        txt = f"\n\n【群聊状态】\n聊天热度: {intensity}\n近期消息数: {group_info.get('message_count', 0)} 条\n"
        if discussing: txt += "群里正在热烈讨论中！\n"
        if topics: txt += "\n【最近话题】\n" + "\n".join([f"• {t}..." for t in topics[-5:]])
        return txt + f"\n{hint}\n"

    def _format_private_chat_for_prompt(self, messages: List[Dict], sharing_type: SharingType) -> str:
        max_length = 500
        if sharing_type == SharingType.GREETING: hint = "可以根据最近的对话内容打招呼"
        elif sharing_type == SharingType.MOOD: hint = "可以延续最近的话题或感受"
        elif sharing_type == SharingType.NEWS: hint = "可以根据对方的兴趣选择新闻"
        else: hint = "可以自然地延续最近的对话"
        
        lines = []
        total_len = 0
        for m in reversed(messages[-5:]):
            content = m["content"]
            if len(content) > 100: content = content[:100] + "..."
            if m.get("source") == DAILY_SHARING_SOURCE:
                line = f"背景: 你之前主动分享过：{content}"
            else:
                role = "用户" if m["role"] == "user" else "你"
                line = f"{role}: {content}"
            if total_len + len(line) > max_length: break
            lines.insert(0, line)
            total_len += len(line)
        return "\n\n【最近的对话】\n" + "\n".join(lines) + f"\n\n{hint}\n"

    # ==================== 策略检查 ====================

    def check_group_strategy(self, group_info: Dict) -> bool:
        if not group_info: return True
        strategy = self.history_conf.get("group_share_strategy", "cautious")
        is_discussing = group_info.get("is_discussing", False)
        intensity = group_info.get("chat_intensity", "low")

        if strategy == "cautious":
            if is_discussing and intensity == "high": return False
        elif strategy == "minimal":
            if is_discussing or intensity != "low": return False
        return True
    
    # ==================== 上下文注入 ====================
    
    async def record_bot_reply_to_history(self, target_umo: str, content: str, image_desc: str = None):
        """
        将 Bot 主动发送的消息写入 AstrBot 框架的对话历史中。
        """
        if not target_umo: return

        # 1. 预处理内容
        clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()
        final_content = clean_content
        if image_desc:
            final_content += f"\n\n[发送了一张配图: {image_desc}]"

        try:
            conv_manager = getattr(self.context, "conversation_manager", None)
            if not conv_manager or not hasattr(conv_manager, "add_message_pair"):
                logger.warning("[上下文] 当前 AstrBot 版本过低，不支持追加对话消息，无法写入消息历史。")
                return
            
            # 获取或创建会话 ID
            conversation_id = await conv_manager.get_curr_conversation_id(target_umo)
            if not conversation_id:
                conversation_id = await conv_manager.new_conversation(target_umo)
            
            # 使用内部标记保留成对历史，同时避免把主动分享误识别为用户真实发言。
            user_msg = {
                "role": "user",
                "content": [{"type": "text", "text": DAILY_SHARING_INTERNAL_TRIGGER}],
            }
            assistant_msg = {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": final_content,
                    }
                ],
            }
            
            await conv_manager.add_message_pair(
                cid=conversation_id,
                user_message=user_msg,
                assistant_message=assistant_msg,
            )
            logger.debug(f"[上下文] 已写入历史: {target_umo}")
            
        except Exception as e:
            logger.warning(f"[上下文] 写入对话历史失败: {e}")

    # ==================== 记忆记录 ====================

    async def record_to_memos(self, target_umo: str, content: str, image_desc: str = None):
        if not self.memory_conf.get("record_sharing_to_memory", True): return
        memos = self._get_memos_plugin()
        if memos:
            try:
                # 清洗内容中的标签
                clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()
                full_text = clean_content

                if image_desc: 
                    tag = f"[配图: {image_desc}]" if self.image_conf.get("record_image_description", True) else "[已发送配图]"
                    full_text += f"\n{tag}"
                elif image_desc is not None:
                    full_text += "\n[已发送配图]"

                cid = await self.context.conversation_manager.get_curr_conversation_id(target_umo)
                if not cid: cid = await self.context.conversation_manager.new_conversation(target_umo)

                virtual_prompt = DAILY_SHARING_MEMORY_PROMPT
                await memos.memory_manager.add_message(
                    messages=[
                        {"role": "user", "content": virtual_prompt}, 
                        {"role": "assistant", "content": full_text}
                    ],
                    user_id=target_umo, conversation_id=cid
                )
                logger.info(f"[上下文] 已记录到 Memos: {target_umo}")
            except Exception as e: 
                logger.warning(f"[上下文] 记录失败: {e}")
                
