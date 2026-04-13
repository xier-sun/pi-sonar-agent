from __future__ import annotations

import subprocess

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange, ReviewedLineOperation
from pi_sonar_agent.core.fix_verifier import FixVerifier
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.propagation_verifier import PropagationCheckResult, PropagationVerifier
from pi_sonar_agent.core.quality_gate import QualityGateResult, QualityGateRule, QualityGateViolation
from pi_sonar_agent.core.quality_gate_verifier import QualityGateVerifier
from pi_sonar_agent.core.repair_plan import RepairPlan, RepairPropagationTarget
from pi_sonar_agent.core.review_gate import ReviewGateDecision, ReviewGateFinding, ReviewGateResult
from pi_sonar_agent.core.scope_guard import IssueEditScope


def test_fix_verifier_records_soft_drift_without_blocking_build(monkeypatch, tmp_path) -> None:
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
        validation_line_range=(5, 5),
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

    build_calls: list[str] = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        return FakeCompletedProcess()

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.ReviewGateAgent.review",
        lambda **kwargs: ReviewGateResult(
            status="not_applicable",
            summary="Review gate disabled for this deterministic propagation regression test.",
        ),
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
    assert outcome.scope_violation is None
    assert outcome.boundary_failure_code == ""
    assert outcome.reviewer_result.status == "pass"
    assert any(
        item.type == "outside_primary_region"
        for item in outcome.reviewer_result.violations
    )
    assert "build ok" in outcome.combined_output
    assert build_calls == ["build", "build"]


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

    build_calls: list[str] = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        return FakeCompletedProcess()

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)

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

    assert outcome.build_passed is False
    assert outcome.quality_gate_result.status == "retry"
    assert outcome.quality_gate_result.violations[0].rule_id == "async_signature"
    assert "异步方法 Process 没有以 Async 结尾" in outcome.combined_output
    assert build_calls == []


def test_fix_verifier_short_circuits_build_on_incomplete_signature_propagation(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-propagation",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=8,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    issue_file = tmp_path / "src" / "Foo.cs"
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    issue_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    public async Task Sync()",
                "    {",
                "        await AutoPlugin(ids);",
                "    }",
                "",
                "    public async Task AutoPluginAsync(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        repair_plan=RepairPlan(
            repair_shape="signature_adjustment",
            primary_file="src/Foo.cs",
            primary_method_name="AutoPlugin",
            proposed_method_name="AutoPluginAsync",
            requires_signature_change=True,
            requires_propagation=True,
            verification_targets=(
                RepairPropagationTarget(
                    file="src/Foo.cs",
                    symbol="definition@8-10",
                    kind="definition",
                    reason="definition sync",
                    start_line=8,
                    end_line=10,
                ),
                RepairPropagationTarget(
                    file="src/Foo.cs",
                    symbol="callsite@5-5",
                    kind="callsite",
                    reason="callsite sync",
                    start_line=5,
                    end_line=5,
                ),
            ),
        ),
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(8,),
            before_changed_lines=(8,),
            after_changed_lines=(8,),
            diff_text=(
                "@@ -8,1 +8,1 @@\n"
                "-    public async Task AutoPlugin(IEnumerable<int> orderIds)\n"
                "+    public async Task AutoPluginAsync(IEnumerable<int> orderIds)\n"
            ),
            hunk_count=1,
        ),
    )

    build_calls: list[str] = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        return FakeCompletedProcess()

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)

    outcome = FixVerifier.evaluate_attempt(
        issue=issue,
        workspace_path=tmp_path,
        build_command='dotnet build "src/Foo.sln"',
        edit_contract=contract,
        guardrail_mode="scope",
        scope=None,
        reviewed_changes=reviewed_changes,
        current_issue_file_content=issue_file.read_text(encoding="utf-8"),
    )

    assert outcome.build_passed is False
    assert outcome.build_invoked is False
    assert outcome.propagation_check_result.status == "retry"
    assert "Residual Targets:" in outcome.combined_output
    assert "callsite" in outcome.combined_output
    assert build_calls == []


def test_fix_verifier_short_circuits_full_build_when_fast_compile_fails(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-fast-compile",
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
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build",),
        scope_mode="statement",
        validation_line_range=(8, 8),
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

    commands: list[str] = []

    class FastCompileFailedProcess:
        returncode = 1
        stdout = "CSC : error CS0103: The name 'Foo' does not exist"
        stderr = ""

    class BuildPassedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        if "--no-restore" in command:
            return FastCompileFailedProcess()
        return BuildPassedProcess()

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)

    outcome = FixVerifier.evaluate_attempt(
        issue=issue,
        workspace_path=tmp_path,
        build_command='dotnet build "src/Foo.sln"',
        edit_contract=contract,
        guardrail_mode="scope",
        scope=None,
        reviewed_changes=reviewed_changes,
        current_issue_file_content="\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        Run();",
                "    }",
                "}",
            ]
        )
        + "\n",
    )

    assert outcome.fast_compile_invoked is True
    assert outcome.fast_compile_passed is False
    assert outcome.build_invoked is False
    assert outcome.build_passed is False
    assert outcome.fast_compile_command.endswith('--no-restore -v:q')
    assert "Fast compile failed" in outcome.combined_output
    assert commands == ['dotnet build "src/Foo.sln" --no-restore -v:q']


def test_fix_verifier_retries_full_build_without_restore_after_nuget_source_failure(monkeypatch, tmp_path) -> None:
    commands: list[str] = []

    class RestoreFailedProcess:
        returncode = 1
        stdout = (
            'error NU1301: Unable to load the service index for source https://api.nuget.org/v3/index.json.'
        )
        stderr = ""

    class OfflineRetryPassedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        if "--no-restore" in command:
            return OfflineRetryPassedProcess()
        return RestoreFailedProcess()

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)

    build_passed, build_output = FixVerifier.run_local_build(
        tmp_path,
        'dotnet build "src/Foo.sln"',
    )

    assert build_passed is True
    assert commands == [
        'dotnet build "src/Foo.sln"',
        'dotnet build "src/Foo.sln" --no-restore',
    ]
    assert "NuGet restore/source failure" in build_output
    assert "--no-restore" in build_output
    assert "build ok" in build_output


def test_fix_verifier_does_not_short_circuit_full_build_on_missing_assets_fast_compile(
    monkeypatch,
    tmp_path,
) -> None:
    issue = SonarIssue(
        key="issue-missing-assets-fast-compile",
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
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build",),
        scope_mode="statement",
        validation_line_range=(8, 8),
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

    commands: list[str] = []

    class MissingAssetsProcess:
        returncode = 1
        stdout = (
            "error NETSDK1004: 找不到资产文件"
            "“D:\\repo\\OpenAuth.Core\\OpenAuth.App\\obj\\project.assets.json”。运行 NuGet 包还原以生成此文件。"
        )
        stderr = ""

    class BuildPassedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        if "--no-restore" in command:
            return MissingAssetsProcess()
        return BuildPassedProcess()

    monkeypatch.setattr("pi_sonar_agent.core.fix_verifier.subprocess.run", fake_run)

    outcome = FixVerifier.evaluate_attempt(
        issue=issue,
        workspace_path=tmp_path,
        build_command='dotnet build "src/Foo.sln"',
        edit_contract=contract,
        guardrail_mode="scope",
        scope=None,
        reviewed_changes=reviewed_changes,
        current_issue_file_content="\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        Run();",
                "    }",
                "}",
            ]
        )
        + "\n",
    )

    assert outcome.fast_compile_invoked is True
    assert outcome.fast_compile_passed is False
    assert outcome.build_invoked is True
    assert outcome.build_passed is True
    assert outcome.build_output == "build ok"
    assert commands == [
        'dotnet build "src/Foo.sln" --no-restore -v:q',
        'dotnet build "src/Foo.sln"',
    ]


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


def test_fix_verifier_scope_uses_contract_allowed_ranges_for_declaration_anchor(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-declaration-anchor",
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
        validation_line_range=(2224, 2225),
        allowed_line_ranges=((2224, 2225), (2223, 2223)),
    )
    scope = IssueEditScope(
        start_line=2224,
        end_line=2225,
        validation_start_line=2224,
        validation_end_line=2225,
        mode="statement",
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(2223,),
            before_changed_lines=(2223,),
            after_changed_lines=(),
            diff_text="@@ -2223 +2222,0 @@\n-        var slpDict = BuildMap();\n",
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

    original_lines = ["class Foo"] + ["" for _ in range(2221)] + ["        var slpDict = BuildMap();", "        return result;", "}"]
    current_lines = ["class Foo"] + ["" for _ in range(2221)] + ["        return result;", "}"]

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


def test_fix_verifier_scope_uses_contract_allowed_ranges_for_adjacent_cleanup(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-adjacent-cleanup",
        rule="csharpsquid:S125",
        message="删除注释代码",
        line=2228,
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
        validation_line_range=(2224, 2231),
        allowed_line_ranges=((2224, 2231), (2223, 2223)),
    )
    scope = IssueEditScope(
        start_line=2228,
        end_line=2228,
        validation_start_line=2224,
        validation_end_line=2231,
        mode="statement",
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(2223, 2227),
            before_changed_lines=(2223, 2227),
            after_changed_lines=(),
            diff_text="@@ -2223,5 +2222,3 @@\n-        var slpDict = BuildMap();\n-\n-        //await AddReceiptsAsync(result, req, slpDict);\n+        return result;\n",
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

    original_lines = ["class Foo"] + ["" for _ in range(2221)] + [
        "        var slpDict = BuildMap();",
        "",
        "",
        "",
        "        //await AddReceiptsAsync(result, req, slpDict);",
        "        return result;",
        "}",
    ]
    current_lines = ["class Foo"] + ["" for _ in range(2225)] + ["        return result;", "}"]

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


def test_reviewed_file_change_keeps_after_lines_empty_for_pure_delete() -> None:
    change = ReviewedFileChange(
        file="src/Foo.cs",
        changed_lines=(2492,),
        before_changed_lines=(2492,),
        after_changed_lines=(),
        line_operations=(
            ReviewedLineOperation(
                kind="delete",
                before_line=2492,
                after_line=2404,
                text="        private void DeadCode() {}",
            ),
        ),
        diff_text="@@ -2492 +2404,0 @@\n-        private void DeadCode() {}\n",
        hunk_count=1,
    )

    assert change.before_changed_lines == (2492,)
    assert change.after_changed_lines == ()
    assert change.quality_gate_changed_lines == ()


def test_quality_gate_verifier_ignores_delete_only_lines_outside_current_file() -> None:
    contract = EditContract(
        issue_key="issue-delete-quality-gate",
        rule_id="csharpsquid:S1144",
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
        ),
    )
    change = ReviewedFileChange(
        file="src/Foo.cs",
        changed_lines=(2492,),
        before_changed_lines=(2492,),
        after_changed_lines=(),
        line_operations=(
            ReviewedLineOperation(
                kind="delete",
                before_line=2492,
                after_line=2404,
                text="        private void DeadCode() {}",
            ),
        ),
        diff_text="@@ -2492 +2404,0 @@\n-        private void DeadCode() {}\n",
        hunk_count=1,
    )

    result = QualityGateVerifier.review(
        issue_file_path="src/Foo.cs",
        edit_contract=contract,
        reviewed_changes=(change,),
        original_issue_file_content=None,
        current_issue_file_content="\n".join(["public class Foo", "{", "}"]) + "\n",
    )

    assert result.status == "pass"
    assert "No post-edit changed lines" in result.summary


def test_propagation_verifier_ignores_stale_signature_plan_for_declaration_hygiene(tmp_path) -> None:
    issue_file = tmp_path / "src" / "Foo.cs"
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    issue_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    private async Task CollectAllRelatedOrderIds()",
                "    {",
                "        await LoadAsync();",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    contract = EditContract(
        issue_key="issue-delete-propagation",
        rule_id="csharpsquid:S1144",
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build",),
        repair_plan=RepairPlan(
            repair_shape="member_cluster_delete",
            primary_file="src/Foo.cs",
            primary_method_name="CollectAllRelatedOrderIds",
            proposed_method_name="CollectAllRelatedOrderIdsAsync",
            selected_archetype="declaration_hygiene",
            requires_signature_change=True,
            requires_propagation=False,
            verification_targets=(
                RepairPropagationTarget(
                    file="src/Foo.cs",
                    symbol="definition@3-6",
                    kind="definition",
                    start_line=3,
                    end_line=6,
                ),
            ),
        ),
    )

    result = PropagationVerifier.review(
        workspace_path=tmp_path,
        edit_contract=contract,
        issue_file_path="src/Foo.cs",
        current_issue_file_content=issue_file.read_text(encoding="utf-8"),
    )

    assert result.status == "pass"
    assert "Declaration-hygiene cleanup" in result.summary


def test_quality_gate_verifier_accepts_tuple_and_nested_generic_task_return_types() -> None:
    contract = EditContract(
        issue_key="issue-async-signature-generic",
        rule_id="csharpsquid:S3776",
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
                rule_id="async_signature",
                title="异步签名规范",
                summary="异步方法要用 Async 命名并返回 Task。",
                enforcement="hard",
            ),
        ),
    )
    change = ReviewedFileChange(
        file="src/Foo.cs",
        changed_lines=(6, 15),
        before_changed_lines=(6, 15),
        after_changed_lines=(6, 15),
        diff_text=(
            "@@ -6,0 +6,0 @@\n"
            "+    public async Task<(bool IsNoRight, int Code, string Message)> GetSnapshotPermissionAsync(int orderId)\n"
            "@@ -15,0 +15,0 @@\n"
            "+    public async Task<Dictionary<int, List<ReceiptForOrder>>> LoadReceiptsAsync(IEnumerable<int> orderIds)\n"
        ),
        hunk_count=2,
    )

    current_issue_file_content = "\n".join(
        [
            "public class Foo",
            "{",
            "    /// <summary>加载权限快照</summary>",
            '    /// <param name="orderId">订单 ID</param>',
            "    /// <returns>权限判定结果。</returns>",
            "    public async Task<(bool IsNoRight, int Code, string Message)> GetSnapshotPermissionAsync(int orderId)",
            "    {",
            "        await Task.Delay(1);",
            '        return (false, 0, "ok");',
            "    }",
            "",
            "    /// <summary>加载回款映射</summary>",
            '    /// <param name="orderIds">订单 ID 集合</param>',
            "    /// <returns>回款映射。</returns>",
            "    public async Task<Dictionary<int, List<ReceiptForOrder>>> LoadReceiptsAsync(IEnumerable<int> orderIds)",
            "    {",
            "        await Task.Delay(1);",
            "        return new Dictionary<int, List<ReceiptForOrder>>();",
            "    }",
            "}",
            "",
            "public class ReceiptForOrder {}",
        ]
    ) + "\n"

    result = QualityGateVerifier.review(
        issue_file_path="src/Foo.cs",
        edit_contract=contract,
        reviewed_changes=(change,),
        original_issue_file_content=None,
        current_issue_file_content=current_issue_file_content,
    )

    assert result.status == "pass"
    assert result.violations == ()


def test_quality_gate_verifier_ignores_public_properties_inside_private_nested_type() -> None:
    contract = EditContract(
        issue_key="issue-private-nested-xml",
        rule_id="csharpsquid:S107",
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
        ),
    )
    change = ReviewedFileChange(
        file="src/Foo.cs",
        changed_lines=(6, 7),
        before_changed_lines=(6, 7),
        after_changed_lines=(6, 7),
        diff_text=(
            "@@ -6,0 +6,2 @@\n"
            "+        public string Name { get; set; }\n"
            "+        public int Count { get; set; }\n"
        ),
        hunk_count=1,
    )
    current_issue_file_content = "\n".join(
        [
            "public class Foo",
            "{",
            "    private sealed class BatchData",
            "    {",
            "        /// <summary>内部名称</summary>",
            "        public string Name { get; set; }",
            "        public int Count { get; set; }",
            "    }",
            "}",
        ]
    ) + "\n"

    result = QualityGateVerifier.review(
        issue_file_path="src/Foo.cs",
        edit_contract=contract,
        reviewed_changes=(change,),
        original_issue_file_content=None,
        current_issue_file_content=current_issue_file_content,
    )

    assert result.status == "pass"
    assert result.violations == ()


def test_quality_gate_verifier_rejects_s3776_when_patch_only_touches_helper_and_leaves_target_method_unchanged() -> None:
    contract = EditContract(
        issue_key="issue-s3776-target-method",
        rule_id="csharpsquid:S3776",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        quality_gate_rules=(
            QualityGateRule(
                rule_id="cognitive_complexity",
                title="单方法认知复杂度不超过 30",
                summary="触达的方法应通过提取子方法、提前返回等方式控制认知复杂度。",
                enforcement="hard",
            ),
        ),
    )
    change = ReviewedFileChange(
        file="src/Foo.cs",
        changed_lines=(33, 34, 35, 36),
        before_changed_lines=(),
        after_changed_lines=(33, 34, 35, 36),
        diff_text=(
            "@@ -33,0 +33,4 @@\n"
            "+    private int Helper()\n"
            "+    {\n"
            "+        return 1;\n"
            "+    }\n"
        ),
        hunk_count=1,
    )
    original_issue_file_content = "\n".join(
        [
            "public class Foo",
            "{",
            "    public int Process(bool a, bool b, bool c, bool d, bool e, bool f, bool g, bool h)",
            "    {",
            "        if (a)",
            "        {",
            "            if (b)",
            "            {",
            "                if (c)",
            "                {",
            "                    if (d)",
            "                    {",
            "                        if (e)",
            "                        {",
            "                            if (f)",
            "                            {",
            "                                if (g)",
            "                                {",
            "                                    if (h)",
            "                                    {",
            "                                        return 1;",
            "                                    }",
            "                                }",
            "                            }",
            "                        }",
            "                    }",
            "                }",
            "            }",
            "        }",
            "        return 0;",
            "    }",
            "}",
        ]
    ) + "\n"
    current_issue_file_content = "\n".join(
        [
            "public class Foo",
            "{",
            "    public int Process(bool a, bool b, bool c, bool d, bool e, bool f, bool g, bool h)",
            "    {",
            "        if (a)",
            "        {",
            "            if (b)",
            "            {",
            "                if (c)",
            "                {",
            "                    if (d)",
            "                    {",
            "                        if (e)",
            "                        {",
            "                            if (f)",
            "                            {",
            "                                if (g)",
            "                                {",
            "                                    if (h)",
            "                                    {",
            "                                        return 1;",
            "                                    }",
            "                                }",
            "                            }",
            "                        }",
            "                    }",
            "                }",
            "            }",
            "        }",
            "        return 0;",
            "    }",
            "",
            "    private int Helper()",
            "    {",
            "        return 1;",
            "    }",
            "}",
        ]
    ) + "\n"

    result = QualityGateVerifier.review(
        issue_file_path="src/Foo.cs",
        edit_contract=contract,
        reviewed_changes=(change,),
        original_issue_file_content=original_issue_file_content,
        current_issue_file_content=current_issue_file_content,
        issue_line=4,
    )

    assert result.status == "retry"
    assert any(item.rule_id == "cognitive_complexity" for item in result.violations)
    assert any("没有下降" in item.message or "没有稳定触达" in item.message for item in result.violations)


def test_fix_verifier_runs_build_when_review_gate_waives_propagation(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-review-gate-pass",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
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
        repair_plan=RepairPlan(
            repair_shape="method_decomposition",
            primary_file="src/Foo.cs",
            primary_method_name="Foo",
            proposed_method_name="FooAsync",
            requires_signature_change=True,
            verification_targets=(
                RepairPropagationTarget(
                    file="src/Foo.cs",
                    symbol="method@8-16",
                    kind="definition",
                    start_line=8,
                    end_line=16,
                ),
            ),
        ),
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(8, 10),
            before_changed_lines=(8, 10),
            after_changed_lines=(8, 10),
            diff_text="@@ -8,1 +8,1 @@\n-public async Task Foo()\n+public async Task FooAsync()\n",
            hunk_count=1,
        ),
    )

    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.PropagationVerifier.review",
        lambda **kwargs: PropagationCheckResult(
            status="retry",
            summary="Propagation still looks stale.",
            residual_targets=("src/Foo.cs:8-16 (definition) still missing `FooAsync`",),
        ),
    )
    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.ReviewGateAgent.review",
        lambda **kwargs: ReviewGateResult(
            status="pass",
            summary="Review gate waived the propagation finding.",
            findings=(
                ReviewGateFinding(
                    finding_id="propagation",
                    source="propagation",
                    title="Signature propagation verification",
                    message="Propagation still looks stale.",
                ),
            ),
            decisions=(
                ReviewGateDecision(
                    finding_id="propagation",
                    decision="waive",
                    reason="The remaining declaration is a wrapper and should not block.",
                ),
            ),
        ),
    )

    build_calls: list[str] = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        build_calls.append(str(args[0] if args else kwargs.get("args", "")))
        return FakeCompletedProcess()

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

    assert outcome.review_gate_result.status == "pass"
    assert outcome.propagation_check_result.status == "pass"
    assert outcome.build_passed is True
    assert len(build_calls) == 2


def test_fix_verifier_stops_before_build_when_review_gate_retries(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-review-gate-retry",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=22,
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
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(22,),
            before_changed_lines=(22,),
            after_changed_lines=(22,),
            diff_text="@@ -22,1 +22,1 @@\n-if (a && b && c)\n+if (a && (b || c))\n",
            hunk_count=1,
        ),
    )

    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.ReviewGateAgent.review",
        lambda **kwargs: ReviewGateResult(
            status="retry",
            summary="Review gate confirmed the cognitive complexity blocker.",
            findings=(
                ReviewGateFinding(
                    finding_id="quality_gate:cognitive_complexity:src/Foo.cs:22:1",
                    source="quality_gate",
                    title="认知复杂度",
                    message="复杂度仍然偏高。",
                ),
            ),
            decisions=(
                ReviewGateDecision(
                    finding_id="quality_gate:cognitive_complexity:src/Foo.cs:22:1",
                    decision="confirm",
                    reason="The new branch structure is still deeply nested.",
                ),
            ),
            feedback=("继续减少嵌套层级，不要只改条件顺序。",),
        ),
    )
    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.QualityGateVerifier.review",
        lambda **kwargs: QualityGateResult(
            status="retry",
            summary="Quality gate still sees cognitive complexity issues.",
            applied_rule_ids=("cognitive_complexity",),
            violations=(
                QualityGateViolation(
                    rule_id="cognitive_complexity",
                    title="认知复杂度",
                    message="复杂度仍然偏高。",
                    file="src/Foo.cs",
                    line=22,
                ),
            ),
        ),
    )

    build_calls: list[str] = []

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        raise AssertionError("build should not run when review gate requests retry")

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

    assert outcome.review_gate_result.status == "retry"
    assert "Review gate rejected the patch" in outcome.combined_output
    assert outcome.build_passed is False
    assert build_calls == []


def test_fix_verifier_falls_back_to_quality_gate_when_review_gate_is_unavailable(monkeypatch, tmp_path) -> None:
    issue = SonarIssue(
        key="issue-review-gate-unavailable",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=22,
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
    )
    reviewed_changes = (
        ReviewedFileChange(
            file="src/Foo.cs",
            changed_lines=(22,),
            before_changed_lines=(22,),
            after_changed_lines=(22,),
            diff_text="@@ -22,1 +22,1 @@\n-if (a && b && c)\n+if (a && (b || c))\n",
            hunk_count=1,
        ),
    )

    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.ReviewGateAgent.review",
        lambda **kwargs: ReviewGateResult(
            status="not_applicable",
            summary="Review gate session returned an agent error; fell back to deterministic verifier blockers.",
            invoked=True,
            model_display="kimi-k2.5",
            findings=(
                ReviewGateFinding(
                    finding_id="quality_gate:cognitive_complexity:src/Foo.cs:22:1",
                    source="quality_gate",
                    title="认知复杂度",
                    message="复杂度仍然偏高。",
                ),
            ),
            error="selected model is unavailable",
        ),
    )
    monkeypatch.setattr(
        "pi_sonar_agent.core.fix_verifier.QualityGateVerifier.review",
        lambda **kwargs: QualityGateResult(
            status="retry",
            summary="Quality gate still sees cognitive complexity issues.",
            applied_rule_ids=("cognitive_complexity",),
            violations=(
                QualityGateViolation(
                    rule_id="cognitive_complexity",
                    title="认知复杂度",
                    message="复杂度仍然偏高。",
                    file="src/Foo.cs",
                    line=22,
                ),
            ),
        ),
    )

    build_calls: list[str] = []

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        raise AssertionError("build should not run when deterministic quality gate already requests retry")

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

    assert outcome.review_gate_result.status == "not_applicable"
    assert outcome.quality_gate_result.status == "retry"
    assert "Review gate rejected the patch" not in outcome.combined_output
    assert "Quality gate verification failed" in outcome.combined_output
    assert build_calls == []
