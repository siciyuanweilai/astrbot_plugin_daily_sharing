from datetime import datetime

from astrbot.api import logger


class PluginQzoneMixin:
    def _inject_qzone_client(self, qzone_plugin):
        """尝试为 QQ 空间插件注入 CQHttp 客户端，解决自动任务缺少客户端的问题。"""
        try:
            if qzone_plugin and hasattr(qzone_plugin, "cfg") and not qzone_plugin.cfg.client:
                if self.ctx_service.bot_map:
                    aiocqhttp_bot = None
                    for pid, bot in self.ctx_service.bot_map.items():
                        if "aiocqhttp" in pid.lower():
                            aiocqhttp_bot = bot
                            break
                    bot_client = aiocqhttp_bot or list(self.ctx_service.bot_map.values())[0]
                    if bot_client:
                        qzone_plugin.cfg.client = bot_client
                        logger.debug("[每日分享] QQ 空间插件注入客户端成功！")
        except Exception as e:
            logger.warning(f"[每日分享] QQ 空间插件注入客户端失败: {e}")

    async def _publish_qzone_raw_images(self, qzone_plugin, text: str = "", images: list = None):
        """使用 qzone 上传层发布 bytes 图片，避开 Post.images 的字符串校验。"""
        service = getattr(qzone_plugin, "service", None)
        qzone = getattr(service, "qzone", None)
        session = getattr(service, "session", None)
        db = getattr(service, "db", None)
        post_cls = getattr(service.__class__.publish_post, "__globals__", {}).get("Post") if service else None
        if not service or not qzone or not session or not post_cls:
            raise RuntimeError("QQ 空间配图已生成，但当前 QQ 空间插件无法上传本地图片")

        raw_images = []
        saved_images = []
        for item in images or []:
            if isinstance(item, (bytes, bytearray, memoryview)):
                raw_images.append(bytes(item))
            else:
                image_url = str(item or "").strip()
                if image_url:
                    raw_images.append(image_url)
                    saved_images.append(image_url)

        uin = await session.get_uin()
        name = await session.get_nickname()
        fields = set(getattr(post_cls, "model_fields", {}) or getattr(post_cls, "__fields__", {}) or [])
        post_kwargs = {
            "id": None,
            "tid": None,
            "uin": uin,
            "name": name,
            "gin": 0,
            "text": text or "",
            "images": raw_images,
            "videos": [],
            "anon": False,
            "status": "approved",
            "create_time": int(datetime.now().timestamp()),
            "rt_con": "",
            "comments": [],
            "extra_text": None,
            "avatar_url": None,
        }
        if fields:
            post_kwargs = {key: value for key, value in post_kwargs.items() if key in fields}

        if hasattr(post_cls, "model_construct"):
            post = post_cls.model_construct(**post_kwargs)
        elif hasattr(post_cls, "construct"):
            post = post_cls.construct(**post_kwargs)
        else:
            raise RuntimeError("QQ 空间配图已生成，但当前 QQ 空间插件无法上传本地图片")

        resp = await qzone.publish(post)
        if not getattr(resp, "ok", False):
            raise RuntimeError(f"发布说说失败：{getattr(resp, 'data', None) or getattr(resp, 'message', '')}")

        post.tid = getattr(resp, "data", {}).get("tid")
        post.status = "approved"
        post.create_time = getattr(resp, "data", {}).get("now", post.create_time)
        post.images = saved_images
        if db and hasattr(db, "save"):
            await db.save(post)
        return post

    async def _safe_publish_qzone(self, qzone_plugin, text: str = "", images: list = None):
        """调用 QQ 空间发布接口（附带登录过期自动重试机制）。"""
        self._inject_qzone_client(qzone_plugin)
        images = images or []

        async def publish():
            if any(isinstance(item, (bytes, bytearray, memoryview)) for item in images):
                return await self._publish_qzone_raw_images(qzone_plugin, text=text, images=images)
            return await qzone_plugin.service.publish_post(text=text, images=images)

        try:
            return await publish()
        except Exception as e:
            err_msg = str(e)
            if "登录" in err_msg or "-100" in err_msg or "-3000" in err_msg or "失效" in err_msg:
                logger.debug("[每日分享] 检测到 QQ 空间登录态异常，正在尝试重新登录并重试...")
                try:
                    if hasattr(qzone_plugin, "session"):
                        await qzone_plugin.session.invalidate()
                    if hasattr(qzone_plugin, "cfg"):
                        qzone_plugin.cfg.update_cookies("")
                    if hasattr(qzone_plugin, "service"):
                        await qzone_plugin.service.query_feeds(pos=0, num=1)
                except Exception as ex:
                    logger.debug(f"[每日分享] 预检 QQ 空间登录态完成: {ex}")

                return await publish()
            raise e
