from .analysis import ContextHistoryAnalysisMixin
from .fetch import ContextHistoryFetchMixin
from .normalize import ContextHistoryNormalizeMixin


class ContextHistoryMixin(
    ContextHistoryFetchMixin,
    ContextHistoryNormalizeMixin,
    ContextHistoryAnalysisMixin,
):
    """聊天历史获取、清洗和分析能力聚合。"""
