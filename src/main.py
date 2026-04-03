"""Main entry point for pi-sonar-agent.

All fixes are handled by Claude Code Agent - simple and powerful.
"""

from __future__ import annotations

import argparse
import time

from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.db_client import create_mysql_client_from_env
from pi_sonar_agent.core.events import EventKind, StateEvent
from pi_sonar_agent.core.model_env import load_project_env
from pi_sonar_agent.core.preflight import (
    load_runtime_environment,
)
from pi_sonar_agent.core.run_coordinator import RunCoordinator, TargetRunOptions
from pi_sonar_agent.core.run_logging import (
    RunLogSession,
)
from pi_sonar_agent.core.state import RunState, derive_run_status, utc_now_iso
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.core.target_config import (
    missing_required_target_fields,
    resolve_cli_target_config,
)

# Load environment variables
load_project_env()


DEFAULT_MAX_ISSUES = 0
DEFAULT_BASE_BRANCH = "develop"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="使用 Claude Code Agent 自动修复 SonarQube 问题")
    parser.add_argument("--project-key", help="SonarQube 项目 Key")
    parser.add_argument("--repository", help="Azure DevOps 仓库名")
    parser.add_argument("--author", help="处理该作者的 issue")
    parser.add_argument("--max-issues", type=int, help="最大处理数量")
    parser.add_argument("--base-branch", help="基线分支")
    parser.add_argument("--build-command", help="构建命令")
    parser.add_argument("--test-command", help="测试命令")
    parser.add_argument("--solution-path", help=".sln 或 .csproj 路径")
    parser.add_argument("--workspace-root", default=".agent_workspaces", help="工作区目录")
    parser.add_argument("--keep-workspace", action="store_true", help="保留工作区")
    parser.add_argument("--skip-build", action="store_true", help="跳过构建验证")
    return parser.parse_args()


def load_default_target() -> dict[str, str]:
    """Load first target from data/targets.json for zero-arg runs."""
    from pathlib import Path

    target_file = Path("data/targets.json")
    if not target_file.exists():
        return {}
    try:
        import json

        data = json.loads(target_file.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            item = data[0]
            if isinstance(item, dict):
                return {
                    "project_key": str(item.get("project_key", "")).strip(),
                    "repository": str(item.get("repository", "")).strip(),
                    "author": str(item.get("author", "")).strip(),
                    "reviewer_email": str(item.get("reviewer_email", "")).strip(),
                    "dingtalk_userid": str(item.get("dingtalk_userid", "")).strip(),
                    "base_branch": str(item.get("base_branch", "")).strip(),
                    "build_command": str(item.get("build_command", "")).strip(),
                    "test_command": str(item.get("test_command", "")).strip(),
                    "solution_path": str(item.get("solution_path", "")).strip(),
                    "max_issues": str(item.get("max_issues", "")).strip(),
                }
    except Exception:
        return {}
    return {}


def main():
    """Main entry point."""
    args = parse_args()
    target_defaults = load_default_target()
    run_label = time.strftime("%Y%m%d%H%M%S")
    run_started_at = utc_now_iso()

    with RunLogSession(run_label=run_label) as log_session:
        print(f"[INFO] 运行日志: {log_session.log_path.as_posix()}")

        # Load config
        runtime_env = load_runtime_environment(
            default_workspace_root=args.workspace_root,
        )

        target_config = resolve_cli_target_config(
            args,
            target_defaults,
            default_base_branch=DEFAULT_BASE_BRANCH,
            default_max_issues=DEFAULT_MAX_ISSUES,
        )
        missing_fields = missing_required_target_fields(target_config)
        if missing_fields:
            field = missing_fields[0]
            raise RuntimeError(f"缺少 {field}，请通过 CLI、.env 或 targets.json 提供")
        coordinator = RunCoordinator(runtime_env)
        state_store = RunStateStore(db_client=create_mysql_client_from_env())
        coordinator.state_store = state_store
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
                    "mode": "single",
                    "base_branch": target_config.base_branch,
                },
            )
        )
        result = coordinator.run_target(
            target_config,
            TargetRunOptions(
                run_label=run_label,
                keep_workspace=args.keep_workspace,
                skip_build=args.skip_build,
            ),
        )
        target_states = (result.target_state,) if result.target_state is not None else ()
        run_state = RunState(
            run_label=run_label,
            status=derive_run_status(target_states),
            targets=target_states,
            started_at=run_started_at,
            finished_at=utc_now_iso(),
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
        print(f"[INFO] 运行摘要: {run_summary_path.as_posix()}")


if __name__ == "__main__":
    main()
