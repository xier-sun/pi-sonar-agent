"""Boundary review runtime for one issue attempt."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from pi_sonar_agent.core.diff_reviewer import DiffReviewer, ReviewedFileChange, ReviewerResult
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.scope_guard import IssueEditScope, LegacyScopeGuard


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

        reviewer_result = DiffReviewer.review(
            edit_contract=edit_contract,
            file_changes=reviewed_changes,
        )
        effective_scope_validator = scope_validator or LegacyScopeGuard.validate_issue_edit_scope
        scope_violation = effective_scope_validator(
            workspace_path,
            issue,
            scope,
            original_content=original_issue_file_content,
            current_content=current_issue_file_content,
        )
        finalized_context = BoundaryReviewContext(
            issue_key=issue_key,
            rule_id=rule_id,
            guardrail_mode=guardrail_mode,
            edit_contract=edit_contract,
            reviewed_changes=reviewed_changes,
            reviewer_result=reviewer_result,
            scope_violation=scope_violation,
        )
        hook_pipeline.after_boundary_review(finalized_context)
        return BoundaryReviewOutcome(
            reviewer_result=reviewer_result,
            reviewer_retry_message=reviewer_result.to_retry_message(),
            scope_violation=scope_violation,
        )
