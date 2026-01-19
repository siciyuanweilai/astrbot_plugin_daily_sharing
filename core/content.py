# core/content.py
import random
import json
import os
import re
import aiofiles
import asyncio
from functools import partial
from datetime import datetime
from typing import Optional, Tuple, List, Dict
from astrbot.api import logger
from ..config import SharingType, TimePeriod

# 新闻源配置
NEWS_SOURCE_MAP = {
    "zhihu": {"name": "知乎热榜", "icon": "📚"},
    "weibo": {"name": "微博热搜", "icon": "🔥"},
    "bili": {"name": "B站热搜", "icon": "📺"},
    "xiaohongshu": {"name": "小红书热搜", "icon": "📕"},
    "douyin": {"name": "抖音热搜", "icon": "🎵"},
    "toutiao": {"name": "头条热搜", "icon": "🗞️"},
    "baidu": {"name": "百度热搜", "icon": "🔍"},
    "tencent": {"name": "腾讯热搜", "icon": "🐧"},
}

# ==================== LLM生成内容库 ====================

# 知识库细分
KNOWLEDGE_CATS = {
    "有趣的冷知识": ["动物行为", "人体奥秘", "地理冷知识", "历史误区", "语言文字"],
    "生活小技巧": ["收纳整理", "厨房妙招", "数码技巧", "省钱攻略", "应急处理"],
    "健康小常识": ["睡眠科学", "饮食营养", "运动误区", "心理健康", "护眼护肤"],
    "历史小故事": ["古代发明", "名人轶事", "文明起源", "战争细节", "文物故事"],
    "科学小发现": ["天文宇宙", "量子物理", "生物进化", "未来科技", "AI发展"],
    "心理学小知识": ["认知偏差", "社交心理", "情绪管理", "微表情", "行为经济学"]
}

# 推荐库细分
REC_CATS = {
    "书籍": ["悬疑推理", "当代文学", "历史传记", "科普新知", "商业思维", "治愈系绘本", "科幻神作"],
    "电影": ["高分冷门", "烧脑科幻", "经典黑白", "是枝裕和风", "赛博朋克", "奥斯卡遗珠", "纪录片"],
    "音乐": ["后摇/纯音", "爵士/蓝调", "独立民谣", "CityPop", "古典入门", "电影原声", "小众乐队"],
    "动漫": ["治愈日常", "硬核科幻", "热血运动", "悬疑智斗", "吉卜力风", "今敏风格", "冷门佳作"],
    "美食": ["地方特色小吃", "创意懒人菜", "季节限定", "深夜罪恶美食", "传统糕点", "异国风味"]
}

class ContentService:
    def __init__(self, config: Dict, llm_func, context, state_file: str, news_service=None):
        """
        初始化内容生成服务
        """
        self.config = config
        self.call_llm = llm_func
        self.context = context 
        self.state_file = state_file 
        self.news_service = news_service
        
        self.news_conf = self.config.get("news_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        # 获取上下文配置
        self.context_conf = self.config.get("context_conf", {})

    async def generate(self, stype: SharingType, period: TimePeriod, 
                      target_id: str, is_group: bool, 
                      life_ctx: str, chat_hist: str, news_data: tuple = None) -> Optional[str]:
        """统一生成入口"""
        persona = await self._get_persona()
        
        now = datetime.now()
        date_str = now.strftime("%Y年%m月%d日") 
        time_str = now.strftime("%H:%M")       
        
        ctx_data = {
            "target_id": target_id, 
            "is_group": is_group,
            "life_hint": life_ctx or "", 
            "chat_hint": chat_hist or "", 
            "persona": persona,
            "period_label": self._get_period_label(period), 
            "date_str": date_str,         
            "time_str": time_str          
        }
        
        try:
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
            
            return await self._gen_greeting(period, ctx_data)
            
        except Exception as e:
            logger.error(f"[内容服务] 生成内容出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    # ==================== 状态文件管理 ====================
    @staticmethod
    def _read_json_sync(path: str) -> dict:
        """同步读取辅助函数 (供 executor 调用)"""
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    @staticmethod
    def _write_json_sync(path: str, data: dict):
        """同步写入辅助函数 (供 executor 调用)"""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def _load_state_safe(self) -> dict:
        """安全加载状态文件 (异步非阻塞)"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._read_json_sync, self.state_file)
        except Exception as e:
            logger.warning(f"[内容服务] 加载状态文件失败: {e}")
            return {}

    async def _save_state_safe(self, state: dict):
        """安全保存状态文件 (异步非阻塞)"""
        try:
            current_state = await self._load_state_safe()
            current_state.update(state) 
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.state_file, current_state)
        except Exception as e:
            logger.error(f"[内容服务] 保存状态文件失败: {e}")

    # ==================== 辅助方法 ====================

    def _get_period_label(self, period: TimePeriod) -> str:
        labels = {
            TimePeriod.DAWN: "凌晨", TimePeriod.MORNING: "早晨",
            TimePeriod.AFTERNOON: "下午", TimePeriod.EVENING: "傍晚",
            TimePeriod.NIGHT: "深夜",
        }
        return labels.get(period, "现在")

    async def _get_persona(self) -> str:
        try:
            persona_id = self.llm_conf.get("persona_id", "")
            if persona_id:
                persona = await self.context.persona_manager.get_persona(persona_id)
                if persona:
                    return persona.system_prompt

            personality = await self.context.persona_manager.get_default_persona_v3()
            if personality and personality.get("prompt"):
                return personality["prompt"]
            return ""
        except Exception as e:
            logger.error(f"[内容服务] 获取人设失败: {e}")
            return ""

    async def _update_history(self, key_type: str, content_summary: str, target_id: str):
        """更新历史记录，防止重复 (区分对象)"""
        try:
            state = await self._load_state_safe()
            
            # 初始化层级结构: targets_history -> target_id -> key_type
            if "targets_history" not in state:
                state["targets_history"] = {}
            if target_id not in state["targets_history"]:
                state["targets_history"][target_id] = {}
            
            # 获取特定对象的历史列表
            history = state["targets_history"][target_id].get(key_type, [])
            
            # 添加新记录（只保留前20个字作为特征）
            summary = content_summary.split("\n")[0][:15].replace("推荐", "").replace("分享", "")
            history.append(summary)
            
            # 只保留最近 20 条
            if len(history) > 20:
                history = history[-20:]
            
            # 更新回 state
            state["targets_history"][target_id][key_type] = history
            
            await self._save_state_safe(state)
        except Exception as e:
            logger.warning(f"[内容服务] 更新历史记录失败: {e}")

    async def _get_history_str(self, key_type: str, target_id: str) -> str:
        """获取历史记录字符串用于 Prompt (区分对象)"""
        state = await self._load_state_safe()
        
        # 安全获取嵌套字典
        history = state.get("targets_history", {}).get(target_id, {}).get(key_type, [])
        
        if not history:
            return "无"
        return "、".join(history)

    # ==================== 生成逻辑 ====================

    async def _gen_greeting(self, period: TimePeriod, ctx: dict):
        emojis = {
            TimePeriod.DAWN: "🌃", TimePeriod.MORNING: "🌅",
            TimePeriod.AFTERNOON: "☀️", TimePeriod.EVENING: "🌇",
            TimePeriod.NIGHT: "🌙",
        }
        p_label = ctx['period_label']
        p_emoji = emojis.get(period, "✨")
        is_group = ctx['is_group']
        
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)

        # 1. 称呼控制
        address_rule = ""
        if is_group:
            address_rule = "面向群友，自然使用'大家'或不加称呼。"
        else:
            address_rule = "【重要】这是一对一私聊，严禁使用'大家'、'你们'。请使用'你'或直接说内容。"

        # 2. 避免尴尬指令 (根据配置动态调整)
        context_instruction = ""
        if is_group:
            if allow_detail:
                # 允许分享细节
                context_instruction = """
【群聊策略 - 允许状态分享】
- 你可以提及你的具体日程，但这必须是为了引出话题。
- 严禁使用：“看大家聊得这么开心”、“既然大家都在潜水”等评价群氛围的话。
- 请完全忽略群聊的上下文，直接开启温馨自然的问候。
"""
            else:
                # 默认脱敏
                context_instruction = """
【严重警告 - 拒绝尴尬开头】
- 严禁使用：“看大家聊得这么开心”、“既然大家都在潜水”等评价群氛围的话。
- 请完全忽略群聊的上下文，直接开启温馨自然的问候。
"""
        else:
            context_instruction = "真诚、个人化"

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({p_label})
你现在要向{'群聊' if is_group else '私聊'}发送一条温馨自然的问候。

{ctx['life_hint']}
{ctx['chat_hint']}
{context_instruction}
{address_rule}

【重要】关于场景状态：
- 如果提供了生活状态（如天气、忙碌/空闲）：
  - 群聊：可以简单带过状态和活动来让问候更真实。
  - 私聊：请结合你当前具体的状态和活动来让问候更真实。

【开头方式】（自然直接）
- 早安/晚安问候："{'大家' if is_group else ''}早安/晚安 "
- 心情切入："今天心情不错呢"
- 状态切入："刚忙完..." / "今天有点..."
- 天气切入：（仅在天气特殊时使用）

要求：
1. 以你的人设性格说话，真实自然
2. 基于当前真实时间问候
3. 忽略群聊历史，直接开启新问候
4. 如果是【早晨】时段，文案开头必须带上温馨的早安问候
5. 如果是【深夜】时段，文案末尾必须带上温馨的晚安问候
6. {'简短（50-80字）' if is_group else '可适当长一些（50-80字）'}
7. 直接输出内容，不要解释  

请生成{p_label}问候："""

        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])
        if res:
            return f"{p_emoji} {res}"
        return None  

    async def _gen_mood(self, period, ctx):
        is_group = ctx['is_group']
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 1. 称呼控制
        address_rule = ""
        if not is_group:
            address_rule = "\n【重要：私聊模式】严禁使用'大家'、'你们'。请把你当做在和单个朋友聊天。"

        # 2. 避免尴尬 (根据配置调整)
        vibe_check = ""
        if is_group:
            if allow_detail:
                vibe_check = "【群聊策略】可以提及你正在做的具体事情，但要把它转化为一种大家都能懂的情绪。"
            else:
                vibe_check = """
【严重警告 - 拒绝尴尬开头】
- 严禁使用：“看你们聊得这么热火朝天”、“看大家都在潜水”等评价群氛围的话。
- 请完全忽略群聊的上下文，直接分享你自己的事情。
"""

        # 3. 共鸣策略
        resonance_guide = ""
        if is_group:
            resonance_guide = f"""
【群聊共鸣策略 - 基于日程的"同频"】
请不要使用刻板的时间情绪（如"早上一定困"、"晚上一定emo"），而是**深度挖掘你当前日程状态（见上方【生活状态】）背后的普世感受**：
1. **若你当前【忙碌/工作/学习】**：
   - 寻找"奋斗者"的共鸣：比如对咖啡的渴望、大脑过载的恍惚、解决难题后的短暂爽感、或是单纯的"不想干了"的小牢骚。
   - *目标*：让正在搬砖的群友觉得"原来你也一样"。
2. **若你当前【休闲/摸鱼/饮食】**：
   - 寻找"享受当下"的共鸣：比如食物带来的瞬间治愈、被窝的引力、忙里偷闲的窃喜、或是对即将到来的周末/下班的期待。
   - *目标*：成为群里的"气氛组"，带动轻松话题。
3. **若你当前【运动/外出/通勤】**：
   - 寻找"身体感官"的共鸣：比如早高峰的拥挤、运动后的酸爽与多巴胺、路边看到好看风景的惊喜。
**核心要求**：
情绪必须**紧扣**你正在做的事情。不要为了强行共鸣而脱离你的人设日程。请将你的人设状态与这种大众情绪结合。
"""
        else:
            resonance_guide = "【私聊策略】像对亲密好友一样，分享一点私人的、细腻的小情绪，或者一个小秘密。"

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你想和{'群聊' if is_group else '私聊'}分享一下现在的心情或想法。

{ctx['life_hint']}
{ctx['chat_hint']}
{vibe_check}
{address_rule}
{resonance_guide}

【重要：如何结合当下状态】
- 群聊（寻找话题点）：
  不要干巴巴地汇报你在干什么。
  请把你【正在做的事】作为引子，转化为一种社交话题或情绪宣泄。
- 私聊（分享沉浸感）：
  请深入描述你【正在做的事】中的某个具体细节，展现你此时此刻的内心独白。

要求：
1. 以你的人设性格说话，真实自然
2. 分享此刻的感受、想法或小感悟
3. 忽略群聊历史，直接开启新话题
4. 可适当用emoji（1-2个）
5. 基于当前真实时间感悟
6. 字数：{'50-80字' if is_group else '50-80字'}
7. 直接输出内容
你的随想："""
        
        return await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])

    async def _gen_news(self, news_data: Tuple[List, str], ctx: dict):
        """生成新闻分享，无数据则不生成"""
        if not news_data:
            logger.warning("[内容服务] 未获取到新闻数据，取消分享")
            return None

        is_group = ctx['is_group']
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)

        news_list, source_key = news_data
        source_config = NEWS_SOURCE_MAP.get(source_key, {"name": "热搜", "icon": "📰"})
        source_name = source_config["name"]
        icon = source_config["icon"]
        
        raw_share_count = self.news_conf.get("news_share_count", "1-2")
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

        items_limit = self.news_conf.get("news_items_count", 5)
        
        news_text = f"【{source_name}】\n\n"
        for idx, item in enumerate(news_list[:items_limit], 1):
            hot = item.get("hot", "")
            title = item.get("title", "")
            if hot:
                hot_str = str(hot)
                if hot_str.isdigit() and int(hot_str) > 10000:
                    hot_display = f"{int(hot_str) / 10000:.1f}万"
                else:
                    hot_display = hot_str
                news_text += f"{idx}. {title} {hot_display}\n"
            else:
                news_text += f"{idx}. {title}\n"
        
        # 称呼控制
        address_rule = ""
        if not is_group:
            address_rule = "【私聊模式】不要说'大家'、'你们'。请假装只分享给**你对面这一个人**看。"

        # 针对不同模式的场景融合指令
        context_instruction = ""
        if is_group:
            if allow_detail:
                 context_instruction = "- 场景参考：必须基于上方提供的【真实状态】。如果是外出探索，就说是“在路上刷到的”；如果是工作，就说是“忙里偷闲”。"
            else:
                 context_instruction = "- 场景参考：请忽略环境干扰，专注于新闻本身。简单带过你的状态即可。"
        else:
            context_instruction = """
- **场景合理化（重要）**：
  必须基于上方提供的【真实生活状态】来设定你“在哪里看新闻”。
  - 严禁违背日程：如果日程是“外出/约会”，必须描述为在途中、躲雨时或到达目的地后看的，严禁说“在被窝里”或“刚醒”。
  - 即使天气不好，也要按照日程设定的“外出人设”来发言（例如：“虽然下雨，但在外面躲雨的时候看到了这个...”）。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你看到了今天的{source_name}，想选择{share_count}条和{'群聊' if is_group else '私聊'}分享。

{ctx['life_hint']}
{ctx['chat_hint']}

{source_name}：
{news_text}

【严重警告 - 拒绝尴尬开头】
- 严禁说：“看大家聊得这么开心”、“既然大家都在”、“看你们都在讨论XX”。
- 请完全忽略群聊的上下文，直接开启这个新闻话题。
{address_rule}

【重要：场景融合与一致性】
{context_instruction}
【特别强调】：请检查你的穿搭和日程，如果你的穿搭是外出/约会的（如大衣、制服），绝对不要描述自己躺在床上或刚睡醒。这不符合逻辑。

【开头方式】（必须自然提到平台"{source_name}"）
- "忙里偷闲刷了下{source_name}..."
- "刚在{source_name}看到..."
- "休息的时候看了眼{source_name}..."
- "{source_name}今天这个..."
- 其他自然的方式
{'【组织方式】' if share_count > 1 else ''}
{f'''- 可以逐条分享：每条新闻+你的看法
- 也可以串联：找出多条新闻的共同点''' if share_count > 1 else ''}

要求：
1. 以你的人设性格说话，真实自然
2. 选择{share_count}条你最感兴趣的热搜
3. {'对每条' if share_count > 1 else '对这条'}热搜要有自己的真实观点，不只是转述
4. 观点真诚，避免过度情绪化或标题党式表达
5. {'群聊中简洁有重点' if is_group else '私聊可以详细展开想法，并结合你当下的状态'}
6. 适当使用emoji（1-2个）
7. 用【】标注热搜标题
8. {'字数：120-150字' if is_group else '字数：150-200字'}
9. 直接输出分享内容
直接输出："""

        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'], timeout=60)
        
        if res:
            return f"{icon} {res}"
        return None 

    async def _gen_knowledge(self, ctx: dict):
        """生成知识分享，API 失败则使用 LLM 兜底"""
        if not self.news_service:
            logger.warning("[内容服务] 无法调用百科服务，无法查询相关资料，取消分享")
            return None

        is_group = ctx['is_group']
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 随机选择大类和子类
        main_cat = random.choice(list(KNOWLEDGE_CATS.keys()))
        sub_cat = random.choice(KNOWLEDGE_CATS[main_cat])
        target_id = ctx['target_id'] # 获取ID
        
        # 获取历史调用
        history_str = await self._get_history_str("knowledge", target_id) 
        
        logger.info(f"[内容服务] 知识方向: {main_cat} - {sub_cat}")

        target_keyword = ""
        baike_context = ""
        
        # 1. 快速生成一个关键词
        pre_prompt = f"""
请输出一个属于【{main_cat}-{sub_cat}】领域的知识点关键词。
【已分享过的列表(请绝对避开)】
{history_str}
要求：
1. 话题范围灵活：可以是【冷知识】、【常见误区】、【实用技巧】或【有趣现象】。
2. 核心标准是“有趣”或“有用”：
   - 如果是生活类，优先选实用性强的。
   - 如果是科普类，优先选反直觉或颠覆认知的。
   - 不要刻意追求“生僻难懂”，大众感兴趣的话题也可以。
3. 严禁输出上述“已分享过的列表”中的内容，必须换一个新的。 
4. 只输出关键词，不要任何解释，不要标点符号。
"""
        kw_res = await self.call_llm(prompt=pre_prompt, system_prompt="你是一个眼光独到的科普博主和生活达人。", timeout=15)
        
        if not kw_res:
            logger.warning("[内容服务] 无法生成知识关键词，取消分享")
            return None

        target_keyword = kw_res.strip().split("\n")[0].replace("。", "")
        
        # 2. 查百科 (增加兜底逻辑)
        info = await self.news_service.get_baike_info(target_keyword)
        
        if info:
            # 命中 API
            baike_context = f"\n\n【事实依据（不要捏造）】\n{info}\n"
            logger.info(f"[内容服务] 百科API命中: {target_keyword}")
        else:
            # 未命中 API，使用 LLM 兜底
            logger.warning(f"[内容服务] 百科未命中【{target_keyword}】，将使用 LLM 内部知识库兜底")
            baike_context = f"\n\n【提示】暂无外部资料，请基于你自己的知识库，准确介绍【{target_keyword}】。"
        
        # 3. 称呼控制
        address_rule = ""
        if is_group:
            address_rule = "面向群友，可以使用'大家'、'你们'。"
        else:
            address_rule = "【重要：私聊模式】🚫 严禁使用'大家'、'你们'、'各位'。✅ 必须把你当做在和单个朋友聊天，使用'你'（例如：'你知道吗...'）。"

        # 场景融合指令
        context_instruction = ""
        if is_group:
             if allow_detail:
                 context_instruction = "- 场景处理：可以结合你当下的真实状态（如工作中、休息中）来引出这个知识点，让分享更有人情味。"
             else:
                 context_instruction = "- 场景处理：**请完全忽略天气**，除非知识点与天气直接相关。如果状态忙碌，可以提一句“工作间隙看到这个”，否则直接分享知识即可。"
        else:
             context_instruction = """
- **关联逻辑（重要）**：
  1. 关于天气：请忽略天气信息，除非这个知识点和天气直接相关。否则不要强行说“今天天气不错，分享个冷知识”，非常生硬。
  2. 关于状态：请尝试将知识点与你【当前正在做的事】联系起来。
     - 正在做饭 -> 分享生活小技巧
     - 正在工作 -> 分享心理学/效率知识
     - 正在发呆/休息 -> 分享脑洞冷知识
     - 如果联系不上，就说是“刚才突然想到的”。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你现在的任务是：向{'群聊' if is_group else '私聊'}分享下面的冷知识。

【核心任务】
1. 知识点关键词：【{target_keyword}】
2. 基于下面的资料进行通俗化讲解。
{baike_context}

{ctx['life_hint']}
{ctx['chat_hint']}

【严重警告 - 拒绝尴尬开头】
- 严禁说：“看大家聊得这么有文化”、“看你们都在聊XX”。
- 直接切入知识点，就像你刚知道这个想告诉朋友一样。
- 请完全忽略群聊的上下文，直接开启新话题。

【重要：称呼控制】
{address_rule}

【重要：场景融合】
{context_instruction}

【开头方式】（随机选择一种）
- 直接知识型："你知道吗..."
- 发现型："刚发现一个有趣的..."
- 提问型："有没有想过..."
- 场景关联型（私聊优先）："刚才在做XX的时候，突然想到..."

【要求】
1. 以你的人设性格说话，自然分享。
2. {'语气轻松简洁' if is_group else '可以详细展开，带点个人见解'}。
3. 可以加入你的个人感想或小评论
4. 用【】将核心关键词【{target_keyword}】括起来。
5. 可以适当用emoji（1-2个）
6. {'字数：100-150字' if is_group else '字数：150-200字'}。
7. 直接输出分享内容。
"""
        
        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])
        
        if res:
            try:
                matches = re.findall(r"【(.*?)】", res)
                if matches:
                    keyword = max(matches, key=len)
                    await self._update_history("knowledge", keyword, target_id)
                elif target_keyword:
                    await self._update_history("knowledge", target_keyword, target_id)
                else:
                    await self._update_history("knowledge", res[:10], target_id)
            except: pass
            
            return f"📚 知识类型: {main_cat} - {sub_cat}\n\n{res}"
        return None

    async def _gen_rec(self, ctx: dict):
        """生成推荐，API 失败则使用 LLM 兜底"""
        if not self.news_service:
            logger.warning("[内容服务] 无法调用百科服务，无法查询相关资料，取消分享")
            return None

        is_group = ctx['is_group']
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 随机选择大类和子类
        rec_type = random.choice(list(REC_CATS.keys()))
        sub_style = random.choice(REC_CATS[rec_type])
        
        target_id = ctx['target_id'] # 获取ID
        # 获取历史调用
        history_str = await self._get_history_str("rec", target_id)
        
        logger.info(f"[内容服务] 推荐方向: {rec_type} ({sub_style})")

        target_work = ""
        baike_context = ""

        # 针对“美食”类型进行特殊约束，防止推荐到动漫/游戏/电影
        
        target_item_desc = "作品名称"
        food_constraint = ""
        
        if rec_type == "美食":
            target_item_desc = "具体的食物名称"
            food_constraint = """
【严重警告 - 类别约束】
你现在推荐的类别是【美食】。
严禁推荐任何动漫、电影、游戏、书籍或小说作品！
严禁推荐《食戟之灵》、《中华小当家》、《黄金神威》等番剧！
必须输出一个【现实中存在的、可以吃的】具体食物名称（如：螺蛳粉、北京烤鸭、仰望星空派、臭豆腐）。
"""

        # 1. 快速生成一个作品/食物名
        pre_prompt = f"""
请推荐一个【{sub_style}】风格的【{rec_type}】{target_item_desc}。
【已推荐过的列表(请绝对避开)】
{history_str}
要求：
1. 请优先选择【口碑极佳】的目标。
2. 拒绝那些被推荐烂了的“教科书式标准答案”。
3. 可以是经典名作，但最好能让人有“眼前一亮”或“值得重温”的感觉。
4. 严禁输出上述“已推荐过的列表”中的内容，必须换一个新的。
5. 只输出名称，不要书名号，不要解释，不要标点。
{food_constraint}
"""

        kw_res = await self.call_llm(prompt=pre_prompt, system_prompt="你是一个品味独特的资深鉴赏家。", timeout=15)
        
        if not kw_res:
            logger.warning("[内容服务] 无法生成推荐作品名，取消分享")
            return None

        target_work = kw_res.strip().split("\n")[0].replace("。", "")
        
        # 2. 查百科 (增加兜底逻辑)
        info = await self.news_service.get_baike_info(target_work)
        
        if info:
            # 命中 API
             baike_context = f"\n\n【资料简介（真实数据）】\n{info}\n"
             logger.info(f"[内容服务] 百科API命中: {target_work}")
        else:
            # 未命中 API，使用 LLM 兜底
             logger.warning(f"[内容服务] 百科未命中【{target_work}】，将使用 LLM 内部知识库兜底")
             baike_context = f"\n\n【提示】暂无外部资料，请基于你自己的知识库，真诚推荐【{target_work}】。"

        # 3. 称呼控制
        address_rule = ""
        if is_group:
             address_rule = "面向群友，推荐给'大家'。"
        else:
             address_rule = "【重要：私聊模式】🚫 严禁使用'大家'、'你们'。✅ 必须把对方当做唯一听众，使用'你'（例如：'推荐你看...'，'你一定会喜欢...'）。"

        # 场景融合指令
        context_instruction = ""
        if is_group:
             if allow_detail:
                 context_instruction = "- 场景参考：可以提及你当下的活动（如刚看完书、听完歌、吃完饭），作为推荐的引子。"
             else:
                 context_instruction = "- 忽略天气，除非它能极大烘托氛围（如下雨推爵士）。重点关注内容本身。如果状态忙碌，可以说“忙里偷闲推荐个”，状态休闲可以说“打发时间”。"
        else:
             context_instruction = """
- **场景筛选（重要）**：
  1. 关于天气：只有当天气能完美烘托作品氛围时才提，否则请完全忽略天气。
  2. 关于状态：请尝试将推荐理由与你【当前正在做的事】联系起来。
     - 刚忙完工作 -> 推荐轻松的剧/音乐来回血
     - 正在深夜网抑云 -> 推荐致郁/治愈电影
     - 正在吃饭 -> 推荐下饭综/美食番/好吃的
     让推荐看起来像是你此刻真实需求的延伸。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你现在的任务是：向{'群聊' if is_group else '私聊'}推荐【{target_work}】。

【核心指令】
1. 必须基于下面的资料进行推荐，不要更换目标。
{baike_context}
2. 历史记录：[{history_str}]

{ctx['life_hint']}
{ctx['chat_hint']}

【严重警告 - 拒绝尴尬开头】
- 严禁使用：“看大家推了那么多”、“看你们都在聊窝被窝”。
- 直接说“最近发现了一个...”或者“推荐一部/一个...”
- 请完全忽略群聊的上下文，直接开启新话题。

【重要：称呼控制】
{address_rule}

【重要：场景融合】
{context_instruction}

【推荐文案要求】
1. 以你的人设性格说话，真实自然
2. 开头必须有明确的推荐表达
3. 真诚推荐，避免营销号式的夸张表达
4. 结合资料介绍它的亮点。
5. 可以适当用emoji（1-2个）
6. 务必用【】将推荐目标的名称【{target_work}】括起来。
7. {'字数：80-120字' if is_group else '字数：120-180字'}。
8. 直接输出推荐内容。
"""

        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])
        
        if res:
            try:
                matches = re.findall(r"【(.*?)】", res)
                if matches:
                    keyword = max(matches, key=len)
                    await self._update_history("rec", keyword, target_id)
                elif target_work:
                     await self._update_history("rec", target_work, target_id)
                else:
                    await self._update_history("rec", res[:10], target_id)
            except: pass
            return f"💡 推荐类型: {rec_type} - {sub_style}\n\n{res}"
        return None
