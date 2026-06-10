from .host.alias import PluginAliasMixin
from .host.helper import PluginNewsHelperMixin
from .host.permission import PluginPermissionMixin
from .host.share import PluginShareMixin
from .host.space import PluginQzoneMixin
from .host.tools import PluginToolMixin


class PluginSupportMixin(
    PluginAliasMixin,
    PluginQzoneMixin,
    PluginShareMixin,
    PluginPermissionMixin,
    PluginNewsHelperMixin,
    PluginToolMixin,
):
    """主插件辅助能力聚合。"""
