"""Batch runner for pi-sonar-agent.

Reads targets.json and runs fix for each target through the shared coordinator.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.db_client import create_mysql_client_from_env
from pi_sonar_agent.core.events import EventKind, StateEvent
from pi_sonar_agent.core.model_env import load_project_env
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.preflight import load_runtime_environment
from pi_sonar_agent.core.run_coordinator import (
    RunCoordinator,
    TargetRunOptions,
    TargetRunResult,
)
from pi_sonar_agent.core.run_logging import RunLogSession
from pi_sonar_agent.core.state import (
    RunState,
    TargetState,
    derive_run_status,
    summarize_run_performance,
    utc_now_iso,
)
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.core.target_config import (
    missing_required_target_fields,
    resolve_batch_target_config,
)

load_project_env()


DEFAULT_MAX_ISSUES = 3
DEFAULT_BASE_BRANCH = "develop"


def load_targets(config_path: Path) -> list[dict]:
    """Load targets from JSON config file."""
    if not config_path.exists():
        raise RuntimeError(f"未找到配置文件: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("targets.json 根节点必须是数组")

    return data


def run_for_target(
    target: dict,
    coordinator: RunCoordinator,
    *,
    run_label: str,
    show_banner: bool = True,
) -> TargetRunResult:
    """Run fix for a single target through the shared coordinator."""
    target_config = resolve_batch_target_config(
        target,
        default_base_branch=DEFAULT_BASE_BRANCH,
        default_max_issues=DEFAULT_MAX_ISSUES,
    )
    missing_fields = missing_required_target_fields(target_config)
    if missing_fields:
        field = missing_fields[0]
        raise RuntimeError(f"targets.json target 缺少 {field}")

    return coordinator.run_target(
        target_config,
        TargetRunOptions(
            run_label=run_label,
            keep_workspace=_bool_flag(target.get("keep_workspace")),
            skip_build=_bool_flag(target.get("skip_build_gate")),
            show_banner=show_banner,
        ),
    )


def main() -> None:
    """Main entry point."""
    import sys

    config_path = Path("data/targets.json")
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])

    run_label = time.strftime("%Y%m%d%H%M%S")
    run_started_at = utc_now_iso()
    state_store = RunStateStore(db_client=create_mysql_client_from_env())
    run_started_recorded = False
    with RunLogSession(run_label=run_label, prefix="batch") as log_session:
        print(f"[INFO] 运行日志: {log_session.log_path.as_posix()}")
        try:
            runtime_env = load_runtime_environment()
            coordinator = RunCoordinator(runtime_env)
            coordinator.state_store = state_store
            targets = load_targets(config_path)
            state_store.record_event(
                StateEvent(
                    kind=EventKind.RUN_STARTED,
                    run_label=run_label,
                    entity_type="run",
                    entity_key=run_label,
                    status="running",
                    payload={
                        "mode": "batch",
                        "target_count": len(targets),
                        "config_path": config_path.as_posix(),
                    },
                )
            )
            run_started_recorded = True
            print(f"加载 {len(targets)} 个目标")

            total_successful = 0
            total_skipped = 0
            total_failed = 0
            total_prs = 0
            target_states: list[TargetState] = []

            for index, target in enumerate(targets, 1):
                target_run_label = f"{run_label}-{index:02d}"
                result = run_for_target(
                    target,
                    coordinator,
                    run_label=target_run_label,
                    show_banner=True,
                )
                total_successful += result.successful
                total_skipped += result.skipped
                total_failed += result.failed
                if result.pr_url:
                    total_prs += 1
                if result.target_state is not None:
                    target_states.append(result.target_state)

            rollout_flags = load_performance_flags().enabled_flags()
            run_state = RunState(
                run_label=run_label,
                status=derive_run_status(target_states),
                targets=tuple(target_states),
                started_at=run_started_at,
                finished_at=utc_now_iso(),
                performance_summary=summarize_run_performance(
                    tuple(target_states),
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
                    status=run_state.status.value,
                    artifact_path=run_summary_path.as_posix(),
                    payload=run_state.to_dict(),
                )
            )

            print(f"\n{'=' * 60}")
            print("BATTLE REPORT")
            print(f"{'=' * 60}")
            print(f"Total Fixed     : {total_successful}")
            print(f"Total Skipped   : {total_skipped}")
            print(f"Total Failed    : {total_failed}")
            print(f"Total PRs       : {total_prs}")
            print(f"Run Summary     : {run_summary_path.as_posix()}")
            print(f"{'=' * 60}")
        except Exception as exc:
            state_store.record_run_aborted(
                run_label=run_label,
                status="failed",
                error=str(exc),
                startup_failure=not run_started_recorded,
                payload={
                    "mode": "batch",
                    "config_path": config_path.as_posix(),
                    "run_started_recorded": run_started_recorded,
                },
            )
            raise


def _bool_flag(value: object) -> bool:
    """Normalize bool-like target flags from JSON."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
