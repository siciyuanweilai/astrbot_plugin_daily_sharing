import random
from typing import Optional

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP
from .client import NewsApiMixin
from .parser import NewsParserMixin
from .sources import NewsSourceMixin


class NewsService(NewsSourceMixin, NewsParserMixin, NewsApiMixin):

    def __init__(self, config: dict):
        self.config = config
        self.conf = self.config.get("news_conf", {})
        self._short_url_cache = {}

    async def get_hot_news(self, specific_source: str = None, limit: int = None, allow_fallback: bool = True) -> Optional[tuple]:
        """获取热搜 (包含降级重试逻辑)"""
        # 检查开关和密钥
        if not self.conf.get("enable_news_api", True): return None

        key = self.conf.get("nycnm_api_key", "").strip()
        if not key: 
            logger.error("[新闻] 未配置柠柚接口密钥！")
            return None

        # 尝试主要源
        if specific_source and specific_source in NEWS_SOURCE_MAP:
             pri_source = specific_source
        else:
             pri_source = self.select_news_source()

        res = await self._fetch_news(pri_source, key, limit=limit)
        if res: 
            return (res, pri_source)

        if not allow_fallback:
            logger.warning(f"[新闻] 指定新闻源 {pri_source} 获取失败，已按要求跳过备用源")
            return None

        logger.warning(f"[新闻] 主要源 {pri_source} 失败，尝试备用源...")
        
        mode = self.conf.get("news_random_mode", "config")
        
        # 确定备选池范围
        if mode in ["config", "time_based"]:
            configured = self.conf.get("news_random_sources", ["zhihu", "weibo"])
            pool = [s for s in configured if s in NEWS_SOURCE_MAP]
        else:
            # 从所有可用源中找
            pool = list(NEWS_SOURCE_MAP.keys())
            
        # 排除刚才失败的源
        fallback_pool = [s for s in pool if s != pri_source]
        
        if not fallback_pool: 
            logger.warning("[新闻] 没有可用的备用源")
            return None
        
        back_source = random.choice(fallback_pool)
        logger.info(f"[新闻] 尝试备用源: {NEWS_SOURCE_MAP[back_source]['name']}")
        
        res = await self._fetch_news(back_source, key, limit=limit)
        if res:
            logger.info(f"[新闻] 备用源成功")
            return (res, back_source)
        
        logger.warning(f"[新闻] 所有新闻源均失败")
        return None

