from __future__ import annotations

from pi_sonar_agent.core.state import TargetStatus, derive_target_status


def test_derive_target_status_marks_zero_issue_target_failure_as_failed() -> None:
    status = derive_target_status(
        total_issues=0,
        successful=0,
        skipped=0,
        failed=1,
        build_passed=False,
    )

    assert status == TargetStatus.FAILED
