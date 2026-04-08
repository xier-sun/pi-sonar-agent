from __future__ import annotations

import runpy
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import batch_runner as legacy_batch_runner
from pi_sonar_agent.core.state import TargetState, TargetStatus


@dataclass(frozen=True)
class _FakeResult:
    successful: int = 0
    skipped: int = 0
    failed: int = 0
    pr_url: str = ""
    target_state: TargetState | None = None


def test_run_for_target_delegates_to_shared_coordinator() -> None:
    calls: list[tuple[object, object]] = []

    class FakeCoordinator:
        def run_target(self, config, options):
            calls.append((config, options))
            return _FakeResult(successful=2, skipped=1, failed=0, pr_url="https://ado/pr/1")

    result = legacy_batch_runner.run_for_target(
        {
            "project_key": "project-a",
            "repository": "repo-a",
            "author": "alice@example.com",
            "base_branch": "release/2026.04",
            "keep_workspace": "true",
            "skip_build_gate": "1",
        },
        FakeCoordinator(),
        run_label="20260403120000-01",
    )

    assert result.successful == 2
    assert len(calls) == 1
    config, options = calls[0]
    assert config.project_key == "project-a"
    assert config.repository == "repo-a"
    assert config.author == "alice@example.com"
    assert config.base_branch == "release/2026.04"
    assert options.run_label == "20260403120000-01"
    assert options.keep_workspace is True
    assert options.skip_build is True
    assert options.show_banner is True


def test_run_for_target_raises_for_missing_required_fields() -> None:
    class FakeCoordinator:
        def run_target(self, config, options):
            raise AssertionError("should not be called")

    try:
        legacy_batch_runner.run_for_target(
            {
                "repository": "repo-a",
                "author": "alice@example.com",
            },
            FakeCoordinator(),
            run_label="20260403120000-01",
        )
    except RuntimeError as exc:
        assert "缺少 project_key" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_batch_main_reuses_shared_runtime_environment(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str]] = []
    run_states = []

    class FakeRunLogSession:
        def __init__(self, *, run_label: str, prefix: str):
            self.log_path = Path(f"logs/{prefix}_{run_label}.log")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeCoordinator:
        def __init__(self, runtime_env):
            assert runtime_env == "runtime-env"

    class FakeArtifactWriter:
        def write_run_state(self, run_state):
            run_states.append(run_state)
            summary_path = Path("logs/run-artifacts/20260403130000/run_summary.json")
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    def fake_run_for_target(target, coordinator, *, run_label: str, show_banner: bool):
        assert isinstance(coordinator, FakeCoordinator)
        assert show_banner is True
        calls.append((target["repository"], run_label))
        target_state = TargetState(
            run_label=run_label,
            project_key=target["project_key"],
            repository=target["repository"],
            author=target["author"],
            base_branch="develop",
            status=TargetStatus.SUCCEEDED if target["repository"] == "repo-a" else TargetStatus.PARTIAL,
        )
        if target["repository"] == "repo-a":
            return _FakeResult(
                successful=1,
                skipped=0,
                failed=0,
                pr_url="https://ado/pr/1",
                target_state=target_state,
            )
        return _FakeResult(successful=0, skipped=1, failed=1, pr_url="", target_state=target_state)

    monkeypatch.setattr(legacy_batch_runner, "RunLogSession", FakeRunLogSession)
    monkeypatch.setattr(legacy_batch_runner, "load_runtime_environment", lambda: "runtime-env")
    monkeypatch.setattr(legacy_batch_runner, "RunCoordinator", FakeCoordinator)
    monkeypatch.setattr(legacy_batch_runner, "ArtifactWriter", FakeArtifactWriter)
    monkeypatch.setattr(
        legacy_batch_runner,
        "load_targets",
        lambda path: [
            {"project_key": "project-a", "repository": "repo-a", "author": "alice@example.com"},
            {"project_key": "project-b", "repository": "repo-b", "author": "bob@example.com"},
        ],
    )
    monkeypatch.setattr(legacy_batch_runner, "run_for_target", fake_run_for_target)
    monkeypatch.setattr(legacy_batch_runner.time, "strftime", lambda _: "20260403130000")
    monkeypatch.setattr(sys, "argv", ["batch_runner.py"])

    legacy_batch_runner.main()

    assert calls == [
        ("repo-a", "20260403130000-01"),
        ("repo-b", "20260403130000-02"),
    ]
    assert len(run_states) == 1
    assert run_states[0].status.value == "partial"
    output = capsys.readouterr().out
    assert "加载 2 个目标" in output
    assert "Total Fixed     : 1" in output
    assert "Total Skipped   : 1" in output
    assert "Total Failed    : 1" in output
    assert "Total PRs       : 1" in output
    assert "Run Summary     :" in output


def test_pi_sonar_agent_batch_runner_bridge_invokes_legacy_main(monkeypatch) -> None:
    calls: list[str] = []
    fake_module = ModuleType("batch_runner")
    fake_module.main = lambda: calls.append("legacy-batch-main")
    fake_module.__all__ = ("main",)
    monkeypatch.setitem(sys.modules, "batch_runner", fake_module)
    sys.modules.pop("pi_sonar_agent.batch_runner", None)

    try:
        runpy.run_module("pi_sonar_agent.batch_runner", run_name="__main__")
    except SystemExit as exc:
        assert exc.code in {None, 0}

    assert calls == ["legacy-batch-main"]
