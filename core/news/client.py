import aiohttp
import asyncio
from typing import Optional, List, Dict, Any

from astrbot.api import logger

from ..config import NEWS_SOURCE_MAP


class NewsApiMixin:
    """新闻外部接口请求。"""

    async def shorten_url(self, original_url: str) -> Optional[str]:
        """使用柠柚短链接接口把原文链接转成短链。失败时返回 None。"""
        if not self.conf.get("enable_news_api", True):
            return None

        key = self.conf.get("nycnm_api_key", "").strip()
        original_url = str(original_url or "").strip()
        if not key or not original_url:
            return None

        cached = self._short_url_cache.get(original_url)
        if cached:
            return cached

        params = {
            "url": original_url,
            "format": "json",
            "apikey": key,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.nycnm.cn/api/v2/duan",
                    params=params,
                    timeout=10,
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"[短链接] 生成失败，状态码: {resp.status}")
                        return None
                    data = self._loads_json_payload(await resp.text())

            if not isinstance(data, dict):
                return None

            payload = data.get("data") if isinstance(data.get("data"), dict) else {}
            short_url = str(payload.get("short_url") or "").strip()
            if not short_url:
                return None

            if len(self._short_url_cache) >= 256:
                self._short_url_cache.pop(next(iter(self._short_url_cache)), None)
            self._short_url_cache[original_url] = short_url
            return short_url
        except asyncio.TimeoutError:
            logger.debug("[短链接] 生成超时")
        except Exception as e:
            logger.debug(f"[短链接] 生成失败: {e}")
        return None

    async def _fetch_news(self, source: str, key: str, limit: int = None) -> Optional[List[Dict]]:
        """发送 HTTP 请求。"""
        if source not in NEWS_SOURCE_MAP: return None
        
        source_name = NEWS_SOURCE_MAP[source]['name']
        url = NEWS_SOURCE_MAP[source]['url']
        extra_params = NEWS_SOURCE_MAP[source].get('extra_params', '')
        full_url = f"{url}?format=json&apikey={key}{extra_params}"
        
        timeout = self.conf.get("news_api_timeout", 30)
        
        logger.info(f"[新闻] 获取新闻: {source_name}")
        logger.debug(f"[新闻] 请求地址: {url}?format=json&apikey=***{extra_params}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(full_url, timeout=timeout) as resp:
                    if resp.status != 200: 
                        logger.warning(f"[新闻] 接口返回状态码: {resp.status}")
                        if resp.status in (401, 403):
                            logger.error("[新闻] 接口密钥无效或已过期！")
                        return None
                    
                    data = self._loads_json_payload(await resp.text())
                    parsed = self._parse_response(data, limit=limit)
                    
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

    async def get_baike_info(self, keyword: str) -> Optional[str]:
        """获取百科词条简介 (柠柚API)"""
        if not self.conf.get("enable_news_api", True): return None
        key = self.conf.get("nycnm_api_key", "").strip()
        if not key: return None

        # 清理关键词 
        keyword = keyword.replace("《", "").replace("》", "").replace("【", "").replace("】", "").strip()
        if not keyword: return None
        
        url = "https://api.nycnm.cn/api/v2/baike"
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
                    except Exception as e:
                        logger.debug(f"[百科] JSON 解析失败: {e}")
                        return None 

                    # 解析接口返回结构
                    if str(data.get("code")) == "200" or data.get("success") is True:
                        info = data.get("data")
                        
                        if isinstance(info, dict):
                            title = info.get("title", keyword)
                            abstract = info.get("abstract", "")
                            desc = info.get("description", "")
                            
                            parts = []
                            if desc:
                                parts.append(f"描述：{desc}")
                            if abstract:
                                clean_abstract = abstract.replace("\n", " ").strip()
                                parts.append(f"摘要：{clean_abstract}")
                                
                            if parts:
                                return f"标题：【{title}】 " + " | ".join(parts)
                                
                        elif isinstance(info, str):
                            return info

            return None
        except Exception as e:
            logger.warning(f"[百科] 查询失败: {e}")
            return None

    async def get_ai_news_json(self) -> Optional[Dict]:
        """获取每日 AI 资讯结构化数据"""
        key = self.conf.get("nycnm_api_key", "").strip()
        if not key:
            logger.error("[新闻] 未配置柠柚接口密钥")
            return None
            
        url = f"https://api.nycnm.cn/api/v2/aizixun?format=json&apikey={key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        
                        if data and isinstance(data, dict):
                            if "news" in data and not data.get("news"):
                                return None
                                
                            if "code" in data and str(data.get("code")) not in ["200", "1"]:
                                return None
                        
                        return data
            return None
        except Exception as e:
            return None           

