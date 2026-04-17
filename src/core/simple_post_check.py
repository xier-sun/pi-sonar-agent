"""Light-weight post-build checks for headless simple-loop execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pi_sonar_agent.core.blocker_checkers import (
    filter_quality_violations,
    filter_semantic_findings,
    run_new_blocker_check,
)
from pi_sonar_agent.core.issue_checkers import run_issue_check
from pi_sonar_agent.core.quality_gate import (
    QualityGateResult,
    QualityGateSoftFinding,
)
from pi_sonar_agent.core.semantic_precheck import SemanticPrecheckResult
from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class IssueCheckResult:
    """Outcome of light-weight, rule-specific issue verification."""

    status: str
    summary: str
    findings: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class NewBlockerCheckResult:
    """Outcome of post-build hard-blocker inspection."""

    status: str
    summary: str
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class PostFixCheckResult:
    """Merged post-build result used by the simple loop."""

    issue_status: str
    issue_check: IssueCheckResult
    blocker_check: NewBlockerCheckResult
    retry_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


class SimplePostCheck:
    """Run headless post-build checks with PASS / FAIL / UNKNOWN semantics."""

    @staticmethod
    def not_evaluated(summary: str) -> PostFixCheckResult:
        issue_check = IssueCheckResult(
            status="UNKNOWN",
            summary="Issue check was not evaluated.",
        )
        blocker_check = NewBlockerCheckResult(
            status="PASS",
            summary=str(summary or "Post-fix check was not evaluated.").strip(),
        )
        return PostFixCheckResult(
            issue_status="UNKNOWN",
            issue_check=issue_check,
            blocker_check=blocker_check,
            retry_message="",
        )

    @classmethod
    def review(
        cls,
        *,
        issue,
        current_issue_file_content: str | None,
        semantic_precheck_result: SemanticPrecheckResult,
        quality_gate_result: QualityGateResult,
    ) -> PostFixCheckResult:
        issue_check = cls._run_issue_check(
            issue=issue,
            current_issue_file_content=current_issue_file_content,
        )
        blocker_check = cls._run_blocker_check(
            rule_id=str(getattr(issue, "rule", "") or "").strip(),
            semantic_precheck_result=semantic_precheck_result,
            quality_gate_result=quality_gate_result,
        )
        retry_sections: list[str] = []
        if blocker_check.status == "FAIL":
            retry_sections.append(blocker_check.summary)
            retry_sections.extend(blocker_check.blockers)
        if issue_check.status == "FAIL":
            retry_sections.append(issue_check.summary)
            retry_sections.extend(issue_check.findings)

        if blocker_check.status == "FAIL" or issue_check.status == "FAIL":
            return PostFixCheckResult(
                issue_status="FAIL",
                issue_check=issue_check,
                blocker_check=blocker_check,
                retry_message="\n".join(item for item in retry_sections if str(item).strip()),
            )

        if issue_check.status == "PASS":
            return PostFixCheckResult(
                issue_status="PASS",
                issue_check=issue_check,
                blocker_check=blocker_check,
                retry_message="",
            )

        return PostFixCheckResult(
            issue_status="UNKNOWN",
            issue_check=issue_check,
            blocker_check=blocker_check,
            retry_message="",
        )

    @classmethod
    def filter_semantic_precheck(
        cls,
        result: SemanticPrecheckResult,
        *,
        rule_id: str = "",
    ) -> SemanticPrecheckResult:
        findings = filter_semantic_findings(
            rule_id=rule_id,
            findings=tuple(getattr(result, "findings", ()) or ()),
        )
        if findings:
            return SemanticPrecheckResult(
                status="retry",
                summary=f"Simple-loop post-check found {len(findings)} hard blocker(s).",
                findings=findings,
            )
        return SemanticPrecheckResult(
            status="pass",
            summary="Simple-loop post-check found no semantic blockers.",
        )

    @classmethod
    def filter_quality_gate(
        cls,
        result: QualityGateResult,
        *,
        rule_id: str = "",
    ) -> QualityGateResult:
        violations = filter_quality_violations(
            rule_id=rule_id,
            violations=tuple(getattr(result, "violations", ()) or ()),
        )
        if violations:
            return QualityGateResult(
                status="retry",
                summary=f"Simple-loop post-check found {len(violations)} hard blocker(s).",
                applied_rule_ids=tuple(getattr(result, "applied_rule_ids", ()) or ()),
                violations=violations,
                soft_findings=tuple(getattr(result, "soft_findings", ()) or ()),
            )

        downgraded_findings = tuple(
            list(tuple(getattr(result, "soft_findings", ()) or ()))
            + [
                QualityGateSoftFinding(
                    rule_id=violation.rule_id,
                    title=violation.title,
                    message=violation.message,
                    file=violation.file,
                    line=violation.line,
                    symbol=violation.symbol,
                    evidence=violation.evidence,
                )
                for violation in tuple(getattr(result, "violations", ()) or ())
            ]
        )
        return QualityGateResult(
            status="pass",
            summary="Simple-loop post-check ignored non-blocking quality-gate findings after a successful build.",
            applied_rule_ids=tuple(getattr(result, "applied_rule_ids", ()) or ()),
            violations=(),
            soft_findings=downgraded_findings,
        )

    @classmethod
    def _run_issue_check(
        cls,
        *,
        issue,
        current_issue_file_content: str | None,
    ) -> IssueCheckResult:
        outcome = run_issue_check(
            issue=issue,
            file_content=current_issue_file_content,
        )
        return IssueCheckResult(
            status=str(outcome.status or "").strip() or "UNKNOWN",
            summary=str(outcome.summary or "").strip(),
            findings=tuple(str(item).strip() for item in outcome.findings if str(item).strip()),
            metrics=dict(getattr(outcome, "metrics", {}) or {}),
        )

    @classmethod
    def _run_blocker_check(
        cls,
        *,
        rule_id: str,
        semantic_precheck_result: SemanticPrecheckResult,
        quality_gate_result: QualityGateResult,
    ) -> NewBlockerCheckResult:
        outcome = run_new_blocker_check(
            rule_id=rule_id,
            semantic_precheck_result=semantic_precheck_result,
            quality_gate_result=quality_gate_result,
            build_passed=True,
            build_output="",
        )
        return NewBlockerCheckResult(
            status=str(outcome.status or "").strip() or "PASS",
            summary=str(outcome.summary or "").strip(),
            blockers=tuple(str(item).strip() for item in outcome.blockers if str(item).strip()),
        )
