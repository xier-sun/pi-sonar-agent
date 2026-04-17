"""Generic post-build blocker classification for headless simple-loop execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.light_check_catalog import (
    LightBlockerCategory,
    load_default_light_check_catalog,
)
from pi_sonar_agent.core.quality_gate import QualityGateResult, QualityGateViolation
from pi_sonar_agent.core.semantic_precheck import SemanticPrecheckFinding, SemanticPrecheckResult
from pi_sonar_agent.core.state import serialize_state

_COMPILER_ERROR_PATTERN = re.compile(r"\berror\s+CS\d+\b", re.IGNORECASE)


@dataclass(frozen=True)
class LightBlockerCheckOutcome:
    """Result of lightweight blocker classification."""

    status: str
    summary: str
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


def run_new_blocker_check(
    *,
    rule_id: str,
    semantic_precheck_result: SemanticPrecheckResult,
    quality_gate_result: QualityGateResult,
    build_passed: bool = True,
    build_output: str = "",
) -> LightBlockerCheckOutcome:
    """Classify post-build blockers using the unified light-check catalog."""

    catalog = load_default_light_check_catalog()
    profile = catalog.rule(rule_id)
    enabled_categories = tuple(profile.blocker_checks) if profile is not None and profile.blocker_checks else tuple(catalog.blocker_categories)

    blockers: list[str] = []
    for category_name in enabled_categories:
        category = catalog.blocker_categories.get(category_name)
        if category is None:
            continue
        blockers.extend(
            _collect_category_blockers(
                category=category,
                semantic_precheck_result=semantic_precheck_result,
                quality_gate_result=quality_gate_result,
                build_passed=build_passed,
                build_output=build_output,
            )
        )

    if blockers:
        return LightBlockerCheckOutcome(
            status="FAIL",
            summary="Post-build simple-loop check detected new hard blockers that must be fixed before moving to the next issue.",
            blockers=tuple(dict.fromkeys(blockers)),
        )
    return LightBlockerCheckOutcome(
        status="PASS",
        summary="Post-build simple-loop check found no new hard blockers.",
    )


def filter_semantic_findings(
    *,
    rule_id: str,
    findings: tuple[SemanticPrecheckFinding, ...] | list[SemanticPrecheckFinding],
) -> tuple[SemanticPrecheckFinding, ...]:
    """Keep only semantic findings that map to configured blocker categories."""

    allowed_ids = _allowed_semantic_ids(rule_id=rule_id)
    return tuple(
        finding
        for finding in tuple(findings or ())
        if str(getattr(finding, "finding_id", "")).strip() in allowed_ids
    )


def filter_quality_violations(
    *,
    rule_id: str,
    violations: tuple[QualityGateViolation, ...] | list[QualityGateViolation],
) -> tuple[QualityGateViolation, ...]:
    """Keep only quality-gate violations that map to configured blocker categories."""

    allowed_ids = _allowed_quality_rule_ids(rule_id=rule_id)
    return tuple(
        violation
        for violation in tuple(violations or ())
        if str(getattr(violation, "rule_id", "")).strip() in allowed_ids
    )


def _allowed_semantic_ids(*, rule_id: str) -> set[str]:
    catalog = load_default_light_check_catalog()
    category_names = _enabled_category_names(rule_id)
    ids: set[str] = set()
    for category_name in category_names:
        category = catalog.blocker_categories.get(category_name)
        if category is None:
            continue
        ids.update(str(item).strip() for item in category.semantic_finding_ids if str(item).strip())
    return ids


def _allowed_quality_rule_ids(*, rule_id: str) -> set[str]:
    catalog = load_default_light_check_catalog()
    category_names = _enabled_category_names(rule_id)
    ids: set[str] = set()
    for category_name in category_names:
        category = catalog.blocker_categories.get(category_name)
        if category is None:
            continue
        ids.update(str(item).strip() for item in category.quality_gate_rule_ids if str(item).strip())
    return ids


def _enabled_category_names(rule_id: str) -> tuple[str, ...]:
    catalog = load_default_light_check_catalog()
    profile = catalog.rule(rule_id)
    if profile is not None and profile.blocker_checks:
        return tuple(profile.blocker_checks)
    return tuple(catalog.blocker_categories)


def _collect_category_blockers(
    *,
    category: LightBlockerCategory,
    semantic_precheck_result: SemanticPrecheckResult,
    quality_gate_result: QualityGateResult,
    build_passed: bool,
    build_output: str,
) -> list[str]:
    blockers: list[str] = []
    if category.name == "compile_errors" and (not build_passed or _COMPILER_ERROR_PATTERN.search(str(build_output or ""))):
        blockers.append(f"[{category.name}] {category.title}: build output still contains compiler errors.")

    for finding in tuple(getattr(semantic_precheck_result, "findings", ()) or ()):
        if str(getattr(finding, "finding_id", "")).strip() not in set(category.semantic_finding_ids):
            continue
        message = str(getattr(finding, "message", "")).strip() or str(getattr(finding, "title", "")).strip()
        raw_id = str(getattr(finding, "finding_id", "")).strip()
        label = f"{category.name}:{raw_id}" if raw_id else category.name
        blockers.append(f"[{label}] {category.title}: {message}")

    for violation in tuple(getattr(quality_gate_result, "violations", ()) or ()):
        if str(getattr(violation, "rule_id", "")).strip() not in set(category.quality_gate_rule_ids):
            continue
        message = str(getattr(violation, "message", "")).strip() or str(getattr(violation, "title", "")).strip()
        raw_id = str(getattr(violation, "rule_id", "")).strip()
        label = f"{category.name}:{raw_id}" if raw_id else category.name
        blockers.append(f"[{label}] {category.title}: {message}")

    return blockers
