import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain

from ..args import find_invalid_non_news_args
from ..config import NEWS_SOURCE_MAP, SharingType
from ..constants import CMD_CN_MAP, SOURCE_CN_MAP, TYPE_CN_MAP


class PluginShareMixin:
    """/分享 命令的实际处理逻辑。"""

    async def _handle_static_news_image_share(
        self,
        event: AstrMessageEvent,
        *,
        url: str,
        display_name: str,
        broadcast_name: str,
        history_text: str,
        download_fail_message: str,
        is_broadcast: bool,
        is_qzone_target: bool,
    ):
        if is_qzone_target:
            yield event.plain_result(f"正在分享{display_name}到QQ空间...")
            qzone_plugin = self.ctx_service._find_plugin("qzone")
            if qzone_plugin and hasattr(qzone_plugin, "service"):
                try:
                    await self._safe_publish_qzone(qzone_plugin, text=history_text, images=[url])
                    yield event.plain_result(f"{display_name}已成功分享到QQ空间！")
                    await self.db.add_sent_history(
                        "qzone_broadcast",
                        "briefing",
                        f"{history_text}(手动)",
                        True,
                        source_type="command",
                        **self.task_manager._image_history_kwargs(url),
                    )
                except Exception as e:
                    await self.db.add_sent_history(
                        "qzone_broadcast",
                        "briefing",
                        f"{history_text}(手动)失败",
                        False,
                        error_reason=str(e),
                        source_type="command",
                        **self.task_manager._image_history_kwargs(url),
                    )
                    yield event.plain_result(f"QQ空间分享失败: {e}")
            else:
                yield event.plain_result("未检测到QQ空间插件！")
            return

        target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
        yield event.plain_result(f"正在向{target_desc}分享{broadcast_name}...")
        filename = self.task_manager._build_news_image_filename(url, broadcast_name)
        if not is_broadcast:
            local_path = await self.task_manager._download_image_to_local(url, filename)
            if local_path:
                yield event.image_result(local_path)
                await self.db.add_sent_history(
                    event.unified_msg_origin,
                    "briefing",
                    f"【{broadcast_name}】手动",
                    True,
                    source_type="command",
                    **self.task_manager._image_history_kwargs(local_path),
                )
            else:
                await self.db.add_sent_history(
                    event.unified_msg_origin,
                    "briefing",
                    download_fail_message,
                    False,
                    error_reason=download_fail_message,
                    source_type="command",
                    **self.task_manager._image_history_kwargs(url),
                )
                yield event.plain_result(download_fail_message)
            return

        targets = self.task_manager.get_broadcast_targets()
        local_path = await self.task_manager._download_image_to_local(url, filename)
        if not local_path:
            await self.db.add_sent_history(
                "global",
                "briefing",
                download_fail_message,
                False,
                error_reason=download_fail_message,
                source_type="command",
                **self.task_manager._image_history_kwargs(url),
            )
            yield event.plain_result(download_fail_message)
            return

        success_count = 0
        fail_count = 0
        for target in targets:
            try:
                prepared_path = await self.task_manager._prepare_image_for_target(target, local_path)
                await self.task_manager._send_message_chain(
                    target,
                    MessageChain().file_image(prepared_path),
                )
                await self.db.add_sent_history(
                    target,
                    "briefing",
                    f"【{broadcast_name}】手动广播",
                    True,
                    source_type="command",
                    **self.task_manager._image_history_kwargs(prepared_path),
                )
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.error(f"[每日分享] 分享{broadcast_name}到 {target} 失败: {e}")
                await self.db.add_sent_history(
                    target,
                    "briefing",
                    f"{broadcast_name}广播失败: {e}",
                    False,
                    error_reason=str(e),
                    source_type="command",
                    **self.task_manager._image_history_kwargs(local_path),
                )
            await asyncio.sleep(1)
        yield event.plain_result(f"{broadcast_name}广播完成：成功 {success_count} 个，失败 {fail_count} 个。")

    async def _handle_share_main_impl(self, event: AstrMessageEvent):
        """
        每日分享统一命令入口
        """
        msg = event.message_str.strip()
        parts = msg.split()

        self._remember_event_adapter(event)
        
        if len(parts) == 1:
            yield event.plain_result("指令格式错误，请指定参数。\n示例：/分享 新闻\n可加后缀：广播、空间")
            return
            
        arg = parts[1].lower()
        
        # 判断后缀模式
        is_broadcast = "广播" in parts
        is_qzone_target = "空间" in parts  # 判断是否指向 QQ 空间
        is_admin = self._is_admin_event(event)
        is_configured_receiver = self._is_configured_receiver_event(event)
        admin_only_args = {"开启", "关闭", "早报空间", "添加当前", "昵称"}

        if arg in admin_only_args or is_broadcast or is_qzone_target:
            if not is_admin:
                yield event.plain_result("权限不足：该操作会修改全局配置、广播或发布QQ空间，仅管理员可用。")
                return
        elif not (is_admin or is_configured_receiver):
            yield self._plain_permission_denied(event)
            return
        
        current_uid = event.unified_msg_origin
        specific_target = None if is_broadcast else current_uid
        share_global_scope = is_broadcast or is_qzone_target

        # =============== 手动触发 60s 新闻 ===============
        if arg == "60s":
            url = self.news_service.get_60s_image_url()
            if not url:
                yield event.plain_result("获取 60s 新闻失败，请检查接口密钥配置。")
                return
                
            async for res in self._handle_static_news_image_share(
                event,
                url=url,
                display_name="每天60s读世界",
                broadcast_name="60s新闻",
                history_text="【每天60秒读懂世界】",
                download_fail_message="60s新闻图片下载失败。",
                is_broadcast=is_broadcast,
                is_qzone_target=is_qzone_target,
            ):
                yield res
            return

        # =============== 手动触发智能资讯 ===============
        if arg == "ai":
            # 先拦截检测
            ai_data = await self.news_service.get_ai_news_json()
            if not ai_data:
                yield event.plain_result("获取 AI 资讯失败或今日暂无更新。")
                return

            url = self.news_service.get_ai_news_image_url()
            if not url:
                yield event.plain_result("获取 AI 资讯图片失败，请检查接口密钥配置。")
                return

            async for res in self._handle_static_news_image_share(
                event,
                url=url,
                display_name="AI资讯快报",
                broadcast_name="AI资讯",
                history_text="【AI资讯快报】",
                download_fail_message="AI资讯快报图片下载失败。",
                is_broadcast=is_broadcast,
                is_qzone_target=is_qzone_target,
            ):
                yield res
            return
        
        # =============== 配置命令 ===============
        if arg == "早报空间":
            async for res in self.command_handler.cmd_briefing_qzone_sync(event, parts): yield res
            return
        elif arg == "昵称":
            async for res in self.command_handler.cmd_contact_alias(event, parts): yield res
            return
        elif arg == "添加当前":
            async for res in self.command_handler.cmd_add_current(event, parts): yield res
            return
        elif arg == "状态":
            async for res in self.command_handler.cmd_status(event): yield res
            return
        elif arg == "开启":
            async for res in self.command_handler.cmd_enable(event): yield res
            return
        elif arg == "关闭":
            async for res in self.command_handler.cmd_disable(event): yield res
            return
        elif arg == "重置序列":
            async for res in self.command_handler.cmd_reset_seq(event): yield res
            return
        elif arg == "查看序列":
            async for res in self.command_handler.cmd_view_seq(event): yield res
            return
        elif arg == "帮助":
            async for res in self.command_handler.cmd_help(event): yield res
            return
        elif arg == "指定序列":
            async for res in self.command_handler.cmd_set_seq(event, parts): yield res
            return

        # =============== 自动或具体类型生成 ===============
        if arg in ["自动", "auto"]:
            invalid_args = find_invalid_non_news_args(parts)
            if invalid_args:
                yield event.plain_result(f"无效参数: {' '.join(invalid_args)}。非新闻类型仅支持后缀：广播、空间。")
                return

            if self._is_share_busy(specific_target, global_scope=share_global_scope):
                yield event.plain_result("正如火如荼地准备中，请稍后...")
                return
            share_lock = self._get_share_lock(specific_target, global_scope=share_global_scope)
            if is_qzone_target:
                yield event.plain_result("正在向QQ空间生成并分享内容(自动类型)...")
                async with share_lock:
                    await self.task_manager.execute_qzone_share(None, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享内容(自动类型)...")
                async with share_lock:
                    await self.task_manager.execute_share(None, specific_target=specific_target, event=event)
            if not share_global_scope:
                self._release_idle_share_lock(specific_target)
            return

        else:
            force_type = None
            if arg in CMD_CN_MAP:
                force_type = CMD_CN_MAP[arg]
            else:
                try:
                    force_type = SharingType(arg)
                except ValueError:
                    yield event.plain_result(f"未知指令或无效类型: {arg}\n可用: 问候, 新闻, 心情, 知识, 推荐, 60s, ai")
                    return

            type_cn = TYPE_CN_MAP.get(force_type.value, arg)
            
            if force_type == SharingType.NEWS:
                news_src = None
                is_image_mode = "图片" in parts
                
                for p in parts[2:]:
                    if p in ["图片", "广播", "空间"]: continue 
                    if p in SOURCE_CN_MAP:
                        news_src = SOURCE_CN_MAP[p]
                        break
                    elif p in NEWS_SOURCE_MAP:
                        news_src = p
                        break
                        
                if is_image_mode:
                    if not news_src: news_src = self.news_service.select_news_source()
                    img_url, src_name = self.news_service.get_hot_news_image_url(news_src)
                    snapshot_data = await self.news_service.get_hot_news(
                        news_src,
                        limit=self.task_manager.get_news_snapshot_limit(),
                        allow_fallback=False
                    )
                    
                    if is_qzone_target:
                        await self.task_manager.cache_news_snapshot("qzone_broadcast", news_data=snapshot_data, source_key=news_src, image_url=img_url)
                        await self.task_manager.cache_news_snapshot(current_uid, news_data=snapshot_data, source_key=news_src, image_url=img_url)
                        yield event.plain_result(f"正在获取[{src_name}]图片并分享到QQ空间...")
                        qzone_plugin = self.ctx_service._find_plugin("qzone")
                        if qzone_plugin and hasattr(qzone_plugin, "service"):
                            try:
                                await self._safe_publish_qzone(qzone_plugin, text=f"【{src_name}】", images=[img_url])
                                yield event.plain_result("QQ空间分享成功！")
                                await self.db.add_sent_history(
                                    "qzone_broadcast",
                                    "news",
                                    f"【{src_name}】长图(手动)",
                                    True,
                                    source_type="command",
                                    **self.task_manager._image_history_kwargs(img_url),
                                )
                            except Exception as e:
                                await self.db.add_sent_history(
                                    "qzone_broadcast",
                                    "news",
                                    f"【{src_name}】长图(手动)失败",
                                    False,
                                    error_reason=str(e),
                                    source_type="command",
                                    **self.task_manager._image_history_kwargs(img_url),
                                )
                                yield event.plain_result(f"QQ空间分享失败: {e}")
                        else:
                            yield event.plain_result("未检测到QQ空间插件！")
                        return

                    await self.task_manager.cache_news_snapshot(current_uid, news_data=snapshot_data, source_key=news_src, image_url=img_url)
                    yield event.plain_result(f"正在获取 [{src_name}] 图片...")
                    filename = self.task_manager._build_news_image_filename(img_url, src_name)
                    local_path = await self.task_manager._download_image_to_local(img_url, filename)
                    if local_path:
                        yield event.image_result(local_path)
                        await self.db.add_sent_history(
                            current_uid,
                            "news",
                            f"【{src_name}】长图(手动)",
                            True,
                            source_type="command",
                            **self.task_manager._image_history_kwargs(local_path),
                        )
                    else:
                        await self.db.add_sent_history(
                            current_uid,
                            "news",
                            f"获取 [{src_name}] 图片下载失败。",
                            False,
                            error_reason=f"获取 [{src_name}] 图片下载失败。",
                            source_type="command",
                            **self.task_manager._image_history_kwargs(img_url),
                        )
                        yield event.plain_result(f"获取 [{src_name}] 图片下载失败。")
                    return
                    
                src_info = f" ({NEWS_SOURCE_MAP[news_src]['name']})" if news_src else ""
                
                if self._is_share_busy(specific_target, global_scope=share_global_scope):
                    yield event.plain_result("正如火如荼地准备中，请稍后...")
                    return
                share_lock = self._get_share_lock(specific_target, global_scope=share_global_scope)

                if is_qzone_target:
                    yield event.plain_result(f"正在向QQ空间生成并分享{type_cn}{src_info} ...")
                    async with share_lock:
                        await self.task_manager.execute_qzone_share(force_type, news_source=news_src, event=event)
                else:
                    target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                    yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn}{src_info} ...")
                    async with share_lock:
                        await self.task_manager.execute_share(force_type, news_source=news_src, specific_target=specific_target, event=event)
                if not share_global_scope:
                    self._release_idle_share_lock(specific_target)
                return

            invalid_args = find_invalid_non_news_args(parts)
            if invalid_args:
                yield event.plain_result(f"无效参数: {' '.join(invalid_args)}。非新闻类型仅支持后缀：广播、空间。")
                return
                 
            if self._is_share_busy(specific_target, global_scope=share_global_scope):
                yield event.plain_result("正如火如荼地准备中，请稍后...")
                return
            share_lock = self._get_share_lock(specific_target, global_scope=share_global_scope)

            if is_qzone_target:
                yield event.plain_result(f"正在向QQ空间生成并分享{type_cn} ...")
                async with share_lock:
                    await self.task_manager.execute_qzone_share(force_type, event=event)
            else:
                target_desc = "配置的所有群聊和私聊" if is_broadcast else "当前会话"
                yield event.plain_result(f"正在向{target_desc}生成并分享{type_cn} ...")
                async with share_lock:
                    await self.task_manager.execute_share(force_type, specific_target=specific_target, event=event)
            if not share_global_scope:
                self._release_idle_share_lock(specific_target)
