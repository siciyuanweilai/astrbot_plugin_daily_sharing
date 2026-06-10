import re


class DashboardJobsMixin:
    """仪表盘任务和日历数据。"""

    def _page_job_display_name(
        self,
        job_id: str,
        job_name: str = "",
        target_labels: dict = None,
        random_share_label: str = "",
    ) -> str:
        job_id = str(job_id or "")
        job_name = str(job_name or "")
        target_labels = target_labels or {}
        static_names = {
            "auto_share": "全局定时分享",
            "qzone_share": "QQ 空间定时分享",
            "share_briefing": "早报分享",
            "weixin_temp_cleanup": "微信临时图片清理",
            "news_image_cleanup": "新闻源图片清理",
            "daily_random_scheduler": "每日随机分享排程",
            "daily_qzone_random_scheduler": "每日 QQ 空间随机排程",
            "delayed_auto_share": "全局分享延迟分享",
            "delayed_qzone_share": "QQ 空间延迟分享",
            "delayed_briefing_share": "早报延迟分享",
            "resume_auto_share": "恢复全局延迟分享",
            "resume_qzone_share": "恢复 QQ 空间延迟分享",
            "resume_briefing_share": "恢复早报延迟分享",
        }
        if job_id in static_names:
            return static_names[job_id]

        patterns = (
            (r"^random_share_(\d+)$", "random", 1, False),
            (r"^qzone_random_share_(\d+)$", "qzone_random", 1, False),
            (r"^custom_share_(.+)$", "custom", 0, True),
            (r"^delayed_custom_share_(.+)$", "custom_delayed", 0, True),
            (r"^resume_custom_share_(.+)$", "custom_resume", 0, True),
        )
        for pattern, kind, offset, is_target in patterns:
            match = re.match(pattern, job_id)
            if not match:
                continue
            value = match.group(1)
            if job_id.startswith("random_share_") and random_share_label:
                return f"{random_share_label} · 随机分享"
            if is_target:
                value = target_labels.get(value, value)
            elif offset:
                try:
                    value = str(int(value) + offset)
                except ValueError:
                    pass
            if kind == "random":
                return f"随机分享 {value}"
            if kind == "qzone_random":
                return f"QQ 空间 · 随机分享 {value}"
            if kind == "custom":
                return f"{value} · 独立分享"
            if kind == "custom_delayed":
                return f"{value} · 延迟独立分享"
            if kind == "custom_resume":
                return f"{value} · 恢复延迟独立分享"

        return job_name or job_id or "任务"
    def _page_jobs(self, targets: dict = None) -> list:
        jobs = []
        targets = targets or {}
        target_labels = self._page_target_label_map(targets)
        random_share_label = self._page_random_share_target_label(targets)
        for job in self.scheduler.get_jobs():
            next_run_time = getattr(job, "next_run_time", None)
            job_id = str(getattr(job, "id", ""))
            job_name = str(getattr(job, "name", ""))
            jobs.append(
                {
                    "id": job_id,
                    "name": job_name,
                    "display_name": self._page_job_display_name(
                        job_id, job_name, target_labels, random_share_label
                    ),
                    "trigger": str(getattr(job, "trigger", "")),
                    "next_run_time": (
                        next_run_time.isoformat(timespec="seconds")
                        if next_run_time
                        else ""
                    ),
                }
            )
        return sorted(jobs, key=lambda item: item["next_run_time"] or "9999")
    def _page_calendar(self, jobs: list) -> list:
        calendar = {}
        for job in jobs:
            next_run_time = str(job.get("next_run_time") or "")
            if not next_run_time:
                continue
            date_key = next_run_time[:10]
            time_key = next_run_time[11:16] if len(next_run_time) >= 16 else ""
            calendar.setdefault(date_key, []).append(
                {
                    "id": job.get("id", ""),
                    "name": job.get("display_name") or job.get("name") or job.get("id") or "任务",
                    "time": time_key,
                    "next_run_time": next_run_time,
                    "trigger": job.get("trigger", ""),
                }
            )
        return [
            {"date": date, "items": sorted(items, key=lambda item: item["next_run_time"])}
            for date, items in sorted(calendar.items())
        ]
