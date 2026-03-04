import asyncio
import json
import random
import os
import re 
from typing import Optional
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig

from .config import TimePeriod, SharingType, NEWS_SOURCE_MAP
from .core.constants import CMD_CN_MAP, SOURCE_CN_MAP, TYPE_CN_MAP
from .core.news import NewsService
from .core.image import ImageService
from .core.content import ContentService
from .core.context import ContextService
from .core.db import DatabaseManager 
from .core.tasks import TaskManager
from .core.commands import CommandHandler

class DailySharingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config 
        self.scheduler = AsyncIOScheduler()
        
        # 配置引用
        self.basic_conf = self.config.get("basic_conf", {})
        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.qzone_conf = self.config.get('qzone_conf', {})
        self.receiver_conf = self.config.get("receiver", {})
        self.extra_shares_conf = self.config.get("extra_shares", {})
        self.context_conf = self.config.get("context_conf", {})
        
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
        
        # 核心逻辑解耦器
        self.task_manager = TaskManager(self)
        self.command_handler = CommandHandler(self)
        
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

        # 通过 TaskManager 挂载所有定时任务
        self.task_manager.setup_tasks()
        
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
            source(string): 仅当 share_type 为'新闻'时有效。指定新闻平台。支持：微博, 知乎, B站, 抖音, 头条, 百度, 腾讯, 小红书, 夸克, 36氪, 51CTO, A站, 爱范儿, 网易, 新浪, 澎湃, 第一财经。如果不指定则留空。
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
            self.task_manager.async_daily_share_task(
                event, share_type, source, get_image, need_image, need_video, need_voice, to_qzone
            )
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

        # 3. 直接返回空字符串，让 LLM 闭嘴，不再生成回复
        return None

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
            targets = [specific_target] if specific_target else self.task_manager.get_broadcast_targets()
            for target in targets:
                await self.context.send_message(target, MessageChain().url_image(url))
                await asyncio.sleep(1)
            return

        # =============== 手动触发AI资讯 ===============
        if arg == "ai":
            # 先拦截检测
            ai_data = await self.news_service.get_ai_news_json()
            if not ai_data:
                yield event.plain_result("获取AI资讯失败或今日暂无更新。")
                return

            url = self.news_service.get_ai_news_image_url()
            if not url:
                yield event.plain_result("获取AI资讯图片失败，请检查API Key配置。")
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
            targets = [specific_target] if specific_target else self.task_manager.get_broadcast_targets()
            for target in targets:
                await self.context.send_message(target, MessageChain().url_image(url))
                await asyncio.sleep(1)
            return
        
        # =============== 配置命令 ===============
        if arg == "早报空间":
            async for res in self.command_handler.cmd_briefing_qzone_sync(event, parts): yield res
            return
        elif arg == "状态":
            async for res in self.command_handler.cmd_status(event): yield res
            return
        elif arg == "开启":
            async for res in self.command_handler.cmd_enable(event): yield res
            return
        elif arg == "关闭":
            async for res in self.command_handler.cmd_disable(event): yield res
            return
        elif arg == "重置序列":
            async for res in self.command_handler.cmd_reset_seq(event): yield res
            return
        elif arg == "查看序列":
            async for res in self.command_handler.cmd_view_seq(event): yield res
            return
        elif arg == "帮助":
            async for res in self.command_handler.cmd_help(event): yield res
            return
        elif arg == "指定序列":
            async for res in self.command_handler.cmd_set_seq(event, parts): yield res
            return

        # =============== 自动或具体类型生成 ===============
        if arg in ["自动", "auto"]:
            if is_qzone_target:
                yield event.plain_result("正在向QQ空间生成并分享内容(自动类型)...")
                await self.task_manager.execute_qzone_share(None, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享内容(自动类型)...")
                await self.task_manager.execute_share(None, specific_target=specific_target)
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
                    await self.task_manager.execute_qzone_share(force_type, news_source=news_src, event=event)
                else:
                    target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                    yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn}{src_info} ...")
                    await self.task_manager.execute_share(force_type, news_source=news_src, specific_target=specific_target)
                return
                
            if is_qzone_target:
                yield event.plain_result(f"正在向QQ空间生成并分享{type_cn} ...")
                await self.task_manager.execute_qzone_share(force_type, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn} ...")
                await self.task_manager.execute_share(force_type, specific_target=specific_target)
                