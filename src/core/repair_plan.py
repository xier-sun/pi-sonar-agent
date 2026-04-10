"""Structured repair plans and precheck results for complex issue fixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class RepairHelperPlan:
    """One helper method the planner expects to introduce or rewrite."""

    name: str
    is_async: bool = False
    reason: str = ""
    await_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class RepairPropagationTarget:
    """One declaration or callsite that must be updated during signature propagation."""

    file: str
    symbol: str
    kind: str = ""
    reason: str = ""
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class RepairPlan:
    """Planner-authored shape for a complex rule fix before editing starts."""

    repair_shape: str
    primary_file: str = ""
    primary_method_name: str = ""
    proposed_method_name: str = ""
    target_symbols: tuple[str, ...] = ()
    new_helpers: tuple[str, ...] = ()
    helper_async_map: tuple[RepairHelperPlan, ...] = ()
    requires_signature_change: bool = False
    requires_propagation: bool = False
    requires_new_type: bool = False
    propagation_targets: tuple[RepairPropagationTarget, ...] = ()
    expected_boundary_capabilities: tuple[str, ...] = ()
    expected_quality_gates: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class PlanPrecheckResult:
    """Machine-readable edit precheck for a structured repair plan."""

    status: str = "not_applicable"
    blocking: bool = False
    code: str = ""
    summary: str = ""
    details: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)
