"""Diff review helpers for single-issue edit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.boundary_policy import BoundaryPolicy
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.state import serialize_state, utc_now_iso


@dataclass(frozen=True)
class ReviewedLineOperation:
    """Single diff edit expressed in before/after coordinate spaces."""

    kind: str
    before_line: int = 0
    after_line: int = 0
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewedFileChange:
    """Single file diff facts consumed by DiffReviewer."""

    file: str
    changed_lines: tuple[int, ...] = ()
    diff_text: str = ""
    hunk_count: int = 0
    before_exists: bool = True
    after_exists: bool = True
    before_changed_lines: tuple[int, ...] = ()
    after_changed_lines: tuple[int, ...] = ()
    line_operations: tuple[ReviewedLineOperation, ...] = ()

    def __post_init__(self) -> None:
        normalized_changed = self._normalize_lines(self.changed_lines)
        normalized_before = self._normalize_lines(self.before_changed_lines)
        normalized_after = self._normalize_lines(self.after_changed_lines)
        explicit_coordinate_facts = bool(
            normalized_before or normalized_after or self.line_operations
        )
        if not normalized_before and normalized_changed:
            normalized_before = normalized_changed
        if not normalized_after and normalized_changed and not explicit_coordinate_facts:
            normalized_after = normalized_changed
        if not normalized_changed:
            normalized_changed = normalized_before or normalized_after
        object.__setattr__(self, "changed_lines", normalized_changed)
        object.__setattr__(self, "before_changed_lines", normalized_before)
        object.__setattr__(self, "after_changed_lines", normalized_after)

    @staticmethod
    def _normalize_lines(raw_lines: tuple[int, ...] | list[int]) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    int(line)
                    for line in raw_lines
                    if str(line).strip() and int(line) > 0
                }
            )
        )

    @property
    def boundary_changed_lines(self) -> tuple[int, ...]:
        """Lines compared against the original issue window."""

        return self.before_changed_lines or self.changed_lines

    @property
    def quality_gate_changed_lines(self) -> tuple[int, ...]:
        """Lines that still exist in the post-edit file."""

        return self.after_changed_lines

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

        hard_types = {item.type for item in self.violations}
        filesystem_only = hard_types and hard_types.issubset(
            {"forbidden_path", "file_created", "file_deleted"}
        )

        lines = [
            (
                "Diff reviewer rejected the patch because it violated the filesystem boundary policy."
                if filesystem_only
                else "Diff reviewer rejected the patch because it no longer stays inside the issue contract."
            ),
            self.summary,
        ]
        if self.violations:
            lines.append("Violations:")
            for index, violation in enumerate(self.violations, start=1):
                detail = f"{index}. [{violation.type}] {violation.file}: {violation.reason}"
                if violation.changed_lines:
                    detail += " | changed lines: " + ", ".join(str(line) for line in violation.changed_lines[:12])
                lines.append(detail)
        if filesystem_only:
            lines.extend(
                [
                    "Review Constraints:",
                    "- Only modify existing source files inside the checked-out workspace.",
                    "- Do not create, delete, rename, or whole-file overwrite files.",
                    "- Keep code changes landing through Edit-style patch operations.",
                ]
            )
        else:
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

    _PROTECTED_PATH_PREFIXES = (
        ".git/",
        "logs/",
        ".agent_workspaces/",
    )

    @staticmethod
    def _line_range(contract: EditContract) -> tuple[int, int] | None:
        if len(contract.validation_line_range) == 2:
            return contract.validation_line_range[0], contract.validation_line_range[1]
        return None

    @staticmethod
    def _allowed_line_ranges(contract: EditContract) -> tuple[tuple[int, int], ...]:
        return BoundaryPolicy.contract_line_ranges(contract)

    @classmethod
    def _is_protected_path(cls, file_path: str) -> bool:
        normalized = str(file_path or "").replace("\\", "/").lstrip("/")
        return any(normalized.startswith(prefix) for prefix in cls._PROTECTED_PATH_PREFIXES)

    @staticmethod
    def _drift_score(violations: list[ReviewerViolation]) -> int:
        score = 0
        for violation in violations:
            if violation.type == "extra_touched_file":
                score += 3
            elif violation.type == "outside_primary_region":
                score += 1
            elif violation.type in {"file_created", "file_deleted", "forbidden_path"}:
                score += 10
        return score

    @staticmethod
    def _build_audit_summary(
        *,
        hard_violations: list[ReviewerViolation],
        soft_violations: list[ReviewerViolation],
    ) -> str:
        if hard_violations:
            return "Patch violates the filesystem boundary policy."
        if not soft_violations:
            return "Patch stayed inside the filesystem boundary and no contract drift was detected."
        return "Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit."

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
        allowed_line_ranges = cls._allowed_line_ranges(edit_contract)
        hard_violations: list[ReviewerViolation] = []
        soft_violations: list[ReviewerViolation] = []
        follow_ups: list[FollowUpItem] = []

        for change in file_changes:
            if cls._is_protected_path(change.file):
                reason = "This path is outside the allowed source workspace boundary."
                hard_violations.append(
                    ReviewerViolation(
                        type="forbidden_path",
                        file=change.file,
                        reason=reason,
                        changed_lines=change.boundary_changed_lines,
                        evidence_hunk=change.diff_text,
                    )
                )
                continue

            if not change.before_exists and change.after_exists:
                reason = "Creating new files is not allowed during automated issue repair."
                hard_violations.append(
                    ReviewerViolation(
                        type="file_created",
                        file=change.file,
                        reason=reason,
                        changed_lines=change.boundary_changed_lines or change.quality_gate_changed_lines,
                        evidence_hunk=change.diff_text,
                    )
                )
                continue

            if change.before_exists and not change.after_exists:
                reason = "Deleting files is not allowed during automated issue repair."
                hard_violations.append(
                    ReviewerViolation(
                        type="file_deleted",
                        file=change.file,
                        reason=reason,
                        changed_lines=change.boundary_changed_lines,
                        evidence_hunk=change.diff_text,
                    )
                )
                continue

            if change.file not in target_files:
                reason = "This file is outside the primary issue file set and was recorded as extra touched drift."
                soft_violations.append(
                    ReviewerViolation(
                        type="extra_touched_file",
                        file=change.file,
                        reason=reason,
                        changed_lines=change.boundary_changed_lines,
                        evidence_hunk=change.diff_text,
                    )
                )
                follow_ups.append(
                    FollowUpItem(
                        source_issue_key=edit_contract.issue_key,
                        file=change.file,
                        symbol="",
                        summary="Extra touched file detected outside the primary issue file set; review separately.",
                        evidence_hunk=change.diff_text,
                        discovered_at=utc_now_iso(),
                    )
                )
                continue

            if not allowed_line_ranges or not change.boundary_changed_lines:
                continue

            outside_lines = BoundaryPolicy.find_outside_lines(
                change.boundary_changed_lines,
                allowed_line_ranges,
            )
            if not outside_lines:
                continue

            if line_range is not None:
                start_line, end_line = line_range
                reason = (
                    f"This hunk changes lines outside the primary issue window {start_line}-{end_line}; recorded as drift only."
                )
            else:
                reason = "This hunk changes lines outside the primary issue ranges; recorded as drift only."
            soft_violations.append(
                ReviewerViolation(
                    type="outside_primary_region",
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
                    summary="Patch touched lines outside the primary issue region; review whether the extra cleanup is acceptable.",
                    evidence_hunk=change.diff_text,
                    discovered_at=utc_now_iso(),
                )
            )

        drift_score = cls._drift_score((*hard_violations, *soft_violations))
        metrics = {
            "changed_file_count": len(file_changes),
            "hunk_count": sum(change.hunk_count for change in file_changes),
            "total_changed_lines": sum(len(change.boundary_changed_lines) for change in file_changes),
            "hard_boundary_violation_count": len(hard_violations),
            "soft_boundary_violation_count": len(soft_violations),
            "extra_touched_file_count": sum(
                1 for item in soft_violations if item.type == "extra_touched_file"
            ),
            "outside_primary_region_line_count": sum(
                len(item.changed_lines) for item in soft_violations if item.type == "outside_primary_region"
            ),
            "drift_score": drift_score,
        }
        summary = cls._build_audit_summary(
            hard_violations=hard_violations,
            soft_violations=soft_violations,
        )
        violations = (*hard_violations, *soft_violations)
        if hard_violations:
            return ReviewerResult(
                status="retry",
                guardrail_mode=edit_contract.guardrail_mode,
                summary=summary,
                violations=violations,
                follow_ups=tuple(follow_ups),
                metrics=metrics,
            )
        return ReviewerResult(
            status="pass",
            guardrail_mode=edit_contract.guardrail_mode,
            summary=summary,
            violations=violations,
            follow_ups=tuple(follow_ups),
            metrics=metrics,
        )
