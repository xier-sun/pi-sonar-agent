"""Structured C# quality-gate catalog and verification payloads."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.resource_loader import DEFAULT_CSHARP_QUALITY_GATE_FILE, ResourceLoader
from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class QualityGateRule:
    """Single structured quality-gate rule."""

    rule_id: str
    title: str
    summary: str
    enforcement: str = "hard"
    validation_scope: str = "changed_region"
    prompt_hint: str = ""
    retry_hint: str = ""
    file_extensions: tuple[str, ...] = (".cs",)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QualityGateRule:
        """Build a quality-gate rule from JSON data."""

        return cls(
            rule_id=str(payload.get("rule_id", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            enforcement=str(payload.get("enforcement", "hard")).strip().lower() or "hard",
            validation_scope=str(payload.get("validation_scope", "changed_region")).strip(),
            prompt_hint=str(payload.get("prompt_hint", "")).strip(),
            retry_hint=str(payload.get("retry_hint", "")).strip(),
            file_extensions=tuple(
                str(item).strip().lower()
                for item in payload.get("file_extensions", [".cs"])
                if str(item).strip()
            )
            or (".cs",),
        )

    def applies_to(self, file_path: str) -> bool:
        """Return True when the rule should apply to the given file path."""

        normalized = str(file_path or "").lower()
        return any(normalized.endswith(extension) for extension in self.file_extensions)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the rule to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateCatalog:
    """Structured catalog loaded from the project quality-gate markdown file."""

    version: int
    source_path: str
    body_markdown: str
    rules: tuple[QualityGateRule, ...]

    @classmethod
    def load_from_file(cls, path: Path) -> QualityGateCatalog:
        """Load the quality-gate catalog from a markdown file with JSON front matter."""

        _, metadata, body = ResourceLoader.load_json_front_matter((path,))
        rule_payloads = metadata.get("rules", []) if isinstance(metadata, dict) else []
        rules = tuple(
            QualityGateRule.from_dict(item)
            for item in rule_payloads
            if isinstance(item, dict) and str(item.get("rule_id", "")).strip()
        )
        version = int(metadata.get("version", 1)) if isinstance(metadata, dict) else 1
        return cls(
            version=version,
            source_path=path.as_posix(),
            body_markdown=body.strip(),
            rules=rules,
        )

    def rules_for_path(
        self,
        file_path: str,
        *,
        enforcement: str | None = None,
    ) -> tuple[QualityGateRule, ...]:
        """Return the rules that apply to the current file."""

        normalized_enforcement = str(enforcement or "").strip().lower()
        selected = []
        for rule in self.rules:
            if not rule.applies_to(file_path):
                continue
            if normalized_enforcement and rule.enforcement != normalized_enforcement:
                continue
            selected.append(rule)
        return tuple(selected)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the catalog to a JSON-ready dictionary."""

        return serialize_state(self)


@lru_cache(maxsize=1)
def load_default_quality_gate_catalog() -> QualityGateCatalog:
    """Load the repository-default C# quality gate catalog."""

    return QualityGateCatalog.load_from_file(DEFAULT_CSHARP_QUALITY_GATE_FILE)


@dataclass(frozen=True)
class QualityGateViolation:
    """Hard quality-gate violation that should trigger a retry."""

    rule_id: str
    title: str
    message: str
    file: str
    line: int = 0
    symbol: str = ""
    evidence: str = ""
    retry_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateSoftFinding:
    """Soft quality-gate finding kept for reviewer visibility."""

    rule_id: str
    title: str
    message: str
    file: str
    line: int = 0
    symbol: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateResult:
    """Verifier result for the current issue attempt."""

    status: str
    summary: str
    applied_rule_ids: tuple[str, ...] = ()
    violations: tuple[QualityGateViolation, ...] = ()
    soft_findings: tuple[QualityGateSoftFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    def to_retry_message(self) -> str:
        """Render hard quality-gate failures into retry guidance."""

        if self.status != "retry" or not self.violations:
            return ""

        lines = [
            "Quality gate verification failed. The patch must satisfy the active C# gate rules before it can pass:",
        ]
        for index, violation in enumerate(self.violations, start=1):
            detail = f"{index}. [{violation.rule_id}] {violation.title}: {violation.message}"
            if violation.file:
                location = violation.file
                if violation.line > 0:
                    location = f"{location}:{violation.line}"
                detail += f" | location: {location}"
            lines.append(detail)
            if violation.evidence:
                lines.append(f"   Evidence: {violation.evidence}")
            if violation.retry_hint:
                lines.append(f"   Retry Hint: {violation.retry_hint}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ComplianceCheck:
    """Normalized outcome for one active quality-gate rule."""

    rule_id: str
    title: str
    enforcement: str
    status: str
    summary: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ComplianceSummary:
    """Structured compliance summary for one issue attempt."""

    status: str
    summary: str
    source_path: str = ""
    hard_rule_count: int = 0
    soft_rule_count: int = 0
    passed_rule_count: int = 0
    failed_rule_count: int = 0
    soft_finding_count: int = 0
    not_applicable_count: int = 0
    checks: tuple[ComplianceCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


def render_quality_gate_prompt(
    rules: tuple[QualityGateRule, ...] | list[QualityGateRule],
    *,
    source_path: str = "",
) -> str:
    """Render a compact prompt section for the active quality-gate rules only."""

    active_rules = tuple(rules)
    if not active_rules:
        return ""

    lines = ["本次修复只需遵守下面这些已启用的质量门禁规则："]
    for rule in active_rules:
        label = "强制" if rule.enforcement == "hard" else "提示"
        detail = f"- [{label}] {rule.rule_id} {rule.title}: {rule.summary}"
        if rule.prompt_hint:
            detail += f" | 当前要求: {rule.prompt_hint}"
        lines.append(detail)
    if source_path:
        lines.append(f"- 规则来源: {source_path}")
    return "\n".join(lines)


def build_compliance_summary(
    rules: tuple[QualityGateRule, ...] | list[QualityGateRule],
    quality_gate_result: QualityGateResult | dict[str, Any] | None,
    *,
    source_path: str = "",
) -> ComplianceSummary:
    """Build a normalized compliance summary from active rules and verifier output."""

    active_rules = tuple(rules)
    if not active_rules:
        return ComplianceSummary(
            status="not_applicable",
            summary="No active quality gates were declared for this attempt.",
            source_path=source_path,
        )

    payload: dict[str, Any]
    if isinstance(quality_gate_result, QualityGateResult):
        payload = quality_gate_result.to_dict()
    elif isinstance(quality_gate_result, dict):
        payload = quality_gate_result
    else:
        payload = {}

    applied_rule_ids = {
        str(item).strip()
        for item in payload.get("applied_rule_ids", [])
        if str(item).strip()
    }
    violations_by_id = {
        str(item.get("rule_id", "")).strip(): item
        for item in payload.get("violations", [])
        if isinstance(item, dict) and str(item.get("rule_id", "")).strip()
    }
    soft_findings_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in payload.get("soft_findings", []):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id", "")).strip()
        if not rule_id:
            continue
        soft_findings_by_id.setdefault(rule_id, []).append(item)

    checks: list[ComplianceCheck] = []
    passed_rule_count = 0
    failed_rule_count = 0
    soft_finding_count = 0
    not_applicable_count = 0

    for rule in active_rules:
        if applied_rule_ids and rule.rule_id not in applied_rule_ids:
            not_applicable_count += 1
            checks.append(
                ComplianceCheck(
                    rule_id=rule.rule_id,
                    title=rule.title,
                    enforcement=rule.enforcement,
                    status="not_applicable",
                    summary=rule.summary,
                    message="This rule was declared but not applied to the final verification context.",
                )
            )
            continue

        violation = violations_by_id.get(rule.rule_id)
        if violation is not None:
            failed_rule_count += 1
            checks.append(
                ComplianceCheck(
                    rule_id=rule.rule_id,
                    title=rule.title,
                    enforcement=rule.enforcement,
                    status="failed",
                    summary=rule.summary,
                    message=str(violation.get("message", "")).strip(),
                )
            )
            continue

        soft_items = soft_findings_by_id.get(rule.rule_id, [])
        if soft_items:
            soft_finding_count += len(soft_items)
            checks.append(
                ComplianceCheck(
                    rule_id=rule.rule_id,
                    title=rule.title,
                    enforcement=rule.enforcement,
                    status="soft_finding",
                    summary=rule.summary,
                    message=str(soft_items[0].get("message", "")).strip(),
                )
            )
            continue

        passed_rule_count += 1
        checks.append(
            ComplianceCheck(
                rule_id=rule.rule_id,
                title=rule.title,
                enforcement=rule.enforcement,
                status="passed",
                summary=rule.summary,
            )
        )

    overall_status = "retry" if failed_rule_count > 0 else "pass"
    result_summary = str(payload.get("summary", "")).strip()
    if not result_summary:
        if overall_status == "retry":
            result_summary = f"Compliance failed with {failed_rule_count} hard rule violation(s)."
        else:
            result_summary = "All active quality gates passed for this attempt."

    return ComplianceSummary(
        status=overall_status,
        summary=result_summary,
        source_path=source_path,
        hard_rule_count=sum(1 for rule in active_rules if rule.enforcement == "hard"),
        soft_rule_count=sum(1 for rule in active_rules if rule.enforcement != "hard"),
        passed_rule_count=passed_rule_count,
        failed_rule_count=failed_rule_count,
        soft_finding_count=soft_finding_count,
        not_applicable_count=not_applicable_count,
        checks=tuple(checks),
    )
