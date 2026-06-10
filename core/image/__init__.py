from datetime import datetime
from typing import Optional

from astrbot.api import logger

from ..config import SharingType, TimePeriod
from .aiimg import ImageAiimgMixin
from .prompt import ImageVisualMixin
from .providers import ImageProviderManager
from .video import ImageVideoMixin


class ImageService(ImageVisualMixin, ImageVideoMixin, ImageAiimgMixin):
    def __init__(self, context, config, llm_func):
        self.context = context
        self.config = config
        self.call_llm = llm_func
        self._aiimg_plugin = None
        self._aiimg_plugin_not_found = False
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
        elif 12 <= hour < 14: return TimePeriod.NOON
        elif 14 <= hour < 16: return TimePeriod.AFTERNOON
        elif 16 <= hour < 19: return TimePeriod.EVENING
        elif 19 <= hour < 22: return TimePeriod.NIGHT
        else: return TimePeriod.LATE_NIGHT

    def _ensure_plugin(self):
        """确保 Gitee 插件已加载"""
        self._aiimg_plugin = self.provider_manager.get_gitee_plugin()
        self._aiimg_plugin_not_found = not bool(self._aiimg_plugin)
        return self._aiimg_plugin

    async def generate_image(self, content: str, sharing_type: SharingType, life_context: str = None, target_umo: str = None) -> Optional[str]:
        """生成图片的入口函数"""
        self.reset_last_description()
        if not self.img_conf.get("enable_ai_image", False): return None

        # 1. 智能判断：是否画人
        involves_self = await self._check_involves_self(content, sharing_type, target_umo=target_umo)
        mode_str = "人物+场景" if involves_self else "纯静物/风景"
        is_text_priority = self.img_conf.get("priority_text_over_schedule", True)
        logic_str = "文案主导" if is_text_priority else "日程主导"        

        # 检测是否启用 Gitee 形象参考图逻辑。
        use_gitee_ref = self.img_conf.get("use_gitee_selfie_ref", False)
        is_selfie_mode = involves_self and use_gitee_ref
        
        logger.info(f"[每日分享] 配图决策: {mode_str} ({logic_str}) | 类型: {sharing_type.value} | 形象模式: {is_selfie_mode}")        
        
        # 3. 智能提取视觉元素
        visuals = {}
        if content or life_context:
            visuals = await self._agent_extract_visuals(content, life_context, target_umo=target_umo)
            
            # 如果大语言模型提取失败（返回空字典），则直接放弃配图
            if not visuals:
                logger.warning("[每日分享] 智能提取失败，已取消配图，仅发送文案")
                return None

            # 日志记录提取结果
            env = visuals.get('environment', '无')
            subj = visuals.get('subject', '无')
            outfit = (
                str(visuals.get("outfit", "") or "").strip() or "无"
                if involves_self
                else "不适用"
            )
            scene = visuals.get('scene_type', '未知')
            temp = visuals.get('temperature_feel', '未知')
            weather = visuals.get('weather_condition') or visuals.get('weather_vibe', '无')
            logger.info(
                f"[每日分享] 配图智能提取：主体: {subj} | 场景: {scene} | 环境: {env} | "
                f"天气: {weather} | 温感: {temp} | 穿搭: {outfit[:15]}..."
            )

        # 4. 组装最终提示词
        prompt = await self._assemble_final_prompt(content, sharing_type, involves_self, visuals, target_umo=target_umo)
        
        if not prompt: 
            logger.warning("[每日分享] 提示词组装失败，取消配图")
            return None
        logger.info(f"[每日分享] 最终配图提示词: {prompt[:100]}...")
        self._last_image_description = prompt
        
        # 5. 调用所选 provider 生成
        return await self._call_image_provider(
            prompt,
            use_ref_selfie=is_selfie_mode,
            target_umo=target_umo,
        )

    def get_last_description(self) -> Optional[str]:
        return self._last_image_description

    def reset_last_description(self):
        self._last_image_description = None
