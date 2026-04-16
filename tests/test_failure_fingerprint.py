from __future__ import annotations

from pi_sonar_agent.core.failure_fingerprint import detect_failure_fingerprints
from pi_sonar_agent.core.issue_retry import _carry_forward_blocker_context
from pi_sonar_agent.core.retry_context import (
    CompilerErrorContext,
    QualityGateFailureContext,
    QualityGateViolationContext,
    RetryContext,
    SemanticPrecheckFailureContext,
    SemanticPrecheckFindingContext,
)


def test_detect_failure_fingerprints_maps_quality_gate_and_compiler_patterns() -> None:
    fingerprints = detect_failure_fingerprints(
        failure_kind="build",
        compiler_errors=(
            CompilerErrorContext(
                file_path="src/Foo.cs",
                line=10,
                column=5,
                code="CS0103",
                message="The name 'helperResult' does not exist in the current context",
            ),
            CompilerErrorContext(
                file_path="src/Foo.cs",
                line=14,
                column=9,
                code="CS8619",
                message="Nullability of reference types in value doesn't match target type",
            ),
        ),
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

    assert "async_without_await" in fingerprints
    assert "helper_extraction_type_break" in fingerprints
    assert "nullable_type_mismatch" in fingerprints


def test_carry_forward_blocker_context_increments_repeated_failure_fingerprint() -> None:
    previous = RetryContext(
        source_attempt_number=1,
        failure_kind="build",
        failure_fingerprints=("helper_extraction_type_break",),
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprint_repetition=1,
    )
    next_context = RetryContext(
        source_attempt_number=2,
        failure_kind="build",
        failure_fingerprints=("helper_extraction_type_break",),
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprint_repetition=1,
    )

    merged = _carry_forward_blocker_context(previous, next_context)

    assert merged.primary_failure_fingerprint == "helper_extraction_type_break"
    assert merged.failure_fingerprint_repetition == 2


def test_detect_failure_fingerprints_marks_invalid_tool_input_and_partial_patch_turn_exhaustion() -> None:
    fingerprints = detect_failure_fingerprints(
        failure_kind="tool_input_invalid",
        raw_output="Reached maximum number of turns (20)",
        changed_files=("src/Foo.cs",),
    )

    assert "tool_input_invalid_burst" in fingerprints
    assert "turn_exhausted_after_partial_patch" in fingerprints


def test_detect_failure_fingerprints_marks_language_feature_incompatibility() -> None:
    fingerprints = detect_failure_fingerprints(
        failure_kind="semantic_precheck",
        raw_output="language_feature_compatibility: 当前仓库不支持 record",
    )

    assert "lang_feature_incompatible" in fingerprints


def test_detect_failure_fingerprints_maps_semantic_precheck_findings() -> None:
    fingerprints = detect_failure_fingerprints(
        failure_kind="semantic_precheck",
        semantic_precheck_failure=SemanticPrecheckFailureContext(
            summary="Semantic precheck rejected the patch.",
            findings=(
                SemanticPrecheckFindingContext(
                    finding_id="anonymous_type_helper_boundary",
                    title="匿名类型跨 helper 边界风险",
                    message="当前 patch 同时新增 helper 和匿名类型表达式。",
                    file="src/Foo.cs",
                    line=18,
                    evidence="return items.Select(x => new { x.Id });",
                    retry_hint="匿名类型保持在当前方法内。",
                ),
            ),
        ),
    )

    assert "anonymous_type_helper_boundary" in fingerprints
    assert "anonymous_type_leak" in fingerprints


def test_detect_failure_fingerprints_maps_dynamic_helper_signature_boundary() -> None:
    fingerprints = detect_failure_fingerprints(
        failure_kind="semantic_precheck",
        semantic_precheck_failure=SemanticPrecheckFailureContext(
            summary="Semantic precheck rejected the patch.",
            findings=(
                SemanticPrecheckFindingContext(
                    finding_id="dynamic_helper_signature_boundary",
                    title="helper 签名不应退化为 dynamic",
                    message="新增 helper 使用 dynamic 签名承载状态。",
                    file="src/Foo.cs",
                    line=18,
                    evidence="private void ProcessOrderGroupForPenalty(ILookup<int, dynamic> items)",
                    retry_hint="保持 concrete type。",
                ),
            ),
        ),
    )

    assert "helper_extraction_type_break" in fingerprints
