ALLOWED_NON_NEWS_SUFFIXES = {"广播", "空间"}


def find_invalid_non_news_args(parts) -> list[str]:
    return [p for p in list(parts or [])[2:] if p not in ALLOWED_NON_NEWS_SUFFIXES]
