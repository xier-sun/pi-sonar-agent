"""Structured failure fingerprint extraction for retry strategy switching."""

from __future__ import annotations

from pi_sonar_agent.core.retry_context import (
    BoundaryFailureContext,
    CompilerErrorContext,
    QualityGateFailureContext,
    ReviewGateFailureContext,
    SemanticPrecheckFailureContext,
)

_NULLABLE_COMPILER_CODES = {"CS8600", "CS8602", "CS8604", "CS8619", "CS8620", "CS8625"}
_HELPER_BREAK_CODES = {"CS0103", "CS0411", "CS1061", "CS1503", "CS1593", "CS7036"}
_PUBLIC_SURFACE_CODES = {"CS0535", "CS0738"}
_LANGUAGE_FEATURE_MARKERS = (
    "language_feature_compatibility",
    "当前仓库不支持 record",
    "当前仓库不支持 init",
    "当前仓库不支持 required",
    "当前仓库不支持 file-scoped namespace",
    "当前仓库不支持 global using",
    "feature 'records' is not available",
    "feature 'init-only setters' is not available",
    "feature 'required members' is not available",
    "feature 'file-scoped namespace' is not available",
    "feature 'global using directive' is not available",
)
_SEMANTIC_PRECHECK_FINGERPRINTS = {
    "anonymous_type_helper_boundary": "anonymous_type_helper_boundary",
    "async_without_await": "async_without_await",
    "dynamic_helper_signature_boundary": "helper_extraction_type_break",
    "language_feature_compatibility": "lang_feature_incompatible",
    "repair_plan_new_type_forbidden": "repair_plan_contract_violation",
    "repair_plan_signature_change_forbidden": "repair_plan_contract_violation",
    "signature_propagation_incomplete": "signature_propagation_incomplete",
}


def detect_failure_fingerprints(
    *,
    failure_kind: str,
    compiler_errors: tuple[CompilerErrorContext, ...] = (),
    quality_gate_failure: QualityGateFailureContext | None = None,
    review_gate_failure: ReviewGateFailureContext | None = None,
    boundary_failure: BoundaryFailureContext | None = None,
    semantic_precheck_failure: SemanticPrecheckFailureContext | None = None,
    raw_output: str = "",
    error: str = "",
    changed_files: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Detect normalized failure fingerprints from retry evidence."""

    fingerprints: list[str] = []

    def add(value: str) -> None:
        normalized = str(value or "").strip()
        if normalized and normalized not in fingerprints:
            fingerprints.append(normalized)

    if semantic_precheck_failure is not None:
        for finding in semantic_precheck_failure.findings:
            finding_id = str(finding.finding_id or "").strip()
            mapped = _SEMANTIC_PRECHECK_FINGERPRINTS.get(finding_id)
            if mapped:
                add(mapped)
            haystack = " ".join(
                [
                    finding_id,
                    str(finding.title or ""),
                    str(finding.message or ""),
                    str(finding.evidence or ""),
                    str(finding.retry_hint or ""),
                ]
            ).lower()
            if any(marker in haystack for marker in _LANGUAGE_FEATURE_MARKERS):
                add("lang_feature_incompatible")
            if "anonymous type" in haystack or "匿名类型" in haystack:
                add("anonymous_type_leak")

    if quality_gate_failure is not None:
        for violation in quality_gate_failure.violations:
            rule_id = str(violation.rule_id or "").strip()
            if rule_id == "language_feature_compatibility":
                add("lang_feature_incompatible")
            elif rule_id == "async_requires_await":
                add("async_without_await")
            elif rule_id == "public_xml_docs":
                add("public_surface_drift")

    if review_gate_failure is not None:
        for decision in review_gate_failure.decisions:
            haystack = " ".join(
                [
                    str(decision.finding_id or ""),
                    str(decision.title or ""),
                    str(decision.source or ""),
                    str(decision.reason or ""),
                ]
            ).lower()
            if "propagation" in haystack or "callsite" in haystack or "nameof" in haystack:
                add("signature_propagation_incomplete")
            if "public" in haystack or "interface" in haystack or "xml" in haystack:
                add("public_surface_drift")

    if boundary_failure is not None:
        boundary_text = " ".join(
            [
                str(boundary_failure.code or ""),
                str(boundary_failure.summary or ""),
                *tuple(boundary_failure.secondary_codes or ()),
            ]
        ).lower()
        if "propagation" in boundary_text:
            add("signature_propagation_incomplete")

    for compiler_error in compiler_errors:
        code = str(compiler_error.code or "").strip().upper()
        message = str(compiler_error.message or "").lower()
        if code in _NULLABLE_COMPILER_CODES or "nullable" in message:
            add("nullable_type_mismatch")
        if "anonymous type" in message:
            add("anonymous_type_leak")
        if code in _HELPER_BREAK_CODES:
            add("helper_extraction_type_break")
        if code in _PUBLIC_SURFACE_CODES:
            add("public_surface_drift")

    normalized_failure_kind = str(failure_kind or "").strip()
    if normalized_failure_kind == "no_change":
        add("turn_exhausted_no_progress")
    if normalized_failure_kind == "tool_input_invalid":
        add("tool_input_invalid_burst")

    normalized_output = " ".join([str(raw_output or ""), str(error or "")]).lower()
    if "anonymous type" in normalized_output or "匿名类型" in normalized_output:
        add("anonymous_type_leak")
    if "nullable" in normalized_output:
        add("nullable_type_mismatch")
    if "propagation" in normalized_output:
        add("signature_propagation_incomplete")
    if changed_files and "maximum number of turns" in normalized_output:
        add("turn_exhausted_after_partial_patch")
    if any(marker in normalized_output for marker in _LANGUAGE_FEATURE_MARKERS):
        add("lang_feature_incompatible")

    for compiler_error in compiler_errors:
        code = str(compiler_error.code or "").strip().upper()
        message = str(compiler_error.message or "").lower()
        if code == "CS0246" and "record" in message:
            add("lang_feature_incompatible")
        if "init-only" in message or "required members" in message:
            add("lang_feature_incompatible")

    return tuple(fingerprints)
