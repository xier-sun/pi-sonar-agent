from __future__ import annotations

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.boundary_runtime import BoundaryHookPipeline, BoundaryRuntime
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.scope_guard import IssueEditScope


def test_boundary_runtime_runs_hooks_and_returns_scope_and_reviewer(tmp_path) -> None:
    contract = EditContract(
        issue_key="ISSUE-1",
        rule_id="csharpsquid:S6562",
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "scope_review", "diff_review"),
        scope_mode="statement",
        validation_line_range=(5, 5),
        allowed_line_ranges=((5, 5),),
    )
    scope = IssueEditScope(
        start_line=5,
        end_line=5,
        validation_start_line=5,
        validation_end_line=5,
        mode="statement",
    )
    issue = SonarIssue(
        key="ISSUE-1",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    file_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(6,),
            before_changed_lines=(6,),
            after_changed_lines=(6,),
            diff_text="@@ -6,1 +6,1 @@\n-var name = \"demo\";\n+var name = \"changed\";\n",
            hunk_count=1,
        ),
    )

    class HookSpy:
        def __init__(self) -> None:
            self.before_calls = 0
            self.after_calls = 0
            self.after_scope: str | None = None

        def before_boundary_review(self, context) -> None:
            self.before_calls += 1

        def after_boundary_review(self, context) -> None:
            self.after_calls += 1
            self.after_scope = context.scope_violation

    hook_spy = HookSpy()
    outcome = BoundaryRuntime.review(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="scope",
        edit_contract=contract,
        reviewed_changes=file_changes,
        workspace_path=tmp_path,
        issue=issue,
        scope=scope,
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
        hooks=BoundaryHookPipeline((hook_spy,)),
    )

    assert hook_spy.before_calls == 1
    assert hook_spy.after_calls == 1
    assert outcome.reviewer_result.status == "retry"
    assert outcome.scope_violation is not None
    assert hook_spy.after_scope == outcome.scope_violation
