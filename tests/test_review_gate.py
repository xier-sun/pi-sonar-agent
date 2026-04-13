from __future__ import annotations

from pi_sonar_agent.core.propagation_verifier import PropagationCheckResult
from pi_sonar_agent.core.quality_gate import QualityGateResult, QualityGateViolation
from pi_sonar_agent.core.review_gate import (
    ReviewGateAgent,
    ReviewGateDecision,
    ReviewGateFinding,
    ReviewGateResult,
)


def test_review_gate_agent_env_overrides_base_url_and_auth_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "pi_sonar_agent.core.review_gate.build_agent_env",
        lambda: {
            "ANTHROPIC_BASE_URL": "https://main.example.com/api/anthropic",
            "ANTHROPIC_API_KEY": "main-key",
            "ANTHROPIC_AUTH_TOKEN": "",
        },
    )

    env = ReviewGateAgent._build_review_gate_agent_env(
        {
            "PI_SONAR_REVIEW_GATE_BASE_URL": "https://review.example.com/api/coding",
            "PI_SONAR_REVIEW_GATE_AUTH_TOKEN": "review-token",
        }
    )

    assert env["ANTHROPIC_BASE_URL"] == "https://review.example.com/api/coding"
    assert env["ANTHROPIC_API_KEY"] == "review-token"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_review_gate_agent_env_overrides_api_key_for_official_host(monkeypatch) -> None:
    monkeypatch.setattr(
        "pi_sonar_agent.core.review_gate.build_agent_env",
        lambda: {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_API_KEY": "main-key",
            "ANTHROPIC_AUTH_TOKEN": "",
        },
    )

    env = ReviewGateAgent._build_review_gate_agent_env(
        {
            "PI_SONAR_REVIEW_GATE_BASE_URL": "https://api.anthropic.com",
            "PI_SONAR_REVIEW_GATE_API_KEY": "review-key",
        }
    )

    assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert env["ANTHROPIC_API_KEY"] == "review-key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_review_gate_apply_waivers_turns_reviewable_blockers_into_pass() -> None:
    propagation_result = PropagationCheckResult(
        status="retry",
        summary="Propagation still looks stale.",
        residual_targets=("src/Foo.cs:1-3 (callsite) still missing `FooAsync`",),
    )
    quality_gate_result = QualityGateResult(
        status="retry",
        summary="Quality gate has reviewable blockers.",
        applied_rule_ids=("cognitive_complexity",),
        violations=(
            QualityGateViolation(
                rule_id="cognitive_complexity",
                title="认知复杂度",
                message="复杂度仍然偏高。",
                file="src/Foo.cs",
                line=10,
            ),
        ),
    )
    review_gate_result = ReviewGateResult(
        status="pass",
        summary="Review gate waived all ambiguous blockers.",
        findings=(
            ReviewGateFinding(
                finding_id="propagation",
                source="propagation",
                title="Signature propagation verification",
                message="Propagation still looks stale.",
            ),
            ReviewGateFinding(
                finding_id="quality_gate:cognitive_complexity:src/Foo.cs:10:1",
                source="quality_gate",
                title="认知复杂度",
                message="复杂度仍然偏高。",
            ),
        ),
        decisions=(
            ReviewGateDecision(
                finding_id="propagation",
                decision="waive",
                reason="Wrapper declaration was classified too broadly.",
            ),
            ReviewGateDecision(
                finding_id="quality_gate:cognitive_complexity:src/Foo.cs:10:1",
                decision="waive",
                reason="Current patch already reduced the targeted branch nesting.",
            ),
        ),
    )

    effective_propagation, effective_quality_gate = ReviewGateAgent.apply_waivers(
        propagation_check_result=propagation_result,
        quality_gate_result=quality_gate_result,
        review_gate_result=review_gate_result,
    )

    assert effective_propagation.status == "pass"
    assert effective_quality_gate.status == "pass"
    assert effective_quality_gate.violations == ()


def test_review_gate_result_builder_tolerates_null_decisions_and_feedback() -> None:
    findings = (
        ReviewGateFinding(
            finding_id="propagation",
            source="propagation",
            title="Signature propagation verification",
            message="Propagation still looks stale.",
        ),
    )

    result = ReviewGateAgent._build_result_from_payload(
        findings=findings,
        model_display="kimi-k2.5",
        raw_response='{"overall_decision":"retry","summary":"need retry","decisions":null,"feedback":null}',
        payload={
            "overall_decision": "retry",
            "summary": "need retry",
            "decisions": None,
            "feedback": None,
        },
    )

    assert result.status == "retry"
    assert result.invoked is True
    assert result.feedback == ()
    assert result.decisions == (
        ReviewGateDecision(
            finding_id="propagation",
            decision="confirm",
            reason="Review agent did not explicitly waive this finding.",
        ),
    )


def test_review_gate_unavailable_result_is_not_applicable() -> None:
    findings = (
        ReviewGateFinding(
            finding_id="quality_gate:cognitive_complexity:src/Foo.cs:10:1",
            source="quality_gate",
            title="认知复杂度",
            message="复杂度仍然偏高。",
        ),
    )

    result = ReviewGateAgent._build_unavailable_result(
        findings=findings,
        model_display="kimi-k2.5",
        summary="Review gate session returned an agent error; fell back to deterministic verifier blockers.",
        error="selected model is unavailable",
    )

    assert result.status == "not_applicable"
    assert result.invoked is True
    assert result.findings == findings
    assert result.decisions == ()
    assert "fell back to deterministic verifier blockers" in result.summary
