"""Per-issue retry and rollback helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from inspect import signature
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import (
    ClaudeFixAgent,
    FixResult,
    SonarIssue,
)
from pi_sonar_agent.agent.rule_policies import (
    EXPRESSION_REWRITE_SCOPE_MODE,
    LOOP_REWRITE_SCOPE_MODE,
    get_rule_policy,
)
from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.follow_up_store import FollowUpStore
from pi_sonar_agent.core.retry_context import (
    CompilerErrorContext,
    RetryContext,
    ReviewFailureContext,
    ReviewViolationContext,
    ScopeViolationContext,
    render_retry_context,
)
from pi_sonar_agent.core.state import (
    AttemptState,
    AttemptStatus,
    IssueState,
    IssueStatus,
    RetryReason,
    WorkspaceBaseline,
    utc_now_iso,
)
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.fixers.build_gate import format_build_failure_report

DEFAULT_MAX_BUILD_RETRIES = 3


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "issue"


def _build_issue_artifact_root(
    repository: str,
    run_label: str,
    issue_key: str,
    artifact_root: str = "logs/issue_artifacts",
) -> Path:
    """Build the artifact directory for a single issue."""

    return (
        Path(artifact_root)
        / _sanitize_name(repository)
        / _sanitize_name(run_label)
        / _sanitize_name(issue_key)
    )


def _read_error_snippet(file_path: Path, line: int, radius: int = 2) -> str:
    """Read a small code snippet around a failing line."""

    if not file_path.exists():
        return ""
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    if not lines:
        return ""

    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return "\n".join(f"{index:4d} | {lines[index - 1]}" for index in range(start, end + 1))


def _extract_compiler_errors(workspace_path: Path, build_output: str) -> list[CompilerErrorContext]:
    """Extract unique compiler errors and local snippets from build output."""

    pattern = re.compile(
        r"^(?P<file>[A-Za-z]:\\.+?\.(?:cs|fs|vb))\((?P<line>\d+),(?P<column>\d+)\):\s+"
        r"error\s+(?P<code>[A-Z]{2,}\d+):\s+(?P<message>.+?)\s+\[",
        re.IGNORECASE,
    )

    seen: set[tuple[str, int, int, str, str]] = set()
    errors: list[CompilerErrorContext] = []

    for raw_line in (build_output or "").splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue

        file_path = match.group("file")
        line = int(match.group("line"))
        column = int(match.group("column"))
        code = match.group("code").upper()
        message = match.group("message").strip()
        key = (file_path, line, column, code, message)
        if key in seen:
            continue
        seen.add(key)

        errors.append(
            CompilerErrorContext(
                file_path=file_path,
                line=line,
                column=column,
                code=code,
                message=message,
                snippet=_read_error_snippet(Path(file_path), line),
            )
        )

    return errors


def _build_retry_guidance(errors: list[CompilerErrorContext]) -> list[str]:
    """Build targeted retry guidance from compiler error patterns."""

    codes = {item.code for item in errors}
    guidance = [
        "先修复这些编译错误，再确认 Sonar 问题仍然被修复。",
        "优先最小化修改，不要额外重写整个文件。",
    ]
    if "CS1963" in codes:
        guidance.append("不要在 IQueryable 或 Entity Framework 表达式树中使用 dynamic；改用显式类型，或先物化到内存后再做复杂逻辑。")
    if "CS0246" in codes:
        guidance.append("不要引入未定义的新类型、返回类型或 DTO；如果确实需要新类型，必须同时定义并正确引用。")
    if "CS0103" in codes:
        guidance.append("不要引用未定义的变量、方法或属性名；优先复用当前作用域中已经存在的符号。")
    return guidance


def _build_scope_retry_constraints(issue: SonarIssue | None) -> list[str]:
    """Build rule-aware retry constraints for scope violations."""

    if issue is None:
        return [
            "- 只保留 Sonar 指向的那一处修改。",
            "- 不要顺手修改本文件其他相同写法或同类规则的位置。",
            "- 如果这是行级问题，只修改包含 issue 行的那条语句。",
        ]

    policy = get_rule_policy(issue.rule)
    if policy.scope_mode == EXPRESSION_REWRITE_SCOPE_MODE:
        return [
            "- 只保留当前 issue 对应表达式附近的改动，不要顺手修改同方法中的其他表达式。",
            "- 可以在附近引入局部变量、if/else 或语句 lambda，但不要新增类级 private/helper 方法。",
            "- 如果 issue 位于 LINQ Select/匿名对象初始化中，优先改写成语句 lambda，并在 lambda 内 return 原对象。",
        ]

    if policy.scope_mode == LOOP_REWRITE_SCOPE_MODE:
        return [
            "- 只重写当前 foreach/for/while 语句块，不要顺手改同方法中的其他循环。",
            "- 如果循环后紧跟着与该查找/过滤逻辑配套的 return 或 throw，可以一并改写；不要扩展到后续无关语句。",
            "- 优先保持语义等价，再考虑用 Any、FirstOrDefault、Where 等 LINQ 形式替换循环。",
        ]

    return [
        "- 只保留 Sonar 指向的那一处修改。",
        "- 不要顺手修改本文件其他相同写法或同类规则的位置。",
        "- 如果这是行级问题，只修改包含 issue 行的那条语句。",
    ]


def _extract_scope_violation_context(
    raw_output: str,
    issue: SonarIssue | None,
) -> ScopeViolationContext | None:
    """Extract structured scope violation details from raw build output."""

    if "Issue changes exceeded the allowed Sonar edit scope." not in raw_output:
        return None

    allowed_lines_match = re.search(r"Allowed lines:\s*(.+)", raw_output)
    changed_lines_match = re.search(r"Changed lines outside scope:\s*(.+)", raw_output)
    return ScopeViolationContext(
        raw_output=raw_output,
        allowed_lines=(allowed_lines_match.group(1).strip() if allowed_lines_match else ""),
        changed_lines_outside_scope=(
            changed_lines_match.group(1).strip() if changed_lines_match else ""
        ),
        constraints=tuple(_build_scope_retry_constraints(issue)),
    )


def _extract_review_failure_context(result: FixResult) -> ReviewFailureContext | None:
    """Extract structured diff-review rejection details from a FixResult."""

    reviewer_result = getattr(result, "reviewer_result", None)
    if not isinstance(reviewer_result, dict):
        return None
    if str(reviewer_result.get("status", "")).strip().lower() != "retry":
        return None

    violations = tuple(
        ReviewViolationContext(
            violation_type=str(item.get("type", "")).strip(),
            file=str(item.get("file", "")).strip(),
            reason=str(item.get("reason", "")).strip(),
            changed_lines=tuple(
                int(line)
                for line in item.get("changed_lines", [])
                if str(line).strip()
            ),
            evidence_hunk=str(item.get("evidence_hunk", "")).strip(),
        )
        for item in reviewer_result.get("violations", [])
        if isinstance(item, dict)
    )
    constraints = (
        "只保留完成当前 Sonar issue 所必需的修改。",
        "不要触碰 Edit Contract 之外的文件或无关代码行。",
        "把相邻问题记录到 follow-up，而不是混入当前 patch。",
    )
    raw_output = str(result.build_output or "").strip()
    summary = str(reviewer_result.get("summary", "")).strip() or "Diff reviewer rejected the patch."
    if not raw_output:
        raw_output = summary
    return ReviewFailureContext(
        raw_output=raw_output,
        summary=summary,
        constraints=constraints,
        violations=violations,
    )


def build_retry_context(
    workspace_path: Path,
    result: FixResult,
    issue: SonarIssue | None = None,
    *,
    source_attempt_number: int = 0,
) -> RetryContext:
    """Build structured retry memory for the next model attempt."""

    raw_output = str(result.build_output or "").strip()
    compiler_errors = tuple(_extract_compiler_errors(workspace_path, result.build_output))
    return RetryContext(
        source_attempt_number=source_attempt_number,
        failure_kind=result.failure_kind,
        error=result.error or "",
        summary=result.summary,
        build_command=result.build_command,
        raw_output=raw_output,
        retryable_failure=result.retryable_failure,
        build_verification_failed=result.build_verification_failed,
        changed_files=_normalize_changed_files(result),
        compiler_errors=compiler_errors,
        guidance=tuple(_build_retry_guidance(list(compiler_errors))) if compiler_errors else (),
        scope_violation=_extract_scope_violation_context(raw_output, issue),
        review_failure=_extract_review_failure_context(result),
        model_timeout_summary=_summarize_model_timeout(raw_output) if result.failure_kind == "model_timeout" else "",
        build_tool_failed=result.failure_kind == "build_tool",
        forbidden_tool_failed=result.failure_kind == "forbidden_tool",
        model_timeout_failed=result.failure_kind == "model_timeout",
    )


def build_retry_feedback(
    workspace_path: Path,
    result: FixResult,
    issue: SonarIssue | None = None,
) -> str:
    """Build concise retry feedback for the next model attempt."""

    return render_retry_context(build_retry_context(workspace_path, result, issue))


def _summarize_model_timeout(raw_output: str) -> str:
    """Summarize the timeout mode for retry feedback."""

    if "没有返回后续响应" in raw_output:
        return "在等待模型后续响应时超时"
    if "单个 issue 在" in raw_output:
        return "在单个 issue 执行过程中超时"
    return "在等待模型首响应时超时"


def _build_final_skip_reason(result: FixResult, attempts: int) -> str:
    """Build a stable final skip reason after retries are exhausted."""

    if result.failure_kind == "build":
        return f"Build verification failed after {attempts} attempt(s)"
    if result.failure_kind == "scope":
        return f"Issue changes exceeded allowed scope after {attempts} attempt(s)"
    if result.failure_kind == "reviewer":
        return f"Diff reviewer rejected the patch after {attempts} attempt(s)"
    if result.failure_kind == "no_change":
        return f"Agent completed without modifying any files after {attempts} attempt(s)"
    if result.failure_kind == "rule_validation":
        return f"Rule-specific validation failed after {attempts} attempt(s)"
    if result.failure_kind == "build_tool":
        return f"Build tool execution failed after {attempts} attempt(s)"
    if result.failure_kind == "forbidden_tool":
        return f"Forbidden tool usage polluted the issue attempt after {attempts} attempt(s)"
    if result.failure_kind == "model_timeout":
        return f"Model response timed out after {attempts} attempt(s)"
    return f"Issue fix failed after {attempts} attempt(s)"


def _run_git_command(
    workspace_path: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command inside the workspace with stable decoding."""

    result = subprocess.run(
        ["git", *args],
        cwd=str(workspace_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if check and result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Git command failed ({' '.join(args)}): {error_text}")
    return result


def capture_workspace_baseline(
    workspace_path: Path,
    *,
    repository: str,
    issue_key: str,
    run_label: str,
    snapshot_root: str = "logs/issue_snapshots",
) -> WorkspaceBaseline:
    """Capture the workspace state so the current issue can be rolled back safely."""

    snapshot_dir = Path(snapshot_root) / f"{_sanitize_name(repository)}_{_sanitize_name(issue_key)}_{run_label}"
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    patch_path = snapshot_dir / "tracked.patch"
    tracked_root = snapshot_dir / "tracked"
    tracked_root.mkdir(parents=True, exist_ok=True)
    untracked_root = snapshot_dir / "untracked"
    untracked_root.mkdir(parents=True, exist_ok=True)
    head_commit_result = _run_git_command(workspace_path, ["rev-parse", "HEAD"])
    head_commit = (head_commit_result.stdout or "").strip()

    diff_result = _run_git_command(workspace_path, ["diff", "--binary", "--no-color", "HEAD"])
    patch_path.write_text(diff_result.stdout or "", encoding="utf-8")
    tracked_result = _run_git_command(
        workspace_path,
        ["diff", "--name-only", "--diff-filter=ACDMRTUXB", "-z", "HEAD"],
    )
    tracked_files = tuple(path for path in (tracked_result.stdout or "").split("\0") if path)
    for rel_path in tracked_files:
        source = workspace_path / rel_path
        if source.is_file():
            target = tracked_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    untracked_result = _run_git_command(
        workspace_path,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    untracked_files = tuple(path for path in (untracked_result.stdout or "").split("\0") if path)
    for rel_path in untracked_files:
        source = workspace_path / rel_path
        if source.is_file():
            target = untracked_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    return WorkspaceBaseline(
        head_commit=head_commit,
        snapshot_dir=snapshot_dir,
        patch_path=patch_path,
        tracked_root=tracked_root,
        tracked_files=tracked_files,
        untracked_root=untracked_root,
        untracked_files=untracked_files,
    )


def restore_workspace_baseline(workspace_path: Path, baseline: WorkspaceBaseline) -> None:
    """Restore the workspace to the baseline captured before the issue attempt."""

    _run_git_command(workspace_path, ["reset", "--hard", baseline.head_commit])
    _run_git_command(workspace_path, ["clean", "-fd"])

    patch_text = baseline.patch_path.read_text(encoding="utf-8") if baseline.patch_path.exists() else ""
    if patch_text.strip():
        apply_result = subprocess.run(
            ["git", "apply", "--binary", str(baseline.patch_path.resolve())],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if apply_result.returncode != 0:
            error_text = (apply_result.stderr or apply_result.stdout or "").strip()
            raise RuntimeError(f"Git apply failed while restoring issue baseline: {error_text}")

    for rel_path in baseline.untracked_files:
        source = baseline.untracked_root / rel_path
        if source.is_file():
            target = workspace_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def cleanup_workspace_baseline(baseline: WorkspaceBaseline) -> None:
    """Remove temporary baseline snapshot files."""

    if baseline.snapshot_dir.exists():
        shutil.rmtree(baseline.snapshot_dir, ignore_errors=True)


class IssueAttemptLogger:
    """Simple file logger for issue attempts."""

    def __init__(
        self,
        *,
        repository: str,
        issue_key: str,
        run_label: str,
        log_root: str = "logs/issue_attempts",
    ) -> None:
        log_dir = Path(log_root)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{_sanitize_name(repository)}_{_sanitize_name(issue_key)}_{run_label}.log"
        self.log_path = log_dir / file_name

    def write(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")


def _invoke_fix_issue(
    agent: ClaudeFixAgent,
    issue: SonarIssue,
    workspace_path: Path,
    build_command: str,
    *,
    retry_feedback: str,
    retry_context: RetryContext | None,
) -> FixResult:
    """Invoke agent.fix_issue while remaining compatible with legacy test doubles."""

    fix_issue_params = signature(agent.fix_issue).parameters
    if "retry_context" in fix_issue_params:
        return agent.fix_issue(
            issue,
            workspace_path,
            build_command,
            retry_feedback=retry_feedback,
            retry_context=retry_context,
        )
    return agent.fix_issue(
        issue,
        workspace_path,
        build_command,
        retry_feedback=retry_feedback,
    )


def _normalize_changed_files(result: FixResult) -> tuple[str, ...]:
    """Extract normalized changed files from a FixResult."""

    return tuple(
        str(change.get("file", "")).replace("\\", "/").lstrip("/")
        for change in result.changes
        if str(change.get("file", "")).strip()
    )


def _determine_attempt_status(
    result: FixResult,
    *,
    attempt: int,
    max_build_retries: int,
) -> AttemptStatus:
    """Map the fix result to a structured attempt status."""

    if result.success:
        return AttemptStatus.SUCCEEDED

    should_retry = result.retryable_failure or result.build_verification_failed
    if should_retry and attempt < max_build_retries:
        return AttemptStatus.RETRYING
    if result.skipped:
        return AttemptStatus.SKIPPED
    return AttemptStatus.FAILED


def _determine_retry_reason(result: FixResult) -> RetryReason:
    """Map retry-related flags to a retry reason enum."""

    if result.build_verification_failed:
        return RetryReason.BUILD_VERIFICATION_FAILED
    if result.retryable_failure:
        return RetryReason.RETRYABLE_FAILURE
    return RetryReason.NONE


def _build_attempt_state(
    *,
    attempt: int,
    max_build_retries: int,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    result: FixResult,
    artifact_dir: str,
) -> AttemptState:
    """Build a structured attempt state from a FixResult."""

    return AttemptState(
        attempt_number=attempt,
        status=_determine_attempt_status(
            result,
            attempt=attempt,
            max_build_retries=max_build_retries,
        ),
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(duration_seconds, 3),
        failure_kind=result.failure_kind,
        retry_reason=_determine_retry_reason(result),
        retryable_failure=result.retryable_failure,
        build_passed=result.build_passed,
        build_verification_failed=result.build_verification_failed,
        error=result.error or "",
        skip_reason=result.skip_reason,
        summary=result.summary,
        changed_files=_normalize_changed_files(result),
        issue_log_path=result.issue_log_path,
        artifact_dir=artifact_dir,
    )


def _build_issue_state(
    *,
    issue: SonarIssue,
    repository: str,
    run_label: str,
    result: FixResult,
    attempts: list[AttemptState],
    artifact_root: str,
) -> IssueState:
    """Build the final issue state after retries are complete."""

    if result.success:
        status = IssueStatus.FIXED
    elif result.skipped:
        status = IssueStatus.SKIPPED
    else:
        status = IssueStatus.FAILED

    return IssueState(
        issue_key=issue.key,
        repository=repository,
        run_label=run_label,
        rule_id=issue.rule,
        file_path=issue.file_path,
        line=issue.line,
        status=status,
        attempts=tuple(attempts),
        final_failure_kind=result.failure_kind,
        final_error=result.error or "",
        final_skip_reason=result.skip_reason,
        final_summary=result.summary,
        artifact_root=artifact_root,
    )


def process_issue_with_retries(
    *,
    agent: ClaudeFixAgent,
    issue: SonarIssue,
    workspace_path: Path,
    build_command: str,
    repository: str,
    run_label: str,
    author: str = "",
    project_key: str = "",
    state_store: RunStateStore | None = None,
    max_build_retries: int = DEFAULT_MAX_BUILD_RETRIES,
) -> FixResult:
    """Retry a single issue fix on build failure, then roll back only that issue if needed."""

    logger = IssueAttemptLogger(repository=repository, issue_key=issue.key, run_label=run_label)
    artifact_writer = ArtifactWriter()
    follow_up_store = FollowUpStore()
    issue_artifact_root = _build_issue_artifact_root(repository, run_label, issue.key)
    baseline = capture_workspace_baseline(
        workspace_path,
        repository=repository,
        issue_key=issue.key,
        run_label=run_label,
    )
    retry_context: RetryContext | None = None
    retry_feedback = ""
    attempt_states: list[AttemptState] = []

    def write_attempt_artifacts(
        *,
        attempt: int,
        started_at: str,
        finished_at: str,
        duration_seconds: float,
        result: FixResult,
    ) -> AttemptState:
        attempt_root = issue_artifact_root / f"attempt-{attempt:02d}"
        attempt_state = _build_attempt_state(
            attempt=attempt,
            max_build_retries=max_build_retries,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            result=result,
            artifact_dir=attempt_root.as_posix(),
        )
        artifact_writer.write_attempt_artifacts(
            repository=repository,
            run_label=run_label,
            issue=issue,
            attempt_state=attempt_state,
            result=result,
            workspace_path=workspace_path,
            baseline=baseline,
            build_command=build_command,
            retry_feedback=retry_feedback,
            retry_context=retry_context,
        )
        attempt_states.append(attempt_state)
        return attempt_state

    def finalize_result(result: FixResult) -> FixResult:
        result.artifact_root = issue_artifact_root.as_posix()
        issue_state = _build_issue_state(
            issue=issue,
            repository=repository,
            run_label=run_label,
            result=result,
            attempts=attempt_states,
            artifact_root=result.artifact_root,
        )
        result.issue_state = issue_state
        artifact_writer.write_issue_state(issue_state)
        if state_store is not None:
            state_store.record_issue_state(
                issue_state,
                author=author,
                project_key=project_key,
            )
        return result

    try:
        for attempt in range(1, max_build_retries + 1):
            attempt_started_at = utc_now_iso()
            attempt_started_monotonic = time.monotonic()
            logger.write(f"Attempt {attempt}/{max_build_retries} started for {issue.key}")
            if state_store is not None:
                state_store.record_attempt_started(
                    run_label=run_label,
                    repository=repository,
                    author=author,
                    project_key=project_key,
                    issue_key=issue.key,
                    attempt_number=attempt,
                    build_command=build_command,
                    retry_context=retry_context.to_dict() if retry_context else None,
                )
            result = _invoke_fix_issue(
                agent,
                issue,
                workspace_path,
                build_command,
                retry_feedback=retry_feedback,
                retry_context=retry_context,
            )
            result.attempts = attempt
            result.issue_log_path = str(logger.log_path)
            queue_path = follow_up_store.append(
                repository=repository,
                run_label=run_label,
                issue_key=issue.key,
                follow_ups=getattr(result, "follow_ups", ()),
            )
            if queue_path is not None:
                result.follow_up_log_path = queue_path.as_posix()
            attempt_finished_at = utc_now_iso()
            attempt_duration_seconds = time.monotonic() - attempt_started_monotonic

            if result.success:
                logger.write(f"Attempt {attempt} succeeded: {result.summary}")
                attempt_state = write_attempt_artifacts(
                    attempt=attempt,
                    started_at=attempt_started_at,
                    finished_at=attempt_finished_at,
                    duration_seconds=attempt_duration_seconds,
                    result=result,
                )
                if state_store is not None:
                    state_store.record_attempt_state(
                        attempt_state,
                        run_label=run_label,
                        repository=repository,
                        author=author,
                        project_key=project_key,
                        issue_key=issue.key,
                    )
                return finalize_result(result)

            if result.skipped and result.failure_kind == "policy_skip":
                logger.write(f"Issue skipped by policy: {result.skip_reason or result.error or 'policy skip'}")
                attempt_state = write_attempt_artifacts(
                    attempt=attempt,
                    started_at=attempt_started_at,
                    finished_at=attempt_finished_at,
                    duration_seconds=attempt_duration_seconds,
                    result=result,
                )
                if state_store is not None:
                    state_store.record_attempt_state(
                        attempt_state,
                        run_label=run_label,
                        repository=repository,
                        author=author,
                        project_key=project_key,
                        issue_key=issue.key,
                    )
                return finalize_result(result)

            logger.write(f"Attempt {attempt} failed: {result.error or 'unknown error'}")
            failure_report = ""
            if result.build_output:
                failure_report = format_build_failure_report(
                    {
                        "error": result.error or "",
                        "build_command": result.build_command,
                        "build_output": result.build_output,
                    },
                    max_lines=40,
                )
                if failure_report:
                    logger.write("Build failure report:\n" + failure_report)

            next_retry_context = build_retry_context(
                workspace_path,
                result,
                issue,
                source_attempt_number=attempt,
            )
            next_retry_feedback = render_retry_context(next_retry_context)
            attempt_state = write_attempt_artifacts(
                attempt=attempt,
                started_at=attempt_started_at,
                finished_at=attempt_finished_at,
                duration_seconds=attempt_duration_seconds,
                result=result,
            )
            if state_store is not None:
                state_store.record_attempt_state(
                    attempt_state,
                    run_label=run_label,
                    repository=repository,
                    author=author,
                    project_key=project_key,
                    issue_key=issue.key,
                )
            restore_workspace_baseline(workspace_path, baseline)
            logger.write("Workspace restored to issue baseline")

            should_retry = result.retryable_failure or result.build_verification_failed
            if should_retry and attempt < max_build_retries:
                retry_context = next_retry_context
                retry_feedback = next_retry_feedback or failure_report or (result.error or "")
                logger.write(f"Retrying issue after recoverable failure, next attempt: {attempt + 1}")
                continue

            if should_retry:
                result.skip_reason = _build_final_skip_reason(result, attempt)
            else:
                result.skip_reason = result.error or "Issue fix failed"
            result.error = result.skip_reason
            result.skipped = True
            logger.write(f"Issue skipped: {result.skip_reason}")
            final_attempt_state = _build_attempt_state(
                attempt=attempt,
                max_build_retries=max_build_retries,
                started_at=attempt_started_at,
                finished_at=attempt_finished_at,
                duration_seconds=attempt_duration_seconds,
                result=result,
                artifact_dir=(issue_artifact_root / f"attempt-{attempt:02d}").as_posix(),
            )
            attempt_states[-1] = final_attempt_state
            artifact_writer.write_attempt_artifacts(
                repository=repository,
                run_label=run_label,
                issue=issue,
                attempt_state=final_attempt_state,
                result=result,
                workspace_path=workspace_path,
                baseline=baseline,
                build_command=build_command,
                retry_feedback=retry_feedback,
                retry_context=retry_context,
            )
            if state_store is not None:
                state_store.record_attempt_state(
                    final_attempt_state,
                    run_label=run_label,
                    repository=repository,
                    author=author,
                    project_key=project_key,
                    issue_key=issue.key,
                )
            return finalize_result(result)

        skipped = FixResult(
            success=False,
            issue_key=issue.key,
            file_path=issue.file_path,
            error=f"Build verification failed after {max_build_retries} attempt(s)",
            attempts=max_build_retries,
            skipped=True,
            skip_reason=f"Build verification failed after {max_build_retries} attempt(s)",
            issue_log_path=str(logger.log_path),
        )
        logger.write(f"Issue skipped: {skipped.skip_reason}")
        return finalize_result(skipped)
    finally:
        cleanup_workspace_baseline(baseline)
