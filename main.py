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
    "腾讯": "tencent",
    "夸克": "quark"
})

@register("daily_sharing", "四次元未来", "定时主动分享所见所闻", "4.7.2")
class DailySharingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config 
        self.scheduler = AsyncIOScheduler()
        
        self.basic_conf = self.config.get("basic_conf", {})
        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.qzone_conf = self.config.get('qzone_conf', {})
        self.receiver_conf = self.config.get("receiver", {})
        self.extra_shares_conf = self.config.get("extra_shares", {})
        
        # 分享内容记录条数 
        self.history_limit = 100
        
        # 锁与防抖
        self._lock = asyncio.Lock()
        self._last_share_time = None
        
        # 生命周期标志位 
        self._is_terminated = False
        
        # 缓存 Adapter ID 
        self._cached_adapter_id = None 

        # 临时降级第一个模型缓存
        self._temp_fallback_provider = None

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

    def _inject_qzone_client(self, qzone_plugin):
        """尝试为 QQ空间 插件注入 CQHttp 客户端，解决自动任务时没有 client 的报错"""
        try:
            if qzone_plugin and hasattr(qzone_plugin, "cfg") and not qzone_plugin.cfg.client:
                if self.ctx_service.bot_map:
                    # 优先寻找 aiocqhttp 适配器
                    aiocqhttp_bot = None
                    for pid, bot in self.ctx_service.bot_map.items():
                        if "aiocqhttp" in pid.lower():
                            aiocqhttp_bot = bot
                            break
                    bot_client = aiocqhttp_bot or list(self.ctx_service.bot_map.values())[0]
                    if bot_client:
                        qzone_plugin.cfg.client = bot_client
                        logger.debug(f"[DailySharing] QQ空间插件注入客户端成功！")
        except Exception as e:
            logger.warning(f"[DailySharing] QQ空间插件注入客户端失败: {e}")        

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

        # 1. 主流程定时任务 (LLM 分享)
        if self.config.get("enable_auto_sharing", False):
            cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
            self._setup_cron(cron)
            actual_cron = CRON_TEMPLATES.get(cron, cron)
            logger.debug(f"[DailySharing] 分享内容定时任务已启动 ({cron})")
        else:
            logger.debug("[DailySharing] 分享内容已禁用")

        # 2. 独立早报任务 (60s + AI) - 共用一个定时器
        enable_60s = self.extra_shares_conf.get("enable_60s_news", False)
        enable_ai = self.extra_shares_conf.get("enable_ai_news", False)

        # 只要有一个开启，就注册定时任务
        if enable_60s or enable_ai:
            cron_briefing = self.extra_shares_conf.get("cron_briefing", "0 8 * * *")
            self._setup_cron_job_custom("share_briefing", cron_briefing, self._task_wrapper_briefing)
            logger.debug(f"[DailySharing] 早报定时任务已启动 ({cron_briefing})")

        # 3. QQ空间独立定时任务
        if self.qzone_conf.get("enable_qzone", False):
            q_cron = self.qzone_conf.get("qzone_cron", "0 20 * * *")
            actual_q_cron = CRON_TEMPLATES.get(q_cron, q_cron)
            self._setup_cron_job_custom("qzone_share", actual_q_cron, self._task_wrapper_qzone)
            logger.debug(f"[DailySharing] QQ空间定时任务已启动 ({actual_q_cron})")

        # 启动调度器 
        if not self._is_terminated and not self.scheduler.running:
            if self.scheduler.get_jobs():
                self.scheduler.start()

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
        get_image: bool = True,
        need_image: bool = False,
        need_video: bool = False,
        need_voice: bool = False,
        to_qzone: bool = False
    ):
        """
        主动分享日常内容、新闻热搜、获取热搜图片等。
        当用户想要看新闻、热搜、早安晚安、冷知识、心情或推荐时调用此工具。
        也支持获取"每天60s读世界"或"AI资讯快报"图片。

        Args:
            share_type(string): 分享类型。支持：'自动', '问候', '新闻', '心情', '知识', '推荐', '60s新闻', 'AI资讯'。当用户没有明确指出发什么类型的内容（比如只说“发个说说”、“分享一下”）时，请务必将其设为 '自动'。
            source(string): 仅当 share_type 为'新闻'时有效。指定新闻平台。支持：微博, 知乎, B站, 抖音, 头条, 百度, 腾讯, 小红书, 夸克。如果不指定则留空。
            get_image(boolean): 仅当 share_type 为'新闻'时有效。默认为 True (优先分享热搜长图)。只有当用户明确要求“文字版”、“文本”、“不要图片”或“写一段新闻”时，才将其设为 False。
            need_image(boolean): 是否需要AI为这段文案配图。默认为 False。仅当用户明确说“配图”、“带图”、“发张图”时，才将其设为 True。
            need_video(boolean): 是否需要AI为这段文案生成视频。默认为 False。仅当用户明确说“视频”、“动态图”、“动起来”时，才将其设为 True。
            need_voice(boolean): 是否需要将文案转为语音(TTS)分享。默认为 False。仅当用户明确提到“语音”、“朗读”、“念给我听”时，设为 True。
            to_qzone(boolean): 是否需要将内容作为说说分享到QQ空间。默认为 False。仅当用户明确要求“发说说”、“发空间”、“分享到空间”时，必须设为 True。
        """
        if self._is_terminated: return ""

        # 1. 防抖检查
        if self._lock.locked():
            await event.send(event.plain_result("正如火如荼地准备中，请稍后..."))
            return None

        # 2. 启动后台异步任务
        task = asyncio.create_task(
            self._async_daily_share_task(
                event, share_type, source, get_image, need_image, need_video, need_voice, to_qzone
            )
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

        # 3. 直接返回空字符串，让 LLM 闭嘴，不再生成回复
        return None

    async def _async_daily_share_task(
        self,
        event: AstrMessageEvent,
        share_type: str,
        source: str,
        get_image: bool,
        need_image: bool,
        need_video: bool,
        need_voice: bool,
        to_qzone: bool
    ):
        """实际执行分享逻辑的后台任务 (LLM 触发)"""
        try:
            # 特殊图片类型处理 (60s / AI) 
            st_clean = share_type.lower().replace(" ", "")
            
            # 60s新闻
            if any(k in st_clean for k in ["60s", "六十秒", "读世界"]):
                url = self.news_service.get_60s_image_url()
                if not url:
                    await event.send(event.plain_result("获取60s新闻失败，请检查API Key配置。"))
                    return 
                    
                if to_qzone:
                    qzone_plugin = self.ctx_service._find_plugin("qzone")
                    if qzone_plugin and hasattr(qzone_plugin, "service"):
                        self._inject_qzone_client(qzone_plugin)
                        try:
                            await qzone_plugin.service.publish_post(text="【每天60秒读懂世界】", images=[url])
                            await event.send(event.plain_result("每天60s读世界已成功分享到QQ空间！"))
                            await self.db.add_sent_history("qzone_broadcast", "news", "【每天60秒读懂世界】(LLM)", True)
                        except Exception as e:
                            await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                    else:
                        await event.send(event.plain_result("未检测到QQ空间插件！"))
                else:
                    await event.send(event.image_result(url))
                return 

            # AI资讯
            if any(k in st_clean for k in ["ai资讯", "ai新闻", "ai日报"]) or st_clean == "ai":
                url = self.news_service.get_ai_news_image_url()
                if not url:
                    await event.send(event.plain_result("获取AI资讯失败，请检查API Key配置。"))
                    return 
                    
                if to_qzone:
                    qzone_plugin = self.ctx_service._find_plugin("qzone")
                    if qzone_plugin and hasattr(qzone_plugin, "service"):
                        self._inject_qzone_client(qzone_plugin)
                        try:
                            await qzone_plugin.service.publish_post(text="【AI资讯快报】", images=[url])
                            await event.send(event.plain_result("AI资讯快报已成功分享到QQ空间！"))
                            await self.db.add_sent_history("qzone_broadcast", "news", "【AI资讯快报】(LLM)", True)
                        except Exception as e:
                            await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                    else:
                        await event.send(event.plain_result("未检测到QQ空间插件！"))
                else:
                    await event.send(event.image_result(url))
                return 

            # === 常规流程 ===
            # 参数清洗与映射
            target_type_enum = None
            
            if share_type == "自动" or share_type == "auto":
                target_type_enum = None  
            else:
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
                    await event.send(event.plain_result(f"不支持的分享类型：{share_type}。支持：自动, 问候, 新闻, 心情, 知识, 推荐, 60s新闻, AI资讯。"))
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
            
            # 逻辑判定：新闻默认发静态图
            is_news = (target_type_enum == SharingType.NEWS)
            
            # 触发静态图发送的条件：
            # 1. 是新闻
            # 2. 需要获取图片 (get_image=True)
            # 3. 不需要AI配图 (not need_image)
            # 4. 不需要语音 (not need_voice) -> 如果需要语音，必须走 LLM 生成文本
            # 5. 不需要视频 (not need_video) -> 如果需要视频，必须走 后续流程
            if is_news and get_image and not need_image and not need_voice and not need_video:
                try:
                    img_url = None
                    src_name = ""
                    # 优先使用指定的源热搜
                    if news_src_key:
                        img_url, src_name = self.news_service.get_hot_news_image_url(news_src_key)
                    else:
                        # 如果没有指定，则随机选择一个已启用的新闻源发送
                        random_src = self.news_service.select_news_source()
                        img_url, src_name = self.news_service.get_hot_news_image_url(random_src)

                    if img_url:
                        if to_qzone:
                            qzone_plugin = self.ctx_service._find_plugin("qzone")
                            if qzone_plugin and hasattr(qzone_plugin, "service"):
                                self._inject_qzone_client(qzone_plugin)
                                try:
                                    await qzone_plugin.service.publish_post(text=f"【{src_name}】", images=[img_url])
                                    await event.send(event.plain_result(f"[{src_name}] 图片已成功分享到QQ空间！"))
                                    await self.db.add_sent_history("qzone_broadcast", "news", f"【{src_name}】长图(LLM)", True)
                                except Exception as e:
                                    await event.send(event.plain_result(f"QQ空间分享失败: {e}"))
                            else:
                                await event.send(event.plain_result("未检测到QQ空间插件！"))
                        else:
                            await event.send(event.image_result(img_url))
                    else:
                        await event.send(event.plain_result("获取新闻图片失败。"))
                except Exception as e:
                    logger.error(f"[DailySharing] 获取新闻图片失败: {e}")
                    await event.send(event.plain_result(f"获取新闻图片失败。"))
                
                return

            # 如果用户要求发QQ空间文案说说
            if to_qzone:
                await self._execute_qzone_share(force_type=target_type_enum, news_source=news_src_key, event=event)
                return

            # 场景 B: 标准 LLM 生成流程
            # 1. 纯文字模式 (问候/心情/知识/推荐/新闻文字版)
            # 2. 高级模式 (任何类型 + need_image=True)
            # 3. 语音模式 (任何类型 + need_voice=True)
            
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
            
            # 初始化 img_path (可能用于存放热搜截图)
            img_path = None
            
            if target_type_enum == SharingType.NEWS:
                # 这里的 news_src_key 如果是 None 会自动选择
                if not news_src_key:
                    news_src_key = self.news_service.select_news_source()
                news_data = await self.news_service.get_hot_news(news_src_key)
                
                # 如果在主流程中(因为要语音等原因进来了)，且用户依然默认想要看热搜图
                # (即：是新闻，且没说不要图片，且没说要AI配图)
                # 那么我们在这里把热搜截图取出来，准备等会一起发
                if get_image and not need_image:
                    try:
                        img_path, _ = self.news_service.get_hot_news_image_url(news_src_key)
                    except Exception as e:
                        logger.warning(f"[DailySharing] 主流程获取热搜图片失败: {e}")

            # 获取历史
            is_group = self.ctx_service._is_group_chat(target_umo)
            hist_data = await self.ctx_service.get_history_data(target_umo, is_group)
            hist_prompt = self.ctx_service.format_history_prompt(hist_data, target_type_enum)
            group_info = hist_data.get("group_info")
            life_prompt = self.ctx_service.format_life_context(life_ctx, target_type_enum, is_group, group_info)
            
            # 获取昵称
            nickname = ""
            if not is_group:
                nickname = event.get_sender_name()

            # 生成内容
            content = await self.content_service.generate(
                target_type_enum, period, target_umo, is_group, life_prompt, hist_prompt, news_data, nickname=nickname
            )
            
            if not content:
                await event.send(event.plain_result("内容生成失败，请稍后再试。"))
                return
            
            # ================= 视觉生成逻辑 (LLM 触发：严格手动控制) =================
            video_url = None
            
            should_gen_visual = False
            # 只有当用户明确要求配图或视频时，才生成
            if self.image_conf.get("enable_ai_image", False):
                if need_image or need_video:
                    should_gen_visual = True

            if should_gen_visual:
                # 生成图片 (注意：如果生成了AI图片，会覆盖上面的热搜截图 img_path)
                ai_img_path = await self.image_service.generate_image(content, target_type_enum, life_ctx)
                if ai_img_path:
                    img_path = ai_img_path
                
                # 生成视频 (如果明确要求视频)
                if img_path and self.image_conf.get("enable_ai_video", False):
                    if need_video:
                        video_url = await self.image_service.generate_video_from_image(img_path, content)

            # ================= 语音生成逻辑 (LLM 触发：严格手动控制) =================
            audio_path = None
            if self.tts_conf.get("enable_tts", False):
                should_gen_voice = False
                # 只有当用户明确要求语音时，才生成
                if need_voice:
                    should_gen_voice = True
                        
                if should_gen_voice:
                    audio_path = await self.ctx_service.text_to_speech(content, target_umo, target_type_enum, period)

            # 发送 (img_path 可能是热搜截图，也可能是AI画的图)
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
        """LLM 调用包装器（支持失败重试与自动降级）"""
        if self._is_terminated: return None
        
        def _get_system_default_provider() -> str:
            # 如果没指定，默认使用第一个模型
            try:
                cfg = self.context.get_config()
                if cfg:
                    pid = cfg.get("provider_settings", {}).get("default_provider_id", "")
                    if pid: return pid
                    for p in cfg.get("provider", []):
                        if p.get("enable", False) and "chat" in p.get("provider_type", "chat"):
                            return p.get("id")
            except Exception:
                pass
            return ""

        user_provider_id = self.llm_conf.get("llm_provider_id", "")

        # 如果存在临时降级缓存，说明指定的模型已经坏了，直接跳过它        
        if self._temp_fallback_provider:
            user_provider_id = self._temp_fallback_provider
            
        current_provider_id = user_provider_id if user_provider_id else _get_system_default_provider()

        config_timeout = self.llm_conf.get("llm_timeout", 60)
        actual_timeout = max(timeout, config_timeout)

        for attempt in range(max_retries + 1):
            if self._is_terminated: return None
            
            # 降级逻辑 1
            is_last_attempt = (attempt == max_retries)
            if is_last_attempt and attempt > 0 and user_provider_id and current_provider_id == user_provider_id:
                default_pid = _get_system_default_provider()
                if default_pid and default_pid != current_provider_id:
                    logger.info(f"[DailySharing] 指定 LLM 已达到重试次数，降级使用默认的第一个模型({default_pid})...")
                    current_provider_id = default_pid
                    self._temp_fallback_provider = default_pid 

            try:
                kwargs = {"prompt": prompt}
                if system_prompt is not None and system_prompt != "":
                    kwargs["system_prompt"] = system_prompt
                if current_provider_id:
                    kwargs["chat_provider_id"] = current_provider_id

                resp = await asyncio.wait_for(
                    self.context.llm_generate(**kwargs),
                    timeout=actual_timeout
                )
                
                if resp and hasattr(resp, 'completion_text'):
                    result = resp.completion_text.strip()
                    if result:
                        return result
                    
            except asyncio.TimeoutError:
                logger.warning(f"[DailySharing] LLM 超时 ({actual_timeout}s) (尝试 {attempt+1}/{max_retries+1})")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
            except Exception as e:
                err_str = str(e)
                if "PROHIBITED_CONTENT" in err_str or "blocked" in err_str:
                    logger.error(f"[DailySharing] 内容被模型安全策略拦截 (敏感词): {prompt[:50]}...")
                    return None 

                if "401" in err_str:
                    logger.error(f"[DailySharing] LLM 失败。请检查 API Key。")
                    # 降级逻辑 2                    
                    if attempt < max_retries and user_provider_id and current_provider_id == user_provider_id:
                        default_pid = _get_system_default_provider()
                        if default_pid and default_pid != current_provider_id:
                            logger.info(f"[DailySharing] 遇到 401 错误，降级使用默认的第一个模型({default_pid})...")
                            current_provider_id = default_pid
                            self._temp_fallback_provider = default_pid 
                            await asyncio.sleep(2)
                            continue
                        else:
                            return None
                    else:
                        return None
                
                logger.error(f"[DailySharing] LLM异常 (尝试 {attempt+1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue

        logger.error(f"[DailySharing] LLM调用失败（已重试{max_retries}次）")
        return None

    def _setup_cron(self, cron_str):
        """设置 Cron 任务 (主流程)"""
        self._setup_cron_job_custom("auto_share", cron_str, self._task_wrapper)

    def _setup_cron_job_custom(self, job_id: str, cron_str: str, func):
        """通用 Cron 设置方法"""
        if self._is_terminated: return
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

            actual_cron = CRON_TEMPLATES.get(cron_str, cron_str)
            parts = actual_cron.split()
            
            if len(parts) == 5:
                self.scheduler.add_job(
                    func, 'cron',
                    minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
                    id=job_id,
                    replace_existing=True,
                    max_instances=1
                )
                logger.debug(f"[DailySharing] 任务[{job_id}]已设定: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 任务[{job_id}]无效的 Cron 表达式: {cron_str}")
        except Exception as e:
            logger.error(f"[DailySharing] 任务[{job_id}]设置失败: {e}")

    async def _task_wrapper(self):
        """主任务包装器（防抖 + 锁 + 随机延迟 + 数据清理）"""
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

            if random_delay_min > 0:
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

    # ==================== 早报任务包装器与执行逻辑 ====================
    
    async def _task_wrapper_briefing(self):
        """早报任务回调"""
        if self._is_terminated: return
        task = asyncio.current_task()
        self._bg_tasks.add(task)
        try:
            await self._execute_briefing_share()
        finally:
            self._bg_tasks.discard(task)

    async def _execute_briefing_share(self, specific_target: str = None):
        """执行早报分享：依次发送开启的 60s 和 AI 资讯"""
        if self._is_terminated: return
        
        logger.info("[DailySharing] 开始执行独立早报任务")
        
        # 1. 收集需要分享的图片 URL
        images_to_send = [] 
        
        # 检查 60s (定时触发时检查开关，手动触发时跳过开关检查)
        check_60s = self.extra_shares_conf.get("enable_60s_news", False)
        if specific_target: check_60s = True 
        
        if self.extra_shares_conf.get("enable_60s_news", False):
            url = self.news_service.get_60s_image_url()
            if url: images_to_send.append(("60s新闻", url))

        # 排除周日和周一
        if self.extra_shares_conf.get("enable_ai_news", False):
            weekday = datetime.now().weekday()
            # 如果是周日(6) 或 周一(0)，且不是手动指定目标(视为自动任务)，则跳过
            if weekday in [0, 6] and specific_target is None:
                logger.info(f"[DailySharing] 今天是周{'日' if weekday==6 else '一'}，跳过发送AI资讯")
            else:
                url = self.news_service.get_ai_news_image_url()
                if url: images_to_send.append(("AI资讯", url))

        if not images_to_send:
            logger.warning("[DailySharing] 早报任务触发，发现没有开启的早报发送或获取图片失败")
            return

        # 定时早报自动同步到QQ空间
        # 仅在自动定时任务触发时且开关打开时执行
        if specific_target is None and self.extra_shares_conf.get("sync_briefing_to_qzone", False):
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if qzone_plugin and hasattr(qzone_plugin, "service"):
                self._inject_qzone_client(qzone_plugin)
                logger.info("[DailySharing] 分享早报到QQ空间已开启...")
                for name, url in images_to_send:
                    try:
                        title = "【每天60秒读懂世界】" if "60s" in name else "【AI资讯快报】"
                        await qzone_plugin.service.publish_post(text=title, images=[url])
                        await self.db.add_sent_history("qzone_broadcast", "news", f"{title}(定时自动)", True)
                        await asyncio.sleep(3) 
                        logger.info(f"[DailySharing] 分享早报{name}到QQ空间成功！")
                    except Exception as e:
                        logger.error(f"[DailySharing] 分享早报{name}到QQ空间失败: {e}")
            else:
                logger.warning("[DailySharing] 分享早报到QQ空间开启，但未检测到 astrbot_plugin_qzone 插件")

        # 2. 确定目标 (复用配置)
        targets = []
        if specific_target:
            targets.append(specific_target)
        else:
            # 自动获取 bot id
            default_adapter_id = self._cached_adapter_id
            if not default_adapter_id:
                try:
                    if hasattr(self.context, "platform_manager"):
                        insts = self.context.platform_manager.get_insts()
                        for inst in insts:
                            if hasattr(inst, "metadata") and inst.metadata.id:
                                default_adapter_id = inst.metadata.id
                                self._cached_adapter_id = default_adapter_id
                                break
                except: pass
            
            if not default_adapter_id: default_adapter_id = "aiocqhttp"

            # 增加健壮性检查：确保配置获取的是列表，且不为None
            r_groups = self.receiver_conf.get("groups")
            if not isinstance(r_groups, list): r_groups = []
            
            r_users = self.receiver_conf.get("users")
            if not isinstance(r_users, list): r_users = []

            for gid in r_groups:
                if gid: targets.append(f"{default_adapter_id}:GroupMessage:{gid}")
            for uid in r_users:
                if uid: targets.append(f"{default_adapter_id}:FriendMessage:{uid}")
            
            logger.info(f"[DailySharing] 早报任务目标: 群{len(r_groups)} / 人{len(r_users)} (Adapter: {default_adapter_id})")

        if not targets:
            logger.warning("[DailySharing] 未找到任何早报接收目标")
            return

        # 3. 分享循环
        for uid in targets:
            if self._is_terminated: break
            try:
                for name, url in images_to_send:
                    # 构建消息链
                    msg = MessageChain().url_image(url)
                    logger.info(f"[DailySharing] 正在分享{name}到{uid}")
                    await self.context.send_message(uid, msg)
                    # 每张图之间间隔 1 秒
                    await asyncio.sleep(1)
                
                # 每个群之间间隔 2 秒
                await asyncio.sleep(2) 
            except Exception as e:
                logger.error(f"[DailySharing] 分享早报到 {uid} 失败: {e}")

    # ==================== 主流程分享逻辑 ====================

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
        
        # 1. 确定分享目标
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
                    # 健壮性检查
                    r_groups = self.receiver_conf.get("groups")
                    if not isinstance(r_groups, list): r_groups = []
                    
                    r_users = self.receiver_conf.get("users")
                    if not isinstance(r_users, list): r_users = []

                    for gid in r_groups:
                        if gid: targets.append(f"{default_adapter_id}:GroupMessage:{gid}")
                    for uid in r_users:
                        if uid: targets.append(f"{default_adapter_id}:FriendMessage:{uid}")

        if not targets:
            logger.warning("[DailySharing] 未配置接收对象，且未指定目标，请在配置页填写群号或QQ号")
            return

        for uid in targets:
            if self._is_terminated: break
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower() or "guild" in uid.lower()
                
                # 尝试获取用户昵称 (仅限私聊) 
                nickname = ""
                if not is_group:
                    try:
                        adapter_id, real_id = self.ctx_service._parse_umo(uid)
                        if adapter_id and real_id:
                            bot = self.ctx_service._get_bot_instance(adapter_id)
                            if bot:
                                # 尝试调用 get_stranger_info 获取昵称
                                ret = await bot.api.call_action("get_stranger_info", user_id=int(real_id))
                                if ret and isinstance(ret, dict):
                                    nickname = ret.get("nickname", "")
                                    logger.info(f"[DailySharing] 获取到用户昵称: {nickname}")
                    except Exception as e:
                         # 获取失败则保持为空，不影响后续流程
                         logger.warning(f"[DailySharing] 获取昵称失败: {e}")

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
                    stype, period, uid, is_group, life_prompt, hist_prompt, news_data, nickname=nickname
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
                # 注意：这是自动任务的逻辑，依然遵守白名单配置
                
                # 1. 配图生成逻辑
                img_path = None
                video_url = None
                enable_img_global = self.image_conf.get("enable_ai_image", False)
                img_allowed_types = self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
                
                # 【新闻类型特殊处理】如果未开启AI配图或当前类型不允许AI配图，但这是新闻，尝试把热搜图带上
                if stype == SharingType.NEWS:
                    try:
                        # 如果没有指定源（自动选择模式），复用 state 中的 last_news_source，或者重新获取 current news source
                        # 注意：这里我们假设 get_hot_news 已经更新了 state 或 news_data 包含了 source
                        
                        # 简化逻辑：直接获取 state 中记录的 last_news_source (刚刚在 get_hot_news 成功后更新的)
                        state = await self.db.get_state("global", {})
                        last_source = state.get("last_news_source")
                        if last_source:
                            img_path, _ = self.news_service.get_hot_news_image_url(last_source)
                    except Exception as e:
                        logger.warning(f"[DailySharing] 自动任务获取新闻图片失败: {e}")

                if enable_img_global:
                    if stype.value in img_allowed_types:
                        ai_img_path = await self.image_service.generate_image(content, stype, life_ctx)
                        if ai_img_path:
                            # AI 图片覆盖热搜截图
                            img_path = ai_img_path
                            
                        # 尝试生成视频
                        if img_path and self.image_conf.get("enable_ai_video", False):
                            video_allowed = self.image_conf.get("video_enabled_types", ["greeting", "mood"])
                            if stype.value in video_allowed:
                                video_url = await self.image_service.generate_video_from_image(img_path, content)
                    else:
                         logger.info(f"[DailySharing] 当前类型 {stype.value} 不在配图允许列表，跳过配图。")

                # 2. 语音生成逻辑
                audio_path = None
                enable_tts_global = self.tts_conf.get("enable_tts", False)
                tts_allowed_types = self.tts_conf.get("tts_enabled_types", ["greeting", "mood"])
                
                if enable_tts_global:
                    if stype.value in tts_allowed_types:
                        # 传入 stype 和 period 以确定情感
                        audio_path = await self.ctx_service.text_to_speech(content, uid, stype, period)
                    else:
                        logger.info(f"[DailySharing] 当前类型 {stype.value} 不在语音允许列表，跳过语音。")

                # 分享内容
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
        """分享内容（支持分开分享，支持语音和视频）"""
        if self._is_terminated: return

        try:
            separate_img = self.image_conf.get("separate_text_and_image", True)
            prefer_audio_only = self.tts_conf.get("prefer_audio_only", False)
            
            # 清洗情感标签
            clean_text = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', text, flags=re.IGNORECASE).strip()
            
            # 判断是否应该分享文字
            # 如果有语音，且开启了“仅发语音”，则不发文字
            should_send_text = True
            if audio_path and prefer_audio_only:
                should_send_text = False

            # 1. 分享文字（如果需要）
            if should_send_text and clean_text: 
                text_chain = MessageChain().message(clean_text) 
                # 如果图片不分开分享，且没有语音，且没有视频（视频无法合并），则合并图片
                if img_path and not video_url and not separate_img and not audio_path:
                    if img_path.startswith("http"): text_chain.url_image(img_path)
                    else: text_chain.file_image(img_path)
                
                await self.context.send_message(uid, text_chain)
                
                # 如果后续还有消息，进行随机延迟
                if audio_path or ((img_path or video_url) and separate_img):
                    await self._random_sleep()

            # 2. 分享语音（如果有）
            if audio_path:
                audio_chain = MessageChain()
                audio_chain.chain.append(Record(file=audio_path))
                await self.context.send_message(uid, audio_chain)
                
                # 如果后续还有视觉媒体，延迟
                if (img_path or video_url) and separate_img:
                    await self._random_sleep()
            
            # 3. 分享视觉媒体（视频优先，其次图片）
            if video_url:
                # 分享视频
                video_chain = MessageChain()
                # 判断是本地文件还是网络URL
                if video_url.startswith("http"):
                    video_chain.chain.append(Video.fromURL(video_url))
                else:
                    # 如果是本地路径，使用 fromFile
                    video_chain.chain.append(Video.fromFileSystem(video_url))              
                await self.context.send_message(uid, video_chain)
            elif img_path:
                # 分享图片（如果视频没生成，或者视频关闭）
                # 逻辑：只要图片还没发（separate_img 为真，或者虽然 separate_img 为假但因为有语音没能合并），就发
                img_not_sent_yet = separate_img or audio_path
                if img_not_sent_yet:
                    img_chain = MessageChain()
                    if img_path.startswith("http"): img_chain.url_image(img_path)
                    else: img_chain.file_image(img_path)
                    await self.context.send_message(uid, img_chain)

        except Exception as e:
            logger.error(f"[DailySharing] 分享内容给 {uid} 失败: {e}")

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

    async def _decide_type_with_state(self, current_period: TimePeriod, is_qzone: bool = False) -> SharingType:
        # 区分配置和数据库存储键
        conf_node = self.qzone_conf if is_qzone else self.basic_conf
        type_key = "qzone_sharing_type" if is_qzone else "sharing_type"
        state_key = "qzone" if is_qzone else "global"
        
        conf_type = conf_node.get(type_key, "auto")
        if conf_type != "auto":
            try: return SharingType(conf_type)
            except: pass
        
        state = await self.db.get_state(state_key, {})
        
        # 映射序列前缀
        prefix = "qzone_" if is_qzone else ""
        config_key_map = {
            TimePeriod.MORNING: f"{prefix}morning_sequence",
            TimePeriod.FORENOON: f"{prefix}forenoon_sequence",
            TimePeriod.AFTERNOON: f"{prefix}afternoon_sequence",
            TimePeriod.EVENING: f"{prefix}evening_sequence",
            TimePeriod.NIGHT: f"{prefix}night_sequence",
            TimePeriod.LATE_NIGHT: f"{prefix}late_night_sequence",
            TimePeriod.DAWN: f"{prefix}dawn_sequence"
        }
        
        config_key = config_key_map.get(current_period)
        seq = conf_node.get(config_key, [])
        
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])
        
        idx_key = f"index_{current_period.value}"
        idx = state.get(idx_key, 0)
        
        if idx >= len(seq): idx = 0
        selected = seq[idx]
        next_idx = (idx + 1) % len(seq)
        
        updates = {
            "last_period": current_period.value,
            idx_key: next_idx,            
            "sequence_index": next_idx,  
            "last_timestamp": datetime.now().isoformat(),
            "last_type": selected
        }
        await self.db.update_state_dict(state_key, updates)
        
        try: return SharingType(selected)
        except: return SharingType.GREETING

    # ==================== QQ空间独立执行逻辑 ====================

    async def _task_wrapper_qzone(self):
        """QQ 空间任务包装器（包含防抖和随机延迟）"""
        if self._is_terminated: return
        task = asyncio.current_task()
        self._bg_tasks.add(task)
        
        try:
            random_delay_min = int(self.basic_conf.get("cron_random_delay", 0))
            if random_delay_min > 0:
                delay_seconds = random.randint(0, random_delay_min * 60)
                if delay_seconds > 0:
                    logger.info(f"[DailySharing] QQ空间任务将随机延迟 {delay_seconds/60:.1f} 分钟...")
                    try:
                        await asyncio.sleep(delay_seconds)
                    except asyncio.CancelledError:
                        return

            if self._is_terminated: return

            # 为了安全，这里也加上互斥锁，防止和群聊同时生成触发大模型并发限制
            async with self._lock:
                await self._execute_qzone_share()
                
        finally:
            self._bg_tasks.discard(task)

    async def _execute_qzone_share(self, force_type: SharingType = None, news_source: str = None, event: AstrMessageEvent = None):
        """完全独立的 QQ 空间执行主流程"""
        if self._is_terminated: return
        
        try:
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if not qzone_plugin or not hasattr(qzone_plugin, "service"):
                logger.warning("[DailySharing] QQ空间任务触发，但未检测到 astrbot_plugin_qzone 插件")
                if event:
                    await event.send(event.plain_result("未检测到 astrbot_plugin_qzone 插件"))
                return

            self._inject_qzone_client(qzone_plugin)
            period = self._get_curr_period()
            # 注意这里传入 is_qzone=True，使用专属序列
            stype = force_type if force_type else await self._decide_type_with_state(period, is_qzone=True) 
            logger.info(f"[DailySharing] QQ空间时段: {period.value}, 类型: {stype.value}")

            # 获取生活上下文
            life_ctx = await self.ctx_service.get_life_context()
            news_data = None
            
            # 如果是发新闻，单独获取热搜（支持手动指定源）
            if stype == SharingType.NEWS:
                actual_source = news_source if news_source else self.news_service.select_news_source()
                news_data = await self.news_service.get_hot_news(actual_source)

            # 屏蔽历史记录，使用纯净的提示词让LLM写说说
            qzone_life_prompt = self.ctx_service.format_life_context(life_ctx, stype, False, None)
            qzone_life_prompt += (
                "\n\n【最高优先级覆盖指令】\n"
                "请你完全无视系统提示中关于“一对一私聊”、“对单个朋友聊天”、“使用你”等所有设定！\n"
                "这是一条个人QQ空间社交平台的动态说说\n"
                "当前任务是以纯粹的【个人日记或心情独白】的口吻来写。\n"
                "1. 绝对禁止对别人说话，严禁出现“你”、“你们”、“大家”等称呼。\n"
                "2. 只能专注描绘自己的状态，就像自己在自言自语一样。"
            )
            
            logger.info("[DailySharing] 正在为QQ空间生成文案...")
            qzone_content = await self.content_service.generate(
                stype, period, "qzone_broadcast", False, qzone_life_prompt, "", news_data, nickname=""
            )
            
            if not qzone_content:
                logger.error("[DailySharing] QQ空间文案生成失败")
                if event:
                    await event.send(event.plain_result("QQ空间文案生成失败"))
                return

            # 清洗情感标签
            clean_qzone_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', qzone_content, flags=re.IGNORECASE).strip()

            # 处理配图逻辑
            qzone_images = []
            target_local_img = None
            
            enable_img_qzone = self.qzone_conf.get("qzone_enable_image", False)
            enable_img_global = self.image_conf.get("enable_ai_image", False)
            
            # 获取QQ空间配图允许类型，如果没配置，默认复用群聊分享的配置
            qzone_img_allowed_types = self.qzone_conf.get(
                "qzone_image_enabled_types", 
                self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
            )

            if enable_img_qzone and enable_img_global:
                if stype.value in qzone_img_allowed_types:
                    logger.info("[DailySharing] 正在为QQ空间生成配图...")
                    try:
                        new_img_path = await self.image_service.generate_image(clean_qzone_content, stype, life_ctx)
                        if new_img_path:
                            target_local_img = new_img_path
                    except Exception as e:
                        logger.error(f"[DailySharing] QQ空间配图生成失败: {e}")
                else:
                    logger.info(f"[DailySharing] 当前类型 {stype.value} 不在QQ空间配图允许列表，跳过配图。")
            
            # 如果是新闻类型，且没有开启画图，尝试贴热搜图
            if stype == SharingType.NEWS and not target_local_img:
                try:
                    if news_data:
                        img_url, _ = self.news_service.get_hot_news_image_url(news_data[1])
                        target_local_img = img_url
                except Exception as e:
                    pass

            if target_local_img:
                if target_local_img.startswith("http"):
                    qzone_images.append(target_local_img)
                else:
                    qzone_images.append(f"local_path::{target_local_img}")
                            
            import sys
            import aiofiles
            qzone_utils_mod = None
            for mod_name, mod in sys.modules.items():
                if "qzone" in mod_name and "utils" in mod_name and hasattr(mod, "download_file"):
                    qzone_utils_mod = mod
                    break
                    
            if qzone_utils_mod:
                orig_download_file = qzone_utils_mod.download_file
                async def patched_download_file(url: str):
                    if isinstance(url, str) and url.startswith("local_path::"):
                        real_path = url.split("::", 1)[1]
                        try:
                            async with aiofiles.open(real_path, "rb") as f:
                                return await f.read()  
                        except Exception:
                            return None
                    return await orig_download_file(url)
                qzone_utils_mod.download_file = patched_download_file
                
            try:
                await qzone_plugin.service.publish_post(
                    text=clean_qzone_content,
                    images=qzone_images
                )
                logger.info("[DailySharing] 成功分享内容到QQ空间！")
                
                await self.db.add_sent_history(
                    target_id="qzone_broadcast",
                    sharing_type=stype.value,
                    content=clean_qzone_content[:100] + "...",
                    success=True
                )
                
                if event:
                    try:
                        text_chain = MessageChain().message(clean_qzone_content)
                        await event.send(text_chain)
                        
                        if target_local_img:
                            await asyncio.sleep(1.0) 
                            img_chain = MessageChain()
                            if target_local_img.startswith("http"):
                                img_chain.url_image(target_local_img)
                            else:
                                img_chain.file_image(target_local_img)
                            await event.send(img_chain)
                    except Exception as e:
                        logger.error(f"[DailySharing] 同步发送内容到会话失败: {e}")
                
            finally:
                if qzone_utils_mod:
                    qzone_utils_mod.download_file = orig_download_file

        except Exception as e:
            logger.error(f"[DailySharing] 生成并分享到QQ空间失败: {e}")
            if event:
                try:
                    await event.send(event.plain_result(f"生成并分享到QQ空间失败: {e}"))
                except:
                    pass

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
            yield event.plain_result("指令格式错误，请指定参数。\n示例：/分享 新闻\n可加后缀：广播、空间")
            return
            
        arg = parts[1].lower()
        
        # 判断后缀模式
        is_broadcast = "广播" in parts
        is_qzone_target = "空间" in parts  # 判断是否指向QQ空间
        
        current_uid = event.unified_msg_origin
        specific_target = None if is_broadcast else current_uid

        # =============== 手动触发 60s 新闻 ===============
        if arg == "60s":
            url = self.news_service.get_60s_image_url()
            if not url:
                yield event.plain_result("获取60s新闻失败，请检查API Key配置。")
                return
                
            if is_qzone_target:
                yield event.plain_result("正在分享每天60s读世界到QQ空间...")
                qzone_plugin = self.ctx_service._find_plugin("qzone")
                if qzone_plugin and hasattr(qzone_plugin, "service"):
                    self._inject_qzone_client(qzone_plugin)
                    try:
                        await qzone_plugin.service.publish_post(text="【每天60秒读懂世界】", images=[url])
                        yield event.plain_result("每天60s读世界已成功分享到QQ空间！")
                        await self.db.add_sent_history("qzone_broadcast", "news", "【每天60秒读懂世界】(手动)", True)
                    except Exception as e:
                        yield event.plain_result(f"QQ空间分享失败: {e}")
                else:
                    yield event.plain_result("未检测到QQ空间插件！")
                return

            target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
            yield event.plain_result(f"正在向{target_desc}分享60s新闻...")
            targets = [specific_target] if specific_target else self._get_broadcast_targets()
            for target in targets:
                await self.context.send_message(target, MessageChain().url_image(url))
                await asyncio.sleep(1)
            return

        # =============== 手动触发AI资讯 ===============
        if arg == "ai":
            url = self.news_service.get_ai_news_image_url()
            if not url:
                yield event.plain_result("获取AI资讯失败，请检查API Key配置。")
                return

            if is_qzone_target:
                yield event.plain_result("正在分享AI资讯快报到QQ空间...")
                qzone_plugin = self.ctx_service._find_plugin("qzone")
                if qzone_plugin and hasattr(qzone_plugin, "service"):
                    self._inject_qzone_client(qzone_plugin)
                    try:
                        await qzone_plugin.service.publish_post(text="【AI资讯快报】", images=[url])
                        yield event.plain_result("AI资讯快报已成功分享到QQ空间！")
                        await self.db.add_sent_history("qzone_broadcast", "news", "【AI资讯快报】(手动)", True)
                    except Exception as e:
                        yield event.plain_result(f"QQ空间分享失败: {e}")
                else:
                    yield event.plain_result("未检测到QQ空间插件！")
                return

            target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
            yield event.plain_result(f"正在向{target_desc}分享AI资讯...")
            targets = [specific_target] if specific_target else self._get_broadcast_targets()
            for target in targets:
                await self.context.send_message(target, MessageChain().url_image(url))
                await asyncio.sleep(1)
            return
        
        # =============== 配置命令 ===============
        if arg == "早报空间":
            async for res in self._cmd_briefing_qzone_sync(event, parts): yield res
            return
        elif arg == "状态":
            async for res in self._cmd_status(event): yield res
            return
        elif arg == "开启":
            async for res in self._cmd_enable(event): yield res
            return
        elif arg == "关闭":
            async for res in self._cmd_disable(event): yield res
            return
        elif arg == "重置序列":
            async for res in self._cmd_reset_seq(event): yield res
            return
        elif arg == "查看序列":
            async for res in self._cmd_view_seq(event): yield res
            return
        elif arg == "帮助":
            async for res in self._cmd_help(event): yield res
            return
        elif arg == "指定序列":
            async for res in self._cmd_set_seq(event, parts): yield res
            return

        # =============== 自动或具体类型生成 ===============
        if arg in ["自动", "auto"]:
            if is_qzone_target:
                yield event.plain_result("正在向QQ空间生成并分享内容(自动类型)...")
                await self._execute_qzone_share(None, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享内容(自动类型)...")
                await self._execute_share(None, specific_target=specific_target)
            return

        else:
            force_type = None
            if arg in CMD_CN_MAP:
                force_type = CMD_CN_MAP[arg]
            else:
                try:
                    force_type = SharingType(arg)
                except ValueError:
                    yield event.plain_result(f"未知指令或无效类型: {arg}\n可用: 问候, 新闻, 心情, 知识, 推荐, 60s, ai")
                    return

            type_cn = TYPE_CN_MAP.get(force_type.value, arg)
            
            if force_type == SharingType.NEWS:
                news_src = None
                is_image_mode = "图片" in parts
                
                for p in parts[2:]:
                    if p in ["图片", "广播", "空间"]: continue 
                    if p in SOURCE_CN_MAP:
                        news_src = SOURCE_CN_MAP[p]
                        break
                    elif p in NEWS_SOURCE_MAP:
                        news_src = p
                        break
                        
                if is_image_mode:
                    if not news_src: news_src = self.news_service.select_news_source()
                    img_url, src_name = self.news_service.get_hot_news_image_url(news_src)
                    
                    if is_qzone_target:
                        yield event.plain_result(f"正在获取[{src_name}]图片并分享到QQ空间...")
                        qzone_plugin = self.ctx_service._find_plugin("qzone")
                        if qzone_plugin and hasattr(qzone_plugin, "service"):
                            self._inject_qzone_client(qzone_plugin)
                            try:
                                await qzone_plugin.service.publish_post(text=f"【{src_name}】", images=[img_url])
                                yield event.plain_result("QQ空间分享成功！")
                                await self.db.add_sent_history("qzone_broadcast", "news", f"【{src_name}】长图(手动)", True)
                            except Exception as e:
                                yield event.plain_result(f"QQ空间分享失败: {e}")
                        else:
                            yield event.plain_result("未检测到QQ空间插件！")
                        return

                    yield event.plain_result(f"正在获取 [{src_name}] 图片...")
                    yield event.image_result(img_url)
                    return
                    
                src_info = f" ({NEWS_SOURCE_MAP[news_src]['name']})" if news_src else ""
                
                if is_qzone_target:
                    yield event.plain_result(f"正在向QQ空间生成并分享{type_cn}{src_info} ...")
                    await self._execute_qzone_share(force_type, news_source=news_src, event=event)
                else:
                    target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                    yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn}{src_info} ...")
                    await self._execute_share(force_type, news_source=news_src, specific_target=specific_target)
                return
                
            if is_qzone_target:
                yield event.plain_result(f"正在向QQ空间生成并分享{type_cn} ...")
                await self._execute_qzone_share(force_type, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn} ...")
                await self._execute_share(force_type, specific_target=specific_target)

    def _get_broadcast_targets(self):
        """辅助方法：获取需要广播的目标列表"""
        targets = []
        default_adapter_id = self._cached_adapter_id or "aiocqhttp"
        
        r_groups = self.receiver_conf.get("groups", [])
        if not isinstance(r_groups, list): r_groups = []
        r_users = self.receiver_conf.get("users", [])
        if not isinstance(r_users, list): r_users = []

        for gid in r_groups:
            if gid: targets.append(f"{default_adapter_id}:GroupMessage:{gid}")
        for uid in r_users:
            if uid: targets.append(f"{default_adapter_id}:FriendMessage:{uid}")
        return targets

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
        # 重置群聊/私聊的指针
        updates = {"sequence_index": 0, "last_period": None}
        for p in TimePeriod:
            updates[f"index_{p.value}"] = 0
        await self.db.update_state_dict("global", updates)
        
        # 重置QQ空间的指针
        qzone_updates = {"sequence_index": 0, "last_period": None}
        for p in TimePeriod:
            qzone_updates[f"index_{p.value}"] = 0
        await self.db.update_state_dict("qzone", qzone_updates)
        
        yield event.plain_result("群聊与空间的序列指针均已重置")

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
        
        # 读取当前时段的独立索引
        idx_key = f"index_{period.value}"
        idx = state.get(idx_key, 0)
        
        txt = f"当前时段: {period.value} ({time_range})\n"
        for i, t_raw in enumerate(seq):
            mark = "👉 " if i == idx else "   "
            t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
            txt += f"{mark}{i}. {t_cn}\n"
        yield event.plain_result(txt)

    async def _cmd_set_seq(self, event, parts):
        """指定序列子命令"""
        if len(parts) > 2 and parts[2].isdigit():
            target_idx = int(parts[2])
            period = self._get_curr_period()
            
            # 判断是否是在调QQ空间的序列
            is_qzone = "空间" in parts
            conf_node = self.qzone_conf if is_qzone else self.basic_conf
            state_key = "qzone" if is_qzone else "global"
            prefix = "qzone_" if is_qzone else ""
            
            config_key_map = {
                TimePeriod.MORNING: f"{prefix}morning_sequence",
                TimePeriod.FORENOON: f"{prefix}forenoon_sequence",
                TimePeriod.AFTERNOON: f"{prefix}afternoon_sequence",
                TimePeriod.EVENING: f"{prefix}evening_sequence",
                TimePeriod.NIGHT: f"{prefix}night_sequence",
                TimePeriod.LATE_NIGHT: f"{prefix}late_night_sequence",
                TimePeriod.DAWN: f"{prefix}dawn_sequence"
            }
            config_key = config_key_map.get(period)
            seq = conf_node.get(config_key, [])
            if not seq:
                seq = SHARING_TYPE_SEQUENCES.get(period, [])

            if 0 <= target_idx < len(seq):
                # 更新当前时段的独立索引
                idx_key = f"index_{period.value}"
                await self.db.update_state_dict(state_key, {
                    idx_key: target_idx,
                    "sequence_index": target_idx,
                    "last_period": period.value 
                })
                t_raw = seq[target_idx]
                t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                target_desc = "QQ空间" if is_qzone else "日常分享"
                yield event.plain_result(f"已切换[{target_desc}]下一次自动分享：{target_idx}. {t_cn}")
            else:
                yield event.plain_result(f"序号无效，当前时段[{period.value}] 范围: 0 ~ {len(seq)-1}")
        else:
            yield event.plain_result("格式错误。例如：/分享 指定序列 1\n可加后缀：空间")

    async def _cmd_briefing_qzone_sync(self, event: AstrMessageEvent, parts: list):
        """开启/关闭 分享早报到QQ空间"""
        if len(parts) > 2 and parts[2] in ["开启", "关闭"]:
            enable = (parts[2] == "开启")
            self.extra_shares_conf["sync_briefing_to_qzone"] = enable
            self.config["extra_shares"] = self.extra_shares_conf
            await self._save_config_file()
            yield event.plain_result(f"✅ 定时早报自动同步QQ空间功能已【{parts[2]}】。")
        else:
            status = "开启" if self.extra_shares_conf.get("sync_briefing_to_qzone", False) else "关闭"
            yield event.plain_result(f"ℹ️ 当前分享早报到QQ空间状态为: 【{status}】\n提示：发送 /分享 早报空间 开启/关闭 来切换。")

    async def _cmd_help(self, event: AstrMessageEvent):
        """帮助菜单"""
        yield event.plain_result("""每日分享插件帮助:
/分享 [类型] - 立即在当前会话生成分享 (默认文字模式)
支持类型: 问候、新闻、心情、知识、推荐、60s、ai

【可用后缀】
 1. 广播：/分享 [类型] 广播 - 向所有配置的群聊、私聊发送
 2. 空间：/分享 [类型] 空间 - 单独生成文案并分享到QQ空间
 3. 图片：/分享 新闻 [源] 图片 - 直接分享热搜图片
 
【配置指令】
/分享 开启/关闭 - 启停自动分享
/分享 早报空间 开启/关闭 - 启停自动分享早报到QQ空间
/分享 状态 - 查看运行状态
/分享 查看序列 - 查看当前时段序列及指针
/分享 指定序列 [序号] - 调整分享内容指针位置 (支持加后缀 空间)
/分享 重置序列 - 重置当前分享内容序列到开头""")
