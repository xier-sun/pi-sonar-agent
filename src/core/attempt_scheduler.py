"""Central execution and verification scheduling for one issue attempt."""

from __future__ import annotations

from dataclasses import dataclass

from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.perf_flags import PerformanceFlags


@dataclass(frozen=True)
class AttemptExecutionSchedule:
    """Execution decisions for one model attempt."""

    execution_profile: str
    fast_path_enabled: bool
    plan_first_enabled: bool
    effective_max_turns: int
    enable_prefetch: bool
    enable_attempt_context_cache: bool
    patch_salvage_enabled: bool
    continuation_retry_enabled: bool
    continuation_retry_limit: int
    short_form_prompt_enabled: bool
    rollout_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_profile": self.execution_profile,
            "fast_path_enabled": self.fast_path_enabled,
            "plan_first_enabled": self.plan_first_enabled,
            "effective_max_turns": self.effective_max_turns,
            "enable_prefetch": self.enable_prefetch,
            "enable_attempt_context_cache": self.enable_attempt_context_cache,
            "patch_salvage_enabled": self.patch_salvage_enabled,
            "continuation_retry_enabled": self.continuation_retry_enabled,
            "continuation_retry_limit": self.continuation_retry_limit,
            "short_form_prompt_enabled": self.short_form_prompt_enabled,
            "rollout_flags": list(self.rollout_flags),
        }


@dataclass(frozen=True)
class VerificationSchedule:
    """Verification ordering and build-gating decisions."""

    run_boundary_first: bool
    run_quality_gate_before_build: bool
    run_rule_validation_before_build: bool
    skip_build_on_precheck_failure: bool
    rollout_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "run_boundary_first": self.run_boundary_first,
            "run_quality_gate_before_build": self.run_quality_gate_before_build,
            "run_rule_validation_before_build": self.run_rule_validation_before_build,
            "skip_build_on_precheck_failure": self.skip_build_on_precheck_failure,
            "rollout_flags": list(self.rollout_flags),
        }


class AttemptScheduler:
    """Centralize execution, verification, and timeout-recovery decisions."""

    @staticmethod
    def build_execution_schedule(
        *,
        edit_contract: EditContract,
        performance_flags: PerformanceFlags,
        default_max_turns: int,
    ) -> AttemptExecutionSchedule:
        fast_path_enabled = bool(getattr(edit_contract, "fast_path_enabled", False))
        effective_max_turns = default_max_turns
        if fast_path_enabled:
            effective_max_turns = min(default_max_turns, performance_flags.fast_path_max_turns)
        enable_prefetch = fast_path_enabled or bool(edit_contract.allowed_related_symbols)
        return AttemptExecutionSchedule(
            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
            fast_path_enabled=fast_path_enabled,
            plan_first_enabled=bool(getattr(edit_contract, "plan_first_enabled", False)),
            effective_max_turns=effective_max_turns,
            enable_prefetch=enable_prefetch,
            enable_attempt_context_cache=True,
            patch_salvage_enabled=performance_flags.patch_salvage,
            continuation_retry_enabled=performance_flags.continuation_retry,
            continuation_retry_limit=performance_flags.continuation_retry_limit,
            short_form_prompt_enabled=performance_flags.short_form_prompt,
            rollout_flags=performance_flags.enabled_flags(),
        )

    @staticmethod
    def build_verification_schedule(
        *,
        edit_contract: EditContract,
        performance_flags: PerformanceFlags,
    ) -> VerificationSchedule:
        return VerificationSchedule(
            run_boundary_first=True,
            run_quality_gate_before_build=True,
            run_rule_validation_before_build=True,
            skip_build_on_precheck_failure=performance_flags.layered_verification,
            rollout_flags=performance_flags.enabled_flags(),
        )

    @staticmethod
    def should_salvage_timeout(
        *,
        schedule: AttemptExecutionSchedule,
        changes_detected: bool,
        used_forbidden_tool: bool,
        build_tool_failed: bool,
    ) -> bool:
        if not schedule.patch_salvage_enabled:
            return False
        if not changes_detected:
            return False
        return not (used_forbidden_tool or build_tool_failed)

    @staticmethod
    def should_continue_after_timeout(
        *,
        schedule: AttemptExecutionSchedule,
        timeout_stage: str,
        continuation_count: int,
        changes_detected: bool,
        used_forbidden_tool: bool,
        build_tool_failed: bool,
    ) -> bool:
        if not schedule.continuation_retry_enabled:
            return False
        if continuation_count >= schedule.continuation_retry_limit:
            return False
        if changes_detected or used_forbidden_tool or build_tool_failed:
            return False
        return str(timeout_stage or "").strip() in {
            "follow_up_response_timeout",
            "post_read_stall",
            "post_edit_stall",
            "post_summary_stall",
            "post_text_stall",
        }
