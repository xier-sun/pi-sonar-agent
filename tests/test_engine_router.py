from __future__ import annotations

from pi_sonar_agent.core.engine_router import route_engine_for_issue
from pi_sonar_agent.core.issue_contract import EditContract


def test_engine_router_skips_s107_when_roslyn_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "pi_sonar_agent.core.engine_router.inspect_roslyn_availability",
        lambda: (False, ("missing project file: fix_engine/AgentFixEngine.csproj",)),
    )

    decision = route_engine_for_issue(
        rule_id="csharpsquid:S107",
        edit_contract=EditContract(
            issue_key="ISSUE-S107",
            rule_id="csharpsquid:S107",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
    )

    assert decision.should_skip is True
    assert decision.requires_roslyn is True
    assert decision.resolved_engine == "skip"
    assert decision.fallback_allowed is False
    assert "S107 requires Roslyn engine" in decision.skip_reason


def test_engine_router_falls_back_for_non_pinned_roslyn_rule(monkeypatch) -> None:
    monkeypatch.setattr(
        "pi_sonar_agent.core.engine_router.inspect_roslyn_availability",
        lambda: (False, ("missing project file: fix_engine/AgentFixEngine.csproj",)),
    )

    decision = route_engine_for_issue(
        rule_id="csharpsquid:S4487",
        edit_contract=EditContract(
            issue_key="ISSUE-S4487",
            rule_id="csharpsquid:S4487",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
    )

    assert decision.should_skip is False
    assert decision.resolved_engine == "agent"
    assert decision.fallback_allowed is True
    assert "Roslyn unavailable" in decision.fallback_reason


def test_engine_router_keeps_s3776_on_agent(monkeypatch) -> None:
    monkeypatch.setattr(
        "pi_sonar_agent.core.engine_router.inspect_roslyn_availability",
        lambda: (False, ("missing project file: fix_engine/AgentFixEngine.csproj",)),
    )

    decision = route_engine_for_issue(
        rule_id="csharpsquid:S3776",
        edit_contract=EditContract(
            issue_key="ISSUE-S3776",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
    )

    assert decision.should_skip is False
    assert decision.resolved_engine == "agent"
    assert decision.requires_roslyn is False


def test_engine_router_uses_agent_fallback_for_s107_in_second_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        "pi_sonar_agent.core.engine_router.inspect_roslyn_availability",
        lambda: (True, ()),
    )

    decision = route_engine_for_issue(
        rule_id="csharpsquid:S107",
        edit_contract=EditContract(
            issue_key="ISSUE-S107",
            rule_id="csharpsquid:S107",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
        second_pass=True,
    )

    assert decision.should_skip is False
    assert decision.resolved_engine == "agent"
    assert decision.fallback_allowed is True
    assert "Second-pass S107 agent fallback enabled" in decision.fallback_reason
    assert decision.requires_roslyn is False
