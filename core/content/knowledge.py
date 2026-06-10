import asyncio
import random
import re

from astrbot.api import logger


class ContentKnowledgeMixin:
    async def _gen_knowledge(self, ctx: dict):
        """生成知识分享，接口失败则使用大语言模型兜底。"""
        if not self.news_service:
            logger.warning("[内容服务] 无法调用百度百科服务，无法查询相关资料，取消分享")
            return None

        is_group = ctx['is_group']
        is_qzone = ctx.get('target_id') == 'qzone_broadcast'
        call_name = ctx.get('nickname', '')
        detect_name = ctx.get('detect_name', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        enable_web_search = self.news_conf.get("enable_tavily_search", True)
        
        # 随机选择大类和子类
        main_cat = random.choice(list(self.knowledge_cats.keys()))
        sub_cat = random.choice(self.knowledge_cats[main_cat])
        target_id = ctx['target_id'] 
        
        logger.info(f"[内容服务] 知识方向: {main_cat} - {sub_cat}")

        # 使用智能发散选题
        target_keyword = await self._agent_brainstorm_topic(main_cat, sub_cat, target_id)
        if not target_keyword:
            logger.warning("[内容服务] 无法生成知识关键词，取消分享")
            return None
        
        # 2. 并发查询百度百科和联网搜索
        baike_task = asyncio.create_task(self.news_service.get_baike_info(target_keyword))
        tavily_task = asyncio.create_task(self._fetch_web_search(target_keyword, "knowledge")) if enable_web_search else None
        
        info = await baike_task
        tavily_info = ""
        if tavily_task:
            _, tavily_info = await tavily_task
        
        if info or tavily_info:
            baike_context = f"\n\n【参考资料（请基于以下真实数据进行通俗化讲解，绝对不要自行捏造）】\n"
            if info:
                baike_context += f"百度百科词条：{info}\n"
            if tavily_info:
                baike_context += f"全网检索：{tavily_info}\n"
            logger.info(f"[内容服务] 知识资料获取成功: {target_keyword} (百度百科命中: {'是' if info else '否'}, 联网检索命中: {'是' if tavily_info else '否'})")
        else:
            logger.warning("[内容服务] 未命中任何外部资料，将使用大语言模型内部知识库兜底")
            baike_context = f"\n\n【提示】暂无外部资料，请基于你自己的知识库，准确介绍【{target_keyword}】。"
        
        # 3. 称呼控制
        address_rule = ""
        user_info_prompt = ""

        if is_qzone:
            address_rule = "【重要：QQ空间动态】这是你的个人社交平台QQ空间的动态。直接分享知识即可，绝对不要向别人提问，不要出现“你知道吗”、“大家知道吗”这样的互动词汇。"
        elif is_group:
            address_rule = "面向群友，可以使用'大家'、'你们'。"
        else:
            address_rule = "【重要：私聊模式】严禁使用'大家'、'你们'、'各位'。必须把你当做在和单个朋友聊天，使用'你'（例如：'你知道吗...'）。"
            user_info_prompt = self._build_user_prompt(call_name, detect_name)

        # 场景融合指令
        context_instruction = ""
        if is_group:
             if allow_detail:
                 context_instruction = "- 场景处理：可以结合你当下的真实状态（如工作中、休息中）来引出这个知识点，让分享更有人情味。"
             else:
                 context_instruction = "- 场景处理：请完全忽略天气，除非知识点与天气直接相关。如果状态忙碌，可以说“忙里偷闲推荐个”，否则直接分享知识即可。"
        else:
             context_instruction = """
- 关联逻辑（重要）：
  1. 关于天气：请忽略天气信息，除非这个知识点和天气直接相关。
  2. 关于状态：请尝试将知识点与你【当前正在做的事】联系起来。
     - 正在做饭 -> 分享生活小技巧
     - 正在工作 -> 分享心理学/效率知识
     - 如果实在联系不上，直接分享即可，不要强行找理由，也不要编造“突然想到”的心理活动。
"""

        dynamics_prompt = ""
        if ctx.get('recent_dynamics'):
            dynamics_prompt = f"\n【你最近发过的动态回顾】\n{ctx['recent_dynamics']}\n(注：请保持人设连贯，可以偶尔自然呼应之前的心情，但绝对不要重复发过的内容)"

        target_str = "QQ空间" if is_qzone else ('群聊' if is_group else '私聊')

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你现在的任务是：向{target_str}分享下面的冷知识。

【核心任务】
1. 知识点关键词：【{target_keyword}】
2. 基于下面的资料进行通俗化讲解。

{baike_context}
{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}
{dynamics_prompt}

【拒绝神怪/脑补开头】
- 严禁使用“脑子里突然蹦出”、“突然灵光一闪”、“不知怎么的突然想到”等描述思维跳跃的语句。
- 严禁描述你大脑内部的运作过程。
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
5. {'字数：100-150字' if is_group else '字数：100-200字'}。
6. 直接输出分享内容。
7. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$, $$sad$$, $$angry$$, $$surprise$$, $$neutral$$。只选一个。
"""
        
        res = await self._call_llm(prompt=prompt, system_prompt=ctx['persona'], target_umo=ctx.get('target_id'))
        
        if res:
            try:
                matches = re.findall(r"【(.*?)】", res)
                keyword = matches[0] if matches else target_keyword or res[:10]
                await self.db.record_topic(target_id, "knowledge", keyword)
            except Exception as e:
                logger.debug(f"[内容服务] 记录知识主题失败: {e}")
            
            if self.content_lib_conf.get("show_knowledge_type_prefix", True):
                return f"知识类型: {main_cat} - {sub_cat}\n\n{res}"
            return res
        return None
