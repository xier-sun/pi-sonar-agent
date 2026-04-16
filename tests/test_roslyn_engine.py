from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
from pi_sonar_agent.fixers.deterministic import IssueGroup
from pi_sonar_agent.fixers.roslyn import RoslynFixEngine, RoslynFixResult


def _build_issue_group(file_path: str) -> IssueGroup:
    return IssueGroup(
        group_key="ISSUE-S107",
        file_path=file_path,
        rule="csharpsquid:S107",
        issues=(
            {
                "key": "ISSUE-S107",
                "line": 3,
                "textRange": {"startLine": 3, "endLine": 3},
                "message": "Methods should not have too many parameters",
            },
        ),
        start_line=3,
        end_line=3,
        symbol_names=(),
    )


def test_roslyn_fix_engine_marks_public_interface_s107_as_unsafe(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    issue_file = workspace / "src" / "FooApp.cs"
    interface_file = workspace / "src" / "Interfaces" / "IFooApp.cs"
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    interface_file.parent.mkdir(parents=True, exist_ok=True)

    issue_file.write_text(
        "\n".join(
            [
                "public class FooApp : IFooApp",
                "{",
                "    public Task Run(string a, string b, string c, string d, string e, string f, string g, string h)",
                "    {",
                "        return Task.CompletedTask;",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    interface_file.write_text(
        "\n".join(
            [
                "public interface IFooApp",
                "{",
                "    Task Run(string a, string b, string c, string d, string e, string f, string g, string h);",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    engine = RoslynFixEngine()
    result = engine.apply_solution_fix(
        workspace_path=str(workspace),
        solution_path="",
        issue_group=_build_issue_group("src/FooApp.cs"),
        primary_issue={"message": "too many parameters"},
    )

    assert result.applied is False
    assert result.can_fix_safely is False
    assert result.strategy == "roslyn:s107_cannot_fix_safely"
    assert "public_or_protected_surface" in result.safety_flags
    assert "interface_propagation_target" in result.safety_flags


def test_roslyn_fix_engine_marks_private_s107_as_safe_candidate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    issue_file = workspace / "src" / "FooApp.cs"
    issue_file.parent.mkdir(parents=True, exist_ok=True)

    issue_file.write_text(
        "\n".join(
            [
                "class FooApp",
                "{",
                "    private void Run(string a, string b, string c, string d, string e, string f, string g, string h)",
                "    {",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    engine = RoslynFixEngine()
    result = engine.apply_solution_fix(
        workspace_path=str(workspace),
        solution_path="",
        issue_group=_build_issue_group("src/FooApp.cs"),
        primary_issue={"message": "too many parameters"},
    )

    assert result.applied is False
    assert result.can_fix_safely is True
    assert result.strategy == "roslyn:s107_candidate_identified"
    assert result.safety_flags == ()


def test_fix_issue_uses_roslyn_path_when_routed(monkeypatch, tmp_path: Path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-roslyn-s107",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=3,
        component="BI:src/FooApp.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    source_file = tmp_path / "src" / "FooApp.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class FooApp",
                "{",
                "    private void Run(string a, string b, string c, string d, string e, string f, string g, string h)",
                "    {",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        "pi_sonar_agent.agent.claude_agent.RoslynFixEngine.apply_solution_fix",
        lambda self, **kwargs: RoslynFixResult(
            applied=False,
            updated_content="",
            strategy="roslyn:s107_cannot_fix_safely",
            summary="Roslyn rejected the S107 candidate.",
            can_fix_safely=False,
            safety_flags=("public_or_protected_surface",),
        ),
    )

    result = agent.fix_issue(issue, tmp_path)

    assert result.skipped is True
    assert result.failure_kind == "roslyn_cannot_fix_safely"
    assert "Roslyn rejected the S107 candidate" in result.skip_reason
