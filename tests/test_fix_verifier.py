from __future__ import annotations

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.fix_verifier import FixVerifier
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.scope_guard import IssueEditScope


def test_fix_verifier_evaluates_scope_violation(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-scope",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build",),
        scope_mode="statement",
        validation_line_range=(5, 6),
    )
    scope = IssueEditScope(
        start_line=5,
        end_line=5,
        validation_start_line=5,
        validation_end_line=5,
        mode="statement",
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(6,),
            diff_text="@@ -6,1 +6,1 @@\n-var name = \"demo\";\n+var name = \"changed\";",
            hunk_count=1,
        ),
    )

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.subprocess.run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    outcome = FixVerifier.evaluate_attempt(
        issue=issue,
        workspace_path=tmp_path,
        build_command='dotnet build "src/Foo.sln"',
        edit_contract=contract,
        guardrail_mode="scope",
        scope=scope,
        reviewed_changes=reviewed_changes,
        original_issue_file_content="\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var now = DateTime.Now;",
                "        var name = \"demo\";",
                "    }",
                "}",
            ]
        )
        + "\n",
        current_issue_file_content="\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var now = DateTime.Now;",
                "        var name = \"changed\";",
                "    }",
                "}",
            ]
        )
        + "\n",
    )

    assert outcome.build_passed is True
    assert outcome.scope_violation is not None
    assert "Changed lines outside scope" in outcome.scope_violation
    assert "build ok" in outcome.combined_output
