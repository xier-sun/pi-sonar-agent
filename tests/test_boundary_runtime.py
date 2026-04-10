from __future__ import annotations

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.boundary_capabilities import (
    BOUNDARY_PROFILE_DECLARATION_ANCHOR,
    BOUNDARY_PROFILE_MEMBER_CLUSTER,
    MEMBER_DELETE_CAPABILITY,
    METHOD_CLUSTER_DELETE_CAPABILITY,
)
from pi_sonar_agent.core.boundary_runtime import BoundaryHookPipeline, BoundaryRuntime
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.scope_guard import IssueEditScope


def test_boundary_runtime_records_soft_drift_without_hard_failure(tmp_path) -> None:
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
    assert outcome.reviewer_result.status == "pass"
    assert outcome.scope_violation is None
    assert hook_spy.after_scope is None
    assert outcome.primary_failure_code == ""
    assert any(item.type == "outside_primary_region" for item in outcome.reviewer_result.violations)


def test_boundary_runtime_allows_declaration_anchor_drift_as_soft_audit(tmp_path) -> None:
    contract = EditContract(
        issue_key="ISSUE-2",
        rule_id="csharpsquid:S1481",
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "scope_review", "diff_review"),
        scope_mode="statement",
        boundary_profile=BOUNDARY_PROFILE_DECLARATION_ANCHOR,
        validation_line_range=(10, 11),
        allowed_line_ranges=((10, 11),),
    )
    scope = IssueEditScope(
        start_line=10,
        end_line=10,
        validation_start_line=10,
        validation_end_line=11,
        mode="statement",
    )
    issue = SonarIssue(
        key="ISSUE-2",
        rule="csharpsquid:S1481",
        message="Remove unused local variable",
        line=10,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    outcome = BoundaryRuntime.review(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="scope",
        edit_contract=contract,
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(9,),
                before_changed_lines=(9,),
                after_changed_lines=(),
                diff_text="@@ -9 +8,0 @@\n-        var slpDict = BuildMap();\n",
                hunk_count=1,
            ),
        ),
        workspace_path=tmp_path,
        issue=issue,
        scope=scope,
        original_issue_file_content="class Foo {}\n",
        current_issue_file_content="class Foo {}\n",
        scope_validator=lambda *args, **kwargs: "Issue changes exceeded the allowed Sonar edit scope.",
    )

    assert outcome.reviewer_result.status == "pass"
    assert outcome.primary_failure_code == ""
    assert outcome.scope_violation is None


def test_boundary_runtime_relaxes_same_file_member_cluster_deletes(tmp_path) -> None:
    contract = EditContract(
        issue_key="ISSUE-3",
        rule_id="csharpsquid:S1144",
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "scope_review", "diff_review"),
        scope_mode="method",
        boundary_profile=BOUNDARY_PROFILE_MEMBER_CLUSTER,
        validation_line_range=(5, 7),
        allowed_line_ranges=((5, 7), (9, 11)),
        allowed_capabilities=(MEMBER_DELETE_CAPABILITY, METHOD_CLUSTER_DELETE_CAPABILITY),
    )
    scope = IssueEditScope(
        start_line=5,
        end_line=7,
        validation_start_line=5,
        validation_end_line=7,
        mode="method",
    )
    issue = SonarIssue(
        key="ISSUE-3",
        rule="csharpsquid:S1144",
        message="Remove unused private method",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    original_content = "\n".join(
        [
            "class Foo",
            "    /// <summary>",
            "    /// Demo",
            "    /// </summary>",
            "    private async Task AddReceiptsAsync()",
            "    {",
            "    }",
            "",
            "    private async Task AddReceiptsCaseAAsync()",
            "    {",
            "    }",
            "",
            "    private async Task AddReceiptsCaseBAsync()",
            "    {",
            "    }",
            "",
            "    public void KeepMe()",
            "    {",
            "    }",
            "}",
        ]
    ) + "\n"

    outcome = BoundaryRuntime.review(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="scope",
        edit_contract=contract,
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=tuple(range(2, 16)),
                before_changed_lines=tuple(range(2, 16)),
                after_changed_lines=(),
                diff_text="@@ -2,14 +1,0 @@\n-removed member cluster\n",
                hunk_count=1,
            ),
        ),
        workspace_path=tmp_path,
        issue=issue,
        scope=scope,
        original_issue_file_content=original_content,
        current_issue_file_content="class Foo\n{\n    public void KeepMe()\n    {\n    }\n}\n",
    )

    assert outcome.reviewer_result.status == "pass"
    assert outcome.scope_violation is None
    assert outcome.primary_failure_code == ""
