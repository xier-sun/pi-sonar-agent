from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import main as legacy_main
from pi_sonar_agent.core.state import TargetState, TargetStatus


def test_main_writes_run_summary(monkeypatch, capsys) -> None:
    run_states = []

    class FakeRunLogSession:
        def __init__(self, *, run_label: str):
            self.log_path = Path(f"logs/{run_label}.log")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeCoordinator:
        def __init__(self, runtime_env):
            assert runtime_env == "runtime-env"

        def run_target(self, target_config, options):
            assert target_config.project_key == "project-a"
            assert options.run_label == "20260403160000"
            return SimpleNamespace(
                target_state=TargetState(
                    run_label=options.run_label,
                    project_key=target_config.project_key,
                    repository=target_config.repository,
                    author=target_config.author,
                    base_branch=target_config.base_branch,
                    status=TargetStatus.SUCCEEDED,
                )
            )

    class FakeArtifactWriter:
        def write_run_state(self, run_state):
            run_states.append(run_state)
            summary_path = Path("logs/run-artifacts/20260403160000/run_summary.json")
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(legacy_main, "parse_args", lambda: SimpleNamespace(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        max_issues=None,
        base_branch=None,
        build_command=None,
        test_command=None,
        solution_path=None,
        workspace_root=".agent_workspaces",
        keep_workspace=False,
        skip_build=False,
    ))
    monkeypatch.setattr(legacy_main, "load_default_target", lambda: {})
    monkeypatch.setattr(legacy_main.time, "strftime", lambda _: "20260403160000")
    monkeypatch.setattr(legacy_main, "RunLogSession", FakeRunLogSession)
    monkeypatch.setattr(legacy_main, "load_runtime_environment", lambda default_workspace_root: "runtime-env")
    monkeypatch.setattr(legacy_main, "resolve_cli_target_config", lambda *args, **kwargs: SimpleNamespace(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        base_branch="main",
    ))
    monkeypatch.setattr(legacy_main, "missing_required_target_fields", lambda config: [])
    monkeypatch.setattr(legacy_main, "RunCoordinator", FakeCoordinator)
    monkeypatch.setattr(legacy_main, "ArtifactWriter", FakeArtifactWriter)

    legacy_main.main()

    assert len(run_states) == 1
    assert run_states[0].status.value == "succeeded"
    output = capsys.readouterr().out
    assert "运行摘要:" in output


def test_pi_sonar_agent_main_bridge_invokes_legacy_main(monkeypatch) -> None:
    calls: list[str] = []
    fake_module = ModuleType("main")
    fake_module.main = lambda: calls.append("legacy-main")
    fake_module.__all__ = ("main",)
    monkeypatch.setitem(sys.modules, "main", fake_module)
    sys.modules.pop("pi_sonar_agent.main", None)

    try:
        runpy.run_module("pi_sonar_agent.main", run_name="__main__")
    except SystemExit as exc:
        assert exc.code in {None, 0}

    assert calls == ["legacy-main"]
