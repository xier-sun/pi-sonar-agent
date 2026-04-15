from __future__ import annotations

from pi_sonar_agent.core import perf_flags as perf_flags_module


def test_load_performance_flags_reads_git_clone_depth_from_project_env(monkeypatch) -> None:
    monkeypatch.setattr(
        perf_flags_module,
        "read_project_env",
        lambda: {"PI_SONAR_GIT_CLONE_DEPTH": "25"},
    )

    flags = perf_flags_module.load_performance_flags()

    assert flags.git_clone_depth == 25


def test_load_performance_flags_allows_disabling_shallow_clone(monkeypatch) -> None:
    monkeypatch.setattr(
        perf_flags_module,
        "read_project_env",
        lambda: {"PI_SONAR_GIT_CLONE_DEPTH": "0"},
    )

    flags = perf_flags_module.load_performance_flags()

    assert flags.git_clone_depth == 0
