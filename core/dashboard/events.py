import asyncio
import json
from datetime import datetime

from astrbot.api import logger

from .common import _quart_response


class DashboardEventsMixin:
    """仪表盘服务端事件流通道。"""

    def _ensure_page_event_state(self) -> None:
        if not hasattr(self, "_page_event_clients"):
            self._page_event_clients = set()
        if not hasattr(self, "_page_event_seq"):
            self._page_event_seq = 0

    def _page_event_payload(self, event_type: str, data: dict = None) -> dict:
        self._ensure_page_event_state()
        self._page_event_seq += 1
        return {
            "seq": self._page_event_seq,
            "type": str(event_type or "status"),
            "data": data or {},
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _page_sse_message(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _page_emit_dashboard_event(self, event_type: str = "status", data: dict = None) -> None:
        """向已打开的仪表盘页面广播轻量事件，前端收到后自行刷新 page/status。"""
        if getattr(self, "_is_terminated", False):
            return

        self._ensure_page_event_state()
        clients = list(self._page_event_clients)
        if not clients:
            return

        payload = self._page_event_payload(event_type, data)
        for queue in clients:
            try:
                while queue.full():
                    queue.get_nowait()
                queue.put_nowait(payload)
            except Exception as exc:
                logger.debug(f"[每日分享] 推送仪表盘事件失败: {exc}")

    async def page_events(self):
        if _quart_response is None:
            return await self._page_response(
                {"ok": False, "error": {"message": "当前环境不支持事件推送"}},
                status=501,
            )

        self._ensure_page_event_state()
        queue = asyncio.Queue(maxsize=50)
        self._page_event_clients.add(queue)

        async def stream():
            try:
                hello = {
                    "seq": self._page_event_seq,
                    "type": "hello",
                    "data": {},
                    "time": datetime.now().isoformat(timespec="seconds"),
                }
                yield self._page_sse_message(hello)
                while not getattr(self, "_is_terminated", False):
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=25)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield self._page_sse_message(payload)
            finally:
                self._page_event_clients.discard(queue)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return _quart_response(stream(), content_type="text/event-stream", headers=headers)
