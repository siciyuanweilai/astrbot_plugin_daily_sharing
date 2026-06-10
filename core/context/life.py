from .shared import Optional, SharingType, datetime, logger


class ContextLifeMixin:
    async def get_life_context(self) -> Optional[str]:
        """获取生活上下文 (支持解析 JSON 数据)"""
        if not self.life_conf.get("enable_life_context", True): 
            return None
            
        if not self._life_plugin: 
            # 尝试用生活日程插件关键字查找。
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
                logger.warning(f"[上下文] 生活日程插件方法调用出错: {e}")
        
        return None

    def _parse_life_data(self, data: dict) -> str:
        """解析生活日程插件返回的 JSON 数据为自然语言"""
        try:
            parts = []
            
            # 1. 天气
            weather = data.get("weather", "")
            if weather: parts.append(f"【今日天气】{weather}")
            
            # 2. 穿搭
            outfit = data.get("outfit", "")
            if outfit: parts.append(f"【今日穿搭】{outfit}")
            
            # 3. 完整元数据
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
                        logger.debug(f"[每日分享] 跳过无效时间线条目 {item}: {e}")
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
        
        # === 针对不同类型的提示词 ===
        
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
        # 私聊直接使用完整上下文，让大语言模型知道所有细节
        
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
