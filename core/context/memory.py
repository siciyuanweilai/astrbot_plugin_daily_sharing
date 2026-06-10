from .shared import (
    DAILY_SHARING_INTERNAL_TRIGGER,
    DAILY_SHARING_MEMORY_PROMPT,
    logger,
    re,
)


class ContextMemoryMixin:
    async def record_bot_reply_to_history(self, target_umo: str, content: str, image_desc: str = None):
        """
        将 Bot 主动发送的消息写入 AstrBot 框架的对话历史中。
        """
        if not target_umo: return

        # 1. 预处理内容
        clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()
        final_content = clean_content
        if image_desc:
            final_content += f"\n\n[发送了一张配图: {image_desc}]"

        try:
            conv_manager = getattr(self.context, "conversation_manager", None)
            if not conv_manager or not hasattr(conv_manager, "add_message_pair"):
                logger.warning("[上下文] 当前 AstrBot 版本过低，不支持追加对话消息，无法写入消息历史。")
                return
            
            # 获取或创建会话标识。
            conversation_id = await conv_manager.get_curr_conversation_id(target_umo)
            if not conversation_id:
                conversation_id = await conv_manager.new_conversation(target_umo)
            
            # 使用内部标记保留成对历史，同时避免把主动分享误识别为用户真实发言。
            user_msg = {
                "role": "user",
                "content": [{"type": "text", "text": DAILY_SHARING_INTERNAL_TRIGGER}],
            }
            assistant_msg = {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": final_content,
                    }
                ],
            }
            
            await conv_manager.add_message_pair(
                cid=conversation_id,
                user_message=user_msg,
                assistant_message=assistant_msg,
            )
            logger.debug(f"[上下文] 已写入历史: {target_umo}")
            
        except Exception as e:
            logger.warning(f"[上下文] 写入对话历史失败: {e}")

    async def record_to_memos(self, target_umo: str, content: str, image_desc: str = None):
        if not self.memory_conf.get("record_sharing_to_memory", True): return
        memos = self._get_memos_plugin()
        if memos:
            try:
                # 清洗内容中的标签
                clean_content = re.sub(r'\$\$(?:EMO:)?(?:happy|sad|angry|neutral|surprise)\$\$', '', content, flags=re.IGNORECASE).strip()
                full_text = clean_content

                if image_desc: 
                    tag = f"[配图: {image_desc}]" if self.image_conf.get("record_image_description", True) else "[已发送配图]"
                    full_text += f"\n{tag}"
                elif image_desc is not None:
                    full_text += "\n[已发送配图]"

                cid = await self.context.conversation_manager.get_curr_conversation_id(target_umo)
                if not cid: cid = await self.context.conversation_manager.new_conversation(target_umo)

                virtual_prompt = DAILY_SHARING_MEMORY_PROMPT
                await memos.memory_manager.add_message(
                    messages=[
                        {"role": "user", "content": virtual_prompt}, 
                        {"role": "assistant", "content": full_text}
                    ],
                    user_id=target_umo, conversation_id=cid
                )
                logger.info(f"[上下文] 已记录到 Memos: {target_umo}")
            except Exception as e: 
                logger.warning(f"[上下文] 记录失败: {e}")
