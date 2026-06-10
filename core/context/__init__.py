from astrbot.api import logger

from .shared import (
    DAILY_SHARING_INTERNAL_TRIGGER,
    DAILY_SHARING_MEMORY_PROMPT,
    DAILY_SHARING_SOURCE,
)
from .history import ContextHistoryMixin
from .life import ContextLifeMixin
from .memory import ContextMemoryMixin
from .tts import ContextTtsMixin
from ..image.providers import ImageProviderManager
from ..platform import (
    find_platform_instance_by_keywords,
    get_platform_client,
    get_platform_id,
    get_platform_type,
    iter_platform_instances,
)


class ContextService(
    ContextTtsMixin,
    ContextLifeMixin,
    ContextHistoryMixin,
    ContextMemoryMixin,
):
    def __init__(self, context_obj, config):
        self.context = context_obj
        self.config = config
        self.bot_map = {} 

        self._life_plugin = None
        self._memos_plugin = None
        self._tts_plugin = None
        
        unified_conf = self.config.get("context_conf", {})
        
        self.life_conf = unified_conf
        self.history_conf = unified_conf
        self.memory_conf = unified_conf

        self.image_conf = self.config.get("image_conf", {})
        self.tts_conf = self.config.get("tts_conf", {}) 
        self.llm_conf = self.config.get("llm_conf", {})
        self.tts_provider_manager = ImageProviderManager(context_obj, self.tts_conf)

    def _find_plugin(self, keyword: str):
        """按 AstrBot 插件元数据查找已加载实例。"""
        try:
            plugins = self.context.get_all_stars()
            
            for plugin in plugins:
                p_id = getattr(plugin, "root_dir_name", "") or getattr(plugin, "module_path", "") or ""
                p_name = getattr(plugin, "name", "") or ""
                display_name = getattr(plugin, "display_name", "") or ""
                
                if (keyword in p_id) or (keyword in p_name) or (keyword in display_name):
                    return getattr(plugin, "star_cls", None)
                    
        except Exception as e:
            logger.warning(f"[上下文] 查找插件 '{keyword}' 错误: {e}")
        return None

    def _get_memos_plugin(self):
        """获取 Memos 插件"""
        if not self._memos_plugin:
            self._memos_plugin = self._find_plugin("memos")
        return self._memos_plugin

    def _get_tts_plugin_inst(self):
        """获取语音合成插件实例。"""
        if not self._tts_plugin:
            self._tts_plugin = self._find_plugin("tts_emotion")
        return self._tts_plugin

    def _is_group_chat(self, target_umo: str) -> bool:
        """判断是否为群聊"""
        try:
            if not target_umo or not isinstance(target_umo, str):
                return False
            
            parts = target_umo.split(':')
            if len(parts) < 2:
                return False
            
            message_type = parts[1].lower()
            group_keywords = ['group', 'guild', 'channel', 'room']
            return any(keyword in message_type for keyword in group_keywords)
        except Exception as e:
            return False

    def _parse_umo(self, target_umo: str):
        """解析 UMO ID"""
        try:
            parts = target_umo.split(':')
            if len(parts) >= 3:
                return parts[0], ":".join(parts[2:])
            return None, None
        except Exception as e:
            logger.debug(f"[每日分享] 解析 UMO 失败: {e}")
            return None, None

    def _is_onebot_platform(self, adapter_id: str) -> bool:
        if not adapter_id:
            return False
        adapter = adapter_id.lower()
        return "aiocqhttp" in adapter or "onebot" in adapter

    def _is_onebot_event(self, event) -> bool:
        try:
            return self._is_onebot_platform(event.get_platform_name())
        except Exception:
            return False

    def _get_history_max_count(self, is_group: bool, group_default: int = 50, private_default: int = 20) -> int:
        default = group_default if is_group else private_default
        key = "deep_history_max_count" if is_group else "private_history_count"
        try:
            return max(0, int(self.history_conf.get(key, default)))
        except Exception:
            return default

    def _is_weixin_oc_event(self, event) -> bool:
        if not event:
            return False
        names = []
        try:
            names.append(str(event.get_platform_name() or ""))
        except Exception as e:
            logger.debug(f"[每日分享] 读取事件平台名失败: {e}")
        platform_inst = getattr(event, "platform", None)
        if platform_inst:
            names.extend(
                [
                    get_platform_type(platform_inst),
                    get_platform_id(platform_inst),
                ]
            )
        return any(str(name).strip().lower() == "weixin_oc" for name in names)

    def _get_onebot_bot(self, target_umo: str = "", event=None, adapter_id: str = ""):
        """获取 OneBot/CQHttp 客户端。UMO 第一段是平台标识，不能当成平台类型判断。"""
        if event and self._is_onebot_event(event):
            bot = getattr(event, "bot", None)
            if bot:
                return bot

        if adapter_id and self._is_onebot_platform(adapter_id):
            bot = self._get_bot_instance(adapter_id)
            if bot:
                return bot

        target_s = str(target_umo or "").strip()
        umo_adapter_id, real_id = self._parse_umo(target_s)
        if umo_adapter_id:
            for inst in iter_platform_instances(self.context):
                if get_platform_id(inst) == umo_adapter_id and self._is_onebot_platform(get_platform_type(inst)):
                    bot = get_platform_client(inst)
                    if bot:
                        return bot

        probe = real_id or target_s
        if str(probe).isdigit():
            inst = find_platform_instance_by_keywords(self.context, ["aiocqhttp", "onebot"])
            bot = get_platform_client(inst)
            if bot:
                return bot

        return None

    async def _bot_call_action(self, bot, action: str, **params):
        api = getattr(bot, "api", None)
        if api and hasattr(api, "call_action"):
            return await api.call_action(action, **params)
        if hasattr(bot, "call_action"):
            return await bot.call_action(action, **params)
        raise AttributeError(f"Bot 客户端不支持 call_action: {type(bot).__name__}")

    def _is_weixin_platform(self, target_umo: str) -> bool:
        raw = str(target_umo or "").lower()
        adapter_id, real_id = self._parse_umo(raw)
        session_id = real_id or raw
        return (
            session_id.endswith("@im.wechat")
            or session_id.endswith("@chatroom")
            or bool(adapter_id and adapter_id.strip().lower() == "weixin_oc")
        )

    async def init_bots(self):
        """
        初始化机器人实例缓存
        """
        logger.debug("[每日分享] 正在初始化机器人实例缓存...")
        try:
            count = 0
            for platform in iter_platform_instances(self.context):
                bot_client = get_platform_client(platform)
                p_id = get_platform_id(platform)
                if bot_client and p_id:
                    self.bot_map[str(p_id)] = bot_client
                    count += 1
                    logger.debug(f"[每日分享] 发现并缓存机器人实例: {p_id}，类型: {type(bot_client).__name__}")
            
            logger.debug(f"[每日分享] 机器人缓存初始化完成，共发现 {count} 个实例。")
            
        except Exception as e:
            logger.error(f"[每日分享] 机器人初始化失败: {e}", exc_info=True)

    def _get_bot_instance(self, adapter_id: str):
        """
        从缓存中获取机器人实例
        """
        if adapter_id:
            return self.bot_map.get(adapter_id)

        if self.bot_map:
            if len(self.bot_map) == 1:
                return list(self.bot_map.values())[0]

            logger.error(
                f"[每日分享] 存在多个机器人实例 {list(self.bot_map.keys())} 但未指定适配器标识，"
                "无法确定使用哪个实例。"
            )
            return None

        logger.warning("[每日分享] 没有任何可用的机器人实例。")
        return None
