from __future__ import annotations

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.quality_gate import QualityGateResult, QualityGateViolation
from pi_sonar_agent.core.semantic_precheck import SemanticPrecheckFinding, SemanticPrecheckResult
from pi_sonar_agent.core.simple_post_check import SimplePostCheck


def test_simple_post_check_returns_unknown_when_no_validator_and_no_blockers() -> None:
    issue = SonarIssue(
        key="issue-unknown",
        rule="csharpsquid:S1066",
        message="可合并的 if 语句",
        line=10,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    result = SimplePostCheck.review(
        issue=issue,
        current_issue_file_content="class Foo { void Process() { return; } }\n",
        semantic_precheck_result=SemanticPrecheckResult(status="pass", summary="ok"),
        quality_gate_result=QualityGateResult(status="pass", summary="ok"),
    )

    assert result.issue_status == "UNKNOWN"
    assert result.blocker_check.status == "PASS"
    assert result.retry_message == ""


def test_simple_post_check_fails_when_new_blocker_is_detected() -> None:
    issue = SonarIssue(
        key="issue-blocker",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=10,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    result = SimplePostCheck.review(
        issue=issue,
        current_issue_file_content="class Foo { void Process() { return; } }\n",
        semantic_precheck_result=SemanticPrecheckResult(
            status="retry",
            summary="blocked",
            findings=(
                SemanticPrecheckFinding(
                    finding_id="async_without_await",
                    title="异步方法必须真正 await",
                    message="异步方法 HelperAsync 没有实际 await。",
                    file="src/Foo.cs",
                    line=10,
                ),
            ),
        ),
        quality_gate_result=QualityGateResult(status="pass", summary="ok"),
    )

    assert result.issue_status == "FAIL"
    assert result.blocker_check.status == "FAIL"
    assert "async_without_await" in result.retry_message


def test_simple_post_check_filters_non_blocking_quality_gate_violations() -> None:
    raw_result = QualityGateResult(
        status="retry",
        summary="raw failure",
        applied_rule_ids=("linq_method_syntax",),
        violations=(
            QualityGateViolation(
                rule_id="linq_method_syntax",
                title="LINQ 优先方法语法",
                message="当前 patch 引入了 query syntax。",
                file="src/Foo.cs",
                line=12,
            ),
        ),
    )

    filtered = SimplePostCheck.filter_quality_gate(raw_result)

    assert filtered.status == "pass"
    assert filtered.violations == ()
    assert len(filtered.soft_findings) == 1
