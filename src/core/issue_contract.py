"""Structured single-issue edit contracts used by guardrail review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.quality_gate import QualityGateRule
from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class ContractTargetSymbol:
    """Named code region that the current issue is allowed to touch."""

    file: str
    symbol: str
    reason: str = ""
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the symbol to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class EditContract:
    """Structured edit boundary for a single Sonar issue attempt."""

    issue_key: str
    rule_id: str
    guardrail_mode: str
    target_files: tuple[str, ...]
    target_symbols: tuple[ContractTargetSymbol, ...] = ()
    allowed_change_kinds: tuple[str, ...] = ()
    forbidden_change_kinds: tuple[str, ...] = ()
    validation_plan: tuple[str, ...] = ()
    follow_up_policy: str = "record_only"
    review_hints: tuple[str, ...] = ()
    quality_gate_rules: tuple[QualityGateRule, ...] = ()
    scope_mode: str = ""
    target_line_range: tuple[int, int] = ()
    validation_line_range: tuple[int, int] = ()
    allowed_line_ranges: tuple[tuple[int, int], ...] = ()
    patch_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize the edit contract to a JSON-ready dictionary."""

        return serialize_state(self)
