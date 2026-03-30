"""Issue classifier for determining fix complexity."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pi_sonar_agent.fixers.rule_profiles import RuleProfile


class ComplexityLevel(str, Enum):
    """Complexity level of an issue."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    ARCHITECTURAL = "architectural"

    @classmethod
    def from_value(cls, value: str | None) -> "ComplexityLevel | None":
        """Create from value string."""
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None

        alias_map = {
            "simple": cls.SIMPLE,
            "moderate": cls.MODERATE,
            "complex": cls.COMPLEX,
            "architectural": cls.ARCHITECTURAL,
            "arch": cls.ARCHITECTURAL,
        }
        return alias_map.get(normalized)


# Rule-specific complexity overrides
RULE_COMPLEXITY_OVERRIDES: dict[str, ComplexityLevel] = {
    "csharpsquid:S4487": ComplexityLevel.SIMPLE,
    "csharpsquid:S1066": ComplexityLevel.SIMPLE,
    "csharpsquid:S1871": ComplexityLevel.SIMPLE,
    "external_roslyn:CS9236": ComplexityLevel.SIMPLE,
    "csharpsquid:S3267": ComplexityLevel.MODERATE,
    "csharpsquid:S6562": ComplexityLevel.MODERATE,
    "csharpsquid:S6561": ComplexityLevel.MODERATE,
    "csharpsquid:S4136": ComplexityLevel.MODERATE,
    "csharpsquid:S2325": ComplexityLevel.MODERATE,
    "csharpsquid:S3776": ComplexityLevel.MODERATE,
    "csharpsquid:S107": ComplexityLevel.COMPLEX,
    "csharpsquid:S1172": ComplexityLevel.COMPLEX,
    "csharpsquid:S6960": ComplexityLevel.ARCHITECTURAL,
}

# Fallback complexity based on risk level
RISK_COMPLEXITY_FALLBACK: dict[str, ComplexityLevel] = {
    "low": ComplexityLevel.SIMPLE,
    "medium": ComplexityLevel.MODERATE,
    "high": ComplexityLevel.COMPLEX,
}


class IssueClassifier:
    """Classifier for determining issue complexity and appropriate fix strategy."""

    def classify(self, issue: dict[str, Any], rule_profile: RuleProfile) -> ComplexityLevel:
        """Classify an issue based on rule profile and issue details."""
        # Check if rule profile specifies complexity
        configured_level = ComplexityLevel.from_value(rule_profile.complexity_level)
        if configured_level is not None:
            return configured_level

        # Check rule-specific override
        rule_id = str(issue.get("rule") or rule_profile.rule_id).strip()
        if rule_id in RULE_COMPLEXITY_OVERRIDES:
            return RULE_COMPLEXITY_OVERRIDES[rule_id]

        # Fallback based on risk
        return RISK_COMPLEXITY_FALLBACK.get(
            rule_profile.risk.strip().lower(),
            ComplexityLevel.SIMPLE,
        )

    def should_use_agent(self, complexity: ComplexityLevel) -> bool:
        """Determine if Agent should be used based on complexity."""
        return complexity in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX, ComplexityLevel.ARCHITECTURAL)

    def should_use_llm(self, complexity: ComplexityLevel) -> bool:
        """Determine if LLM should be used based on complexity."""
        return complexity in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)

    def should_use_deterministic(self, complexity: ComplexityLevel) -> bool:
        """Determine if deterministic fix should be used based on complexity."""
        return complexity == ComplexityLevel.SIMPLE