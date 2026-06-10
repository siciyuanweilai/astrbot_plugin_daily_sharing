import asyncio
import random
import re

from astrbot.api import logger


class ContentRecommendationMixin:
    async def _gen_rec(self, ctx: dict):
        """生成推荐，接口失败则使用大语言模型兜底。"""
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
        rec_type = random.choice(list(self.rec_cats.keys()))
        sub_style = random.choice(self.rec_cats[rec_type])
        
        target_id = ctx['target_id'] 
        
        logger.info(f"[内容服务] 推荐方向: {rec_type} ({sub_style})")

        # 使用智能发散选题
        target_work = await self._agent_brainstorm_topic(rec_type, sub_style, target_id)
        if not target_work:
             logger.warning("[内容服务] 无法生成推荐作品名，取消分享")
             return None

        # 2. 并发查询百度百科和联网搜索
        baike_task = asyncio.create_task(self.news_service.get_baike_info(target_work))
        tavily_task = asyncio.create_task(self._fetch_web_search(target_work, "rec")) if enable_web_search else None
        
        info = await baike_task
        tavily_info = ""
        if tavily_task:
            _, tavily_info = await tavily_task

        if info or tavily_info:
            baike_context = f"\n\n【资料简介（真实数据，请严格参考它来推荐，绝对不要自行捏造）】\n"
            if info:
                baike_context += f"百度百科简介：{info}\n"
            if tavily_info:
                baike_context += f"全网评价与亮点：{tavily_info}\n"
            logger.info(f"[内容服务] 推荐资料获取成功: {target_work} (百度百科命中: {'是' if info else '否'}, 联网检索命中: {'是' if tavily_info else '否'})")
        else:
            logger.warning("[内容服务] 未命中任何外部资料，将使用大语言模型内部知识库兜底")
            baike_context = f"\n\n【提示】暂无外部资料，请基于你自己的知识库，真诚推荐【{target_work}】。"

        # 3. 称呼控制
        address_rule = ""
        user_info_prompt = ""
        if is_qzone:
             address_rule = "【重要：QQ空间动态】这是你的个人社交平台QQ空间的动态。纯粹表达你自己对这个作品的喜爱，绝对不要向别人安利，不要说“推荐给你们”、“推荐你看”之类的话。"
        elif is_group:
             address_rule = "面向群友，推荐给'大家'。"
        else:
             address_rule = "【重要：私聊模式】严禁使用'大家'、'你们'。必须把对方当做唯一听众，使用'你'（例如：'推荐你看...'，'你一定会喜欢...'）。"
             user_info_prompt = self._build_user_prompt(call_name, detect_name)

        # 场景融合指令
        context_instruction = ""
        if is_group:
             if allow_detail:
                 context_instruction = "- 场景参考：可以提及你当下的活动（如刚看完书、听完歌、吃完饭），作为推荐的引子的。"
             else:
                 context_instruction = "- 忽略天气，除非它能极大烘托氛围（如下雨推爵士）。重点关注内容本身。如果状态忙碌，可以说“忙里偷闲推荐个”，状态休闲可以说“打发时间”。"
        else:
             context_instruction = """
- 场景筛选（重要）：
  1. 关于天气：只有当天气能完美烘托作品氛围时才提，否则请完全忽略天气。
  2. 关于状态：请尝试将推荐理由与你【当前正在做的事】联系起来。
     - 刚忙完工作 -> 推荐轻松的剧/音乐来回血
     - 正在深夜网抑云 -> 推荐致郁/治愈电影
     - 正在吃饭 -> 推荐下饭综/美食番/好吃的
     让推荐看起来像是你此刻真实需求的延伸。
  3. 如果联系不上，就直接说“最近在重温/看到了这个”即可，不要强行编造理由，也不要说“突然想到”。
"""

        dynamics_prompt = ""
        if ctx.get('recent_dynamics'):
            dynamics_prompt = f"\n【你最近发过的动态回顾】\n{ctx['recent_dynamics']}\n(注：请保持人设连贯，可以偶尔自然呼应之前的心情，但绝对不要重复发过的内容)"

        target_str = "QQ空间" if is_qzone else ('群聊' if is_group else '私聊')

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你现在的任务是：向{target_str}推荐【{target_work}】。

【核心指令】
1. 必须基于下面的资料进行推荐，不要更换目标。

{baike_context}
{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}
{dynamics_prompt}

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
6. {'字数：80-120字' if is_group else '字数：120-150字'}。
7. 直接输出推荐内容。
8. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$, $$sad$$, $$angry$$, $$surprise$$, $$neutral$$。只选一个。
"""

        res = await self._call_llm(prompt=prompt, system_prompt=ctx['persona'], target_umo=ctx.get('target_id'))
        
        if res:
            try:
                matches = re.findall(r"【(.*?)】", res)
                keyword = matches[0] if matches else target_work or res[:10]
                await self.db.record_topic(target_id, "rec", keyword)
            except Exception as e:
                logger.debug(f"[内容服务] 记录推荐主题失败: {e}")
            if self.content_lib_conf.get("show_rec_type_prefix", True):
                return f"推荐类型: {rec_type} - {sub_style}\n\n{res}"
            return res
        return None
