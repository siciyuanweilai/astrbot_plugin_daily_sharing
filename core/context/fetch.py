from .shared import (
    DAILY_SHARING_SOURCE,
    Any,
    Dict,
    List,
    asyncio,
    datetime,
    json,
    logger,
    time,
)


class ContextHistoryFetchMixin:
    async def _fetch_deep_history(self, bot, target_id: int, is_group: bool, hours: int = 24, max_count: int = 100) -> List[Dict]:
        """深度回溯获取更早的聊天历史记录"""
        all_messages = []
        seen_ids = set()
        per_page = min(max_count + 20, 100)
        cursor_seq = 0
        try:
            effective_hours = max(1, min(int(hours), 168))
        except Exception:
            effective_hours = 24
        cutoff_time = time.time() - (effective_hours * 3600)
        max_rounds = 20
        
        action = "get_group_msg_history" if is_group else "get_friend_msg_history"
        id_key = "group_id" if is_group else "user_id"

        for round_idx in range(max_rounds):
            if len(all_messages) >= max_count:
                break
            
            try:
                if round_idx > 0:
                    await asyncio.sleep(0.5)

                params = {
                    id_key: target_id,
                    "count": per_page
                }
                if cursor_seq > 0:
                    params["message_seq"] = cursor_seq

                resp = await self._bot_call_action(bot, action, **params)
                
                if isinstance(resp, dict):
                    batch_msgs = resp.get("messages", [])
                elif isinstance(resp, list):
                    batch_msgs = resp
                else:
                    break
                    
                if not batch_msgs:
                    break

                batch_seqs = []
                # 记录本轮是否添加了新消息
                added_count = 0 
                
                for msg in batch_msgs:
                    # 收集消息序号，优先使用消息序列号，没有则使用消息标识。
                    seq = msg.get("message_seq") or msg.get("message_id")
                    if seq is not None:
                        try:
                            batch_seqs.append(int(seq))
                        except (TypeError, ValueError):
                            logger.debug(f"[每日分享] 跳过无法解析的消息序号: {seq}")

                    # 2. 去重入库
                    mid = msg.get("message_id")
                    if mid is None:
                        mid = f"{msg.get('time')}-{msg.get('sender',{}).get('user_id')}"
                    
                    mid_str = str(mid)
                    
                    if mid_str not in seen_ids:
                        seen_ids.add(mid_str)
                        msg_time = int(msg.get("time", 0))
                        if msg_time >= cutoff_time:
                            all_messages.append(msg)
                            added_count += 1

                # 3. 翻页逻辑
                if not batch_seqs:
                    break 
                
                min_seq_in_batch = min(batch_seqs)
                
                # 如果这一轮没有任何新消息入库（说明全是重复的），强制停止，防止死循环
                if added_count == 0 and round_idx > 0:
                    break
                
                # 如果游标没有向前推进，停止
                if cursor_seq != 0 and min_seq_in_batch >= cursor_seq:
                    break
                
                # 更新游标：直接使用存在的最小序号，允许下一页有一条重叠。
                cursor_seq = min_seq_in_batch
                
            except Exception as e:
                # 即使使用了重叠策略，依然保留这个捕获作为最后一道防线
                err_str = str(e)
                if "不存在" in err_str or getattr(e, 'retcode', 0) == 1200:
                    logger.debug(f"[每日分享] 历史记录翻到底了: {err_str}")
                else:
                    logger.warning(f"[每日分享] 获取历史中断: {e}")
                break
        
        # 结果排序与截取
        all_messages.sort(key=lambda x: x.get("time", 0))
        final_msgs = all_messages[-max_count:]
        
        return final_msgs

    async def get_history_data(self, target_umo: str, is_group: bool = None, event=None) -> Dict[str, Any]:
        """
        获取聊天历史记录
        """
        # 1. 基础开关检查
        if not self.history_conf.get("enable_chat_history", True):
            return {}
            
        if is_group is None:
            is_group = self._is_group_chat(target_umo)
        adapter_id, real_id = self._parse_umo(target_umo)
        if not real_id:
            target_s = str(target_umo or "").strip()
            if target_s.isdigit():
                real_id = target_s
            else:
                logger.warning(f"[每日分享] 无法解析目标标识: {target_umo}")
                return {}

        is_onebot_target = (
            self._is_onebot_platform(adapter_id)
            or self._is_onebot_event(event)
            or (not adapter_id and str(real_id or target_umo).strip().isdigit())
        )
        if not is_onebot_target:
            return await self._get_astrbot_saved_history_data(target_umo, is_group)

        bot = self._get_onebot_bot(target_umo, event=event, adapter_id=adapter_id)
        if not bot:
            return await self._get_astrbot_saved_history_data(target_umo, is_group)
        
        enable_deep = self.history_conf.get("enable_deep_history", True)
        history_hours = int(self.history_conf.get("deep_history_hours", 24))
        if history_hours > 168:
            history_hours = 168
        
        if is_group:
            # 群聊使用深度历史最大条数配置。
            max_count = int(self.history_conf.get("deep_history_max_count", 80))
        else:
            # 私聊使用私聊历史条数配置。
            max_count = int(self.history_conf.get("private_history_count", 20))
            
        try:
            logger.info(f"[每日分享] 正在获取 {real_id} 的聊天历史记录 (模式: {'群聊' if is_group else '私聊'}, 目标: {max_count}条)...")
            messages = []
            raw_msgs = []

            try:
                if enable_deep:
                    raw_msgs = await self._fetch_deep_history(
                        bot, 
                        int(real_id), 
                        is_group=is_group,
                        hours=history_hours, 
                        max_count=max_count
                    )
                    logger.info(f"[每日分享] 聊天历史记录获取成功: {len(raw_msgs)} 条")
                else:
                    action = "get_group_msg_history" if is_group else "get_friend_msg_history"
                    key = "group_id" if is_group else "user_id"

                    req_count = max_count 
                    
                    payloads = {key: int(real_id), "count": req_count}
                    
                    result = await self._bot_call_action(bot, action, **payloads)
                    raw_msgs = result.get("messages", []) if isinstance(result, dict) else (result or [])

            except Exception as e:
                logger.warning(f"[每日分享] 获取聊天历史记录失败: {e}")
                return await self._get_astrbot_saved_history_data(target_umo, is_group)

            bot_qq = ""
            try:
                login_info = await self._bot_call_action(bot, "get_login_info")
                if login_info and isinstance(login_info, dict):
                    bot_qq = str(login_info.get("user_id", ""))
            except Exception as e:
                logger.debug(f"[每日分享] 获取 login_info 失败: {e}")

            for msg in raw_msgs:
                sender_data = msg.get("sender", {})
                msg_uid = str(sender_data.get("user_id", ""))
                
                raw_content = ""
                if "message" in msg and isinstance(msg["message"], list):
                    raw_content = "".join(
                        seg["data"]["text"] for seg in msg["message"] if seg["type"] == "text"
                    ).strip()
                elif "raw_message" in msg:
                    raw_content = str(msg["raw_message"])

                if not raw_content: continue
                
                role = "assistant" if (bot_qq and msg_uid == bot_qq) else "user"
                ts = msg.get("time")
                try:
                    ts_str = datetime.datetime.fromtimestamp(ts).isoformat() if isinstance(ts, (int, float)) else ""
                except Exception:
                    ts_str = ""
                messages.append({"role": role, "content": raw_content, "timestamp": ts_str, "user_id": msg_uid})

            if not messages: return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                result["group_info"] = self._analyze_group_chat(messages)
            
            return result

        except Exception as e:
            logger.warning(f"[每日分享] 接口获取历史出错: {e}")
            return await self._get_astrbot_saved_history_data(target_umo, is_group)

    async def _get_astrbot_saved_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """优先读取 AstrBot 平台消息历史；没有可用记录时再读取会话历史。"""
        platform_data = await self._get_platform_message_history_data(target_umo, is_group)
        if not platform_data:
            return await self._get_conversation_history_data(target_umo, is_group)

        if any(msg.get("role") == "assistant" for msg in platform_data.get("messages", [])):
            conversation_data = await self._get_conversation_history_data(target_umo, is_group)
            self._mark_daily_share_sources(
                platform_data.get("messages", []),
                conversation_data.get("messages", []) if conversation_data else [],
            )
            if platform_data.get("is_group"):
                analysis_messages = [
                    msg for msg in platform_data["messages"]
                    if msg.get("source") != DAILY_SHARING_SOURCE
                ]
                platform_data["group_info"] = self._analyze_group_chat(analysis_messages)

        return platform_data

    async def _get_platform_message_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """读取 AstrBot 保存的平台消息记录表，用于 WebChat 等平台。"""
        if is_group is None:
            is_group = self._is_group_chat(target_umo)

        adapter_id, real_id = self._parse_umo(str(target_umo or ""))
        if not adapter_id or not real_id:
            return {}

        history_manager = getattr(self.context, "message_history_manager", None)
        get_history = getattr(history_manager, "get", None)
        if not callable(get_history):
            return {}

        max_count = self._get_history_max_count(is_group)
        if max_count <= 0:
            return {}

        try:
            records = []
            for user_id in self._get_platform_history_user_ids(adapter_id, real_id):
                records = await get_history(
                    platform_id=adapter_id,
                    user_id=user_id,
                    page=1,
                    page_size=max_count,
                )
                if records:
                    break

            messages = []
            next_assistant_is_daily_share = False
            for record in records or []:
                role_content = self._extract_platform_history_role_content(record)
                if role_content and self._is_internal_share_trigger(*role_content):
                    next_assistant_is_daily_share = True
                    continue

                msg = self._normalize_platform_history_item(record)
                if msg:
                    if next_assistant_is_daily_share:
                        if msg.get("role") == "assistant":
                            msg["source"] = DAILY_SHARING_SOURCE
                        next_assistant_is_daily_share = False
                    messages.append(msg)

            messages = messages[-max_count:]
            if not messages:
                return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                analysis_messages = [
                    msg for msg in messages
                    if msg.get("source") != DAILY_SHARING_SOURCE
                ]
                result["group_info"] = self._analyze_group_chat(analysis_messages)
            logger.debug(f"[每日分享] 已读取 AstrBot 平台消息历史: {target_umo} ({len(messages)} 条)")
            return result
        except Exception as e:
            logger.warning(f"[每日分享] 读取 AstrBot 平台消息历史失败: {e}")
            return {}

    async def _get_conversation_history_data(self, target_umo: str, is_group: bool = None) -> Dict[str, Any]:
        """读取 AstrBot 已保存的会话历史，用于个人微信不支持主动拉取历史的平台。"""
        if is_group is None:
            is_group = self._is_group_chat(target_umo)

        conv_manager = getattr(self.context, "conversation_manager", None)
        if not conv_manager:
            return {}

        try:
            conversation_id = await conv_manager.get_curr_conversation_id(target_umo)
            if not conversation_id:
                return {}
            conversation = await conv_manager.get_conversation(target_umo, conversation_id)
            if not conversation:
                return {}

            history_raw = getattr(conversation, "history", "[]")
            if isinstance(history_raw, list):
                history = history_raw
            else:
                try:
                    history = json.loads(history_raw or "[]")
                except json.JSONDecodeError as e:
                    logger.debug(f"[每日分享] 会话历史 JSON 解析失败: {e}")
                    history = []

            if not isinstance(history, list):
                return {}

            max_count = self._get_history_max_count(is_group)
            if max_count <= 0:
                return {}

            messages = []
            next_assistant_is_daily_share = False
            history_window = history[-(max_count + 1):]
            for item in history_window:
                role_content = self._extract_conversation_item_role_content(item)
                if role_content and self._is_internal_share_trigger(*role_content):
                    next_assistant_is_daily_share = True
                    continue

                msg = self._normalize_conversation_history_item(item)
                if msg:
                    if next_assistant_is_daily_share:
                        if msg.get("role") == "assistant":
                            msg["source"] = DAILY_SHARING_SOURCE
                        next_assistant_is_daily_share = False
                    messages.append(msg)
            messages = messages[-max_count:]

            if not messages:
                return {}

            result = {"messages": messages, "is_group": is_group}
            if is_group:
                analysis_messages = [
                    msg for msg in messages
                    if msg.get("source") != DAILY_SHARING_SOURCE
                ]
                result["group_info"] = self._analyze_group_chat(analysis_messages)
            logger.debug(f"[每日分享] 已读取 AstrBot 会话历史: {target_umo} ({len(messages)} 条)")
            return result
        except Exception as e:
            logger.warning(f"[每日分享] 读取 AstrBot 会话历史失败: {e}")
            return {}
