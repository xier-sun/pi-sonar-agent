"""Diff review helpers for single-issue edit contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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
                    "- Do not create, delete, move, or rename files.",
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
    _PRIVATE_METHOD_DECLARATION_RE = re.compile(
        r"^\s*private\s+(?!class\b|record\b|struct\b|interface\b|enum\b|delegate\b)"
        r"(?:static\s+|async\s+|unsafe\s+|new\s+|partial\s+|extern\s+|virtual\s+|override\s+)*"
        r"[\w<>\[\],.?]+\s+[\w@]+\s*\(",
        re.IGNORECASE,
    )
    _PRIVATE_CONSTRUCTOR_DECLARATION_RE = re.compile(
        r"^\s*private\s+(?:unsafe\s+|extern\s+|partial\s+)*[\w@]+\s*\(",
        re.IGNORECASE,
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
    def _normalize_creation_roots(edit_contract: EditContract) -> tuple[str, ...]:
        roots = []
        for root in getattr(edit_contract, "allowed_new_file_roots", ()) or ():
            normalized = str(root or "").replace("\\", "/").strip().strip("/")
            if normalized:
                roots.append(normalized)
        return tuple(dict.fromkeys(roots))

    @classmethod
    def _is_allowed_created_file(cls, file_path: str, edit_contract: EditContract) -> bool:
        if not bool(getattr(edit_contract, "allow_file_creation", False)):
            return False
        normalized_path = str(file_path or "").replace("\\", "/").strip().lstrip("/")
        if not normalized_path or cls._is_protected_path(normalized_path):
            return False
        roots = cls._normalize_creation_roots(edit_contract)
        if not roots:
            return False
        return any(
            root == "." or normalized_path == root or normalized_path.startswith(root + "/")
            for root in roots
        )

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

    @classmethod
    def _is_private_method_declaration(cls, line_text: str) -> bool:
        normalized = str(line_text or "").strip()
        if not normalized.startswith("private ") or "(" not in normalized:
            return False
        prefix = normalized.split("(", 1)[0]
        if "=" in prefix:
            return False
        return bool(
            cls._PRIVATE_METHOD_DECLARATION_RE.match(normalized)
            or cls._PRIVATE_CONSTRUCTOR_DECLARATION_RE.match(normalized)
        )

    @classmethod
    def _find_private_method_additions(
        cls,
        change: ReviewedFileChange,
    ) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    int(operation.after_line)
                    for operation in change.line_operations
                    if operation.kind == "add"
                    and int(operation.after_line or 0) > 0
                    and cls._is_private_method_declaration(operation.text)
                }
            )
        )

    @staticmethod
    def _build_audit_summary(
        *,
        hard_violations: list[ReviewerViolation],
        soft_violations: list[ReviewerViolation],
    ) -> str:
        if hard_violations:
            hard_types = {item.type for item in hard_violations}
            filesystem_only = hard_types.issubset(
                {"forbidden_path", "file_created", "file_deleted"}
            )
            if filesystem_only:
                return "Patch violates the filesystem boundary policy."
            return "Patch violates the current issue contract and must be retried with a smaller, compliant patch."
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

        drift_score = cls._drift_score(hard_violations)
        metrics = {
            "changed_file_count": len(file_changes),
            "hunk_count": sum(change.hunk_count for change in file_changes),
            "total_changed_lines": sum(len(change.boundary_changed_lines) for change in file_changes),
            "hard_boundary_violation_count": len(hard_violations),
            "soft_boundary_violation_count": 0,
            "extra_touched_file_count": 0,
            "outside_primary_region_line_count": 0,
            "outside_primary_region_count": 0,
            "scope_audit_mode": "filesystem_only",
            "scope_audit_active": False,
            "scope_expansion_count": 0,
            "scope_expansion_reasons": [],
            "high_drift_warning": drift_score >= 10,
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
