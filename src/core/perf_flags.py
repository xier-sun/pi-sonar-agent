"""Feature flags for gradual, no-regression performance optimizations."""

from __future__ import annotations

from dataclasses import dataclass

from pi_sonar_agent.core.project_env import read_project_env


def _env_flag(name: str, default: bool) -> bool:
    raw_value = read_project_env().get(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw_value = read_project_env().get(name)
    if raw_value is None:
        return default
    try:
        return max(1, int(str(raw_value).strip()))
    except Exception:
        return default


def _env_non_negative_int(name: str, default: int) -> int:
    raw_value = read_project_env().get(name)
    if raw_value is None:
        return default
    try:
        return max(0, int(str(raw_value).strip()))
    except Exception:
        return default


@dataclass(frozen=True)
class PerformanceFlags:
    """Execution flags for gradual performance improvements."""

    short_form_prompt: bool = True
    fast_path: bool = True
    plan_first_complex_rules: bool = True
    repair_archetype_constraint_injection: bool = True
    repair_archetype_strategy_selection: bool = True
    layered_verification: bool = True
    propagation_lifecycle: bool = True
    review_gate: bool = True
    fast_compile: bool = True
    patch_salvage: bool = True
    continuation_retry: bool = True
    edit_failure_context_feedback: bool = True
    fast_path_max_turns: int = 20
    continuation_retry_limit: int = 2
    git_clone_depth: int = 50

    def enabled_flags(self) -> tuple[str, ...]:
        flags: list[str] = []
        if self.short_form_prompt:
            flags.append("perf.short_form_prompt")
        if self.fast_path:
            flags.append("perf.fast_path")
        if self.plan_first_complex_rules:
            flags.append("perf.plan_first_complex_rules")
        if self.repair_archetype_constraint_injection:
            flags.append("planner.repair_archetypes.constraint_injection")
        if self.repair_archetype_strategy_selection:
            flags.append("planner.repair_archetypes.strategy_selection")
        if self.layered_verification:
            flags.append("perf.layered_verification")
        if self.propagation_lifecycle:
            flags.append("verifier.propagation_lifecycle")
        if self.review_gate:
            flags.append("verifier.review_gate")
        if self.fast_compile:
            flags.append("verifier.fast_compile")
        if self.patch_salvage:
            flags.append("perf.patch_salvage")
        if self.continuation_retry:
            flags.append("perf.continuation_retry")
        if self.edit_failure_context_feedback:
            flags.append("runtime.edit_failure_context_feedback")
        flags.append(f"perf.fast_path_max_turns={self.fast_path_max_turns}")
        flags.append(f"perf.continuation_retry_limit={self.continuation_retry_limit}")
        flags.append(f"perf.git_clone_depth={self.git_clone_depth}")
        return tuple(flags)


def load_performance_flags() -> PerformanceFlags:
    """Load rollout flags from environment with safe defaults."""

    return PerformanceFlags(
        short_form_prompt=_env_flag("PI_SONAR_PERF_SHORT_FORM_PROMPT", True),
        fast_path=_env_flag("PI_SONAR_PERF_FAST_PATH", True),
        plan_first_complex_rules=_env_flag("PI_SONAR_PERF_PLAN_FIRST_COMPLEX_RULES", True),
        repair_archetype_constraint_injection=_env_flag(
            "PI_SONAR_PLANNER_REPAIR_ARCHETYPE_CONSTRAINT_INJECTION",
            True,
        ),
        repair_archetype_strategy_selection=_env_flag(
            "PI_SONAR_PLANNER_REPAIR_ARCHETYPE_STRATEGY_SELECTION",
            True,
        ),
        layered_verification=_env_flag("PI_SONAR_PERF_LAYERED_VERIFICATION", True),
        propagation_lifecycle=_env_flag("PI_SONAR_VERIFIER_PROPAGATION_LIFECYCLE", True),
        review_gate=_env_flag("PI_SONAR_REVIEW_GATE_ENABLED", True),
        fast_compile=_env_flag("PI_SONAR_VERIFIER_FAST_COMPILE", True),
        patch_salvage=_env_flag("PI_SONAR_PERF_PATCH_SALVAGE", True),
        continuation_retry=_env_flag("PI_SONAR_PERF_CONTINUATION_RETRY", True),
        edit_failure_context_feedback=_env_flag(
            "PI_SONAR_RUNTIME_EDIT_FAILURE_CONTEXT_FEEDBACK",
            True,
        ),
        fast_path_max_turns=_env_int("PI_SONAR_PERF_FAST_PATH_MAX_TURNS", 20),
        continuation_retry_limit=_env_int("PI_SONAR_PERF_CONTINUATION_RETRY_LIMIT", 2),
        git_clone_depth=_env_non_negative_int("PI_SONAR_GIT_CLONE_DEPTH", 50),
    )
