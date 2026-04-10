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


@dataclass(frozen=True)
class PerformanceFlags:
    """Execution flags for gradual performance improvements."""

    short_form_prompt: bool = True
    fast_path: bool = True
    plan_first_complex_rules: bool = True
    layered_verification: bool = True
    patch_salvage: bool = True
    continuation_retry: bool = True
    fast_path_max_turns: int = 6
    continuation_retry_limit: int = 2

    def enabled_flags(self) -> tuple[str, ...]:
        flags: list[str] = []
        if self.short_form_prompt:
            flags.append("perf.short_form_prompt")
        if self.fast_path:
            flags.append("perf.fast_path")
        if self.plan_first_complex_rules:
            flags.append("perf.plan_first_complex_rules")
        if self.layered_verification:
            flags.append("perf.layered_verification")
        if self.patch_salvage:
            flags.append("perf.patch_salvage")
        if self.continuation_retry:
            flags.append("perf.continuation_retry")
        flags.append(f"perf.fast_path_max_turns={self.fast_path_max_turns}")
        flags.append(f"perf.continuation_retry_limit={self.continuation_retry_limit}")
        return tuple(flags)


def load_performance_flags() -> PerformanceFlags:
    """Load rollout flags from environment with safe defaults."""

    return PerformanceFlags(
        short_form_prompt=_env_flag("PI_SONAR_PERF_SHORT_FORM_PROMPT", True),
        fast_path=_env_flag("PI_SONAR_PERF_FAST_PATH", True),
        plan_first_complex_rules=_env_flag("PI_SONAR_PERF_PLAN_FIRST_COMPLEX_RULES", True),
        layered_verification=_env_flag("PI_SONAR_PERF_LAYERED_VERIFICATION", True),
        patch_salvage=_env_flag("PI_SONAR_PERF_PATCH_SALVAGE", True),
        continuation_retry=_env_flag("PI_SONAR_PERF_CONTINUATION_RETRY", True),
        fast_path_max_turns=_env_int("PI_SONAR_PERF_FAST_PATH_MAX_TURNS", 6),
        continuation_retry_limit=_env_int("PI_SONAR_PERF_CONTINUATION_RETRY_LIMIT", 2),
    )
