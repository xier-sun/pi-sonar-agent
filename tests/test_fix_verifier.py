from __future__ import annotations

import subprocess

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.fix_verifier import FixVerifier
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.quality_gate import QualityGateRule
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
            before_changed_lines=(6,),
            after_changed_lines=(6,),
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


def test_fix_verifier_handles_build_timeout(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-timeout",
        rule="csharpsquid:S1144",
        message="Remove unused private method",
        line=12,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        validation_plan=("build",),
        scope_mode="statement",
        validation_line_range=(12, 13),
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(12,),
            before_changed_lines=(12,),
            after_changed_lines=(),
            diff_text="@@ -12,3 +12,0 @@\n-private void DeadCode() {}\n",
            hunk_count=1,
        ),
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args", args[0] if args else "dotnet build"),
            timeout=FixVerifier.BUILD_TIMEOUT_SECONDS,
        )

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)

    outcome = FixVerifier.evaluate_attempt(
        issue=issue,
        workspace_path=tmp_path,
        build_command='dotnet build "src/Foo.sln"',
        edit_contract=contract,
        guardrail_mode="contract_review",
        scope=None,
        reviewed_changes=reviewed_changes,
        current_issue_file_content="class Foo {}\n",
    )

    assert outcome.build_passed is False
    assert "timed out after" in outcome.build_output


def test_fix_verifier_rejects_quality_gate_violation(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-quality-gate",
        rule="csharpsquid:S138",
        message="方法过长",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        quality_gate_rules=(
            QualityGateRule(
                rule_id="async_signature",
                title="异步签名规范",
                summary="异步方法要用 Async 命名并返回 Task。",
                enforcement="hard",
            ),
        ),
        scope_mode="method",
        validation_line_range=(1, 6),
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(3,),
            before_changed_lines=(3,),
            after_changed_lines=(3,),
            diff_text='@@ -3,1 +3,1 @@\n-public async Task ProcessAsync()\n+public async Task Process()\n',
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
        guardrail_mode="contract_review",
        scope=None,
        reviewed_changes=reviewed_changes,
        current_issue_file_content="\n".join(
            [
                "class Foo",
                "{",
                "    public async Task Process()",
                "    {",
                "        await Task.Delay(1);",
                "    }",
                "}",
            ]
        )
        + "\n",
    )

    assert outcome.build_passed is True
    assert outcome.quality_gate_result.status == "retry"
    assert outcome.quality_gate_result.violations[0].rule_id == "async_signature"
    assert "Quality gate verification failed" in outcome.combined_output


def test_fix_verifier_quality_gate_stays_inside_touched_region(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-quality-scope",
        rule="csharpsquid:S1481",
        message="移除未使用变量",
        line=8,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        quality_gate_rules=(
            QualityGateRule(
                rule_id="public_xml_docs",
                title="公开成员 XML 文档完整",
                summary="公开成员要有 XML。",
                enforcement="hard",
            ),
            QualityGateRule(
                rule_id="linq_method_syntax",
                title="LINQ 优先方法语法",
                summary="不要引入 query syntax。",
                enforcement="hard",
            ),
        ),
        scope_mode="statement",
        validation_line_range=(8, 8),
        allowed_line_ranges=((8, 8),),
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(8,),
            before_changed_lines=(8,),
            after_changed_lines=(),
            diff_text="@@ -8,1 +8,0 @@\n-        var unused = 1;\n",
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
        guardrail_mode="contract_review",
        scope=None,
        reviewed_changes=reviewed_changes,
        current_issue_file_content="\n".join(
            [
                "public class Foo",
                "{",
                "    public IQueryable<int> Query()",
                "    {",
                "        return from item in Enumerable.Empty<int>() select item;",
                "    }",
                "",
                "    private void Demo()",
                "    {",
                "        Run();",
                "    }",
                "}",
            ]
        )
        + "\n",
    )

    assert outcome.build_passed is True
    assert outcome.quality_gate_result.status == "pass"


def test_fix_verifier_scope_accepts_delete_using_before_coordinates(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-delete-scope",
        rule="csharpsquid:S1481",
        message="移除未使用变量",
        line=2224,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "scope_review"),
        scope_mode="statement",
        validation_line_range=(2224, 2224),
        allowed_line_ranges=((2224, 2224),),
    )
    scope = IssueEditScope(
        start_line=2224,
        end_line=2224,
        validation_start_line=2224,
        validation_end_line=2224,
        mode="statement",
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(2224,),
            before_changed_lines=(2224,),
            after_changed_lines=(),
            diff_text="@@ -2224 +2223,0 @@\n-        var orderNum = result.OrderNum();\n",
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

    original_lines = ["class Foo"] + ["" for _ in range(2222)] + ["        var orderNum = result.OrderNum();", "}"]
    current_lines = ["class Foo"] + ["" for _ in range(2222)] + ["}"]

    outcome = FixVerifier.evaluate_attempt(
        issue=issue,
        workspace_path=tmp_path,
        build_command='dotnet build "src/Foo.sln"',
        edit_contract=contract,
        guardrail_mode="scope",
        scope=scope,
        reviewed_changes=reviewed_changes,
        original_issue_file_content="\n".join(original_lines) + "\n",
        current_issue_file_content="\n".join(current_lines) + "\n",
    )

    assert outcome.build_passed is True
    assert outcome.scope_violation is None
