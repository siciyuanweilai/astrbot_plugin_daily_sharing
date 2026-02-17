from enum import Enum

class TimePeriod(Enum):
    """时间段"""
    DAWN = "dawn"          # 凌晨 0-6
    MORNING = "morning"    # 早晨 6-9
    FORENOON = "forenoon"  # 上午 9-12
    AFTERNOON = "afternoon"  # 下午 12-16  
    EVENING = "evening"    # 傍晚 16-19  
    NIGHT = "night"        # 晚上 19-22
    LATE_NIGHT = "late_night" # 深夜 22-24

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
    "quark": {
        "url": "https://api.nycnm.cn/API/quark.php",
        "name": "夸克热搜",
        "icon": "⚛️"
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
    "toutiao": {
        "url": "https://api.nycnm.cn/API/toutiao.php",
        "name": "头条热搜",
        "icon": "🗞️"
    },
    "baidu": {
        "url": "https://api.nycnm.cn/API/baidu.php",
        "name": "百度热搜",
        "icon": "🔍"
    },
    "tencent": {
        "url": "https://api.nycnm.cn/API/txxw.php",
        "name": "腾讯热搜",
        "icon": "🐧"
    },
}

# 时间段新闻源偏好 
NEWS_TIME_PREFERENCES = {
    # 凌晨：偏向种草、短视频、emo时刻
    TimePeriod.DAWN: {
        "zhihu": 0.30,
        "bili": 0.20,
        "douyin": 0.15,
        "xiaohongshu": 0.10,
        "weibo": 0.10,
        "quark": 0.05,
        "toutiao": 0.05,
        "baidu": 0.05,
        "tencent": 0.05,
    },    
    # 早晨：偏向生活方式、通勤阅读、硬新闻
    TimePeriod.MORNING: {
        "xiaohongshu": 0.20, 
        "weibo": 0.20,
        "quark": 0.15,
        "toutiao": 0.10,
        "baidu": 0.10,
        "bili": 0.10,
        "douyin": 0.10,
        "tencent": 0.05,
        "zhihu": 0.05,
    },
    # 上午：偏向资讯、工作摸鱼
    TimePeriod.FORENOON: {
        "weibo": 0.20,
        "quark": 0.15,
        "toutiao": 0.15,
        "zhihu": 0.15,
        "baidu": 0.10,
        "tencent": 0.05,
        "xiaohongshu": 0.10,
        "bili": 0.10,
        "douyin": 0.05,
    },    
    # 下午：偏向娱乐视频、吃瓜、深度阅读
    TimePeriod.AFTERNOON: {
        "douyin": 0.20,
        "quark": 0.15,
        "zhihu": 0.15,
        "weibo": 0.10,
        "bili": 0.10,
        "baidu": 0.10,
        "toutiao": 0.10,
        "xiaohongshu": 0.10,
        "tencent": 0.05,
    },
    # 傍晚：偏向放松、下班娱乐、长视频
    TimePeriod.EVENING: {
        "bili": 0.20,
        "quark": 0.15,
        "weibo": 0.15,
        "douyin": 0.15,
        "tencent": 0.10,
        "xiaohongshu": 0.10,
        "zhihu": 0.10,
        "baidu": 0.05,
        "toutiao": 0.05,
    },
    # 晚上：偏向娱乐、社区讨论、短视频
    TimePeriod.NIGHT: {
        "douyin": 0.25,
        "bili": 0.20,
        "weibo": 0.15,
        "quark": 0.10,
        "xiaohongshu": 0.10,
        "baidu": 0.10,
        "zhihu": 0.05,
        "tencent": 0.05,
    },
    # 深夜：偏向情感、阅读、吃瓜、助眠
    TimePeriod.LATE_NIGHT: {
        "xiaohongshu": 0.25,
        "zhihu": 0.20,
        "bili": 0.20,
        "douyin": 0.15,
        "weibo": 0.10,
        "quark": 0.05,
        "baidu": 0.05,
        "tencent": 0.05,
        "toutiao": 0.00,
    },
}

# 分享类型序列
SHARING_TYPE_SEQUENCES = {
    # ===== 凌晨时段 (0:00-6:00) =====
    TimePeriod.DAWN: [
        SharingType.MOOD.value,        # 深夜心情（通常不设置定时）
    ],
        
    # ===== 早晨时段 (06:00-09:00) =====
    TimePeriod.MORNING: [
        SharingType.GREETING.value,    # 第1次：早安问候
    ],

    # ===== 上午时段 (09:00-12:00) =====
    TimePeriod.FORENOON: [
        SharingType.NEWS.value,        # 第1次：新闻热搜
        SharingType.KNOWLEDGE.value,   # 第2次：知识
    ],    

    # ===== 下午时段 (12:00-16:00) =====
    TimePeriod.AFTERNOON: [
        SharingType.NEWS.value,        # 第1次：新闻热搜
        SharingType.KNOWLEDGE.value,   # 第2次：知识
    ],

    # ===== 傍晚时段 (16:00-19:00) =====
    TimePeriod.EVENING: [
        SharingType.RECOMMENDATION.value,  # 第1次：推荐
        SharingType.NEWS.value,        # 第2次：新闻热搜
    ],

    # ===== 晚上时段 (19:00-22:00) =====
    TimePeriod.NIGHT: [
        SharingType.RECOMMENDATION.value, # 第1次：推荐
        SharingType.MOOD.value,           # 第2次：晚间闲聊
    ],

    # ===== 深夜时段 (22:00-24:00) =====
    TimePeriod.LATE_NIGHT: [
        SharingType.MOOD.value,        # 第1次：深夜心情
        SharingType.GREETING.value,    # 第2次：晚安问候        
    ],
}

# 默认知识库细分
DEFAULT_KNOWLEDGE_CATS = {
    "有趣的冷知识": "动物行为, 人体奥秘, 地理奇观, 历史误区, 语言文字, 植物智慧, 海洋生物, 昆虫视界, 真菌世界, 人体极限",
    "生活小技巧": "收纳整理, 厨房妙招, 数码技巧, 省钱攻略, 应急处理, 衣物护理, 家居清洁, 园艺入门, 旅行打包, 急救常识",
    "健康小常识": "睡眠科学, 饮食营养, 运动误区, 心理健康, 护眼护肤, 牙齿护理, 脱发自救, 饮水科学, 姿势矫正, 抗衰老",
    "历史小故事": "古代发明, 名人轶事, 文明起源, 战争细节, 文物故事, 丝绸之路, 大航海时代, 工业革命, 文艺复兴, 古代货币",
    "科学小发现": "天文宇宙, 平行宇宙, 生物进化, 未来科技, AI发展, 材料科学, 气象奥秘, 深海探测, 脑科学, 基因工程",
    "心理学知识": "认知偏差, 社交心理, 情绪管理, 微表情, 行为经济学, 人格类型, 梦境解析, 记忆规律, 说服技巧, 色彩心理",
    "艺术小百科": "名画赏析, 建筑风格, 设计美学, 色彩搭配, 流派演变, 博物馆巡礼, 传统工艺, 摄影构图, 书法篆刻, 音乐理论",
    "商业冷思维": "营销陷阱, 品牌故事, 经济学原理, 消费心理, 投资误区, 商业模式, 广告玄机, 博弈论, 富人思维, 独角兽兴衰",
    "哲学与逻辑": "著名悖论, 逻辑谬误, 思维模型, 存在主义, 伦理难题, 批判性思维, 奥卡姆剃刀, 墨菲定律, 斯多葛学派, 思想实验",
    "职场进化论": "高效办公, 沟通话术, 时间管理, 汇报技巧, 向上管理, 面试心理, 团队协作, 摸鱼哲学, 领导力, 职业规划"
}
# 默认推荐库细分
DEFAULT_REC_CATS = {
    "书籍": "悬疑推理, 当代文学, 历史传记, 科普新知, 商业思维, 治愈系绘本, 科幻神作, 哲学入门, 古典诗词, 艺术图鉴",
    "电影": "高分冷门, 烧脑科幻, 经典黑白, 是枝裕和风, 赛博朋克, 奥斯卡遗珠, 纪录片, 励志传记, 暴力美学, 黑色幽默",
    "音乐": "新世纪音乐, 治愈系钢琴, 氛围电子, 华语流行, 梦幻流行, 影视原声, 自然白噪音, 爵士蓝调, 摇滚精神, 民谣故事",
    "动漫": "治愈日常, 硬核科幻, 热血运动, 悬疑智斗, 吉卜力风, 奇幻史诗, 冷门佳作, 机甲浪漫, 异世界冒险, 推理侦探",
    "美食": "地方特色小吃, 创意懒人菜, 季节限定, 深夜治愈美食, 传统糕点, 异国风味, 烘焙甜点, 咖啡茶饮, 海鲜料理, 面食文化",
    "游戏": "独立神作, 治愈解谜, 剧情向, 像素风, 肉鸽Like, 模拟经营, 开放世界, 恐怖游戏, 复古怀旧, 派对游戏",
    "剧集": "英美神剧, 悬疑破案, 高分韩剧, 下饭情景剧, 职场爽剧, 历史正剧, 日式律政, 迷你剧, 真人秀, 讽刺喜剧",
    "播客": "怪诞故事, 商业内幕, 历史闲聊, 科技前沿, 情感治愈, 真实罪案, 文化对谈, 读书分享, 英语听力, 助眠ASMR",
    "好物": "桌面美学, 创意文具, 数码配件, 居家神器, 露营装备, 解压玩具, 咖啡器具, 极简收纳, 黑科技, 手工DIY",
    "旅行": "避世古镇, 赛博城市, 海岛度假, 徒步路线, 博物馆, 自驾公路, 露营圣地, 建筑打卡, 云旅游, 特色民宿"
}
