from .base import DashboardBaseMixin
from .api import DashboardConfigMixin
from .meta import DashboardConfigMetaMixin
from .validation import DashboardConfigValidationMixin
from .media import DashboardMediaMixin
from .provider_probe import DashboardProviderProbeMixin
from .routes import DashboardRoutesMixin
from .audience import DashboardTargetsMixin
from .common import PAGE_PREFERENCES_FILE


class DashboardBackendMixin(
    DashboardRoutesMixin,
    DashboardProviderProbeMixin,
    DashboardMediaMixin,
    DashboardConfigValidationMixin,
    DashboardConfigMetaMixin,
    DashboardConfigMixin,
    DashboardTargetsMixin,
    DashboardBaseMixin,
):
    """聚合仪表盘页面 API、配置、媒体和目标管理能力。"""


__all__ = ["DashboardBackendMixin", "PAGE_PREFERENCES_FILE"]
