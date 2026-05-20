"""Access control and rate limiting for DingTalk-triggered manual jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.job_store import JobStore, RunJob
from pi_sonar_agent.core.project_env import read_project_env


@dataclass(frozen=True)
class DingTalkAccessDecision:
    """Decision returned by DingTalk access policy checks."""

    allowed: bool
    status: str
    message: str


class DingTalkAccessPolicy:
    """Apply whitelist, cancel authority, and basic rate limiting."""

    def __init__(
        self,
        *,
        allowed_staff_ids: tuple[str, ...] = (),
        admin_staff_ids: tuple[str, ...] = (),
        allowed_conversation_ids: tuple[str, ...] = (),
        max_active_jobs_per_user: int = 0,
        max_jobs_per_window: int = 0,
        window_seconds: int = 600,
    ) -> None:
        self.allowed_staff_ids = _normalize_csv_values(allowed_staff_ids)
        self.admin_staff_ids = _normalize_csv_values(admin_staff_ids)
        self.allowed_conversation_ids = _normalize_csv_values(allowed_conversation_ids)
        self.max_active_jobs_per_user = max(int(max_active_jobs_per_user or 0), 0)
        self.max_jobs_per_window = max(int(max_jobs_per_window or 0), 0)
        self.window_seconds = max(int(window_seconds or 0), 0)

    def evaluate_trigger(
        self,
        *,
        job_store: JobStore,
        sender_staff_id: str,
        conversation_id: str,
    ) -> DingTalkAccessDecision:
        """Evaluate whether one incoming trigger command is allowed."""

        if self.allowed_staff_ids and sender_staff_id not in self.allowed_staff_ids:
            return DingTalkAccessDecision(
                allowed=False,
                status="unauthorized",
                message="当前用户不在钉钉触发白名单中，无法创建修复任务。",
            )
        if self.allowed_conversation_ids and conversation_id not in self.allowed_conversation_ids:
            return DingTalkAccessDecision(
                allowed=False,
                status="unauthorized",
                message="当前会话不在允许触发的钉钉群范围内，无法创建修复任务。",
            )
        if sender_staff_id and self.max_active_jobs_per_user > 0:
            active_count = job_store.count_active_jobs_for_user(sender_staff_id)
            if active_count >= self.max_active_jobs_per_user:
                return DingTalkAccessDecision(
                    allowed=False,
                    status="rate_limited",
                    message=(
                        "当前用户已有过多待处理任务，请等待已有任务完成后再发起新的修复请求。"
                    ),
                )
        if (
            sender_staff_id
            and self.max_jobs_per_window > 0
            and self.window_seconds > 0
        ):
            recent_count = job_store.count_recent_jobs_for_user(
                sender_staff_id,
                window_seconds=self.window_seconds,
            )
            if recent_count >= self.max_jobs_per_window:
                return DingTalkAccessDecision(
                    allowed=False,
                    status="rate_limited",
                    message=(
                        f"当前用户在 {self.window_seconds} 秒内创建任务过于频繁，请稍后再试。"
                    ),
                )
        return DingTalkAccessDecision(allowed=True, status="allowed", message="")

    def can_cancel_job(self, *, job: RunJob, requester_staff_id: str) -> bool:
        """Check whether one requester can cancel the target job."""

        requester = str(requester_staff_id or "").strip()
        if not requester:
            return False
        if requester == str(job.trigger_user_id or "").strip():
            return True
        return requester in self.admin_staff_ids

    def can_confirm_job(self, *, job: RunJob, requester_staff_id: str) -> bool:
        """Check whether one requester can confirm the target job."""

        return self.can_cancel_job(job=job, requester_staff_id=requester_staff_id)

    def can_view_job(self, *, job: RunJob, requester_staff_id: str) -> bool:
        """Check whether one requester can inspect the target job."""

        return self.can_cancel_job(job=job, requester_staff_id=requester_staff_id)

    def can_rerun_job(self, *, job: RunJob, requester_staff_id: str) -> bool:
        """Check whether one requester can create a rerun from the target job."""

        return self.can_cancel_job(job=job, requester_staff_id=requester_staff_id)


def create_dingtalk_access_policy_from_env() -> DingTalkAccessPolicy:
    """Build one access policy from repository `.env` values."""

    env = read_project_env()
    return DingTalkAccessPolicy(
        allowed_staff_ids=_normalize_csv_values(env.get("DINGTALK_ALLOWED_STAFF_IDS", "")),
        admin_staff_ids=_normalize_csv_values(env.get("DINGTALK_ADMIN_STAFF_IDS", "")),
        allowed_conversation_ids=_normalize_csv_values(
            env.get("DINGTALK_ALLOWED_CONVERSATION_IDS", "")
        ),
        max_active_jobs_per_user=int(env.get("DINGTALK_TRIGGER_MAX_ACTIVE_JOBS_PER_USER", "0") or 0),
        max_jobs_per_window=int(env.get("DINGTALK_TRIGGER_MAX_JOBS_PER_WINDOW", "0") or 0),
        window_seconds=int(env.get("DINGTALK_TRIGGER_WINDOW_SECONDS", "600") or 600),
    )


def _normalize_csv_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.replace("，", ",")
        items = [item.strip() for item in normalized.split(",")]
        return tuple(dict.fromkeys(item for item in items if item))
    if isinstance(value, (list, tuple, set)):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return ()


__all__ = [
    "DingTalkAccessDecision",
    "DingTalkAccessPolicy",
    "create_dingtalk_access_policy_from_env",
]
