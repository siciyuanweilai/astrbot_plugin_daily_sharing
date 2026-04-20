import datetime
import time
import re 
import json 
import asyncio
from typing import Optional, Dict, Any, List
from astrbot.api import logger
from ..config import SharingType, TimePeriod 

try:
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        TextPart,
    )
    HAS_NEW_MESSAGE_API = True
except ImportError:
    HAS_NEW_MESSAGE_API = False

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
        """查找插件实例 """
        try:
            plugins = self.context.get_all_stars()
            
            for plugin in plugins:
                p_id = getattr(plugin, "id", "") or ""
                p_name = getattr(plugin, "name", "") or ""
                
                if (keyword in p_id) or (keyword in p_name):
                    if hasattr(plugin, "instance") and plugin.instance:
                        return plugin.instance
                    if hasattr(plugin, "star_instance") and plugin.star_instance:
                        return plugin.star_instance
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
                return parts[0], parts[2]
            return None, None
        except:
            return None, None

    # ==================== Bot 实例管理 ====================

    async def init_bots(self):
        """
        初始化 Bot 实例缓存
        """
        logger.debug("[DailySharing] 正在初始化 Bot 实例缓存...")
        try:
            # 获取平台管理器
            pm = getattr(self.context, "platform_manager", None)
            if not pm:
                logger.warning("[DailySharing] 无法获取 PlatformManager，Bot 获取可能受限。")
                return

            # 获取所有平台实例 
            platforms = []
            if hasattr(pm, "get_insts"):
                platforms = pm.get_insts() 
            elif hasattr(pm, "insts"):
                raw = pm.insts
                platforms = list(raw.values()) if isinstance(raw, dict) else raw

            count = 0
            for platform in platforms:
                # 获取 Bot 客户端对象
                bot_client = None
                if hasattr(platform, "get_client"):
                    bot_client = platform.get_client()
                elif hasattr(platform, "bot"):
                    bot_client = platform.bot
                
                # 获取平台 ID
                p_id = None
                if hasattr(platform, "metadata") and hasattr(platform.metadata, "id"):
                    p_id = platform.metadata.id
                elif hasattr(platform, "id"):
                    p_id = platform.id
                
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

    async def _agent_analyze_sentiment(self, content: str, sharing_type: SharingType) -> str:
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
            # 使用 context 自带的 llm_generate
            provider_id = self.llm_conf.get("llm_provider_id", "")
            
            # 设置较长的超时时间 (15秒)
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    prompt=user_prompt, 
                    system_prompt=system_prompt,
                    chat_provider_id=provider_id if provider_id else None
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
                target_emotion = await self._agent_analyze_sentiment(text, sharing_type)

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
                    except:
                        pass
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
        cutoff_time = time.time() - (max(hours, 24) * 3600)
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

                resp = await bot.api.call_action(action, **params)
                
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
                        try: batch_seqs.append(int(seq))
                        except: pass

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

    async def get_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
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
            logger.warning(f"[DailySharing] 无法解析目标ID: {target_umo}")
            return {}
        bot = self._get_bot_instance(adapter_id)
        if not bot: return {}
        
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
                    
                    result = await bot.api.call_action(action, **payloads)
                    raw_msgs = result.get("messages", []) if isinstance(result, dict) else (result or [])

            except Exception as e:
                logger.warning(f"[DailySharing] 获取聊天历史记录失败: {e}")
                return {}

            bot_qq = ""
            if hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                try:
                    login_info = await bot.api.call_action("get_login_info")
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
                ts = msg.get("time", time.time())
                ts_str = datetime.datetime.fromtimestamp(ts).isoformat()
                messages.append({"role": role, "content": raw_content, "timestamp": ts_str, "user_id": msg_uid})

            if not messages: return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                result["group_info"] = self._analyze_group_chat(messages)
            
            return result

        except Exception as e:
            logger.warning(f"[DailySharing] API 获取历史出错: {e}")
            return {}

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
                except:
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
            role = "用户" if m["role"] == "user" else "你"
            content = m["content"]
            if len(content) > 100: content = content[:100] + "..."
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

        # 检查是否支持新版 API
        if not HAS_NEW_MESSAGE_API:
            logger.warning("[上下文] 当前 AstrBot 版本过低，不支持新的消息历史写入。请升级 AstrBot 最新版本。")
            return

        # 1. 预处理内容
        clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()
        final_content = clean_content
        if image_desc:
            final_content += f"\n\n[发送了一张配图: {image_desc}]"

        # 虚拟的用户提示词（因为是Bot主动发起，需要模拟一个用户请求来保持对话成对）
        user_prompt = "请发送今天的每日分享内容。"

        try:
            conv_manager = self.context.conversation_manager
            
            # 获取或创建会话 ID
            conversation_id = await conv_manager.get_curr_conversation_id(target_umo)
            if not conversation_id:
                conversation_id = await conv_manager.new_conversation(target_umo)
            
            # 使用新版 API (AstrBot v3.3+)
            user_msg = UserMessageSegment(content=[TextPart(text=user_prompt)])
            assistant_msg = AssistantMessageSegment(content=[TextPart(text=final_content)])
            
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

                virtual_prompt = "请发送今天的每日分享内容。" 
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
                