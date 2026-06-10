from .apply import DashboardConfigApplyMixin
from .payload import DashboardConfigPayloadMixin
from .refresh import DashboardConfigRefreshMixin


class DashboardConfigMixin(
    DashboardConfigRefreshMixin,
    DashboardConfigPayloadMixin,
    DashboardConfigApplyMixin,
):
    """仪表盘设置页配置能力聚合。"""
