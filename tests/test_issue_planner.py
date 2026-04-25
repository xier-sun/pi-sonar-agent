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


def _assert_simple_loop_minimal_plan(plan) -> None:
    assert plan.edit_contract.execution_mode == "simple_loop"
    assert plan.edit_contract.fast_path_enabled is False
    assert plan.edit_contract.plan_first_enabled is False
    assert plan.edit_contract.execution_profile == "full_path"
    assert plan.edit_contract.repair_plan is None
    assert plan.edit_contract.plan_precheck is not None
    assert plan.edit_contract.plan_precheck.status == "not_applicable"
    assert plan.edit_contract.plan_precheck.code == "simple_loop_minimal_plan"
    assert "【Repair Plan】" not in plan.prompt_guidance
    assert plan.skip_reason == ""


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


def test_issue_planner_loads_cross_issue_lessons_for_first_attempt(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    store.record_success(
        repository="repo",
        run_label="run-success",
        issue_key="ISSUE-SUCCESS",
        issue_rule_id="csharpsquid:S3776",
        summary="成功经验：优先在当前方法体内收口复杂度，再做最小补丁。",
        guidance=(
            "优先保持单文件最小补丁。",
            "优先在当前方法或当前文件内收口，不要先提取 helper/private method。",
        ),
        scope_mode="method",
        guardrail_mode="contract_review",
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-FIRST",
        rule_id="csharpsquid:S3776",
        file_path="src/Foo.cs",
        issue_line=72,
        guardrail_mode="contract_review",
        scope_mode="method",
        scope_start_line=60,
        scope_end_line=96,
        validation_start_line=60,
        validation_end_line=110,
        lessons_store=store,
    )

    assert plan.edit_contract.planner_lessons
    assert any(lesson.source == "success_pattern" for lesson in plan.edit_contract.planner_lessons)
    assert "successful strategy pattern" in plan.edit_contract.planner_lessons[0].selection_reason
    assert "prefer the most relevant recent successful strategy pattern" in plan.strategy


def test_issue_planner_prefers_shape_aware_success_lesson_on_first_attempt(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    store.record_success(
        repository="repo",
        run_label="run-local",
        issue_key="ISSUE-LOCAL",
        issue_rule_id="csharpsquid:S3776",
        summary="成功经验：局部私有方法优先在当前方法内收口，必要时再提取 very small helper。",
        guidance=("优先保持单文件最小补丁。",),
        scope_mode="method",
        guardrail_mode="scope",
        repo_slice="src",
        shape_tags=(
            "scope:method",
            "boundary:method_window",
            "cap:helper_extract",
            "access:private",
            "async:no",
        ),
    )
    store.record_success(
        repository="repo",
        run_label="run-public",
        issue_key="ISSUE-PUBLIC",
        issue_rule_id="csharpsquid:S3776",
        summary="成功经验：公开 async 方法优先保签名，在原方法体内收口复杂度，不要先提 helper 再回滚。",
        guidance=("优先保持现有公开签名和调用链稳定。",),
        scope_mode="method",
        guardrail_mode="scope",
        repo_slice="OpenAuth.Core/OpenAuth.App",
        shape_tags=(
            "scope:method",
            "boundary:method_window",
            "access:public",
            "async:yes",
            "return:task_like",
        ),
    )

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-FIRST-SHAPE",
        rule_id="csharpsquid:S3776",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs",
        issue_line=3,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=3,
        scope_end_line=6,
        validation_start_line=3,
        validation_end_line=6,
        source_lines=(
            "public class FinanceHanlerApp",
            "{",
            "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
            "    {",
            "        await SaveAsync(orderIds);",
            "    }",
            "}",
        ),
        lessons_store=store,
    )

    assert plan.edit_contract.planner_lessons
    assert "公开 async 方法优先保签名" in plan.edit_contract.planner_lessons[0].summary
    assert "repo_slice=OpenAuth.Core/OpenAuth.App" in plan.edit_contract.planner_lessons[0].selection_reason


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


def test_issue_planner_keeps_simple_loop_minimal_contract_for_low_risk_first_attempt() -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert "perf.fast_path" in plan.edit_contract.rollout_flags
    assert "planner.repair_archetypes.constraint_injection" in plan.edit_contract.rollout_flags
    assert "planner.repair_archetypes.strategy_selection" in plan.edit_contract.rollout_flags
    assert "verifier.propagation_lifecycle" in plan.edit_contract.rollout_flags
    assert "verifier.fast_compile" in plan.edit_contract.rollout_flags
    assert "runtime.edit_failure_context_feedback" in plan.edit_contract.rollout_flags
    assert "short-form fast path" not in plan.strategy


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


def test_issue_planner_keeps_simple_loop_minimal_contract_for_complexity_rules() -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert plan.edit_contract.target_files == ("src/Foo.cs",)
    assert SIGNATURE_CHANGE_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert "apply the smallest issue-focused patch" in plan.strategy


def test_issue_planner_keeps_public_interface_complexity_fix_single_file(tmp_path) -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert SIGNATURE_CHANGE_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert "OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IFinanceHanlerApp.cs" not in plan.edit_contract.target_files
    assert all(
        not symbol.file.endswith("IFinanceHanlerApp.cs")
        for symbol in plan.edit_contract.allowed_related_symbols
    )


def test_issue_planner_retains_local_helper_options_without_structured_repair_plan() -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert HELPER_EXTRACT_CAPABILITY in plan.edit_contract.allowed_capabilities
    assert "extract-private-helper" in plan.edit_contract.allowed_change_kinds


def test_issue_planner_keeps_retry_lessons_and_single_file_constraints_without_repair_plan(tmp_path) -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert any(lesson.source == "quality_gate_lesson" for lesson in plan.edit_contract.planner_lessons)
    assert "repair the last failed patch with the smallest compile-safe delta" in plan.strategy


def test_issue_planner_preserves_retry_capability_downgrades_in_simple_loop() -> None:
    type_shape_retry = RetryContext(
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
    anonymous_boundary_retry = RetryContext(
        source_attempt_number=2,
        failure_kind="semantic_precheck",
        failure_fingerprints=("anonymous_type_helper_boundary",),
        primary_failure_fingerprint="anonymous_type_helper_boundary",
        failure_fingerprint_repetition=1,
    )

    type_shape_plan = IssuePlanner.plan_issue(
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
        retry_context=type_shape_retry,
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
    anonymous_boundary_plan = IssuePlanner.plan_issue(
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
        retry_context=anonymous_boundary_retry,
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

    _assert_simple_loop_minimal_plan(type_shape_plan)
    _assert_simple_loop_minimal_plan(anonymous_boundary_plan)
    assert HELPER_EXTRACT_CAPABILITY not in type_shape_plan.edit_contract.allowed_capabilities
    assert "extract-private-helper" not in type_shape_plan.edit_contract.allowed_change_kinds
    assert HELPER_EXTRACT_CAPABILITY not in anonymous_boundary_plan.edit_contract.allowed_capabilities
    assert "extract-private-helper" not in anonymous_boundary_plan.edit_contract.allowed_change_kinds


def test_issue_planner_updates_retry_strategy_text_without_structured_repair_plan() -> None:
    no_change_plan = IssuePlanner.plan_issue(
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
        retry_context=RetryContext(
            source_attempt_number=1,
            failure_kind="no_change",
            error="Agent completed without modifying any files",
        ),
        source_lines=(
            "class Foo",
            "    private int _unused = 1;",
            "",
            "    public void KeepMe() { }",
        ),
    )
    partial_patch_plan = IssuePlanner.plan_issue(
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
        retry_context=RetryContext(
            source_attempt_number=2,
            failure_kind="tool_input_invalid",
            failure_fingerprints=("turn_exhausted_after_partial_patch", "tool_input_invalid_burst"),
            primary_failure_fingerprint="turn_exhausted_after_partial_patch",
            failure_fingerprint_repetition=1,
        ),
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

    _assert_simple_loop_minimal_plan(no_change_plan)
    _assert_simple_loop_minimal_plan(partial_patch_plan)
    assert "apply a concrete minimal fix and immediately validate it" in no_change_plan.strategy
    assert "apply the smallest issue-focused patch" in partial_patch_plan.strategy
    assert "extract-private-helper" in partial_patch_plan.edit_contract.allowed_change_kinds


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


def test_issue_planner_does_not_skip_repeated_failures_in_simple_loop() -> None:
    repeated_anon_plan = IssuePlanner.plan_issue(
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
        retry_context=RetryContext(
            source_attempt_number=4,
            failure_kind="semantic_precheck",
            failure_fingerprints=("anonymous_type_helper_boundary",),
            primary_failure_fingerprint="anonymous_type_helper_boundary",
            failure_fingerprint_repetition=3,
        ),
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
    repeated_build_plan = IssuePlanner.plan_issue(
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
        retry_context=RetryContext(
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
        ),
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
    repeated_turn_plan = IssuePlanner.plan_issue(
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
        retry_context=RetryContext(
            source_attempt_number=3,
            failure_kind="tool_input_invalid",
            failure_fingerprints=("turn_exhausted_after_partial_patch",),
            primary_failure_fingerprint="turn_exhausted_after_partial_patch",
            failure_fingerprint_repetition=2,
        ),
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

    _assert_simple_loop_minimal_plan(repeated_anon_plan)
    _assert_simple_loop_minimal_plan(repeated_build_plan)
    _assert_simple_loop_minimal_plan(repeated_turn_plan)


def test_issue_planner_keeps_private_async_cleanup_minimal_in_simple_loop() -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert SIGNATURE_CHANGE_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert plan.edit_contract.target_files == ("src/Foo.cs",)


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


def test_issue_planner_keeps_real_workspace_complexity_fix_single_file_in_simple_loop(tmp_path) -> None:
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

    _assert_simple_loop_minimal_plan(plan)
    assert SIGNATURE_CHANGE_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert MULTI_FILE_REFACTOR_CAPABILITY not in plan.edit_contract.allowed_capabilities
    assert plan.edit_contract.target_files == ("OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs",)


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
