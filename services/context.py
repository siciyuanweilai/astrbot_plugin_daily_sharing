# services/context.py
import datetime
from typing import Optional, Dict, Any, List
from astrbot.api import logger
from ..config import SharingType

class ContextService:
    def __init__(self, context_obj, config):
        self.context = context_obj
        self.config = config
        self._life_plugin = None
        self._memos_plugin = None

    def _find_plugin(self, keyword: str):
        try:
            # 遍历所有已加载的插件
            plugins = self.context.get_all_stars()
            for plugin in plugins:
                if keyword in getattr(plugin, "name", ""):
                    return getattr(plugin, "star_cls", None)
        except Exception as e:
            logger.warning(f"[Context] Find plugin '{keyword}' error: {e}")
        return None

    async def get_life_context(self) -> Optional[str]:
        """获取生活上下文"""
        if not self.config.get("enable_life_context", True): 
            return None
            
        if not self._life_plugin: 
            self._life_plugin = self._find_plugin("life_scheduler")
            
        if self._life_plugin and hasattr(self._life_plugin, 'get_life_context'):
            try: 
                ctx = await self._life_plugin.get_life_context()
                if ctx and len(ctx.strip()) > 10:
                    return ctx
            except Exception as e: 
                logger.warning(f"[Context] Life Scheduler error: {e}")
        return None

    def format_life_context(self, context: str, sharing_type: SharingType, is_group: bool, group_info: dict = None) -> str:
        """格式化生活上下文"""
        if not context: return ""
        
        if is_group:
            # === 群聊格式化 ===
            if not self.config.get("life_context_in_group", True): return ""
            
            # Mood 且群聊热度高时不发送
            if sharing_type == SharingType.MOOD and group_info and group_info.get("chat_intensity") == "high":
                return "" 
            
            lines = context.split('\n')
            weather, period, busy = None, None, False
            for line in lines:
                if '天气' in line or '温度' in line: weather = line.strip()
                elif '时段' in line: period = line.strip()
                elif '今日计划' in line or '约会' in line: busy = True
            
            hint = "\n\n【你的状态】\n"
            if sharing_type == SharingType.GREETING:
                if weather: hint += f"{weather}\n💡 可以提醒大家注意天气\n"
                if period: hint += f"{period}\n"
                if busy: hint += "今天有些安排\n💡 可以简单提一下你今天比较忙\n"
                return hint
            elif sharing_type == SharingType.NEWS:
                if weather: return f"\n\n【当前场景】\n{weather}\n💡 可以说在什么天气下看到这个新闻\n"
            elif sharing_type == SharingType.MOOD:
                hint_str = f"\n\n【你的状态】\n{weather or ''}\n"
                if busy: hint_str += "今天有些事情要做\n"
                return hint_str + "💡 可以简单分享心情，但不要过于私人\n"
            return ""

        else:
            # === 私聊格式化 ===
            if sharing_type == SharingType.GREETING:
                return f"\n\n【你的真实状态】\n{context}\n\n💡 可以结合上面的真实状态（天气、穿搭、今日计划）来打招呼\n"
            elif sharing_type == SharingType.MOOD:
                return f"\n\n【你现在的状态】\n{context}\n\n💡 可以结合当前的穿搭、天气、心情、约会等分享感受\n"
            elif sharing_type == SharingType.NEWS:
                # 仅保留天气、穿搭、约会行
                lines = [l for l in context.split('\n') if '天气' in l or '穿搭' in l or '约会' in l]
                if lines:
                    return f"\n\n【你当前在做什么】\n{chr(10).join(lines[:3])}\n\n💡 可以说明你在什么场景下看到这个新闻\n"
                return ""
            elif sharing_type in (SharingType.KNOWLEDGE, SharingType.RECOMMENDATION):
                lines = [l for l in context.split('\n') if '天气' in l or '时段' in l]
                if lines:
                    return f"\n\n【当前场景】\n{chr(10).join(lines[:2])}\n\n💡 可以简单提一下当前场景\n"
                return ""
        
        return ""

    async def get_history_data(self, target_umo: str, is_group: bool) -> Dict[str, Any]:
        """获取历史记录"""
        if not self.config.get("enable_chat_history", True): return {}
        
        if not self._memos_plugin: 
            self._memos_plugin = self._find_plugin("astrbot_plugin_memos_integrator")
        
        if not self._memos_plugin: return {}

        try:
            default_limit = 10
            conf_limit = self.config.get("chat_history_count", default_limit)
            
            # 群聊 limit 计算
            if is_group:
                group_conf = self.config.get("group_chat_history_count", conf_limit * 2)
                limit = min(group_conf, 20)
            else:
                limit = conf_limit

            # 调用参数
            memories = await self._memos_plugin.memory_manager.retrieve_relevant_memories(
                query="最近的对话", 
                user_id=target_umo, 
                conversation_id="", 
                limit=limit
            )
            
            if not memories: return {}

            messages = []
            for mem in memories:
                # 类型映射逻辑
                m_type = mem.get("type", "fact")
                role = "system" if m_type == "preference" else "assistant"
                
                messages.append({
                    "role": role,
                    "content": mem.get("content", ""),
                    "timestamp": mem.get("timestamp", ""),
                    "user_id": mem.get("user_id", "")
                })
            
            result = {"messages": messages, "is_group": is_group}
            if is_group:
                result["group_info"] = self._analyze_group_chat(messages)
            return result

        except Exception as e:
            logger.error(f"[Context] History error: {e}")
            return {}

    def _analyze_group_chat(self, messages: List[Dict]) -> Dict[str, Any]:
        if not messages: return {}
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
        
        cnt = len(messages)
        intensity = "high" if cnt > 10 else ("medium" if cnt > 5 else "low")

        is_discussing = False
        if timestamps:
            try:
                last = datetime.datetime.fromisoformat(timestamps[-1])
                if (datetime.datetime.now() - last).total_seconds() < 300:
                    is_discussing = True
            except: pass
            
        return {
            "recent_topics": topics[-5:],
            "chat_intensity": intensity,
            "message_count": cnt,
            "is_discussing": is_discussing
        }

    def check_group_strategy(self, group_info: Dict) -> bool:
        """检查群聊策略"""
        strategy = self.config.get("group_share_strategy", "cautious")
        is_discussing = group_info.get("is_discussing", False)
        intensity = group_info.get("chat_intensity", "low")

        if strategy == "cautious":
            if is_discussing and intensity == "high": return False
        elif strategy == "minimal":
            if is_discussing or intensity != "low": return False
        return True

    def format_history_prompt(self, history_data: Dict, sharing_type: SharingType) -> str:
        """格式化历史记录提示词"""
        if not history_data or not history_data.get("messages"): return ""
        msgs = history_data["messages"]
        max_length = 500
        
        if history_data.get("is_group"):
            # === 群聊历史 Prompt ===
            g_info = history_data.get("group_info", {})
            intensity = g_info.get("chat_intensity", "low")
            discussing = g_info.get("is_discussing", False)
            topics = g_info.get("recent_topics", [])
            
            hint = ""
            if sharing_type == SharingType.GREETING:
                if discussing:
                    hint = "💡 群里正在热烈讨论，简短打个招呼即可"
                else:
                    hint = "💡 可以活跃一下气氛"
            elif sharing_type == SharingType.NEWS:
                hint = "💡 选择可能引起群内讨论的新闻"
            elif sharing_type == SharingType.MOOD:
                hint = "💡 可以简单分享心情，但不要过于私人"
            
            txt = f"\n\n【群聊状态】\n聊天热度: {intensity}\n消息数: {g_info.get('message_count', 0)} 条\n"
            if discussing: txt += "⚠️ 群里正在热烈讨论中！\n"
            if topics:
                txt += "\n【最近话题】\n" + "\n".join([f"{i+1}. {t}..." for i, t in enumerate(topics[-3:])])
            return txt + f"\n{hint}\n"
        else:
            # === 私聊历史 Prompt ===
            hint = "💡 可以自然地延续最近的对话"
            if sharing_type == SharingType.GREETING: hint = "💡 可以根据最近的对话内容打招呼"
            elif sharing_type == SharingType.MOOD: hint = "💡 可以延续最近的话题或感受"
            elif sharing_type == SharingType.NEWS: hint = "💡 可以根据对方的兴趣选择新闻"
            
            lines = []
            total_len = 0
            # 倒序取，保证最近的消息在最下面
            for m in reversed(msgs[-5:]):
                role = "用户" if m["role"] == "user" else "你"
                content = m["content"]
                if len(content) > 100: content = content[:100] + "..."
                
                line = f"{role}: {content}"
                if total_len + len(line) > max_length: break
                
                lines.insert(0, line)
                total_len += len(line)
            
            return "\n\n【最近的对话】\n" + "\n".join(lines) + f"\n\n{hint}\n"

    async def record_to_memos(self, target_umo: str, content: str, image_desc: str = None):
        """记录到 Memos"""
        if not self.config.get("record_sharing_to_memory", True): return
        
        if not self._memos_plugin:
            self._memos_plugin = self._find_plugin("astrbot_plugin_memos_integrator")
        
        if self._memos_plugin:
            try:
                full_text = content
                if image_desc: 
                    if self.config.get("record_image_description", True):
                        full_text += f"\n[配图: {image_desc}]"
                    else:
                        full_text += "\n[已发送配图]"
                elif image_desc is not None: 
                    full_text += "\n[已发送配图]"

                cid = await self.context.conversation_manager.get_curr_conversation_id(target_umo)
                if not cid: cid = await self.context.conversation_manager.new_conversation(target_umo)

                await self._memos_plugin.memory_manager.add_message(
                    messages=[{"role": "assistant", "content": full_text}],
                    user_id=target_umo, conversation_id=cid
                )
                logger.info(f"[Context] Recorded to Memos for {target_umo}")
            except Exception as e: 
                logger.warning(f"[Context] Record error: {e}")
