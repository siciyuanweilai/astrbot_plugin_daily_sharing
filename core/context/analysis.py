from .shared import Any, Dict, List, SharingType, datetime, logger, time
from .shared import DAILY_SHARING_SOURCE


class ContextHistoryAnalysisMixin:
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
            
            # 只看最近若干条，减少计算量，但后续仍要过滤时间。
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
            # 高热度：20 分钟内消息数超过阈值的一半
            threshold_high = check_count * 0.5 
            # 中热度：20 分钟内消息数超过较低阈值
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
            logger.warning(f"[每日分享] 分析群聊热度出错: {e}")
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
