"""Per-issue retry and rollback helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
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
from pi_sonar_agent.fixers.build_gate import format_build_failure_report

DEFAULT_MAX_BUILD_RETRIES = 3


@dataclass(frozen=True)
class WorkspaceBaseline:
    """Snapshot of the workspace state before an issue attempt starts."""

    head_commit: str
    snapshot_dir: Path
    patch_path: Path
    untracked_root: Path
    untracked_files: tuple[str, ...]


@dataclass(frozen=True)
class CompilerErrorContext:
    """Structured compiler error with optional source snippet."""

    file_path: str
    line: int
    column: int
    code: str
    message: str
    snippet: str = ""


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "issue"


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


def build_retry_feedback(
    workspace_path: Path,
    result: FixResult,
    issue: SonarIssue | None = None,
) -> str:
    """Build concise retry feedback for the next model attempt."""

    errors = _extract_compiler_errors(workspace_path, result.build_output)
    raw_output = str(result.build_output or "").strip()
    has_scope_violation = "Issue changes exceeded the allowed Sonar edit scope." in raw_output
    build_tool_failed = result.failure_kind == "build_tool"
    forbidden_tool_failed = result.failure_kind == "forbidden_tool"
    model_timeout_failed = result.failure_kind == "model_timeout"
    if not errors:
        if raw_output:
            if model_timeout_failed:
                return "\n".join(
                    [
                        "上次尝试在等待模型首响应时超时，请先确认当前模型网关或 provider 可正常返回工具调用响应：",
                        raw_output,
                        "重试约束:",
                        "- 不要更换构建命令，先确认模型能正常返回第一条消息。",
                        "- 如果连续超时，优先检查 .env 中的模型 endpoint、token 和 provider 兼容性。",
                    ]
                )
            if forbidden_tool_failed:
                sections = [
                    "上次尝试使用了被禁止的工具，或污染了当前 issue 的 Git 基线；本次必须严格按下面约束重试：",
                    raw_output,
                    "重试约束:",
                    "- 严禁使用 Bash、git_add、git_commit、git_push 或任何自行提交/推送动作。",
                    "- 修复阶段只能直接编辑代码并运行推荐构建命令，提交由外层流程统一处理。",
                    "- 先根据下面的本地构建输出修复问题，再重新运行推荐构建命令验证。",
                ]
                if has_scope_violation:
                    sections.extend(
                        [
                            "范围约束:",
                            *_build_scope_retry_constraints(issue),
                        ]
                    )
                return "\n".join(sections)
            if build_tool_failed:
                sections = [
                    "上次尝试在运行构建工具时异常退出，请先处理下面的异常信息和本地回退构建输出：",
                    raw_output,
                    "重试约束:",
                    "- 先修复 stderr 或回退构建输出中暴露的问题，再重新运行推荐构建命令。",
                    "- 如果回退构建已经通过，也要再次运行构建，确认修改后的代码仍然稳定。",
                ]
                if has_scope_violation:
                    sections.extend(
                        [
                            "范围约束:",
                            *_build_scope_retry_constraints(issue),
                        ]
                    )
                return "\n".join(sections)
            if has_scope_violation:
                return "\n".join(
                    [
                        "上次尝试修改了 Sonar 指定范围之外的代码，请严格缩小修改范围：",
                        raw_output,
                        "重试约束:",
                        *_build_scope_retry_constraints(issue),
                    ]
                )
            return raw_output
        if result.error == "Agent completed without modifying any files":
            return "\n".join(
                [
                    "上次尝试没有实际修改任何文件。",
                    "这次必须对 Sonar 指向的代码真正落盘修改，然后再运行构建验证。",
                    "重试约束:",
                    "- 不要只做分析或解释，必须提交实际代码修改。",
                    "- 修改后立即使用推荐构建命令验证。",
                    "- 如果这是行级问题，只修改 Sonar 指向的那条语句。",
                ]
            )
        return result.error or ""

    sections: list[str] = []
    if model_timeout_failed:
        sections.append("上次尝试在等待模型首响应时超时。")
        sections.append("先确认当前模型 provider/网关与 Claude SDK 工具调用协议兼容，再继续重试。")
    if forbidden_tool_failed:
        sections.append("上次尝试使用了被禁止的工具，或污染了当前 issue 的 Git 基线。")
        sections.append("这次严禁使用 Bash、git_add、git_commit、git_push；只允许直接编辑代码并运行推荐构建命令。")
    if build_tool_failed:
        sections.append("上次尝试在运行构建工具时异常退出；已附加本地回退构建结果。")
    sections.append("上次尝试引入了以下关键编译错误，请先修复这些错误：")
    for index, item in enumerate(errors[:12], start=1):
        sections.append(
            f"{index}. {item.code} at {item.file_path}:{item.line}:{item.column}\n"
            f"   错误信息: {item.message}"
        )
        if item.snippet:
            sections.append(f"   出错代码片段:\n{item.snippet}")

    guidance = _build_retry_guidance(errors)
    if guidance:
        sections.append("重试约束:")
        sections.extend(f"- {item}" for item in guidance)

    if has_scope_violation:
        sections.extend(
            [
                "另外，上次修改还越过了 Sonar 允许范围：",
                raw_output,
                "范围约束:",
                *_build_scope_retry_constraints(issue),
            ]
        )

    return "\n".join(sections)


def _build_final_skip_reason(result: FixResult, attempts: int) -> str:
    """Build a stable final skip reason after retries are exhausted."""

    if result.failure_kind == "build":
        return f"Build verification failed after {attempts} attempt(s)"
    if result.failure_kind == "scope":
        return f"Issue changes exceeded allowed scope after {attempts} attempt(s)"
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
    untracked_root = snapshot_dir / "untracked"
    untracked_root.mkdir(parents=True, exist_ok=True)
    head_commit_result = _run_git_command(workspace_path, ["rev-parse", "HEAD"])
    head_commit = (head_commit_result.stdout or "").strip()

    diff_result = _run_git_command(workspace_path, ["diff", "--binary", "--no-color", "HEAD"])
    patch_path.write_text(diff_result.stdout or "", encoding="utf-8")

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


def process_issue_with_retries(
    *,
    agent: ClaudeFixAgent,
    issue: SonarIssue,
    workspace_path: Path,
    build_command: str,
    repository: str,
    run_label: str,
    max_build_retries: int = DEFAULT_MAX_BUILD_RETRIES,
) -> FixResult:
    """Retry a single issue fix on build failure, then roll back only that issue if needed."""

    logger = IssueAttemptLogger(repository=repository, issue_key=issue.key, run_label=run_label)
    baseline = capture_workspace_baseline(
        workspace_path,
        repository=repository,
        issue_key=issue.key,
        run_label=run_label,
    )
    retry_feedback = ""

    try:
        for attempt in range(1, max_build_retries + 1):
            logger.write(f"Attempt {attempt}/{max_build_retries} started for {issue.key}")
            result = agent.fix_issue(
                issue,
                workspace_path,
                build_command,
                retry_feedback=retry_feedback,
            )
            result.attempts = attempt
            result.issue_log_path = str(logger.log_path)

            if result.success:
                logger.write(f"Attempt {attempt} succeeded: {result.summary}")
                return result

            if result.skipped and result.failure_kind == "policy_skip":
                logger.write(f"Issue skipped by policy: {result.skip_reason or result.error or 'policy skip'}")
                return result

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

            next_retry_feedback = build_retry_feedback(workspace_path, result, issue)
            restore_workspace_baseline(workspace_path, baseline)
            logger.write("Workspace restored to issue baseline")

            should_retry = result.retryable_failure or result.build_verification_failed
            if should_retry and attempt < max_build_retries:
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
            return result

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
        return skipped
    finally:
        cleanup_workspace_baseline(baseline)
