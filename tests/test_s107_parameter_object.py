from __future__ import annotations

import subprocess
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
from pi_sonar_agent.core.review_gate import ReviewGateResult
from pi_sonar_agent.fixers.deterministic import IssueGroup
from pi_sonar_agent.fixers.s107_parameter_object import generate_s107_parameter_object_patch


def _issue_group(file_path: str, line: int) -> IssueGroup:
    return IssueGroup(
        group_key="ISSUE-S107",
        file_path=file_path,
        rule="csharpsquid:S107",
        issues=(
            {
                "key": "ISSUE-S107",
                "line": line,
                "textRange": {"startLine": line, "endLine": line},
                "message": "Methods should not have too many parameters",
            },
        ),
        start_line=line,
        end_line=line,
        symbol_names=(),
    )


def _write_demo_repo(root: Path) -> Path:
    project = root / "Demo.csproj"
    source = root / "InternalService.cs"
    project.write_text(
        "\n".join(
            [
                '<Project Sdk="Microsoft.NET.Sdk">',
                "  <PropertyGroup>",
                "    <TargetFramework>net8.0</TargetFramework>",
                "    <Nullable>enable</Nullable>",
                "  </PropertyGroup>",
                "</Project>",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    source.write_text(
        "\n".join(
            [
                "using System.Threading.Tasks;",
                "",
                "namespace DemoApp",
                "{",
                "    internal class InternalService",
                "    {",
                "        internal Task ExecuteAsync()",
                "        {",
                '            return RunAsync("a", "b", "c", "d", "e", "f", "g", "h");',
                "        }",
                "",
                "        internal Task RunAsync(string a, string b, string c, string d, string e, string f, string g, string h)",
                "        {",
                "            return Task.FromResult($\"{a}{b}{c}{d}{e}{f}{g}{h}\");",
                "        }",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return source


def test_generate_s107_parameter_object_patch_updates_signature_and_invocations(tmp_path: Path) -> None:
    source = _write_demo_repo(tmp_path)

    result = generate_s107_parameter_object_patch(
        tmp_path,
        _issue_group(source.name, 12),
    )

    assert result.applied is True
    assert result.strategy == "roslyn:s107_parameter_object_applied"
    assert source.name in result.changed_files
    updated = result.changed_files[source.name]
    assert "sealed class RunAsyncParameters" in updated
    assert "internal Task RunAsync(RunAsyncParameters parameters)" in updated
    assert "var a = parameters.A;" in updated
    assert 'RunAsync(new global::DemoApp.InternalService.RunAsyncParameters("a", "b", "c", "d", "e", "f", "g", "h"))' in updated

    for relative_path, content in result.changed_files.items():
        target = tmp_path / relative_path
        target.write_text(content, encoding="utf-8")

    build = subprocess.run(
        ["dotnet", "build", str(tmp_path / "Demo.csproj"), "-v:q"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )

    assert build.returncode == 0, build.stdout + "\n" + build.stderr


def test_fix_issue_uses_roslyn_deterministic_patch_path(monkeypatch, tmp_path: Path) -> None:
    source = _write_demo_repo(tmp_path)
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-s107-det",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=12,
        component=f"BI:{source.name}",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.load_performance_flags",
        lambda: type(
            "Flags",
            (),
            {
                "review_gate": False,
                "propagation_lifecycle": True,
                "fast_compile": True,
                "layered_verification": True,
                "enabled_flags": lambda self: (),
            },
        )(),
    )
    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.ReviewGateAgent.review",
        lambda **kwargs: ReviewGateResult(status="not_applicable", summary="disabled"),
    )

    result = agent.fix_issue(
        issue,
        tmp_path,
        build_command="dotnet build Demo.csproj -v:q",
    )

    assert result.success is True
    assert result.build_passed is True
    assert result.engine_routing_decision is not None
    assert result.engine_routing_decision.resolved_engine == "roslyn"
    assert result.performance_metrics["roslyn_strategy"] == "roslyn:s107_parameter_object_applied"
    assert any(change["file"] == source.name for change in result.changes)
