from datetime import datetime

from astrbot.api import logger

from ..config import SHARING_TYPE_SEQUENCES, SharingType, TimePeriod


class TaskTypeSelectorMixin:
    """分享类型轮换与时段兜底选择。"""

    async def decide_type_with_state(
        self,
        current_period: TimePeriod,
        is_qzone: bool = False,
        target_id: str = None,
        specific_type: str = "auto",
    ) -> SharingType:
        """带目标标识状态的分享类型决定，支持自定义列表轮换。"""
        if is_qzone:
            state_key = "qzone"
        else:
            state_key = f"target_{target_id}" if target_id else "global"

        state = await self.db.get_state(state_key, {})

        if specific_type and specific_type.lower() != "auto":
            seq_str = specific_type.replace("，", ",")
            custom_seq = [s.strip().lower() for s in seq_str.split(",") if s.strip()]

            if custom_seq and custom_seq != ["auto"]:
                idx_key = "custom_sequence_index"
                idx = state.get(idx_key, 0)
                if idx >= len(custom_seq):
                    idx = 0

                selected_str = custom_seq[idx]
                next_idx = (idx + 1) % len(custom_seq)

                await self.db.update_state_dict(
                    state_key,
                    {
                        idx_key: next_idx,
                        "last_timestamp": datetime.now().isoformat(),
                    },
                )

                if selected_str != "auto":
                    try:
                        return SharingType(selected_str)
                    except ValueError:
                        logger.warning(f"[每日分享] 自定义序列包含无效分享类型 {selected_str!r}，使用时段序列兜底。")

        conf_node = self.qzone_conf if is_qzone else self.basic_conf
        prefix = "qzone_" if is_qzone else ""
        config_key = f"{prefix}{current_period.value}_sequence"
        seq = conf_node.get(config_key, [])

        if not seq:
            seq = SHARING_TYPE_SEQUENCES.get(current_period, [SharingType.GREETING.value])

        idx_key = f"index_{current_period.value}"
        idx = state.get(idx_key, 0)
        if idx >= len(seq):
            idx = 0

        selected = seq[idx]
        next_idx = (idx + 1) % len(seq)

        await self.db.update_state_dict(
            state_key,
            {
                "last_period": current_period.value,
                idx_key: next_idx,
                "sequence_index": next_idx,
                "last_timestamp": datetime.now().isoformat(),
                "last_type": selected,
            },
        )

        try:
            return SharingType(selected)
        except ValueError:
            logger.warning(f"[每日分享] 无效分享类型 {selected!r}，回退到问候。")
            return SharingType.GREETING
