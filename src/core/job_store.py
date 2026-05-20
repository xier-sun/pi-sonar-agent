"""Job persistence helpers for DingTalk-triggered manual runs."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pi_sonar_agent.core.db_client import MySQLClient, create_mysql_client_from_env

RUN_JOB_STATUSES = frozenset(
    {
        "pending",
        "awaiting_confirmation",
        "queued",
        "running",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "timeout",
    }
)


@dataclass(frozen=True)
class RunJob:
    """One persisted manual-trigger job."""

    id: int
    job_id: str
    status: str
    trigger_source: str
    trigger_user_id: str
    trigger_user_name: str
    conversation_type: str
    conversation_id: str
    repository: str
    project_key: str
    author: str
    base_branch: str
    issue_keys: tuple[str, ...]
    skip_issue_keys: tuple[str, ...]
    max_issues: int
    reviewer_email: str
    dingtalk_userid: str
    target_payload: dict[str, Any]
    confirmation_token: str
    confirmation_card_instance_id: str
    confirmed_at: str
    queued_at: str
    started_at: str
    finished_at: str
    run_label: str
    result_status: str
    pr_url: str
    target_summary_path: str
    run_log_path: str
    error_message: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DingTalkCommandRecord:
    """One raw DingTalk command audit row."""

    id: int
    job_id: str
    message_id: str
    sender_staff_id: str
    sender_nick: str
    raw_text: str
    parsed_command: dict[str, Any]
    parse_status: str
    parse_error: str
    created_at: str


class JobStore:
    """Storage facade for run_jobs and dingtalk_command_records."""

    def __init__(self, db_client: MySQLClient) -> None:
        self.db_client = db_client
        self.db_client.ensure_tables()

    def create_job(
        self,
        *,
        repository: str,
        project_key: str,
        author: str,
        base_branch: str,
        issue_keys: tuple[str, ...] = (),
        skip_issue_keys: tuple[str, ...] = (),
        max_issues: int = 0,
        reviewer_email: str = "",
        dingtalk_userid: str = "",
        target_payload: dict[str, Any] | None = None,
        trigger_source: str = "manual_seed",
        trigger_user_id: str = "",
        trigger_user_name: str = "",
        conversation_type: str = "",
        conversation_id: str = "",
        await_confirmation: bool = False,
        job_id: str = "",
        confirmation_token: str = "",
    ) -> RunJob:
        """Create one job row and return the normalized record."""

        normalized_issue_keys = _normalize_issue_keys(issue_keys)
        normalized_skip_issue_keys = _normalize_issue_keys(skip_issue_keys)
        now = datetime.now()
        status = "awaiting_confirmation" if await_confirmation else "queued"
        resolved_job_id = job_id or _generate_job_id(now)
        resolved_confirmation_token = (
            confirmation_token
            or (secrets.token_urlsafe(18) if await_confirmation else "")
        )
        payload = dict(target_payload or {})
        payload.update(
            {
                "repository": repository,
                "project_key": project_key,
                "author": author,
                "base_branch": base_branch,
                "issue_keys": list(normalized_issue_keys),
                "skip_issue_keys": list(normalized_skip_issue_keys),
                "max_issues": int(max_issues),
                "reviewer_email": reviewer_email,
                "dingtalk_userid": dingtalk_userid,
            }
        )
        self.db_client.insert_run_job(
            job_id=resolved_job_id,
            status=status,
            trigger_source=trigger_source,
            trigger_user_id=trigger_user_id,
            trigger_user_name=trigger_user_name,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            repository=repository,
            project_key=project_key,
            author=author,
            base_branch=base_branch,
            issue_keys_json=json.dumps(list(normalized_issue_keys), ensure_ascii=False),
            skip_issue_keys_json=json.dumps(list(normalized_skip_issue_keys), ensure_ascii=False),
            max_issues=int(max_issues),
            reviewer_email=reviewer_email,
            dingtalk_userid=dingtalk_userid,
            target_payload_json=json.dumps(payload, ensure_ascii=False),
            confirmation_token=resolved_confirmation_token,
            queued_at=(None if await_confirmation else now),
        )
        created = self.get_job(resolved_job_id)
        if created is None:
            raise RuntimeError(f"创建任务后未能重新读取 job: {resolved_job_id}")
        return created

    def record_command(
        self,
        *,
        raw_text: str,
        parse_status: str,
        job_id: str = "",
        message_id: str = "",
        sender_staff_id: str = "",
        sender_nick: str = "",
        parsed_command: dict[str, Any] | None = None,
        parse_error: str = "",
    ) -> DingTalkCommandRecord:
        """Persist one original DingTalk command record."""

        record_id = self.db_client.insert_dingtalk_command_record(
            job_id=job_id,
            message_id=message_id,
            sender_staff_id=sender_staff_id,
            sender_nick=sender_nick,
            raw_text=raw_text,
            parsed_command_json=json.dumps(parsed_command or {}, ensure_ascii=False),
            parse_status=parse_status,
            parse_error=parse_error,
        )
        return DingTalkCommandRecord(
            id=record_id,
            job_id=job_id,
            message_id=message_id,
            sender_staff_id=sender_staff_id,
            sender_nick=sender_nick,
            raw_text=raw_text,
            parsed_command=dict(parsed_command or {}),
            parse_status=parse_status,
            parse_error=parse_error,
            created_at="",
        )

    def get_command_record_by_message_id(self, message_id: str) -> DingTalkCommandRecord | None:
        """Read one original command row by message_id for idempotency checks."""

        row = self.db_client.get_dingtalk_command_record_by_message_id(message_id)
        return _row_to_command(row) if row else None

    def get_job(self, job_id: str) -> RunJob | None:
        """Read one job by job_id."""

        row = self.db_client.get_run_job_by_job_id(job_id)
        return _row_to_job(row) if row else None

    def get_job_by_confirmation_token(self, token: str) -> RunJob | None:
        """Read one job by confirmation token."""

        row = self.db_client.get_run_job_by_confirmation_token(token)
        return _row_to_job(row) if row else None

    def get_job_by_confirmation_card_instance_id(self, card_instance_id: str) -> RunJob | None:
        """Read one job by confirmation card instance id."""

        row = self.db_client.get_run_job_by_confirmation_card_instance_id(card_instance_id)
        return _row_to_job(row) if row else None

    def get_latest_job_for_user(self, trigger_user_id: str) -> RunJob | None:
        """Read the most recent job created by one trigger user."""

        row = self.db_client.get_latest_run_job_for_user(trigger_user_id)
        return _row_to_job(row) if row else None

    def list_jobs(self, *, status: str = "", limit: int = 50) -> list[RunJob]:
        """List recent jobs."""

        return [
            _row_to_job(row)
            for row in self.db_client.list_run_jobs(status=status, limit=limit)
        ]

    def count_active_jobs_for_user(self, trigger_user_id: str) -> int:
        """Count active jobs owned by one trigger user."""

        return self.db_client.count_run_jobs(
            trigger_user_id=trigger_user_id,
            statuses=("awaiting_confirmation", "queued", "running"),
        )

    def count_recent_jobs_for_user(
        self,
        trigger_user_id: str,
        *,
        window_seconds: int,
    ) -> int:
        """Count recently created jobs for one trigger user."""

        threshold = datetime.now() - timedelta(seconds=max(int(window_seconds), 1))
        return self.db_client.count_run_jobs(
            trigger_user_id=trigger_user_id,
            created_after=threshold,
        )

    def create_rerun_job(
        self,
        original_job: RunJob,
        *,
        trigger_user_id: str,
        trigger_user_name: str,
        conversation_type: str,
        conversation_id: str,
    ) -> RunJob:
        """Create one awaiting-confirmation rerun job from an existing historical job."""

        return self.create_job(
            repository=original_job.repository,
            project_key=original_job.project_key,
            author=original_job.author,
            base_branch=original_job.base_branch,
            issue_keys=original_job.issue_keys,
            skip_issue_keys=original_job.skip_issue_keys,
            max_issues=original_job.max_issues,
            reviewer_email=original_job.reviewer_email,
            dingtalk_userid=str(original_job.dingtalk_userid or "").strip()
            or str(trigger_user_id or "").strip(),
            target_payload=original_job.target_payload,
            trigger_source="dingtalk_rerun",
            trigger_user_id=trigger_user_id,
            trigger_user_name=trigger_user_name,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            await_confirmation=True,
        )

    def attach_confirmation_card_instance(
        self,
        *,
        job_id: str,
        confirmation_card_instance_id: str,
    ) -> RunJob | None:
        """Persist the delivered confirmation card instance id for later callbacks."""

        if not str(job_id or "").strip() or not str(confirmation_card_instance_id or "").strip():
            return self.get_job(job_id)
        self.db_client.update_run_job_fields(
            job_id,
            {"confirmation_card_instance_id": confirmation_card_instance_id},
        )
        return self.get_job(job_id)

    def confirm_job(self, *, job_id: str, confirmation_token: str) -> RunJob | None:
        """Confirm one awaiting job and move it to queued."""

        job = self.get_job(job_id)
        if job is None or job.confirmation_token != confirmation_token:
            return None
        if job.status == "queued":
            return job
        if job.status != "awaiting_confirmation":
            return None
        now = datetime.now()
        self.db_client.update_run_job_fields(
            job_id,
            {
                "status": "queued",
                "confirmed_at": now,
                "queued_at": now,
            },
        )
        return self.get_job(job_id)

    def confirm_job_by_job_id(self, *, job_id: str) -> RunJob | None:
        """Confirm one awaiting job without requiring the confirmation token."""

        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status == "queued":
            return job
        if job.status != "awaiting_confirmation":
            return None
        now = datetime.now()
        self.db_client.update_run_job_fields(
            job_id,
            {
                "status": "queued",
                "confirmed_at": now,
                "queued_at": now,
            },
        )
        return self.get_job(job_id)

    def cancel_job(self, *, job_id: str, confirmation_token: str) -> RunJob | None:
        """Cancel one waiting/queued job before execution really starts."""

        job = self.get_job(job_id)
        if job is None or job.confirmation_token != confirmation_token:
            return None
        if job.status == "cancelled":
            return job
        if job.status not in {"awaiting_confirmation", "queued"}:
            return None
        now = datetime.now()
        self.db_client.update_run_job_fields(
            job_id,
            {
                "status": "cancelled",
                "result_status": "cancelled",
                "error_message": "cancelled by user before execution",
                "finished_at": now,
            },
        )
        return self.get_job(job_id)

    def cancel_job_by_job_id(self, *, job_id: str) -> RunJob | None:
        """Cancel one waiting/queued job without requiring the confirmation token."""

        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status == "cancelled":
            return job
        if job.status not in {"awaiting_confirmation", "queued"}:
            return None
        now = datetime.now()
        self.db_client.update_run_job_fields(
            job_id,
            {
                "status": "cancelled",
                "result_status": "cancelled",
                "error_message": "cancelled by user before execution",
                "finished_at": now,
            },
        )
        return self.get_job(job_id)

    def claim_next_job(self) -> RunJob | None:
        """Claim the next queued job for execution."""

        row = self.db_client.claim_next_run_job()
        return _row_to_job(row) if row else None

    def mark_job_run_context(
        self,
        job_id: str,
        *,
        run_label: str,
        run_log_path: str,
    ) -> None:
        """Attach run context once execution really starts."""

        self.db_client.update_run_job_fields(
            job_id,
            {
                "run_label": run_label,
                "run_log_path": run_log_path,
            },
        )

    def mark_job_finished(
        self,
        job_id: str,
        *,
        status: str,
        result_status: str,
        pr_url: str = "",
        target_summary_path: str = "",
        run_log_path: str = "",
        error_message: str = "",
    ) -> RunJob | None:
        """Mark one job as terminal."""

        _validate_status(status)
        now = datetime.now()
        self.db_client.update_run_job_fields(
            job_id,
            {
                "status": status,
                "result_status": result_status,
                "pr_url": pr_url,
                "target_summary_path": target_summary_path,
                "run_log_path": run_log_path,
                "error_message": error_message,
                "finished_at": now,
            },
        )
        return self.get_job(job_id)

    def mark_job_failed(
        self,
        job_id: str,
        *,
        error_message: str,
        run_log_path: str = "",
        target_summary_path: str = "",
    ) -> RunJob | None:
        """Convenience helper for terminal failed jobs."""

        return self.mark_job_finished(
            job_id,
            status="failed",
            result_status="failed",
            error_message=error_message,
            run_log_path=run_log_path,
            target_summary_path=target_summary_path,
        )

    def recover_stale_running_jobs(self, *, timeout_seconds: int) -> int:
        """Mark stale running jobs as timeout so the queue can recover."""

        threshold = datetime.now() - timedelta(seconds=max(int(timeout_seconds), 1))
        return self.db_client.mark_stale_running_jobs_timed_out(
            timeout_before=threshold,
            error_message=(
                f"job exceeded running timeout ({int(timeout_seconds)}s) and was marked timeout"
            ),
        )


def create_job_store_from_env() -> JobStore | None:
    """Create one job store from repository .env DB settings."""

    db_client = create_mysql_client_from_env()
    if db_client is None:
        return None
    return JobStore(db_client)


def _normalize_issue_keys(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
        return tuple(dict.fromkeys(item for item in items if item))
    if isinstance(value, (list, tuple, set)):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return ()


def _parse_json_dict(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _parse_json_strings(text: str) -> tuple[str, ...]:
    raw = str(text or "").strip()
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except Exception:
        return ()
    if isinstance(value, list):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return ()


def _generate_job_id(now: datetime) -> str:
    return f"JOB-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"


def _row_to_job(row: dict[str, Any]) -> RunJob:
    return RunJob(
        id=int(row.get("id", 0) or 0),
        job_id=str(row.get("job_id", "") or ""),
        status=str(row.get("status", "") or ""),
        trigger_source=str(row.get("trigger_source", "") or ""),
        trigger_user_id=str(row.get("trigger_user_id", "") or ""),
        trigger_user_name=str(row.get("trigger_user_name", "") or ""),
        conversation_type=str(row.get("conversation_type", "") or ""),
        conversation_id=str(row.get("conversation_id", "") or ""),
        repository=str(row.get("repository", "") or ""),
        project_key=str(row.get("project_key", "") or ""),
        author=str(row.get("author", "") or ""),
        base_branch=str(row.get("base_branch", "") or ""),
        issue_keys=_parse_json_strings(str(row.get("issue_keys_json", "") or "")),
        skip_issue_keys=_parse_json_strings(str(row.get("skip_issue_keys_json", "") or "")),
        max_issues=int(row.get("max_issues", 0) or 0),
        reviewer_email=str(row.get("reviewer_email", "") or ""),
        dingtalk_userid=str(row.get("dingtalk_userid", "") or ""),
        target_payload=_parse_json_dict(str(row.get("target_payload_json", "") or "")),
        confirmation_token=str(row.get("confirmation_token", "") or ""),
        confirmation_card_instance_id=str(row.get("confirmation_card_instance_id", "") or ""),
        confirmed_at=str(row.get("confirmed_at", "") or ""),
        queued_at=str(row.get("queued_at", "") or ""),
        started_at=str(row.get("started_at", "") or ""),
        finished_at=str(row.get("finished_at", "") or ""),
        run_label=str(row.get("run_label", "") or ""),
        result_status=str(row.get("result_status", "") or ""),
        pr_url=str(row.get("pr_url", "") or ""),
        target_summary_path=str(row.get("target_summary_path", "") or ""),
        run_log_path=str(row.get("run_log_path", "") or ""),
        error_message=str(row.get("error_message", "") or ""),
        created_at=str(row.get("created_at", "") or ""),
        updated_at=str(row.get("updated_at", "") or ""),
    )


def _row_to_command(row: dict[str, Any]) -> DingTalkCommandRecord:
    return DingTalkCommandRecord(
        id=int(row.get("id", 0) or 0),
        job_id=str(row.get("job_id", "") or ""),
        message_id=str(row.get("message_id", "") or ""),
        sender_staff_id=str(row.get("sender_staff_id", "") or ""),
        sender_nick=str(row.get("sender_nick", "") or ""),
        raw_text=str(row.get("raw_text", "") or ""),
        parsed_command=_parse_json_dict(str(row.get("parsed_command_json", "") or "")),
        parse_status=str(row.get("parse_status", "") or ""),
        parse_error=str(row.get("parse_error", "") or ""),
        created_at=str(row.get("created_at", "") or ""),
    )


def _validate_status(status: str) -> None:
    if status not in RUN_JOB_STATUSES:
        raise ValueError(f"Unsupported run job status: {status}")


__all__ = [
    "DingTalkCommandRecord",
    "JobStore",
    "RUN_JOB_STATUSES",
    "RunJob",
    "create_job_store_from_env",
]
