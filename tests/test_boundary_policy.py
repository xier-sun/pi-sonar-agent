from __future__ import annotations

from pi_sonar_agent.core.boundary_capabilities import (
    ADJACENT_CLEANUP_CAPABILITY,
    DECLARATION_DELETE_CAPABILITY,
)
from pi_sonar_agent.core.boundary_policy import BoundaryPolicy
from pi_sonar_agent.core.issue_contract import EditContract


def test_boundary_policy_normalizes_contract_capabilities() -> None:
    contract = EditContract(
        issue_key="ISSUE-1",
        rule_id="csharpsquid:S1481",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        allowed_capabilities=(
            DECLARATION_DELETE_CAPABILITY,
            ADJACENT_CLEANUP_CAPABILITY,
            DECLARATION_DELETE_CAPABILITY,
        ),
    )

    assert BoundaryPolicy.contract_capabilities(contract) == (
        DECLARATION_DELETE_CAPABILITY,
        ADJACENT_CLEANUP_CAPABILITY,
    )
