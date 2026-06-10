import asyncio
from typing import Optional

from astrbot.api import logger


class PluginLlmMixin:
    """主插件的大语言模型调用包装能力。"""

    async def _call_llm_wrapper(
        self,
        prompt: str,
        system_prompt: str = None,
        timeout: int = 60,
        max_retries: int = 2,
        tools: list = None,
        umo: str = None,
    ) -> Optional[str]:
        """大语言模型调用包装器（支持失败重试与自动降级）"""
        if self._is_terminated:
            return None

        def _get_system_default_provider() -> str:
            # 如果没指定，默认使用第一个模型
            try:
                cfg = self.context.get_config()
                if cfg:
                    pid = cfg.get("provider_settings", {}).get("default_provider_id", "")
                    if pid:
                        return pid
                    for p in cfg.get("provider", []):
                        if p.get("enable", False) and "chat" in p.get("provider_type", "chat"):
                            return p.get("id")
            except Exception as e:
                logger.debug(f"[每日分享] 读取默认大语言模型服务提供商失败: {e}")
            return ""

        async def _get_session_provider(umo_value: str) -> str:
            if not umo_value:
                return ""
            try:
                getter = getattr(self.context, "get_current_chat_provider_id", None)
                if callable(getter):
                    return await getter(umo_value)
            except Exception as e:
                logger.debug(f"[每日分享] 读取会话大语言模型服务提供商失败: {e}")
            return ""

        configured_provider_id = str(self.llm_conf.get("llm_provider_id", "") or "").strip()
        session_provider_id = ""
        if not configured_provider_id:
            session_provider_id = await _get_session_provider(umo)
        primary_provider_id = configured_provider_id or session_provider_id or _get_system_default_provider()
        current_provider_id = primary_provider_id

        # 临时降级只保留一段时间，避免指定模型恢复后仍长期被跳过。
        now = asyncio.get_running_loop().time()
        if configured_provider_id and self._temp_fallback_provider:
            if now < self._temp_fallback_until:
                current_provider_id = self._temp_fallback_provider
            else:
                logger.info("[每日分享] 大语言模型临时降级已过期，恢复尝试指定模型。")
                self._temp_fallback_provider = None
                self._temp_fallback_until = 0.0
                current_provider_id = primary_provider_id

        try:
            config_timeout = int(self.llm_conf.get("llm_timeout", 60))
        except Exception:
            config_timeout = 60
        actual_timeout = max(int(timeout or 60), config_timeout)
        if tools:
            logger.debug("[每日分享] 当前 AstrBot 文本生成接口不支持工具名列表，已忽略工具参数。")
        if not current_provider_id:
            logger.error("[每日分享] 未找到可用的大语言模型服务提供商，无法生成内容。")
            return None

        for attempt in range(max_retries + 1):
            if self._is_terminated:
                return None

            # 降级逻辑 1
            is_last_attempt = attempt == max_retries
            if is_last_attempt and attempt > 0 and primary_provider_id and current_provider_id == primary_provider_id:
                default_pid = _get_system_default_provider()
                if default_pid and default_pid != current_provider_id:
                    logger.info(f"[每日分享] 指定大语言模型已达到重试次数，降级使用默认的第一个模型({default_pid})...")
                    current_provider_id = default_pid
                    if configured_provider_id:
                        self._temp_fallback_provider = default_pid
                        self._temp_fallback_until = asyncio.get_running_loop().time() + self._fallback_ttl_seconds

            try:
                kwargs = {"prompt": prompt}
                if system_prompt is not None and system_prompt != "":
                    kwargs["system_prompt"] = system_prompt
                if current_provider_id:
                    kwargs["chat_provider_id"] = current_provider_id

                resp = await asyncio.wait_for(
                    self.context.llm_generate(**kwargs),
                    timeout=actual_timeout,
                )

                if resp and hasattr(resp, "completion_text"):
                    result = resp.completion_text.strip()
                    if result:
                        return result

            except asyncio.TimeoutError:
                logger.warning(f"[每日分享] 大语言模型请求超时 ({actual_timeout}s) (尝试 {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
            except Exception as e:
                err_str = str(e)
                if "PROHIBITED_CONTENT" in err_str or "blocked" in err_str:
                    logger.error(f"[每日分享] 内容被模型安全策略拦截 (敏感词): {prompt[:50]}...")
                    return None

                if "401" in err_str:
                    logger.error("[每日分享] 大语言模型调用失败，请检查密钥配置。")
                    # 降级逻辑 2
                    if attempt < max_retries and primary_provider_id and current_provider_id == primary_provider_id:
                        default_pid = _get_system_default_provider()
                        if default_pid and default_pid != current_provider_id:
                            logger.info(f"[每日分享] 遇到 401 错误，降级使用默认的第一个模型({default_pid})...")
                            current_provider_id = default_pid
                            if configured_provider_id:
                                self._temp_fallback_provider = default_pid
                                self._temp_fallback_until = asyncio.get_running_loop().time() + self._fallback_ttl_seconds
                            await asyncio.sleep(2)
                            continue
                        return None
                    return None

                logger.error(f"[每日分享] 大语言模型调用异常（第 {attempt + 1} 次尝试）: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue

        logger.error(f"[每日分享] 大语言模型调用失败（已重试 {max_retries} 次）")
        return None
