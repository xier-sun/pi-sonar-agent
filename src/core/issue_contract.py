"""Structured single-issue edit contracts used by guardrail review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.lessons_store import PlannerLesson
from pi_sonar_agent.core.quality_gate import QualityGateRule
from pi_sonar_agent.core.repo_capability import RepoCapabilityProfile
from pi_sonar_agent.core.repair_plan import PlanPrecheckResult, RepairPlan
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
class ContractContextSnippet:
    """Prefetched context snippet prepared before the model starts editing."""

    file: str
    label: str
    reason: str = ""
    start_line: int = 0
    end_line: int = 0
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snippet to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class EditContract:
    """Structured edit boundary for a single Sonar issue attempt."""

    issue_key: str
    rule_id: str
    guardrail_mode: str
    target_files: tuple[str, ...]
    target_symbols: tuple[ContractTargetSymbol, ...] = ()
    allowed_related_symbols: tuple[ContractTargetSymbol, ...] = ()
    boundary_profile: str = ""
    allowed_capabilities: tuple[str, ...] = ()
    allowed_change_kinds: tuple[str, ...] = ()
    forbidden_change_kinds: tuple[str, ...] = ()
    validation_plan: tuple[str, ...] = ()
    follow_up_policy: str = "record_only"
    review_hints: tuple[str, ...] = ()
    quality_gate_rules: tuple[QualityGateRule, ...] = ()
    planner_lessons: tuple[PlannerLesson, ...] = ()
    prefetched_context: tuple[ContractContextSnippet, ...] = ()
    execution_profile: str = "full_path"
    fast_path_enabled: bool = False
    plan_first_enabled: bool = False
    rollout_flags: tuple[str, ...] = ()
    scope_mode: str = ""
    target_line_range: tuple[int, int] = ()
    validation_line_range: tuple[int, int] = ()
    allowed_line_ranges: tuple[tuple[int, int], ...] = ()
    allow_file_creation: bool = False
    allowed_new_file_roots: tuple[str, ...] = ()
    repo_capability: RepoCapabilityProfile | None = None
    repo_capability_summary: str = ""
    repo_capability_hints: tuple[str, ...] = ()
    repair_plan: RepairPlan | None = None
    plan_precheck: PlanPrecheckResult | None = None
    patch_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize the edit contract to a JSON-ready dictionary."""

        return serialize_state(self)
