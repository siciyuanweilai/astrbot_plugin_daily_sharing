# config.py
from enum import Enum

class TimePeriod(Enum):
    """时间段"""
    DAWN = "dawn"          # 凌晨 0-6
    MORNING = "morning"    # 早晨 6-12
    AFTERNOON = "afternoon"  # 下午 12-17  
    EVENING = "evening"    # 傍晚 17-20  
    NIGHT = "night"        # 深夜 20-24  

class SharingType(Enum):
    """分享类型"""
    GREETING = "greeting"        # 问候
    NEWS = "news"               # 新闻见闻
    MOOD = "mood"               # 心情随想
    KNOWLEDGE = "knowledge"     # 知识分享
    RECOMMENDATION = "recommendation"  # 随机推荐（书籍/电影/音乐/动漫/美食）

# Cron 模板
CRON_TEMPLATES = {
    "morning": "0 8 * * *",       # 早上8点
    "noon": "0 12 * * *",         # 中午12点
    "afternoon": "0 15 * * *",    # 下午3点
    "evening": "0 19 * * *",      # 晚上7点
    "night": "0 22 * * *",        # 晚上10点
    "twice": "0 8,20 * * *",      # 早晚各一次
    "three_times": "0 8,12,20 * * *",  # 早中晚
}

# 新闻源配置
NEWS_SOURCE_MAP = {
    "zhihu": {
        "url": "https://api.nycnm.cn/API/zhihu.php",
        "name": "知乎热榜",
        "icon": "📚"
    },
    "weibo": {
        "url": "https://api.nycnm.cn/API/wb.php",
        "name": "微博热搜",
        "icon": "🔥"
    },
    "bili": {
        "url": "https://api.nycnm.cn/API/bilibilirs.php",
        "name": "B站热搜",
        "icon": "📺"
    },
    "xiaohongshu": {
        "url": "https://api.nycnm.cn/API/xhsrs.php",
        "name": "小红书热搜",
        "icon": "📕"
    },
    "douyin": {
        "url": "https://api.nycnm.cn/API/douyinrs.php",
        "name": "抖音热搜",
        "icon": "🎵"
    },
}

# 时间段新闻源偏好
NEWS_TIME_PREFERENCES = {
    TimePeriod.MORNING: {
        "xiaohongshu": 0.4,  # 早上偏向小红书
        "weibo": 0.3,
        "bili": 0.2,
        "zhihu": 0.05,
        "douyin": 0.05,
    },
    TimePeriod.AFTERNOON: {
        "douyin": 0.4,       # 下午偏向抖音
        "zhihu": 0.25,
        "bili": 0.2,
        "xiaohongshu": 0.1,
        "weibo": 0.05,
    },
    TimePeriod.EVENING: {
        "bili": 0.35,        # 傍晚偏向B站
        "weibo": 0.3,
        "douyin": 0.2,
        "zhihu": 0.1,
        "xiaohongshu": 0.05,
    },
    TimePeriod.NIGHT: {
        "douyin": 0.4,       # 深夜偏向抖音
        "bili": 0.3,
        "weibo": 0.2,
        "xiaohongshu": 0.05,
        "zhihu": 0.05,
    },
    TimePeriod.DAWN: {
        "xiaohongshu": 0.5,  # 凌晨偏向小红书
        "bili": 0.3,
        "weibo": 0.15,
        "zhihu": 0.03,
        "douyin": 0.02,
    },
}

# 分享类型序列
SHARING_TYPE_SEQUENCES = {
    # ===== 早晨时段 (6:00-12:00) =====
    TimePeriod.MORNING: [
        SharingType.GREETING.value,    # 第1次：早安问候
        SharingType.NEWS.value,        # 第2次：新闻热搜
    ],

    # ===== 下午时段 (12:00-17:00) =====
    TimePeriod.AFTERNOON: [
        SharingType.NEWS.value,        # 第1次：新闻热搜
        SharingType.KNOWLEDGE.value,   # 第2次：知识
        SharingType.RECOMMENDATION.value,  # 第3次：推荐
    ],

    # ===== 傍晚时段 (17:00-20:00) =====
    TimePeriod.EVENING: [
        SharingType.NEWS.value,        # 第1次：新闻热搜
    ],

    # ===== 夜晚时段 (20:00-24:00) =====
    TimePeriod.NIGHT: [
        SharingType.MOOD.value,        # 第1次：夜晚心情
        SharingType.GREETING.value,    # 第2次：晚安问候        
    ],

    # ===== 凌晨时段 (0:00-6:00) =====
    TimePeriod.DAWN: [
        SharingType.MOOD.value,        # 深夜心情（通常不设置定时）
    ],
}
