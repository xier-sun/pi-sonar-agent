from __future__ import annotations

from pi_sonar_agent.core.blocker_checkers import (
    filter_quality_violations,
    filter_semantic_findings,
    run_new_blocker_check,
)
from pi_sonar_agent.core.quality_gate import QualityGateResult, QualityGateViolation
from pi_sonar_agent.core.semantic_precheck import SemanticPrecheckFinding, SemanticPrecheckResult


def test_blocker_checker_filters_findings_by_rule_profile() -> None:
    filtered = filter_semantic_findings(
        rule_id="csharpsquid:S1481",
        findings=(
            SemanticPrecheckFinding(
                finding_id="anonymous_type_helper_boundary",
                title="匿名类型跨 helper 边界风险",
                message="risk",
                file="src/Foo.cs",
                line=12,
            ),
            SemanticPrecheckFinding(
                finding_id="repair_plan_contract_violation",
                title="越界范围变更",
                message="scope",
                file="src/Foo.cs",
                line=16,
            ),
        ),
    )

    assert len(filtered) == 1
    assert filtered[0].finding_id == "repair_plan_contract_violation"


def test_blocker_checker_reports_type_shape_blockers_for_s3776() -> None:
    result = run_new_blocker_check(
        rule_id="csharpsquid:S3776",
        semantic_precheck_result=SemanticPrecheckResult(
            status="retry",
            summary="blocked",
            findings=(
                SemanticPrecheckFinding(
                    finding_id="anonymous_type_helper_boundary",
                    title="匿名类型跨 helper 边界风险",
                    message="当前 patch 在 helper 内构造匿名类型。",
                    file="src/Foo.cs",
                    line=12,
                ),
            ),
        ),
        quality_gate_result=QualityGateResult(status="pass", summary="ok"),
        build_passed=True,
    )

    assert result.status == "FAIL"
    assert any("helper_type_shape_break" in item for item in result.blockers)


def test_blocker_checker_keeps_only_profile_relevant_quality_gate_rules() -> None:
    filtered = filter_quality_violations(
        rule_id="csharpsquid:S1481",
        violations=(
            QualityGateViolation(
                rule_id="async_requires_await",
                title="异步方法必须真正 await",
                message="missing await",
                file="src/Foo.cs",
                line=8,
            ),
            QualityGateViolation(
                rule_id="language_feature_compatibility",
                title="语言特性兼容性",
                message="record 不兼容",
                file="src/Foo.cs",
                line=2,
            ),
        ),
    )

    assert len(filtered) == 1
    assert filtered[0].rule_id == "language_feature_compatibility"
