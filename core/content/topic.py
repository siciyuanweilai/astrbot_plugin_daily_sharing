from typing import Optional


class ContentTopicMixin:
    async def _agent_brainstorm_topic(self, category_type: str, sub_category: str, target_id: str) -> Optional[str]:
        """
        选题智能提取：专门负责从给定的类别中，结合历史记录，避坑并选出一个有趣的、不重复的话题/作品名。
        """
        is_rec = category_type in self.rec_cats
        db_category = "rec" if is_rec else "knowledge"
        
        # 获取最近若干天使用过的话题。
        used_topics = await self.db.get_used_topics(target_id, db_category, days_limit=self.dedup_days)
        history_str = "、".join(used_topics) if used_topics else "无"
        
        if is_rec:
            # 推荐类提示词
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
            # === 知识类提示词 ===
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

        # 调用大语言模型
        res = await self._call_llm(prompt=user_prompt, system_prompt=system_prompt, timeout=15, target_umo=target_id)
        if not res: return None
        
        # 清洗结果 (去除标点和多余空格)
        topic = res.strip().split("\n")[0].replace("。", "").replace("《", "").replace("》", "")
        return topic
