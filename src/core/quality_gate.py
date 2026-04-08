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
