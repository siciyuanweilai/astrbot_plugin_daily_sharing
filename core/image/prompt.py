import json
import re
from datetime import datetime
from typing import Dict

from astrbot.api import logger

from ..config import SharingType, TimePeriod


class ImageVisualMixin:
    async def _agent_extract_visuals(self, content: str, life_context: str, target_umo: str = None) -> Dict[str, str]:
        """
        使用智能体一次性提取：主体、环境、光影、场景、天气温感、穿搭、动作。
        """
        if not content and not life_context: return {}

        # 获取当前基础信息
        now = datetime.now()
        curr_hour = now.hour
        period = self._get_current_period()
        is_night = period in [TimePeriod.LATE_NIGHT, TimePeriod.DAWN]
        
        # 1. 基础时间光影库 
        if period == TimePeriod.DAWN: 
            if curr_hour < 4:
                time_hint = "凌晨深夜的寂静，漆黑的夜空，漆黑的夜色，路灯或城市灯光"
            else:
                time_hint = "黎明前的微光，天空是非常深的暗蓝色，微弱的冷光，清冷寂静，朦胧感"        
        elif period == TimePeriod.MORNING: 
            time_hint = "早晨的日出晨光, 柔和的朝阳, 清晨柔和的漫射光，丁达尔效应, 梦幻光影"
        elif period == TimePeriod.FORENOON:
            time_hint = "上午的明亮日光，通透，晴朗的天空, 充满活力的光线"
        elif period == TimePeriod.NOON:
            time_hint = "中午明亮而柔和的日光，清爽通透，带一点午休前后的轻盈生活感"
        elif period == TimePeriod.AFTERNOON:
            time_hint = "下午的充足阳光，光影对比清晰，慵懒或明亮的氛围, 清晰的照明"
        elif period == TimePeriod.EVENING: 
            time_hint = "傍晚的暖色调，温暖的金色夕阳, 晚霞或暮色，柔和的长阴影，逆光轮廓"
        elif period == TimePeriod.NIGHT: 
            time_hint = "夜晚的漆黑天空, 深沉的夜景，城市霓虹灯光, 室内温馨的人造暖光"
        else: 
            time_hint = "深夜的幽暗氛围，漆黑的环境，城市夜景，昏暗的室内人造光，宁静的氛围"

        # 2. 穿搭提示
        outfit_hint = (
            "当前是休息时间，优先提取睡衣、家居服、拖鞋、赤脚等居家状态；"
            "只有文案或日程明确正在外出时，才使用完整外出穿搭。"
            if is_night
            else
            "当前是活动时间，请结合生活日程里的地点、天气、温度、今日穿搭提取合理穿搭。"
        )
        outfit_rules = """
【穿搭合理性规则】
1. 必须优先参考【生活日程】里的天气、温度、今日穿搭、当前活动、完整时间轴；缺失时再根据【分享文案】和当前时段推断。
2. 判断场景类型：
   - 家里：可穿家居服、睡衣、拖鞋或赤脚；炎热时可以不穿外套；寒冷时可以加针织开衫、毛绒拖鞋、毯子。
   - 室内公共场所：可脱外套或把外套搭在椅背，但一般保留日常鞋子；只有酒店房间、瑜伽馆、榻榻米、换鞋区等场景才允许拖鞋或赤脚。
   - 室外：必须是合理外出状态；寒冷要有外套、围巾、长裤或保暖鞋子；炎热要轻薄衣物、凉鞋或透气鞋；雨雪天气要体现伞、防水鞋、湿润地面等细节。
3. 脚部状态必须写进 outfit，但不要机械按关键词判断；要先理解文案里的动作、地点、身体姿态、地面接触方式和生活习惯，再决定赤脚、拖鞋、居家鞋、居家袜或外出鞋：
   - 如果文案强调脚感、光脚、刚洗澡、泡脚、蜷在沙发/床上、瑜伽垫/榻榻米、海边沙滩等语境，赤脚可能更自然。
   - 如果文案只是描述在家中木地板、客厅、厨房、玄关等位置站立、走动或踩着地面，要判断日常生活里是否更自然是拖鞋、居家鞋或居家袜，不要为了画面氛围强行赤脚。
   - 如果文案没有提供足够依据，就选择最符合当前场景和温度的日常状态，并在 outfit_logic 里说明判断理由。
4. 不要让人物在室外赤脚，除非文案明确说明。
5. 不要让人物在家里穿厚重大衣和外出鞋，除非文案明确说明刚回家或准备出门。
6. 如果生活日程给了“今日穿搭”，可以在此基础上按当前地点和温度微调：例如在家可脱外套、换拖鞋；到室内公共场所可把外套搭在椅背；到室外则保持完整外出穿搭。
"""

        # 3. 动态构建地点逻辑提示词
        # 读取配置，默认为文案主导
        prioritize_text = self.img_conf.get("priority_text_over_schedule", True)

        if prioritize_text:
            # 模式一：文案主导（文案优先于日程）
            logic_prompt = f"""
1. **第一优先级（文案主导）**：首先检查【分享文案】。如果文案中明确提及了地点（例如：“我在海边”、“刚到酒店”、“去公园玩”），**必须无条件直接绘制文案描述的地点**，即使它与日程表冲突。
2. **第二优先级（日程补缺）**：只有当【分享文案】**完全未提及**地点时，才提取日程中 **{curr_hour}:00 正在进行** 的状态来设定背景场景。
"""
        else:
            # 模式二：日程主导（日程优先于文案）
            logic_prompt = f"""
1. **第一优先级（日程主导）**：首先检查【生活日程】。如果 **{curr_hour}:00** 有明确的活动地点（例如：“在办公室”、“在健身房”），**必须无条件优先绘制日程地点**。忽略文案中的地点（视为比喻或回忆）。
2. **第二优先级（文案补缺）**：只有当【生活日程】为空或未明确指定地点时，才参考【分享文案】中的地点描述。
"""

        # 4. 定义系统提示词
        system_prompt = f"""你是一个专业的 AI 绘画视觉导演。
任务：根据用户的【分享文案】和【生活日程】，提取画面关键词。

【提取逻辑】
1. **分析主体 (Subject)**：首先判断文案是否在描述或推荐一个**具体物品**（如美食、书籍、电子产品、电影海报）。
   - 如果是：该物品就是【subject】。
   - 如果否（文案是纯风景描绘）：【subject】填“无”。
2. **分析背景 (Environment)**：
{logic_prompt}
3. **负向过滤（未来禁区）**：**严禁**提取 {curr_hour}:00 之后的未来日程作为背景。
   - 错误示例：现在8点，日程显示11点去公园。-> **绝对不能**画公园。
   - 正确操作：现在8点，日程显示9点才醒。-> **必须**画卧室/床/室内。
4. **场景与穿搭判断**：先判断当前画面属于“家里 / 室内公共场所 / 室外 / 未知”，再根据天气和温度决定外套、脚部状态、层次和材质。

{outfit_rules}

【提取要求】
1. **主体 (subject)**：【最重要】画面的核心物体描述（例如：精致的荷花酥，一杯牛奶或者一本封皮复古的书）。如果是纯风景或画人，此项填“无”。
2. **环境 (environment)**：根据逻辑确定的具体地点。
3. **光影 (lighting)**：参考时间段[{time_hint}]。如果是室内，强调人造光；如果是室外，强调自然天气氛围。
4. **场景 (scene_type)**：填“家里 / 室内公共场所 / 室外 / 未知”之一。
5. **温感 (temperature_feel)**：根据天气温度和文案判断，填“寒冷 / 微凉 / 舒适 / 温暖 / 炎热 / 未知”之一。
6. **天气 (weather_condition)**：提取晴、雨、雪、阴、闷热、潮湿等真实天气；不明确则填“未知”。
7. **穿搭 (outfit)**：{outfit_hint} 请明确区分"内搭"和"外穿"层次，并说明外套是否穿着、半脱、挂在椅背或不需要；脚部状态也要自然融入穿搭里，要由你理解文案后决定。
8. **穿搭逻辑 (outfit_logic)**：用一句话说明为什么这样穿，重点说明你如何根据地点、温度、动作和文案语气判断外套与脚部状态。
9. **动作 (action)**：人物动作。

请严格输出 JSON 格式：
{{
    "subject": "...",      // 主体 (例如: 粉色荷花酥)
    "environment": "...",  // 环境 (例如: 苏州河畔的野餐垫上)
    "lighting": "...",     // 光影 (例如：昏黄的室内灯光)
    "scene_type": "...",   // 场景: 家里 / 室内公共场所 / 室外 / 未知
    "temperature_feel": "...", // 温感: 寒冷 / 微凉 / 舒适 / 温暖 / 炎热 / 未知
    "weather_condition": "...", // 天气: 晴 / 雨 / 雪 / 阴 / 闷热 / 潮湿 / 未知
    "outfit": "...",       // 穿搭，包含外套层次和脚部状态 (例如：白色棒球服外套，内搭黑色高领毛衣，白色运动鞋)
    "outfit_logic": "...", // 穿搭逻辑 (例如：在家且温暖，所以脱掉外套换成拖鞋)
    "action": "...",       // 动作 (例如：双手捧着热咖啡)
    "weather_vibe": "..."  // 例如：玻璃上有水雾，朦胧感
}}
"""
        user_prompt = f"【分享文案】：{content}\n【生活日程】：{life_context}\n\n请提取视觉元素："

        try:
            res = await self._call_llm(user_prompt, system_prompt, timeout=45, target_umo=target_umo)
            if not res: return {}
            # 清洗结构化结果。
            clean_json = res.replace("```json", "").replace("```", "").strip()
            match = re.search(r"\{.*\}", clean_json, re.DOTALL)
            if match: clean_json = match.group(0)
            return json.loads(clean_json)
        except Exception as e:
            logger.warning(f"[每日分享] 智能提取失败: {e}")
            return {}

    def _format_outfit_consistency_hint(self, visuals: Dict) -> str:
        scene_type = str(visuals.get("scene_type", "") or "").strip()
        temperature = str(visuals.get("temperature_feel", "") or "").strip()
        weather = str(visuals.get("weather_condition", "") or "").strip()

        details = []
        if scene_type and scene_type != "未知":
            details.append(f"场景：{scene_type}")
        if temperature and temperature != "未知":
            details.append(f"温感：{temperature}")
        if weather and weather != "未知":
            details.append(f"天气：{weather}")

        if not details:
            return "穿搭、外套和脚部状态必须符合当前地点、天气和温度。"

        scene_key = scene_type.lower()
        if "家" in scene_type or "home" in scene_key:
            rule = "家中要结合动作、姿态、温度和文案语气决定拖鞋、居家袜或赤脚。"
        elif "室外" in scene_type or "户外" in scene_type or "outdoor" in scene_key:
            rule = "室外必须保持合理外出状态，避免赤脚，并让脚部状态自然融入外出穿搭。"
        elif "室内" in scene_type or "indoor" in scene_key:
            rule = "室内公共场所可脱外套但通常保留日常鞋子，除非是明确换鞋场景。"
        else:
            rule = "穿搭、外套和脚部状态必须符合当前地点、天气和温度。"

        return f"{'，'.join(details)}，{rule}"

    async def _check_involves_self(self, content: str, sharing_type: SharingType, target_umo: str = None) -> bool:
        """检测内容是否涉及'自己'"""
        # 1. 强制配置优先
        if self.img_conf.get("image_always_include_self", False): return True
        if self.img_conf.get("image_never_include_self", False): return False
        
        try:
            # 2. 根据类型给予额外提示
            type_hint = ""
            if sharing_type in [SharingType.GREETING, SharingType.MOOD]: 
                type_hint = "(提示：问候或心情分享通常需要人物出镜)"
            
            # 3. 使用详细的判别标准
            system_prompt = f"""你是一个AI绘画构图顾问。
任务：根据用户的【分享文案】，判断画面中【是否需要出现人物角色】。
【判断标准】
- YES (画人): 
  1. 包含第一人称动作/状态 ("我穿着..." "我正在..." "我感觉...")
  2. 社交问候/互动 ("早安" "晚安" "看着我")
  3. 表达个人情绪/自拍感 ("今天好开心" "累瘫了")
  
- NO (画景/物): 
  1. 纯客观描述 ("今天天气很好" "这朵花很美")
  2. 推荐具体物品 ("推荐这本书" "这个电影很好看")
  3. 分享新闻/知识 ("据说..." "你知道吗...")
请回答 YES 或 NO，不要解释。"""
            user_prompt = f"类型：{sharing_type.value} {type_hint}\n内容：{content}\n\n是否含人物？"
            
            res = await self._call_llm(user_prompt, system_prompt, timeout=10, target_umo=target_umo)
            if res and "YES" in res.strip().upper(): return True
        except Exception as e:
            logger.debug(f"[图像服务] 人物判断失败，按不含人物处理: {e}")
        
        return False

    async def _get_appearance_keywords(self, target_umo: str = None) -> str:
        """获取人设外貌"""
        conf_p = self.img_conf.get("appearance_prompt", "").strip()
        if conf_p: return conf_p
        try:
            p_obj = await self.context.persona_manager.get_default_persona_v3()
            p_text = p_obj.get("prompt", "") if p_obj else ""
            if not p_text: return ""
            prompt = f"""请从以下人设描述中提取外貌特征，并转换为中文的图片生成提示词。
人设描述：
{p_text}
要求：
1. 【重要】必须包含人种/国籍描述
2. 提取外貌细节（发型、发色、眼睛、肤色、体型）
3. 转换为简短的中文关键词，用逗号分隔
4. 不要包含性格、职业等非外貌信息
5. 直接输出中文关键词，不要解释
请输出："""
            res = await self._call_llm(prompt, timeout=20, target_umo=target_umo)
            return res.strip() if res else ""
        except Exception as e:
            logger.debug(f"[图像服务] 提取人设外貌失败: {e}")
            return ""

    async def _assemble_final_prompt(self, content: str, sharing_type: SharingType, involves_self: bool, visuals: Dict, target_umo: str = None) -> str:
        prompts = []
        comp_desc = "" 
        
        # 定义质量词
        quality_tags = "8K分辨率, 高质量, 写实, 高分辨率, 细节丰富, 色彩鲜艳, 电影级光影效果"

        # 1. 主体与构图
        if involves_self:
            # === 人物模式 ===
            action = visuals.get("action", "")
            
            # 一、外貌
            if self.img_conf.get("use_gitee_selfie_ref", False):
                logger.info("[每日分享] 已启用形象参考图，跳过默认人设外貌提取")
            else:
                appearance = await self._get_appearance_keywords(target_umo=target_umo)
                if appearance: prompts.append(appearance)
                else: prompts.append("1个女孩, 独奏")

            # 二、穿搭
            raw_outfit = str(visuals.get("outfit", "") or "").strip()
            if raw_outfit: prompts.append(raw_outfit)
            
            # 三、动作
            if action: prompts.append(action)

            # 四、决定镜头（人物版）
            # 提取公共判断：是否存在明确的主体（物品）
            subject_str = visuals.get("subject", "")
            has_subj = subject_str and subject_str not in ["无", "N/A", "None", ""]

            if sharing_type == SharingType.GREETING: 
                comp_desc = "半身像, 面对镜头, 眼神交流, 背景虚化"
            elif sharing_type == SharingType.MOOD: 
                comp_desc = "特写, 脸部聚焦, 情绪表达, 景深效果"
            elif sharing_type == SharingType.NEWS: 
                if not action and not has_subj:
                    comp_desc = "中景, 生活快照, 看手机或屏幕"
                else:
                    comp_desc = "中景, 生活快照"
            elif sharing_type == SharingType.RECOMMENDATION: 
                if not action and not has_subj:
                    comp_desc = "中景, 展示物品, 手部特写, 聚焦物体"
                else:
                    comp_desc = "中景, 聚焦物体"
            else: 
                comp_desc = "中景, 自然姿态"

        else:
            # === 纯静物/风景模式 ===
            subject = visuals.get("subject", "")
            # 判断主体是否有效
            is_valid_subject = subject and subject not in ["无", "N/A", "None", ""]
            
            if is_valid_subject:
                # [静物逻辑] 具体物品推荐
                prompts.append("无人, 静物")
                prompts.append(subject) 
                
                # 四、决定镜头（静物版）
                comp_desc = "特写, 景深, 静物摄影, 高细节"
            else:
                # [风景逻辑] 纯风景/空镜
                prompts.append("无人, 风景, 景观, 细节丰富")
                
                # 四、决定镜头（风景版）
                comp_desc = "广角镜头, 全景视图"

        # 统一追加镜头描述
        if comp_desc: prompts.append(comp_desc)

        # 2. 环境、光影与天气
        env = visuals.get("environment", "")
        lighting = visuals.get("lighting", "")
        weather_vibe = visuals.get("weather_vibe", "")
        
        if env: prompts.append(f"位于 {env}")
        else: prompts.append("简单的背景")

        if lighting: prompts.append(lighting)
        else:
            # 兜底光影
            period = self._get_current_period()
            if period in [TimePeriod.NIGHT, TimePeriod.LATE_NIGHT]: prompts.append("夜晚, 城市灯光")
            else: prompts.append("白天, 自然光")

        if weather_vibe: prompts.append(weather_vibe)

        if involves_self:
            outfit_consistency = self._format_outfit_consistency_hint(visuals)
            if outfit_consistency: prompts.append(outfit_consistency)

        # 3. 质量词
        prompts.append(quality_tags)

        return ", ".join(filter(None, prompts))
