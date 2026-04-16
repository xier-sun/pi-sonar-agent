"""Runtime engine routing for issue fixes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.state import serialize_state
from pi_sonar_agent.fixers.roslyn import inspect_roslyn_availability, supports_roslyn_rule
from pi_sonar_agent.fixers.rule_profiles import load_rule_catalog


@dataclass(frozen=True)
class EngineRoutingDecision:
    """Resolved engine decision for a single issue attempt."""

    primary_engine: str
    resolved_engine: str
    fallback_allowed: bool
    fallback_reason: str
    skip_reason: str
    requires_roslyn: bool
    capability_blockers: tuple[str, ...] = ()

    @property
    def should_skip(self) -> bool:
        return self.resolved_engine == "skip" or bool(self.skip_reason)

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


def route_engine_for_issue(
    *,
    rule_id: str,
    edit_contract: EditContract | None = None,
) -> EngineRoutingDecision:
    """Resolve which engine should be used for the current rule at runtime."""

    profile = load_rule_catalog().get(rule_id)
    primary_engine = str(getattr(profile, "primary_engine", "agent") or "agent").strip() or "agent"
    fallback_engines = tuple(getattr(profile, "fallback_engines", ()) or ())
    repo_capability = getattr(edit_contract, "repo_capability", None)
    capability_blockers = (
        tuple(getattr(repo_capability, "unsupported_language_features", lambda: ())() or ())
        if repo_capability is not None
        else ()
    )

    roslyn_available, roslyn_reasons = inspect_roslyn_availability()
    roslyn_reason_text = "; ".join(roslyn_reasons)

    if str(rule_id or "").strip() == "csharpsquid:S107":
        if not roslyn_available:
            skip_reason = "S107 requires Roslyn engine, but it is unavailable."
            if roslyn_reason_text:
                skip_reason = f"{skip_reason} {roslyn_reason_text}"
            return EngineRoutingDecision(
                primary_engine=primary_engine,
                resolved_engine="skip",
                fallback_allowed=False,
                fallback_reason="S107 agent fallback disabled until Roslyn solution engine is available.",
                skip_reason=skip_reason,
                requires_roslyn=True,
                capability_blockers=capability_blockers,
            )
        return EngineRoutingDecision(
            primary_engine=primary_engine,
            resolved_engine="roslyn",
            fallback_allowed=False,
            fallback_reason="S107 remains pinned to Roslyn for solution-scope safety.",
            skip_reason="",
            requires_roslyn=True,
            capability_blockers=capability_blockers,
        )

    if primary_engine != "roslyn":
        return EngineRoutingDecision(
            primary_engine=primary_engine,
            resolved_engine=primary_engine,
            fallback_allowed=bool(fallback_engines),
            fallback_reason="",
            skip_reason="",
            requires_roslyn=False,
            capability_blockers=capability_blockers,
        )

    if not supports_roslyn_rule(rule_id):
        fallback_reason = "Roslyn execution path is not implemented for this rule yet; falling back to the existing runtime path."
        if not roslyn_available and roslyn_reason_text:
            fallback_reason = f"Roslyn unavailable: {roslyn_reason_text}. {fallback_reason}"
        fallback_engine = str((fallback_engines[0] if fallback_engines else "agent") or "agent").strip() or "agent"
        return EngineRoutingDecision(
            primary_engine=primary_engine,
            resolved_engine=fallback_engine,
            fallback_allowed=True,
            fallback_reason=fallback_reason,
            skip_reason="",
            requires_roslyn=False,
            capability_blockers=capability_blockers,
        )

    if roslyn_available:
        return EngineRoutingDecision(
            primary_engine=primary_engine,
            resolved_engine="roslyn",
            fallback_allowed=bool(fallback_engines),
            fallback_reason="",
            skip_reason="",
            requires_roslyn=False,
            capability_blockers=capability_blockers,
        )

    if fallback_engines:
        fallback_engine = str(fallback_engines[0]).strip()
        fallback_reason = f"Roslyn unavailable: {roslyn_reason_text}".strip()
        return EngineRoutingDecision(
            primary_engine=primary_engine,
            resolved_engine=fallback_engine,
            fallback_allowed=True,
            fallback_reason=fallback_reason,
            skip_reason="",
            requires_roslyn=False,
            capability_blockers=capability_blockers,
        )

    skip_reason = "Primary Roslyn engine is unavailable and no fallback engine is configured."
    if roslyn_reason_text:
        skip_reason = f"{skip_reason} {roslyn_reason_text}"
    return EngineRoutingDecision(
        primary_engine=primary_engine,
        resolved_engine="skip",
        fallback_allowed=False,
        fallback_reason="",
        skip_reason=skip_reason,
        requires_roslyn=False,
        capability_blockers=capability_blockers,
    )
