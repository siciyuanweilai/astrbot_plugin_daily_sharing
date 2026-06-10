import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import AstrBotConfig

from .core.config import (
    SharingType,
    NEWS_SOURCE_MAP,
)
from .core.news import NewsService
from .core.image import ImageService
from .core.content import ContentService
from .core.context import ContextService
from .core.db import DatabaseManager 
from .core.tasks import TaskManager
from .core.commands import CommandHandler
from .core.dashboard import DashboardBackendMixin, PAGE_PREFERENCES_FILE
from .core.dashboard.common import _PAGE_MEDIA_CACHE_SECONDS
from .core.args import find_invalid_non_news_args
from .core.llm import PluginLlmMixin
from .core.runtime import PluginRuntimeMixin
from .core.support import PluginSupportMixin

class DailySharingPlugin(PluginRuntimeMixin, PluginLlmMixin, PluginSupportMixin, DashboardBackendMixin, Star):
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
        self.news_conf = self.config.get("news_conf", {})
        self.contact_aliases = self.config.get("contact_aliases", [])
        
        # 分享内容记录条数 
        self.history_limit = 100
        
        # 锁与防抖
        self._lock = asyncio.Lock()
        self._target_locks = {}
        self._last_share_time = None
        
        # 生命周期标志位 
        self._is_terminated = False
        
        # 缓存适配器标识
        self._cached_adapter_id = None 
        self._cached_qq_adapter_id = None
        self._cached_weixin_adapter_id = None

        # 临时降级第一个模型缓存
        self._temp_fallback_provider = None
        self._temp_fallback_until = 0.0
        self._fallback_ttl_seconds = 600

        # 任务追踪 (用于生命周期清理)
        self._bg_tasks = set()
        self._page_target_label_cache_data = {}
        
        # 数据路径
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_daily_sharing")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置文件路径
        config_dir = self.data_dir.parent.parent / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = config_dir / "astrbot_plugin_daily_sharing_config.json"
        self.page_preferences_file = self.data_dir / PAGE_PREFERENCES_FILE
        
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
        self._page_action_seq = 0
        self._page_action_runs = {}
        self._page_config_schema_meta_cache = None
        self._page_config_schema_meta_version = None
        self._register_page_web_apis()
        
        # 启动延迟初始化机器人缓存的任务
        self._track_task(self._delayed_init_bots())

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
        当用户想要看新闻、热搜、早安、晚安、知识、心情或推荐时调用此工具。
        也支持获取"每天60s读世界"或"AI资讯快报"图片。

        Args:
            share_type (string): 分享类型。支持：自动、问候、新闻、心情、知识、推荐、60s新闻、AI资讯。用户没有明确类型时设为自动。
            source (string): 仅新闻类型有效。指定新闻平台，如微博、知乎、B站、抖音、头条、百度、腾讯、小红书、夸克、36氪、51CTO、A站、爱范儿、网易、新浪、澎湃、第一财经、财联社。不指定则留空。
            get_image (boolean): 仅新闻类型有效。默认优先分享热搜长图；用户明确要求文字版、文本、不要图片或写一段新闻时设为 false。
            need_image (boolean): 是否需要AI配图。仅当用户明确说配图、带图、发张图时设为 true。
            need_video (boolean): 是否需要AI生成视频。仅当用户明确说视频、动态图、动起来时设为 true。
            need_voice (boolean): 是否需要转为语音。仅当用户明确提到语音、朗读、念给我听时设为 true。
            to_qzone (boolean): 是否分享到QQ空间。仅当用户明确要求发说说、发空间、分享到空间时设为 true。
        """
        return await self._daily_share_tool_impl(
            event,
            share_type,
            source,
            get_image,
            need_image,
            need_video,
            need_voice,
            to_qzone,
        )

    @filter.on_llm_request(priority=-1000)
    async def inject_news_link_context(self, event: AstrMessageEvent, req):
        """在模型请求前注入最近新闻缓存状态，帮助大语言模型稳定调用 news_link。"""
        await self._inject_news_link_context_impl(event, req)

    @filter.llm_tool(name="news_link")
    async def news_link_tool(
        self,
        event: AstrMessageEvent,
        action: str = "link",
        index: str = "",
        query: str = "",
        source: str = None,
        to_qzone: bool = False
    ):
        """
        获取最近一次新闻热搜长图或新闻分享中某条新闻的链接、摘要或来源。
        当用户追问“第3条链接”“这个详细说说”“刚才那条来源”“澎湃第10条原文”等新闻后续问题时调用。
        只要用户追问新闻的“链接、网址、原文、原文链接、出处、来源、详情、摘要、刚才那条、上面那条、第几条”，就优先调用本工具。
        如果用户同时提到新闻源名称，例如“知乎第3条原文”“澎湃第10条链接”，请把新闻源填入 source。
        你必须把自己理解出的新闻序号填入 index，使用阿拉伯数字字符串，例如用户说“第十条链接”时 index 填 "10"。
        用户只说“这个”“刚才那条”“上面那条”且没有明确序号时，index 留空，工具会使用最近关注的新闻。
        只负责按结构化序号、最近关注项或标题关键词查缓存；返回结果会优先使用短链接，不要把工具返回的短链接替换成原始长链接；不要用它重新生成新闻分享。
        工具返回中的内部提示只供你判断下一步，最终回复不要向用户提及缓存命中、缓存未命中或工具状态。
        

        Args:
            action (string): 查询动作。链接填 link；详细说明或摘要填 summary；只问来源出处填 source；想看可查列表填 list。用户没说清时默认 link。
            index (string): 用户要看的新闻序号，1 表示第 1 条。必须由你理解用户话语后填写阿拉伯数字字符串，例如 "18"；不要把“第十八条链接”整句填进来。
            query (string): 没有明确序号时填写标题关键词；不要把“第三条”“第3条链接”等序号原句片段填到这里。
            source (string): 可选新闻源，如财联社、微博、知乎、抖音。只有用户明确指定某个新闻源时填写；追问刚才长图时留空。
            to_qzone (boolean): 是否查询最近一次 QQ 空间新闻缓存。只有用户明确说空间或QQ空间那条时设为 true。
        """
        return await self._news_link_tool_impl(
            event,
            action=action,
            index=index,
            query=query,
            source=source,
            to_qzone=to_qzone,
        )

    @filter.on_llm_response(priority=-10000)
    async def clean_news_link_llm_references(self, event: AstrMessageEvent, resp):
        """保留大语言模型自然回复，只移除 news_link 场景下模型补出的参考链接尾部。"""
        await self._clean_news_link_llm_references_impl(event, resp)

    @filter.on_decorating_result(priority=-10000)
    async def clean_news_link_decorating_references(self, event: AstrMessageEvent):
        """发送前兜底清理参考链接尾部，但不覆盖大语言模型正文。"""
        await self._clean_news_link_decorating_references_impl(event)

    @filter.command("分享")
    async def handle_share_main(self, event: AstrMessageEvent):
        """每日分享统一命令入口。"""
        async for result in self._handle_share_main_impl(event):
            yield result
                
