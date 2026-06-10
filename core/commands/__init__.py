from .basic import CommandBasicMixin
from .receiver import CommandTargetsMixin
from .sequence import CommandSequenceMixin


class CommandHandler(
    CommandTargetsMixin,
    CommandBasicMixin,
    CommandSequenceMixin,
):
    def __init__(self, plugin):
        self.plugin = plugin
        self.db = plugin.db
        self.config = plugin.config
        self.basic_conf = plugin.basic_conf
        self.extra_shares_conf = plugin.extra_shares_conf
        self.qzone_conf = plugin.qzone_conf
