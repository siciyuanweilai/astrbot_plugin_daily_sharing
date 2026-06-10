import asyncio
import json

from astrbot.api import logger

from .common import (
    _PAGE_MEDIA_CACHE_SECONDS,
    _PAGE_PREFERENCES_DEFAULTS,
    _quart_jsonify,
    _quart_request,
)


class DashboardBaseMixin:
    """仪表盘基础能力。"""

    def _register_page_web_apis(self) -> None:
        routes = (
            ("page/preferences", self.page_preferences, ["GET", "POST"], "仪表盘偏好"),
            ("page/status", self.page_status, ["GET"], "每日分享仪表盘状态"),
            ("page/config", self.page_config, ["GET", "POST"], "每日分享配置"),
            ("page/history", self.page_history, ["GET"], "每日分享历史"),
            ("page/failures", self.page_failures, ["GET"], "每日分享失败记录"),
            ("page/failures/clear", self.page_failures_clear, ["POST"], "清空每日分享失败记录"),
            ("page/media", self.page_media, ["GET"], "每日分享媒体"),
            ("page/media/view", self.page_media_view, ["POST"], "查看每日分享媒体"),
            ("page/toggle", self.page_toggle, ["POST"], "切换每日分享开关"),
            ("page/run", self.page_run, ["POST"], "手动分享"),
            ("page/retry", self.page_retry, ["POST"], "重试每日分享"),
            ("page/targets", self.page_targets_update, ["POST"], "更新每日分享目标"),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/astrbot_plugin_daily_sharing/{endpoint}",
                handler,
                methods,
                desc,
            )

    async def _page_response(self, payload: dict, status: int = 200, headers=None):
        if _quart_jsonify is None:
            return payload
        response = _quart_jsonify(payload)
        response.status_code = status
        if headers:
            response.headers.update(headers)
        return response

    async def _page_json(self, callback, headers=None):
        try:
            payload = await callback()
            status = 200
            response_headers = headers
        except Exception as exc:
            logger.exception("[每日分享] 仪表盘接口处理失败: %s", exc)
            payload = {
                "ok": False,
                "error": {"message": str(exc) or "请求失败"},
            }
            status = 200
            response_headers = None
        return await self._page_response(payload, status, response_headers)

    def _page_media_cache_headers(self) -> dict:
        return {
            "Cache-Control": f"private, max-age={_PAGE_MEDIA_CACHE_SECONDS}",
        }

    async def _page_query_params(self) -> dict:
        if _quart_request is None:
            return {}
        args = getattr(_quart_request, "args", {}) or {}
        return {str(key): value for key, value in args.items()}

    async def _page_json_body(self) -> dict:
        if _quart_request is None:
            return {}
        try:
            data = await _quart_request.get_json(silent=True)
        except TypeError:
            data = await _quart_request.get_json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _read_json_sync(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _normalize_page_preferences(self, preferences=None) -> dict:
        normalized = dict(_PAGE_PREFERENCES_DEFAULTS)
        if isinstance(preferences, dict):
            if "sakura_enabled" in preferences:
                normalized["sakura_enabled"] = bool(preferences.get("sakura_enabled"))
            active_view = str(preferences.get("active_view") or "").strip()
            if active_view in {"dashboard", "settings"}:
                normalized["active_view"] = active_view
        return normalized

    async def _load_page_preferences(self) -> dict:
        try:
            if not self.page_preferences_file.exists():
                return dict(_PAGE_PREFERENCES_DEFAULTS)
            loop = asyncio.get_running_loop()
            preferences = await loop.run_in_executor(
                None,
                self._read_json_sync,
                self.page_preferences_file,
            )
            return self._normalize_page_preferences(preferences)
        except Exception as exc:
            logger.error("[每日分享] 读取仪表盘偏好失败: %s", exc)
            return dict(_PAGE_PREFERENCES_DEFAULTS)

    async def _save_page_preferences(self, preferences: dict) -> dict:
        normalized = self._normalize_page_preferences(preferences)
        self.page_preferences_file.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._write_json_sync,
            self.page_preferences_file,
            normalized,
        )
        return normalized

