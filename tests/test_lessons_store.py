from __future__ import annotations

import json

from pi_sonar_agent.core.lessons_store import LessonsStore
from pi_sonar_agent.core.retry_context import (
    BoundaryFailureContext,
    QualityGateFailureContext,
    QualityGateViolationContext,
    RetryContext,
    ScopeViolationContext,
)


def test_lessons_store_records_patterns_and_returns_planner_lessons(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="quality_gate",
        summary="Quality gate rejected the patch.",
        guidance=("先修复质量门禁问题。",),
        boundary_failure=BoundaryFailureContext(
            code="scope_symbol_anchor_miss",
            summary="Patch missed the declaration anchor range.",
        ),
        scope_violation=ScopeViolationContext(
            raw_output="Issue changes exceeded the allowed Sonar edit scope.",
            allowed_lines="2224-2224",
            changed_lines_outside_scope="2223",
            constraints=("- 只保留当前 issue 所需改动。",),
        ),
        quality_gate_failure=QualityGateFailureContext(
            summary="C# quality gate rejected the patch.",
            violations=(
                QualityGateViolationContext(
                    rule_id="async_signature",
                    title="异步签名规范",
                    message="Async method must end with Async.",
                    retry_hint="将异步方法名改为 Async 结尾。",
                ),
            ),
        ),
    )

    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-1",
        issue_rule_id="csharpsquid:S3776",
        retry_context=retry_context,
        scope_mode="method",
        guardrail_mode="contract_review",
        quality_gate_rule_ids=("async_signature",),
    )

    assert store.quality_gate_lessons_path.exists()
    assert store.boundary_patterns_path.exists()
    assert store.rule_patterns_path.exists()

    quality_lines = store.quality_gate_lessons_path.read_text(encoding="utf-8").splitlines()
    assert len(quality_lines) == 1
    assert json.loads(quality_lines[0])["quality_gate_rule_ids"] == ["async_signature"]

    planner_lessons = store.load_planner_lessons(
        issue_rule_id="csharpsquid:S3776",
        failure_kind="quality_gate",
        scope_mode="method",
        guardrail_mode="contract_review",
        boundary_failure_code="scope_symbol_anchor_miss",
        quality_gate_rule_ids=("async_signature",),
    )

    assert planner_lessons
    assert any(lesson.source == "quality_gate_lesson" for lesson in planner_lessons)
    assert any("Async method must end with Async" in lesson.summary for lesson in planner_lessons)
    assert any(lesson.boundary_failure_code == "scope_symbol_anchor_miss" for lesson in planner_lessons)


def test_lessons_store_prefers_exact_failure_fingerprint_and_limits_injection(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    helper_retry = RetryContext(
        source_attempt_number=1,
        issue_rule_id="csharpsquid:S3776",
        failure_kind="build",
        summary="Helper extraction broke the type shape.",
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprints=("helper_extraction_type_break",),
        guidance=("不要再提取 helper。",),
    )
    public_retry = RetryContext(
        source_attempt_number=2,
        issue_rule_id="csharpsquid:S3776",
        failure_kind="build",
        summary="Public surface drift after signature change.",
        primary_failure_fingerprint="public_surface_drift",
        failure_fingerprints=("public_surface_drift",),
        guidance=("恢复公开签名。",),
    )

    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-HELPER",
        issue_rule_id="csharpsquid:S3776",
        retry_context=helper_retry,
        scope_mode="method",
        guardrail_mode="contract_review",
    )
    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-PUBLIC",
        issue_rule_id="csharpsquid:S3776",
        retry_context=public_retry,
        scope_mode="method",
        guardrail_mode="contract_review",
    )

    planner_lessons = store.load_planner_lessons(
        issue_rule_id="csharpsquid:S3776",
        failure_kind="build",
        failure_fingerprints=("helper_extraction_type_break",),
        scope_mode="method",
        guardrail_mode="contract_review",
    )

    assert planner_lessons
    assert len(planner_lessons) <= 2
    assert planner_lessons[0].primary_failure_fingerprint == "helper_extraction_type_break"
    assert planner_lessons[0].selection_mode == "rule_plus_fingerprint"
    assert "failure_fingerprint=helper_extraction_type_break" in planner_lessons[0].selection_reason


def test_lessons_store_returns_success_patterns_for_first_attempts(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")

    store.record_success(
        repository="repo",
        run_label="run-success",
        issue_key="ISSUE-SUCCESS",
        issue_rule_id="csharpsquid:S3776",
        summary="成功经验：优先在当前方法体内做最小收口，再交给外层 build 验证。",
        guidance=(
            "优先保持单文件最小补丁。",
            "优先在当前方法或当前文件内收口，不要先提取 helper/private method。",
        ),
        scope_mode="method",
        guardrail_mode="contract_review",
    )

    planner_lessons = store.load_planner_lessons(
        issue_rule_id="csharpsquid:S3776",
        scope_mode="method",
        guardrail_mode="contract_review",
    )

    assert planner_lessons
    assert any(lesson.source == "success_pattern" for lesson in planner_lessons)
    assert any("当前方法体内做最小收口" in lesson.summary for lesson in planner_lessons)
    assert any(lesson.selection_mode == "rule_exact_success" for lesson in planner_lessons)


def test_lessons_store_prefers_success_patterns_with_matching_shape_and_repo_slice(tmp_path) -> None:
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

    planner_lessons = store.load_planner_lessons(
        issue_rule_id="csharpsquid:S3776",
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

    assert planner_lessons
    assert "公开 async 方法优先保签名" in planner_lessons[0].summary
    assert "repo_slice=OpenAuth.Core/OpenAuth.App" in planner_lessons[0].selection_reason
    assert "shape_tags=access:public" in planner_lessons[0].selection_reason
