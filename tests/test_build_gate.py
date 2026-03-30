from __future__ import annotations

from types import SimpleNamespace

from pi_sonar_agent.fixers.build_gate import (
    format_build_failure_report,
    resolve_build_command,
    run_local_build,
)


def test_run_local_build_uses_utf8_replace_for_subprocess(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="build ok", stderr="")

    import pi_sonar_agent.fixers.build_gate as build_gate_module

    monkeypatch.setattr(build_gate_module.subprocess, "run", fake_run)

    result = run_local_build(tmp_path, "dotnet build")

    assert result["succeeded"] is True
    assert len(calls) == 1
    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"
    assert calls[0]["text"] is True
    assert calls[0]["capture_output"] is True


def test_run_local_build_handles_missing_streams_on_failed_build(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=None, stderr="compile failed")

    import pi_sonar_agent.fixers.build_gate as build_gate_module

    monkeypatch.setattr(build_gate_module.subprocess, "run", fake_run)

    result = run_local_build(tmp_path, "dotnet build")

    assert result["succeeded"] is False
    assert result["error"] == "Build failed: compile failed"
    assert result["build_output"] == "compile failed"
    assert result["build_command"] == "dotnet build"
    assert result["test_command"] is None


def test_resolve_build_command_appends_solution_path_for_dotnet_build() -> None:
    command = resolve_build_command("dotnet build", "OpenAuth.Core/OpenAuth.Core.WebApi.sln")

    assert command == 'dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"'


def test_format_build_failure_report_includes_tail_of_logs() -> None:
    report = format_build_failure_report(
        {
            "error": "Build failed: compile failed",
            "build_command": "dotnet build Foo.sln",
            "test_command": "dotnet test Foo.sln",
            "build_output": "line1\nFoo.cs(12,3): error CS0103: name not found\nline3",
            "test_output": "test1\nTestFoo: error assertion failed\ntest3",
        },
        max_lines=2,
    )

    assert "错误: Build failed: compile failed" in report
    assert "构建命令: dotnet build Foo.sln" in report
    assert "测试命令: dotnet test Foo.sln" in report
    assert "关键错误:" in report
    assert "Foo.cs(12,3): error CS0103: name not found" in report
    assert "测试关键错误:" in report
    assert "TestFoo: error assertion failed" in report
    assert "... (省略前 1 行)" in report
    assert "error CS0103: name not found\nline3" in report
    assert "error assertion failed\ntest3" in report
