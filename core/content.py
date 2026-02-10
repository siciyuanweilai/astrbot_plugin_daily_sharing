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

# 知识库细分
KNOWLEDGE_CATS = {
    "有趣的冷知识": ["动物行为", "人体奥秘", "地理奇观", "历史误区", "语言文字", "植物智慧", "海洋生物", "昆虫视界", "真菌世界", "人体极限"],
    "生活小技巧": ["收纳整理", "厨房妙招", "数码技巧", "省钱攻略", "应急处理", "衣物护理", "家居清洁", "园艺入门", "旅行打包", "急救常识"],
    "健康小常识": ["睡眠科学", "饮食营养", "运动误区", "心理健康", "护眼护肤", "牙齿护理", "脱发自救", "饮水科学", "姿势矫正", "抗衰老"],
    "历史小故事": ["古代发明", "名人轶事", "文明起源", "战争细节", "文物故事", "丝绸之路", "大航海时代", "工业革命", "文艺复兴", "古代货币"],
    "科学小发现": ["天文宇宙", "平行宇宙", "生物进化", "未来科技", "AI发展", "材料科学", "气象奥秘", "深海探测", "脑科学", "基因工程"],
    "心理学知识": ["认知偏差", "社交心理", "情绪管理", "微表情", "行为经济学", "人格类型", "梦境解析", "记忆规律", "说服技巧", "色彩心理"],
    "艺术小百科": ["名画赏析", "建筑风格", "设计美学", "色彩搭配", "流派演变", "博物馆巡礼", "传统工艺", "摄影构图", "书法篆刻", "音乐理论"],
    "商业冷思维": ["营销陷阱", "品牌故事", "经济学原理", "消费心理", "投资误区", "商业模式", "广告玄机", "博弈论", "富人思维", "独角兽兴衰"],
    "哲学与逻辑": ["著名悖论", "逻辑谬误", "思维模型", "存在主义", "伦理难题", "批判性思维", "奥卡姆剃刀", "墨菲定律", "斯多葛学派", "思想实验"],
    "职场进化论": ["高效办公", "沟通话术", "时间管理", "汇报技巧", "向上管理", "面试心理", "团队协作", "摸鱼哲学", "领导力", "职业规划"]
}

# 推荐库细分
REC_CATS = {
    "书籍": ["悬疑推理", "当代文学", "历史传记", "科普新知", "商业思维", "治愈系绘本", "科幻神作", "哲学入门", "古典诗词", "艺术图鉴"],
    "电影": ["高分冷门", "烧脑科幻", "经典黑白", "是枝裕和风", "赛博朋克", "奥斯卡遗珠", "纪录片", "励志传记", "暴力美学", "黑色幽默"],
    "音乐": ["新世纪音乐", "治愈系钢琴", "氛围电子", "华语流行", "梦幻流行", "影视原声", "自然白噪音", "爵士蓝调", "摇滚精神", "民谣故事"],
    "动漫": ["治愈日常", "硬核科幻", "热血运动", "悬疑智斗", "吉卜力风", "奇幻史诗", "冷门佳作", "机甲浪漫", "异世界冒险", "推理侦探"],
    "美食": ["地方特色小吃", "创意懒人菜", "季节限定", "深夜治愈美食", "传统糕点", "异国风味", "烘焙甜点", "咖啡茶饮", "海鲜料理", "面食文化"],
    "游戏": ["独立神作", "治愈解谜", "剧情向", "像素风", "肉鸽Like", "模拟经营", "开放世界", "恐怖游戏", "复古怀旧", "派对游戏"],
    "剧集": ["英美神剧", "悬疑破案", "高分韩剧", "下饭情景剧", "职场爽剧", "历史正剧", "日式律政", "迷你剧", "真人秀", "讽刺喜剧"],
    "播客": ["怪诞故事", "商业内幕", "历史闲聊", "科技前沿", "情感治愈", "真实罪案", "文化对谈", "读书分享", "英语听力", "助眠ASMR"],
    "好物": ["桌面美学", "创意文具", "数码配件", "居家神器", "露营装备", "解压玩具", "咖啡器具", "极简收纳", "黑科技", "手工DIY"],
    "旅行": ["避世古镇", "赛博城市", "海岛度假", "徒步路线", "博物馆", "自驾公路", "露营圣地", "建筑打卡", "云旅游", "特色民宿"]
}

class ContentService:
    def __init__(self, config: Dict, llm_func, context, db_manager, news_service=None):
        """
        初始化内容生成服务
        """
        self.config = config
        self.call_llm = llm_func
        self.context = context 
        self.db = db_manager 
        self.news_service = news_service
        
        self.basic_conf = self.config.get("basic_conf", {})
        self.dedup_days = int(self.basic_conf.get("dedup_days_limit", 60))
        
        self.news_conf = self.config.get("news_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.context_conf = self.config.get("context_conf", {})

    async def generate(self, stype: SharingType, period: TimePeriod, 
                      target_id: str, is_group: bool, 
                      life_ctx: str, chat_hist: str, news_data: tuple = None,
                      nickname: str = "") -> Optional[str]:
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
            "time_str": time_str,
            "nickname": nickname
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

    # ==================== Agent 选题 ====================

    async def _agent_brainstorm_topic(self, category_type: str, sub_category: str, target_id: str) -> Optional[str]:
        """
        选题 Agent：专门负责从给定的类别中，结合历史记录，避坑并选出一个有趣的、不重复的话题/作品名。
        """
        is_rec = category_type in REC_CATS
        db_category = "rec" if is_rec else "knowledge"
        
        # 获取最近 N 天使用过的话题
        used_topics = await self.db.get_used_topics(target_id, db_category, days_limit=self.dedup_days)
        history_str = "、".join(used_topics) if used_topics else "无"
        
        if is_rec:
            # 推荐类 Prompt
            constraint = ""
            target_item_desc = "具体作品名称"
            
            # 针对不同类型的特殊约束
            if category_type == "美食":
                target_item_desc = "具体食物名称"
                constraint = """
【严重警告 - 类别约束】
你现在推荐的类别是【美食】。
严禁推荐任何动漫、电影、游戏、书籍或小说作品！
严禁推荐《食戟之灵》、《中华小当家》、《黄金神威》等番剧！
必须输出一个【现实中存在的、可以吃的】具体食物名称（如：螺蛳粉、北京烤鸭、臭豆腐）。
"""
            elif category_type == "游戏":
                target_item_desc = "具体游戏名称"
                constraint = """
【严重警告 - 类别约束】
你现在推荐的是【游戏】。
请确保推荐的是具体的游戏名（如：塞尔达传说、星露谷物语、原神）。
不要推荐游戏机硬件（如PS5、Switch），只推荐软件游戏本身。
"""
            elif category_type == "好物":
                target_item_desc = "具体物品/产品名称"
                constraint = """
【严重警告 - 类别约束】
你现在推荐的是【生活好物/产品】。
请推荐具体的物品种类或知名单品（如：洞洞板、机械键盘、气泡水机）。
不要推荐过于抽象的概念。
"""
            
            system_prompt = "你是一个品味独特的资深鉴赏家和推荐官。"
            user_prompt = f"""
任务：推荐一个【{sub_category}】风格的【{category_type}】{target_item_desc}。
【已推荐过的列表(请绝对避开)】：{history_str}

要求：
1. 请优先选择【口碑极佳】的目标。
2. 拒绝那些被推荐烂了的“教科书式标准答案”。
3. 可以是经典名作，但最好能让人有“眼前一亮”或“值得重温”的感觉。
4. 严禁输出上述“已推荐过的列表”中的内容，必须换一个新的。
5. 只输出名称，不要书名号，不要解释，不要标点。
{constraint}
"""
        else:
            # === 知识类 Prompt ===
            system_prompt = "你是一个眼光独到的科普博主和生活达人。"
            user_prompt = f"""
请输出一个属于【{category_type}-{sub_category}】领域的知识点关键词。
【已分享过的列表(请绝对避开)】：{history_str}

要求：
1. 话题范围灵活：可以是【冷知识】、【常见误区】、【实用技巧】或【有趣现象】。
2. 核心标准是“有趣”或“有用”：
   - 如果是生活类，优先选实用性强的。
   - 如果是科普类，优先选反直觉或颠覆认知的。
   - 不要刻意追求“生僻难懂”，大众感兴趣的话题也可以。
3. 严禁输出上述“已分享过的列表”中的内容，必须换一个新的。 
4. 只输出关键词，不要任何解释，不要标点符号。
"""

        # 调用 LLM 
        res = await self.call_llm(prompt=user_prompt, system_prompt=system_prompt, timeout=15)
        if not res: return None
        
        # 清洗结果 (去除标点和多余空格)
        topic = res.strip().split("\n")[0].replace("。", "").replace("《", "").replace("》", "")
        return topic

    # ==================== 辅助方法 ====================

    def _get_period_label(self, period: TimePeriod) -> str:
        labels = {
            TimePeriod.DAWN: "凌晨", 
            TimePeriod.MORNING: "早晨",
            TimePeriod.FORENOON: "上午",
            TimePeriod.AFTERNOON: "下午", 
            TimePeriod.EVENING: "傍晚",
            TimePeriod.NIGHT: "夜晚",      
            TimePeriod.LATE_NIGHT: "深夜", 
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

    # ==================== 生成逻辑 ====================

    def _build_user_prompt(self, nickname: str) -> str:
        """构建强化的用户信息提示，包含日程检测逻辑"""
        if not nickname:
            return ""
        
        return f"""
【用户信息】
对方昵称：{nickname}
【重要交互逻辑】
1. **昵称称呼**：你可以自然地使用“{nickname}”称呼对方。
2. **日程关联检测**：请仔细检查你的【生活日程】。如果日程中出现了“{nickname}”这个名字（或同音/包含关系）：
   - **必须**将文案转换为“和你一起”的语气。
   - 错误示例：日程说“和{nickname}逛街”，文案写“今天我要和{nickname}去逛街”。（这是把对方当第三人称）
   - 正确示例：日程说“和{nickname}逛街”，文案写“今天终于可以和你一起逛街啦，好期待！”。（这是对当事人说话）
"""

    async def _gen_greeting(self, period: TimePeriod, ctx: dict):
        p_label = ctx['period_label']
        is_group = ctx['is_group']
        nickname = ctx.get('nickname', '')
        
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)

        # 1. 称呼控制
        address_rule = ""
        user_info_prompt = ""

        if is_group:
            address_rule = "面向群友，自然使用'大家'或不加称呼。"
        else:
            address_rule = "【重要】这是一对一私聊，严禁使用'大家'、'你们'。请使用'你'或直接说内容。"
            user_info_prompt = self._build_user_prompt(nickname)

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

        greeting_constraint = ""
        
        # 清晨(6-9) -> 强制早安
        if period in [TimePeriod.MORNING]:
            greeting_constraint = "4. 文案开头必须带上温馨的早安问候，因为现在是早晨准备起床的时候。"
            
        # 深夜(22-24) 和 凌晨(0-6) -> 强制晚安
        elif period in [TimePeriod.LATE_NIGHT, TimePeriod.DAWN]:
            greeting_constraint = "4. 文案末尾必须带上温馨的晚安问候，因为现在是深夜准备睡觉的时候。"

        # 上午/下午/傍晚/晚上 -> 自然打招呼
        else:
            greeting_constraint = "4. 就像平常聊天一样自然打招呼即可，不需要刻意说早安晚安"            

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({p_label})
你现在要向{'群聊' if is_group else '私聊'}发送一条温馨自然的问候。

{user_info_prompt}
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
{greeting_constraint} 
5. {'简短（50-80字）' if is_group else '可适当长一些（80-100字）'}
6. 直接输出内容，不要解释
7. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$ (开心/期待/治愈), $$sad$$ (低落/深夜/晚安), $$angry$$ (吐槽), $$surprise$$ (吃瓜), $$neutral$$ (平淡)。只选一个。

请生成{p_label}问候："""

        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])
        if res:
            return f"{res}"
        return None  

    async def _gen_mood(self, period, ctx):
        is_group = ctx['is_group']
        nickname = ctx.get('nickname', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 1. 称呼控制
        address_rule = ""
        user_info_prompt = ""

        if not is_group:
            address_rule = "\n【重要：私聊模式】严禁使用'大家'、'你们'。请把你当做在和单个朋友聊天。"
            user_info_prompt = self._build_user_prompt(nickname)

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
【群聊共鸣策略 - 日程中的"治愈微光"】
请拒绝机械的时间报时（如"早上了"、"晚上了"），而是**捕捉你当前生活状态中那些微小但能抚慰人心的瞬间**。
请根据你的【生活状态】选择对应策略：

1. **若你当前【忙碌/工作/学习/攻坚】**：
   - **寻找"缝隙中的安宁"**：不要单纯宣泄压力，而是分享你在忙乱中如何自我安抚。
   - *示例*：忙得焦头烂额时偷喝的一口冰美式、解决难题后那一秒的长舒一口气、或是告诉大家“虽然很累，但我们在一点点变好”。
   - *治愈目标*：给同样在奋斗的群友一种**“并肩作战的陪伴感”**，让他们觉得焦虑是被接纳的。

2. **若你当前【休闲/摸鱼/饮食/宅家】**：
   - **传递"允许暂停的松弛感"**：描述感官上的舒适细节，传递慢下来的权利。
   - *示例*：窗帘透进来的光影、食物冒出的热气、被窝里安全的包裹感、或者是“就在此刻，世界与我无关”的窃喜。
   - *治愈目标*：成为群里的**“精神充电站”**，让紧绷的人看到你的文字能感到一丝放松。

3. **若你当前【运动/外出/通勤/散步】**：
   - **捕捉"世界的生命力"**：跳出赶路的焦躁，分享你眼中的风景和生机。
   - *示例*：耳机里的BGM和步伐踩点的瞬间、路边顽强开出的小花、晚霞落在建筑上的温柔、甚至是风吹过脸颊的真实触感。
   - *治愈目标*：为群聊打开一扇窗，带去一点**“户外的氧气”**和对生活的热爱。

**核心要求**：
情绪必须**源于你正在做的事**，但视角要**温柔且有力量**。不要说教，而是通过分享你的“小确幸”，治愈屏幕对面的人。
"""
        else:
            resonance_guide = "【私聊策略】像对亲密好友一样，分享一点私人的、细腻的小情绪，或者一个小秘密。"


        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你想和{'群聊' if is_group else '私聊'}分享一下现在的心情或想法。

{user_info_prompt}
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
4. 基于当前真实时间感悟
5. 字数：{'50-80字' if is_group else '80-100字'}
6. 直接输出内容
7. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$ (开心/期待/治愈), $$sad$$ (低落/深夜/晚安), $$angry$$ (吐槽), $$surprise$$ (吃瓜), $$neutral$$ (平淡)。只选一个。

你的随想："""
        
        return await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])

    async def _gen_news(self, news_data: Tuple[List, str], ctx: dict):
        """生成新闻分享，无数据则不生成"""
        if not news_data:
            logger.warning("[内容服务] 未获取到新闻数据，取消分享")
            return None

        is_group = ctx['is_group']
        nickname = ctx.get('nickname', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)

        news_list, source_key = news_data
        source_config = NEWS_SOURCE_MAP.get(source_key, {"name": "热搜", "icon": "📰"})
        source_name = source_config["name"]
        
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
        user_info_prompt = ""
        if not is_group:
            address_rule = "【私聊模式】不要说'大家'、'你们'。请假装只分享给**你对面这一个人**看。"
            user_info_prompt = self._build_user_prompt(nickname)

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
  - 严禁违背日程：如果日程是“外出”，必须描述为在途中、躲雨时或到达目的地后看的，严禁说“在被窝里”或“刚醒”。
  - 即使天气不好，也要按照日程设定的“外出人设”来发言（例如：“虽然下雨，但在外面躲雨的时候看到了这个...”）。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你看到了今天的{source_name}，想选择{share_count}条和{'群聊' if is_group else '私聊'}分享。

{user_info_prompt}
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
【特别强调】：请检查你的穿搭和日程，如果你的穿搭是外出的（如大衣、制服），绝对不要描述自己躺在床上或刚睡醒。这不符合逻辑。

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
6. 用【】标注热搜标题
7. {'字数：120-150字' if is_group else '字数：150-200字'}
8. 直接输出分享内容
9. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$, $$sad$$, $$angry$$, $$surprise$$, $$neutral$$。只选一个。

直接输出："""

        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'], timeout=60)
        
        if res:
            return f"{res}"
        return None 

    async def _gen_knowledge(self, ctx: dict):
        """生成知识分享，API 失败则使用 LLM 兜底"""
        if not self.news_service:
            logger.warning("[内容服务] 无法调用百科服务，无法查询相关资料，取消分享")
            return None

        is_group = ctx['is_group']
        nickname = ctx.get('nickname', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 随机选择大类和子类
        main_cat = random.choice(list(KNOWLEDGE_CATS.keys()))
        sub_cat = random.choice(KNOWLEDGE_CATS[main_cat])
        target_id = ctx['target_id'] 
        
        logger.info(f"[内容服务] 知识方向: {main_cat} - {sub_cat}")

        # 使用 Agent Brainstorming
        target_keyword = await self._agent_brainstorm_topic(main_cat, sub_cat, target_id)
        if not target_keyword:
            logger.warning("[内容服务] 无法生成知识关键词，取消分享")
            return None
        
        # 2. 查百科 (增加兜底逻辑)
        info = await self.news_service.get_baike_info(target_keyword)
        
        if info:
            # 命中 API
            baike_context = f"\n\n【事实依据（不要捏造）】\n{info}\n"
            logger.info(f"[内容服务] 百度百科命中: {target_keyword}")
        else:
            # 未命中 API，使用 LLM 兜底
            logger.warning(f"[内容服务] 百科未命中【{target_keyword}】，将使用 LLM 内部知识库兜底")
            baike_context = f"\n\n【提示】暂无外部资料，请基于你自己的知识库，准确介绍【{target_keyword}】。"
        
        # 3. 称呼控制
        address_rule = ""
        user_info_prompt = ""

        if is_group:
            address_rule = "面向群友，可以使用'大家'、'你们'。"
        else:
            address_rule = "【重要：私聊模式】严禁使用'大家'、'你们'、'各位'。必须把你当做在和单个朋友聊天，使用'你'（例如：'你知道吗...'）。"
            user_info_prompt = self._build_user_prompt(nickname)

        # 场景融合指令
        context_instruction = ""
        if is_group:
             if allow_detail:
                 context_instruction = "- 场景处理：可以结合你当下的真实状态（如工作中、休息中）来引出这个知识点，让分享更有人情味。"
             else:
                 context_instruction = "- 场景处理：**请完全忽略天气**，除非知识点与天气直接相关。如果状态忙碌，可以说“忙里偷闲推荐个”，否则直接分享知识即可。"
        else:
             context_instruction = """
- **关联逻辑（重要）**：
  1. 关于天气：请忽略天气信息，除非这个知识点和天气直接相关。
  2. 关于状态：请尝试将知识点与你【当前正在做的事】联系起来。
     - 正在做饭 -> 分享生活小技巧
     - 正在工作 -> 分享心理学/效率知识
     - 如果实在联系不上，直接分享即可，不要强行找理由，也不要编造“突然想到”的心理活动。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你现在的任务是：向{'群聊' if is_group else '私聊'}分享下面的冷知识。

【核心任务】
1. 知识点关键词：【{target_keyword}】
2. 基于下面的资料进行通俗化讲解。
{baike_context}

{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}

【拒绝神怪/脑补开头】
- 严禁使用“脑子里突然蹦出”、“突然灵光一闪”、“不知怎么的突然想到”等描述思维跳跃的语句。
- 严禁描述你大脑内部的运作过程（如“我的数据库检索到”）。
- 必须像个正常人类一样，自然地开启话题。

【严重警告 - 拒绝尴尬开头】
- 严禁说：“看大家聊得这么有文化”、“看你们都在聊窝被窝”。
- 直接切入知识点，就像你刚知道这个想告诉朋友一样。
- 请完全忽略群聊的上下文，直接开启新话题。

【重要：称呼控制】
{address_rule}

【重要：场景融合】
{context_instruction}

【开头方式】（自然流畅）
- 直接知识型："你知道吗..." / "据说..."
- 发现型："刚看到一个有趣的说法..."
- 提问型："大家有没有想过..."
- 场景关联型（私聊优先）："刚好在做XX，顺便分享一个..." (必须逻辑通顺)

【要求】
1. 以你的人设性格说话，自然分享。
2. {'语气轻松简洁' if is_group else '可以详细展开，带点个人见解'}。
3. 可以加入你的个人感想或小评论
4. 用【】将核心关键词【{target_keyword}】括起来。
5. {'字数：100-150字' if is_group else '字数：150-200字'}。
6. 直接输出分享内容。
7. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$, $$sad$$, $$angry$$, $$surprise$$, $$neutral$$。只选一个。
"""
        
        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])
        
        if res:
            try:
                matches = re.findall(r"【(.*?)】", res)
                keyword = matches[0] if matches else target_keyword or res[:10]
                await self.db.record_topic(target_id, "knowledge", keyword)
            except: pass
            
            return f"知识类型: {main_cat} - {sub_cat}\n\n{res}"
        return None

    async def _gen_rec(self, ctx: dict):
        """生成推荐，API 失败则使用 LLM 兜底"""
        if not self.news_service:
            logger.warning("[内容服务] 无法调用百科服务，无法查询相关资料，取消分享")
            return None

        is_group = ctx['is_group']
        nickname = ctx.get('nickname', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 随机选择大类和子类
        rec_type = random.choice(list(REC_CATS.keys()))
        sub_style = random.choice(REC_CATS[rec_type])
        
        target_id = ctx['target_id'] 
        
        logger.info(f"[内容服务] 推荐方向: {rec_type} ({sub_style})")

        # 使用 Agent Brainstorming
        target_work = await self._agent_brainstorm_topic(rec_type, sub_style, target_id)
        if not target_work:
             logger.warning("[内容服务] 无法生成推荐作品名，取消分享")
             return None

        baike_context = ""
        
        # 2. 查百科 (增加兜底逻辑)
        info = await self.news_service.get_baike_info(target_work)
        
        if info:
            # 命中 API
             baike_context = f"\n\n【资料简介（真实数据）】\n{info}\n"
             logger.info(f"[内容服务] 百度百科命中: {target_work}")
        else:
            # 未命中 API，使用 LLM 兜底
             logger.warning(f"[内容服务] 百科未命中【{target_work}】，将使用 LLM 内部知识库兜底")
             baike_context = f"\n\n【提示】暂无外部资料，请基于你自己的知识库，真诚推荐【{target_work}】。"

        # 3. 称呼控制
        address_rule = ""
        user_info_prompt = ""
        if is_group:
             address_rule = "面向群友，推荐给'大家'。"
        else:
             address_rule = "【重要：私聊模式】严禁使用'大家'、'你们'。必须把对方当做唯一听众，使用'你'（例如：'推荐你看...'，'你一定会喜欢...'）。"
             user_info_prompt = self._build_user_prompt(nickname)

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
  3. 如果联系不上，就直接说“最近在重温/看到了这个”即可，不要强行编造理由，也不要说“突然想到”。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你现在的任务是：向{'群聊' if is_group else '私聊'}推荐【{target_work}】。

【核心指令】
1. 必须基于下面的资料进行推荐，不要更换目标。
{baike_context}

{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}

【拒绝神怪/脑补开头】
- 严禁使用“脑子里突然蹦出”、“突然灵光一闪”、“不知怎么的脑海中浮现”等描述思维跳跃的语句。
- 严禁描述你大脑内部的运作过程。
- 必须像个正常人类一样，自然地开启话题。

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
5. 务必用【】将推荐目标的名称【{target_work}】括起来。
6. {'字数：80-120字' if is_group else '字数：120-180字'}。
7. 直接输出推荐内容。
8. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$, $$sad$$, $$angry$$, $$surprise$$, $$neutral$$。只选一个。
"""

        res = await self.call_llm(prompt=prompt, system_prompt=ctx['persona'])
        
        if res:
            try:
                matches = re.findall(r"【(.*?)】", res)
                keyword = matches[0] if matches else target_work or res[:10]
                await self.db.record_topic(target_id, "rec", keyword)
            except: pass
            return f"推荐类型: {rec_type} - {sub_style}\n\n{res}"
        return None