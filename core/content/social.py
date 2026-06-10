from ..config import TimePeriod


class ContentSocialMixin:
    async def _gen_greeting(self, period: TimePeriod, ctx: dict):
        p_label = ctx['period_label']
        is_group = ctx['is_group']
        is_qzone = ctx.get('target_id') == 'qzone_broadcast'
        call_name = ctx.get('nickname', '')
        detect_name = ctx.get('detect_name', '')
        
        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)

        # 1. 称呼控制
        address_rule = ""
        user_info_prompt = ""

        if is_qzone:
            address_rule = "【重要：QQ空间动态】这是你的个人社交平台QQ空间的动态。不需要@任何人，不需要打招呼，自然抒发当下的感受即可。"
        elif is_group:
            address_rule = "面向群友，自然使用'大家'或不加称呼。"
        else:
            address_rule = "【重要】这是一对一私聊，严禁使用'大家'、'你们'。请使用'你'或直接说内容。"
            user_info_prompt = self._build_user_prompt(call_name, detect_name)

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
        opening_rule = ""
        
        # 清晨(6-9) -> 早间问候放开头
        if period in [TimePeriod.MORNING]:
            greeting_constraint = "4. 文案开头必须是自然的早间问候语，例如“早安”“早上好”“早呀”“早哦”"
            opening_rule = f"- 早间问候开头：\"{'大家' if is_group else ''}早上好，\" / \"{'大家' if is_group else ''}早安，\" / \"{'大家' if is_group else ''}早呀，\""
            
        # 深夜(22-24) 和 凌晨(0-6) -> 睡前祝福放结尾
        elif period in [TimePeriod.LATE_NIGHT, TimePeriod.DAWN]:
            greeting_constraint = "4. 文案不得以睡前祝福开头；必须在正文最后、情感标签之前自然收束一句睡前祝福，例如“晚安”“安安”“好梦”“早点睡，做个好梦”"
            opening_rule = "- 睡前问候：不要用“晚安/安安/好梦”开头，可从当前状态、困意、被窝、夜色等自然切入，最后再用睡前祝福收束。"

        # 上午/下午/傍晚/晚上 -> 自然打招呼
        else:
            greeting_constraint = "4. 就像平常聊天一样自然打招呼即可，不需要刻意说早间问候或睡前祝福"
            opening_rule = "- 自然切入：\"今天心情不错呢\" / \"刚忙完...\" / \"今天有点...\""            

        dynamics_prompt = ""
        if ctx.get('recent_dynamics'):
            dynamics_prompt = f"\n【你最近发过的动态回顾】\n{ctx['recent_dynamics']}\n(注：请保持人设连贯，可以偶尔自然呼应之前的心情，但绝对不要重复发过的内容)"

        target_str = "QQ空间" if is_qzone else ('群聊' if is_group else '私聊')

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({p_label})
你现在要向{target_str}发送一条温馨自然的问候。

{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}
{dynamics_prompt}
{context_instruction}
{address_rule}

【重要】关于场景状态：
- 如果提供了生活状态（如天气、忙碌/空闲）：
  - 群聊：可以简单带过状态和活动来让问候更真实。
  - 私聊：请结合你当前具体的状态和活动来让问候更真实。

【开头方式】（自然直接）
{opening_rule}

- 心情切入："今天心情不错呢"
- 状态切入："刚忙完..." / "今天有点..."
- 天气切入：（仅在天气特殊时使用）

要求：
1. 以你的人设性格说话，真实自然
2. 基于当前真实时间问候
3. 忽略群聊历史，直接开启新问候
{greeting_constraint} 
5. {'简短（80-100字）' if is_group else '可适当长一些（100-120字）'}
6. 直接输出内容，不要解释
7. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$ (开心/期待/治愈), $$sad$$ (低落/深夜/睡前), $$angry$$ (吐槽), $$surprise$$ (吃瓜), $$neutral$$ (平淡)。只选一个。

请生成{p_label}问候："""

        res = await self._call_llm(prompt=prompt, system_prompt=ctx['persona'], target_umo=ctx.get('target_id'))
        if res:
            return f"{res}"
        return None

    async def _gen_mood(self, period, ctx):
        is_group = ctx['is_group']
        is_qzone = ctx.get('target_id') == 'qzone_broadcast'
        call_name = ctx.get('nickname', '')
        detect_name = ctx.get('detect_name', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        
        # 1. 称呼控制
        address_rule = ""
        user_info_prompt = ""

        if is_qzone:
            address_rule = "\n【重要：QQ空间动态】这是你的个人社交平台QQ空间的动态。绝对禁止对别人说话，严禁出现“你”、“大家”等任何称呼，纯粹的自言自语。"
        elif not is_group:
            address_rule = "\n【重要：私聊模式】严禁使用'大家'、'你们'。请把你当做在和单个朋友聊天。"
            user_info_prompt = self._build_user_prompt(call_name, detect_name)

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
        if is_qzone:
            resonance_guide = "【QQ空间日记策略】无需顾及听众，无需互动提问，只专注描绘你周遭的光影、细微的动作和个人的思绪沉淀。"
        elif is_group:
            resonance_guide = f"""
【群聊共鸣策略 - 日程中的"治愈微光"】
请拒绝机械的时间报时（如"早上了"、"晚上了"），而是捕捉你当前生活状态中那些微小但能抚慰人心的瞬间。
请根据你的【生活状态】选择对应策略：

1. 若你当前【忙碌/工作/学习/攻坚】：
   - 寻找"缝隙中的安宁"：不要单纯宣泄压力，而是分享你在忙乱中如何自我安抚。
   - 示例：忙得焦头烂额时偷喝的一口冰美式、解决难题后那一秒的长舒一口气、或是告诉大家“虽然很累，但我们在一点点变好”。
   - 治愈目标：给同样在奋斗的群友一种“并肩作战的陪伴感”，让他们觉得焦虑是被接纳的。

2. 若你当前【休闲/摸鱼/饮食/宅家】：
   - 传递"允许暂停的松弛感"：描述感官上的舒适细节，传递慢下来的权利。
   - 示例：窗帘透进来的光影、食物冒出的热气、被窝里安全的包裹感、或者是“就在此刻，世界与我无关”的窃喜。
   - 治愈目标：成为群里的“精神充电站”，让紧绷的人看到你的文字能感到一丝放松。

3. 若你当前【运动/外出/通勤/散步】：
   - 捕捉"世界的生命力"：跳出赶路的焦躁，分享你眼中的风景和生机。
   - 示例：耳机里的BGM和步伐踩点的瞬间、路边顽强开出的小花、晚霞落在建筑上的温柔、甚至是风吹过脸颊的真实触感。
   - 治愈目标：为群聊打开一扇窗，带去一点“户外的氧气”和对生活的热爱。

核心要求：
情绪必须源于你正在做的事，但视角要温柔且有力量。不要说教，而是通过分享你的“小确幸”，治愈屏幕对面的人。
"""
        else:
            resonance_guide = "【私聊策略】像对亲密好友一样，分享一点私人的、细腻的小情绪，或者一个小秘密。"

        dynamics_prompt = ""
        if ctx.get('recent_dynamics'):
            dynamics_prompt = f"\n【你最近发过的动态回顾】\n{ctx['recent_dynamics']}\n(注：请保持人设连贯，可以偶尔自然呼应之前的心情，但绝对不要重复发过的内容)"

        target_str = "QQ空间" if is_qzone else ('群聊' if is_group else '私聊')
        time_greeting_rule = ""
        if period in (TimePeriod.LATE_NIGHT, TimePeriod.DAWN):
            time_greeting_rule = """
【深夜表达规则】
- 不要以“晚安/安安/好梦”等睡前祝福作为开头；心情分享应先写当下状态、动作或感受。
- 如果要写睡前祝福，只能放在正文最后、情感标签之前；可以自然使用“晚安”“安安”“好梦”“早点睡，做个好梦”等表达。
"""

        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你想和{target_str}分享一下现在的心情或想法。

{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}
{dynamics_prompt}
{vibe_check}
{address_rule}
{resonance_guide}
{time_greeting_rule}

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
5. 字数：{'80-100字' if is_group else '100-120字'}
6. 直接输出内容
7. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$ (开心/期待/治愈), $$sad$$ (低落/深夜/睡前), $$angry$$ (吐槽), $$surprise$$ (吃瓜), $$neutral$$ (平淡)。只选一个。

你的随想："""
        
        res = await self._call_llm(prompt=prompt, system_prompt=ctx['persona'], target_umo=ctx.get('target_id'))
        return res
