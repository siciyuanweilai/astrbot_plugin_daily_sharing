import random
from datetime import datetime
from typing import Optional

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP, NEWS_TIME_PREFERENCES, TimePeriod


class NewsSourceMixin:
    """新闻源选择和图片地址。"""

    def _get_current_period(self) -> TimePeriod:
        hour = datetime.now().hour
        if 0 <= hour < 6: return TimePeriod.DAWN
        elif 6 <= hour < 9: return TimePeriod.MORNING
        elif 9 <= hour < 12: return TimePeriod.FORENOON
        elif 12 <= hour < 14: return TimePeriod.NOON
        elif 14 <= hour < 16: return TimePeriod.AFTERNOON
        elif 16 <= hour < 19: return TimePeriod.EVENING
        elif 19 <= hour < 22: return TimePeriod.NIGHT
        else: return TimePeriod.LATE_NIGHT

    def select_news_source(self, excluded_source: str = None) -> str:
        """选择主新闻源"""
        mode = self.conf.get("news_random_mode", "config")
        
        if mode == "fixed": 
            source = self.conf.get("news_api_source", "zhihu")
            logger.debug(f"[新闻] 固定模式: {source}")
            return source
        elif mode == "random": 
            keys = list(NEWS_SOURCE_MAP.keys())
            if excluded_source and excluded_source in keys and len(keys) > 1:
                keys.remove(excluded_source)
            source = random.choice(keys)
            logger.info(f"[新闻] 完全随机: {NEWS_SOURCE_MAP[source]['name']}")
            return source
        elif mode == "config":
            c = self.conf.get("news_random_sources", ["zhihu", "weibo"])
            valid = [s for s in c if s in NEWS_SOURCE_MAP]
            if not valid: valid = ["zhihu"] 
            
            # 去重逻辑
            if excluded_source and excluded_source in valid and len(valid) > 1:
                valid.remove(excluded_source)
                
            source = random.choice(valid)
            logger.info(f"[新闻] 配置列表随机: {NEWS_SOURCE_MAP[source]['name']}")
            return source
        elif mode == "time_based": 
            return self._select_by_time(excluded_source)
        
        return "zhihu"

    def _select_by_time(self, excluded_source: str = None) -> str:
        """基于时间的智能选择"""
        period = self._get_current_period()
        # 获取偏好，默认为早晨配置
        prefs = NEWS_TIME_PREFERENCES.get(period, NEWS_TIME_PREFERENCES[TimePeriod.MORNING]).copy()
        
        # 去重
        if excluded_source and excluded_source in prefs:
            # 如果存在多个选项，才进行排除。如果只配置了一个选项，则无法排除。
            if len(prefs) > 1:
                del prefs[excluded_source]
                logger.debug(f"[新闻] 已排除上次使用的源: {excluded_source}")
        
        conf = self.conf.get("news_random_sources", None)
        
        selected = "zhihu"
        if conf:
            # 如果配置了限制列表，取交集
            valid = [s for s in conf if s in prefs]
            
            # 如果交集为空（可能排除后没了），则回退到不排除的状态
            if not valid:
                valid = [s for s in conf if s in NEWS_TIME_PREFERENCES.get(period, {})]
                
            if valid:
                # 重新计算权重
                total = sum(prefs.get(s, 0.1) for s in valid)
                if total == 0: total = 1
                weights = [prefs.get(s, 0.1)/total for s in valid]
                selected = random.choices(valid, weights=weights, k=1)[0]
            else:
                # 没交集则从配置里随机
                selected = random.choice(conf)
        else:
            # 默认使用所有偏好
            if not prefs:
                 prefs = NEWS_TIME_PREFERENCES.get(period, NEWS_TIME_PREFERENCES[TimePeriod.MORNING]).copy()
            selected = random.choices(list(prefs.keys()), weights=list(prefs.values()), k=1)[0]
            
        period_label = {
            TimePeriod.DAWN: "凌晨", 
            TimePeriod.MORNING: "早晨",
            TimePeriod.FORENOON: "上午",
            TimePeriod.NOON: "中午",
            TimePeriod.AFTERNOON: "下午", 
            TimePeriod.EVENING: "傍晚", 
            TimePeriod.NIGHT: "夜晚", 
            TimePeriod.LATE_NIGHT: "深夜"
        }.get(period, "现在")
        
        logger.info(f"[新闻] {period_label}智能选择: {NEWS_SOURCE_MAP[selected]['name']}")
        return selected

    def get_hot_news_image_url(self, source: str = None) -> tuple:
        """获取热搜图片链接"""
        if not source or source not in NEWS_SOURCE_MAP:
            source = self.select_news_source()
        
        base_url = NEWS_SOURCE_MAP[source]['url']
        extra_params = NEWS_SOURCE_MAP[source].get('extra_params', '')
        key = self.conf.get("nycnm_api_key", "").strip()
        
        final_url = f"{base_url}?format=image{extra_params}"
        if key:
            final_url += f"&apikey={key}"
            
        return final_url, NEWS_SOURCE_MAP[source]['name']

    def get_60s_image_url(self) -> Optional[str]:
        """获取每日 60 秒读世界图片链接"""
        key = self.conf.get("nycnm_api_key", "").strip()
        if not key:
            logger.error("[新闻] 未配置柠柚接口密钥")
            return None
        return f"https://api.nycnm.cn/api/v2/60s?format=image&apikey={key}"

    def get_ai_news_image_url(self) -> Optional[str]:
        """获取每日 AI 资讯图片链接"""
        key = self.conf.get("nycnm_api_key", "").strip()
        if not key:
            logger.error("[新闻] 未配置柠柚接口密钥")
            return None
        return f"https://api.nycnm.cn/api/v2/aizixun?format=image&apikey={key}"
        
