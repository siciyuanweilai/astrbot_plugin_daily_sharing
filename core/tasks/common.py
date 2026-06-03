import os
import aiohttp
import asyncio
import random
import re
import hashlib
import aiofiles
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Record, Video

from ...config import TimePeriod, SharingType, SHARING_TYPE_SEQUENCES, CRON_TEMPLATES, NEWS_SOURCE_MAP
from ..constants import CMD_CN_MAP, SOURCE_CN_MAP

try:
    from astrbot.core.platform.message_session import MessageSesion
    from astrbot.core.platform.message_type import MessageType
except Exception:
    MessageSesion = None
    MessageType = None
