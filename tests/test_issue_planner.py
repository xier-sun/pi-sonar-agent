from __future__ import annotations

from pi_sonar_agent.core.boundary_capabilities import (
    ADJACENT_CLEANUP_CAPABILITY,
    BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
    BOUNDARY_PROFILE_DECLARATION_ANCHOR,
    BOUNDARY_PROFILE_MEMBER_CLUSTER,
    DECLARATION_DELETE_CAPABILITY,
    MEMBER_DELETE_CAPABILITY,
    METHOD_CLUSTER_DELETE_CAPABILITY,
    MULTI_FILE_REFACTOR_CAPABILITY,
    SIGNATURE_CHANGE_CAPABILITY,
)
from pi_sonar_agent.core.issue_planner import IssuePlanner
from pi_sonar_agent.core.lessons_store import LessonsStore
from pi_sonar_agent.core.repair_plan import PlanPrecheckResult
from pi_sonar_agent.core.retry_context import (
    BoundaryFailureContext,
    RetryContext,
    ScopeViolationContext,
)


def test_issue_planner_loads_recent_lessons_into_contract(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    retry_context = RetryContext(
        source_attempt_number=1,
        failure_kind="scope",
        summary="Issue changes exceeded allowed scope.",
        scope_violation=ScopeViolationContext(
            raw_output="Issue changes exceeded the allowed Sonar edit scope.",
            allowed_lines="72-72",
            changed_lines_outside_scope="141",
            constraints=(
                "- 只保留 Sonar 指向的那一处修改。",
                "- 不要顺手修改本文件其他相同写法或同类规则的位置。",
            ),
        ),
    )
    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-1",
        issue_rule_id="csharpsquid:S125",
        retry_context=retry_context,
        scope_mode="statement",
        guardrail_mode="scope",
        quality_gate_rule_ids=("public_xml_docs",),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-1",
        rule_id="csharpsquid:S125",
        file_path="src/Foo.cs",
        issue_line=72,
        guardrail_mode="scope",
        scope_mode="statement",
        scope_start_line=72,
        scope_end_line=72,
        validation_start_line=72,
        validation_end_line=72,
        retry_context=retry_context,
        lessons_store=store,
    )

    assert plan.edit_contract.planner_lessons
    assert any("只保留 Sonar 指向的那一处修改" in hint for hint in plan.edit_contract.review_hints)
    assert "Recent Lesson" in plan.prompt_guidance
    assert "avoid the repeated failure patterns" in plan.strategy


def test_issue_planner_attaches_boundary_capabilities_to_contract() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-2",
        rule_id="csharpsquid:S125",
        file_path="src/Foo.cs",
        issue_line=72,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=72,
        scope_end_line=72,
        validation_start_line=68,
        validation_end_line=73,
    )

    assert plan.edit_contract.boundary_profile == BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP
    assert ADJACENT_CLEANUP_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert "Boundary Profile" in plan.prompt_guidance
    assert "Allowed Capabilities" in plan.prompt_guidance


def test_issue_planner_builds_declaration_anchor_related_symbol_ranges() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-3",
        rule_id="csharpsquid:S1481",
        file_path="src/Foo.cs",
        issue_line=4,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=4,
        scope_end_line=4,
        validation_start_line=4,
        validation_end_line=5,
        source_lines=(
            "class Foo",
            "{",
            "    var slpDict = BuildMap();",
            "    return slpDict.Count;",
            "}",
        ),
    )

    assert plan.edit_contract.boundary_profile == BOUNDARY_PROFILE_DECLARATION_ANCHOR
    assert DECLARATION_DELETE_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert any(symbol.symbol.startswith("declaration_anchor@") for symbol in plan.edit_contract.allowed_related_symbols)
    assert (3, 3) in plan.edit_contract.allowed_line_ranges
    assert any(snippet.label == "issue_window" for snippet in plan.edit_contract.prefetched_context)
    assert any(snippet.label.startswith("declaration_anchor@") for snippet in plan.edit_contract.prefetched_context)
    assert "Related Symbols" in plan.prompt_guidance


def test_issue_planner_builds_adjacent_cleanup_multirange_contract() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-4",
        rule_id="csharpsquid:S125",
        file_path="src/Foo.cs",
        issue_line=5,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=5,
        scope_end_line=5,
        validation_start_line=5,
        validation_end_line=8,
        source_lines=(
            "class Foo",
            "{",
            "    var slpDict = BuildMap();",
            "",
            "    //await AddReceiptsAsync(result, req, slpDict);",
            "    return result;",
            "}",
        ),
    )

    assert plan.edit_contract.boundary_profile == BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP
    assert any(symbol.symbol.startswith("adjacent_cleanup@") for symbol in plan.edit_contract.allowed_related_symbols)
    assert (3, 3) in plan.edit_contract.allowed_line_ranges
    assert (5, 8) in plan.edit_contract.allowed_line_ranges
    assert any(snippet.label.startswith("adjacent_cleanup@") for snippet in plan.edit_contract.prefetched_context)


def test_issue_planner_builds_member_cluster_related_ranges() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-5",
        rule_id="csharpsquid:S1144",
        file_path="src/Foo.cs",
        issue_line=2,
        guardrail_mode="contract_review",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=4,
        validation_start_line=2,
        validation_end_line=4,
        source_lines=(
            "class Foo",
            "    private async Task AddReceiptsAsync()",
            "    {",
            "    }",
            "",
            "    private async Task AddReceiptsCaseAAsync()",
            "    {",
            "    }",
            "",
            "    public void KeepMe()",
            "    {",
            "    }",
        ),
    )

    assert plan.edit_contract.boundary_profile == BOUNDARY_PROFILE_MEMBER_CLUSTER
    assert MEMBER_DELETE_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert METHOD_CLUSTER_DELETE_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert any(symbol.symbol.startswith("method_cluster@") for symbol in plan.edit_contract.allowed_related_symbols)
    assert (6, 8) in plan.edit_contract.allowed_line_ranges


def test_issue_planner_builds_multiple_member_cluster_ranges() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-5B",
        rule_id="csharpsquid:S1144",
        file_path="src/Foo.cs",
        issue_line=2,
        guardrail_mode="contract_review",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=4,
        validation_start_line=2,
        validation_end_line=4,
        source_lines=(
            "class Foo",
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
        ),
    )

    cluster_ranges = {
        (symbol.start_line, symbol.end_line)
        for symbol in plan.edit_contract.allowed_related_symbols
        if symbol.symbol.startswith("method_cluster@")
    }

    assert (6, 8) in cluster_ranges
    assert (10, 12) in cluster_ranges


def test_issue_planner_enables_fast_path_for_low_risk_first_attempt() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-FAST",
        rule_id="csharpsquid:S1481",
        file_path="src/Foo.cs",
        issue_line=4,
        guardrail_mode="scope",
        scope_mode="statement",
        scope_start_line=4,
        scope_end_line=4,
        validation_start_line=4,
        validation_end_line=5,
        source_lines=(
            "class Foo",
            "{",
            "    var slpDict = BuildMap();",
            "    return result;",
            "}",
        ),
    )

    assert plan.edit_contract.fast_path_enabled is True
    assert plan.edit_contract.execution_profile == "fast_path_short_form"
    assert "perf.fast_path" in plan.edit_contract.rollout_flags
    assert "short-form fast path" in plan.strategy


def test_issue_planner_disables_fast_path_after_retry_context() -> None:
    retry_context = RetryContext(
        source_attempt_number=1,
        failure_kind="scope",
        summary="Issue changes exceeded allowed scope.",
    )
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-FAST-RETRY",
        rule_id="csharpsquid:S1481",
        file_path="src/Foo.cs",
        issue_line=4,
        guardrail_mode="scope",
        scope_mode="statement",
        scope_start_line=4,
        scope_end_line=4,
        validation_start_line=4,
        validation_end_line=5,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    var slpDict = BuildMap();",
            "    return result;",
            "}",
        ),
    )

    assert plan.edit_contract.fast_path_enabled is False
    assert plan.edit_contract.execution_profile == "full_path"


def test_issue_planner_uses_boundary_lessons_to_add_fallback_ranges(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    retry_context = RetryContext(
        source_attempt_number=1,
        failure_kind="scope",
        summary="Issue changes exceeded allowed scope.",
        boundary_failure=BoundaryFailureContext(code="adjacent_cleanup_not_declared"),
        scope_violation=ScopeViolationContext(
            raw_output="Issue changes exceeded the allowed Sonar edit scope.",
            allowed_lines="2224-2231",
            changed_lines_outside_scope="2223",
            constraints=("- 只保留当前 issue 所需改动。",),
        ),
    )
    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-6",
        issue_rule_id="csharpsquid:S125",
        retry_context=retry_context,
        scope_mode="statement",
        guardrail_mode="contract_review",
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-6",
        rule_id="csharpsquid:S125",
        file_path="src/Foo.cs",
        issue_line=10,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=10,
        scope_end_line=10,
        validation_start_line=10,
        validation_end_line=12,
        retry_context=retry_context,
        lessons_store=store,
    )

    assert any(
        symbol.symbol.startswith("adjacent_cleanup_lesson@")
        for symbol in plan.edit_contract.allowed_related_symbols
    )
    assert (9, 9) in plan.edit_contract.allowed_line_ranges


def test_issue_planner_enables_plan_first_for_complex_rule_and_detects_signature_conflict() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=5,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=12,
        validation_start_line=2,
        validation_end_line=12,
        source_lines=(
            "class Foo",
            "{",
            "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
            "    {",
            "        await SaveAsync(orderIds);",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.plan_first_enabled is True
    assert plan.edit_contract.execution_profile == "plan_first_full_path"
    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is True
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck == PlanPrecheckResult(
        status="conflict",
        blocking=True,
        code="signature_change_not_allowed",
        summary="Plan 预检发现本次修复需要 signature_change，但当前 contract 不允许该能力。",
        details=(
            "当前结构化 plan 预计需要修改方法签名/名称，但 EditContract 未声明 signature_change capability。",
        ),
        guidance=(
            "如果该规则必须修改方法名或方法签名，请先让 planner/contract 显式放开 signature_change。",
            "如果不允许改签名，则应在 edit 前调整计划，避免进入必然失败的 attempt。",
        ),
    )
    assert "【Repair Plan】" in plan.prompt_guidance
    assert "Requires Signature Change: yes" in plan.prompt_guidance


def test_issue_planner_allows_controlled_signature_propagation_for_s3776(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    issue_file = workspace / "OpenAuth.Core" / "OpenAuth.App" / "Finance" / "FinanceHanlerApp.cs"
    interface_file = (
        workspace
        / "OpenAuth.Core"
        / "OpenAuth.App"
        / "Finance"
        / "Interfaces"
        / "IFinanceHanlerApp.cs"
    )
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    interface_file.parent.mkdir(parents=True, exist_ok=True)

    issue_file.write_text(
        "\n".join(
            [
                "class FinanceHanlerApp : IFinanceHanlerApp",
                "{",
                "    public async Task Sync()",
                "    {",
                "        await AutoPlugin(ids);",
                "    }",
                "",
                "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
                "    }",
                "",
                "    void Log()",
                "    {",
                "        Console.WriteLine(nameof(AutoPlugin));",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    interface_file.write_text(
        "\n".join(
            [
                "public interface IFinanceHanlerApp",
                "{",
                "    Task AutoPlugin(IEnumerable<int> orderIds);",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-PROP",
        rule_id="csharpsquid:S3776",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=8,
        scope_end_line=11,
        validation_start_line=8,
        validation_end_line=11,
        source_lines=tuple(issue_file.read_text(encoding="utf-8").splitlines()),
        workspace_path=workspace,
    )

    assert plan.edit_contract.plan_first_enabled is True
    assert SIGNATURE_CHANGE_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert "OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IFinanceHanlerApp.cs" in plan.edit_contract.target_files
    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is True
    assert plan.edit_contract.repair_plan.requires_propagation is True
    assert plan.edit_contract.repair_plan.proposed_method_name == "AutoPluginAsync"
    assert any(
        target.kind == "signature_declaration"
        and target.file.endswith("IFinanceHanlerApp.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )
    assert any(
        target.kind == "callsite" and target.file.endswith("FinanceHanlerApp.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )
    assert any(
        target.kind == "nameof_ref" and target.file.endswith("FinanceHanlerApp.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck.status == "pass"
    assert any(
        symbol.file.endswith("IFinanceHanlerApp.cs")
        for symbol in plan.edit_contract.allowed_related_symbols
    )


def test_issue_planner_signature_propagation_scans_real_agent_workspace_layout(tmp_path) -> None:
    workspace = tmp_path / ".agent_workspaces" / "fix_BI_20260409171247-01"
    issue_file = workspace / "OpenAuth.Core" / "OpenAuth.App" / "Finance" / "FinanceHanlerApp.cs"
    interface_file = (
        workspace
        / "OpenAuth.Core"
        / "OpenAuth.App"
        / "Finance"
        / "Interfaces"
        / "IFinanceHanlerApp.cs"
    )
    issue_file.parent.mkdir(parents=True, exist_ok=True)
    interface_file.parent.mkdir(parents=True, exist_ok=True)

    issue_file.write_text(
        "\n".join(
            [
                "class FinanceHanlerApp : IFinanceHanlerApp",
                "{",
                "    public async Task Sync()",
                "    {",
                "        await AutoPlugin(ids);",
                "    }",
                "",
                "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
                "    }",
                "",
                "    void Log()",
                "    {",
                "        Console.WriteLine(nameof(AutoPlugin));",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    interface_file.write_text(
        "\n".join(
            [
                "public interface IFinanceHanlerApp",
                "{",
                "    Task AutoPlugin(IEnumerable<int> orderIds);",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-PROP-REAL-WORKSPACE",
        rule_id="csharpsquid:S3776",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=8,
        scope_end_line=11,
        validation_start_line=8,
        validation_end_line=11,
        source_lines=tuple(issue_file.read_text(encoding="utf-8").splitlines()),
        workspace_path=workspace,
    )

    assert plan.edit_contract.plan_first_enabled is True
    assert SIGNATURE_CHANGE_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck.status == "pass"
    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is True
    assert plan.edit_contract.repair_plan.requires_propagation is True
    assert any(
        target.kind == "signature_declaration"
        and target.file.endswith("IFinanceHanlerApp.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )
    assert any(
        target.kind == "callsite" and target.file.endswith("FinanceHanlerApp.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )
    assert any(
        target.kind == "nameof_ref" and target.file.endswith("FinanceHanlerApp.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )
