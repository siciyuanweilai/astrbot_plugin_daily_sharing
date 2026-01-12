# main.py
import asyncio
import json
import random
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import Record
from .config import TimePeriod, SharingType, SHARING_TYPE_SEQUENCES, CRON_TEMPLATES
from .services.news import NewsService
from .services.image import ImageService
from .services.content import ContentService
from .services.context import ContextService

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
        
        # 依赖注入：将 news_service 传给 content_service
        self.content_service = ContentService(
            config, 
            self._call_llm_wrapper, 
            context,
            str(self.state_file),
            self.news_service 
        )

    async def initialize(self):
        """初始化插件"""
        self.sharing_history = self._load_history()
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
        """任务包装器（防抖 + 锁）"""
        now = datetime.now()
        if self._last_share_time:
            if (now - self._last_share_time).total_seconds() < 5:
                return
        
        if self._lock.locked():
            return

        async with self._lock:
            self._last_share_time = now
            await self._execute_share()

    async def _execute_share(self, force_type: SharingType = None):
        """执行分享的主流程"""
        period = self._get_curr_period()
        if force_type:
            stype = force_type
        else:
            stype = self._decide_type_with_state(period)
        
        logger.info(f"[DailySharing] 时段: {period.value}, 类型: {stype.value}")

        life_ctx = await self.ctx_service.get_life_context()
        news_data = None
        if stype == SharingType.NEWS:
            news_data = await self.news_service.get_hot_news()

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
                    self._append_history({
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

                # --- 记录与历史 ---
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_to_memos(uid, content, img_desc)

                self._append_history({
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
        if 6 <= h < 12: return TimePeriod.MORNING
        if 12 <= h < 17: return TimePeriod.AFTERNOON
        if 17 <= h < 20: return TimePeriod.EVENING
        return TimePeriod.NIGHT

    def _load_state(self) -> dict:
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception: pass
        return {"sequence_index": 0, "last_period": None}

    def _save_state(self, state):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception: pass

    def _decide_type_with_state(self, current_period: TimePeriod) -> SharingType:
        conf_type = self.basic_conf.get("sharing_type", "auto")
        if conf_type != "auto":
            try: return SharingType(conf_type)
            except: pass

        state = self._load_state()
        
        if state.get("last_period") != current_period.value:
            state["sequence_index"] = 0
        
        config_key_map = {
            TimePeriod.MORNING: "morning_sequence",
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
        self._save_state(state)
        
        try: return SharingType(selected)
        except: return SharingType.GREETING

    # ==================== 历史记录管理 ====================

    def _load_history(self):
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except: pass
        return []

    def _append_history(self, record):
        self.sharing_history.append(record)
        if len(self.sharing_history) > 50:
            self.sharing_history = self.sharing_history[-50:]
        
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._write_history_sync)
        except Exception as e:
            logger.error(f"[DailySharing] 保存历史记录失败: {e}")

    def _write_history_sync(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.sharing_history, f, ensure_ascii=False, indent=2)
        except Exception: pass

    async def _save_config_file(self):
        try:
            if self.config_file.parent.exists():
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
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
            yield event.plain_result("🚀 正在生成并发送分享内容 (自动类型)...")
            await self._execute_share(None)
        else:
            if arg in CMD_CN_MAP:
                force_type = CMD_CN_MAP[arg]
                type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                yield event.plain_result(f"🚀 正在生成并发送 [{type_cn}] 分享...")
                await self._execute_share(force_type)
                return

            try:
                force_type = SharingType(arg)
                type_cn = TYPE_CN_MAP.get(force_type.value, arg)
                yield event.plain_result(f"🚀 正在生成并发送 [{type_cn}] 分享...")
                await self._execute_share(force_type)
            except ValueError:
                yield event.plain_result(f"❌ 未知指令或无效类型: {arg}\n可用类型: 问候, 新闻, 心情, 知识, 推荐")

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
        state = self._load_state()
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
        self._save_state({"sequence_index": 0, "last_period": None})
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

        state = self._load_state()
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
/分享 状态 - 查看运行状态
/分享 开启 - 启用自动分享
/分享 关闭 - 禁用自动分享
/分享 重置序列 - 重置当前发送序列
/分享 查看序列 - 查看当前时段序列""")
