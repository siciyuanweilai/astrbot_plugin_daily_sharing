import random
import aiohttp
import asyncio
from typing import Optional, List, Dict, Any 
from astrbot.api import logger
from ..config import NEWS_SOURCE_MAP, NEWS_TIME_PREFERENCES, TimePeriod

class NewsService:
    def __init__(self, config: dict):
        self.config = config
        self.conf = self.config.get("news_conf", {})

    def _get_current_period(self) -> TimePeriod:
        from datetime import datetime
        hour = datetime.now().hour
        if 0 <= hour < 6: return TimePeriod.DAWN
        elif 6 <= hour < 9: return TimePeriod.MORNING
        elif 9 <= hour < 12: return TimePeriod.FORENOON
        elif 12 <= hour < 16: return TimePeriod.AFTERNOON
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
            TimePeriod.AFTERNOON: "下午", 
            TimePeriod.EVENING: "傍晚", 
            TimePeriod.NIGHT: "夜晚", 
            TimePeriod.LATE_NIGHT: "深夜"
        }.get(period, "现在")
        
        logger.info(f"[新闻] {period_label}智能选择: {NEWS_SOURCE_MAP[selected]['name']}")
        return selected

    async def get_hot_news(self, specific_source: str = None) -> Optional[tuple]:
        """获取热搜 (包含降级重试逻辑)"""
        # 检查开关和Key
        if not self.conf.get("enable_news_api", True): return None

        key = self.conf.get("nycnm_api_key", "").strip()
        if not key: 
            logger.error("[新闻] 未配置柠柚API密钥！")
            return None

        # 尝试主要源
        if specific_source and specific_source in NEWS_SOURCE_MAP:
             pri_source = specific_source
        else:
             pri_source = self.select_news_source()

        res = await self._fetch_news(pri_source, key)
        if res: 
            return (res, pri_source)

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
        
        res = await self._fetch_news(back_source, key)
        if res:
            logger.info(f"[新闻] 备用源成功")
            return (res, back_source)
        
        logger.warning(f"[新闻] 所有新闻源均失败")
        return None

    def get_hot_news_image_url(self, source: str = None) -> tuple:
        """获取热搜图片URL"""
        if not source or source not in NEWS_SOURCE_MAP:
            source = self.select_news_source()
        
        base_url = NEWS_SOURCE_MAP[source]['url']
        key = self.conf.get("nycnm_api_key", "").strip()
        
        final_url = f"{base_url}?format=image"
        if key:
            final_url += f"&apikey={key}"
            
        return final_url, NEWS_SOURCE_MAP[source]['name']

    async def _fetch_news(self, source: str, key: str) -> Optional[List[Dict]]:
        """执行 HTTP 请求 """
        if source not in NEWS_SOURCE_MAP: return None
        
        source_name = NEWS_SOURCE_MAP[source]['name']
        url = NEWS_SOURCE_MAP[source]['url']
        full_url = f"{url}?format=json&apikey={key}"
        
        timeout = self.conf.get("news_api_timeout", 15)
        
        logger.info(f"[新闻] 获取新闻: {source_name}")
        logger.debug(f"[新闻] 请求URL: {url}?format=json&apikey=***")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, timeout=timeout) as resp:
                    if resp.status != 200: 
                        logger.warning(f"[新闻] API返回状态码: {resp.status}")
                        if resp.status in (401, 403):
                            logger.error("[新闻] API密钥无效或已过期！")
                        return None
                    
                    data = await resp.json(content_type=None)
                    parsed = self._parse_response(data)
                    
                    if parsed:
                        logger.info(f"[新闻] 成功获取 {len(parsed)} 条{source_name}")
                        return parsed
                    else:
                        logger.warning(f"[新闻] 未能解析到新闻内容")
                        logger.debug(f"[新闻] 原始数据: {str(data)[:300]}...")
                        return None
                        
        except asyncio.TimeoutError:
            logger.error(f"[新闻] 请求超时: {source_name}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"[新闻] 网络请求失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[新闻] 解析新闻失败: {e}", exc_info=True)
            return None

    def _parse_response(self, data: Any) -> Optional[List[Dict]]:
        """
        解析响应数据
        支持多层级 JSON 和多种字段名 (hot/heat/hotValue)
        """
        items = []
        
        # 定位列表数据位置 (兼容多种API返回格式)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for k in ["data", "list", "items", "result"]:
                if k in data:
                    val = data[k]
                    if isinstance(val, list): 
                        items = val
                        break
                    elif isinstance(val, dict):
                        for sub_k in ["list", "items"]:
                            if sub_k in val and isinstance(val[sub_k], list): 
                                items = val[sub_k]
                                break
        
        if not items: return None

        limit = self.conf.get("news_items_count", 5)

        # 提取字段 (title, hot, url)
        res = []
        for i in items[:limit + 10]: 
            if len(res) >= limit: break 

            if not isinstance(i, dict): continue
            
            # 标题提取 (兼容多种字段名)
            title = i.get("title") or i.get("name") or i.get("query") or i.get("word") or i.get("keyword")
            if not title: continue
            
            # 热度提取 (兼容多种字段名)
            hot = i.get("hot") or i.get("hotValue") or i.get("heat") or i.get("hotScore") or ""
            
            # URL 提取 (兼容多种字段名)
            url_link = i.get("url") or i.get("link") or i.get("mobileUrl") or ""
            
            res.append({
                "title": str(title).strip(),
                "hot": str(hot).strip() if hot else "",
                "url": str(url_link).strip() if url_link else ""
            })
            
        return res if res else None

    async def get_baike_info(self, keyword: str) -> Optional[str]:
        """获取百科词条简介 (柠柚API)"""
        if not self.conf.get("enable_news_api", True): return None
        key = self.conf.get("nycnm_api_key", "").strip()
        if not key: return None

        # 清理关键词 
        keyword = keyword.replace("《", "").replace("》", "").replace("【", "").replace("】", "").strip()
        if not keyword: return None
        
        url = "https://api.nycnm.cn/API/baike.php"
        params = {
            "word": keyword,
            "format": "json", 
            "apikey": key
        }
        
        logger.debug(f"[百科] 查询: {keyword}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200: return None
                    
                    try:
                        data = await resp.json(content_type=None)
                    except:
                        return None 

                    # 解析结构 {"code": 200, "data": {"title":..., "abstract":..., "description":...}}
                    if str(data.get("code")) == "200" or data.get("success") is True:
                        info = data.get("data")
                        
                        if isinstance(info, dict):
                            # 优先取 abstract (详细摘要)，其次取 description (简述)
                            title = info.get("title", keyword)
                            abstract = info.get("abstract", "")
                            desc = info.get("description", "")
                            
                            if abstract:
                                clean_abstract = abstract.replace("\n", " ").strip()
                                # 截取前600字避免太长
                                return f"{title}：{clean_abstract[:600]}"
                            elif desc:
                                return f"{title}：{desc}"
                                
                        elif isinstance(info, str):
                            return info

            return None
        except Exception as e:
            logger.warning(f"[百科] 查询失败: {e}")
            return None
