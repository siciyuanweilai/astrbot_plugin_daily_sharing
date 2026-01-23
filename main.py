
import asyncio
import json
import random
import os
from functools import partial
from datetime import datetime
from pathlib import Path
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import Record
from .config import TimePeriod, SharingType, SHARING_TYPE_SEQUENCES, CRON_TEMPLATES, NEWS_SOURCE_MAP
from .core.news import NewsService
from .core.image import ImageService
from .core.content import ContentService
from .core.context import ContextService

# 类型汉化映射表
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
    "知乎": "zhihu", "微博": "weibo", "B站": "bili", 
    "小红书": "xiaohongshu", "抖音": "douyin", 
    "头条": "toutiao", "百度": "baidu", "腾讯": "tencent"
})

@register("daily_sharing", "四次元未来", "定时主动分享所见所闻", "1.0.0")
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
        
        # 运行日志记录条数 (默认50)
        self.history_limit = int(self.basic_conf.get("history_limit", 50))
        # 内容去重历史记录条数 (默认20)
        self.topic_history_limit = int(self.basic_conf.get("topic_history_limit", 20))
        
        # 锁与防抖
        self._lock = asyncio.Lock()
        self._last_share_time = None
        
        # 数据路径
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_daily_sharing")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置文件路径
        config_dir = self.data_dir.parent.parent / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = config_dir / "astrbot_plugin_daily_sharing_config.json"
        
        self.state_file = self.data_dir / "sharing_state.json"
        self.history_file = self.data_dir / "sharing_history.json"
        
        # 历史记录缓存
        self.sharing_history = []
        
        # 初始化服务层
        self.ctx_service = ContextService(context, config)
        self.news_service = NewsService(config)
        self.image_service = ImageService(context, config, self._call_llm_wrapper)
        
        # 初始化 ContentService，传入 topic_history_limit
        self.content_service = ContentService(
            config, 
            self._call_llm_wrapper, 
            context,
            str(self.state_file),
            self.news_service,
            topic_history_limit=self.topic_history_limit # 传递配置
        )

    async def initialize(self):
        """初始化插件"""
        self.sharing_history = await self._load_history() 
        asyncio.create_task(self._delayed_init())

    async def terminate(self):
        """插件卸载/重载时的清理逻辑"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
            logger.info("[DailySharing] 🛑 旧的定时任务调度器已停止")
        except Exception as e:
            logger.error(f"[DailySharing] 停止插件出错: {e}")        

    async def _delayed_init(self):
        """延迟初始化逻辑"""
        await asyncio.sleep(3)
        
        has_targets = self.receiver_conf.get("groups") or self.receiver_conf.get("users")
        
        if not has_targets:
            logger.warning("[DailySharing] ⚠️ 未配置接收对象 (receiver)")

        if self.config.get("enable_auto_sharing", False):
            cron = self.basic_conf.get("sharing_cron", "0 8,20 * * *")
            self._setup_cron(cron)
            if not self.scheduler.running:
                self.scheduler.start()
            logger.info("[DailySharing] 定时任务已启动")
        else:
            logger.info("[DailySharing] 自动分享已禁用")

    # ==================== 核心逻辑 (LLM调用与任务) ====================

    @filter.llm_tool(name="daily_share")
    async def daily_share_tool(
        self, 
        event: AstrMessageEvent, 
        share_type: str, 
        source: str = None, 
        get_image: bool = False,
        need_image: bool = False,
        need_voice: bool = False
    ):
        """
        主动分享日常内容、新闻热搜、获取热搜图片等。
        当用户想要看新闻、热搜、早安晚安、冷知识、心情或推荐时调用此工具。

        Args:
            share_type(string): 分享类型。必须是以下之一：'问候', '新闻', '心情', '知识', '推荐'。
            source(string): 仅当 share_type 为'新闻'时有效。指定新闻平台。支持：微博, 知乎, B站, 抖音, 头条, 百度, 腾讯, 小红书。如果不指定则留空。
            get_image(boolean): 仅当 share_type 为'新闻'时有效。如果用户明确想看“图片”、“长图”或“截图”时设为 True。默认为 False (即只看文字摘要)。
            need_image(boolean): 是否需要AI为这段文案配图。默认为 False。仅当用户明确说“配图”、“带图”、“发张图”时，才将其设为 True。
            need_voice(boolean): 是否需要将文案转为语音(TTS)发送。默认为 False。仅当用户明确提到“语音”、“朗读”、“念给我听”时，设为 True。
        """
        
        # 1. 防抖检查
        request_id = f"share_{event.get_sender_id()}"
        if self._lock.locked():
             return "正如火如荼地准备中，请稍后..."
        
        # 2. 参数清洗与映射
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
                return f"不支持的分享类型：{share_type}。支持：问候, 新闻, 心情, 知识, 推荐。"

        # 映射新闻源 (中文 -> key)
        news_src_key = None
        if target_type_enum == SharingType.NEWS and source:
            # 尝试直接匹配
            if source in SOURCE_CN_MAP:
                news_src_key = SOURCE_CN_MAP[source]
            # 尝试在 map 的 values 中找 (处理 LLM 可能传英文 key 的情况)
            elif source in NEWS_SOURCE_MAP:
                news_src_key = source
            else:
                # 模糊匹配
                for name, key in SOURCE_CN_MAP.items():
                    if name in source or source in name:
                        news_src_key = key
                        break
        
        # 3. 执行逻辑
        try:
            # 场景 A: 获取新闻长图 (直接发送图片，不走 LLM 生成文本流程)
            if target_type_enum == SharingType.NEWS and get_image:
                # 如果没指定源，让 service 自动选一个
                if not news_src_key:
                    news_src_key = self.news_service.select_news_source()
                
                img_url, src_name = self.news_service.get_hot_news_image_url(news_src_key)
                
                # 发送图片
                await event.send(event.image_result(img_url))
                return f"已发送{src_name}图片。"

            # 场景 B: 标准流程 (生成文案 + 可选配图 + 可选语音)
            else:
                src_info = f" ({NEWS_SOURCE_MAP[news_src_key]['name']})" if news_src_key else ""
                
                # 获取上下文
                uid = event.get_sender_id()
                # 统一格式 adapter:type:id
                if not ":" in str(uid):
                    # 尝试从 event 构建标准 UMO ID
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
                    return "内容生成失败，请稍后再试。"
                
                # 生成配图/语音
                img_path = None
                if self.image_conf.get("enable_image", False) and need_image:
                    allowed = self.image_conf.get("image_enabled_types", [])
                    if target_type_enum.value in allowed:
                        img_path = await self.image_service.generate_image(content, target_type_enum, life_ctx)

                audio_path = None
                if self.tts_conf.get("enable_tts", False) and need_voice:
                    audio_path = await self.ctx_service.text_to_speech(content, target_umo, target_type_enum, period)

                # 发送
                await self._send(target_umo, content, img_path, audio_path)
                
                # 记录上下文
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_bot_reply_to_history(target_umo, content, image_desc=img_desc)
                await self.ctx_service.record_to_memos(target_umo, content, img_desc)
                
                return f"已成功分享{share_type}内容。"

        except Exception as e:
            logger.error(f"[DailySharing] Tool error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"执行出错: {str(e)}"

    async def _call_llm_wrapper(self, prompt: str, system_prompt: str = None, timeout: int = 60, max_retries: int = 2) -> Optional[str]:
        """LLM 调用包装器"""
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
                    logger.error(f"[DailySharing] ❌ 内容被模型安全策略拦截 (敏感词): {prompt[:50]}...")
                    return None 

                if "401" in str(e):
                    logger.error(f"[DailySharing] ❌ LLM 失败。请检查 API Key。")
                    return None
                
                logger.error(f"[DailySharing] LLM异常 (尝试 {attempt+1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue

        logger.error(f"[DailySharing] LLM调用失败（已重试{max_retries}次）")
        return None

    def _setup_cron(self, cron_str):
        """设置 Cron 任务"""
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
                logger.info(f"[DailySharing] 定时任务已设定: {actual_cron}")
            else:
                logger.error(f"[DailySharing] 无效的 Cron 表达式: {cron_str}")
        except Exception as e:
            logger.error(f"[DailySharing] 设置 Cron 失败: {e}")

    async def _task_wrapper(self):
        """任务包装器（防抖 + 锁 + 随机延迟）"""
        # === 随机延迟逻辑 ===
        try:
            # 从配置获取随机延迟分钟数，默认为 0
            random_delay_min = int(self.basic_conf.get("cron_random_delay", 0))
        except Exception:
            random_delay_min = 0

        if random_delay_min > 0:
            # 计算延迟秒数 (0 到 max*60)
            delay_seconds = random.randint(0, random_delay_min * 60)
            if delay_seconds > 0:
                trigger_time = datetime.now()
                expected_time = trigger_time.timestamp() + delay_seconds
                time_str = datetime.fromtimestamp(expected_time).strftime('%H:%M:%S')
                
                logger.info(f"[DailySharing] ⏰ 定时任务已触发，启用随机延迟策略。")
                logger.info(f"[DailySharing] ⏳ 将延迟 {delay_seconds/60:.1f} 分钟，预计于 {time_str} 执行...")
                
                # 异步等待，不阻塞主线程
                await asyncio.sleep(delay_seconds)

        # === 核心执行逻辑 ===
        now = datetime.now()
        
        # 防抖检查：如果最近 60 秒内已经执行过（比如手动触发了，或在上一个任务等待期间执行了）
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
                logger.info("[DailySharing] ⏳ 随机延迟结束，开始执行分享...")
            await self._execute_share()

    async def _execute_share(self, force_type: SharingType = None, news_source: str = None):
        """执行分享的主流程"""
        period = self._get_curr_period()
        if force_type:
            stype = force_type
        else:
            stype = await self._decide_type_with_state(period) 
        
        logger.info(f"[DailySharing] 时段: {period.value}, 类型: {stype.value}")

        life_ctx = await self.ctx_service.get_life_context()
        news_data = None
        if stype == SharingType.NEWS:
            news_data = await self.news_service.get_hot_news(news_source)

        targets = []
        adapter_id = self.receiver_conf.get("adapter_id", "QQ")
        for gid in self.receiver_conf.get("groups", []):
            if gid:
                targets.append(f"{adapter_id}:GroupMessage:{gid}")
        for uid in self.receiver_conf.get("users", []):
            if uid:
                targets.append(f"{adapter_id}:FriendMessage:{uid}")
        if not targets:
            logger.warning("[DailySharing] ⚠️ 未配置接收对象，请在配置页填写群号或QQ号")
            return

        for uid in targets:
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower() or "guild" in uid.lower()
                
                hist_data = await self.ctx_service.get_history_data(uid, is_group)
                if is_group and "group_info" in hist_data:
                    if not self.ctx_service.check_group_strategy(hist_data["group_info"]):
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
                    await self._append_history({
                        "timestamp": datetime.now().isoformat(),
                        "target": uid,
                        "type": stype.value,
                        "content": "❌ 生成失败 (LLM无响应)",
                        "success": False
                    })
                    continue
                
                # --- 生成多媒体素材 (图片 & 语音) ---
                
                # 1. 配图生成逻辑
                img_path = None
                enable_img_global = self.image_conf.get("enable_ai_image", False)
                img_allowed_types = self.image_conf.get("image_enabled_types", ["greeting", "mood", "knowledge", "recommendation"])
                
                if enable_img_global:
                    if stype.value in img_allowed_types:
                        img_path = await self.image_service.generate_image(content, stype, life_ctx)
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

                # --- 发送消息 ---
                await self._send(uid, content, img_path, audio_path)
                
                # --- 获取图片描述并写入 AstrBot 聊天上下文 ---
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_bot_reply_to_history(uid, content, image_desc=img_desc)

                # --- 记录与历史 ---
                await self.ctx_service.record_to_memos(uid, content, img_desc)

                await self._append_history({
                    "timestamp": datetime.now().isoformat(),
                    "target": uid,
                    "type": stype.value,
                    "content": content[:50] + "...",
                    "success": True
                })
                
                await asyncio.sleep(2) 

            except Exception as e:
                logger.error(f"[DailySharing] 处理 {uid} 时出错: {e}")
                import traceback
                logger.error(traceback.format_exc())

    async def _send(self, uid, text, img_path, audio_path=None):
        """发送消息（支持分开发送，支持语音）"""
        try:
            separate_img = self.image_conf.get("separate_text_and_image", True)
            prefer_audio_only = self.tts_conf.get("prefer_audio_only", False)
            
            # 判断是否应该发送文字
            # 如果有语音，且开启了“仅发语音”，则不发文字
            should_send_text = True
            if audio_path and prefer_audio_only:
                should_send_text = False

            # 1. 发送文字（如果需要）
            if should_send_text:
                text_chain = MessageChain().message(text)
                # 如果图片不分开发送，且没有语音（因为如果有语音，图片最好单独发），则合并图片
                if img_path and not separate_img and not audio_path:
                    if img_path.startswith("http"): text_chain.url_image(img_path)
                    else: text_chain.file_image(img_path)
                
                await self.context.send_message(uid, text_chain)
                
                # 如果后续还有消息，进行随机延迟
                if audio_path or (img_path and separate_img):
                    await self._random_sleep()

            # 2. 发送语音（如果有）
            if audio_path:
                audio_chain = MessageChain()
                audio_chain.chain.append(Record(file=audio_path))
                await self.context.send_message(uid, audio_chain)
                
                # 如果后续还有图片，延迟
                if img_path and separate_img:
                    await self._random_sleep()
            
            # 3. 发送图片（如果需要单独发送，或者因为有语音而被迫单独发送）
            # 逻辑：只要图片还没发（separate_img 为真，或者虽然 separate_img 为假但因为有语音没能合并），就发
            img_not_sent_yet = img_path and (separate_img or audio_path)
            
            if img_not_sent_yet:
                img_chain = MessageChain()
                if img_path.startswith("http"): img_chain.url_image(img_path)
                else: img_chain.file_image(img_path)
                await self.context.send_message(uid, img_chain)

        except Exception as e:
            logger.error(f"[DailySharing] 发送消息给 {uid} 失败: {e}")

    async def _random_sleep(self):
        """随机延迟"""
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
        return TimePeriod.NIGHT

    @staticmethod
    def _read_json_sync(path):
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    @staticmethod
    def _write_json_sync(path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def _load_state(self) -> dict:
        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, self._read_json_sync, self.state_file)
            return data if data else {"sequence_index": 0, "last_period": None}
        except Exception: 
            return {"sequence_index": 0, "last_period": None}

    async def _save_state(self, state):
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.state_file, state)
        except Exception: pass

    async def _decide_type_with_state(self, current_period: TimePeriod) -> SharingType:
        conf_type = self.basic_conf.get("sharing_type", "auto")
        if conf_type != "auto":
            try: return SharingType(conf_type)
            except: pass
        state = await self._load_state() 
        
        if state.get("last_period") != current_period.value:
            state["sequence_index"] = 0
        
        config_key_map = {
            TimePeriod.MORNING: "morning_sequence",
            TimePeriod.FORENOON: "forenoon_sequence",
            TimePeriod.AFTERNOON: "afternoon_sequence",
            TimePeriod.EVENING: "evening_sequence",
            TimePeriod.NIGHT: "night_sequence",
            TimePeriod.DAWN: "dawn_sequence"
        }
        
        config_key = config_key_map.get(current_period)
        seq = self.basic_conf.get(config_key, [])
        
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])
        
        idx = state.get("sequence_index", 0)
        if idx >= len(seq): idx = 0
        
        selected = seq[idx]
        
        state["last_period"] = current_period.value
        state["sequence_index"] = (idx + 1) % len(seq)
        state["last_timestamp"] = datetime.now().isoformat()
        state["last_type"] = selected
        
        await self._save_state(state) 
        
        try: return SharingType(selected)
        except: return SharingType.GREETING

    # ==================== 历史记录管理 ====================

    async def _load_history(self):
        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, self._read_json_sync, self.history_file)
            return data if data else []
        except: return []

    async def _append_history(self, record):
        self.sharing_history.append(record)
        # 使用配置中的 history_limit
        if len(self.sharing_history) > self.history_limit:
            self.sharing_history = self.sharing_history[-self.history_limit:]
        
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.history_file, self.sharing_history)
        except Exception as e:
            logger.error(f"[DailySharing] 保存历史记录失败: {e}")

    async def _save_config_file(self):
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_json_sync, self.config_file, self.config)
        except Exception as e:
            logger.error(f"[DailySharing] 保存配置失败: {e}")

    # ==================== 统一命令入口 ====================
    @filter.command("分享")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_share_main(self, event: AstrMessageEvent):
        """
        每日分享统一命令入口
        """
        msg = event.message_str.strip()
        parts = msg.split()
        
        if len(parts) == 1:
            yield event.plain_result("❌ 指令格式错误，请指定参数。")
            return
        arg = parts[1].lower()
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
            
        elif arg in ["自动", "auto"]:
            yield event.plain_result("正在生成并发送分享内容 (自动类型)...")
            await self._execute_share(None)
        else:
            if arg in CMD_CN_MAP:
                force_type = CMD_CN_MAP[arg]
                
                # ===== 新闻类型的特殊逻辑 (处理源和图片) =====
                if force_type == SharingType.NEWS:
                    news_src = None
                    is_image_mode = False
                    
                    # 检查参数中是否包含 "图片"
                    if "图片" in parts:
                        is_image_mode = True
                    
                    # 检查参数中是否包含 指定源
                    for p in parts[2:]:
                        if p == "图片": continue 
                        if p in SOURCE_CN_MAP:
                            news_src = SOURCE_CN_MAP[p]
                            break
                        elif p in NEWS_SOURCE_MAP:
                            news_src = p
                            break
                    # 如果是图片模式，直接发送图片，绕过 LLM
                    if is_image_mode:
                        img_url, src_name = self.news_service.get_hot_news_image_url(news_src)
                        yield event.plain_result(f"正在获取 [{src_name}] 图片...")
                        yield event.image_result(img_url)
                        return
                    # 正常的 LLM 文字新闻模式
                    type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                    src_info = f" ({NEWS_SOURCE_MAP[news_src]['name']})" if news_src else ""
                    yield event.plain_result(f"正在生成并发送分享 [{type_cn}{src_info}] ...")
                    await self._execute_share(force_type, news_source=news_src)
                    return
                # 其他类型 (问候/心情等)
                type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                yield event.plain_result(f"正在生成并发送分享 [{type_cn}] ...")
                await self._execute_share(force_type)
                return
            try:
                force_type = SharingType(arg)
                type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                yield event.plain_result(f"正在生成并发送分享 [{type_cn}] ...")
                await self._execute_share(force_type)
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
        
        yield event.plain_result("✅ 自动分享已启用")

    async def _cmd_disable(self, event: AstrMessageEvent):
        """禁用插件"""
        self.config["enable_auto_sharing"] = False
        await self._save_config_file()
        self.scheduler.remove_all_jobs()
        yield event.plain_result("❌ 自动分享已禁用")

    async def _cmd_status(self, event: AstrMessageEvent):
        """查看详细状态"""
        state = await self._load_state() 
        enabled = self.config.get("enable_auto_sharing", True)
        cron = self.basic_conf.get("sharing_cron")
        
        last_type_raw = state.get('last_type', '无')
        last_type_cn = TYPE_CN_MAP.get(last_type_raw, last_type_raw)

        hist_txt = "无记录"
        if self.sharing_history:
            lines = []
            for h in reversed(self.sharing_history[-3:]):
                ts = h.get("timestamp", "")[5:16].replace("T", " ")
                content_preview = h.get('content', '') or ""
                
                t_raw = h.get('type')
                t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
                
                lines.append(f"• {ts} [{t_cn}] {content_preview}")
            hist_txt = "\n".join(lines)

        msg = f"""📊 每日分享状态
================
运行状态: {'✅ 启用' if enabled else '❌ 禁用'}
Cron规则: {cron}
当前时段: {self._get_curr_period().value}

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
        await self._save_state({"sequence_index": 0, "last_period": None})
        yield event.plain_result("✅ 序列已重置")

    async def _cmd_view_seq(self, event: AstrMessageEvent):
        """查看序列详情"""
        period = self._get_curr_period()
        config_key_map = {
            TimePeriod.MORNING: "morning_sequence",
            TimePeriod.AFTERNOON: "afternoon_sequence",
            TimePeriod.EVENING: "evening_sequence",
            TimePeriod.NIGHT: "night_sequence",
            TimePeriod.DAWN: "dawn_sequence"
        }
        config_key = config_key_map.get(period)
        seq = self.basic_conf.get(config_key, [])
        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(period, [])

        state = await self._load_state()
        idx = state.get("sequence_index", 0)
        
        txt = f"🔄 当前时段: {period.value}\n"
        for i, t_raw in enumerate(seq):
            mark = "👉 " if i == idx else "   "
            t_cn = TYPE_CN_MAP.get(t_raw, t_raw)
            txt += f"{mark}{i}. {t_cn}\n"
        yield event.plain_result(txt)

    async def _cmd_help(self, event: AstrMessageEvent):
        """帮助菜单"""
        yield event.plain_result("""📚 每日分享插件帮助:
/分享 [类型] - 立即执行 (类型: 问候/新闻/心情/知识/推荐)
/分享 新闻 [源] - 获取指定平台热搜 (如: 微博/B站/头条/百度)
/分享 新闻 [源] 图片 - 获取热搜长图 (如: /分享 新闻 微博 图片)
/分享 状态 - 查看运行状态
/分享 开启 - 启用自动分享
/分享 关闭 - 禁用自动分享
/分享 重置序列 - 重置当前发送序列
/分享 查看序列 - 查看当前时段序列""")
