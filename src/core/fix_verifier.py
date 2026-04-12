"""Post-edit verification helpers for issue fix attempts."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_sonar_agent.agent.rule_policies import get_rule_policy
from pi_sonar_agent.agent.rule_validators import validate_rule_fix
from pi_sonar_agent.core.attempt_scheduler import AttemptScheduler
from pi_sonar_agent.core.boundary_runtime import BoundaryRuntime
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange, ReviewerResult
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.propagation_verifier import PropagationCheckResult, PropagationVerifier
from pi_sonar_agent.core.quality_gate import QualityGateResult
from pi_sonar_agent.core.quality_gate_verifier import QualityGateVerifier
from pi_sonar_agent.core.review_gate import ReviewGateAgent, ReviewGateResult
from pi_sonar_agent.core.scope_guard import IssueEditScope

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
    propagation_check_result: PropagationCheckResult
    quality_gate_result: QualityGateResult
    review_gate_result: ReviewGateResult
    combined_output: str
    rule_validation_message: str
    fast_compile_passed: bool = True
    fast_compile_output: str = ""
    fast_compile_command: str = ""
    fast_compile_invoked: bool = False
    fast_compile_duration_seconds: float = 0.0
    boundary_failure_code: str = ""
    boundary_failure_summary: str = ""
    secondary_boundary_failure_codes: tuple[str, ...] = ()
    build_invoked: bool = False
    build_duration_seconds: float = 0.0


class FixVerifier:
    """Build and guardrail verification for issue patches."""

    BUILD_TIMEOUT_SECONDS = 300
    FAST_COMPILE_TIMEOUT_SECONDS = 90
    _RESTORE_FAILURE_MARKERS = (
        "NU1301",
        "Unable to load the service index for source",
        "api.nuget.org",
        "No such host is known",
        "Temporary failure in name resolution",
        "Name or service not known",
    )
    _MISSING_ASSETS_MARKERS = (
        "NETSDK1004",
        "project.assets.json",
        "运行 NuGet 包还原以生成此文件",
    )

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
    def _looks_like_restore_source_failure(cls, output: str) -> bool:
        text = str(output or "")
        return any(marker in text for marker in cls._RESTORE_FAILURE_MARKERS)

    @classmethod
    def _looks_like_missing_assets_failure(cls, output: str) -> bool:
        text = str(output or "")
        return all(marker in text for marker in ("NETSDK1004", "project.assets.json")) or any(
            marker in text for marker in cls._MISSING_ASSETS_MARKERS
        )

    @staticmethod
    def _derive_offline_verification_command(command: str) -> str:
        normalized = str(command or "").strip()
        lowered = normalized.lower()
        if not (lowered.startswith("dotnet build") or lowered.startswith("dotnet test")):
            return ""
        if "--no-restore" not in lowered:
            normalized += " --no-restore"
        return normalized

    @classmethod
    def run_local_build_fallback(
        cls,
        workspace_path: Path,
        build_command: str,
    ) -> tuple[bool, str]:
        """Run a local fallback build when the model-triggered build tool crashes."""

        def execute(command: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                command,
                shell=True,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cls.BUILD_TIMEOUT_SECONDS,
            )

        def format_result(result: subprocess.CompletedProcess[str]) -> str:
            output = cls._combine_process_output(result).strip()
            header = f"本地回退构建 Exit code: {result.returncode}"
            if output:
                return f"{header}\n\n{output}"
            return header

        try:
            result = execute(build_command)
        except Exception as exc:
            return False, f"本地回退构建也失败：\n{cls.format_exception_details(exc)}"

        output = cls._combine_process_output(result)
        if result.returncode == 0:
            return True, format_result(result)

        offline_command = cls._derive_offline_verification_command(build_command)
        if (
            offline_command
            and offline_command != str(build_command or "").strip()
            and cls._looks_like_restore_source_failure(output)
        ):
            try:
                retry_result = execute(offline_command)
            except Exception as exc:
                sections = [
                    "本地回退构建首次执行失败，且离线重试也抛出了异常。",
                    format_result(result),
                    f"离线重试命令: {offline_command}",
                    cls.format_exception_details(exc),
                ]
                return False, "\n\n".join(part for part in sections if str(part).strip())

            sections = [
                "本地回退构建首次执行遇到 NuGet restore/source 故障，已自动改用离线命令重试。",
                format_result(result),
                f"离线重试命令: {offline_command}",
                format_result(retry_result),
            ]
            return retry_result.returncode == 0, "\n\n".join(
                part for part in sections if str(part).strip()
            )

        return False, format_result(result)

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
        normalized_build_command = str(build_command or "").strip()
        try:
            result = runner(
                normalized_build_command,
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
        output = cls._combine_process_output(result)
        if result.returncode == 0:
            return True, output

        offline_command = cls._derive_offline_verification_command(normalized_build_command)
        if (
            offline_command
            and offline_command != normalized_build_command
            and cls._looks_like_restore_source_failure(output)
        ):
            try:
                retry_result = runner(
                    offline_command,
                    shell=True,
                    cwd=str(workspace_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=cls.BUILD_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                sections = [
                    "Build verification hit a NuGet restore/source failure and offline retry also raised an exception.",
                    output.strip(),
                    f"Offline retry command: {offline_command}",
                    cls.format_exception_details(exc),
                ]
                return False, "\n\n".join(part for part in sections if str(part).strip())

            retry_output = cls._combine_process_output(retry_result)
            sections = [
                "Build verification detected a NuGet restore/source failure and retried with --no-restore.",
                output.strip(),
                f"Offline retry command: {offline_command}",
                retry_output.strip(),
            ]
            return retry_result.returncode == 0, "\n\n".join(
                part for part in sections if str(part).strip()
            )

        return False, output

    @staticmethod
    def _derive_fast_compile_command(build_command: str) -> str:
        normalized = FixVerifier._derive_offline_verification_command(build_command)
        lowered = normalized.lower()
        if not lowered.startswith("dotnet build"):
            return ""
        if " -v:" not in lowered and " --verbosity" not in lowered:
            normalized += " -v:q"
        return normalized

    @classmethod
    def run_fast_compile(
        cls,
        workspace_path: Path,
        build_command: str,
        *,
        build_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> tuple[bool, str, str, bool]:
        """Run a short-lived compile precheck before the full build when supported."""

        fast_compile_command = cls._derive_fast_compile_command(build_command)
        if not fast_compile_command:
            return True, "", "", False

        runner = build_runner or subprocess.run
        try:
            result = runner(
                fast_compile_command,
                shell=True,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=cls.FAST_COMPILE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return False, cls.format_exception_details(exc), fast_compile_command, True
        return result.returncode == 0, cls._combine_process_output(result), fast_compile_command, True

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
        fast_compile_passed = True
        fast_compile_output = ""
        fast_compile_command = ""
        fast_compile_invoked = False
        boundary_outcome = BoundaryRuntime.review(
            issue_key=issue.key,
            rule_id=issue.rule,
            guardrail_mode=guardrail_mode,
            edit_contract=edit_contract,
            reviewed_changes=reviewed_changes,
            workspace_path=workspace_path,
            issue=issue,
            scope=scope,
            original_issue_file_content=original_issue_file_content,
            current_issue_file_content=current_issue_file_content,
            scope_validator=scope_validator,
        )
        reviewer_result = boundary_outcome.reviewer_result
        reviewer_retry_message = boundary_outcome.reviewer_retry_message
        scope_violation = boundary_outcome.scope_violation
        guardrail_message = reviewer_retry_message or scope_violation or ""
        propagation_check_result = PropagationCheckResult(
            status="pass",
            summary="Propagation lifecycle verifier disabled for this attempt.",
        )
        quality_gate_result = QualityGateVerifier.review(
            issue_file_path=issue.file_path,
            edit_contract=edit_contract,
            reviewed_changes=reviewed_changes,
            original_issue_file_content=original_issue_file_content,
            current_issue_file_content=current_issue_file_content,
        )
        rule_validation_message = ""
        if current_issue_file_content is not None:
            effective_rule_validator = rule_validator or cls.run_rule_specific_validation
            rule_validation_message = effective_rule_validator(issue, current_issue_file_content)

        verification_schedule = AttemptScheduler.build_verification_schedule(
            edit_contract=edit_contract,
            performance_flags=load_performance_flags(),
        )
        if verification_schedule.run_propagation_check_before_build:
            propagation_check_result = PropagationVerifier.review(
                workspace_path=workspace_path,
                edit_contract=edit_contract,
                issue_file_path=issue.file_path,
                current_issue_file_content=current_issue_file_content,
            )
        review_gate_result = ReviewGateResult(
            status="not_applicable",
            summary="Review gate was not evaluated for this attempt.",
        )
        if getattr(load_performance_flags(), "review_gate", True):
            review_gate_result = ReviewGateAgent.review(
                workspace_path=workspace_path,
                issue=issue,
                reviewed_changes=reviewed_changes,
                edit_contract=edit_contract,
                propagation_check_result=propagation_check_result,
                quality_gate_result=quality_gate_result,
                reviewer_status=reviewer_result.status,
                rule_validation_message=rule_validation_message,
            )
            if review_gate_result.status == "pass":
                propagation_check_result, quality_gate_result = ReviewGateAgent.apply_waivers(
                    propagation_check_result=propagation_check_result,
                    quality_gate_result=quality_gate_result,
                    review_gate_result=review_gate_result,
                )
        should_run_build = workspace_path.exists()
        if verification_schedule.skip_build_on_precheck_failure:
            if reviewer_result.status == "retry":
                should_run_build = False
            if review_gate_result.status == "retry":
                should_run_build = False
            if propagation_check_result.status == "retry":
                should_run_build = False
            if quality_gate_result.status == "retry":
                should_run_build = False
            if rule_validation_message:
                should_run_build = False

        fast_compile_duration_seconds = 0.0
        if should_run_build and verification_schedule.run_fast_compile_before_build:
            fast_compile_started_at = time.monotonic()
            fast_compile_passed, fast_compile_output, fast_compile_command, fast_compile_invoked = cls.run_fast_compile(
                workspace_path,
                build_command,
                build_runner=build_runner,
            )
            fast_compile_duration_seconds = time.monotonic() - fast_compile_started_at
            if fast_compile_invoked and not fast_compile_passed:
                if cls._looks_like_missing_assets_failure(fast_compile_output):
                    # Fresh clones may not have obj/project.assets.json yet. Let the full
                    # build perform restore instead of turning fast compile into a false
                    # hard gate for every issue in the run.
                    pass
                else:
                    should_run_build = False
                    build_output = (
                        f"Fast compile failed: {fast_compile_command}\n\n{fast_compile_output}".strip()
                    )

        build_duration_seconds = 0.0
        if should_run_build:
            build_started_at = time.monotonic()
            build_passed, build_output = cls.run_local_build(
                workspace_path,
                build_command,
                build_runner=build_runner,
            )
            build_duration_seconds = time.monotonic() - build_started_at
        elif fast_compile_invoked and not fast_compile_passed:
            build_passed = False

        combined_output_parts = [part for part in [build_output.strip(), guardrail_message] if part]
        combined_output = "\n\n".join(combined_output_parts)
        if propagation_check_result.status == "retry":
            propagation_retry_message = propagation_check_result.to_retry_message()
            combined_output = "\n\n".join(
                part
                for part in [combined_output.strip(), propagation_retry_message]
                if str(part).strip()
            )
        if review_gate_result.status == "retry":
            review_retry_message = review_gate_result.to_retry_message()
            combined_output = "\n\n".join(
                part
                for part in [combined_output.strip(), review_retry_message]
                if str(part).strip()
            )
        if quality_gate_result.status == "retry":
            quality_retry_message = quality_gate_result.to_retry_message()
            combined_output = "\n\n".join(
                part
                for part in [combined_output.strip(), quality_retry_message]
                if str(part).strip()
            )
        if rule_validation_message:
            combined_output = "\n\n".join(
                part
                for part in [combined_output.strip(), rule_validation_message]
                if str(part).strip()
            )

        return VerificationOutcome(
            build_passed=build_passed,
            build_output=build_output,
            reviewer_result=reviewer_result,
            reviewer_retry_message=reviewer_retry_message,
            scope_violation=scope_violation,
            propagation_check_result=propagation_check_result,
            quality_gate_result=quality_gate_result,
            review_gate_result=review_gate_result,
            combined_output=combined_output,
            rule_validation_message=rule_validation_message,
            fast_compile_passed=fast_compile_passed,
            fast_compile_output=fast_compile_output,
            fast_compile_command=fast_compile_command,
            fast_compile_invoked=fast_compile_invoked,
            fast_compile_duration_seconds=round(fast_compile_duration_seconds, 3),
            boundary_failure_code=boundary_outcome.primary_failure_code,
            boundary_failure_summary=boundary_outcome.primary_failure_summary,
            secondary_boundary_failure_codes=boundary_outcome.secondary_failure_codes,
            build_invoked=should_run_build,
            build_duration_seconds=round(build_duration_seconds, 3),
        )
