from typing import List, Optional

from astrbot.api import logger


class ImageAiimgMixin:
    async def _get_gitee_reference_images(self) -> List[bytes]:
        """从 Gitee 插件中提取参考图"""
        gitee = self.provider_manager.get_gitee_plugin()
        if not gitee: return []
        
        try:
            # 1. 优先从网页配置读取
            if hasattr(gitee, "_get_config_selfie_reference_paths") and hasattr(gitee, "_read_paths_bytes"):
                ref_paths = gitee._get_config_selfie_reference_paths()
                if ref_paths:
                    return await gitee._read_paths_bytes(ref_paths)
            
            # 2. 其次尝试从参考图存储读取
            if hasattr(gitee, "refs"):
                # 尝试通用键
                ref_paths = await gitee.refs.get_paths("bot_selfie")
                if ref_paths and hasattr(gitee, "_read_paths_bytes"):
                    return await gitee._read_paths_bytes(ref_paths)
                    
        except Exception as e:
            logger.warning(f"[每日分享] 获取 Gitee 参考图失败: {e}")
        
        return []

    async def _call_image_provider(
        self,
        prompt: str,
        use_ref_selfie: bool = False,
        target_umo: str = None,
    ) -> Optional[str]:
        """调用配置的生图 provider。"""
        provider = self.provider_manager.select_provider()
        if provider == "generic_plugin":
            if use_ref_selfie:
                logger.info("[每日分享] 当前为通用生图 provider，尝试使用通用改图/参考图方法")
            return await self.provider_manager.generate_with_generic_plugin(
                prompt,
                use_ref_selfie=use_ref_selfie,
                target_umo=target_umo or "",
            )
        if provider == "calibrated_tool":
            if use_ref_selfie:
                logger.info("[每日分享] 当前为校准工具生图 provider，优先调用已校准自拍/参考图工具")
            return await self.provider_manager.generate_with_calibrated_tool(
                prompt,
                use_ref_selfie=use_ref_selfie,
                target_umo=target_umo or "",
            )

        return await self._call_aiimg(prompt, use_ref_selfie=use_ref_selfie)

    async def _call_aiimg(self, prompt: str, use_ref_selfie: bool = False) -> Optional[str]:
        """调用底层 Gitee 插件"""
        aiimg_plugin = self._ensure_plugin()
        if not aiimg_plugin:
            logger.error("[每日分享] 未找到 astrbot_plugin_gitee_aiimg 插件")
            return None

        try:
            # ================= 形象参考图逻辑 =================
            if use_ref_selfie and hasattr(aiimg_plugin, "edit"):
                logger.info("[每日分享] 正在使用 Gitee 形象参考图生成...")
                
                # 1. 获取参考图
                ref_images = await self._get_gitee_reference_images()
                if ref_images:
                    logger.info(f"[每日分享] 找到 {len(ref_images)} 张参考图，调用图生图接口")
                    
                    # 2. 构建提示词前缀
                    final_prompt = (
                        "请根据参考图生成一张新的生活照：\n"
                        "1) 以第1张参考图的人脸身份为准（仅人脸身份特征），保持五官/气质一致。\n"
                        "2) 如果还有其它参考图，请将它们仅作为服装/姿势/构图/场景的参考。\n"
                        f"3) 画面具体描述：{prompt}\n"
                        "4) 输出高质量生活照片风格，不要拼图，不要水印。"
                    )
                    
                    # 3. 调用改图接口
                    path_obj = await aiimg_plugin.edit.edit(
                        prompt=final_prompt,
                        images=ref_images,
                        backend=None,
                        task_types=["id", "background", "style"] 
                    )
                    return str(path_obj)
                else:
                    logger.warning("[每日分享] 虽开启形象模式，但未找到参考图，降级为文生图")

            # ================= 普通文生图逻辑 =================
            if hasattr(aiimg_plugin, "draw"):
                target_size = aiimg_plugin.config.get("size", "1024x1024")
                path_obj = await aiimg_plugin.draw.generate(prompt=prompt, size=target_size)
                return str(path_obj)
            else:
                 # 这种情况下通常意味着获取到的是类而非实例，或者插件异常
                 logger.error("[每日分享] Gitee 插件实例不完整，无法生成图片")
                 return None

        except Exception as e: 
            logger.error(f"[每日分享] 生成图片出错: {e}")
            return None
