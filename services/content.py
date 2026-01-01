# services/content.py
import random
import re
from typing import Optional, Tuple, List, Dict
from astrbot.api import logger
from ..config import SharingType, TimePeriod, NEWS_SOURCE_MAP

class ContentService:
    def __init__(self, config, llm_func, context):
        self.config = config
        self.call_llm = llm_func
        self.context = context 
        self._last_rec_type = None 

    async def generate(self, stype: SharingType, period: TimePeriod, 
                      target_id: str, is_group: bool, 
                      life_ctx: str, chat_hist: str, news_data: tuple = None) -> Optional[str]:
        """统一生成入口"""
        persona = await self._get_persona()
        
        # 统一上下文数据包
        ctx_data = {
            "is_group": is_group,
            "life_hint": life_ctx, 
            "chat_hint": chat_hist, 
            "persona": persona
        }
        
        # 分发处理
        if stype == SharingType.GREETING:
            return await self._gen_greeting(period, ctx_data)
        elif stype == SharingType.NEWS:
            return await self._gen_news(news_data, ctx_data)
        elif stype == SharingType.MOOD:
            return await self._gen_mood(period, ctx_data)
        elif stype == SharingType.KNOWLEDGE:
            return await self._gen_knowledge(ctx_data)
        elif stype == SharingType.RECOMMENDATION:
            return await self._gen_rec(ctx_data)
        
        # 默认回退
        return await self._gen_greeting(period, ctx_data)

    async def _get_persona(self) -> str:
        """获取人设 prompt"""
        try:
            persona_id = self.config.get("persona_id", "")
            if persona_id:
                persona = await self.context.persona_manager.get_persona(persona_id)
                if persona:
                    return persona.system_prompt

            personality = await self.context.persona_manager.get_default_persona_v3()
            if personality and personality.get("prompt"):
                return personality["prompt"]
            
            return ""
        except Exception as e:
            logger.error(f"[Content] Get persona error: {e}")
            return ""

    def _get_preset_greeting(self, period: TimePeriod) -> str:
        """兜底问候语"""
        greetings = {
            TimePeriod.DAWN: ["🌃 还没睡吗？要注意休息哦~", "🌃 深夜了，早点休息吧~"],
            TimePeriod.MORNING: ["🌅 早上好！新的一天开始啦~", "🌅 早安~今天也要加油哦！"],
            TimePeriod.AFTERNOON: ["☀️ 中午好！辛苦啦，记得吃午饭~", "☀️ 下午好~要不要休息一下？"],
            TimePeriod.EVENING: ["🌇 傍晚好~今天辛苦啦！", "🌇 晚上好~准备吃晚饭了吗？"],
            TimePeriod.NIGHT: ["🌙 晚安~做个好梦哦！", "🌙 夜深了，要早点休息呀~"],
        }
        return random.choice(greetings.get(period, ["✨ 你好呀~"]))

    async def _gen_greeting(self, period: TimePeriod, ctx: dict):
        """生成问候"""
        # 1. 完善的时间段映射 (与原版完全一致)
        labels = {
            TimePeriod.DAWN: "凌晨",
            TimePeriod.MORNING: "早晨",
            TimePeriod.AFTERNOON: "下午",
            TimePeriod.EVENING: "傍晚",
            TimePeriod.NIGHT: "深夜",
        }
        emojis = {
            TimePeriod.DAWN: "🌃",
            TimePeriod.MORNING: "🌅",
            TimePeriod.AFTERNOON: "☀️",
            TimePeriod.EVENING: "🌇",
            TimePeriod.NIGHT: "🌙",
        }
        
        p_label = labels.get(period, "现在")
        p_emoji = emojis.get(period, "✨")

        is_group = ctx['is_group']
        
        group_instruction = """
【重要】群聊注意事项：
1. 简短（50-80字）
2. 如果正在讨论，不要打断
3. 避免过于私人化
4. 天气可以提，但不要详细说穿搭日程
5. 使用群聊语气
""" if is_group else """
【私聊提示】
1. 可以详细分享状态（50-100字）
2. 真诚、个人化
"""

        prompt = f"""现在是{p_label}，你要向{'群聊' if is_group else '用户'}发送一条温馨自然的问候。
{ctx['life_hint']}
{ctx['chat_hint']}
{group_instruction}

【重要】关于天气和场景：
- 生活上下文信息仅供参考
- 只在天气特殊或相关时提到
- {'群聊中避免详细穿搭日程' if is_group else '私聊中可以详细分享'}

【开头方式】（随机选择一种）
- 直接问候："{'大家' if is_group else ''}早上好~ 今天也要加油哦"
- 心情切入："今天心情不错呢"
- 计划切入："今天打算..."
- 感受切入："突然想到..."
- 天气切入：（仅在天气特殊时使用）

要求：
1. 以你的人设性格说话，真实自然
2. {'简短（50-80字）' if is_group else '可适当长一些（80-120字）'}  
3. 可以加入此刻的心情、想法或今日计划
4. 如果有真实状态信息，可以自然地提到（但不必全部提）
5. 直接输出内容，不要解释  

请生成{p_label}问候："""

        res = await self.call_llm(prompt, ctx['persona'])
        if res:
            return f"{p_emoji} {res}"
        else:
            return self._get_preset_greeting(period)

    async def _gen_mood(self, period, ctx):
        """生成心情"""
        is_group = ctx['is_group']
        prompt = f"""现在是{period.value}，你想和{'群里的大家' if is_group else '用户'}分享一下现在的心情或想法。
{ctx['life_hint']}
{ctx['chat_hint']}

【重要】
- 生活状态信息仅供参考，选择最有感触的1-2点即可
- {'群聊中避免过于私人，分享能引起共鸣的感受' if is_group else '私聊中可以详细分享内心想法'}
- 避免流水账式地罗列所有信息

【开头方式】（随机选择）
- 感受切入："今天心情..."
- 事件切入："刚才..."
- 想法切入："突然想到..."
- 状态切入："现在..."
- 问题切入："有没有觉得..."

要求：
1. 以你的人设性格说话，真实自然
2. 分享此刻的感受、想法或小感悟
3. 可适当用emoji（1-2个）
4. 字数：{'80-120字' if is_group else '120-150字'}
5. 直接输出内容
你的随想："""
        
        return await self.call_llm(prompt, ctx['persona'])

    async def _gen_news(self, news_data: Tuple[List, str], ctx: dict):
        """生成新闻"""
        is_group = ctx['is_group']
        
        # 1. 降级逻辑：如果没有新闻数据，生成纯文本新闻
        if not news_data:
            prompt = f"""你突然想和朋友分享一些最近的新闻见闻或有趣的事。
{ctx['life_hint']}
要求：
1. 可以是：社会热点、科技新闻、趣味事件等
2. 真实可信，不要编造假新闻
3. 以你的人设性格说话，真实自然
4. 可以加入你的看法和态度
5. 如果有当前场景信息，可以说明在什么情况下看到的
6. 字数：80-150字
7. 直接输出内容，不要有说明文字

直接输出："""
            return await self.call_llm(prompt, ctx['persona'])

        # 2. 正常逻辑：格式化新闻列表
        news_list, source_key = news_data
        source_name = NEWS_SOURCE_MAP[source_key]["name"]
        icon = NEWS_SOURCE_MAP[source_key]["icon"]
        
        # 解析分享数量配置 (支持 "1-2" 这种格式)
        raw_share_count = self.config.get("news_share_count", "1-2")
        try:
            if isinstance(raw_share_count, int):
                share_count = raw_share_count
            elif isinstance(raw_share_count, str):
                if "-" in raw_share_count:
                    min_c, max_c = map(int, raw_share_count.split("-"))
                    share_count = random.randint(min_c, max_c)
                else:
                    share_count = int(raw_share_count)
            else:
                share_count = 2
        except:
            share_count = 2

        items_limit = self.config.get("news_items_count", 5)
        
        # 构建新闻文本列表 (✅ 修复：还原热度数值格式化逻辑)
        news_text = f"【{source_name}】\n\n"
        for idx, item in enumerate(news_list[:items_limit], 1):
            hot = item.get("hot", "")
            title = item.get("title", "")
            
            if hot:
                # 格式化热度值 (123456 -> 12.3万)
                hot_str = str(hot)
                if hot_str.isdigit() and int(hot_str) > 10000:
                    hot_display = f"{int(hot_str) / 10000:.1f}万"
                else:
                    hot_display = hot_str
                news_text += f"{idx}. {title} {hot_display}\n"
            else:
                news_text += f"{idx}. {title}\n"

        # 生成 Prompt
        prompt = f"""你看到了今天的{source_name}，想选择{share_count}条和{'群里的大家' if is_group else '朋友'}分享。
{ctx['life_hint']}
{ctx['chat_hint']}

{source_name}：
{news_text}

【重要】关于场景描述：
- 生活上下文信息仅供参考，不是必须提及
- 只在以下情况提到场景：
1. 场景与新闻内容有关联
2. 场景引发了特别的感受
- 其他情况可以直接进入新闻内容
【开头方式】（必须自然提到平台"{source_name}"）
- "刚看到{source_name}上【某新闻】..."
- "在{source_name}刷到【某新闻】..."
- "{source_name}今天【某新闻】..."
- 其他自然的方式
{'【组织方式】' if share_count > 1 else ''}
{f'''- 可以逐条分享：每条新闻+你的看法
- 也可以串联：找出多条新闻的共同点''' if share_count > 1 else ''}
要求：
1. 以你的人设性格说话，真实自然
2. 选择{share_count}条你最感兴趣的热搜
3. {'对每条' if share_count > 1 else '对这条'}热搜要有自己的真实观点，不只是转述
4. 观点真诚，避免过度情绪化或标题党式表达
5. {'群聊中简洁有重点' if is_group else '可以详细展开你的想法'}  
6. 如果提到场景，要自然简短
7. 适当使用emoji（1-2个）
8. 用【】标注热搜标题
9. {'字数：120-150字' if is_group else '字数：150-200字'}
10. 直接输出分享内容
直接输出："""

        res = await self.call_llm(prompt, ctx['persona'], timeout=60)
        
        if res:
            return f"{icon} {res}"
        else:
            # 兜底：直接发送新闻列表
            return f"{icon} 今天的{source_name}~\n\n{news_text[:500]}"

    async def _gen_knowledge(self, ctx: dict):
        """生成冷知识"""
        is_group = ctx['is_group']
        topics = ["有趣的冷知识", "生活小技巧", "健康小常识", "历史小故事", "科学小发现", "心理学小知识"]
        topic = random.choice(topics)
        
        prompt = f"""请分享一个关于"{topic}"的有趣内容给{'群里的大家' if is_group else '朋友'}。
{ctx['life_hint']}
{ctx['chat_hint']}

【重要】关于场景描述：
- 当前场景信息仅供参考，不是必须提及
- 只在以下情况提到场景：
  1. 场景与知识内容有关联（如：天气冷→保暖知识）
  2. 想说明在什么情况下想到这个知识
- 大部分时候可以直接分享知识内容
【开头方式】（随机选择一种）
- 直接知识型："你知道吗..." / "今天学到一个..."
- 发现型："刚发现一个有趣的..." / "突然想到..."
- 提问型："有没有想过..." / "你们会不会好奇..."
- 场景切入型："刚才在...的时候想到..."（仅在场景相关时使用）
要求：
1. 选择一个真实、有趣、实用的知识点
2. 以你的人设性格说话，自然分享（而非说教）
3. {'语气轻松简洁' if is_group else '可以详细展开'}
4. 可以加入你的个人感想或小评论
5. 如果提到场景，要简短自然（不超过10字）
6. 可以适当用emoji（1-2个）
7. {'字数：100-150字' if is_group else '字数：150-200字'}
8. 直接输出分享内容
分享内容："""
        
        res = await self.call_llm(prompt, ctx['persona'])
        return f"📚 {res}" if res else None

    async def _gen_rec(self, ctx: dict):
        """生成推荐"""
        is_group = ctx['is_group']
        
        # 智能随机逻辑 (内存记忆)
        rec_types = ["书籍", "电影", "音乐", "动漫"]
        available = [t for t in rec_types if t != self._last_rec_type]
        if not available: available = rec_types
        
        rec_type = random.choice(available)
        self._last_rec_type = rec_type # 更新状态
        
        logger.info(f"[Content] Rec type: {rec_type}")

        prompt = f"""你想向{'群里的大家' if is_group else '朋友'}推荐一个{rec_type}。
{ctx['life_hint']}
{ctx['chat_hint']}

【重要】必须包含的元素：
1. 明确的推荐动作（必须出现）：
   • "推荐你看/听..."
   • "想推荐给你..."
   • "你可以试试..."
   • "强烈推荐..."
   等类似表达

2. 推荐内容的基本信息：
   • {rec_type}的名称（必须真实存在）
   • 作者/导演/歌手等创作者
   • 简短的介绍

3. 推荐理由：
   • 为什么喜欢/觉得好
   • 适合什么情况看/听
   • 打动你的点是什么

【关于场景描述】
- 当前场景信息仅供参考，不是必须提及
- 只在以下情况提到场景：
  1. 场景与推荐内容有关联
  2. 想说明在什么情况下接触到这个内容
- 其他情况可以直接进入推荐内容

【开头方式示例】（必须包含"推荐"）
✅ 好的开头：
• "最近看了一本书，想推荐给你——《xxx》"
• "推荐你听听xxx的《xxx》，真的很棒"
• "强烈推荐这部电影《xxx》，因为..."
• "你一定要看看《xxx》这本书..."

❌ 避免的开头：
• "最近在看《xxx》..." (缺少推荐动作)
• "刚看完《xxx》..." (只是分享，不是推荐)
• "这本书超级棒！！！绝对不能错过！！！" (营销号语气)

【格式要求】
1. 以你的人设性格说话，真实自然
2. 只推荐1个真实存在的{rec_type} 
3. 开头必须有明确的推荐表达
4. 真诚推荐，避免营销号式的夸张表达 
5. 可以适当用emoji（1-2个）
6. {'字数：80-120字' if is_group else '字数：120-180字'} 
7. 直接输出推荐内容，不要有"以下是..."等说明文字

请生成推荐："""

        res = await self.call_llm(prompt, ctx['persona'])
        return f"💡 {res}" if res else None
