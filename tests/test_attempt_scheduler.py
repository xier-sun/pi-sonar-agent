from __future__ import annotations

from pi_sonar_agent.core.attempt_scheduler import AttemptScheduler
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.perf_flags import PerformanceFlags


def test_attempt_scheduler_builds_fast_path_execution_schedule() -> None:
    schedule = AttemptScheduler.build_execution_schedule(
        edit_contract=EditContract(
            issue_key="ISSUE-1",
            rule_id="csharpsquid:S1481",
            guardrail_mode="scope",
            target_files=("src/Foo.cs",),
            execution_profile="fast_path_short_form",
            fast_path_enabled=True,
        ),
        performance_flags=PerformanceFlags(fast_path_max_turns=4),
        default_max_turns=10,
    )

    assert schedule.fast_path_enabled is True
    assert schedule.effective_max_turns == 4
    assert schedule.enable_prefetch is True
    assert schedule.enable_attempt_context_cache is True


def test_attempt_scheduler_builds_layered_verification_schedule() -> None:
    schedule = AttemptScheduler.build_verification_schedule(
        edit_contract=EditContract(
            issue_key="ISSUE-2",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
        performance_flags=PerformanceFlags(layered_verification=True),
    )

    assert schedule.run_boundary_first is True
    assert schedule.run_semantic_precheck_before_build is True
    assert schedule.run_propagation_check_before_build is True
    assert schedule.run_quality_gate_before_build is True
    assert schedule.run_rule_validation_before_build is True
    assert schedule.run_fast_compile_before_build is True
    assert schedule.skip_build_on_precheck_failure is True


def test_attempt_scheduler_builds_simple_loop_verification_schedule() -> None:
    schedule = AttemptScheduler.build_verification_schedule(
        edit_contract=EditContract(
            issue_key="ISSUE-3",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
            execution_mode="simple_loop",
        ),
        performance_flags=PerformanceFlags(layered_verification=True, fast_compile=True),
    )

    assert schedule.run_boundary_first is True
    assert schedule.run_semantic_precheck_before_build is False
    assert schedule.run_propagation_check_before_build is False
    assert schedule.run_quality_gate_before_build is False
    assert schedule.run_rule_validation_before_build is False
    assert schedule.run_fast_compile_before_build is False
    assert schedule.skip_build_on_precheck_failure is False
