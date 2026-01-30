import asyncio
import json
import random
import os
import re 
from functools import partial
from datetime import datetime
from pathlib import Path
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import Record, Video 
from .config import TimePeriod, SharingType, SHARING_TYPE_SEQUENCES, CRON_TEMPLATES, NEWS_SOURCE_MAP
from .core.news import NewsService
from .core.image import ImageService
from .core.content import ContentService
from .core.context import ContextService
from .core.db import DatabaseManager 

# 类型中文映射表
TYPE_CN_MAP = {
    "greeting": "问候",
    "news": "新闻",
    "mood": "心情",
    "knowledge": "知识",
    "recommendation": "推荐"
}

# 输入指令映射表
CMD_CN_MAP = {
    "问候": SharingType.GREETING,
    "新闻": SharingType.NEWS,
    "心情": SharingType.MOOD,
    "知识": SharingType.KNOWLEDGE,
    "推荐": SharingType.RECOMMENDATION
}

# 新闻源中文映射表
SOURCE_CN_MAP = {v['name']: k for k, v in NEWS_SOURCE_MAP.items()}
SOURCE_CN_MAP.update({
    "知乎": "zhihu", 
    "微博": "weibo", 
    "B站": "bili", 
    "小红书": "xiaohongshu", 
    "抖音": "douyin", 
    "头条": "toutiao", 
    "百度": "baidu", 
    "腾讯": "tencent"
})

@register("daily_sharing", "四次元未来", "定时主动分享所见所闻", "4.0.0")
class DailySharingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config 
        self.scheduler = AsyncIOScheduler()
        
        self.basic_conf = self.config.get("basic_conf", {})
        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.receiver_conf = self.config.get("receiver", {})
        
        # 分享内容记录条数 (用于内存缓存，固定100)
        self.history_limit = 100
        
        # 锁与防抖
        self._lock = asyncio.Lock()
        self._last_share_time = None
        
        # 生命周期标志位 (防止重载时旧实例复活)
        self._is_terminated = False
        
        # 缓存 Adapter ID 
        self._cached_adapter_id = None 

        # 任务追踪 (用于生命周期清理)
        self._bg_tasks = set()
        
        # 数据路径
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_daily_sharing")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置文件路径
        config_dir = self.data_dir.parent.parent / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = config_dir / "astrbot_plugin_daily_sharing_config.json"
        
        # 数据库初始化
        self.db = DatabaseManager(self.data_dir)
        
        # 初始化服务层
        self.ctx_service = ContextService(context, config)
        self.news_service = NewsService(config)
        self.image_service = ImageService(context, config, self._call_llm_wrapper)
        
        # 初始化内容服务
        self.content_service = ContentService(
            config, 
            self._call_llm_wrapper, 
            context,
            self.db, 
            self.news_service
        )
        
        # 启动延迟初始化 Bot 缓存的任务
        bot_init_task = asyncio.create_task(self._delayed_init_bots())
        self._bg_tasks.add(bot_init_task)
        bot_init_task.add_done_callback(self._bg_tasks.discard)

    async def initialize(self):
        """初始化插件"""
        task = asyncio.create_task(self._delayed_init())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def terminate(self):
        """插件卸载/重载时的清理逻辑"""
        self._is_terminated = True 
        try:
            # 1. 停止调度器
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
            
            # 2. 取消所有后台任务
            for task in self._bg_tasks:
                if not task.done():
                    task.cancel()
            
            logger.info("[DailySharing] 插件已停止，清理资源完成")
        except Exception as e:
            logger.error(f"[DailySharing] 停止插件出错: {e}")        

    async def _delayed_init(self):
        """延迟初始化逻辑 (调度器)"""
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            return 
        
        # 再次检查终止状态，防止僵尸实例启动调度器
        if self._is_terminated:
            return

        # 启动时清理一次过期数据
        try:
            days_limit = self.content_service.dedup_days
            await self.db.clean_expired_data(days_limit)
        except Exception:
            pass

        has_targets = False
        if self.receiver_conf:
            if self.receiver_conf.get("groups") or self.receiver_conf.get("users"):
                has_targets = True
        
        if not has_targets:
            logger.warning("[DailySharing] 未配置接收对象 (receiver)")

        if self.config.get("enable_auto_sharing", False):
            cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
            self._setup_cron(cron)
            
            # 只有在未终止且未运行的情况下才启动
            if not self._is_terminated and not self.scheduler.running:
                self.scheduler.start()
            logger.info("[DailySharing] 定时任务已启动")
        else:
            logger.info("[DailySharing] 自动分享已禁用")

    async def _delayed_init_bots(self):
        """延迟初始化 Bot 缓存"""
        try:
            # 等待 30 秒，确保 AstrBot 核心和适配器完全加载
            await asyncio.sleep(30)
            if self._is_terminated: return
            
            # 调用 ContextService 进行 Bot 扫描
            await self.ctx_service.init_bots()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[DailySharing] Bot 初始化任务出错: {e}")

    # ==================== 核心逻辑 (LLM调用与任务) ====================

    @filter.llm_tool(name="daily_share")
    async def daily_share_tool(
        self, 
        event: AstrMessageEvent, 
        share_type: str, 
        source: str = None, 
        get_image: bool = False,
        need_image: bool = False,
        need_video: bool = False,
        need_voice: bool = False
    ):
        """
        主动分享日常内容、新闻热搜、获取热搜图片等。
        当用户想要看新闻、热搜、早安晚安、冷知识、心情或推荐时调用此工具。

        Args:
            share_type(string): 分享类型。必须是以下之一：'问候', '新闻', '心情', '知识', '推荐'。
            source(string): 仅当 share_type 为'新闻'时有效。指定新闻平台。支持：微博, 知乎, B站, 抖音, 头条, 百度, 腾讯, 小红书。如果不指定则留空。
            get_image(boolean): 仅当 share_type 为'新闻'时有效。默认为 True (优先发送热搜长图)。只有当用户明确要求“文字版”、“文本”、“不要图片”或“写一段新闻”时，才将其设为 False。
            need_image(boolean): 是否需要AI为这段文案配图。默认为 False。仅当用户明确说“配图”、“带图”、“发张图”时，才将其设为 True。
            need_video(boolean): 是否需要AI为这段文案生成视频。默认为 False。仅当用户明确说“视频”、“动态图”、“动起来”时，才将其设为 True。
            need_voice(boolean): 是否需要将文案转为语音(TTS)发送。默认为 False。仅当用户明确提到“语音”、“朗读”、“念给我听”时，设为 True。
        """
        if self._is_terminated: return ""

        # 1. 防抖检查
        if self._lock.locked():
            await event.send(event.plain_result("正如火如荼地准备中，请稍后..."))
            return ""

        # 2. 启动后台异步任务
        task = asyncio.create_task(
            self._async_daily_share_task(
                event, share_type, source, get_image, need_image, need_video, need_voice
            )
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

        # 3. 直接返回空字符串，让 LLM 闭嘴，不再生成回复
        return ""

    async def _async_daily_share_task(
        self,
        event: AstrMessageEvent,
        share_type: str,
        source: str,
        get_image: bool,
        need_image: bool,
        need_video: bool,
        need_voice: bool
    ):
        """实际执行分享逻辑的后台任务"""
        try:
            # 参数清洗与映射
            target_type_enum = None
            
            # 映射分享类型 (中文 -> 枚举)
            if share_type in CMD_CN_MAP:
                target_type_enum = CMD_CN_MAP[share_type]
            else:
                # 模糊匹配尝试
                for k, v in CMD_CN_MAP.items():
                    if k in share_type:
                        target_type_enum = v
                        break
                if not target_type_enum:
                    # 错误提示直接发给用户
                    await event.send(event.plain_result(f"不支持的分享类型：{share_type}。支持：问候, 新闻, 心情, 知识, 推荐。"))
                    return

            # 映射新闻源 (中文 -> key)
            news_src_key = None
            if target_type_enum == SharingType.NEWS and source:
                if source in SOURCE_CN_MAP:
                    news_src_key = SOURCE_CN_MAP[source]
                elif source in NEWS_SOURCE_MAP:
                    news_src_key = source
                else:
                    for name, key in SOURCE_CN_MAP.items():
                        if name in source or source in name:
                            news_src_key = key
                            break
            
            # 场景 A: 获取新闻长图 (直接发送图片)
            if target_type_enum == SharingType.NEWS and get_image:
                if not news_src_key:
                    news_src_key = self.news_service.select_news_source()
                
                try:
                    img_url, src_name = self.news_service.get_hot_news_image_url(news_src_key)
                    # 发送图片
                    await event.send(event.image_result(img_url))
                except Exception as e:
                    logger.error(f"[DailySharing] 获取新闻图片失败: {e}")
                    await event.send(event.plain_result(f"获取新闻长图失败，请稍后再试。"))
                return

            # 场景 B: 标准流程 (生成文案 + 可选配图/视频 + 可选语音)
            else:
                # 获取上下文 ID
                uid = event.get_sender_id()
                if not ":" in str(uid):
                    target_umo = event.unified_msg_origin
                else:
                    target_umo = uid

                # 重新计算时段
                period = self._get_curr_period()
                
                # 准备数据
                life_ctx = await self.ctx_service.get_life_context()
                news_data = None
                if target_type_enum == SharingType.NEWS:
                    news_data = await self.news_service.get_hot_news(news_src_key)

                # 获取历史
                is_group = self.ctx_service._is_group_chat(target_umo)
                hist_data = await self.ctx_service.get_history_data(target_umo, is_group)
                hist_prompt = self.ctx_service.format_history_prompt(hist_data, target_type_enum)
                group_info = hist_data.get("group_info")
                life_prompt = self.ctx_service.format_life_context(life_ctx, target_type_enum, is_group, group_info)
                
                # 生成内容
                content = await self.content_service.generate(
                    target_type_enum, period, target_umo, is_group, life_prompt, hist_prompt, news_data
                )
                
                if not content:
                    await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                    return
                
                # ================= 视觉生成逻辑 (图片/视频) =================
                img_path = None
                video_url = None
                
                # 判断是否生成: 只要总开关开启，且(用户明确要求，或类型在允许列表中)
                enable_global = self.image_conf.get("enable_ai_image", False)
                
                should_gen_visual = False
                if enable_global:
                    if need_image or need_video:
                        # 1. 用户明确要求：强制生成 (无视类型白名单)
                        should_gen_visual = True
                    else:
                        # 2. 用户未要求：检查类型白名单
                        allowed = self.image_conf.get("image_enabled_types", [])
                        if target_type_enum.value in allowed:
                            should_gen_visual = True

                if should_gen_visual:
                    # 生成图片
                    img_path = await self.image_service.generate_image(content, target_type_enum, life_ctx)
                    
                    # 生成视频 (如果明确要求视频，或类型在视频白名单中)
                    if img_path and self.image_conf.get("enable_ai_video", False):
                        should_gen_video = False
                        if need_video:
                            should_gen_video = True
                        else:
                            video_allowed = self.image_conf.get("video_enabled_types", [])
                            if target_type_enum.value in video_allowed:
                                should_gen_video = True
                                
                        if should_gen_video:
                            video_url = await self.image_service.generate_video_from_image(img_path, content)

                # ================= 语音生成逻辑 (TTS) =================
                audio_path = None
                if self.tts_conf.get("enable_tts", False):
                    should_gen_voice = False
                    if need_voice:
                        # 1. 用户明确要求：强制生成
                        should_gen_voice = True
                    else:
                        # 2. 检查白名单
                        tts_allowed = self.tts_conf.get("tts_enabled_types", [])
                        if target_type_enum.value in tts_allowed:
                            should_gen_voice = True
                            
                    if should_gen_voice:
                        audio_path = await self.ctx_service.text_to_speech(content, target_umo, target_type_enum, period)

                # 发送
                await self._send(target_umo, content, img_path, audio_path, video_url)
                
                # 记录上下文
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_bot_reply_to_history(target_umo, content, image_desc=img_desc)
                await self.ctx_service.record_to_memos(target_umo, content, img_desc)
                

        except Exception as e:
            logger.error(f"[DailySharing] 异步任务错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"执行出错: {str(e)}"))

    async def _call_llm_wrapper(self, prompt: str, system_prompt: str = None, timeout: int = 60, max_retries: int = 2) -> Optional[str]:
        """LLM 调用包装器"""
        if self._is_terminated: return None
        
        provider_id = self.llm_conf.get("llm_provider_id", "")
        
        # 自动探测 Provider 
        if not provider_id:
            try:
                cfg = self.context.get_config()
                if cfg:
                    provider_id = cfg.get("provider_settings", {}).get("default_provider_id", "")
                    if not provider_id:
                        for p in cfg.get("provider", []):
                            if p.get("enable", False) and "chat" in p.get("provider_type", "chat"):
                                provider_id = p.get("id")
                                break
            except Exception:
                pass

        config_timeout = self.llm_conf.get("llm_timeout", 60)
        actual_timeout = max(timeout, config_timeout)

        for attempt in range(max_retries + 1):
            if self._is_terminated: return None
            try:
                resp = await asyncio.wait_for(
                    self.context.llm_generate(
                        prompt=prompt, 
                        system_prompt=system_prompt, 
                        chat_provider_id=provider_id if provider_id else None
                    ),
                    timeout=actual_timeout
                )
                
                if resp and hasattr(resp, 'completion_text'):
                    result = resp.completion_text.strip()
                    if result:
                        return result
                    
            except asyncio.TimeoutError:
                logger.warning(f"[DailySharing] LLM超时 ({actual_timeout}s) (尝试 {attempt+1}/{max_retries+1})")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
            except Exception as e:
                err_str = str(e)
                if "PROHIBITED_CONTENT" in err_str or "blocked" in err_str:
                    logger.error(f"[DailySharing] 内容被模型安全策略拦截 (敏感词): {prompt[:50]}...")
                    return None 

                if "401" in str(e):
                    logger.error(f"[DailySharing] LLM 失败。请检查 API Key。")
                    return None
                
                logger.error(f"[DailySharing] LLM异常 (尝试 {attempt+1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue

        logger.error(f"[DailySharing] LLM调用失败（已重试{max_retries}次）")
        return None

    def _setup_cron(self, cron_str):
        """设置 Cron 任务"""
        if self._is_terminated: return
        
        try:
            if self.scheduler.get_job("auto_share"):
                self.scheduler.remove_job("auto_share")

            actual_cron = CRON_TEMPLATES.get(cron_str, cron_str)
            parts = actual_cron.split()
            
            if len(parts) == 5:
                self.scheduler.add_job(
                    self._task_wrapper, 'cron',
                    minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
                    id="auto_share",
                    replace_existing=True,
                    max_instances=1  
                )
                logger.debug(f"[DailySharing] 定时任务已设定: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 无效的 Cron 表达式: {cron_str}")
        except Exception as e:
            logger.error(f"[DailySharing] 设置 Cron 失败: {e}")

    async def _task_wrapper(self):
        """任务包装器（防抖 + 锁 + 随机延迟 + 数据清理）"""
        if self._is_terminated: return
        
        task = asyncio.current_task()
        self._bg_tasks.add(task)
        
        try:
            # 执行数据库自动清理
            try:
                days_limit = self.content_service.dedup_days
                await self.db.clean_expired_data(days_limit)
            except Exception as e:
                logger.warning(f"[DailySharing] 数据库清理失败: {e}")

            # 随机延迟逻辑
            try:
                # 从配置获取随机延迟分钟数，默认为 0
                random_delay_min = int(self.basic_conf.get("cron_random_delay", 0))
            except Exception:
                random_delay_min = 0

            # 1. 延迟逻辑移动到锁外，避免长时间占用锁导致交互阻塞
            if random_delay_min > 0:
                # 计算延迟秒数 (0 到 max*60)
                delay_seconds = random.randint(0, random_delay_min * 60)
                if delay_seconds > 0:
                    trigger_time = datetime.now()
                    expected_time = trigger_time.timestamp() + delay_seconds
                    time_str = datetime.fromtimestamp(expected_time).strftime('%H:%M:%S')
                    
                    logger.info(f"[DailySharing] 定时任务已触发，启用随机延迟策略。")
                    logger.info(f"[DailySharing] 将延迟 {delay_seconds/60:.1f} 分钟，预计于 {time_str} 执行...")
                    
                    try:
                        await asyncio.sleep(delay_seconds)
                    except asyncio.CancelledError:
                        return

            if self._is_terminated: return

            # 核心执行逻辑
            now = datetime.now()
            
            # 防抖检查
            if self._last_share_time:
                if (now - self._last_share_time).total_seconds() < 60:
                    logger.info("[DailySharing] 检测到近期已执行任务，跳过本次定时触发。")
                    return
            
            if self._lock.locked():
                logger.warning("[DailySharing] 上一个任务正在进行中，跳过本次触发。")
                return

            async with self._lock:
                self._last_share_time = now
                if random_delay_min > 0:
                    logger.info("[DailySharing] 随机延迟结束，开始执行分享...")
                await self._execute_share()
                
        finally:
            self._bg_tasks.discard(task)

    async def _execute_share(self, force_type: SharingType = None, news_source: str = None, specific_target: str = None):
        """执行分享的主流程"""
        if self._is_terminated: return

        period = self._get_curr_period()
        if force_type:
            stype = force_type
        else:
            stype = await self._decide_type_with_state(period) 
        
        logger.info(f"[DailySharing] 时段: {period.value}, 类型: {stype.value}")

        life_ctx = await self.ctx_service.get_life_context()
        news_data = None
        
        # 加载状态以获取上次的新闻源
        state = await self.db.get_state("global", {})
        last_news_source = state.get("last_news_source")

        if stype == SharingType.NEWS:
            # 如果没有指定源（自动选择模式），则传入 last_news_source 进行去重
            if not news_source:
                news_source = self.news_service.select_news_source(excluded_source=last_news_source)
            
            news_data = await self.news_service.get_hot_news(news_source)
            
            # 如果获取成功，更新状态中的 last_news_source
            if news_data:
                actual_source = news_data[1]
                await self.db.update_state_dict("global", {"last_news_source": actual_source})

        targets = []
        
        # 1. 确定发送目标
        if specific_target:
            targets.append(specific_target)
        else:
            if self.receiver_conf:
                # 尝试获取 Adapter ID
                default_adapter_id = self._cached_adapter_id
                
                # 1. 从上下文获取平台管理器，找到第一个有 ID 的平台实例
                if not default_adapter_id:
                    try:
                        if hasattr(self.context, "platform_manager"):
                            insts = self.context.platform_manager.get_insts()
                            for inst in insts:
                                if hasattr(inst, "metadata") and inst.metadata.id:
                                    default_adapter_id = inst.metadata.id
                                    self._cached_adapter_id = default_adapter_id
                                    logger.info(f"[DailySharing] 自动发现并缓存 Adapter ID: {default_adapter_id}")
                                    break
                    except Exception as e:
                        logger.warning(f"[DailySharing] 尝试自动发现 Bot ID 失败: {e}")

                # 2. 如果还是没找到，才使用默认值兜底
                if not default_adapter_id:
                     default_adapter_id = "aiocqhttp"
                     logger.warning("[DailySharing] 尚未缓存 Adapter ID，使用默认值 'aiocqhttp'。")

                if default_adapter_id:
                    for gid in self.receiver_conf.get("groups", []):
                        if gid: targets.append(f"{default_adapter_id}:GroupMessage:{gid}")
                    for uid in self.receiver_conf.get("users", []):
                        if uid: targets.append(f"{default_adapter_id}:FriendMessage:{uid}")

        if not targets:
            logger.warning("[DailySharing] 未配置接收对象，且未指定目标，请在配置页填写群号或QQ号")
            return

        for uid in targets:
            if self._is_terminated: break
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower() or "guild" in uid.lower()
                
                hist_data = await self.ctx_service.get_history_data(uid, is_group)
                if is_group and "group_info" in hist_data:
                    # 手动触发时通常忽略策略检查，但自动触发时需要检查
                    if not specific_target and not self.ctx_service.check_group_strategy(hist_data["group_info"]):
                        logger.info(f"[DailySharing] 因策略跳过群组 {uid}")
                        continue

                hist_prompt = self.ctx_service.format_history_prompt(hist_data, stype)
                group_info = hist_data.get("group_info")
                life_prompt = self.ctx_service.format_life_context(life_ctx, stype, is_group, group_info)

                logger.info(f"[DailySharing] 正在为 {uid} 生成内容...")
                content = await self.content_service.generate(
                    stype, period, uid, is_group, life_prompt, hist_prompt, news_data
                )
                
                if not content:
                    logger.warning(f"[DailySharing] 内容生成失败 {uid}")
                    await self.db.add_sent_history(
                        target_id=uid,
                        sharing_type=stype.value,
                        content="生成失败 (LLM无响应)",
                        success=False
                    )
                    continue
                
                # 生成多媒体素材 (图片 & 视频 & 语音) 
                
                # 1. 配图生成逻辑
                img_path = None
                video_url = None
                enable_img_global = self.image_conf.get("enable_ai_image", False)
                img_allowed_types = self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
                
                if enable_img_global:
                    if stype.value in img_allowed_types:
                        img_path = await self.image_service.generate_image(content, stype, life_ctx)
                        # 尝试生成视频
                        if img_path and self.image_conf.get("enable_ai_video", False):
                            video_allowed = self.image_conf.get("video_enabled_types", ["greeting", "mood"])
                            if stype.value in video_allowed:
                                video_url = await self.image_service.generate_video_from_image(img_path, content)
                    else:
                         logger.info(f"[DailySharing] 当前类型 {stype.value} 不在配图允许列表，跳过作图。")

                # 2. 语音生成逻辑
                audio_path = None
                enable_tts_global = self.tts_conf.get("enable_tts", False)
                tts_allowed_types = self.tts_conf.get("tts_enabled_types", ["greeting", "mood"])
                
                if enable_tts_global:
                    if stype.value in tts_allowed_types:
                        # 传入 stype 和 period 以确定情感
                        audio_path = await self.ctx_service.text_to_speech(content, uid, stype, period)
                    else:
                        logger.info(f"[DailySharing] 当前类型 {stype.value} 不在语音允许列表，跳过 TTS。")

                # 发送消息
                await self._send(uid, content, img_path, audio_path, video_url)
                
                # 获取图片描述并写入 AstrBot 聊天上下文
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_bot_reply_to_history(uid, content, image_desc=img_desc)

                # 记录与历史
                await self.ctx_service.record_to_memos(uid, content, img_desc)

                # 清洗历史记录内容中的情感标签
                clean_content_for_log = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()

                await self.db.add_sent_history(
                    target_id=uid,
                    sharing_type=stype.value,
                    content=clean_content_for_log[:100] + "...",
                    success=True
                )
                
                await asyncio.sleep(2) 

            except Exception as e:
                logger.error(f"[DailySharing] 处理 {uid} 时出错: {e}")
                import traceback
                logger.error(traceback.format_exc())

    async def _send(self, uid, text, img_path, audio_path=None, video_url=None):
        """发送消息（支持分开发送，支持语音和视频）"""
        if self._is_terminated: return

        try:
            separate_img = self.image_conf.get("separate_text_and_image", True)
            prefer_audio_only = self.tts_conf.get("prefer_audio_only", False)
            
            # 清洗情感标签
            clean_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', text, flags=re.IGNORECASE).strip()
            
            # 判断是否应该发送文字
            # 如果有语音，且开启了“仅发语音”，则不发文字
            should_send_text = True
            if audio_path and prefer_audio_only:
                should_send_text = False

            # 1. 发送文字（如果需要）
            if should_send_text and clean_text: 
                text_chain = MessageChain().message(clean_text) 
                # 如果图片不分开发送，且没有语音，且没有视频（视频无法合并），则合并图片
                if img_path and not video_url and not separate_img and not audio_path:
                    if img_path.startswith("http"): text_chain.url_image(img_path)
                    else: text_chain.file_image(img_path)
                
                await self.context.send_message(uid, text_chain)
                
                # 如果后续还有消息，进行随机延迟
                if audio_path or ((img_path or video_url) and separate_img):
                    await self._random_sleep()

            # 2. 发送语音（如果有）
            if audio_path:
                audio_chain = MessageChain()
                audio_chain.chain.append(Record(file=audio_path))
                await self.context.send_message(uid, audio_chain)
                
                # 如果后续还有视觉媒体，延迟
                if (img_path or video_url) and separate_img:
                    await self._random_sleep()
            
            # 3. 发送视觉媒体（视频优先，其次图片）
            if video_url:
                # 发送视频
                video_chain = MessageChain()
                # 判断是本地文件还是网络URL
                if video_url.startswith("http"):
                    video_chain.chain.append(Video.fromURL(video_url))
                else:
                    # 如果是本地路径，使用 fromFile
                    video_chain.chain.append(Video.fromFileSystem(video_url))              
                await self.context.send_message(uid, video_chain)
            elif img_path:
                # 发送图片（如果视频没生成，或者视频关闭）
                # 逻辑：只要图片还没发（separate_img 为真，或者虽然 separate_img 为假但因为有语音没能合并），就发
                img_not_sent_yet = separate_img or audio_path
                if img_not_sent_yet:
                    img_chain = MessageChain()
                    if img_path.startswith("http"): img_chain.url_image(img_path)
                    else: img_chain.file_image(img_path)
                    await self.context.send_message(uid, img_chain)

        except Exception as e:
            logger.error(f"[DailySharing] 发送消息给 {uid} 失败: {e}")

    async def _random_sleep(self):
        """随机延迟"""
        if self._is_terminated: return

        delay_str = self.image_conf.get("separate_send_delay", "1.0-2.0")
        try:
            if "-" in str(delay_str):
                d_min, d_max = map(float, str(delay_str).split("-"))
                await asyncio.sleep(random.uniform(d_min, d_max))
            else:
                await asyncio.sleep(float(delay_str))
        except:
            await asyncio.sleep(1.5)

    # ==================== 状态管理 ====================

    def _get_curr_period(self) -> TimePeriod:
        h = datetime.now().hour
        if 0 <= h < 6: return TimePeriod.DAWN
        if 6 <= h < 9: return TimePeriod.MORNING
        if 9 <= h < 12: return TimePeriod.FORENOON
        if 12 <= h < 16: return TimePeriod.AFTERNOON
        if 16 <= h < 19: return TimePeriod.EVENING
        if 19 <= h < 22: return TimePeriod.NIGHT
        return TimePeriod.LATE_NIGHT

    def _get_period_range_str(self, period: TimePeriod) -> str:
        """获取时段对应的时间范围字符串"""
        return {
            TimePeriod.DAWN: "00:00-06:00",            
            TimePeriod.MORNING: "06:00-09:00",
            TimePeriod.FORENOON: "09:00-12:00",
            TimePeriod.AFTERNOON: "12:00-16:00",
            TimePeriod.EVENING: "16:00-19:00",
            TimePeriod.NIGHT: "19:00-22:00",
            TimePeriod.LATE_NIGHT: "22:00-24:00"
        }.get(period, "")

    @staticmethod
    def _write_json_sync(path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def _save_config_file(self):
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.config_file, self.config)
        except Exception as e:
            logger.error(f"[DailySharing] 保存配置失败: {e}")

    async def _decide_type_with_state(self, current_period: TimePeriod) -> SharingType:
        conf_type = self.basic_conf.get("sharing_type", "auto")
        if conf_type != "auto":
            try: return SharingType(conf_type)
            except: pass
        
        state = await self.db.get_state("global", {})
        
        if state.get("last_period") != current_period.value:
            state["sequence_index"] = 0
        
        config_key_map = {
            TimePeriod.MORNING: "morning_sequence",
            TimePeriod.FORENOON: "forenoon_sequence",
            TimePeriod.AFTERNOON: "afternoon_sequence",
            TimePeriod.EVENING: "evening_sequence",
            TimePeriod.NIGHT: "night_sequence",
            TimePeriod.LATE_NIGHT: "late_night_sequence",
            TimePeriod.DAWN: "dawn_sequence"
        }
        
        config_key = config_key_map.get(current_period)
        seq = self.basic_conf.get(config_key, [])
        
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])
        
        idx = state.get("sequence_index", 0)
        if idx >= len(seq): idx = 0
        
        selected = seq[idx]
        
        updates = {
            "last_period": current_period.value,
            "sequence_index": (idx + 1) % len(seq),
            "last_timestamp": datetime.now().isoformat(),
            "last_type": selected
        }
        await self.db.update_state_dict("global", updates)
        
        try: return SharingType(selected)
        except: return SharingType.GREETING

    # ==================== 统一命令入口 ====================
    @filter.command("分享")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_share_main(self, event: AstrMessageEvent):
        """
        每日分享统一命令入口
        """
        msg = event.message_str.strip()
        parts = msg.split()
        
        # 指令触发时缓存 Adapter ID
        try:
            if event.unified_msg_origin:
                adapter_id = event.unified_msg_origin.split(":")[0]
                if adapter_id:
                    self._cached_adapter_id = adapter_id
        except Exception:
            pass
        
        if len(parts) == 1:
            yield event.plain_result("指令格式错误，请指定参数。\n示例：/分享 新闻")
            return
            
        arg = parts[1].lower()
        
        # 判断是否是广播模式
        is_broadcast = "广播" in parts
        
        # 确定发送目标
        # 如果不是广播，就只发给当前会话
        current_uid = event.unified_msg_origin
        specific_target = None if is_broadcast else current_uid
        
        if arg == "状态":
            async for res in self._cmd_status(event): yield res
        elif arg == "开启":
            async for res in self._cmd_enable(event): yield res
        elif arg == "关闭":
            async for res in self._cmd_disable(event): yield res
        elif arg == "重置序列":
            async for res in self._cmd_reset_seq(event): yield res
        elif arg == "查看序列":
            async for res in self._cmd_view_seq(event): yield res
        elif arg == "帮助":
            async for res in self._cmd_help(event): yield res
        
        elif arg == "指定序列":
            if len(parts) > 2 and parts[2].isdigit():
                target_idx = int(parts[2])
                
                period = self._get_curr_period()
                config_key_map = {
                    TimePeriod.MORNING: "morning_sequence",
                    TimePeriod.FORENOON: "forenoon_sequence",
                    TimePeriod.AFTERNOON: "afternoon_sequence",
                    TimePeriod.EVENING: "evening_sequence",
                    TimePeriod.NIGHT: "night_sequence",
                    TimePeriod.LATE_NIGHT: "late_night_sequence",
                    TimePeriod.DAWN: "dawn_sequence"
                }
                config_key = config_key_map.get(period)
                seq = self.basic_conf.get(config_key, [])
                if not seq:
                    seq = SHARING_TYPE_SEQUENCES.get(period, [])

                if 0 <= target_idx < len(seq):
                    await self.db.update_state_dict("global", {"sequence_index": target_idx})
                    
                    t_raw = seq[target_idx]
                    t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                    yield event.plain_result(f"已切换下一次自动分享：{target_idx}. {t_cn}")
                else:
                    yield event.plain_result(f"序号无效，当前时段[{period.value}] 范围: 0 ~ {len(seq)-1}")
            else:
                yield event.plain_result("格式错误，请带上序号。例如：/分享 指定序列 1")

        elif arg in ["自动", "auto"]:
            target_desc = "配置的所有私聊和群聊" if is_broadcast else "当前会话"
            yield event.plain_result(f"正在向{target_desc}生成并发送分享内容(自动类型)...")
            await self._execute_share(None, specific_target=specific_target)
        else:
            if arg in CMD_CN_MAP:
                force_type = CMD_CN_MAP[arg]
                type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                
                # ===== 新闻类型的特殊逻辑 (处理源和图片) =====
                if force_type == SharingType.NEWS:
                    news_src = None
                    is_image_mode = False
                    
                    # 检查参数中是否包含 "图片"
                    if "图片" in parts:
                        is_image_mode = True
                    
                    # 检查参数中是否包含 指定源
                    for p in parts[2:]:
                        if p in ["图片", "广播"]: continue 
                        if p in SOURCE_CN_MAP:
                            news_src = SOURCE_CN_MAP[p]
                            break
                        elif p in NEWS_SOURCE_MAP:
                            news_src = p
                            break
                            
                    # 如果是图片模式，直接发送图片，绕过 LLM
                    if is_image_mode:
                        try:
                            if not news_src:
                                news_src = self.news_service.select_news_source()
                            
                            img_url, src_name = self.news_service.get_hot_news_image_url(news_src)
                            yield event.plain_result(f"正在获取 [{src_name}] 图片...")
                            yield event.image_result(img_url)
                        except Exception as e:
                            logger.error(f"[DailySharing] 指令获取新闻图片失败: {e}")
                            yield event.plain_result(f"获取图片失败: {e}")
                        return
                        
                    # 正常的 LLM 文字新闻模式
                    src_info = f" ({NEWS_SOURCE_MAP[news_src]['name']})" if news_src else ""
                    target_desc = "配置的所有私聊和群聊" if is_broadcast else "当前会话"
                    yield event.plain_result(f"正在向{target_desc}生成并发送分享{type_cn}{src_info} ...")
                    
                    await self._execute_share(force_type, news_source=news_src, specific_target=specific_target)
                    return
                    
                # 其他类型 (问候/心情等)
                target_desc = "配置的所有私聊和群聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并发送分享{type_cn} ...")
                await self._execute_share(force_type, specific_target=specific_target)
                return
            try:
                force_type = SharingType(arg)
                type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                target_desc = "配置的所有私聊和群聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并发送分享{type_cn} ...")
                await self._execute_share(force_type, specific_target=specific_target)
            except ValueError:
                yield event.plain_result(f"未知指令或无效类型: {arg}\n可用类型: 问候, 新闻, 心情, 知识, 推荐")

    # ==================== 子命令逻辑 ====================

    async def _cmd_enable(self, event: AstrMessageEvent):
        """启用插件"""
        self.config["enable_auto_sharing"] = True
        await self._save_config_file()
        
        cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
        self._setup_cron(cron)
        if not self.scheduler.running: self.scheduler.start()
        
        yield event.plain_result("自动分享已启用")

    async def _cmd_disable(self, event: AstrMessageEvent):
        """禁用插件"""
        self.config["enable_auto_sharing"] = False
        await self._save_config_file()
        self.scheduler.remove_all_jobs()
        yield event.plain_result("自动分享已禁用")

    async def _cmd_status(self, event: AstrMessageEvent):
        """查看详细状态"""
        state = await self.db.get_state("global", {})
        enabled = self.config.get("enable_auto_sharing", True)
        cron = self.basic_conf.get("sharing_cron")
        
        last_type_raw = state.get('last_type', '无')
        last_type_cn = TYPE_CN_MAP.get(last_type_raw, last_type_raw)
        
        period = self._get_curr_period()
        time_range = self._get_period_range_str(period)

        recent_history = await self.db.get_recent_history(5)
        hist_txt = "无记录"
        if recent_history:
            lines = []
            for h in recent_history:
                ts = str(h.get("timestamp", ""))
                content_preview = h.get('content', '') or ""
                t_raw = h.get('type')
                t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                lines.append(f"• {ts} [{t_cn}] {content_preview}")
            hist_txt = "\n".join(lines)

        msg = f"""每日分享状态
================
运行状态: {'启用' if enabled else '禁用'}
Cron规则: {cron}
当前时段: {period.value} ({time_range})

【序列状态】
上次类型: {last_type_cn}
上次时间: {state.get('last_timestamp', '无')[5:16].replace('T', ' ')}
序列索引: {state.get('sequence_index', 0)}

【最近记录】
{hist_txt}
"""
        yield event.plain_result(msg)

    async def _cmd_reset_seq(self, event: AstrMessageEvent):
        """重置序列"""
        await self.db.update_state_dict("global", {"sequence_index": 0, "last_period": None})
        yield event.plain_result("序列已重置")

    async def _cmd_view_seq(self, event: AstrMessageEvent):
        """查看序列详情"""
        period = self._get_curr_period()
        time_range = self._get_period_range_str(period)
        
        config_key_map = {
            TimePeriod.MORNING: "morning_sequence",
            TimePeriod.FORENOON: "forenoon_sequence",
            TimePeriod.AFTERNOON: "afternoon_sequence",
            TimePeriod.EVENING: "evening_sequence",
            TimePeriod.NIGHT: "night_sequence",
            TimePeriod.LATE_NIGHT: "late_night_sequence",
            TimePeriod.DAWN: "dawn_sequence"
        }
        config_key = config_key_map.get(period)
        seq = self.basic_conf.get(config_key, [])
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(period, [])

        state = await self.db.get_state("global", {})
        idx = state.get("sequence_index", 0)
        
        txt = f"当前时段: {period.value} ({time_range})\n"
        for i, t_raw in enumerate(seq):
            mark = "👉 " if i == idx else "   "
            t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
            txt += f"{mark}{i}. {t_cn}\n"
        yield event.plain_result(txt)

    async def _cmd_help(self, event: AstrMessageEvent):
        """帮助菜单"""
        yield event.plain_result("""每日分享插件帮助:
/分享 [类型] - 立即在当前会话生成分享 (类型: 问候/新闻/心情/知识/推荐)
/分享 [类型] 广播 - 立即向所有配置的私聊和群聊分享
/分享 新闻 [源] - 获取指定平台热搜
/分享 新闻 [源] 图片 - 获取热搜长图
/分享 状态 - 查看运行状态
/分享 开启/关闭 - 启停自动分享
/分享 查看序列 - 查看当前时段序列
/分享 指定序列 [序号] - 手动调整指定分享内容
/分享 重置序列 - 重置当前发送序列到开头""")
