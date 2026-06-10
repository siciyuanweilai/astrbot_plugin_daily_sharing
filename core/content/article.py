import asyncio
import random
from typing import List, Tuple

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP


class ContentNewsMixin:
    async def _gen_news(self, news_data: Tuple[List, str], ctx: dict):
        """生成新闻分享，带基于联网搜索的自动核查功能。"""
        if not news_data:
            logger.warning("[内容服务] 未获取到新闻数据，取消分享")
            return None

        is_group = ctx['is_group']
        is_qzone = ctx.get('target_id') == 'qzone_broadcast'
        call_name = ctx.get('nickname', '')
        detect_name = ctx.get('detect_name', '')

        # 0. 获取配置
        allow_detail = self.context_conf.get("group_share_schedule", False)
        enable_web_search = self.news_conf.get("enable_tavily_search", True)

        news_list, source_key = news_data
        source_config = NEWS_SOURCE_MAP.get(source_key, {"name": "热搜", "icon": "📰"})
        source_name = source_config["name"]
        
        items_limit = self.news_conf.get("news_items_count", 5)
        selected_to_search = news_list[:items_limit]

        def get_api_background(item: dict) -> str:
            title = str(item.get("title", "") or "").strip()
            desc = str(item.get("description", "") or "").strip()
            if not desc or desc == title or len(desc) < 20:
                return ""
            return desc

        # 优先使用新闻源接口自带摘要/正文；只有缺少背景时才调用联网搜索。
        search_results = [None] * len(selected_to_search)
        pending_tasks = []
        pending_indexes = []
        api_bg_count = 0
        for idx, item in enumerate(selected_to_search):
            title = item.get("title", "")
            api_bg = get_api_background(item)
            if api_bg:
                search_results[idx] = (title, api_bg)
                api_bg_count += 1
            elif enable_web_search:
                pending_indexes.append(idx)
                pending_tasks.append(self._fetch_web_search(title, "news"))
            else:
                search_results[idx] = (title, "")

        if pending_tasks:
            logger.info(
                f"[内容服务] {source_name} 有 {api_bg_count} 条使用接口摘要，"
                f"{len(pending_tasks)} 条补充联网检索..."
            )
            fetched_results = await asyncio.gather(*pending_tasks)
            for idx, result in zip(pending_indexes, fetched_results):
                search_results[idx] = result
        elif api_bg_count:
            logger.info(f"[内容服务] {source_name} 已使用接口自带摘要/正文，跳过联网检索。")
        else:
            logger.info(f"[内容服务] 联网搜索功能已关闭，且接口未提供可用摘要。")

        search_results = [
            result if result is not None else (item.get("title", ""), "")
            for item, result in zip(selected_to_search, search_results)
        ]
        
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
        except (TypeError, ValueError):
            share_count = 2

        news_text = f"【{source_name}】\n\n"
        for idx, (item, (s_title, s_bg)) in enumerate(zip(selected_to_search, search_results), 1):
            hot = item.get("hot", "")
            title = item.get("title", "")
            hot_display = ""
            if hot:
                hot_str = str(hot)
                if hot_str.isdigit() and int(hot_str) > 10000:
                    hot_display = f" {int(hot_str) / 10000:.1f}万"
                else:
                    hot_display = f" {hot_str}"
            
            # 给每个标题加上强制关联的背景提示
            bg_str = f"\n  -> [必须参考的真实背景与人物]: {s_bg}" if s_bg else "\n  -> [真实背景]: 无，请仅就标题做字面简评，严禁擅自编造！"
            news_text += f"{idx}. 标题：【{title}】{hot_display}{bg_str}\n\n"
        
        # 称呼控制
        address_rule = ""
        user_info_prompt = ""
        if is_qzone:
            address_rule = "【重要：QQ空间动态】这是你的个人社交平台QQ空间的动态。不需要和任何人对话，纯粹记录自己看到新闻后的感慨即可。"
        elif not is_group:
            address_rule = "【私聊模式】不要说'大家'、'你们'。请假装只分享给你对面这一个人看。"
            user_info_prompt = self._build_user_prompt(call_name, detect_name)

        # 针对不同模式的场景融合指令
        context_instruction = ""
        if is_group:
            if allow_detail:
                 context_instruction = "- 场景参考：必须基于上方提供的【真实状态】。如果是外出探索，就说是“在路上刷到的”；如果是工作，就说是“忙里偷闲”。"
            else:
                 context_instruction = "- 场景参考：请忽略环境干扰，专注于新闻本身。简单带过你的状态即可。"
        else:
            context_instruction = """
- 场景合理化（重要）：
  必须基于上方提供的【真实生活状态】来设定你“在哪里看新闻”。
  - 严禁违背日程：如果日程是“外出”，必须描述为在途中、躲雨时或到达目的地后看的，严禁说“在被窝里”或“刚醒”。
  - 即使天气不好，也要按照日程设定的“外出人设”来发言（例如：“虽然下雨，但在外面躲雨的时候看到了这个...”）。
"""

        dynamics_prompt = ""
        if ctx.get('recent_dynamics'):
            dynamics_prompt = f"\n【你最近发过的动态回顾】\n{ctx['recent_dynamics']}\n(注：请保持人设连贯，可以偶尔自然呼应之前的心情，但绝对不要重复发过的内容)"

        target_str = "QQ空间" if is_qzone else ('群聊' if is_group else '私聊')

        # 在提示词头部加入最强烈的“阅读理解”指令
        prompt = f"""
【当前时间】{ctx['date_str']} {ctx['time_str']} ({ctx['period_label']})
你看到了今天的{source_name}，想选择{share_count}条和{target_str}分享。

【阅读理解与防幻觉最高指令】
请务必仔细阅读下方提供的新闻列表，每一条都可能附带有 `[必须参考的真实背景与人物]`。
1. 提取与引用：你必须直接使用背景中提到的真实人名和真实数据，严禁无视背景自己瞎写。
2. 绝对禁止编造：如果 `[必须参考的真实背景]` 中没有明确指出具体细节，你就用“有人”、“某地”来概括。绝对不允许去记忆里翻找或随机捏造！

{user_info_prompt}
{ctx['life_hint']}
{ctx['chat_hint']}
{dynamics_prompt}
{source_name}（含 API 摘要/检索真相）：
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
3. 观点真诚，必须结合新闻下方的真实背景进行锐评，不要当毫无营养的复读机！
4. 避免过度情绪化或标题党式表达
5. {'群聊中简洁有重点' if is_group else '私聊可以详细展开想法，并结合你当下的状态'}
6. 用【】标注热搜标题
7. {'字数：120-150字' if is_group else '字数：150-200字'}
8. 直接输出分享内容
9. 【重要】文案末尾必须附带情感标签，格式为：$$happy$$, $$sad$$, $$angry$$, $$surprise$$, $$neutral$$。只选一个。

直接输出："""

        res = await self._call_llm(prompt=prompt, system_prompt=ctx['persona'], timeout=60, target_umo=ctx.get('target_id'))
        
        if res:
            return f"{res}"
        return None
