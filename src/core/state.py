"""Structured run state models for issue processing artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class RunStatus(StrEnum):
    """Status of a run across one or more targets."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class TargetStatus(StrEnum):
    """Status of a single target execution."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class IssueStatus(StrEnum):
    """Final status of a single Sonar issue."""

    PENDING = "pending"
    FIXED = "fixed"
    SKIPPED = "skipped"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    """Lifecycle status for one attempt of an issue."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    FAILED = "failed"


class RetryReason(StrEnum):
    """Why the next attempt should be retried."""

    NONE = "none"
    RETRYABLE_FAILURE = "retryable_failure"
    BUILD_VERIFICATION_FAILED = "build_verification_failed"


@dataclass(frozen=True)
class WorkspaceBaseline:
    """Snapshot of the workspace state before an issue attempt starts."""

    head_commit: str
    snapshot_dir: Path
    patch_path: Path
    tracked_root: Path
    tracked_files: tuple[str, ...]
    untracked_root: Path
    untracked_files: tuple[str, ...]


@dataclass(frozen=True)
class AttemptState:
    """Structured summary of a single issue attempt."""

    attempt_number: int
    status: AttemptStatus
    started_at: str
    finished_at: str
    duration_seconds: float
    failure_kind: str = ""
    retry_reason: RetryReason = RetryReason.NONE
    retryable_failure: bool = False
    build_passed: bool = False
    build_verification_failed: bool = False
    error: str = ""
    skip_reason: str = ""
    summary: str = ""
    changed_files: tuple[str, ...] = ()
    boundary_failure_code: str = ""
    secondary_boundary_failure_codes: tuple[str, ...] = ()
    issue_log_path: str = ""
    artifact_dir: str = ""
    performance_metrics: dict[str, Any] = field(default_factory=dict)
    rollout_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the attempt state to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class IssueState:
    """Structured summary for all attempts of a single issue."""

    issue_key: str
    repository: str
    run_label: str
    rule_id: str
    file_path: str
    line: int
    status: IssueStatus
    attempts: tuple[AttemptState, ...] = ()
    final_failure_kind: str = ""
    final_error: str = ""
    final_skip_reason: str = ""
    final_summary: str = ""
    final_boundary_failure_code: str = ""
    final_secondary_boundary_failure_codes: tuple[str, ...] = ()
    artifact_root: str = ""
    performance_summary: dict[str, Any] = field(default_factory=dict)
    rollout_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the issue state to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class TargetState:
    """Structured summary for a single target execution."""

    run_label: str
    project_key: str
    repository: str
    author: str
    base_branch: str
    status: TargetStatus
    issues: tuple[IssueState, ...] = ()
    started_at: str = ""
    finished_at: str = ""
    performance_summary: dict[str, Any] = field(default_factory=dict)
    rollout_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the target state to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class RunState:
    """Structured summary for a complete run."""

    run_label: str
    status: RunStatus
    targets: tuple[TargetState, ...] = ()
    started_at: str = ""
    finished_at: str = ""
    performance_summary: dict[str, Any] = field(default_factory=dict)
    rollout_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the run state to a JSON-ready dictionary."""

        return serialize_state(self)


def derive_target_status(
    *,
    total_issues: int,
    successful: int,
    skipped: int,
    failed: int,
    build_passed: bool,
) -> TargetStatus:
    """Derive a target status from issue-level and build-level results."""

    if total_issues <= 0:
        if failed > 0 or not build_passed:
            return TargetStatus.FAILED
        return TargetStatus.SUCCEEDED
    if successful == total_issues and skipped == 0 and failed == 0 and build_passed:
        return TargetStatus.SUCCEEDED
    if successful == 0 and skipped == 0 and (failed > 0 or not build_passed):
        return TargetStatus.FAILED
    return TargetStatus.PARTIAL


def derive_run_status(targets: tuple[TargetState, ...] | list[TargetState]) -> RunStatus:
    """Derive the overall run status from all target states."""

    if not targets:
        return RunStatus.SUCCEEDED

    statuses = {target.status for target in targets}
    if statuses == {TargetStatus.SUCCEEDED}:
        return RunStatus.SUCCEEDED
    if statuses == {TargetStatus.FAILED}:
        return RunStatus.FAILED
    return RunStatus.PARTIAL


def summarize_issue_performance(
    attempts: tuple[AttemptState, ...] | list[AttemptState],
    *,
    rollout_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Summarize performance metrics for one issue across all attempts."""

    attempt_list = list(attempts)
    total_duration = round(sum(float(item.duration_seconds) for item in attempt_list), 3)
    attempt_count = len(attempt_list)
    model_timeout_attempts = sum(1 for item in attempt_list if item.failure_kind == "model_timeout")
    scope_reject_attempts = sum(
        1
        for item in attempt_list
        if item.failure_kind == "scope" or str(item.boundary_failure_code).startswith("scope_")
    )
    boundary_hard_reject_attempts = sum(
        1
        for item in attempt_list
        if item.failure_kind == "reviewer"
        and str(item.boundary_failure_code).startswith("filesystem_")
    )
    build_invocations = sum(
        1
        for item in attempt_list
        if bool(item.performance_metrics.get("build_invoked"))
        or float(item.performance_metrics.get("build_duration_seconds", 0.0) or 0.0) > 0
    )
    salvaged_timeout_count = sum(
        1
        for item in attempt_list
        if bool(item.performance_metrics.get("patch_salvaged"))
    )
    follow_up_timeout_count = sum(
        1
        for item in attempt_list
        if str(item.performance_metrics.get("model_timeout_stage", "")).startswith("post_")
    )
    return {
        "issue_attempt_count": attempt_count,
        "issue_total_duration_seconds": total_duration,
        "avg_attempt_duration_seconds": round(total_duration / attempt_count, 3) if attempt_count else 0.0,
        "model_timeout_attempts": model_timeout_attempts,
        "scope_reject_attempts": scope_reject_attempts,
        "boundary_hard_reject_attempts": boundary_hard_reject_attempts,
        "build_invocation_count": build_invocations,
        "follow_up_timeout_attempts": follow_up_timeout_count,
        "patch_salvaged_attempts": salvaged_timeout_count,
        "rollout_flags": list(rollout_flags),
    }


def summarize_target_performance(
    issues: tuple[IssueState, ...] | list[IssueState],
    *,
    rollout_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Summarize performance metrics for one target run."""

    issue_list = list(issues)
    attempt_list = [attempt for issue in issue_list for attempt in issue.attempts]
    attempt_count = len(attempt_list)
    issue_count = len(issue_list)
    issue_durations = [
        float(issue.performance_summary.get("issue_total_duration_seconds", 0.0) or 0.0)
        for issue in issue_list
    ]
    sorted_issue_durations = sorted(value for value in issue_durations if value > 0)
    if sorted_issue_durations:
        percentile_index = max(0, min(len(sorted_issue_durations) - 1, int(round((len(sorted_issue_durations) - 1) * 0.95))))
        p95_issue_duration_seconds = round(sorted_issue_durations[percentile_index], 3)
    else:
        p95_issue_duration_seconds = 0.0

    model_timeout_attempts = sum(1 for item in attempt_list if item.failure_kind == "model_timeout")
    scope_reject_attempts = sum(
        1
        for item in attempt_list
        if item.failure_kind == "scope" or str(item.boundary_failure_code).startswith("scope_")
    )
    boundary_hard_reject_attempts = sum(
        1
        for item in attempt_list
        if item.failure_kind == "reviewer"
        and str(item.boundary_failure_code).startswith("filesystem_")
    )
    follow_up_timeout_attempts = sum(
        1
        for item in attempt_list
        if str(item.performance_metrics.get("model_timeout_stage", "")).startswith("post_")
    )
    build_durations = [
        float(item.performance_metrics.get("build_duration_seconds", 0.0) or 0.0)
        for item in attempt_list
        if bool(item.performance_metrics.get("build_invoked"))
        or float(item.performance_metrics.get("build_duration_seconds", 0.0) or 0.0) > 0
    ]
    fast_path_attempts = sum(
        1 for item in attempt_list if bool(item.performance_metrics.get("fast_path_enabled"))
    )
    salvaged_timeout_count = sum(
        1 for item in attempt_list if bool(item.performance_metrics.get("patch_salvaged"))
    )
    return {
        "issue_count": issue_count,
        "attempt_count": attempt_count,
        "avg_attempts_per_issue": round(attempt_count / issue_count, 3) if issue_count else 0.0,
        "avg_issue_duration_seconds": round(sum(issue_durations) / issue_count, 3) if issue_count else 0.0,
        "p95_issue_duration_seconds": p95_issue_duration_seconds,
        "model_timeout_rate": round(model_timeout_attempts / attempt_count, 4) if attempt_count else 0.0,
        "scope_reject_rate": round(scope_reject_attempts / attempt_count, 4) if attempt_count else 0.0,
        "boundary_hard_reject_rate": round(boundary_hard_reject_attempts / attempt_count, 4) if attempt_count else 0.0,
        "follow_up_timeout_rate": round(follow_up_timeout_attempts / attempt_count, 4) if attempt_count else 0.0,
        "build_invocation_rate": round(len(build_durations) / attempt_count, 4) if attempt_count else 0.0,
        "avg_build_duration_seconds": round(sum(build_durations) / len(build_durations), 3) if build_durations else 0.0,
        "fast_path_attempt_count": fast_path_attempts,
        "patch_salvaged_attempts": salvaged_timeout_count,
        "rollout_flags": list(rollout_flags),
    }


def summarize_run_performance(
    targets: tuple[TargetState, ...] | list[TargetState],
    *,
    rollout_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Summarize performance metrics for a complete run."""

    target_list = list(targets)
    issue_count = sum(len(target.issues) for target in target_list)
    attempt_count = sum(
        len(issue.attempts)
        for target in target_list
        for issue in target.issues
    )
    issue_durations = [
        float(issue.performance_summary.get("issue_total_duration_seconds", 0.0) or 0.0)
        for target in target_list
        for issue in target.issues
    ]
    build_durations = [
        float(attempt.performance_metrics.get("build_duration_seconds", 0.0) or 0.0)
        for target in target_list
        for issue in target.issues
        for attempt in issue.attempts
        if bool(attempt.performance_metrics.get("build_invoked"))
        or float(attempt.performance_metrics.get("build_duration_seconds", 0.0) or 0.0) > 0
    ]
    model_timeout_attempts = sum(
        1
        for target in target_list
        for issue in target.issues
        for attempt in issue.attempts
        if attempt.failure_kind == "model_timeout"
    )
    scope_reject_attempts = sum(
        1
        for target in target_list
        for issue in target.issues
        for attempt in issue.attempts
        if attempt.failure_kind == "scope" or str(attempt.boundary_failure_code).startswith("scope_")
    )
    boundary_hard_reject_attempts = sum(
        1
        for target in target_list
        for issue in target.issues
        for attempt in issue.attempts
        if attempt.failure_kind == "reviewer"
        and str(attempt.boundary_failure_code).startswith("filesystem_")
    )
    return {
        "target_count": len(target_list),
        "issue_count": issue_count,
        "attempt_count": attempt_count,
        "avg_attempts_per_issue": round(attempt_count / issue_count, 3) if issue_count else 0.0,
        "avg_issue_duration_seconds": round(sum(issue_durations) / issue_count, 3) if issue_count else 0.0,
        "model_timeout_rate": round(model_timeout_attempts / attempt_count, 4) if attempt_count else 0.0,
        "scope_reject_rate": round(scope_reject_attempts / attempt_count, 4) if attempt_count else 0.0,
        "boundary_hard_reject_rate": round(boundary_hard_reject_attempts / attempt_count, 4) if attempt_count else 0.0,
        "avg_build_duration_seconds": round(sum(build_durations) / len(build_durations), 3) if build_durations else 0.0,
        "rollout_flags": list(rollout_flags),
    }


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat()


def serialize_state(value: Any) -> Any:
    """Recursively convert state objects into JSON-safe primitives."""

    if is_dataclass(value):
        return {
            field.name: serialize_state(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple):
        return [serialize_state(item) for item in value]
    if isinstance(value, list):
        return [serialize_state(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_state(item) for key, item in value.items()}
    return value
