# core/context.py
import datetime
import time
import re
import json 
from typing import Optional, Dict, Any, List
from astrbot.api import logger
from ..config import SharingType, TimePeriod 

class ContextService:
    def __init__(self, context_obj, config):
        self.context = context_obj
        self.config = config
        self._life_plugin = None
        self._memos_plugin = None
        self._tts_plugin = None
        
        unified_conf = self.config.get("context_conf", {})
        
        self.life_conf = unified_conf
        self.history_conf = unified_conf
        self.memory_conf = unified_conf

        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {}) 

    # ==================== 基础辅助方法 ====================

    def _find_plugin(self, keyword: str):
        """查找插件实例"""
        try:
            plugins = self.context.get_all_stars()
            for plugin in plugins:
                if keyword in getattr(plugin, "name", ""):
                    return getattr(plugin, "star_cls", None)
        except Exception as e:
            logger.warning(f"[上下文] 查找插件 '{keyword}' 错误: {e}")
        return None

    def _get_memos_plugin(self):
        """懒加载获取 Memos 插件 (仅用于写入记录)"""
        if not self._memos_plugin:
            self._memos_plugin = self._find_plugin("astrbot_plugin_memos_integrator")
        return self._memos_plugin

    def _get_tts_plugin_inst(self):
        """获取 TTS 插件实例"""
        if not self._tts_plugin:
            # 查找 astrbot_plugin_tts_emotion_router
            self._tts_plugin = self._find_plugin("astrbot_plugin_tts_emotion_router")
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

    def _get_bot_instance(self, adapter_id: str):
        """
        获取 Bot 实例 
        """
        # 1. 尝试 Context 的标准方法
        if hasattr(self.context, "get_bot"):
            try:
                bot = self.context.get_bot(adapter_id)
                if bot: return bot
            except: pass

        pm = self.context.platform_manager
        all_insts = []

        # 2. 获取所有实例 (List/Dict 兼容性读取)
        try:
            # 尝试直接访问 .insts 属性
            if hasattr(pm, "insts"):
                raw = pm.insts
                if isinstance(raw, dict):
                    all_insts.extend(list(raw.values()))
                elif isinstance(raw, list):
                    all_insts.extend(raw)
            
            # 如果没找到，尝试调用 .get_insts() 方法
            if not all_insts and hasattr(pm, "get_insts") and callable(pm.get_insts):
                raw = pm.get_insts()
                if isinstance(raw, dict):
                    all_insts.extend(list(raw.values()))
                elif isinstance(raw, list):
                    all_insts.extend(raw)

        except Exception as e:
            logger.warning(f"[DailySharing] 获取实例列表失败: {e}")

        if not all_insts:
            return None

        valid_candidates = []

        # 3. 遍历查找
        for inst in all_insts:
            # 尝试获取 bot 对象
            bot = getattr(inst, "bot", None)
            
            # 如果 inst.bot 不存在，检查 inst 本身是否像一个 Bot (拥有 api 属性)
            if not bot and hasattr(inst, "api"):
                bot = inst
            
            if not bot:
                continue
            
            # 收集有效候选
            valid_candidates.append(bot)

            inst_id = str(getattr(inst, "id", ""))
            inst_type = str(getattr(inst, "adapter_type", ""))

            # 精确/模糊匹配
            if adapter_id and (adapter_id == inst_id or adapter_id == inst_type or adapter_id in inst_id):
                return bot

        # 4. 智能兜底 (如果名字没对上，但找到了 Bot，就用第一个)
        if valid_candidates:
            # 如果只有一个，直接用，不报错（这是最常见的情况）
            if len(valid_candidates) == 1:
                return valid_candidates[0]
            
            # 如果有多个，用第一个，但记录一条 debug 日志
            logger.debug(f"[DailySharing] 未精确匹配适配器 '{adapter_id}'，将使用默认 Bot 实例。")
            return valid_candidates[0]

        # 5. 真没找到
        logger.warning(f"[DailySharing] ❌ 未找到任何可用的 Bot 实例。")
        return None

    # ==================== TTS 集成 ====================

    def _determine_emotion_raw(self, sharing_type: SharingType, period: TimePeriod, content: str = "") -> str:
        """
        根据分享类型、时间段和文本内容，决定 TTS 的情绪字符串。
        """
        
        # === 1. 扩充关键词库 ===
        
        happy_keywords = [
            "开心", "快乐", "高兴", "喜悦", "愉快", "兴奋", "喜欢", "棒", "不错", "哈哈", 
            "lol", "great", "awesome", "happy", "joy", "excited", ":)", "😀",
            "震惊", "惊爆", "突发", "奇迹", "不可思议", "没想到", "惊讶", "哇", "天啊", 
            "surprise", "喜讯", "祝贺", "期待"
        ]
        
        # 愤怒/生气
        angry_keywords = [
            "生气", "愤怒", "火大", "恼火", "气愤", "气死", "怒", "怒了", "angry", 
            "furious", "mad", "rage", "annoyed", "nm", "tmd", "淦", "😡",
            "怒斥", "谴责", "恶劣", "讨厌", "过分", "无语", "抵制"
        ]
        
        # 悲伤/难过 
        sad_keywords = [
            "伤心", "难过", "沮丧", "低落", "悲伤", "哭", "流泪", "难受", "失望", 
            "委屈", "心碎", "sad", "depress", "upset", "unhappy", "blue", "tear", 
            "遗憾", "可惜", "哀悼", "去世", "逝世", "痛苦", ":(", "😢"
        ]

        # === 2. 优先根据关键词判断强情绪 ===
        
        for k in angry_keywords:
            if k in content: return "angry"
            
        for k in sad_keywords:
            if k in content: return "sad"
            
        for k in happy_keywords:
            if k in content: return "happy"
        
        # === 3. 根据业务类型和时间段判断基础情绪 (兜底策略) ===
        
        if sharing_type == SharingType.GREETING:
            if period in [TimePeriod.DAWN, TimePeriod.MORNING, TimePeriod.EVENING]:
                return "happy" 
            elif period == TimePeriod.NIGHT:
                return "sad"   
            else:
                return "happy"
        
        elif sharing_type == SharingType.MOOD:
            if period == TimePeriod.NIGHT:
                return "sad" 
            else:
                return "neutral"

        elif sharing_type in [SharingType.NEWS, SharingType.KNOWLEDGE, SharingType.RECOMMENDATION]:
            if sharing_type == SharingType.RECOMMENDATION:
                return "happy"
            else:
                return "neutral" 

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

        # 3. 文本清洗与情感获取
        final_text = text
        
        # 【正则替换】：彻底清洗文本中可能存在的任何标签，只保留纯文本给 TTS
        final_text = re.sub(r'$$(EMO:)?(happy|sad|angry|neutral|surprise)$$', '', final_text, flags=re.IGNORECASE).strip()
        
        target_emotion = "neutral"
        if sharing_type and period:
            # 获取纯情绪字符串 (如 "happy")
            target_emotion = self._determine_emotion_raw(sharing_type, period, text)

        # 4. 调用生成
        try:
            session_state = None
            
            # 直接操作 TTS 插件的 Session State
            if hasattr(tts_plugin, "_get_session_state"):
                session_state = tts_plugin._get_session_state(target_umo)
                
                # 【注入情感】
                if target_emotion and target_emotion != "neutral":
                    if hasattr(session_state, "pending_emotion"):
                        session_state.pending_emotion = target_emotion
                        logger.debug(f"[DailySharing] 已注入 TTS 情绪状态: {target_emotion}")

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
            self._life_plugin = self._find_plugin("life_scheduler")
            
        if self._life_plugin and hasattr(self._life_plugin, 'get_life_context'):
            try: 
                raw_data = await self._life_plugin.get_life_context()
                
                # 处理字典格式 (新的 Life Scheduler 返回结构)
                if isinstance(raw_data, dict):
                    return self._parse_life_data(raw_data)
                
                # 处理字符串格式 (旧的兼容)
                if raw_data and isinstance(raw_data, str) and len(raw_data.strip()) > 10:
                    return raw_data
            except Exception as e: 
                logger.warning(f"[上下文] Life Scheduler 插件调用出错: {e}")
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
            
            # 3. 风格与心情
            meta = data.get("meta", {})
            mood = meta.get("mood", "")
            style = meta.get("style", "")
            if mood or style:
                parts.append(f"【今日风格】心情{mood}，走{style}")
                
            # 4. 日程详情
            schedule = data.get("schedule", "")
            if schedule: parts.append(f"【今日日程与状态】\n{schedule}")
            
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"[上下文] 解析生活数据失败: {e}")
            return str(data)

    def format_life_context(self, context: str, sharing_type: SharingType, is_group: bool, group_info: dict = None) -> str:
        """格式化生活上下文 (统一入口)"""
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
            return f"\n\n【你的当前状态】\n{context}\n💡 (注意：这是群聊，你可以提及上述状态，但请保持自然，不要像汇报工作一样)\n"

        # --- 以下为默认隐私模式（脱敏） ---

        # 解析上下文中的关键信息
        lines = context.split('\n')
        weather, period, busy = None, None, False
        for line in lines:
            if '天气' in line or '温度' in line: weather = line.strip()
            elif '时段' in line: period = line.strip()
            elif '今日计划' in line or '约会' in line: busy = True
        
        # 构建状态描述列表
        status_parts = []
        if weather: status_parts.append(weather)
        if period: status_parts.append(period) 
        if busy: status_parts.append("（今日状态：比较忙碌）")
        
        full_status = "\n".join(status_parts) if status_parts else "未知"
        
        # === 针对不同类型的 Prompt ===
        
        if sharing_type == SharingType.GREETING:
            return f"\n\n【你的状态】\n{full_status}\n💡 结合天气、时段(早/晚)和忙闲状态，自然地向大家打招呼\n"
            
        elif sharing_type == SharingType.NEWS:
            return f"\n\n【当前场景】\n{full_status}\n💡 结合你当前的状态(如忙碌/休闲/天气)自然地分享新闻\n"
            
        elif sharing_type in (SharingType.KNOWLEDGE, SharingType.RECOMMENDATION):
            return f"\n\n【当前场景】\n{full_status}\n💡 结合你当前的状态(如工作中/休息中)来切入分享\n"

        elif sharing_type == SharingType.MOOD:
            return f"\n\n【你的状态】\n{full_status}\n💡 可以简单分享心情（结合天气或忙闲），但不要过于私人\n"
            
        return ""

    def _format_life_context_for_private(self, context: str, sharing_type: SharingType) -> str:
        """格式化私聊生活上下文"""
        # 私聊直接使用完整上下文 (context)，让 LLM 知道所有细节
        
        if sharing_type == SharingType.GREETING:
            return f"\n\n【你的真实状态】\n{context}\n\n💡 请根据上面的真实日程（天气、穿搭、正在做什么）来打招呼\n"
            
        elif sharing_type == SharingType.MOOD:
            return f"\n\n【你现在的状态】\n{context}\n\n💡 可以结合当前的穿搭、天气、具体心情、约会/工作安排等分享感受\n"
            
        elif sharing_type == SharingType.NEWS:
            return f"\n\n【你当前真实状态】\n{context}\n\n💡 你正在这个状态下偷闲刷手机，请根据当前状态合理描述（例如：工作时间就说是忙里偷闲；休息时间可以随意些）。\n"
            
        elif sharing_type in (SharingType.KNOWLEDGE, SharingType.RECOMMENDATION):
            return (
                f"\n\n【你当前真实状态】\n{context}\n\n"
                "💡 请结合你【当前正在做的事】来自然地引出这个分享。\n"
                "   (例如：如果正在工作，可以是为了解决工作问题；如果正在运动，可以是间隙的思考。)\n"
            )
            
        return ""

    # ==================== 聊天历史 ====================

    async def get_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """
        获取聊天历史 
        """
        if not self.history_conf.get("enable_chat_history", True):
            return {}
            
        if is_group is None:
            is_group = self._is_group_chat(target_umo)

        adapter_id, real_id = self._parse_umo(target_umo)
        if not real_id:
            logger.warning(f"[DailySharing] 无法解析目标ID: {target_umo}")
            return {}

        bot = self._get_bot_instance(adapter_id)

        if not bot:
            return {}

        limit = 20
        
        try:
            logger.info(f"[DailySharing] 正在读取 {real_id} 的历史记录...")
            messages = []
            
            if is_group:
                # === 群聊逻辑 ===
                try:
                    payloads = {"group_id": int(real_id), "count": limit}
                    result = await bot.api.call_action("get_group_msg_history", **payloads)
                    
                    raw_msgs = []
                    if result and isinstance(result, dict):
                        raw_msgs = result.get("messages", [])
                    elif result and isinstance(result, list):
                        raw_msgs = result
                    
                    self_id = str(bot.self_id) if hasattr(bot, "self_id") else ""

                    for msg in raw_msgs:
                        sender_id = str(msg.get("sender", {}).get("user_id", ""))
                        raw_content = ""
                        if "message" in msg and isinstance(msg["message"], list):
                            raw_content = "".join(
                                seg["data"]["text"] for seg in msg["message"] if seg["type"] == "text"
                            ).strip()
                        elif "raw_message" in msg:
                            raw_content = msg["raw_message"]

                        if not raw_content: continue
                        role = "assistant" if sender_id == self_id else "user"
                        ts = msg.get("time", time.time())
                        ts_str = datetime.datetime.fromtimestamp(ts).isoformat()
                        messages.append({"role": role, "content": raw_content, "timestamp": ts_str, "user_id": sender_id})

                    if messages:
                        logger.info(f"[DailySharing] 群聊历史获取成功: {len(messages)} 条")
                    else:
                        logger.warning(f"[DailySharing] 群聊历史为空 (API返回了数据但解析后为0，或群内无新消息)")

                except Exception as e:
                    logger.warning(f"[DailySharing] 获取群聊历史失败: {e} (可能是当前适配器不支持 get_group_msg_history)")

            else:
                # === 私聊逻辑 ===
                try:
                    payloads = {"user_id": int(real_id), "count": limit}
                    result = await bot.api.call_action("get_friend_msg_history", **payloads)
                    raw_msgs = result.get("messages", [])
                    
                    self_id = str(bot.self_id) if hasattr(bot, "self_id") else ""

                    for msg in raw_msgs:
                        sender_data = msg.get("sender", {})
                        msg_uid = str(sender_data.get("user_id", ""))
                        
                        raw_content = ""
                        if "message" in msg and isinstance(msg["message"], list):
                            raw_content = "".join(
                                seg["data"]["text"] for seg in msg["message"] if seg["type"] == "text"
                            ).strip()
                        elif "raw_message" in msg:
                            raw_content = msg["raw_message"]

                        if not raw_content: continue

                        role = "assistant" if msg_uid == self_id else "user"
                        ts = msg.get("time", time.time())
                        ts_str = datetime.datetime.fromtimestamp(ts).isoformat()
                        messages.append({"role": role, "content": raw_content, "timestamp": ts_str, "user_id": msg_uid})
                        
                    logger.info(f"[DailySharing] 私聊历史获取成功: {len(messages)} 条")

                except Exception as e:
                    logger.debug(f"[DailySharing] 私聊历史 API 获取失败: {e}")

            if not messages: return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                result["group_info"] = self._analyze_group_chat(messages)
            
            return result

        except Exception as e:
            logger.warning(f"[DailySharing] API 获取历史出错: {e}")
            return {}

    def _analyze_group_chat(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """分析群聊"""
        if not messages: return {}
        try:
            user_count = {}
            topics = []
            timestamps = []
            
            for msg in messages:
                if msg.get("role") == "user":
                    uid = msg.get("user_id", "unknown")
                    user_count[uid] = user_count.get(uid, 0) + 1
                
                content = msg.get("content", "")
                if len(content) > 5: topics.append(content[:50])
                if msg.get("timestamp"): timestamps.append(msg.get("timestamp"))
            
            active_users = sorted(user_count.items(), key=lambda x: x[1], reverse=True)[:3]
            cnt = len(messages)
            intensity = "high" if cnt > 10 else "medium" if cnt > 5 else "low"
            
            is_discussing = False
            if timestamps:
                try:
                    last_ts = timestamps[-1]
                    if isinstance(last_ts, str): last = datetime.datetime.fromisoformat(last_ts)
                    else: last = last_ts
                    if isinstance(last, (int, float)): last = datetime.datetime.fromtimestamp(last)
                    if (datetime.datetime.now() - last).total_seconds() < 600: is_discussing = True
                except: pass
            
            return {
                "recent_topics": topics[-5:],
                "active_users": [u for u, c in active_users],
                "chat_intensity": intensity,
                "message_count": cnt,
                "is_discussing": is_discussing,
            }
        except Exception as e:
            logger.warning(f"[DailySharing] 分析群聊出错: {e}")
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
            hint = "💡 群里正在热烈讨论，简短打个招呼即可" if discussing else "💡 可以活跃一下气氛"
        elif sharing_type == SharingType.NEWS: hint = "💡 选择可能引起群内讨论的新闻"
        elif sharing_type == SharingType.MOOD: hint = "💡 可以简单分享心情，但不要过于私人"
        else: hint = ""
        
        txt = f"\n\n【群聊状态】\n聊天热度: {intensity}\n消息数: {group_info.get('message_count', 0)} 条\n"
        if discussing: txt += "⚠️ 群里正在热烈讨论中！\n"
        if topics: txt += "\n【最近话题】\n" + "\n".join([f"• {t}..." for t in topics[-3:]])
        return txt + f"\n{hint}\n"

    def _format_private_chat_for_prompt(self, messages: List[Dict], sharing_type: SharingType) -> str:
        max_length = 500
        if sharing_type == SharingType.GREETING: hint = "💡 可以根据最近的对话内容打招呼"
        elif sharing_type == SharingType.MOOD: hint = "💡 可以延续最近的话题或感受"
        elif sharing_type == SharingType.NEWS: hint = "💡 可以根据对方的兴趣选择新闻"
        else: hint = "💡 可以自然地延续最近的对话"
        
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
        这样用户后续回复时，LLM 能知道 Bot 刚才说了什么。
        """
        try:
            # 1. 获取 ConversationManager
            conv_manager = self.context.conversation_manager
            
            # 2. 获取或创建会话 ID
            # target_umo 格式如 "QQ:GroupMessage:123456"
            conversation_id = await conv_manager.get_curr_conversation_id(target_umo)
            
            if not conversation_id:
                # 如果是全新的会话，初始化一个
                conversation_id = await conv_manager.new_conversation(target_umo)
            
            # 3. 获取现有历史
            conversation = await conv_manager.get_conversation(target_umo, conversation_id)
            
            current_history = []
            if conversation and conversation.history:
                try:
                    current_history = json.loads(conversation.history)
                except Exception:
                    current_history = []
            
            # 4. 构造 Assistant 消息 (包含图片描述)
            final_content = content
            if image_desc:
                # 【修改】不再截断，记录完整描述，防止细节丢失
                final_content += f"\n\n[发送了一张配图: {image_desc}]"

            # 注意：这里 role 是 assistant，因为是机器人说的
            bot_message = {
                "role": "assistant", 
                "content": final_content
            }
            current_history.append(bot_message)
            
            # 可选：限制历史记录长度，防止无限膨胀 (例如保留最近 100 条)
            if len(current_history) > 100:
                current_history = current_history[-100:]
            
            # 5. 写回数据库
            await conv_manager.update_conversation(target_umo, conversation_id, current_history)
            
            logger.debug(f"[上下文] ✅ 已将主动分享内容(含配图描述)写入对话历史: {target_umo}")
            
        except Exception as e:
            logger.warning(f"[上下文] 写入对话历史失败: {e}")

    # ==================== 记忆记录 ====================

    async def record_to_memos(self, target_umo: str, content: str, image_desc: str = None):
        if not self.memory_conf.get("record_sharing_to_memory", True): return
        memos = self._get_memos_plugin()
        if memos:
            try:
                full_text = content
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
