from .briefing import TaskBriefingMixin
from .command import TaskCommandShareMixin
from .delivery import TaskDeliveryMixin
from .assets import TaskDeliveryAssetsMixin
from .weixin import TaskDeliveryWeixinMixin
from .executor import TaskExecutorMixin
from .helpers import TaskExecutorHelperMixin
from .progress import TaskProgressMixin
from .zone import TaskQzoneMixin
from .selector import TaskTypeSelectorMixin
from .cache import TaskNewsCacheMixin
from .scheduler import TaskSchedulerMixin
from .targets import TaskTargetMixin


class TaskManager(
    TaskNewsCacheMixin,
    TaskTargetMixin,
    TaskSchedulerMixin,
    TaskProgressMixin,
    TaskExecutorHelperMixin,
    TaskTypeSelectorMixin,
    TaskBriefingMixin,
    TaskQzoneMixin,
    TaskCommandShareMixin,
    TaskExecutorMixin,
    TaskDeliveryAssetsMixin,
    TaskDeliveryWeixinMixin,
    TaskDeliveryMixin,
):
    """Coordinates scheduled and manual daily-sharing tasks."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.scheduler = plugin.scheduler
        self.db = plugin.db
        self.ctx_service = plugin.ctx_service
        self.news_service = plugin.news_service
        self.image_service = plugin.image_service
        self.content_service = plugin.content_service
        self._lock = plugin._lock
        
        self.basic_conf = plugin.basic_conf
        self.extra_shares_conf = plugin.extra_shares_conf
        self.qzone_conf = plugin.qzone_conf
        self.image_conf = plugin.image_conf
        self.tts_conf = plugin.tts_conf
        self.context_conf = plugin.context_conf
        self.receiver_conf = plugin.receiver_conf
