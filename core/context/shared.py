import asyncio
import datetime
import json
import re
import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ..config import SharingType, TimePeriod


DAILY_SHARING_INTERNAL_TRIGGER = "愿此见闻悄然为我启封"
DAILY_SHARING_MEMORY_PROMPT = "每日分享记录"
DAILY_SHARING_SOURCE = "daily_sharing"
