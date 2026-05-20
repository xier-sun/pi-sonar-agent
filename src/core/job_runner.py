"""Execution adapter for queued manual jobs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.db_client import MySQLClient
from pi_sonar_agent.core.events import EventKind, StateEvent
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.run_coordinator import RunCoordinator, TargetRunOptions
from pi_sonar_agent.core.run_logging import RunLogSession
from pi_sonar_agent.core.state import (
    RunState,
    derive_run_status,
    summarize_run_performance,
    utc_now_iso,
)
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.core.target_config import TargetConfig


@dataclass(frozen=True)
class JobExecutionResult:
    """Outcome of one queued job execution."""

    job_id: str
    run_label: str
    status: str
    ok: bool
    pr_url: str
    target_summary_path: str
    run_log_path: str
    error_message: str


class JobRunner:
    """Run one queued manual job through the existing coordinator."""

    def __init__(
        self,
        *,
        db_client: MySQLClient | None = None,
        runtime_loader: Callable[..., Any] | None = None,
        coordinator_factory: Callable[[Any], RunCoordinator] = RunCoordinator,
        state_store_factory: Callable[..., RunStateStore] = RunStateStore,
    ) -> None:
        self.db_client = db_client
        self.runtime_loader = runtime_loader or _default_runtime_loader
        self.coordinator_factory = coordinator_factory
        self.state_store_factory = state_store_factory

    def run_job(
        self,
        job: Any,
        *,
        on_started: Callable[[str, str], None] | None = None,
    ) -> JobExecutionResult:
        """Execute one claimed job and return the final outcome."""

        payload = dict(getattr(job, "target_payload", {}) or {})
        run_label = time.strftime("%Y%m%d%H%M%S")
        run_started_at = utc_now_iso()
        runtime_env = self.runtime_loader(
            default_workspace_root=str(payload.get("workspace_root", "") or ""),
        )
        state_store = self.state_store_factory(db_client=self.db_client)
        coordinator = self.coordinator_factory(runtime_env)
        coordinator.state_store = state_store
        target_config = build_target_config_from_job(job)
        target_identity = {
            "repository": target_config.repository,
            "author": target_config.author,
            "project_key": target_config.project_key,
        }
        run_started_recorded = False
        run_log_path = ""
        try:
            with RunLogSession(run_label=run_label, prefix="job") as log_session:
                run_log_path = log_session.log_path.as_posix()
                if on_started is not None:
                    on_started(run_label, run_log_path)
                print(f"[INFO] Job {job.job_id} started")
                print(f"[INFO] 运行日志: {run_log_path}")
                state_store.record_event(
                    StateEvent(
                        kind=EventKind.RUN_STARTED,
                        run_label=run_label,
                        entity_type="run",
                        entity_key=run_label,
                        repository=target_config.repository,
                        author=target_config.author,
                        project_key=target_config.project_key,
                        status="running",
                        payload={
                            "mode": "manual_job",
                            "job_id": job.job_id,
                            "base_branch": target_config.base_branch,
                        },
                    )
                )
                run_started_recorded = True
                result = coordinator.run_target(
                    target_config,
                    TargetRunOptions(
                        run_label=run_label,
                        keep_workspace=_bool_flag(payload.get("keep_workspace")),
                        skip_build=_bool_flag(
                            payload.get("skip_build_gate", payload.get("skip_build"))
                        ),
                        show_banner=False,
                    ),
                )
                target_states = (result.target_state,) if result.target_state is not None else ()
                rollout_flags = load_performance_flags().enabled_flags()
                run_state = RunState(
                    run_label=run_label,
                    status=derive_run_status(target_states),
                    targets=target_states,
                    started_at=run_started_at,
                    finished_at=utc_now_iso(),
                    performance_summary=summarize_run_performance(
                        target_states,
                        rollout_flags=rollout_flags,
                    ),
                    rollout_flags=rollout_flags,
                )
                run_summary_path = ArtifactWriter().write_run_state(run_state)
                state_store.record_run_state(run_state, artifact_path=run_summary_path.as_posix())
                state_store.record_event(
                    StateEvent(
                        kind=EventKind.RUN_FINISHED,
                        run_label=run_label,
                        entity_type="run",
                        entity_key=run_label,
                        repository=target_config.repository,
                        author=target_config.author,
                        project_key=target_config.project_key,
                        status=run_state.status.value,
                        artifact_path=run_summary_path.as_posix(),
                        payload=run_state.to_dict(),
                    )
                )
                return JobExecutionResult(
                    job_id=job.job_id,
                    run_label=run_label,
                    status=result.status or run_state.status.value,
                    ok=bool(result.ok),
                    pr_url=result.pr_url,
                    target_summary_path=result.target_summary_path or run_summary_path.as_posix(),
                    run_log_path=run_log_path,
                    error_message=result.pr_error or "",
                )
        except Exception as exc:
            state_store.record_run_aborted(
                run_label=run_label,
                status="failed",
                repository=target_identity.get("repository", ""),
                author=target_identity.get("author", ""),
                project_key=target_identity.get("project_key", ""),
                error=str(exc),
                startup_failure=not run_started_recorded,
                payload={
                    "mode": "manual_job",
                    "job_id": job.job_id,
                    "run_started_recorded": run_started_recorded,
                },
            )
            return JobExecutionResult(
                job_id=job.job_id,
                run_label=run_label,
                status="failed",
                ok=False,
                pr_url="",
                target_summary_path="",
                run_log_path=run_log_path,
                error_message=str(exc),
            )


def build_target_config_from_job(job: Any) -> TargetConfig:
    """Project one queued job into the existing TargetConfig contract."""

    payload = dict(getattr(job, "target_payload", {}) or {})
    build_command = str(payload.get("build_command", "") or "").strip() or "dotnet build"
    test_command = str(payload.get("test_command", "") or "").strip() or None
    solution_path = str(payload.get("solution_path", "") or "").strip() or None
    return TargetConfig(
        project_key=str(getattr(job, "project_key", "") or payload.get("project_key", "") or "").strip(),
        repository=str(getattr(job, "repository", "") or payload.get("repository", "") or "").strip(),
        author=str(getattr(job, "author", "") or payload.get("author", "") or "").strip(),
        reviewer_email=str(
            getattr(job, "reviewer_email", "") or payload.get("reviewer_email", "") or ""
        ).strip(),
        dingtalk_userid=str(
            getattr(job, "dingtalk_userid", "") or payload.get("dingtalk_userid", "") or ""
        ).strip(),
        base_branch=str(getattr(job, "base_branch", "") or payload.get("base_branch", "") or "").strip(),
        base_branch_source="run_jobs.base_branch",
        build_command=build_command,
        test_command=test_command,
        solution_path=solution_path,
        max_issues=int(getattr(job, "max_issues", 0) or payload.get("max_issues", 0) or 0),
        issue_keys=_normalize_issue_keys(
            getattr(job, "issue_keys", ()) or payload.get("issue_keys", ()) or ()
        ),
        skip_issue_keys=_normalize_issue_keys(
            getattr(job, "skip_issue_keys", ()) or payload.get("skip_issue_keys", ()) or ()
        ),
    )


def _bool_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_issue_keys(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
        return tuple(dict.fromkeys(item for item in items if item))
    if isinstance(value, (list, tuple, set)):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return ()


def _default_runtime_loader(*, default_workspace_root: str = "") -> Any:
    from pi_sonar_agent.core.preflight import load_runtime_environment

    kwargs: dict[str, Any] = {}
    if default_workspace_root.strip():
        kwargs["default_workspace_root"] = default_workspace_root.strip()
    return load_runtime_environment(**kwargs)


__all__ = [
    "JobExecutionResult",
    "JobRunner",
    "build_target_config_from_job",
]
