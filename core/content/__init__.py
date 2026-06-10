from datetime import datetime
from typing import Dict, Optional

from astrbot.api import logger

from ..config import DEFAULT_KNOWLEDGE_CATS, DEFAULT_REC_CATS, SharingType, TimePeriod
from .assist import ContentSupportMixin
from .generators import ContentGeneratorMixin


class ContentService(ContentSupportMixin, ContentGeneratorMixin):
    def __init__(self, config: Dict, llm_func, context, db_manager, news_service=None):
        """
        初始化内容生成服务
        """
        self.config = config
        self.call_llm = llm_func
        self.context = context 
        self.db = db_manager 
        self.news_service = news_service
        
        self.content_lib_conf = self.config.get("content_library", {})
        raw_knowledge = self.content_lib_conf.get("knowledge_cats", DEFAULT_KNOWLEDGE_CATS)
        if not raw_knowledge: raw_knowledge = DEFAULT_KNOWLEDGE_CATS
        self.knowledge_cats = self._parse_category_config(raw_knowledge)
        raw_rec = self.content_lib_conf.get("rec_cats", DEFAULT_REC_CATS)
        if not raw_rec: raw_rec = DEFAULT_REC_CATS
        self.rec_cats = self._parse_category_config(raw_rec)
        
        self.basic_conf = self.config.get("basic_conf", {})
        raw_dedup_days = self.basic_conf.get(
            "data_retention_days",
            self.basic_conf.get("dedup_days_limit", 60)
        )
        try:
            self.dedup_days = int(raw_dedup_days)
        except Exception:
            self.dedup_days = 60
        
        self.news_conf = self.config.get("news_conf", {})
        self.llm_conf = self.config.get("llm_conf", {})
        self.context_conf = self.config.get("context_conf", {})

    async def generate(self, stype: SharingType, period: TimePeriod, 
                      target_id: str, is_group: bool, 
                      life_ctx: str, chat_hist: str, news_data: tuple = None,
                      nickname: str = "", recent_dynamics: str = "") -> Optional[str]:
        """统一生成入口"""
        # 获取人设信息
        persona_info = await self._get_persona_info()
        
        # 区分【亲昵称呼】和【用户昵称】：
        # - 亲昵称呼只来自人设配置，避免把本地昵称映射写成第三人称。
        # - 参数 nickname 仅作为用户昵称，用来判断日程/记忆里出现的人是否就是当前私聊对象。
        persona_user_name = persona_info.get("user_name", "").strip()
        detect_names = []
        for name in (nickname, persona_user_name):
            name = str(name or "").strip()
            if name and name not in detect_names:
                detect_names.append(name)
        detect_name = "、".join(detect_names)
        if is_group:
            call_name = "" 
        else:
            call_name = persona_user_name
        
        now = datetime.now()
        date_str = now.strftime("%Y年%m月%d日") 
        time_str = now.strftime("%H:%M")       
        
        ctx_data = {
            "target_id": target_id, 
            "is_group": is_group,
            "life_hint": life_ctx or "", 
            "chat_hint": chat_hist or "", 
            "persona": persona_info.get("prompt", ""),
            "period_label": self._get_period_label(period), 
            "date_str": date_str,         
            "time_str": time_str,
            "nickname": call_name,      
            "detect_name": detect_name,
            "recent_dynamics": recent_dynamics
        }
        
        try:
            if stype == SharingType.GREETING:
                return await self._gen_greeting(period, ctx_data)
            elif stype == SharingType.NEWS:
                return await self._gen_news(news_data, ctx_data)
            elif stype == SharingType.MOOD:
                return await self._gen_mood(period, ctx_data)
            elif stype == SharingType.KNOWLEDGE:
                return await self._gen_knowledge(ctx_data)
            elif stype == SharingType.RECOMMENDATION:
                return await self._gen_rec(ctx_data)
            
            return await self._gen_greeting(period, ctx_data)
            
        except Exception as e:
            logger.error(f"[内容服务] 生成内容出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
