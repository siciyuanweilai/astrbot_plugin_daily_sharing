# main.py
import asyncio
import json
import random
import os
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api import AstrBotConfig
from .config import TimePeriod, SharingType, SHARING_TYPE_SEQUENCES, NEWS_SOURCE_MAP
from .services.news import NewsService
from .services.image import ImageService
from .services.content import ContentService
from .services.context import ContextService

@register("daily_sharing", "四次元未来", "定时主动分享所见所闻", "1.0.0")
class DailySharingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        
        # 锁与防抖
        self._lock = asyncio.Lock()
        self._last_share_time = None
        
        # 数据路径
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_daily_sharing")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_file = self.data_dir / "sharing_state.json"
        self.history_file = self.data_dir / "sharing_history.json"
        
        # 历史记录缓存
        self.sharing_history = []
        
        # 初始化服务层
        self.ctx_service = ContextService(context, config)
        self.news_service = NewsService(config)
        self.image_service = ImageService(context, config, self._call_llm_wrapper)
        self.content_service = ContentService(config, self._call_llm_wrapper, context)

    async def initialize(self):
        """初始化插件"""
        # 加载历史记录
        self.sharing_history = self._load_history()
        
        # 延迟初始化
        asyncio.create_task(self._delayed_init())

    async def _delayed_init(self):
        """延迟初始化逻辑"""
        await asyncio.sleep(3)
        
        # 检查配置
        if not self.config.get("target_users", []):
            logger.warning("[DailySharing] ⚠️ 未配置接收对象 (target_users)")

        # 启动调度器
        if self.config.get("enable_auto_sharing", True):
            cron = self.config.get("sharing_cron", "0 8,20 * * *")
            self._setup_cron(cron)
            if not self.scheduler.running:
                self.scheduler.start()
            logger.info("[DailySharing] 定时任务已启动")
        else:
            logger.info("[DailySharing] 自动分享已禁用")

    # ==================== 核心逻辑 ====================

    async def _call_llm_wrapper(self, prompt, system_prompt=None, timeout=60):
        """LLM 调用包装器（供 Service 层使用）"""
        provider_id = self.config.get("llm_provider_id", "")
        
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

        try:
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    prompt=prompt, 
                    system_prompt=system_prompt, 
                    chat_provider_id=provider_id if provider_id else None
                ),
                timeout=timeout
            )
            return resp.completion_text if resp else None
        except Exception as e:
            logger.error(f"[DailySharing] LLM Error: {e}")
            return None

    def _setup_cron(self, cron_str):
        """设置 Cron 任务"""
        try:
            if self.scheduler.get_job("auto_share"):
                self.scheduler.remove_job("auto_share")

            # 预设模板支持
            templates = {
                "morning": "0 8 * * *", 
                "noon": "0 12 * * *", 
                "evening": "0 19 * * *", 
                "night": "0 22 * * *", 
                "twice": "0 8,20 * * *"
            }
            actual_cron = templates.get(cron_str, cron_str)
            parts = actual_cron.split()
            
            if len(parts) == 5:
                self.scheduler.add_job(
                    self._task_wrapper, 'cron',
                    minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
                    id="auto_share",
                    replace_existing=True,
                    max_instances=1  
                )
                logger.info(f"[DailySharing] Cron set: {actual_cron}")
            else:
                logger.error(f"[DailySharing] Invalid cron: {cron_str}")
        except Exception as e:
            logger.error(f"[DailySharing] Cron setup failed: {e}")

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
        
        # 确定时间段和类型
        period = self._get_curr_period()
        if force_type:
            stype = force_type
        else:
            stype = self._decide_type_with_state(period)
        
        logger.info(f"[DailySharing] Period: {period.value}, Type: {stype.value}")

        # 获取全局上下文
        life_ctx = await self.ctx_service.get_life_context()
        news_data = None
        if stype == SharingType.NEWS:
            news_data = await self.news_service.get_hot_news()

        # 遍历目标用户
        targets = self.config.get("target_users", [])
        for uid in targets:
            try:
                is_group = "group" in uid.lower() or "room" in uid.lower()
                
                # 获取聊天历史 & 群策略检查
                hist_data = await self.ctx_service.get_history_data(uid, is_group)
                if is_group and "group_info" in hist_data:
                    if not self.ctx_service.check_group_strategy(hist_data["group_info"]):
                        logger.info(f"[DailySharing] Skip group {uid} due to strategy")
                        continue

                # 格式化 Prompt
                hist_prompt = self.ctx_service.format_history_prompt(hist_data, stype)
                group_info = hist_data.get("group_info")
                life_prompt = self.ctx_service.format_life_context(life_ctx, stype, is_group, group_info)

                # 生成文本
                logger.info(f"[DailySharing] Generating content for {uid}...")
                content = await self.content_service.generate(
                    stype, period, uid, is_group, life_prompt, hist_prompt, news_data
                )
                
                if not content:
                    logger.warning(f"[DailySharing] Content gen failed for {uid}")
                    continue

                # 生成图片
                img_path = None
                if self.config.get("enable_ai_image", False):
                    img_path = await self.image_service.generate_image(content, stype, life_ctx)

                # 发送消息
                await self._send(uid, content, img_path)

                # 记录记忆 (Memos)
                img_desc = self.image_service.get_last_description()
                await self.ctx_service.record_to_memos(uid, content, img_desc)

                # 记录到本地历史文件 
                self._append_history({
                    "timestamp": datetime.now().isoformat(),
                    "target": uid,
                    "type": stype.value,
                    "content": content[:50] + "...",
                    "success": True
                })
                
                await asyncio.sleep(2) 

            except Exception as e:
                logger.error(f"[DailySharing] Error processing {uid}: {e}")
                
        logger.info("[DailySharing] <<< Execution finished")

    async def _send(self, uid, text, img_path):
        """发送消息（支持分开发送）"""
        chain = MessageChain().message(text)
        
        if img_path:
            if self.config.get("separate_text_and_image", True):
                # 分开发送
                await self.context.send_message(uid, chain)
                await asyncio.sleep(random.uniform(1.0, 2.0))
                
                img_chain = MessageChain()
                if img_path.startswith("http"): img_chain.url_image(img_path)
                else: img_chain.file_image(img_path)
                await self.context.send_message(uid, img_chain)
            else:
                # 合并发送
                if img_path.startswith("http"): chain.url_image(img_path)
                else: chain.file_image(img_path)
                await self.context.send_message(uid, chain)
        else:
            await self.context.send_message(uid, chain)

    # ==================== 状态管理 ====================

    def _get_curr_period(self) -> TimePeriod:
        h = datetime.now().hour
        if 0 <= h < 6: return TimePeriod.DAWN
        if 6 <= h < 11: return TimePeriod.MORNING
        if 11 <= h < 17: return TimePeriod.AFTERNOON
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
        # 如果配置强制指定类型
        conf_type = self.config.get("sharing_type", "auto")
        if conf_type != "auto":
            try: return SharingType(conf_type)
            except: pass

        state = self._load_state()
        
        # 如果时段变了，重置索引
        if state.get("last_period") != current_period.value:
            state["sequence_index"] = 0
        
        # 获取序列
        seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])
        idx = state.get("sequence_index", 0)
        
        if idx >= len(seq): idx = 0
        
        selected = seq[idx]
        
        # 更新状态
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
        """添加历史并保存文件"""
        self.sharing_history.append(record)
        if len(self.sharing_history) > 50:
            self.sharing_history = self.sharing_history[-50:]
        
        # 异步写入文件
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.sharing_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[DailySharing] Save history failed: {e}")

    async def _save_config_file(self):
        """保存配置到文件 (用于 enable/disable 命令)"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[DailySharing] Save config failed: {e}")

    # ==================== 命令系统 ====================

    @filter.command("share_now")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_share_now(self, event: AstrMessageEvent):
        """立即触发分享 """
        event.stop_event()
        
        msg = event.message_str.strip()
        parts = msg.split()
        force_type = None
        
        if len(parts) > 1:
            try:
                force_type = SharingType(parts[1].lower())
            except ValueError:
                yield event.plain_result(f"❌ 无效类型。可用: {', '.join([t.value for t in SharingType])}")
                return

        await self._execute_share(force_type)

    @filter.command("share_enable")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_enable(self, event: AstrMessageEvent):
        """启用插件"""
        self.config["enable_auto_sharing"] = True
        await self._save_config_file()
        
        cron = self.config.get("sharing_cron", "0 8,20 * * *")
        self._setup_cron(cron)
        if not self.scheduler.running: self.scheduler.start()
        
        yield event.plain_result("✅ 自动分享已启用")

    @filter.command("share_disable")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_disable(self, event: AstrMessageEvent):
        """禁用插件"""
        self.config["enable_auto_sharing"] = False
        await self._save_config_file()
        self.scheduler.remove_all_jobs()
        yield event.plain_result("❌ 自动分享已禁用")

    @filter.command("share_status")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_status(self, event: AstrMessageEvent):
        """查看详细状态"""
        # 读取状态文件
        state = self._load_state()
        
        enabled = self.config.get("enable_auto_sharing", True)
        cron = self.config.get("sharing_cron")
        
        # 构建历史预览
        hist_txt = "无记录"
        if self.sharing_history:
            lines = []
            for h in reversed(self.sharing_history[-3:]):
                ts = h.get("timestamp", "")[5:16].replace("T", " ")
                lines.append(f"• {ts} [{h.get('type')}] {h.get('content')}")
            hist_txt = "\n".join(lines)

        msg = f"""📊 Daily Sharing 状态
================
运行状态: {'✅ 启用' if enabled else '❌ 禁用'}
Cron规则: {cron}
当前时段: {self._get_curr_period().value}

【序列状态】
上次类型: {state.get('last_type', '无')}
上次时间: {state.get('last_timestamp', '无')[5:16].replace('T', ' ')}
序列索引: {state.get('sequence_index', 0)}

【最近记录】
{hist_txt}
"""
        yield event.plain_result(msg)

    @filter.command("share_reset_sequence")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_reset_seq(self, event: AstrMessageEvent):
        """重置序列"""
        self._save_state({"sequence_index": 0, "last_period": None})
        yield event.plain_result("✅ 序列已重置")

    @filter.command("share_set_image_behavior")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_img_behavior(self, event: AstrMessageEvent):
        """设置配图行为"""
        args = event.message_str.split()
        if len(args) < 2:
            curr = "auto"
            if self.config.get("image_always_include_self"): curr = "always"
            elif self.config.get("image_never_include_self"): curr = "never"
            yield event.plain_result(f"当前模式: {curr}\n用法: /share_set_image_behavior <auto|always|never>")
            return

        mode = args[1].lower()
        if mode == "auto":
            self.config["image_always_include_self"] = False
            self.config["image_never_include_self"] = False
        elif mode == "always":
            self.config["image_always_include_self"] = True
            self.config["image_never_include_self"] = False
        elif mode == "never":
            self.config["image_always_include_self"] = False
            self.config["image_never_include_self"] = True
        else:
            yield event.plain_result("❌ 无效模式")
            return
            
        await self._save_config_file()
        yield event.plain_result(f"✅ 配图模式已设置为: {mode}")

    @filter.command("share_sequence_status")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def handle_seq_status(self, event: AstrMessageEvent):
        """查看序列详情"""
        period = self._get_curr_period()
        seq = SHARING_TYPE_SEQUENCES.get(period, [])
        state = self._load_state()
        idx = state.get("sequence_index", 0)
        
        txt = f"🔄 当前时段: {period.value}\n"
        for i, t in enumerate(seq):
            mark = "👉 " if i == idx else "   "
            txt += f"{mark}{i}. {t}\n"
            
        yield event.plain_result(txt)

    @filter.command("share_help")
    async def handle_help(self, event: AstrMessageEvent):
        """帮助菜单"""
        yield event.plain_result("""📚 Daily Sharing 命令列表:
/share_status - 查看运行状态
/share_now [类型] - 立即执行一次
/share_enable - 启用插件
/share_disable - 禁用插件
/share_reset_sequence - 重置发送序列
/share_sequence_status - 查看当前序列
/share_set_image_behavior <mode> - 设置配图模式""")
