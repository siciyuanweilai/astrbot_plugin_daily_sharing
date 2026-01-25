import os
import random
from datetime import datetime
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
        
        self.img_conf = self.config.get("image_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})

    def _get_current_period(self) -> TimePeriod:
        hour = datetime.now().hour
        if 0 <= hour < 6: return TimePeriod.DAWN
        elif 6 <= hour < 9: return TimePeriod.MORNING
        elif 9 <= hour < 12: return TimePeriod.FORENOON
        elif 12 <= hour < 16: return TimePeriod.AFTERNOON
        elif 16 <= hour < 19: return TimePeriod.EVENING
        else: return TimePeriod.NIGHT

    def _ensure_plugin(self):
        """确保获取到绘图插件实例"""
        if not self._aiimg_plugin and not self._aiimg_plugin_not_found:
            for p in self.context.get_all_stars():
                if p.name == "astrbot_plugin_gitee_aiimg":
                    self._aiimg_plugin = p.star_cls
                    break
            if not self._aiimg_plugin: 
                self._aiimg_plugin_not_found = True

    # ==================== 主入口 ====================
    async def generate_image(self, content: str, sharing_type: SharingType, life_context: str = None) -> Optional[str]:
        if not self.img_conf.get("enable_ai_image", False): return None

        # 检测是否涉及自己
        involves_self = await self._check_involves_self(content, sharing_type)
        
        # 提取穿搭 (仅当涉及自己且有上下文时)
        outfit_info = None
        if involves_self and life_context:
            outfit_info = await self._extract_outfit(life_context)
            if outfit_info:
                logger.debug(f"[DailySharing] 使用智能提取的穿搭: {outfit_info}")

        # 生成 Prompt (传入 life_context)
        prompt = await self._generate_image_prompt(content, sharing_type, involves_self, outfit_info, life_context)
        if not prompt: 
            logger.warning("[DailySharing] 提示词生成失败")
            return None

        logger.info(f"[DailySharing] 配图提示词: {prompt[:100]}...")
        
        # 直接使用 Prompt 作为记忆描述
        self._last_image_description = prompt[:200]

        # 调用画图插件
        return await self._call_aiimg(prompt)
    
    async def generate_video_from_image(self, image_path: str, content: str) -> Optional[str]:
        """使用生成的图片生成视频 (Grok)"""
        # 检查是否开启视频
        if not self.img_conf.get("enable_ai_video", False): return None
        
        self._ensure_plugin()
        if not self._aiimg_plugin:
            logger.warning("[DailySharing] 未找到Gitee插件，无法生成视频")
            return None
        
        if not hasattr(self._aiimg_plugin, "video"):
            logger.warning("[DailySharing] Gitee插件版本过低或未启用视频功能")
            return None

        try:
            # 读取图片二进制
            if not os.path.exists(image_path):
                return None
                
            with open(image_path, "rb") as f:
                image_bytes = f.read()

            # 构建视频提示词 (简单描述动效即可，Grok会自动理解图片)
            # 复用之前的图片描述，加上动效词
            video_prompt = f"{self._last_image_description}, cinematic motion, slow pan, high quality video"
            
            logger.info(f"[DailySharing] 正在将配图转换为视频...")
            video_url = await self._aiimg_plugin.video.generate_video_url(
                prompt=video_prompt,
                image_bytes=image_bytes
            )
            return video_url

        except Exception as e:
            logger.error(f"[DailySharing] 视频生成失败: {e}")
            return None

    def get_last_description(self) -> Optional[str]:
        d = self._last_image_description
        return d

    # ==================== 智能判断逻辑 ====================
    async def _check_involves_self(self, content: str, sharing_type: SharingType) -> bool:
        """检测内容是否涉及'自己'"""
        # 配置强制模式
        if self.img_conf.get("image_always_include_self", False):
            return True
        if self.img_conf.get("image_never_include_self", False):
            return False

        # LLM 智能判断
        try:
            type_hint = ""
            if sharing_type == SharingType.GREETING: type_hint = "(提示：问候通常需要人物出镜)"
            elif sharing_type == SharingType.NEWS: type_hint = "(提示：新闻通常画具体事件或物体，不画人)"

            system_prompt = f"""你是一个AI绘画构图顾问。
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

            user_prompt = f"分享类型：{sharing_type.value} {type_hint}\n内容：{content}\n\n画面是否包含人物？"
            
            # 快速判断，超时短
            res = await self.call_llm(user_prompt, system_prompt, timeout=10)
            if res:
                result = res.strip().upper()
                if "YES" in result: return True
                if "NO" in result: return False
        except Exception as e:
            logger.warning(f"[DailySharing] 智能判断出镜失败: {e}")
            
        return False

    # ==================== 穿搭与外貌 ====================
    async def _extract_outfit(self, life_ctx: str) -> Optional[str]:
        """从生活上下文提取穿搭"""
        period = self._get_current_period()
        is_night = period in [TimePeriod.NIGHT, TimePeriod.DAWN]
        
        # 定义时间段约束，防止晚上提取到白天的衣服
        time_constraint = "【深夜/休息模式】：忽略白天外出服装，仅提取上衣、下装、袜子、睡衣或家居服。" if is_night else "【白天/活动模式】：提取外出的日常穿搭，忽略睡衣。"

        prompt = f"""
你是一个 AI 绘图提示词优化专家。你的任务是将用户的【日记式穿搭文本】转化为【AI 视觉提示词】。
【当前时间约束】：{time_constraint}
【待处理文本】：
{life_ctx}
请严格遵守以下清洗规则：
1. 【提取视觉元素】：提取发型、衣物（外套、上衣、下装）、配饰（包、发卡、耳饰、鞋袜）。
2. 【明确穿搭层次（核心）】：
   - 若文本同时包含【外套】和【内搭】，必须明确描述层次关系。
   - 推荐格式：使用 '穿着xxx外套，敞开露出内搭xxx' 或 '外穿xxx，内搭xxx'。
   - 严禁将外套和内搭简单并列，防止画面材质混淆（例如不要说：'香芋紫毛衣，棒球服'，要说：'棒球服外套，内搭香芋紫毛衣'）。
3. 【保留关键细节】：保留物体的数量（如'双'马尾）、颜色、材质（如'马海毛'、'丝绒'）和形状。
4. 【去除噪音】：删除情绪描写（如'心情好'）、天气原因。
5. 【禁止比喻】：删除比喻句（如'像路人甲'），只保留物体本身的视觉特征。
6. 【保留鞋袜】：在此阶段保留所有鞋子和袜子的描述（构图剪裁将在后续步骤处理）。
7. 【输出格式】：直接输出清洗后的中文视觉描述字符串，用逗号分隔，不要任何解释。
请输出视觉提示词："""
        
        res = await self.call_llm(prompt, timeout=30)
        if res:
            return res.replace("Output:", "").replace("Prompt:", "").strip()
        return None

    async def _smart_filter_outfit(self, outfit: str, scene_context: str) -> str:
        """根据构图过滤鞋袜"""
        if not outfit: return ""
        
        system_prompt = (
            "你是一个 AI 绘画提示词专家。"
            "任务：根据用户的【画面描述】，决定是否在【穿搭】中保留鞋子/靴子/袜子。"
            "目标：防止生成图片时出现“断脚”、“鞋子切一半”或“画面底部强行塞入鞋子”的构图崩坏。"
            "严格执行以下规则："
            "1. 【保留规则】：只有当画面描述中**明确包含**“全身”、“Full body”、“从头到脚”、“展示鞋子”这些强调全身构图的词汇时，才允许【保留】鞋袜描述。"
            "2. 【删除规则】：如果画面描述只是模糊的“站立”、“走在街上”、“坐在...”，但**没有**明确写“全身”，默认 AI 可能会生成七分身（膝盖以上）。此时必须【删除】所有鞋子、靴子、袜子的描述，确保画面自然截断。"
            "3. 【删除规则】：如果是“半身”、“特写”、“自拍”、“上半身”，必须【删除】鞋袜描述。"
            "4. 仅输出修改后的穿搭字符串，不要包含任何解释。"
        )

        user_prompt = f"当前穿搭：{outfit}\n画面描述：{scene_context}\n\n请输出优化后的穿搭："
        
        res = await self.call_llm(user_prompt, system_prompt, timeout=20)
        return res.strip().strip(".").strip() if res else outfit

    async def _get_appearance_keywords(self) -> str:
        """获取人设外貌"""
        # 配置优先
        conf_p = self.img_conf.get("appearance_prompt", "").strip()
        if conf_p: return conf_p

        # 从人设提取
        try:
            pid = self.llm_conf.get("persona_id", "")
            p_text = ""
            
            if pid: 
                persona = await self.context.persona_manager.get_persona(pid)
                p_text = persona.system_prompt if persona else ""
            else:
                # 获取默认人设
                p_obj = await self.context.persona_manager.get_default_persona_v3()
                p_text = p_obj.get("prompt", "") if p_obj else ""
            
            if not p_text or len(p_text) < 10: return ""

            prompt = f"""请从以下人设描述中提取外貌特征，并转换为中文的图片生成提示词。
人设描述：
{p_text}
要求：
1. 【重要】必须包含人种/国籍描述
2. 提取外貌细节（发型、发色、眼睛、肤色、体型、常穿衣服等）
3. 转换为简短的中文关键词，用逗号分隔
4. 适合用于 AI 绘画
5. 不要包含性格、职业等非外貌信息
6. 直接输出中文关键词，不要解释
请输出："""
            
            res = await self.call_llm(prompt, timeout=30)
            if res:
                return res.replace("```", "").replace("\n", ", ").strip()
            return ""
        except: return ""

    # ==================== Prompt 生成核心 ====================
    async def _generate_image_prompt(self, content, stype, involves_self, outfit, life_context=None) -> str:
        # 传递 life_context
        scene_prompt = await self._generate_scene_prompt(content, stype, involves_self, outfit, life_context)
        if not scene_prompt: return ""
        
        final_prompt = scene_prompt
        # 叠加外貌
        if involves_self:
            appearance = await self._get_appearance_keywords()
            if appearance: final_prompt = f"{appearance}, {final_prompt}"

        # 强制注入环境修正词
        period = self._get_current_period()
        time_enforcement = ""
        
        if period in [TimePeriod.NIGHT, TimePeriod.DAWN]:
            # 夜晚强制词
            time_enforcement = ", 夜晚, 午夜, 深色天空, 幽暗环境, 城市夜景" 
        elif period == TimePeriod.EVENING:
            time_enforcement = ", 日落, 黄昏, 金色光照"
        else:
            time_enforcement = ", 白天, 日光, 晴朗, 明亮"
            
        # 将强制词加到最后，权重通常较高
        final_prompt = f"{final_prompt}{time_enforcement}"            
        
        # 叠加质量词
        quality_tags = "高质量, 杰作, 高分辨率, 细节丰富, 色彩鲜艳"
        return f"{final_prompt}, {quality_tags}"

    async def _generate_scene_prompt(self, content, sharing_type, involves_self, outfit_info, life_context=None) -> str:
        period = self._get_current_period()
        
        # === 光影逻辑与环境 ===
        if period in [TimePeriod.NIGHT, TimePeriod.DAWN]:
            time_context = "夜晚"
            light_vibe = "昏暗的光线, 电影感布光, 舒适的氛围, 深色调, 室内人造光, 室外城市灯光"
            negative_constraint = "不要阳光, 不要蓝天, 不要明亮的白天景色, 画面整体不要太亮"
        elif period == TimePeriod.EVENING:
            time_context = "傍晚"
            light_vibe = "温暖的金色光线, 日落氛围, 柔和的阴影"
            negative_constraint = "不要正午强光, 不要漆黑的夜晚"
        elif period == TimePeriod.MORNING:
            time_context = "早晨"
            light_vibe = "日出晨光, 柔和的朝阳, 清新的空气感, 丁达尔效应, 梦幻光影"
            negative_constraint = "不要正午顶光, 不要夜晚"    
        elif period in [TimePeriod.FORENOON, TimePeriod.AFTERNOON]:
            time_context = "白天"
            light_vibe = "明亮的日光, 晴朗的天空, 清晰的照明, 充满活力"
            negative_constraint = "不要夜景, 不要黄昏, 画面不要太暗"                    
        else:
            time_context = "白天"
            light_vibe = "自然光, 明亮, 柔和的日光, 清晰的照明"
            negative_constraint = "不要夜景, 不要星空, 不要黑暗的房间"

        # 构建生活状态描述，供LLM参考场景
        life_info_str = ""
        if life_context:
            life_info_str = f"\n【重要：当前生活状态/日程】\n{life_context}\n\n构图指示：如果【分享内容】没有明确提到地点，请务必根据【生活状态】来设定背景场景（例如：日程是'在咖啡馆'，背景就画咖啡馆）。"

        if involves_self:
            # ================= 画人模式 =================
            if sharing_type == SharingType.GREETING: comp_desc = "半身像, 面对镜头, 背景虚化"
            elif sharing_type == SharingType.MOOD: comp_desc = "特写, 脸部聚焦, 景深效果"
            elif sharing_type == SharingType.NEWS: comp_desc = "中景, 生活快照, 看手机或屏幕"
            elif sharing_type == SharingType.RECOMMENDATION: comp_desc = "中景, 展示物品, 手部特写, 聚焦物体"
            else: comp_desc = "中景, 自然姿态"

            outfit_constraint = ""
            if outfit_info:
                filtered = await self._smart_filter_outfit(outfit_info, comp_desc)
                outfit_constraint = f"\n\n【穿搭信息】\n原始穿搭：{outfit_info}\n过滤后穿搭：{filtered}\n请使用过滤后的穿搭生成提示词，必须准确描述发型数量（如双丸子头）和衣服特征。"

            system_prompt = f"""你是一个AI绘画提示词专家。
请根据用户的分享内容、当前时间段、以及生活状态，生成适合的场景、动作、穿搭描述。

【环境设定】
- 时间: {time_context}
- 光影: {light_vibe}
- 禁止: {negative_constraint}

【构图要求】(当前必须执行)
- {comp_desc}

要求：
1. 仅输出中文提示词，不要有任何解释
2. 描述人物的动作、姿态、表情
3. 描述场景、环境、氛围
4. 如果提供了穿搭信息，必须优先使用并详细转换为中文提示词。
5. **严禁省略数量词**：如果是“两个”或“双”，必须在提示词中体现（例如：双丸子头，双马尾）。
6. 如果提供了生活状态，请将人物放置在生活状态描述的场景中。
7. 提示词用逗号分隔，简洁明确
"""
            # 将 life_info_str 加入 Prompt
            user_prompt = f"""分享类型：{sharing_type.value}
分享内容：{content[:300]}{life_info_str}{outfit_constraint}

请生成人物场景中文提示词："""

        else:
            # ================= 画景模式 =================
            system_prompt = f"""你是一个AI绘画提示词专家。
请根据用户的分享内容、当前时间段，生成适合的纯场景描述。

【环境设定】
- 时间: {time_context}
- 光影: {light_vibe}
- 禁止: {negative_constraint}

要求：
1. 仅输出中文提示词，不要有任何解释
2. 描述场景、环境、氛围、主题
3. **不要包含人物描述** (无人物)
4. 如果提供了生活状态，请参考其中的地点信息来设定场景。
5. 提示词用逗号分隔，简洁明确
"""
            # 将 life_info_str 加入 Prompt
            user_prompt = f"""分享类型：{sharing_type.value}
分享内容：{content[:300]}{life_info_str}

请生成纯景物中文提示词："""
        
        res = await self.call_llm(user_prompt, system_prompt, timeout=30)
        
        # 清理输出
        if res:
            scene_prompt = res.strip().replace("\n", " ").replace("  ", " ")
            prefixes = ["输出：", "Output:", "提示词：", "Prompt:", "Keywords:", "提示词："]
            for prefix in prefixes:
                if scene_prompt.startswith(prefix):
                    scene_prompt = scene_prompt[len(prefix):].strip()
            return scene_prompt
            
        logger.warning("[DailySharing] 场景提示词生成失败（LLM异常或被拦截），取消配图")
        return ""

    async def _call_aiimg(self, prompt: str) -> Optional[str]:
        # 插件查找逻辑
        self._ensure_plugin()

        if self._aiimg_plugin:
            try: 
                target_size = self._aiimg_plugin.config.get("size", "1024x1024")
                path_obj = await self._aiimg_plugin.draw.generate(prompt=prompt, size=target_size)
                return str(path_obj)
                
            except Exception as e: 
                logger.error(f"[DailySharing] 生成图片出错: {e}")
                
        return None
