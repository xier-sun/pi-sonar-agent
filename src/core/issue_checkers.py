"""Lightweight rule-specific issue validators for headless simple-loop execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pi_sonar_agent.agent.rule_policies import get_rule_policy
from pi_sonar_agent.agent.rule_validators import (
    _strip_comments_and_literals,
    validate_rule_fix,
)
from pi_sonar_agent.core.light_check_catalog import load_default_light_check_catalog
from pi_sonar_agent.core.quality_gate_verifier import QualityGateVerifier
from pi_sonar_agent.core.state import serialize_state

_QUOTED_SYMBOL_PATTERN = re.compile(r"[`'\"“”](?P<name>[A-Za-z_]\w*)[`'\"“”]")


@dataclass(frozen=True)
class LightIssueCheckOutcome:
    """Tri-state issue-check outcome used by the simple-loop post checker."""

    status: str
    summary: str
    findings: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


def run_issue_check(*, issue, file_content: str | None) -> LightIssueCheckOutcome:
    """Run the configured lightweight validator for the current rule."""

    text = str(file_content or "")
    if not text.strip():
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Current issue file content was unavailable for local issue validation.",
        )

    catalog = load_default_light_check_catalog()
    profile = catalog.rule(getattr(issue, "rule", ""))
    if profile is None or not str(profile.issue_validator or "").strip():
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="No lightweight rule validator is configured for this issue.",
        )

    validator_name = str(profile.issue_validator or "").strip()
    if validator_name == "rule_policy_validator":
        return _run_rule_policy_validator(issue=issue, file_content=text)
    if validator_name == "s3776_method_complexity":
        return _run_s3776_method_complexity(issue=issue, file_content=text, validator_config=profile.validator_config or {})
    if validator_name == "s107_parameter_count":
        return _run_s107_parameter_count(issue=issue, file_content=text, validator_config=profile.validator_config or {})
    if validator_name == "s1172_unused_parameter":
        return _run_s1172_unused_parameter(issue=issue, file_content=text)
    if validator_name == "s1481_unused_local":
        return _run_s1481_unused_local(issue=issue, file_content=text)
    if validator_name == "s1144_unused_private_member":
        return _run_s1144_unused_private_member(issue=issue, file_content=text)

    return LightIssueCheckOutcome(
        status="UNKNOWN",
        summary=f"Lightweight validator '{validator_name}' is not implemented locally.",
    )


def _run_rule_policy_validator(*, issue, file_content: str) -> LightIssueCheckOutcome:
    policy = get_rule_policy(getattr(issue, "rule", ""))
    validator_name = str(policy.local_validator or "").strip()
    if not validator_name:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="No rule-policy validator is configured for this issue.",
        )
    validation_message = validate_rule_fix(
        validator_name=validator_name,
        issue_line=int(getattr(issue, "line", 0) or 0),
        file_content=file_content,
    )
    if validation_message:
        return LightIssueCheckOutcome(
            status="FAIL",
            summary="Local issue validator still reports the current Sonar pattern.",
            findings=(validation_message,),
        )
    return LightIssueCheckOutcome(
        status="PASS",
        summary="Rule-policy local validator passed for the current issue.",
    )


def _run_s3776_method_complexity(*, issue, file_content: str, validator_config: dict[str, Any]) -> LightIssueCheckOutcome:
    lines = file_content.splitlines()
    target_line = int(getattr(issue, "start_line", 0) or getattr(issue, "line", 0) or 0)
    method = QualityGateVerifier._find_enclosing_method(lines, target_line)
    if method is None:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Could not locate the target method for lightweight S3776 validation.",
        )
    body_text = "\n".join(lines[method.start_line - 1:method.end_line])
    complexity = QualityGateVerifier._estimate_cognitive_complexity(body_text)
    pass_threshold = int(validator_config.get("pass_complexity_threshold", 15) or 15)
    fail_threshold = int(validator_config.get("fail_complexity_threshold", 30) or 30)
    if complexity <= pass_threshold:
        return LightIssueCheckOutcome(
            status="PASS",
            summary=f"Estimated cognitive complexity for {method.name} is {complexity}, within the simple-loop pass threshold {pass_threshold}.",
            metrics={
                "method_name": method.name,
                "estimated_cognitive_complexity": complexity,
                "pass_threshold": pass_threshold,
                "fail_threshold": fail_threshold,
            },
        )
    return LightIssueCheckOutcome(
        status="UNKNOWN",
        summary=(
            f"Estimated cognitive complexity for {method.name} is {complexity}. "
            "The local estimator is heuristic-only in simple-loop mode, so final Sonar confirmation is still needed."
        ),
        metrics={
            "method_name": method.name,
            "estimated_cognitive_complexity": complexity,
            "pass_threshold": pass_threshold,
            "fail_threshold": fail_threshold,
        },
    )


def _run_s107_parameter_count(*, issue, file_content: str, validator_config: dict[str, Any]) -> LightIssueCheckOutcome:
    method, signature_parts = _find_target_method(issue=issue, file_content=file_content)
    if method is None or signature_parts is None:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Could not locate the target method for lightweight S107 validation.",
        )
    parameter_count = _count_signature_parameters(str(signature_parts.get("parameter_text", "") or ""))
    max_parameters = int(validator_config.get("max_parameters", 7) or 7)
    if parameter_count <= max_parameters:
        return LightIssueCheckOutcome(
            status="PASS",
            summary=f"Target method parameter count dropped to {parameter_count}, which is within the configured S107 threshold {max_parameters}.",
        )
    return LightIssueCheckOutcome(
        status="FAIL",
        summary=f"Target method still exposes {parameter_count} parameters, above the configured S107 threshold {max_parameters}.",
        findings=(method.signature,),
    )


def _run_s1172_unused_parameter(*, issue, file_content: str) -> LightIssueCheckOutcome:
    parameter_name = _extract_quoted_symbol(getattr(issue, "message", ""))
    if not parameter_name:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Could not extract the target parameter name from the S1172 issue message.",
        )

    method, signature_parts = _find_target_method(issue=issue, file_content=file_content)
    if method is None or signature_parts is None:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Could not locate the target method for lightweight S1172 validation.",
        )

    if not _parameter_is_still_declared(str(signature_parts.get("parameter_text", "") or ""), parameter_name):
        return LightIssueCheckOutcome(
            status="PASS",
            summary=f"Parameter '{parameter_name}' is no longer declared in the target method signature.",
        )

    usage_count = _count_identifier_usage_in_window(file_content=file_content, identifier=parameter_name, window=method)
    if usage_count > 1:
        return LightIssueCheckOutcome(
            status="PASS",
            summary=f"Parameter '{parameter_name}' is referenced inside the target method body.",
        )
    return LightIssueCheckOutcome(
        status="FAIL",
        summary=f"Parameter '{parameter_name}' still appears unused in the target method.",
        findings=(method.signature,),
    )


def _run_s1481_unused_local(*, issue, file_content: str) -> LightIssueCheckOutcome:
    variable_name = _extract_quoted_symbol(getattr(issue, "message", ""))
    if not variable_name:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Could not extract the target local-variable name from the S1481 issue message.",
        )

    method, _ = _find_target_method(issue=issue, file_content=file_content)
    usage_count = _count_identifier_usage_in_window(file_content=file_content, identifier=variable_name, window=method)
    if usage_count <= 0:
        return LightIssueCheckOutcome(
            status="PASS",
            summary=f"Local variable '{variable_name}' no longer exists in the target scope.",
        )
    if usage_count == 1:
        return LightIssueCheckOutcome(
            status="FAIL",
            summary=f"Local variable '{variable_name}' still looks unused in the target scope.",
        )
    return LightIssueCheckOutcome(
        status="PASS",
        summary=f"Local variable '{variable_name}' is now referenced in the target scope.",
    )


def _run_s1144_unused_private_member(*, issue, file_content: str) -> LightIssueCheckOutcome:
    member_name = _extract_quoted_symbol(getattr(issue, "message", ""))
    if not member_name:
        return LightIssueCheckOutcome(
            status="UNKNOWN",
            summary="Could not extract the target member name from the S1144 issue message.",
        )

    stripped = _strip_comments_and_literals(file_content)
    private_member_pattern = re.compile(
        rf"\bprivate\b[^\n{{;}}]*\b{re.escape(member_name)}\b\s*(?:\(|[;={{])"
    )
    if private_member_pattern.search(stripped):
        return LightIssueCheckOutcome(
            status="FAIL",
            summary=f"Private member '{member_name}' still exists after the patch.",
        )
    return LightIssueCheckOutcome(
        status="PASS",
        summary=f"Private member '{member_name}' no longer exists as a private declaration.",
    )


def _find_target_method(*, issue, file_content: str):
    lines = str(file_content or "").splitlines()
    target_line = int(getattr(issue, "start_line", 0) or getattr(issue, "line", 0) or 0)
    if target_line <= 0:
        return None, None
    method = QualityGateVerifier._find_enclosing_method(lines, target_line)
    if method is None:
        total_lines = len(lines)
        start = max(1, target_line - 5)
        end = min(total_lines, target_line + 5)
        for candidate_line in range(start, end + 1):
            method = QualityGateVerifier._build_method_window(lines, candidate_line)
            if method is not None:
                break
    if method is None:
        return None, None
    signature_parts = QualityGateVerifier._parse_method_signature(method.signature)
    return method, signature_parts


def _extract_quoted_symbol(message: str) -> str:
    match = _QUOTED_SYMBOL_PATTERN.search(str(message or ""))
    if match is None:
        return ""
    return str(match.group("name") or "").strip()


def _count_signature_parameters(parameter_text: str) -> int:
    entries = _split_top_level_segments(parameter_text)
    return len(entries)


def _parameter_is_still_declared(parameter_text: str, parameter_name: str) -> bool:
    for entry in _split_top_level_segments(parameter_text):
        if re.search(rf"\b{re.escape(parameter_name)}\b", entry):
            return True
    return False


def _split_top_level_segments(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    segments: list[str] = []
    current: list[str] = []
    angle = 0
    paren = 0
    bracket = 0
    brace = 0
    for char in normalized:
        if char == "<":
            angle += 1
        elif char == ">":
            angle = max(0, angle - 1)
        elif char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
        if char == "," and angle == 0 and paren == 0 and bracket == 0 and brace == 0:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _count_identifier_usage_in_window(*, file_content: str, identifier: str, window: Any | None) -> int:
    lines = str(file_content or "").splitlines()
    if window is None:
        text = str(file_content or "")
    else:
        start = max(1, int(getattr(window, "start_line", 0) or 0))
        end = min(len(lines), int(getattr(window, "end_line", 0) or 0))
        text = "\n".join(lines[start - 1:end]) if start and end and start <= end else str(file_content or "")
    stripped = _strip_comments_and_literals(text)
    return len(re.findall(rf"\b{re.escape(identifier)}\b", stripped))
