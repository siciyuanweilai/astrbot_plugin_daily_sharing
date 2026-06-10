from .adapter import TaskTargetPlatformMixin
from .identity import TaskTargetIdentityMixin
from .rule import TaskTargetConfigMixin


class TaskTargetMixin(
    TaskTargetConfigMixin,
    TaskTargetPlatformMixin,
    TaskTargetIdentityMixin,
):
    """目标解析、平台选择和显示名辅助能力聚合。"""
