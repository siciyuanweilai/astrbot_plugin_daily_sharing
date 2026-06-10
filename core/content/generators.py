from .knowledge import ContentKnowledgeMixin
from .article import ContentNewsMixin
from .recommendation import ContentRecommendationMixin
from .social import ContentSocialMixin
from .topic import ContentTopicMixin


class ContentGeneratorMixin(
    ContentTopicMixin,
    ContentSocialMixin,
    ContentNewsMixin,
    ContentKnowledgeMixin,
    ContentRecommendationMixin,
):
    """内容生成能力聚合。"""
