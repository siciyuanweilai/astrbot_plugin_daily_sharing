# services/context.py
import datetime
import time
from typing import Optional, Dict, Any, List
from astrbot.api import logger
from ..config import SharingType

class ContextService:
    def __init__(self, context_obj, config):
        self.context = context_obj
        self.config = config
        self._life_plugin = None
        self._memos_plugin = None
        
        unified_conf = self.config.get("context_conf", {})
        
        self.life_conf = unified_conf
        self.history_conf = unified_conf
        self.memory_conf = unified_conf

        self.image_conf = self.config.get("image_conf", {})

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

    def _get_bot_instance(self, pm, adapter_id: str):
        # --- 方案 1: 标准精确查找 (性能最好) ---
        try:
            inst = pm.get_inst(adapter_id)
            if inst and hasattr(inst, "bot") and inst.bot:
                return inst.bot
        except: pass

        # --- 方案 2: 全局暴力搜索 (解决 ID 不匹配问题) ---
        try:
            for attr_name in dir(pm):
                if attr_name.startswith("__"): continue 
                try:
                    val = getattr(pm, attr_name)
                    # 检查字典 (通常是 insts 字典)
                    if isinstance(val, dict):
                        for v in val.values():
                            if hasattr(v, "bot") and v.bot:
                                return v.bot
                    # 检查列表
                    elif isinstance(val, list):
                        for v in val:
                            if hasattr(v, "bot") and v.bot:
                                return v.bot
                except: continue
        except Exception:
            pass
            
        return None

    # ==================== 生活上下文 (Life Scheduler) ====================

    async def get_life_context(self) -> Optional[str]:
        """获取生活上下文"""
        if not self.life_conf.get("enable_life_context", True): 
            return None
            
        if not self._life_plugin: 
            self._life_plugin = self._find_plugin("life_scheduler")
            
        if self._life_plugin and hasattr(self._life_plugin, 'get_life_context'):
            try: 
                ctx = await self._life_plugin.get_life_context()
                if ctx and len(ctx.strip()) > 10:
                    return ctx
            except Exception as e: 
                logger.warning(f"[上下文] Life Scheduler 插件调用出错: {e}")
        return None

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
        
        # 如果是心情分享，且群聊热度高，则不带生活状态（避免在大家聊得火热时突然插一句无关的“我今天好累”）
        if sharing_type == SharingType.MOOD and group_info and group_info.get("chat_intensity") == "high":
            return ""

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

        bot = self._get_bot_instance(self.context.platform_manager, adapter_id)

        if not bot:
            logger.warning(f"[DailySharing] ❌ 无法找到任何可用的 Bot 实例。")
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

