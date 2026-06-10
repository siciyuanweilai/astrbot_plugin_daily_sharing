import os
import re
from typing import Optional

from astrbot.api import logger


class ImageVideoMixin:
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
            logger.debug(f"[每日分享] 生成视频动态提示词失败，使用默认视频动态提示词: {e}")

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
            logger.debug(f"[每日分享] 生成视频声音提示词失败，使用默认视频声音提示词: {e}")

        return fallback

    async def generate_video_from_image(self, image_path: str, content: str, target_umo: str = None) -> Optional[str]:
        """图片转视频"""
        if not self.img_conf.get("enable_ai_video", False): return None
        
        try:
            if not os.path.exists(image_path): return None
            with open(image_path, "rb") as f: image_bytes = f.read()
            logger.info(f"[每日分享] 正在将配图转换为视频...")
            
            # 构建视频提示词（复用之前的图片描述，生成匹配的动态和声音设计）
            image_description = self._last_image_description or ""
            motion_prompt = await self._build_video_motion_prompt(image_description, content, target_umo=target_umo)
            sound_prompt = await self._build_video_sound_prompt(image_description, content, target_umo=target_umo)
            video_prompt = f"{image_description}, {motion_prompt}, {sound_prompt}"
            logger.info(f"[每日分享] 视频动态提取：动态: {motion_prompt[:180]}...")
            logger.info(f"[每日分享] 配音提示提取：声音: {sound_prompt[:180]}...")
            logger.info(f"[每日分享] 最终视频提示词: {video_prompt[:180]}...")

            provider = self.provider_manager.select_video_provider()
            if provider == "generic_plugin":
                return await self.provider_manager.generate_video_with_generic_plugin(
                    video_prompt,
                    image_path,
                    image_bytes,
                )
            if provider == "auto_scan":
                return await self.provider_manager.generate_video_with_auto_scan(
                    video_prompt,
                    image_path,
                    image_bytes,
                )

            self._ensure_plugin()
            if not self._aiimg_plugin: return None

            # 强制依赖新版后端注册表架构
            if not hasattr(self._aiimg_plugin, "registry"):
                logger.warning("[每日分享] 检测到 GiteeAIImage 插件不支持视频后端注册表，跳过视频生成")
                return None
            
            # 获取配置的视频提供商链
            if hasattr(self._aiimg_plugin, "_get_video_chain"):
                chain = self._aiimg_plugin._get_video_chain()
            else:
                logger.warning("[每日分享] 无法获取视频服务配置链")
                return None
            
            if not chain:
                logger.warning("[每日分享] 未配置视频服务提供商")
                return None
            
            # 取第一个可用的提供商标识。
            provider_id = chain[0]
            try:
                # 从注册表中获取后端服务并调用
                backend = self._aiimg_plugin.registry.get_video_backend(provider_id)
                return await backend.generate_video_url(prompt=video_prompt, image_bytes=image_bytes)
            except Exception as e:
                logger.error(f"[每日分享] 获取视频后端或生成失败: {e}")
                return None
                
        except Exception as e:
            logger.error(f"[每日分享] 视频生成流程异常: {e}")
            return None
