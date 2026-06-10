from datetime import datetime

from ..constants import TYPE_CN_MAP


class TaskProgressMixin:
    """分享过程阶段进度。"""

    _PROGRESS_STEP_LABELS = {
        "content": "文案",
        "image": "配图",
        "video": "视频",
        "audio": "语音",
        "send": "发送",
    }

    _PROGRESS_STAGE_LABELS = {
        "prepare": "准备中",
        "content": "文案生成中",
        "image": "配图生成中",
        "video": "视频生成中",
        "audio": "语音生成中",
        "send": "发送中",
        "done": "已完成",
        "error": "失败",
        "empty": "空闲",
        "skipped": "已跳过",
    }

    def _progress_now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _progress_type_label(self, share_type) -> str:
        value = getattr(share_type, "value", share_type)
        value = str(value or "auto").strip()
        if value == "briefing":
            return "早报"
        return TYPE_CN_MAP.get(value, "自动" if value == "auto" else value)

    def _progress_source_label(self, source_type: str) -> str:
        return {
            "manual": "手动",
            "command": "自然语言",
            "scheduled": "定时",
        }.get(str(source_type or "").strip().lower(), str(source_type or "").strip() or "分享")

    def _progress_target_label(self, target_id: str, target_label: str = "") -> str:
        label = str(target_label or "").strip()
        if label:
            return label
        try:
            label = self._get_contact_alias(target_id)
        except Exception:
            label = ""
        if label:
            return label
        raw = str(target_id or "").strip()
        known = {
            "global": "全局",
            "qzone_broadcast": "QQ 空间",
            "briefing": "早报",
            "briefing_broadcast": "早报",
        }
        if raw in known:
            return known[raw]
        try:
            _adapter_id, real_id = self.ctx_service._parse_umo(raw)
            return real_id or raw or "当前任务"
        except Exception:
            return raw or "当前任务"

    def _progress_emit(self, event_type: str = "share_progress", payload: dict = None) -> None:
        emit = getattr(self.plugin, "_page_emit_dashboard_event", None)
        if callable(emit):
            emit(event_type, payload or {})

    def _progress_steps(self, enabled=None) -> list:
        enabled_set = set(self._PROGRESS_STEP_LABELS.keys() if enabled is None else enabled)
        return [
            {
                "key": key,
                "label": label,
                "status": "pending" if key in enabled_set else "skipped",
            }
            for key, label in self._PROGRESS_STEP_LABELS.items()
        ]

    def _start_share_progress(
        self,
        *,
        source_type: str,
        target_id: str = "",
        target_label: str = "",
        share_type=None,
        total_targets: int = 1,
        current_index: int = 1,
        enabled_steps=None,
        message: str = "",
    ) -> str:
        seq = int(getattr(self.plugin, "_share_progress_seq", 0) or 0) + 1
        self.plugin._share_progress_seq = seq
        job_id = f"share-{seq}"
        now = self._progress_now()
        progress = {
            "id": job_id,
            "status": "running",
            "stage": "prepare",
            "stage_label": self._PROGRESS_STAGE_LABELS["prepare"],
            "message": message or self._PROGRESS_STAGE_LABELS["prepare"],
            "source_type": str(source_type or "").strip(),
            "source_label": self._progress_source_label(source_type),
            "target_id": str(target_id or "").strip(),
            "target_label": self._progress_target_label(target_id, target_label),
            "share_type": getattr(share_type, "value", share_type) or "auto",
            "share_type_label": self._progress_type_label(share_type),
            "total_targets": max(1, int(total_targets or 1)),
            "current_index": max(1, int(current_index or 1)),
            "started_at": now,
            "updated_at": now,
            "finished_at": "",
            "steps": self._progress_steps(enabled_steps),
        }
        self.plugin._share_progress = progress
        self._progress_emit("share_progress", progress)
        return job_id

    def _update_share_progress(
        self,
        job_id: str = "",
        stage: str = "",
        *,
        status: str = "running",
        message: str = "",
        step_status: str = "running",
        mark_previous_done: bool = True,
        extra: dict = None,
    ) -> None:
        if not job_id:
            return
        progress = getattr(self.plugin, "_share_progress", None)
        if not isinstance(progress, dict):
            return
        if progress.get("id") != job_id:
            return

        stage = str(stage or progress.get("stage") or "prepare").strip()
        now = self._progress_now()
        progress["status"] = status
        progress["stage"] = stage
        progress["stage_label"] = self._PROGRESS_STAGE_LABELS.get(stage, stage)
        progress["message"] = message or progress["stage_label"]
        progress["updated_at"] = now
        if status in {"done", "error", "empty"}:
            progress["finished_at"] = now
        if extra:
            progress.update(extra)

        step_keys = [item["key"] for item in progress.get("steps", [])]
        if stage in step_keys:
            current_pos = step_keys.index(stage)
            for index, step in enumerate(progress["steps"]):
                if step.get("status") in {"skipped", "error"}:
                    continue
                if index < current_pos and mark_previous_done:
                    step["status"] = "done"
                elif index == current_pos:
                    step["status"] = step_status

        self.plugin._share_progress = progress
        self._progress_emit("share_progress", progress)

    def _skip_share_progress_step(self, job_id: str, stage: str, message: str = "") -> None:
        if not job_id:
            return
        progress = getattr(self.plugin, "_share_progress", None)
        if not isinstance(progress, dict):
            return
        if progress.get("id") != job_id:
            return
        for step in progress.get("steps", []):
            if step.get("key") == stage:
                step["status"] = "skipped"
                break
        progress["updated_at"] = self._progress_now()
        if message:
            progress["message"] = message
        self.plugin._share_progress = progress
        self._progress_emit("share_progress", progress)

    def _complete_share_progress_step(self, job_id: str, stage: str, message: str = "") -> None:
        if not job_id:
            return
        progress = getattr(self.plugin, "_share_progress", None)
        if not isinstance(progress, dict):
            return
        if progress.get("id") != job_id:
            return
        for step in progress.get("steps", []):
            if step.get("key") == stage and step.get("status") != "skipped":
                step["status"] = "done"
                break
        progress["updated_at"] = self._progress_now()
        if message:
            progress["message"] = message
        self.plugin._share_progress = progress
        self._progress_emit("share_progress", progress)

    def _fail_share_progress_step(self, job_id: str, stage: str, message: str = "") -> None:
        self._update_share_progress(
            job_id,
            stage,
            message=message,
            step_status="error",
            mark_previous_done=False,
        )

    def _finish_share_progress(self, job_id: str = "", *, success: bool = True, message: str = "") -> None:
        if not job_id:
            return
        status = "done" if success else "error"
        progress = getattr(self.plugin, "_share_progress", None)
        if isinstance(progress, dict):
            if progress.get("id") != job_id:
                return
            for step in progress.get("steps", []):
                if step.get("status") == "skipped":
                    continue
                if success and step.get("status") in {"pending", "running"}:
                    step["status"] = "done"
                elif not success and step.get("status") == "running":
                    step["status"] = "error"
        self._update_share_progress(
            job_id,
            "done" if success else "error",
            status=status,
            message=message or ("分享完成" if success else "分享失败"),
        )

    def get_share_progress_snapshot(self) -> dict:
        progress = getattr(self.plugin, "_share_progress", None)
        if not isinstance(progress, dict):
            return {
                "status": "idle",
                "stage": "empty",
                "stage_label": "空闲",
                "message": "空闲",
                "steps": self._progress_steps(enabled=[]),
            }
        snapshot = dict(progress)
        snapshot["steps"] = [dict(step) for step in progress.get("steps", [])]
        return snapshot
