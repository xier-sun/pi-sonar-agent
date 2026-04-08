"""Post-edit verification helpers for issue fix attempts."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_sonar_agent.agent.rule_policies import get_rule_policy
from pi_sonar_agent.agent.rule_validators import validate_rule_fix
from pi_sonar_agent.core.diff_reviewer import DiffReviewer, ReviewedFileChange, ReviewerResult
from pi_sonar_agent.core.scope_guard import IssueEditScope, LegacyScopeGuard

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue


@dataclass(frozen=True)
class VerificationOutcome:
    """Structured verification result for a completed issue attempt."""

    build_passed: bool
    build_output: str
    reviewer_result: ReviewerResult
    reviewer_retry_message: str
    scope_violation: str | None
    combined_output: str
    rule_validation_message: str


class FixVerifier:
    """Build and guardrail verification for issue patches."""

    BUILD_TIMEOUT_SECONDS = 300

    @staticmethod
    def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        return f"{stdout}{stderr}"

    @staticmethod
    def _normalize_exception_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace").strip()
        return str(value).strip()

    @classmethod
    def format_exception_details(cls, exc: BaseException) -> str:
        """Collect the most useful exception details, including stderr/stdout when present."""

        parts: list[str] = []
        primary = cls._normalize_exception_text(exc)
        if primary:
            parts.append(primary)

        for attr_name, label in (
            ("stderr", "STDERR"),
            ("stdout", "STDOUT"),
            ("output", "OUTPUT"),
        ):
            value = cls._normalize_exception_text(getattr(exc, attr_name, ""))
            if value:
                parts.append(f"{label}:\n{value}")

        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            cause_text = cls._normalize_exception_text(cause)
            if cause_text and cause_text not in parts:
                parts.append(f"CAUSE:\n{cause_text}")

        return "\n\n".join(dict.fromkeys(item for item in parts if item))

    @classmethod
    def run_local_build_fallback(
        cls,
        workspace_path: Path,
        build_command: str,
    ) -> tuple[bool, str]:
        """Run a local fallback build when the model-triggered build tool crashes."""

        try:
            result = subprocess.run(
                build_command,
                shell=True,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cls.BUILD_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return False, f"本地回退构建也失败：\n{cls.format_exception_details(exc)}"

        output = cls._combine_process_output(result).strip()
        header = f"本地回退构建 Exit code: {result.returncode}"
        if output:
            return result.returncode == 0, f"{header}\n\n{output}"
        return result.returncode == 0, header

    @classmethod
    def run_local_build(
        cls,
        workspace_path: Path,
        build_command: str,
        *,
        build_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> tuple[bool, str]:
        """Run the normal post-edit build verification."""

        runner = build_runner or subprocess.run
        try:
            result = runner(
                build_command,
                shell=True,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cls.BUILD_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return False, cls.format_exception_details(exc)
        return result.returncode == 0, cls._combine_process_output(result)

    @staticmethod
    def run_rule_specific_validation(issue: SonarIssue, file_content: str) -> str:
        """Run post-fix local validation for rules that support it."""

        policy = get_rule_policy(issue.rule)
        return validate_rule_fix(
            validator_name=policy.local_validator,
            issue_line=issue.line,
            file_content=file_content,
        )

    @classmethod
    def evaluate_attempt(
        cls,
        *,
        issue: SonarIssue,
        workspace_path: Path,
        build_command: str,
        edit_contract: Any,
        guardrail_mode: str,
        scope: IssueEditScope | None,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        original_issue_file_content: str | None = None,
        current_issue_file_content: str | None = None,
        build_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        scope_validator: Callable[..., str | None] | None = None,
        rule_validator: Callable[[SonarIssue, str], str] | None = None,
    ) -> VerificationOutcome:
        """Evaluate build, diff review, scope, and rule-specific validation."""

        build_passed = False
        build_output = ""
        if workspace_path.exists():
            build_passed, build_output = cls.run_local_build(
                workspace_path,
                build_command,
                build_runner=build_runner,
            )

        reviewer_result = DiffReviewer.review(
            edit_contract=edit_contract,
            file_changes=reviewed_changes,
        )
        reviewer_retry_message = reviewer_result.to_retry_message()
        effective_scope_validator = scope_validator or LegacyScopeGuard.validate_issue_edit_scope
        scope_violation = effective_scope_validator(
            workspace_path,
            issue,
            scope,
            original_content=original_issue_file_content,
            current_content=current_issue_file_content,
        )
        guardrail_message = scope_violation if guardrail_mode == "scope" else reviewer_retry_message
        combined_output_parts = [part for part in [build_output.strip(), guardrail_message] if part]
        combined_output = "\n\n".join(combined_output_parts)

        rule_validation_message = ""
        if current_issue_file_content is not None:
            effective_rule_validator = rule_validator or cls.run_rule_specific_validation
            rule_validation_message = effective_rule_validator(issue, current_issue_file_content)

        return VerificationOutcome(
            build_passed=build_passed,
            build_output=build_output,
            reviewer_result=reviewer_result,
            reviewer_retry_message=reviewer_retry_message,
            scope_violation=scope_violation,
            combined_output=combined_output,
            rule_validation_message=rule_validation_message,
        )
