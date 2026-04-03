"""Structured run state models for issue processing artifacts."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
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
    issue_log_path: str = ""
    artifact_dir: str = ""

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
    artifact_root: str = ""

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
