# services/image.py
import os
import random
from typing import Optional
from astrbot.api import logger
from ..config import SharingType, TimePeriod

class ImageService:
    def __init__(self, context, config, llm_func):
        self.context = context
        self.config = config
        self.call_llm = llm_func
        self._aiimg_plugin = None
        self._aiimg_plugin_not_found = False
        self._last_image_description = None

    def _get_current_period(self) -> TimePeriod:
        from datetime import datetime
        hour = datetime.now().hour
        if 0 <= hour < 6: return TimePeriod.DAWN
        elif 6 <= hour < 11: return TimePeriod.MORNING
        elif 11 <= hour < 17: return TimePeriod.AFTERNOON
        elif 17 <= hour < 20: return TimePeriod.EVENING
        else: return TimePeriod.NIGHT

    # ==================== 主入口 ====================
    async def generate_image(self, content: str, sharing_type: SharingType, life_context: str = None) -> Optional[str]:
        if not self.config.get("enable_ai_image", False): return None

        # 1. 检测是否涉及自己
        involves_self = await self._check_involves_self(content, sharing_type)
        
        # 2. 提取穿搭 (仅当涉及自己且有上下文时)
        outfit_info = None
        if involves_self and life_context:
            outfit_info = await self._extract_outfit(life_context)
            if outfit_info:
                logger.debug(f"[DailySharing] 🎨 使用智能提取的穿搭: {outfit_info}")

        # 3. 生成 Prompt
        prompt = await self._generate_image_prompt(content, sharing_type, involves_self, outfit_info)
        if not prompt: 
            logger.warning("[DailySharing] 提示词生成失败")
            return None

        logger.debug(f"[DailySharing] 配图提示词: {prompt[:100]}...")
        
        # 4. 生成中文描述用于记忆
        self._last_image_description = await self._convert_prompt_to_description(prompt)

        # 5. 调用画图插件
        return await self._call_aiimg(prompt)

    def get_last_description(self):
        d = self._last_image_description
        self._last_image_description = None
        return d

    # ==================== 智能判断逻辑 ====================
    async def _check_involves_self(self, content: str, sharing_type: SharingType) -> bool:
        """【智能版】检测内容是否涉及'自己'"""
        # 1. 配置强制模式
        if self.config.get("image_always_include_self", False):
            logger.debug("[DailySharing] 配置：始终包含自己")
            return True
        if self.config.get("image_never_include_self", False):
            logger.debug("[DailySharing] 配置：从不包含自己")
            return False

        # 2. LLM 智能判断
        try:
            type_hint = ""
            if sharing_type == SharingType.GREETING: type_hint = "(提示：问候通常需要人物出镜)"
            elif sharing_type == SharingType.NEWS: type_hint = "(提示：新闻通常画具体事件或物体，不画人)"

            sys_p = f"""你是一个AI绘画构图顾问。
任务：根据用户的【分享内容】，判断画面中【是否需要出现人物角色】。

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
            
            user_p = f"分享类型：{sharing_type.value} {type_hint}\n内容：{content}\n\n画面是否包含人物？"
            
            res = await self.call_llm(user_p, sys_p, timeout=10)
            if res:
                if "YES" in res.upper(): return True
                if "NO" in res.upper(): return False
        except Exception as e:
            logger.warning(f"[DailySharing] 智能判断出镜失败: {e}")

        # 3. 关键词兜底
        keywords = [
            "我", "我的", "我在", "我正在", "我刚", "我想", "我觉得", "我发现",
            "咱", "本人", "俺", "吾", "余",
            "感觉", "觉得", "想起", "回忆", "心情", "开心", "难过", "激动",
            "喜欢", "讨厌", "推荐", "分享", "发现", "学到", "体会",
            "今天", "昨天", "刚才", "最近"
        ]
        if any(k in content for k in keywords): return True

        # 4. 特定类型兜底
        if sharing_type in [SharingType.GREETING, SharingType.MOOD, SharingType.RECOMMENDATION]:
            return True
            
        return False

    # ==================== 穿搭与外貌 ====================
    async def _extract_outfit(self, life_ctx: str) -> Optional[str]:
        """从生活上下文提取穿搭"""
        period = self._get_current_period()
        is_night = period in [TimePeriod.NIGHT, TimePeriod.DAWN]
        time_desc = "深夜/休息时间" if is_night else "白天/活动时间"
        
        prompt = f"""任务：从生活状态描述中，提取**符合当前时间段**的角色穿搭，翻译为 **AI绘画英文提示词**。
【时间】：{time_desc}
【状态】：{life_ctx}
【规则】：
1. 如果是深夜，优先提取睡衣/家居服。
2. 如果是白天，优先提取外出服/常服。
3. 仅输出逗号分隔的英文单词。
请输出英文穿搭提示词："""
        
        res = await self.call_llm(prompt, timeout=30)
        return res.replace("Output:", "").strip() if res else None

    async def _smart_filter_outfit(self, outfit: str, scene_context: str) -> str:
        """根据构图过滤鞋袜"""
        sys_p = "你是一个AI绘画Prompt优化专家。如果场景暗示【看不见脚】(如upper body, sitting, close-up)，请从穿搭中【删除】鞋袜描述。仅输出修改后的穿搭英文单词。"
        user_p = f"当前穿搭：{outfit}\n场景构图：{scene_context}\n\n请输出优化后的穿搭："
        res = await self.call_llm(user_p, sys_p, timeout=20)
        return res.strip() if res else outfit

    async def _get_appearance_keywords(self) -> str:
        """获取人设外貌"""
        # 1. 配置优先
        conf_p = self.config.get("appearance_prompt", "").strip()
        if conf_p: return conf_p

        # 2. 从人设提取
        try:
            pid = self.config.get("persona_id", "")
            p_text = ""
            
            if pid: 
                persona = await self.context.persona_manager.get_persona(pid)
                p_text = persona.system_prompt if persona else ""
            else:
                # 获取默认人设
                p_obj = await self.context.persona_manager.get_default_persona_v3()
                p_text = p_obj.get("prompt", "") if p_obj else ""
            
            if not p_text or len(p_text) < 10: return ""

            prompt = f"""请从以下人设描述中提取外貌特征，转换为英文图片生成提示词。
人设：{p_text}
要求：必须包含人种/国籍。提取发型、发色、眼睛、肤色。用逗号分隔。仅输出关键词。"""
            
            res = await self.call_llm(prompt, timeout=30)
            return res.replace("```", "").replace("\n", ", ").strip() if res else ""
        except: return ""

    # ==================== Prompt 生成核心 ====================
    async def _generate_image_prompt(self, content, stype, involves_self, outfit) -> str:
        scene_prompt = await self._generate_scene_prompt(content, stype, involves_self, outfit)
        if not scene_prompt: return ""
        
        final_prompt = scene_prompt
        # 叠加外貌
        if involves_self:
            appearance = await self._get_appearance_keywords()
            if appearance: final_prompt = f"{appearance}, {final_prompt}"
        
        # 叠加质量词
        return f"{final_prompt}, realist style, masterpiece, best quality, high resolution, detailed, vibrant colors"

    async def _generate_scene_prompt(self, content, sharing_type, involves_self, outfit_info) -> str:
        period = self._get_current_period()
        
        # 光影逻辑
        if period in [TimePeriod.NIGHT, TimePeriod.DAWN]:
            env = "Night/Late Night"
            light = "dim lighting, indoor artificial light (lamp/screen), cinematic lighting"
            neg = "NO sunlight, NO blue sky"
        elif period == TimePeriod.EVENING:
            env = "Evening/Dusk"
            light = "warm golden lighting, sunset vibe, soft shadows"
            neg = "NO strong noon sun"
        else:
            env = "Daytime"
            light = "natural window light, bright, soft daylight"
            neg = "NO night view"

        if involves_self:
            # === 画人模式 ===
            if sharing_type == SharingType.GREETING: comp = "portrait, upper body, looking at viewer"
            elif sharing_type == SharingType.MOOD: comp = "close-up, facial focus"
            elif sharing_type == SharingType.NEWS: comp = "medium shot, sitting at desk"
            elif sharing_type == SharingType.RECOMMENDATION: comp = "medium shot, holding object"
            else: comp = "medium shot, natural pose"

            outfit_constraint = ""
            if outfit_info:
                filtered = await self._smart_filter_outfit(outfit_info, comp)
                outfit_constraint = f"穿搭：{filtered}\n💡 使用过滤后的穿搭"

            sys_p = f"""你是一个AI绘画提示词专家。
请根据用户的分享内容、当前时间段、以及生活状态，生成适合的场景、动作、穿搭描述。

【环境设定】
- 时间: {env}
- 光影: {light}
- 禁止: {neg}
- 构图: {comp}

要求：
1. 仅输出英文提示词，不要有任何解释
2. 描述人物的动作、姿态、表情
3. 描述场景、环境、氛围
4. 如果提供了穿搭信息，必须优先使用
5. 提示词用逗号分隔，简洁明确"""
            
            user_p = f"分享类型：{sharing_type.value}\n内容：{content}\n{outfit_constraint}\n\n生成人物场景提示词："
        else:
            # === 画景模式 ===
            sys_p = f"""你是一个AI绘画提示词专家。
请根据用户的分享内容、当前时间段，生成适合的纯场景描述。

【环境设定】
- 时间: {env}
- 光影: {light}
- 禁止: {neg}

要求：
1. 仅输出英文提示词，不要有任何解释
2. 描述场景、环境、氛围、主题
3. **不要包含人物描述** (No humans)
4. 提示词用逗号分隔，简洁明确"""
            
            user_p = f"分享类型：{sharing_type.value}\n内容：{content}\n\n生成纯景物提示词："
        
        res = await self.call_llm(user_p, sys_p, timeout=30)
        
        # 清理输出
        if res:
            res = res.strip().replace("Output:", "").replace("Prompt:", "")
            return res
        return self._get_fallback_scene_prompt(sharing_type, involves_self)

    def _get_fallback_scene_prompt(self, sharing_type: SharingType, involves_self: bool) -> str:
        """兜底场景逻辑"""
        period = self._get_current_period()
        
        if period in [TimePeriod.NIGHT, TimePeriod.DAWN]:
            time_suffix = ", dim lighting, indoor lamp light, dark atmosphere"
        elif period == TimePeriod.EVENING:
            time_suffix = ", warm lighting, sunset atmosphere"
        else:
            time_suffix = ", natural lighting, soft daylight"

        if involves_self:
            # 涉及自己的场景字典
            base_scenes = {
                SharingType.GREETING: "standing in cozy room, gentle smile, daily life theme",
                SharingType.NEWS: "sitting at desk, looking at phone/screen, casual lifestyle",
                SharingType.MOOD: "relaxing by window, thoughtful expression, peaceful vibe",
                SharingType.KNOWLEDGE: "reading book, focused, comfortable study room",
                SharingType.RECOMMENDATION: "holding an item, enthusiastic expression, sharing moment",
            }
        else:
            # 纯空镜字典
            base_scenes = {
                SharingType.GREETING: "aesthetic room corner, morning vibe, clean composition",
                SharingType.NEWS: "city street view, depth of field, urban life",
                SharingType.MOOD: "quiet corner, light and shadow, emotional atmosphere",
                SharingType.KNOWLEDGE: "bookshelf, desk setup, study atmosphere",
                SharingType.RECOMMENDATION: "product display style, elegant background, soft focus",
            }
  
        base = base_scenes.get(sharing_type, "aesthetic scene, high quality")
        return f"{base}{time_suffix}, masterpiece, best quality, realist style"

    # ==================== 辅助方法 ====================
    async def _convert_prompt_to_description(self, prompt: str) -> str:
        try:
            simple = prompt.replace("realist style,", "").strip()[:200]
            res = await self.call_llm(f"将以下英文提示词翻译为20字内中文描述:\n{simple}", timeout=15)
            return res.strip() if res else "图片"
        except: return "图片"

    async def _call_aiimg(self, prompt: str) -> Optional[str]:
        # 插件查找逻辑
        if not self._aiimg_plugin and not self._aiimg_plugin_not_found:
            for p in self.context.get_all_stars():
                if p.name == "astrbot_plugin_gitee_aiimg":
                    self._aiimg_plugin = p.star_cls
                    break
            if not self._aiimg_plugin: self._aiimg_plugin_not_found = True

        if self._aiimg_plugin:
            try: return await self._aiimg_plugin._generate_image(prompt=prompt, size="")
            except Exception as e: logger.error(f"[DailySharing] Generate error: {e}")
        return None
