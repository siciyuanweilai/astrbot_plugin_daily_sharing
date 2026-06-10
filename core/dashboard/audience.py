from .activity import DashboardActivityMixin
from .jobs import DashboardJobsMixin
from .labels import DashboardLabelsMixin
from .roster import DashboardTargetConfigMixin


class DashboardTargetsMixin(
    DashboardActivityMixin,
    DashboardJobsMixin,
    DashboardTargetConfigMixin,
    DashboardLabelsMixin,
):
    """聚合仪表盘目标、任务和动态能力。"""


__all__ = ["DashboardTargetsMixin"]
