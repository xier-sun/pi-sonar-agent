from __future__ import annotations

from pi_sonar_agent.core.boundary_capabilities import (
    ADJACENT_CLEANUP_CAPABILITY,
    BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
    BOUNDARY_PROFILE_DECLARATION_ANCHOR,
    BOUNDARY_PROFILE_MEMBER_CLUSTER,
    DECLARATION_DELETE_CAPABILITY,
    HELPER_EXTRACT_CAPABILITY,
    MEMBER_DELETE_CAPABILITY,
    METHOD_CLUSTER_DELETE_CAPABILITY,
    MULTI_FILE_REFACTOR_CAPABILITY,
    SIGNATURE_CHANGE_CAPABILITY,
)
from pi_sonar_agent.core.issue_planner import IssuePlanner
from pi_sonar_agent.core.lessons_store import LessonsStore
from pi_sonar_agent.core.retry_context import (
    BoundaryFailureContext,
    CompilerErrorContext,
    QualityGateFailureContext,
    QualityGateViolationContext,
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
    assert "planner.repair_archetypes.constraint_injection" in plan.edit_contract.rollout_flags
    assert "planner.repair_archetypes.strategy_selection" in plan.edit_contract.rollout_flags
    assert "verifier.propagation_lifecycle" in plan.edit_contract.rollout_flags
    assert "verifier.fast_compile" in plan.edit_contract.rollout_flags
    assert "runtime.edit_failure_context_feedback" in plan.edit_contract.rollout_flags
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


def test_issue_planner_downgrades_unverified_signature_change_to_signature_preserving() -> None:
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
    assert plan.edit_contract.repair_plan.requires_signature_change is False
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert plan.edit_contract.repair_plan.fallback_archetype == "expression_simplification"
    assert plan.edit_contract.repair_plan.archetype_chain == (
        "signature_preserving_refactor",
        "expression_simplification",
    )
    assert plan.edit_contract.repair_plan.propagation_budget == 0
    assert "Keep `AutoPlugin` stable" in plan.edit_contract.repair_plan.impact_summary
    assert "avoid_signature_change_without_verified_targets" in plan.edit_contract.repair_plan.strategy_preferences
    assert any(
        "Avoid introducing new public or protected surface area" in hint
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )
    assert any(
        "verified propagation targets" in hint
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck.status == "pass"
    assert "【Repair Plan】" in plan.prompt_guidance
    assert "Requires Signature Change: no" in plan.prompt_guidance
    assert "Selected Archetype: signature_preserving_refactor" in plan.prompt_guidance
    assert "Fallback Archetype: expression_simplification" in plan.prompt_guidance
    assert "Archetype Chain: signature_preserving_refactor -> expression_simplification" in plan.prompt_guidance


def test_issue_planner_downgrades_public_interface_propagation_for_s3776(tmp_path) -> None:
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
    assert SIGNATURE_CHANGE_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert "OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IFinanceHanlerApp.cs" not in plan.edit_contract.target_files
    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is False
    assert plan.edit_contract.repair_plan.requires_propagation is False
    assert plan.edit_contract.repair_plan.proposed_method_name == ""
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert plan.edit_contract.repair_plan.fallback_archetype == "expression_simplification"
    assert plan.edit_contract.repair_plan.archetype_chain == (
        "signature_preserving_refactor",
        "expression_simplification",
    )
    assert plan.edit_contract.repair_plan.propagation_budget == 0
    assert "avoid_interface_controller_propagation" in plan.edit_contract.repair_plan.strategy_preferences
    assert any(
        "keep the externally visible api stable" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck.status == "pass"
    assert all(
        not symbol.file.endswith("IFinanceHanlerApp.cs")
        for symbol in plan.edit_contract.allowed_related_symbols
    )
    assert "Selected Archetype: signature_preserving_refactor" in plan.prompt_guidance
    assert "Constraint Hints:" in plan.prompt_guidance


def test_issue_planner_prefers_private_helper_extract_for_default_local_s3776() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-LOCAL-S3776",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=6,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=3,
        scope_end_line=16,
        validation_start_line=3,
        validation_end_line=16,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (first) { Work(); }",
            "        if (second) { Work(); }",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "private_helper_extract"
    assert plan.edit_contract.repair_plan.fallback_archetype == "guard_clause_flatten"
    assert "extract-private-helper" in plan.edit_contract.allowed_change_kinds
    assert plan.edit_contract.repair_plan.archetype_chain == (
        "private_helper_extract",
        "guard_clause_flatten",
        "local_block_reorder",
    )
    assert any(
        "small private helpers" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_allows_controlled_internal_signature_propagation_for_s3776(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    issue_file = workspace / "OpenAuth.Core" / "OpenAuth.App" / "Finance" / "FinanceHanlerApp.cs"
    caller_file = workspace / "OpenAuth.Core" / "OpenAuth.App" / "Finance" / "FinanceSyncService.cs"
    issue_file.parent.mkdir(parents=True, exist_ok=True)

    issue_file.write_text(
        "\n".join(
            [
                "class FinanceHanlerApp",
                "{",
                "    internal async Task AutoPlugin(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    caller_file.write_text(
        "\n".join(
            [
                "class FinanceSyncService",
                "{",
                "    private readonly FinanceHanlerApp _app = new();",
                "",
                "    public Task Sync(IEnumerable<int> ids)",
                "    {",
                "        return _app.AutoPlugin(ids);",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-INTERNAL-PROP",
        rule_id="csharpsquid:S3776",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs",
        issue_line=3,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=3,
        scope_end_line=6,
        validation_start_line=3,
        validation_end_line=6,
        source_lines=tuple(issue_file.read_text(encoding="utf-8").splitlines()),
        workspace_path=workspace,
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is True
    assert plan.edit_contract.repair_plan.requires_propagation is True
    assert plan.edit_contract.repair_plan.selected_archetype == "bounded_signature_propagation"
    assert plan.edit_contract.repair_plan.fallback_archetype == "private_helper_extract"
    assert plan.edit_contract.repair_plan.archetype_chain == (
        "bounded_signature_propagation",
        "private_helper_extract",
        "guard_clause_flatten",
        "local_block_reorder",
    )
    assert any(
        target.kind == "callsite" and target.file.endswith("FinanceSyncService.cs")
        for target in plan.edit_contract.repair_plan.propagation_targets
    )


def test_issue_planner_downgrades_s3776_after_async_quality_gate_retry() -> None:
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="quality_gate",
        quality_gate_failure=QualityGateFailureContext(
            violations=(
                QualityGateViolationContext(
                    rule_id="async_requires_await",
                    title="异步方法必须真正 await",
                    message="helper has no await",
                ),
            )
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-ASYNC-RETRY",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=5,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=18,
        validation_start_line=2,
        validation_end_line=18,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private async Task DemoAsync()",
            "    {",
            "        await SaveAsync();",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert "retry_sync_first" in plan.edit_contract.repair_plan.strategy_preferences
    assert any(
        "must stay synchronous" in hint
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_downgrades_public_async_rename_after_async_gate_retry(tmp_path) -> None:
    workspace = tmp_path / ".agent_workspaces" / "fix_issue"
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
                "public class FinanceHanlerApp : IFinanceHanlerApp",
                "{",
                "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
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

    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="quality_gate",
        quality_gate_failure=QualityGateFailureContext(
            violations=(
                QualityGateViolationContext(
                    rule_id="async_signature",
                    title="异步签名规范",
                    message="async method missing Async suffix",
                ),
            )
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-PROP-ASYNC-RETRY",
        rule_id="csharpsquid:S3776",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs",
        issue_line=3,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=3,
        scope_end_line=6,
        validation_start_line=3,
        validation_end_line=6,
        retry_context=retry_context,
        source_lines=tuple(issue_file.read_text(encoding="utf-8").splitlines()),
        workspace_path=workspace,
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is False
    assert plan.edit_contract.repair_plan.requires_propagation is False
    assert plan.edit_contract.repair_plan.proposed_method_name == ""
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert "avoid_async_rename_churn" in plan.edit_contract.repair_plan.strategy_preferences
    assert "preserve_existing_signature_in_this_attempt" in plan.edit_contract.repair_plan.strategy_preferences
    assert any(
        "externally visible api stable" in hint.lower() or "keep the externally visible api stable" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )
    assert any(
        "do not rename the existing public async method" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_keeps_async_gate_downgrade_from_lessons_across_build_retry(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-ASYNC-LESSON",
        issue_rule_id="csharpsquid:S3776",
        retry_context=RetryContext(
            source_attempt_number=1,
            failure_kind="quality_gate",
            quality_gate_failure=QualityGateFailureContext(
                violations=(
                    QualityGateViolationContext(
                        rule_id="async_requires_await",
                        title="异步方法必须真正 await",
                        message="helper has no await",
                    ),
                )
            ),
        ),
        scope_mode="method",
        guardrail_mode="scope",
        quality_gate_rule_ids=("async_requires_await",),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-ASYNC-LESSON-BUILD",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=5,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=18,
        validation_start_line=2,
        validation_end_line=18,
        retry_context=RetryContext(
            source_attempt_number=2,
            failure_kind="build",
            compiler_errors=(
                CompilerErrorContext(
                    file_path="src/Foo.cs",
                    line=9,
                    column=1,
                    code="CS1977",
                    message="lambda on dynamic dispatch",
                ),
            ),
        ),
        lessons_store=store,
        source_lines=(
            "class Foo",
            "{",
            "    private async Task DemoAsync()",
            "    {",
            "        await SaveAsync();",
            "    }",
            "}",
        ),
    )

    assert any(lesson.source == "quality_gate_lesson" for lesson in plan.edit_contract.planner_lessons)
    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert "prefer_private_sync_helpers" in plan.edit_contract.repair_plan.strategy_preferences
    assert any(
        "must stay synchronous" in hint
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_downgrades_s3776_after_symbol_closure_failure() -> None:
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="build",
        compiler_errors=(
            CompilerErrorContext(
                file_path="src/Foo.cs",
                line=8,
                column=1,
                code="CS0103",
                message="name does not exist",
            ),
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-CS0103",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=30,
        validation_start_line=2,
        validation_end_line=30,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert "avoid_helper_fanout_after_symbol_errors" in plan.edit_contract.repair_plan.strategy_preferences
    assert any(
        "avoid helper fan-out" in hint
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_keeps_s1144_on_minimal_deletion_strategy_after_no_change() -> None:
    retry_context = RetryContext(
        source_attempt_number=1,
        failure_kind="no_change",
        error="Agent completed without modifying any files",
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-S1144-RETRY",
        rule_id="csharpsquid:S1144",
        file_path="src/Foo.cs",
        issue_line=2,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=4,
        validation_start_line=2,
        validation_end_line=4,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "    private int _unused = 1;",
            "",
            "    public void KeepMe() { }",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "declaration_hygiene"
    assert "prefer_minimal_deletion_patch" in plan.edit_contract.repair_plan.strategy_preferences
    assert "force_direct_local_edit" in plan.edit_contract.repair_plan.strategy_preferences


def test_issue_planner_marks_type_shape_retry_constraints_for_s3776() -> None:
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="build",
        failure_fingerprints=("helper_extraction_type_break",),
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprint_repetition=1,
        compiler_errors=(
            CompilerErrorContext(
                file_path="src/Foo.cs",
                line=12,
                column=3,
                code="CS0103",
                message="name does not exist",
            ),
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-TYPE-SHAPE",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert HELPER_EXTRACT_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert "extract-private-helper" not in plan.edit_contract.allowed_change_kinds
    assert plan.edit_contract.repair_plan.repair_shape == "method_rewrite_in_place"
    assert plan.edit_contract.repair_plan.new_helpers == ()
    assert "preserve_type_shape_on_retry" in plan.edit_contract.repair_plan.strategy_preferences
    assert (
        "disable_helper_extract_after_type_shape_failure"
        in plan.edit_contract.repair_plan.strategy_preferences
    )
    assert (
        "force_in_method_refactor_after_type_shape_failure"
        in plan.edit_contract.repair_plan.strategy_preferences
    )
    assert any(
        "preserve concrete types" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_downgrades_s3776_after_anonymous_type_helper_boundary() -> None:
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="semantic_precheck",
        failure_fingerprints=("anonymous_type_helper_boundary",),
        primary_failure_fingerprint="anonymous_type_helper_boundary",
        failure_fingerprint_repetition=1,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-ANON-HELPER-BOUNDARY",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert HELPER_EXTRACT_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert "extract-private-helper" not in plan.edit_contract.allowed_change_kinds
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert (
        "forbid_helper_boundaries_for_anonymous_projections"
        in plan.edit_contract.repair_plan.strategy_preferences
    )
    assert (
        "force_in_method_refactor_for_anonymous_projections"
        in plan.edit_contract.repair_plan.strategy_preferences
    )
    assert any(
        "anonymous projections cannot cross helper boundaries" in hint.lower()
        or "keep them inside the current method body" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_downgrades_s3776_after_partial_patch_turn_exhaustion() -> None:
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="tool_input_invalid",
        failure_fingerprints=("turn_exhausted_after_partial_patch", "tool_input_invalid_burst"),
        primary_failure_fingerprint="turn_exhausted_after_partial_patch",
        failure_fingerprint_repetition=1,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-TURN-EXHAUSTION",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    public void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert (
        "prefer_one_shot_small_patch_after_turn_exhaustion"
        in plan.edit_contract.repair_plan.strategy_preferences
    )
    assert (
        "force_precise_write_payload_after_invalid_tool_input"
        in plan.edit_contract.repair_plan.strategy_preferences
    )
    assert any(
        "partial patch" in hint.lower() or "one precise patch" in hint.lower()
        for hint in plan.edit_contract.repair_plan.constraint_hints
    )


def test_issue_planner_allows_second_repeated_anonymous_type_helper_boundary_retry() -> None:
    retry_context = RetryContext(
        source_attempt_number=3,
        failure_kind="semantic_precheck",
        failure_fingerprints=("anonymous_type_helper_boundary",),
        primary_failure_fingerprint="anonymous_type_helper_boundary",
        failure_fingerprint_repetition=2,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-REPEATED-ANON-BOUNDARY",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert plan.skip_reason == ""


def test_issue_planner_allows_second_repeated_type_shape_failure_retry() -> None:
    retry_context = RetryContext(
        source_attempt_number=3,
        failure_kind="build",
        failure_fingerprints=("helper_extraction_type_break",),
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprint_repetition=2,
        compiler_errors=(
            CompilerErrorContext(
                file_path="src/Foo.cs",
                line=12,
                column=3,
                code="CS1503",
                message="cannot convert anonymous projection to dynamic helper contract",
            ),
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-SECOND-REPEATED-TYPE-SHAPE",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert HELPER_EXTRACT_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert "extract-private-helper" not in plan.edit_contract.allowed_change_kinds
    assert plan.skip_reason == ""


def test_issue_planner_skips_after_third_repeated_anonymous_type_helper_boundary() -> None:
    retry_context = RetryContext(
        source_attempt_number=4,
        failure_kind="semantic_precheck",
        failure_fingerprints=("anonymous_type_helper_boundary",),
        primary_failure_fingerprint="anonymous_type_helper_boundary",
        failure_fingerprint_repetition=3,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-REPEATED-ANON-BOUNDARY",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert "unlikely to converge" in plan.skip_reason


def test_issue_planner_skips_after_third_repeated_failure_fingerprint() -> None:
    retry_context = RetryContext(
        source_attempt_number=4,
        failure_kind="build",
        failure_fingerprints=("helper_extraction_type_break",),
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprint_repetition=3,
        compiler_errors=(
            CompilerErrorContext(
                file_path="src/Foo.cs",
                line=12,
                column=3,
                code="CS0103",
                message="name does not exist",
            ),
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-REPEATED-FP",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert "unlikely to converge" in plan.skip_reason


def test_issue_planner_skips_after_repeated_partial_patch_turn_exhaustion() -> None:
    retry_context = RetryContext(
        source_attempt_number=3,
        failure_kind="tool_input_invalid",
        failure_fingerprints=("turn_exhausted_after_partial_patch",),
        primary_failure_fingerprint="turn_exhausted_after_partial_patch",
        failure_fingerprint_repetition=2,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-PLAN-REPEATED-TURN-EXHAUSTION",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=8,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=2,
        scope_end_line=24,
        validation_start_line=2,
        validation_end_line=24,
        retry_context=retry_context,
        source_lines=(
            "class Foo",
            "{",
            "    private void Demo()",
            "    {",
            "        if (true) { }",
            "    }",
            "}",
        ),
    )

    assert "unlikely to converge" in plan.skip_reason


def test_issue_planner_does_not_schedule_async_rename_for_s1144_private_async_method() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-S1144-ASYNC-DELETE",
        rule_id="csharpsquid:S1144",
        file_path="src/Foo.cs",
        issue_line=3,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=3,
        scope_end_line=7,
        validation_start_line=3,
        validation_end_line=7,
        source_lines=(
            "class Foo",
            "{",
            "    private async Task CollectAllRelatedOrderIds()",
            "    {",
            "        await LoadAsync();",
            "    }",
            "}",
        ),
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.selected_archetype == "declaration_hygiene"
    assert plan.edit_contract.repair_plan.requires_signature_change is False
    assert plan.edit_contract.repair_plan.requires_propagation is False
    assert plan.edit_contract.repair_plan.proposed_method_name == ""
    assert plan.edit_contract.repair_plan.verification_targets == ()
    assert plan.edit_contract.repair_plan.propagation_budget == 0
    assert "skip_signature_propagation_for_this_attempt" in plan.edit_contract.repair_plan.strategy_preferences
    assert "minimal local cleanup patch" in plan.edit_contract.repair_plan.impact_summary


def test_issue_planner_prefetches_full_method_for_method_scope_rules() -> None:
    source_lines = tuple(
        [
            "class Foo",
            "{",
            "    public async Task DemoAsync()",
            "    {",
        ]
        + [f"        Step({index});" for index in range(1, 9)]
        + [
            "        await SaveAsync();",
            "    }",
            "}",
        ]
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-FULL-METHOD",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=7,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=3,
        scope_end_line=14,
        validation_start_line=3,
        validation_end_line=14,
        source_lines=source_lines,
    )

    assert plan.edit_contract.prefetched_context
    assert plan.edit_contract.prefetched_context[0].label == "target_method_full"
    assert "DemoAsync" in plan.edit_contract.prefetched_context[0].content
    assert "await SaveAsync();" in plan.edit_contract.prefetched_context[0].content


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
    assert SIGNATURE_CHANGE_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck.status == "pass"
    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is False
    assert plan.edit_contract.repair_plan.requires_propagation is False
    assert plan.edit_contract.repair_plan.propagation_targets == ()
    assert "prefer_single_file_complexity_reduction" in plan.edit_contract.repair_plan.strategy_preferences


def test_issue_planner_ignores_controller_wrapper_declarations_in_signature_propagation(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    app_file = (
        workspace
        / "OpenAuth.Core"
        / "OpenAuth.App"
        / "HumanResource"
        / "Employees"
        / "JobAbilityApp.cs"
    )
    interface_file = (
        workspace
        / "OpenAuth.Core"
        / "OpenAuth.App"
        / "HumanResource"
        / "Interfaces"
        / "IJobAbilityApp.cs"
    )
    controller_file = (
        workspace
        / "OpenAuth.Core"
        / "OpenAuth.WebApi"
        / "Controllers"
        / "HumanResource"
        / "Employee"
        / "JobAbilityController.cs"
    )
    app_file.parent.mkdir(parents=True, exist_ok=True)
    interface_file.parent.mkdir(parents=True, exist_ok=True)
    controller_file.parent.mkdir(parents=True, exist_ok=True)

    app_file.write_text(
        "\n".join(
            [
                "public class JobAbilityApp : IJobAbilityApp",
                "{",
                "    public async Task<Response<List<AbilityChangeHistoryResp>>> GetAbilityChangeHistory(string userId, JobPosition jobPosition)",
                "    {",
                "        await Task.Delay(1);",
                "        return new Response<List<AbilityChangeHistoryResp>>();",
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
                "public interface IJobAbilityApp",
                "{",
                "    Task<Response<List<AbilityChangeHistoryResp>>> GetAbilityChangeHistory(string userId, JobPosition jobPosition);",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    controller_file.write_text(
        "\n".join(
            [
                "public class JobAbilityController",
                "{",
                "    private readonly IJobAbilityApp _jobAbilityApp;",
                "",
                "    [HttpGet]",
                "    public async Task<Response<List<AbilityChangeHistoryResp>>> GetAbilityChangeHistory([FromQuery] string userId, [FromQuery] JobPosition jobPosition)",
                "    {",
                "        return await _jobAbilityApp.GetAbilityChangeHistory(userId, jobPosition);",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    propagation_targets = IssuePlanner._scan_signature_propagation_targets(
        workspace_path=workspace,
        normalized_path="OpenAuth.Core/OpenAuth.App/HumanResource/Employees/JobAbilityApp.cs",
        method_descriptor={
            "name": "GetAbilityChangeHistory",
            "proposed_name": "GetAbilityChangeHistoryAsync",
        },
        scope_start_line=3,
        scope_end_line=7,
    )

    assert any(
        target.kind == "signature_declaration"
        and target.file.endswith("IJobAbilityApp.cs")
        for target in propagation_targets
    )
    assert any(
        target.kind == "callsite"
        and target.file.endswith("JobAbilityController.cs")
        and target.start_line == 8
        for target in propagation_targets
    )
    assert not any(
        target.kind == "signature_declaration"
        and target.file.endswith("JobAbilityController.cs")
        for target in propagation_targets
    )
