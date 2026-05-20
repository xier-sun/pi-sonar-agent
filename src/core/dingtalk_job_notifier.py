"""Thin DingTalk notification wrapper for manual-trigger jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.dingtalk import DingTalkCorpClient, create_dingtalk_client_from_env


@dataclass(frozen=True)
class DingTalkJobNotifier:
    """Send start/finish notifications for one manual-trigger job."""

    client: DingTalkCorpClient

    def notify_job_started(
        self,
        job: Any,
        *,
        run_label: str,
        run_log_path: str,
    ) -> dict[str, Any]:
        """Push one running notification."""

        title = f"[RUNNING] Sonar 自动修复开始 - {job.author}"
        text = (
            "## 手动修复任务开始执行\n\n"
            f"- **任务编号**: {job.job_id}\n"
            f"- **仓库**: {job.repository}\n"
            f"- **作者**: {job.author}\n"
            f"- **基线分支**: {job.base_branch}\n"
            f"- **run_label**: {run_label}\n"
        )
        if run_log_path:
            text += f"- **运行日志**: `{run_log_path}`\n"
        return self.client.send_markdown_message(
            title,
            text,
            userid=(job.dingtalk_userid or None),
        )

    def notify_job_finished(
        self,
        job: Any,
        *,
        terminal_status: str,
        result_status: str,
        run_label: str,
        pr_url: str = "",
        target_summary_path: str = "",
        run_log_path: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        """Push one terminal notification."""

        status_tag = _status_tag(terminal_status)
        title = f"{status_tag} Sonar 自动修复完成 - {job.author}"
        text = (
            "## 手动修复任务执行完成\n\n"
            f"- **任务编号**: {job.job_id}\n"
            f"- **仓库**: {job.repository}\n"
            f"- **作者**: {job.author}\n"
            f"- **基线分支**: {job.base_branch}\n"
            f"- **终态状态**: {terminal_status}\n"
            f"- **执行结果**: {result_status}\n"
            f"- **run_label**: {run_label}\n"
        )
        if pr_url:
            text += f"- **PR 链接**: [查看 PR]({pr_url})\n"
        if target_summary_path:
            text += f"- **运行摘要**: `{target_summary_path}`\n"
        if run_log_path:
            text += f"- **运行日志**: `{run_log_path}`\n"
        if error_message:
            text += f"- **附加说明**: {error_message}\n"
        return self.client.send_markdown_message(
            title,
            text,
            userid=(job.dingtalk_userid or None),
        )


def create_dingtalk_job_notifier_from_env() -> DingTalkJobNotifier | None:
    """Create one notifier from repository DingTalk configuration."""

    client = create_dingtalk_client_from_env()
    if client is None:
        return None
    return DingTalkJobNotifier(client=client)


def _status_tag(terminal_status: str) -> str:
    normalized = str(terminal_status or "").strip().lower()
    if normalized == "succeeded":
        return "[SUCCESS]"
    if normalized == "partial":
        return "[WARN]"
    if normalized in {"failed", "timeout"}:
        return "[FAILED]"
    if normalized == "cancelled":
        return "[CANCELLED]"
    return "[INFO]"


__all__ = [
    "DingTalkJobNotifier",
    "create_dingtalk_job_notifier_from_env",
]
