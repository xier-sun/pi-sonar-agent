from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import core.job_runner as legacy_job_runner_module
from pi_sonar_agent.core.job_runner import JobRunner, build_target_config_from_job
from pi_sonar_agent.core.state import TargetState, TargetStatus


def test_build_target_config_from_job_uses_job_payload_defaults() -> None:
    job = SimpleNamespace(
        job_id="JOB-1",
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        issue_keys=("i1", "i2"),
        skip_issue_keys=("i3",),
        max_issues=3,
        reviewer_email="reviewer@example.com",
        dingtalk_userid="ding-user",
        target_payload={
            "build_command": "dotnet build Foo.sln",
            "test_command": "dotnet test Foo.sln",
            "solution_path": "Foo.sln",
        },
    )

    config = build_target_config_from_job(job)

    assert config.repository == "BI"
    assert config.project_key == "sonar-bi"
    assert config.author == "alice@example.com"
    assert config.base_branch == "develop"
    assert config.issue_keys == ("i1", "i2")
    assert config.skip_issue_keys == ("i3",)
    assert config.build_command == "dotnet build Foo.sln"
    assert config.test_command == "dotnet test Foo.sln"
    assert config.solution_path == "Foo.sln"
    assert config.base_branch_source == "run_jobs.base_branch"


def test_job_runner_executes_existing_run_target_flow(monkeypatch, tmp_path) -> None:
    started: list[tuple[str, str]] = []
    recorded_events: list[str] = []
    written_run_states: list[object] = []
    run_target_calls: list[tuple[object, object]] = []

    class FakeStateStore:
        def __init__(self, *, db_client=None):
            self.db_client = db_client

        def record_event(self, event):
            recorded_events.append(event.kind.value)

        def record_run_state(self, run_state, *, artifact_path: str = ""):
            written_run_states.append((run_state, artifact_path))

        def record_run_aborted(self, **kwargs):
            recorded_events.append("run_aborted")

    class FakeCoordinator:
        def __init__(self, runtime_env):
            self.runtime_env = runtime_env
            self.state_store = None

        def run_target(self, target_config, options):
            run_target_calls.append((target_config, options))
            return SimpleNamespace(
                ok=True,
                status="succeeded",
                pr_url="https://example/pr/2",
                pr_error="",
                target_summary_path=str(tmp_path / "run-artifacts" / "target_summary.json"),
                target_state=TargetState(
                    run_label=options.run_label,
                    project_key=target_config.project_key,
                    repository=target_config.repository,
                    author=target_config.author,
                    base_branch=target_config.base_branch,
                    status=TargetStatus.SUCCEEDED,
                ),
            )

    class FakeArtifactWriter:
        def write_run_state(self, run_state):
            path = tmp_path / "run-artifacts" / "run_summary.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

    class FakeRunLogSession:
        def __init__(self, *, run_label=None, log_root="logs/runs", prefix="job"):
            self.run_label = run_label or "20260518190000"
            self.log_path = tmp_path / "logs" / f"{prefix}_{self.run_label}.log"

        def __enter__(self):
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("", encoding="utf-8")
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(legacy_job_runner_module, "ArtifactWriter", FakeArtifactWriter)
    monkeypatch.setattr(legacy_job_runner_module, "RunLogSession", FakeRunLogSession)

    runner = JobRunner(
        db_client=None,
        runtime_loader=lambda **kwargs: SimpleNamespace(workspace_root=tmp_path / "workspaces"),
        coordinator_factory=FakeCoordinator,
        state_store_factory=FakeStateStore,
    )
    job = SimpleNamespace(
        job_id="JOB-1",
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        issue_keys=("issue-1",),
        skip_issue_keys=(),
        max_issues=1,
        reviewer_email="",
        dingtalk_userid="",
        target_payload={"skip_build_gate": True},
    )

    result = runner.run_job(
        job,
        on_started=lambda run_label, run_log_path: started.append((run_label, run_log_path)),
    )

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.pr_url == "https://example/pr/2"
    assert started
    assert started[0][1].endswith(".log")
    assert run_target_calls
    assert run_target_calls[0][0].issue_keys == ("issue-1",)
    assert run_target_calls[0][1].skip_build is True
    assert recorded_events[0] == "run_started"
    assert recorded_events[-1] == "run_finished"
    assert written_run_states
