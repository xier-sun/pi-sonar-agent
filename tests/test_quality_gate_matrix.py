from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue


def _build_guardrail_issue() -> SonarIssue:
    return SonarIssue(
        key="issue-guardrail-matrix",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )


def _seed_guardrail_workspace(workspace: Path) -> None:
    foo_file = workspace / "src" / "Foo.cs"
    bar_file = workspace / "src" / "Bar.cs"
    foo_file.parent.mkdir(parents=True, exist_ok=True)
    foo_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var now = DateTime.Now;",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bar_file.write_text(
        "\n".join(
            [
                "class Bar",
                "{",
                "    void Demo() { }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_guardrail_modes_diverge_for_undeclared_side_file(monkeypatch, tmp_path: Path) -> None:
    issue = _build_guardrail_issue()

    def fake_get_rule_details(self, rule_key: str) -> dict[str, str]:
        return {"description": "原因", "how_to_fix": "修复方法"}

    monkeypatch.setattr(ClaudeFixAgent, "get_rule_details", fake_get_rule_details)
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs", "src/Bar.cs"]),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_agent_run(func) -> None:
        (active_workspace / "src" / "Foo.cs").write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "        var now = DateTime.SpecifyKind(DateTime.Now, DateTimeKind.Local);",
                    "    }",
                    "}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (active_workspace / "src" / "Bar.cs").write_text(
            "\n".join(
                [
                    "class Bar",
                    "{",
                    "    void Demo() { }",
                    "    // unrelated side edit",
                    "}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(claude_agent_module.anyio, "run", fake_agent_run)
    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    active_workspace = tmp_path / "scope"
    _seed_guardrail_workspace(active_workspace)
    scope_agent = ClaudeFixAgent(
        sonar_host="https://sonar.example",
        sonar_token="token",
        agent_env={"ISSUE_GUARDRAIL_MODE": "scope"},
    )
    scope_result = scope_agent.fix_issue(issue, active_workspace, 'dotnet build "src/Foo.sln"')

    active_workspace = tmp_path / "contract-review"
    _seed_guardrail_workspace(active_workspace)
    contract_agent = ClaudeFixAgent(
        sonar_host="https://sonar.example",
        sonar_token="token",
        agent_env={"ISSUE_GUARDRAIL_MODE": "contract_review"},
    )
    contract_result = contract_agent.fix_issue(issue, active_workspace, 'dotnet build "src/Foo.sln"')

    assert scope_result.success is True
    assert scope_result.guardrail_mode == "scope"
    assert scope_result.reviewer_result["status"] == "retry"
    assert scope_result.follow_ups

    assert contract_result.success is False
    assert contract_result.retryable_failure is True
    assert contract_result.failure_kind == "reviewer"
    assert contract_result.guardrail_mode == "contract_review"
    assert contract_result.reviewer_result["status"] == "retry"
    assert contract_result.follow_ups
