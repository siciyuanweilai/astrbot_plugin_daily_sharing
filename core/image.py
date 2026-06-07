import os
import re
import json
import random
from datetime import datetime
from typing import Optional, Dict, Any, List
from astrbot.api import logger
from ..config import SharingType, TimePeriod
from .image_providers import ImageProviderManager

class ImageService:
    def __init__(self, context, config, llm_func):
        self.context = context
        self.config = config
        self.call_llm = llm_func
        self._last_image_description = None
        
        # 获取配置引用
        self.img_conf = self.config.get("image_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.provider_manager = ImageProviderManager(context, self.img_conf)

    async def _call_llm(self, *args, target_umo: str = None, **kwargs):
        if target_umo:
            kwargs["umo"] = target_umo
            try:
                return await self.call_llm(*args, **kwargs)
            except TypeError as e:
                if "umo" not in str(e):
                    raise
                kwargs.pop("umo", None)
        return await self.call_llm(*args, **kwargs)

    def _get_current_period(self) -> TimePeriod:
        """获取当前时间段"""
        hour = datetime.now().hour
        if 0 <= hour < 6: return TimePeriod.DAWN
        elif 6 <= hour < 9: return TimePeriod.MORNING
        elif 9 <= hour < 12: return TimePeriod.FORENOON
        elif 12 <= hour < 16: return TimePeriod.AFTERNOON
        elif 16 <= hour < 19: return TimePeriod.EVENING
        elif 19 <= hour < 22: return TimePeriod.NIGHT
        else: return TimePeriod.LATE_NIGHT

    def _ensure_plugin(self):
        """确保Gitee插件已加载"""
        return self.provider_manager.get_gitee_plugin()

    # ==================== 1. 核心逻辑：Agent 提取 ====================

    async def _agent_extract_visuals(self, content: str, life_context: str, target_umo: str = None) -> Dict[str, str]:
        """
        使用 Agent 思维一次性提取：主体、环境、光影、穿搭、动作。
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
        elif period == TimePeriod.AFTERNOON:
            time_hint = "下午的充足阳光，光影对比清晰，慵懒或明亮的氛围, 清晰的照明"
        elif period == TimePeriod.EVENING: 
            time_hint = "傍晚的暖色调，温暖的金色夕阳, 晚霞或暮色，柔和的长阴影，逆光轮廓"
        elif period == TimePeriod.NIGHT: 
            time_hint = "夜晚的漆黑天空, 深沉的夜景，城市霓虹灯光, 室内温馨的人造暖光"
        else: 
            time_hint = "深夜的幽暗氛围，漆黑的环境，城市夜景，昏暗的室内人造光，宁静的氛围"

        # 2. 穿搭提示
        outfit_hint = "当前是休息时间，忽略白天外出服装，仅提取睡衣或家居服。" if is_night else "当前是活动时间，提取完整的外出日常穿搭。"

        # 3. 动态构建地点逻辑 Prompt
        # 读取配置，默认为 True (文案主导)
        prioritize_text = self.img_conf.get("priority_text_over_schedule", True)

        if prioritize_text:
            # 模式 A: 文案主导 (文案 > 日程)
            logic_prompt = f"""
1. **第一优先级（文案主导）**：首先检查【分享文案】。如果文案中明确提及了地点（例如：“我在海边”、“刚到酒店”、“去公园玩”），**必须无条件直接绘制文案描述的地点**，即使它与日程表冲突。
2. **第二优先级（日程补缺）**：只有当【分享文案】**完全未提及**地点时，才提取日程中 **{curr_hour}:00 正在进行** 的状态来设定背景场景。
"""
        else:
            # 模式 B: 日程主导 (日程 > 文案)
            logic_prompt = f"""
1. **第一优先级（日程主导）**：首先检查【生活日程】。如果 **{curr_hour}:00** 有明确的活动地点（例如：“在办公室”、“在健身房”），**必须无条件优先绘制日程地点**。忽略文案中的地点（视为比喻或回忆）。
2. **第二优先级（文案补缺）**：只有当【生活日程】为空或未明确指定地点时，才参考【分享文案】中的地点描述。
"""

        # 4. 定义 System Prompt
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

【提取要求】
1. **主体 (subject)**：【最重要】画面的核心物体描述（例如：精致的荷花酥，一杯牛奶或者一本封皮复古的书）。如果是纯风景或画人，此项填“无”。
2. **环境 (environment)**：根据逻辑确定的具体地点。
3. **光影 (lighting)**：参考时间段[{time_hint}]。如果是室内，强调人造光；如果是室外，强调自然天气氛围。
4. **穿搭 (outfit)**：{outfit_hint} 请明确区分"内搭"和"外穿"层次。
5. **动作 (action)**：人物动作。

请严格输出 JSON 格式：
{{
    "subject": "...",      // 主体 (例如: 粉色荷花酥)
    "environment": "...",  // 环境 (例如: 苏州河畔的野餐垫上)
    "lighting": "...",     // 光影 (例如：昏黄的室内灯光)
    "outfit": "...",       // 穿搭 (例如：白色棒球服外套，内搭黑色高领毛衣)
    "action": "...",       // 动作 (例如：双手捧着热咖啡)
    "weather_vibe": "..."  // 例如：玻璃上有水雾，朦胧感
}}
"""
        user_prompt = f"【分享文案】：{content}\n【生活日程】：{life_context}\n\n请提取视觉元素："

        try:
            res = await self._call_llm(user_prompt, system_prompt, timeout=45, target_umo=target_umo)
            if not res: return {}
            # 清洗 JSON
            clean_json = res.replace("```json", "").replace("```", "").strip()
            match = re.search(r"\{.*\}", clean_json, re.DOTALL)
            if match: clean_json = match.group(0)
            return json.loads(clean_json)
        except Exception as e:
            logger.warning(f"[DailySharing] Agent 提取失败: {e}")
            return {}

    # ==================== 2. 辅助逻辑：判断与外貌 ====================

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

    # ==================== 3. 主入口 ====================

    async def generate_image(self, content: str, sharing_type: SharingType, life_context: str = None, target_umo: str = None) -> Optional[str]:
        """生成图片的入口函数"""
        self.reset_last_description()
        if not self.img_conf.get("enable_ai_image", False): return None

        # 1. 智能判断：是否画人
        involves_self = await self._check_involves_self(content, sharing_type, target_umo=target_umo)
        mode_str = "人物+场景" if involves_self else "纯静物/风景"
        is_text_priority = self.img_conf.get("priority_text_over_schedule", True)
        logic_str = "文案主导" if is_text_priority else "日程主导"        

        # 2. 检测是否启用 Gitee 形象参考图逻辑
        use_gitee_ref = self.img_conf.get("use_gitee_selfie_ref", False)
        is_selfie_mode = involves_self and use_gitee_ref
        
        logger.info(f"[DailySharing] 配图决策: {mode_str} ({logic_str}) | 类型: {sharing_type.value} | 形象模式: {is_selfie_mode}")        
        
        # 3. Agent 提取视觉元素
        visuals = {}
        if content or life_context:
            visuals = await self._agent_extract_visuals(content, life_context, target_umo=target_umo)
            
            # 如果 LLM 提取失败（返回空字典），则直接放弃配图
            if not visuals:
                logger.warning("[DailySharing] Agent 提取失败，已取消配图，仅发送文案")
                return None

            # 日志记录提取结果
            env = visuals.get('environment', '无')
            subj = visuals.get('subject', '无')
            outfit = visuals.get('outfit', '无') if involves_self else "N/A"
            weather = visuals.get('weather_vibe', '无')
            logger.info(f"[DailySharing] 配图 Agent 提取 -> 主体: {subj} | 环境: {env} | 天气: {weather} | 穿搭: {outfit[:15]}...")

        # 4. 组装最终 Prompt
        prompt = await self._assemble_final_prompt(content, sharing_type, involves_self, visuals, target_umo=target_umo)
        
        if not prompt: 
            logger.warning("[DailySharing] Prompt 组装失败，取消配图")
            return None
        logger.info(f"[DailySharing] 最终配图 Prompt: {prompt[:100]}...")
        self._last_image_description = prompt
        
        # 5. 调用所选生图 provider 生成
        return await self._call_image_provider(prompt, use_ref_selfie=is_selfie_mode)

    async def _assemble_final_prompt(self, content: str, sharing_type: SharingType, involves_self: bool, visuals: Dict, target_umo: str = None) -> str:
        prompts = []
        comp_desc = "" 
        
        # 定义质量词
        quality_tags = "8K分辨率, 高质量, 写实, 高分辨率, 细节丰富, 色彩鲜艳, 电影级光影效果"

        # 1. 主体与构图
        if involves_self:
            # === 人物模式 ===
            action = visuals.get("action", "")
            
            # A. 外貌
            appearance = await self._get_appearance_keywords(target_umo=target_umo)
            if appearance: prompts.append(appearance)
            else: prompts.append("1个女孩, 独奏")

            # B. 穿搭 
            raw_outfit = visuals.get("outfit", "")
            if raw_outfit: prompts.append(raw_outfit)
            
            # C. 动作
            if action: prompts.append(action)

            # D. 决定镜头 (人物版)
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
                
                # D. 决定镜头 (静物版)
                comp_desc = "特写, 景深, 静物摄影, 高细节"
            else:
                # [风景逻辑] 纯风景/空镜
                prompts.append("无人, 风景, 景观, 细节丰富")
                
                # D. 决定镜头 (风景版)
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

        # 3. 质量词
        prompts.append(quality_tags)

        return ", ".join(filter(None, prompts))

    # ==================== 4. 工具函数 ====================

    async def _build_video_motion_prompt(self, image_description: str, content: str = "", target_umo: str = None) -> str:
        """根据画面描述和文案生成图生视频动态提示词。"""
        fallback = "保持原始人物、服装和场景一致，轻微自然动作，电影感缓慢推近或平移，画面稳定，氛围感"
        image_description = str(image_description or "").strip()
        content = str(content or "").strip()
        if not image_description and not content:
            return fallback

        system_prompt = """
你是短视频专业导演。根据画面描述和分享文案，为生活感短视频生成动态提示词。
要求：
1. 只输出一行中文视频动态提示词，不要解释。
2. 根据分享文案的内容、情绪和画面场景决定镜头运动、人物或物体细微动作、氛围变化；镜头运动应整体自然，可轻微推近、缓慢横移或带少量手持呼吸感，不改变原图景别、构图重心和人物比例。
3. 必须保持原图人物五官、脸型、发型、年龄感、气质、服装、场景、光线、构图和主体位置一致，不新增人物或物体，不改变画面关系。
4. 如果画面有人物，人物应在不破坏原图姿态和朝向的前提下，自然看向镜头或与镜头产生轻微眼神交流，保持原有神态和气质。
5. 如果声音提示词包含画面内人物台词，人物需保持原有神态和气质，嘴唇轻微自然开合并与说话节奏同步，可有细微点头、眨眼、呼吸感，不做夸张表演；如果声音提示词不包含人物台词，则人物不要出现明显说话口型。
6. 动态要自然、有氛围感并贴合画面，适合生活感短视频。
"""
        user_prompt = f"画面描述：{image_description}\n分享文案：{content}\n\n请生成视频动态提示词："

        try:
            res = await self._call_llm(user_prompt, system_prompt, timeout=10, target_umo=target_umo)
            motion = re.sub(r"```(?:\w+)?|```", "", str(res or ""))
            motion = re.sub(r"^\s*[-*]\s*", "", motion)
            motion = re.sub(r"\s+", " ", motion).strip(" ：:，,。")
            if motion:
                if not motion.startswith(("视频设计", "镜头设计", "动态设计")):
                    motion = f"{motion}"
                return motion[:260]
        except Exception as e:
            logger.debug(f"[DailySharing] 生成视频动态提示词失败，使用默认视频动态提示词: {e}")

        return fallback

    async def _build_video_sound_prompt(self, image_description: str, content: str = "", target_umo: str = None) -> str:
        """根据画面描述和文案生成视频声音设计提示词。"""
        fallback = "根据画面和文案情绪选择自然环境声、细微动作声、合适的钢琴背景声氛围或轻声人声，整体氛围自然"
        image_description = str(image_description or "").strip()
        content = str(content or "").strip()
        if not image_description and not content:
            return fallback

        system_prompt = """
你是短视频声音设计师。根据画面描述和分享文案，为生活感短视频生成声音提示词。
要求：
1. 只输出一行中文视频声音提示词，不要解释。
2. 根据分享文案的内容、情绪和画面场景决定环境声、动作声、钢琴背景声和人声设计，让声音自然融入画面，禁止旁白、画外音、解说或朗读文案。
3. 如果画面有人物，优先判断是否适合让人物根据文案内容自然开口说话；台词应口语化、生活化、短句表达，可提炼成一句符合人物状态和情绪的话。
4. 人物配音需符合原图人物的年龄、性别、气质和场景氛围，语气自然，像真实生活中随口说出，并对应画面人物的口型。
5. 当同时包含人物配音和钢琴背景声时，人声、环境声、动作声与钢琴背景声应自然融合，保持真实生活场景里的声音层次，人物说话清晰可听但不过分贴耳，钢琴背景声持续存在但不喧宾夺主。
6. 声音要自然、氛围感和贴合画面，适合生活感短视频。
"""
        user_prompt = f"画面描述：{image_description}\n分享文案：{content}\n\n请生成视频声音提示词："

        try:
            res = await self._call_llm(user_prompt, system_prompt, timeout=10, target_umo=target_umo)
            sound = re.sub(r"```(?:\w+)?|```", "", str(res or ""))
            sound = re.sub(r"^\s*[-*]\s*", "", sound)
            sound = re.sub(r"\s+", " ", sound).strip(" ：:，,。")
            if sound:
                if not sound.startswith(("声音设计", "声音创作", "音频设计")):
                    sound = f"{sound}"
                return sound[:240]
        except Exception as e:
            logger.debug(f"[DailySharing] 生成视频声音提示词失败，使用默认视频声音提示词: {e}")

        return fallback

    async def generate_video_from_image(self, image_path: str, content: str, target_umo: str = None) -> Optional[str]:
        """图片转视频"""
        if not self.img_conf.get("enable_ai_video", False): return None
        
        aiimg_plugin = self._ensure_plugin()
        if not aiimg_plugin: return None
        
        # 强制依赖新版 registry 架构
        if not hasattr(aiimg_plugin, "registry"): 
            logger.warning("[DailySharing] 检测到 GiteeAIImage 插件不支持视频后端注册表，跳过视频生成")
            return None
        
        try:
            if not os.path.exists(image_path): return None
            with open(image_path, "rb") as f: image_bytes = f.read()
            logger.info(f"[DailySharing] 正在将配图转换为视频...")
            
            # 构建视频提示词（复用之前的图片描述，生成匹配的动态和声音设计）
            image_description = self._last_image_description or ""
            motion_prompt = await self._build_video_motion_prompt(image_description, content, target_umo=target_umo)
            sound_prompt = await self._build_video_sound_prompt(image_description, content, target_umo=target_umo)
            video_prompt = f"{image_description}, {motion_prompt}, {sound_prompt}"
            logger.info(f"[DailySharing] 视频 Agent 提取 -> 动态: {motion_prompt[:180]}...")
            logger.info(f"[DailySharing] 配音 Agent 提取 -> 声音: {sound_prompt[:180]}...")
            logger.info(f"[DailySharing] 最终视频 Prompt: {video_prompt[:180]}...")
            
            # 获取配置的视频提供商链
            if hasattr(aiimg_plugin, "_get_video_chain"):
                chain = aiimg_plugin._get_video_chain()
            else:
                logger.warning("[DailySharing] 无法获取视频服务配置链")
                return None
            
            if not chain:
                logger.warning("[DailySharing] 未配置视频服务提供商")
                return None
            
            # 取第一个可用的提供商 ID
            provider_id = chain[0]
            try:
                # 从注册表中获取后端服务并调用
                backend = aiimg_plugin.registry.get_video_backend(provider_id)
                return await backend.generate_video_url(prompt=video_prompt, image_bytes=image_bytes)
            except Exception as e:
                logger.error(f"[DailySharing] 获取视频后端或生成失败: {e}")
                return None
                
        except Exception as e:
            logger.error(f"[DailySharing] 视频生成流程异常: {e}")
            return None

    def get_last_description(self) -> Optional[str]:
        return self._last_image_description

    def reset_last_description(self):
        self._last_image_description = None

    async def _get_gitee_reference_images(self) -> List[bytes]:
        """从 Gitee 插件中提取参考图"""
        gitee = self.provider_manager.get_gitee_plugin()
        if not gitee: return []
        
        try:
            # 1. 优先从 WebUI 配置读取 
            if hasattr(gitee, "_get_config_selfie_reference_paths") and hasattr(gitee, "_read_paths_bytes"):
                ref_paths = gitee._get_config_selfie_reference_paths()
                if ref_paths:
                    return await gitee._read_paths_bytes(ref_paths)
            
            # 2. 其次尝试从 RefStore 读取 
            if hasattr(gitee, "refs"):
                # 尝试通用 key
                ref_paths = await gitee.refs.get_paths("bot_selfie")
                if ref_paths and hasattr(gitee, "_read_paths_bytes"):
                    return await gitee._read_paths_bytes(ref_paths)
                    
        except Exception as e:
            logger.warning(f"[DailySharing] 获取 Gitee 参考图失败: {e}")
        
        return []

    async def _call_image_provider(self, prompt: str, use_ref_selfie: bool = False) -> Optional[str]:
        """调用配置的生图 provider。"""
        provider = self.provider_manager.select_provider()
        if provider == "generic_plugin":
            if use_ref_selfie:
                logger.info("[DailySharing] 当前为通用生图 provider，形象参考图仅在 Gitee provider 下生效")
            return await self.provider_manager.generate_with_generic_plugin(prompt)

        return await self._call_gitee_aiimg(prompt, use_ref_selfie=use_ref_selfie)

    async def _call_gitee_aiimg(self, prompt: str, use_ref_selfie: bool = False) -> Optional[str]:
        """调用底层Gitee插件"""
        aiimg_plugin = self._ensure_plugin()
        if not aiimg_plugin:
            logger.error("[DailySharing] 未找到 astrbot_plugin_gitee_aiimg 插件")
            return None

        try:
            # ================= 形象参考图逻辑 =================
            if use_ref_selfie and hasattr(aiimg_plugin, "edit"):
                logger.info("[DailySharing] 正在使用 Gitee 形象参考图生成...")
                
                # 1. 获取参考图
                ref_images = await self._get_gitee_reference_images()
                if ref_images:
                    logger.info(f"[DailySharing] 找到 {len(ref_images)} 张参考图，调用图生图接口")
                    
                    # 2. 构建 Prompt 前缀
                    final_prompt = (
                        "请根据参考图生成一张新的生活照：\n"
                        "1) 以第1张参考图的人脸身份为准（仅人脸身份特征），保持五官/气质一致。\n"
                        "2) 如果还有其它参考图，请将它们仅作为服装/姿势/构图/场景的参考。\n"
                        f"3) 画面具体描述：{prompt}\n"
                        "4) 输出高质量生活照片风格，不要拼图，不要水印。"
                    )
                    
                    # 3. 调用 Edit 接口
                    path_obj = await aiimg_plugin.edit.edit(
                        prompt=final_prompt,
                        images=ref_images,
                        backend=None,
                        task_types=["id", "background", "style"] 
                    )
                    return str(path_obj)
                else:
                    logger.warning("[DailySharing] 虽开启形象模式，但未找到参考图，降级为文生图")

            # ================= 普通文生图逻辑 =================
            if hasattr(aiimg_plugin, "draw"):
                target_size = aiimg_plugin.config.get("size", "1024x1024")
                path_obj = await aiimg_plugin.draw.generate(prompt=prompt, size=target_size)
                return str(path_obj)
            else:
                 # 这种情况下通常意味着获取到的是 Class 而非 Instance，或者插件异常
                 logger.error("[DailySharing] Gitee插件实例不完整，无法生成图片")
                 return None

        except Exception as e: 
            logger.error(f"[DailySharing] 生成图片出错: {e}")
            return None
