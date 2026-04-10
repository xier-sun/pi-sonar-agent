"""Boundary review runtime for one issue attempt."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from pi_sonar_agent.core.boundary_capabilities import (
    ADJACENT_CLEANUP_CAPABILITY,
    BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
    BOUNDARY_PROFILE_DECLARATION_ANCHOR,
    BOUNDARY_PROFILE_MEMBER_CLUSTER,
    DECLARATION_DELETE_CAPABILITY,
    HELPER_EXTRACT_CAPABILITY,
    MEMBER_DELETE_CAPABILITY,
    METHOD_CLUSTER_DELETE_CAPABILITY,
)
from pi_sonar_agent.core.diff_reviewer import DiffReviewer, ReviewedFileChange, ReviewerResult
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.scope_guard import IssueEditScope, LegacyScopeGuard
from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class BoundaryReviewContext:
    """Runtime facts shared with optional boundary hooks."""

    issue_key: str
    rule_id: str
    guardrail_mode: str
    edit_contract: EditContract
    reviewed_changes: tuple[ReviewedFileChange, ...]
    reviewer_result: ReviewerResult | None = None
    scope_violation: str | None = None


@dataclass(frozen=True)
class BoundaryReviewOutcome:
    """Structured result for scope + contract boundary review."""

    reviewer_result: ReviewerResult
    reviewer_retry_message: str
    scope_violation: str | None
    primary_failure_code: str = ""
    primary_failure_summary: str = ""
    secondary_failure_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoundaryFailure:
    """Structured boundary failure emitted by the runtime pipeline."""

    code: str
    source: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return serialize_state(self)


class BoundaryRuntimeHook(Protocol):
    """Optional hook interface around boundary review."""

    def before_boundary_review(self, context: BoundaryReviewContext) -> None:
        """Called before the boundary runtime evaluates the attempt."""

    def after_boundary_review(self, context: BoundaryReviewContext) -> None:
        """Called after the boundary runtime evaluates the attempt."""


class BoundaryHookPipeline:
    """Execute boundary hooks in a stable order."""

    def __init__(self, hooks: Iterable[object] = ()) -> None:
        self._hooks = tuple(hooks)

    def before_boundary_review(self, context: BoundaryReviewContext) -> None:
        for hook in self._hooks:
            callback = getattr(hook, "before_boundary_review", None)
            if callable(callback):
                callback(context)

    def after_boundary_review(self, context: BoundaryReviewContext) -> None:
        for hook in self._hooks:
            callback = getattr(hook, "after_boundary_review", None)
            if callable(callback):
                callback(context)


class BoundaryRuntime:
    """Run post-edit scope/contract boundary checks as one pipeline."""

    _PRIVATE_MEMBER_HEADER_TOKENS = ("private ", "private\t")

    @classmethod
    def _find_previous_non_empty_line(
        cls,
        lines: list[str],
        start_line: int,
    ) -> int | None:
        for line_number in range(min(start_line, len(lines)), 0, -1):
            if lines[line_number - 1].strip():
                return line_number
        return None

    @classmethod
    def _looks_like_private_member_header(
        cls,
        lines: list[str],
        start_line: int,
    ) -> bool:
        header_end = min(len(lines), start_line + 3)
        header_text = " ".join(
            line.strip()
            for line in lines[start_line - 1:header_end]
            if line.strip()
        ).lower()
        return any(token in header_text for token in cls._PRIVATE_MEMBER_HEADER_TOKENS)

    @staticmethod
    def _is_comment_or_attribute_line(line_text: str) -> bool:
        stripped = str(line_text or "").strip()
        return bool(
            stripped.startswith("///")
            or stripped.startswith("//")
            or stripped.startswith("[")
            or stripped.endswith("]")
            or stripped.startswith("/*")
            or stripped.startswith("*")
        )

    @classmethod
    def _expand_attached_comment_start(
        cls,
        lines: list[str],
        start_line: int,
    ) -> int:
        current = max(1, start_line)
        while current > 1:
            previous = lines[current - 2].strip()
            if not previous:
                break
            if cls._is_comment_or_attribute_line(previous):
                current -= 1
                continue
            break
        return current

    @classmethod
    def _scan_adjacent_private_member_ranges(
        cls,
        lines: list[str],
        start_search_line: int,
        *,
        stop_after_line: int = 0,
    ) -> tuple[tuple[int, int], ...]:
        ranges: list[tuple[int, int]] = []
        cursor = max(1, start_search_line)
        cluster_end = cursor - 1
        while True:
            next_line = LegacyScopeGuard._find_next_non_empty_line(lines, cursor)
            if next_line is None:
                break
            if cluster_end > 0:
                gap_lines = lines[cluster_end: max(next_line - 1, cluster_end)]
                if any(
                    line.strip() and not cls._is_comment_or_attribute_line(line)
                    for line in gap_lines
                ):
                    break
            member_range = LegacyScopeGuard._find_enclosing_method_range(lines, next_line)
            if member_range is None or member_range[0] < next_line:
                break
            if not cls._looks_like_private_member_header(lines, member_range[0]):
                break
            expanded_start = cls._expand_attached_comment_start(lines, member_range[0])
            ranges.append((expanded_start, member_range[1]))
            cluster_end = member_range[1]
            cursor = member_range[1] + 1
            if stop_after_line and cluster_end >= stop_after_line:
                break
        return tuple(ranges)

    @classmethod
    def _build_runtime_relaxed_contract(
        cls,
        *,
        edit_contract: EditContract,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        original_issue_file_content: str | None,
    ) -> EditContract | None:
        target_files = set(edit_contract.target_files)
        if not target_files or any(change.file not in target_files for change in reviewed_changes):
            return None
        if original_issue_file_content is None:
            return None

        changed_lines = sorted(
            {
                int(line)
                for change in reviewed_changes
                if change.file in target_files
                for line in change.boundary_changed_lines
                if int(line) > 0
            }
        )
        if not changed_lines:
            return None

        source_lines = original_issue_file_content.splitlines()
        if not source_lines:
            return None

        normalized_ranges = list(edit_contract.allowed_line_ranges)
        changed_max = max(changed_lines)
        capabilities = set(edit_contract.allowed_capabilities)
        profile = str(edit_contract.boundary_profile or "").strip()
        expanded = False

        if normalized_ranges and (
            DECLARATION_DELETE_CAPABILITY in capabilities
            or ADJACENT_CLEANUP_CAPABILITY in capabilities
            or profile in {
                BOUNDARY_PROFILE_DECLARATION_ANCHOR,
                BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
            }
        ):
            first_start = min(start for start, _ in normalized_ranges)
            previous_non_empty = cls._find_previous_non_empty_line(
                source_lines,
                first_start - 1,
            )
            if previous_non_empty is not None and first_start - previous_non_empty <= 2:
                statement_range = LegacyScopeGuard._find_enclosing_statement_range(
                    source_lines,
                    previous_non_empty,
                )
                if statement_range not in normalized_ranges:
                    normalized_ranges.append(statement_range)
                    expanded = True

        if normalized_ranges and (
            MEMBER_DELETE_CAPABILITY in capabilities
            or METHOD_CLUSTER_DELETE_CAPABILITY in capabilities
            or HELPER_EXTRACT_CAPABILITY in capabilities
            or profile == BOUNDARY_PROFILE_MEMBER_CLUSTER
        ):
            first_start = min(start for start, _ in normalized_ranges)
            expanded_start = cls._expand_attached_comment_start(source_lines, first_start)
            if expanded_start < first_start:
                normalized_ranges.append((expanded_start, first_start))
                expanded = True

            current_end = max(end for _, end in normalized_ranges)
            adjacent_ranges = cls._scan_adjacent_private_member_ranges(
                source_lines,
                current_end + 1,
                stop_after_line=changed_max,
            )
            for item in adjacent_ranges:
                if item not in normalized_ranges:
                    normalized_ranges.append(item)
                    expanded = True
                current_end = max(current_end, item[1])
            next_non_empty = LegacyScopeGuard._find_next_non_empty_line(source_lines, current_end + 1)
            if next_non_empty is not None:
                trailing_gap_end = next_non_empty - 1
                if trailing_gap_end >= current_end + 1:
                    trailing_gap_lines = source_lines[current_end:trailing_gap_end]
                    if not any(
                        line.strip() and not cls._is_comment_or_attribute_line(line)
                        for line in trailing_gap_lines
                    ):
                        current_end = trailing_gap_end
            contiguous_cluster_range = (expanded_start, current_end)
            if contiguous_cluster_range not in normalized_ranges:
                normalized_ranges.append(contiguous_cluster_range)
                expanded = True

        if not expanded:
            return None

        normalized_ranges = sorted(
            {
                (min(start, end), max(start, end))
                for start, end in normalized_ranges
                if start > 0 and end > 0
            }
        )
        if not normalized_ranges:
            return None

        return replace(edit_contract, allowed_line_ranges=tuple(normalized_ranges))

    @staticmethod
    def _classify_boundary_failure(
        *,
        source: str,
        edit_contract: EditContract,
        reviewer_result: ReviewerResult | None = None,
        scope_violation: str | None = None,
    ) -> BoundaryFailure | None:
        if source == "reviewer" and (reviewer_result is None or reviewer_result.status != "retry"):
            return None
        if source == "scope" and not str(scope_violation or "").strip():
            return None

        if source == "reviewer" and reviewer_result is not None:
            forbidden_path_violation = next(
                (item for item in reviewer_result.violations if item.type == "forbidden_path"),
                None,
            )
            if forbidden_path_violation is not None:
                return BoundaryFailure(
                    code="filesystem_forbidden_path",
                    source=source,
                    summary=forbidden_path_violation.reason
                    or "Patch touched a path outside the allowed source workspace boundary.",
                )
            file_created_violation = next(
                (item for item in reviewer_result.violations if item.type == "file_created"),
                None,
            )
            if file_created_violation is not None:
                return BoundaryFailure(
                    code="filesystem_file_created",
                    source=source,
                    summary=file_created_violation.reason
                    or "Patch created a new file, which is not allowed during automated repair.",
                )
            file_deleted_violation = next(
                (item for item in reviewer_result.violations if item.type == "file_deleted"),
                None,
            )
            if file_deleted_violation is not None:
                return BoundaryFailure(
                    code="filesystem_file_deleted",
                    source=source,
                    summary=file_deleted_violation.reason
                    or "Patch deleted a file, which is not allowed during automated repair.",
                )
            return BoundaryFailure(
                code="filesystem_boundary_reject",
                source=source,
                summary="Patch violated the filesystem boundary policy.",
            )

        return None

    @classmethod
    def review(
        cls,
        *,
        issue_key: str,
        rule_id: str,
        guardrail_mode: str,
        edit_contract: EditContract,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        workspace_path,
        issue,
        scope: IssueEditScope | None,
        original_issue_file_content: str | None = None,
        current_issue_file_content: str | None = None,
        scope_validator=None,
        hooks: BoundaryHookPipeline | None = None,
    ) -> BoundaryReviewOutcome:
        hook_pipeline = hooks or BoundaryHookPipeline()
        pending_context = BoundaryReviewContext(
            issue_key=issue_key,
            rule_id=rule_id,
            guardrail_mode=guardrail_mode,
            edit_contract=edit_contract,
            reviewed_changes=reviewed_changes,
        )
        hook_pipeline.before_boundary_review(pending_context)

        effective_contract = edit_contract
        reviewer_result = DiffReviewer.review(edit_contract=effective_contract, file_changes=reviewed_changes)
        scope_violation = None
        finalized_context = BoundaryReviewContext(
            issue_key=issue_key,
            rule_id=rule_id,
            guardrail_mode=guardrail_mode,
            edit_contract=effective_contract,
            reviewed_changes=reviewed_changes,
            reviewer_result=reviewer_result,
            scope_violation=scope_violation,
        )
        hook_pipeline.after_boundary_review(finalized_context)
        reviewer_failure = cls._classify_boundary_failure(
            source="reviewer",
            edit_contract=effective_contract,
            reviewer_result=reviewer_result,
        )
        primary_failure = reviewer_failure
        secondary_failures = tuple(
            failure.code
            for failure in (reviewer_failure,)
            if failure is not None
            and primary_failure is not None
            and failure.code != primary_failure.code
        )
        return BoundaryReviewOutcome(
            reviewer_result=reviewer_result,
            reviewer_retry_message=reviewer_result.to_retry_message(),
            scope_violation=scope_violation,
            primary_failure_code=(primary_failure.code if primary_failure is not None else ""),
            primary_failure_summary=(primary_failure.summary if primary_failure is not None else ""),
            secondary_failure_codes=secondary_failures,
        )
