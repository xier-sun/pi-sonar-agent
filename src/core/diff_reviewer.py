"""Diff review helpers for single-issue edit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.state import serialize_state, utc_now_iso


@dataclass(frozen=True)
class ReviewedFileChange:
    """Single file diff facts consumed by DiffReviewer."""

    file: str
    changed_lines: tuple[int, ...]
    diff_text: str
    hunk_count: int = 0
    before_exists: bool = True
    after_exists: bool = True

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewerViolation:
    """Structured reviewer violation."""

    type: str
    file: str
    reason: str
    symbol: str = ""
    changed_lines: tuple[int, ...] = ()
    evidence_hunk: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class FollowUpItem:
    """Adjacent or incidental fix that should be recorded, not edited now."""

    source_issue_key: str
    file: str
    symbol: str
    summary: str
    evidence_hunk: str
    discovered_at: str
    discovered_by: str = "diff_reviewer"

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewerResult:
    """Diff reviewer verdict for the current issue attempt."""

    status: str
    guardrail_mode: str
    summary: str
    violations: tuple[ReviewerViolation, ...] = ()
    follow_ups: tuple[FollowUpItem, ...] = ()
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    def to_retry_message(self) -> str:
        """Render a retry-oriented summary for prompt feedback and logs."""

        if self.status != "retry":
            return ""

        lines = [
            "Diff reviewer rejected the patch because it no longer stays inside the issue contract.",
            self.summary,
        ]
        if self.violations:
            lines.append("Violations:")
            for index, violation in enumerate(self.violations, start=1):
                detail = f"{index}. [{violation.type}] {violation.file}: {violation.reason}"
                if violation.changed_lines:
                    detail += " | changed lines: " + ", ".join(str(line) for line in violation.changed_lines[:12])
                lines.append(detail)
        lines.extend(
            [
                "Review Constraints:",
                "- Only keep changes that are required for the current Sonar issue.",
                "- Do not touch undeclared files or unrelated lines in the same file.",
                "- Record incidental findings as follow-ups instead of editing them now.",
            ]
        )
        return "\n".join(lines)


class DiffReviewer:
    """Review a patch against the declared edit contract."""

    @staticmethod
    def _line_range(contract: EditContract) -> tuple[int, int] | None:
        if len(contract.validation_line_range) == 2:
            return contract.validation_line_range[0], contract.validation_line_range[1]
        return None

    @classmethod
    def review(
        cls,
        *,
        edit_contract: EditContract,
        file_changes: tuple[ReviewedFileChange, ...],
    ) -> ReviewerResult:
        """Review the current file changes against the edit contract."""

        target_files = set(edit_contract.target_files)
        line_range = cls._line_range(edit_contract)
        violations: list[ReviewerViolation] = []
        follow_ups: list[FollowUpItem] = []

        for change in file_changes:
            if change.file not in target_files:
                reason = "This file is not declared in the current edit contract."
                violations.append(
                    ReviewerViolation(
                        type="undeclared_file",
                        file=change.file,
                        reason=reason,
                        changed_lines=change.changed_lines,
                        evidence_hunk=change.diff_text,
                    )
                )
                follow_ups.append(
                    FollowUpItem(
                        source_issue_key=edit_contract.issue_key,
                        file=change.file,
                        symbol="",
                        summary="Potential adjacent fix found in undeclared file; review separately.",
                        evidence_hunk=change.diff_text,
                        discovered_at=utc_now_iso(),
                    )
                )
                continue

            if line_range is None or not change.changed_lines:
                continue

            start_line, end_line = line_range
            outside_lines = tuple(
                line for line in change.changed_lines if line < start_line or line > end_line
            )
            if not outside_lines:
                continue

            reason = (
                f"This hunk changes lines outside the edit contract window {start_line}-{end_line}."
            )
            violations.append(
                ReviewerViolation(
                    type="incidental_fix",
                    file=change.file,
                    reason=reason,
                    symbol=(edit_contract.target_symbols[0].symbol if edit_contract.target_symbols else ""),
                    changed_lines=outside_lines,
                    evidence_hunk=change.diff_text,
                )
            )
            follow_ups.append(
                FollowUpItem(
                    source_issue_key=edit_contract.issue_key,
                    file=change.file,
                    symbol=(edit_contract.target_symbols[0].symbol if edit_contract.target_symbols else ""),
                    summary="Potential adjacent cleanup found outside the current edit contract window.",
                    evidence_hunk=change.diff_text,
                    discovered_at=utc_now_iso(),
                )
            )

        metrics = {
            "changed_file_count": len(file_changes),
            "hunk_count": sum(change.hunk_count for change in file_changes),
            "total_changed_lines": sum(len(change.changed_lines) for change in file_changes),
        }
        if violations:
            summary = "Patch touches undeclared files or lines outside the declared edit contract."
            return ReviewerResult(
                status="retry",
                guardrail_mode=edit_contract.guardrail_mode,
                summary=summary,
                violations=tuple(violations),
                follow_ups=tuple(follow_ups),
                metrics=metrics,
            )
        return ReviewerResult(
            status="pass",
            guardrail_mode=edit_contract.guardrail_mode,
            summary="Patch stays inside the declared issue contract.",
            metrics=metrics,
        )
