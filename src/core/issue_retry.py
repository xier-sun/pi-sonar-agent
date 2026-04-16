"""Per-issue retry and rollback helpers."""

from __future__ import annotations

from dataclasses import replace
import hashlib
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
from pi_sonar_agent.core.failure_fingerprint import detect_failure_fingerprints
from pi_sonar_agent.core.fix_verifier import FixVerifier
from pi_sonar_agent.core.follow_up_store import FollowUpStore
from pi_sonar_agent.core.lessons_store import LessonsStore
from pi_sonar_agent.core.quality_gate import build_compliance_summary
from pi_sonar_agent.core.retry_context import (
    BoundaryFailureContext,
    CompilerErrorContext,
    PlanFailureContext,
    QualityGateFailureContext,
    QualityGateViolationContext,
    RetryContext,
    ReviewGateDecisionContext,
    ReviewGateFailureContext,
    ReviewFailureContext,
    ReviewViolationContext,
    SemanticPrecheckFailureContext,
    SemanticPrecheckFindingContext,
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
    summarize_issue_performance,
    utc_now_iso,
)
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.fixers.build_gate import format_build_failure_report

DEFAULT_MAX_BUILD_RETRIES = 5
EARLY_RETRY_ABORT_MIN_ATTEMPTS = 5
EARLY_RETRY_ABORT_MIN_ATTEMPTS_NO_CHANGE = 2
EARLY_RETRY_ABORT_MIN_ATTEMPTS_FIRST_RESPONSE_TIMEOUT = 2
EARLY_RETRY_ABORT_MIN_ATTEMPTS_TOOL_INPUT_INVALID = 2
EXTENDED_BUILD_TIMEOUT_SECONDS = 600
PROMPT_SAFE_BUILD_REPORT_MAX_LINES = 24
RETRY_RUNTIME_MAX_TAIL_LINES = 120


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


def _find_callee_declaration_snippet(
    file_path: Path,
    call_line: int,
    *,
    radius: int = 4,
) -> str:
    """Find the local declaration of a method referenced on the failing call line."""

    if not file_path.exists():
        return ""
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    if call_line < 1 or call_line > len(lines):
        return ""

    call_text = lines[call_line - 1]
    if not call_text.strip():
        return ""

    ignored_names = {
        "if",
        "for",
        "foreach",
        "while",
        "switch",
        "catch",
        "using",
        "lock",
        "return",
        "new",
    }
    candidate_names: list[str] = []
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", call_text):
        method_name = match.group(1)
        if method_name.lower() in ignored_names:
            continue
        if method_name not in candidate_names:
            candidate_names.append(method_name)

    if not candidate_names:
        return ""

    for method_name in candidate_names:
        declaration_pattern = re.compile(
            rf"\b(?:private|public|internal|protected)\b.*\b{re.escape(method_name)}\s*\(",
            re.IGNORECASE,
        )
        for index, line_text in enumerate(lines, start=1):
            if index == call_line:
                continue
            if method_name not in line_text:
                continue
            if declaration_pattern.search(line_text):
                return _read_error_snippet(file_path, index, radius=radius)
    return ""


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

        snippet = _read_error_snippet(Path(file_path), line)
        if code in {"CS1503", "CS0029"}:
            callee_snippet = _find_callee_declaration_snippet(Path(file_path), line)
            if callee_snippet:
                snippet = (
                    f"{snippet}\n\n--- 被调方法声明 ---\n{callee_snippet}"
                    if snippet
                    else callee_snippet
                )

        errors.append(
            CompilerErrorContext(
                file_path=file_path,
                line=line,
                column=column,
                code=code,
                message=message,
                snippet=snippet,
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
        guidance.append("如果提取 helper 需要带出多个外层局部变量，回退为原方法内重构，或把依赖显式参数化后再提取。")
    if "CS1061" in codes:
        guidance.append("不要留下不完整的成员重命名或类型迁移；先修复调用点与定义的成员名/类型不一致。")
        guidance.append("如果新的 helper 或中间类型导致成员访问链断裂，回退为更小的原方法内重构。")
    if codes.intersection({"CS0535", "CS0738"}):
        guidance.append("不要留下公开方法与接口/抽象契约不一致的半成品重命名；如果传播闭包不完整，优先恢复现有公开签名。")
        guidance.append("如果必须保留 Async 重命名，必须同步接口声明、实现类签名、调用点和 nameof(...)。")
    if codes.intersection({"CS1503", "CS0029"}):
        guidance.append(
            "提取 helper 方法时，参数类型和返回值类型必须与调用点实际变量类型完全一致，尤其要保留 nullable 标注。"
        )
        guidance.append(
            "不要把 decimal? 写成 decimal，不要把 DateTime? 写成 DateTime，也不要把包含 nullable 成员的 ValueTuple 简化成 non-nullable。"
        )
        guidance.append(
            "请先用 Read 工具检查调用点变量的声明类型，再修正 helper 方法的参数签名和返回值类型。"
        )
    if "CS0029" in codes:
        guidance.append("类型转换错误通常意味着 helper 返回值类型或赋值目标类型不一致；先对齐两边的完整类型。")

    file_counts: dict[str, int] = {}
    for item in errors:
        normalized_path = str(item.file_path or "").strip().lower()
        if not normalized_path:
            continue
        file_counts[normalized_path] = file_counts.get(normalized_path, 0) + 1
    if any(count >= 2 for count in file_counts.values()):
        guidance.append(
            "如果你提取了新的 private helper 方法，请用 Read 工具检查该 helper 的完整签名，确认每个参数类型和返回值类型都与调用点完全匹配。"
        )

    return list(dict.fromkeys(item for item in guidance if str(item).strip()))


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


def _tail_text(text: str, max_lines: int) -> str:
    """Return a bounded tail for large logs."""

    normalized = str(text or "").strip()
    if not normalized:
        return ""
    lines = normalized.splitlines()
    if len(lines) <= max_lines:
        return normalized
    omitted = len(lines) - max_lines
    return f"... (省略前 {omitted} 行)\n" + "\n".join(lines[-max_lines:])


def _looks_like_timeout_output(output: str) -> bool:
    text = str(output or "").lower()
    return "timed out after" in text or "timeoutexpired" in text


def _extract_build_totals(output: str) -> tuple[int | None, int | None, str]:
    """Extract error/warning counters and elapsed time from mixed-language build logs."""

    text = str(output or "")
    error_count: int | None = None
    warning_count: int | None = None
    elapsed = ""

    for pattern in (
        r"(?P<count>\d+)\s+个错误",
        r"(?P<count>\d+)\s+Error\(s\)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            error_count = int(match.group("count"))
            break

    for pattern in (
        r"(?P<count>\d+)\s+个警告",
        r"(?P<count>\d+)\s+Warning\(s\)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            warning_count = int(match.group("count"))
            break

    for pattern in (
        r"已用时间\s+(?P<elapsed>[0-9:.]+)",
        r"Time Elapsed\s+(?P<elapsed>[0-9:.]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            elapsed = str(match.group("elapsed") or "").strip()
            break

    return error_count, warning_count, elapsed


def _build_prompt_safe_output(
    result: FixResult,
    *,
    compiler_errors: tuple[CompilerErrorContext, ...],
) -> tuple[str, bool, bool]:
    """Build model-facing retry text without dumping raw build logs back into the prompt."""

    raw_output = str(result.build_output or "").strip()
    if not raw_output:
        return "", False, False

    build_timeout_failed = (
        result.failure_kind == "build"
        and result.build_verification_failed
        and _looks_like_timeout_output(raw_output)
    )
    error_count, warning_count, elapsed = _extract_build_totals(raw_output)
    build_timeout_without_errors = build_timeout_failed and not compiler_errors and error_count == 0

    if result.failure_kind == "build":
        if compiler_errors:
            return "", build_timeout_failed, build_timeout_without_errors

        if build_timeout_without_errors:
            sections = [
                "上次尝试不是明确编译错误，而是本地构建验证超时。",
                f"构建命令: {result.build_command or 'dotnet build'}",
            ]
            if elapsed:
                sections.append(f"日志中的已用时间: {elapsed}")
            if warning_count is not None:
                sections.append(f"日志统计: {warning_count} 个警告")
            sections.append("日志中没有发现明确的编译错误；不要因为仓库现存 warning 盲目大改代码。")
            sections.append("优先保持当前 patch 语义稳定；如果需要查看更多上下文，只按需 Read 本地摘要或 tail 日志。")
            return "\n".join(sections), build_timeout_failed, build_timeout_without_errors

        build_report = format_build_failure_report(
            {
                "error": result.error or "",
                "build_command": result.build_command,
                "build_output": raw_output,
            },
            max_lines=PROMPT_SAFE_BUILD_REPORT_MAX_LINES,
        ).strip()
        return build_report or _tail_text(raw_output, PROMPT_SAFE_BUILD_REPORT_MAX_LINES), build_timeout_failed, build_timeout_without_errors

    return "", build_timeout_failed, build_timeout_without_errors


def _materialize_retry_workspace_files(
    *,
    workspace_path: Path,
    issue_key: str,
    retry_context: RetryContext,
) -> RetryContext:
    """Write model-readable retry artifacts inside the workspace after baseline restore."""

    if not workspace_path.exists() or retry_context.failure_kind != "build":
        return retry_context

    retry_root = workspace_path / ".pi-sonar-agent-runtime" / "retry" / _sanitize_name(issue_key)
    retry_root.mkdir(parents=True, exist_ok=True)

    references: list[str] = []
    summary_text = str(retry_context.prompt_output or "").strip()
    if summary_text:
        summary_path = retry_root / f"attempt-{retry_context.source_attempt_number:02d}-build-summary.txt"
        summary_path.write_text(summary_text + "\n", encoding="utf-8")
        references.append(summary_path.relative_to(workspace_path).as_posix())

    raw_output = str(retry_context.raw_output or "").strip()
    if raw_output:
        tail_path = retry_root / f"attempt-{retry_context.source_attempt_number:02d}-build-tail.log"
        tail_path.write_text(_tail_text(raw_output, RETRY_RUNTIME_MAX_TAIL_LINES) + "\n", encoding="utf-8")
        references.append(tail_path.relative_to(workspace_path).as_posix())

    if not references:
        return retry_context

    return replace(
        retry_context,
        workspace_file_references=tuple(references),
        workspace_read_hint="先看 summary，再按需看 tail；不要一次性读取整份大日志。",
    )


def _should_retry_build_verification_only(
    result: FixResult,
    retry_context: RetryContext,
) -> bool:
    """Detect build timeouts that should be retried as verification, not code edits."""

    return bool(
        result.failure_kind == "build"
        and result.build_verification_failed
        and retry_context.build_timeout_without_errors
        and _normalize_changed_files(result)
    )


def _retry_build_verification_with_extended_timeout(
    *,
    workspace_path: Path,
    result: FixResult,
) -> FixResult:
    """Retry local build verification with a larger timeout before asking the model to edit again."""

    build_command = str(result.build_command or "").strip() or "dotnet build"
    build_passed, extended_output = FixVerifier.run_local_build(
        workspace_path,
        build_command,
        timeout_seconds=EXTENDED_BUILD_TIMEOUT_SECONDS,
    )

    performance_metrics = dict(getattr(result, "performance_metrics", {}) or {})
    performance_metrics.update(
        {
            "verification_timeout_recheck_invoked": True,
            "verification_timeout_recheck_timeout_seconds": EXTENDED_BUILD_TIMEOUT_SECONDS,
            "verification_timeout_recheck_passed": build_passed,
        }
    )
    result.performance_metrics = performance_metrics

    original_output = str(result.build_output or "").strip()
    if build_passed:
        result.success = True
        result.build_passed = True
        result.build_verification_failed = False
        result.retryable_failure = False
        result.failure_kind = ""
        result.error = None
        result.skip_reason = ""
        result.build_output = "\n\n".join(
            part
            for part in (
                f"初次构建验证在 {FixVerifier.BUILD_TIMEOUT_SECONDS} 秒内超时；已自动使用 {EXTENDED_BUILD_TIMEOUT_SECONDS} 秒超时重跑并通过。",
                str(extended_output or "").strip(),
            )
            if str(part).strip()
        )
        return result

    result.build_output = "\n\n".join(
        part
        for part in (
            f"初次构建验证在 {FixVerifier.BUILD_TIMEOUT_SECONDS} 秒内超时；已自动使用 {EXTENDED_BUILD_TIMEOUT_SECONDS} 秒超时重跑，但验证仍未通过。",
            _tail_text(original_output, PROMPT_SAFE_BUILD_REPORT_MAX_LINES),
            str(extended_output or "").strip(),
        )
        if str(part).strip()
    )
    return result


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
    violation_types = {item.violation_type for item in violations}
    filesystem_only = violation_types and violation_types.issubset(
        {"forbidden_path", "file_created", "file_deleted"}
    )
    constraints = (
        (
            "只修改工作区内已有的源文件。",
            "不要新建、删除、重命名文件，也不要整文件覆盖写入。",
            "继续使用 Edit 做局部补丁，不要通过工具改坏文件系统边界。",
        )
        if filesystem_only
        else (
            "只保留完成当前 Sonar issue 所必需的修改。",
            "不要触碰 Edit Contract 之外的文件或无关代码行。",
            "把相邻问题记录到 follow-up，而不是混入当前 patch。",
        )
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


def _extract_quality_gate_failure_context(result: FixResult) -> QualityGateFailureContext | None:
    """Extract structured quality-gate failures from a FixResult."""

    payload = getattr(result, "quality_gate_result", None)
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status", "")).strip().lower() != "retry":
        return None

    violations = tuple(
        QualityGateViolationContext(
            rule_id=str(item.get("rule_id", "")).strip(),
            title=str(item.get("title", "")).strip(),
            message=str(item.get("message", "")).strip(),
            file=str(item.get("file", "")).strip(),
            line=int(item.get("line", 0) or 0),
            symbol=str(item.get("symbol", "")).strip(),
            evidence=str(item.get("evidence", "")).strip(),
            retry_hint=str(item.get("retry_hint", "")).strip(),
        )
        for item in payload.get("violations", [])
        if isinstance(item, dict)
    )
    if not violations:
        return None
    return QualityGateFailureContext(
        summary=str(payload.get("summary", "")).strip() or "C# quality gate rejected the patch.",
        violations=violations,
    )


def _extract_review_gate_failure_context(result: FixResult) -> ReviewGateFailureContext | None:
    """Extract structured model-reviewed gate rejections from a FixResult."""

    payload = getattr(result, "review_gate_result", None)
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status", "")).strip().lower() != "retry":
        return None

    findings_by_id: dict[str, dict[str, str | int]] = {}
    for item in payload.get("findings", []):
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id", "")).strip()
        if not finding_id:
            continue
        findings_by_id[finding_id] = {
            "title": str(item.get("title", "")).strip(),
            "source": str(item.get("source", "")).strip(),
            "file": str(item.get("file", "")).strip(),
            "line": int(item.get("line", 0) or 0),
            "evidence": str(item.get("evidence", "")).strip(),
        }

    decisions = tuple(
        ReviewGateDecisionContext(
            finding_id=finding_id,
            title=str(findings_by_id.get(finding_id, {}).get("title", "")).strip(),
            source=str(findings_by_id.get(finding_id, {}).get("source", "")).strip(),
            decision=str(item.get("decision", "")).strip().lower() or "confirm",
            reason=str(item.get("reason", "")).strip(),
            file=str(findings_by_id.get(finding_id, {}).get("file", "")).strip(),
            line=int(findings_by_id.get(finding_id, {}).get("line", 0) or 0),
            evidence=str(findings_by_id.get(finding_id, {}).get("evidence", "")).strip(),
        )
        for item in payload.get("decisions", [])
        if isinstance(item, dict)
        for finding_id in [str(item.get("finding_id", "")).strip()]
        if finding_id
    )

    return ReviewGateFailureContext(
        summary=str(payload.get("summary", "")).strip() or "Review gate rejected the patch.",
        decisions=decisions,
        feedback=tuple(
            str(item).strip()
            for item in payload.get("feedback", [])
            if str(item).strip()
        ),
    )


def _extract_plan_failure_context(result: FixResult) -> PlanFailureContext | None:
    """Extract structured plan-first precheck conflicts from a FixResult."""

    payload = getattr(result, "plan_precheck", None)
    if payload is None:
        return None
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    if not isinstance(payload, dict):
        return None
    if result.failure_kind != "plan_conflict" and not bool(payload.get("blocking")):
        return None
    code = str(payload.get("code", "")).strip()
    if not code:
        return None
    return PlanFailureContext(
        code=code,
        summary=str(payload.get("summary", "")).strip(),
        details=tuple(
            str(item).strip()
            for item in payload.get("details", [])
            if str(item).strip()
        ),
        guidance=tuple(
            str(item).strip()
            for item in payload.get("guidance", [])
            if str(item).strip()
        ),
    )


def _extract_semantic_precheck_failure_context(
    result: FixResult,
) -> SemanticPrecheckFailureContext | None:
    """Extract structured semantic-precheck blockers from a FixResult."""

    payload = getattr(result, "semantic_precheck_result", None)
    if payload is None:
        return None
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status", "")).strip().lower() != "retry":
        return None

    findings = tuple(
        SemanticPrecheckFindingContext(
            finding_id=str(item.get("finding_id", "")).strip(),
            title=str(item.get("title", "")).strip(),
            message=str(item.get("message", "")).strip(),
            file=str(item.get("file", "")).strip(),
            line=int(item.get("line", 0) or 0),
            evidence=str(item.get("evidence", "")).strip(),
            retry_hint=str(item.get("retry_hint", "")).strip(),
        )
        for item in payload.get("findings", [])
        if isinstance(item, dict)
    )
    if not findings:
        return None
    return SemanticPrecheckFailureContext(
        summary=str(payload.get("summary", "")).strip()
        or "Semantic precheck rejected the patch.",
        findings=findings,
    )


def _extract_boundary_failure_context(result: FixResult) -> BoundaryFailureContext | None:
    """Extract structured runtime boundary failure details from a FixResult."""

    code = str(getattr(result, "boundary_failure_code", "")).strip()
    if not code:
        return None
    secondary_codes = tuple(
        str(item).strip()
        for item in getattr(result, "secondary_boundary_failure_codes", ())
        if str(item).strip()
    )
    return BoundaryFailureContext(
        code=code,
        summary=str(getattr(result, "boundary_failure_summary", "")).strip(),
        secondary_codes=secondary_codes,
    )


def _build_failure_detail_key(
    result: FixResult,
    *,
    compiler_errors: tuple[CompilerErrorContext, ...],
    boundary_failure: BoundaryFailureContext | None,
    quality_gate_failure: QualityGateFailureContext | None,
    review_gate_failure: ReviewGateFailureContext | None,
    plan_failure: PlanFailureContext | None,
    semantic_precheck_failure: SemanticPrecheckFailureContext | None,
) -> str:
    if result.failure_kind == "no_change":
        return "no_change"
    if result.failure_kind == "tool_input_invalid":
        normalized_detail = _normalize_invalid_write_tool_input_detail(
            "\n".join(
                part
                for part in (
                    str(result.error or "").strip(),
                    str(result.build_output or "").strip(),
                )
                if part
            )
        )
        if normalized_detail:
            return f"tool_input_invalid:{normalized_detail}"
        return "tool_input_invalid"
    if result.failure_kind == "semantic_precheck" and semantic_precheck_failure is not None:
        finding_ids = tuple(
            str(item.finding_id).strip()
            for item in semantic_precheck_failure.findings
            if str(item.finding_id).strip()
        )
        if finding_ids:
            return "semantic_precheck:" + ",".join(dict.fromkeys(finding_ids))
        return "semantic_precheck"
    if quality_gate_failure is not None and quality_gate_failure.violations:
        return "quality_gate:" + ",".join(
            dict.fromkeys(item.rule_id for item in quality_gate_failure.violations if item.rule_id)
        )
    if review_gate_failure is not None:
        confirmed_findings = tuple(
            item.finding_id
            for item in review_gate_failure.decisions
            if item.finding_id and item.decision != "waive"
        )
        if confirmed_findings:
            return "review_gate:" + ",".join(dict.fromkeys(confirmed_findings))
        return "review_gate"
    if plan_failure is not None and plan_failure.code:
        return f"plan:{plan_failure.code}"
    if boundary_failure is not None and boundary_failure.code:
        return f"boundary:{boundary_failure.code}"
    if compiler_errors:
        return "compiler:" + ",".join(dict.fromkeys(item.code for item in compiler_errors if item.code))
    if result.failure_kind == "model_timeout":
        return f"timeout:{str(getattr(result, 'model_timeout_stage', '')).strip() or 'unknown'}"
    if result.failure_kind == "rule_validation":
        return "rule_validation:" + (str(result.error or result.summary or "").strip().splitlines() or ["unknown"])[0][:120]
    if result.failure_kind:
        normalized_output = " ".join(str(result.build_output or result.error or "").split())
        if normalized_output:
            digest = hashlib.sha1(normalized_output.encode("utf-8")).hexdigest()[:12]
            return f"{result.failure_kind}:{digest}"
        return result.failure_kind
    return ""


def _normalize_invalid_write_tool_input_detail(raw_text: str) -> str:
    """Normalize volatile tool-input errors into stable retry detail keys."""

    normalized = " ".join(str(raw_text or "").split()).lower()
    if not normalized:
        return ""

    normalized = re.sub(r"call_[0-9a-f]+", "call_id", normalized)
    missing_fields = [
        field
        for field in ("file_path", "old_string", "new_string", "edits", "content")
        if re.search(rf"(required parameter|parameter)\s+`?{re.escape(field)}`?\s+is missing", normalized)
        or f"`{field}` is missing" in normalized
    ]
    if missing_fields:
        return "missing:" + ",".join(dict.fromkeys(missing_fields))
    if "empty payload" in normalized:
        return "empty_payload"
    if "at least one valid edits" in normalized:
        return "invalid_edits"
    if "string to replace not found" in normalized:
        return "old_string_not_found"
    if "no changes to make" in normalized:
        return "no_effect"
    if "inputvalidationerror" in normalized:
        return "input_validation_error"
    return ""


def _build_strategy_fingerprint(result: FixResult) -> str:
    edit_contract = getattr(result, "edit_contract", None)
    repair_plan = getattr(result, "repair_plan", None) or getattr(edit_contract, "repair_plan", None)
    parts = [
        f"profile={str(getattr(edit_contract, 'execution_profile', '')).strip()}",
        f"fast_path={int(bool(getattr(edit_contract, 'fast_path_enabled', False)))}",
        f"plan_first={int(bool(getattr(edit_contract, 'plan_first_enabled', False)))}",
        "caps=" + ",".join(getattr(edit_contract, "allowed_capabilities", ()) or ()),
        f"shape={str(getattr(repair_plan, 'repair_shape', '')).strip()}",
        f"archetype={str(getattr(repair_plan, 'selected_archetype', '')).strip()}",
        f"fallback={str(getattr(repair_plan, 'fallback_archetype', '')).strip()}",
        "archetype_chain=" + ">".join(getattr(repair_plan, "archetype_chain", ()) or ()),
    ]
    return "|".join(part for part in parts if not part.endswith("="))


def _build_diff_fingerprint(workspace_path: Path, result: FixResult) -> str:
    changed_files = _normalize_changed_files(result)
    if not changed_files:
        return "no_change"

    digest = hashlib.sha1()
    for rel_path in changed_files:
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        candidate = workspace_path / rel_path
        if not candidate.exists() or not candidate.is_file():
            digest.update(b"<missing>")
            digest.update(b"\0")
            continue
        try:
            digest.update(candidate.read_bytes())
        except Exception:
            digest.update(candidate.read_text(encoding="utf-8", errors="replace").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _should_abort_retry_early(
    previous_retry_context: RetryContext | None,
    next_retry_context: RetryContext,
) -> bool:
    if previous_retry_context is None:
        return False
    if previous_retry_context.model_timeout_failed or next_retry_context.model_timeout_failed:
        return False
    if previous_retry_context.failure_kind != next_retry_context.failure_kind:
        return False
    if not next_retry_context.failure_detail_key:
        return False
    if previous_retry_context.failure_detail_key != next_retry_context.failure_detail_key:
        return False
    if previous_retry_context.strategy_fingerprint != next_retry_context.strategy_fingerprint:
        return False
    return previous_retry_context.diff_fingerprint == next_retry_context.diff_fingerprint


def _build_early_retry_stop_reason(result: FixResult, attempt: int, retry_context: RetryContext) -> str:
    detail = retry_context.failure_detail_key or result.failure_kind or "unknown_failure"
    return (
        f"Retry stopped early after {attempt} attempt(s): repeated `{detail}` with unchanged strategy and diff."
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
    boundary_failure = _extract_boundary_failure_context(result)
    scope_violation = _extract_scope_violation_context(raw_output, issue)
    review_failure = _extract_review_failure_context(result)
    quality_gate_failure = _extract_quality_gate_failure_context(result)
    review_gate_failure = _extract_review_gate_failure_context(result)
    plan_failure = _extract_plan_failure_context(result)
    semantic_precheck_failure = _extract_semantic_precheck_failure_context(result)
    changed_files = _normalize_changed_files(result)
    prompt_output, build_timeout_failed, build_timeout_without_errors = _build_prompt_safe_output(
        result,
        compiler_errors=compiler_errors,
    )
    failure_fingerprints = detect_failure_fingerprints(
        failure_kind=result.failure_kind,
        compiler_errors=compiler_errors,
        quality_gate_failure=quality_gate_failure,
        review_gate_failure=review_gate_failure,
        boundary_failure=boundary_failure,
        semantic_precheck_failure=semantic_precheck_failure,
        raw_output=raw_output,
        error=result.error or "",
        changed_files=changed_files,
    )
    primary_failure_fingerprint = failure_fingerprints[0] if failure_fingerprints else ""
    return RetryContext(
        source_attempt_number=source_attempt_number,
        issue_rule_id=issue.rule if issue is not None else "",
        failure_kind=result.failure_kind,
        failure_detail_key=_build_failure_detail_key(
            result,
            compiler_errors=compiler_errors,
            boundary_failure=boundary_failure,
            quality_gate_failure=quality_gate_failure,
            review_gate_failure=review_gate_failure,
            plan_failure=plan_failure,
            semantic_precheck_failure=semantic_precheck_failure,
        ),
        failure_fingerprints=failure_fingerprints,
        primary_failure_fingerprint=primary_failure_fingerprint,
        failure_fingerprint_repetition=(1 if primary_failure_fingerprint else 0),
        strategy_fingerprint=_build_strategy_fingerprint(result),
        diff_fingerprint=_build_diff_fingerprint(workspace_path, result),
        error=result.error or "",
        summary=result.summary,
        build_command=result.build_command,
        raw_output=raw_output,
        prompt_output=prompt_output,
        retryable_failure=result.retryable_failure,
        build_verification_failed=result.build_verification_failed,
        changed_files=changed_files,
        compiler_errors=compiler_errors,
        guidance=tuple(_build_retry_guidance(list(compiler_errors))) if compiler_errors else (),
        boundary_failure=boundary_failure,
        scope_violation=scope_violation,
        review_failure=review_failure,
        quality_gate_failure=quality_gate_failure,
        review_gate_failure=review_gate_failure,
        plan_failure=plan_failure,
        semantic_precheck_failure=semantic_precheck_failure,
        model_timeout_summary=(
            _summarize_model_timeout(raw_output, getattr(result, "model_timeout_stage", ""))
            if result.failure_kind == "model_timeout"
            else ""
        ),
        model_timeout_stage=str(getattr(result, "model_timeout_stage", "")).strip(),
        build_tool_failed=result.failure_kind == "build_tool",
        forbidden_tool_failed=result.failure_kind == "forbidden_tool",
        model_timeout_failed=result.failure_kind == "model_timeout",
        patch_salvaged=bool(getattr(result, "patch_salvaged", False)),
        build_timeout_failed=build_timeout_failed,
        build_timeout_without_errors=build_timeout_without_errors,
    )


def build_retry_feedback(
    workspace_path: Path,
    result: FixResult,
    issue: SonarIssue | None = None,
) -> str:
    """Build concise retry feedback for the next model attempt."""

    return render_retry_context(build_retry_context(workspace_path, result, issue))


def _carry_forward_blocker_context(
    previous_retry_context: RetryContext | None,
    next_retry_context: RetryContext,
) -> RetryContext:
    """Preserve the last concrete blocker when the new attempt never produced a patch."""

    if previous_retry_context is None:
        return next_retry_context
    if next_retry_context.failure_kind not in {"no_change", "tool_input_invalid"}:
        return _apply_failure_fingerprint_progression(previous_retry_context, next_retry_context)

    guidance = tuple(
        dict.fromkeys(
            item
            for item in (
                *next_retry_context.guidance,
                *previous_retry_context.guidance,
            )
            if str(item).strip()
        )
    )

    merged = replace(
        next_retry_context,
        guidance=guidance,
        quality_gate_failure=next_retry_context.quality_gate_failure or previous_retry_context.quality_gate_failure,
        review_gate_failure=next_retry_context.review_gate_failure or previous_retry_context.review_gate_failure,
        plan_failure=next_retry_context.plan_failure or previous_retry_context.plan_failure,
        semantic_precheck_failure=(
            next_retry_context.semantic_precheck_failure
            or previous_retry_context.semantic_precheck_failure
        ),
    )
    if not merged.failure_fingerprints and previous_retry_context.failure_fingerprints:
        merged = replace(
            merged,
            failure_fingerprints=previous_retry_context.failure_fingerprints,
            primary_failure_fingerprint=previous_retry_context.primary_failure_fingerprint,
            failure_fingerprint_repetition=previous_retry_context.failure_fingerprint_repetition,
        )
    return _apply_failure_fingerprint_progression(previous_retry_context, merged)


def _apply_failure_fingerprint_progression(
    previous_retry_context: RetryContext | None,
    next_retry_context: RetryContext,
) -> RetryContext:
    """Track repeated failure fingerprints across attempts."""

    primary = str(getattr(next_retry_context, "primary_failure_fingerprint", "")).strip()
    if not primary:
        return replace(next_retry_context, failure_fingerprint_repetition=0)
    if (
        previous_retry_context is not None
        and str(getattr(previous_retry_context, "primary_failure_fingerprint", "")).strip() == primary
    ):
        previous_repetition = int(
            getattr(previous_retry_context, "failure_fingerprint_repetition", 0) or 0
        )
        return replace(
            next_retry_context,
            failure_fingerprint_repetition=max(1, previous_repetition) + 1,
        )
    return replace(next_retry_context, failure_fingerprint_repetition=1)


def _summarize_model_timeout(raw_output: str, timeout_stage: str = "") -> str:
    """Summarize the timeout mode for retry feedback."""

    normalized_stage = str(timeout_stage or "").strip()
    stage_map = {
        "post_read_stall": "在 Read 工具返回后等待模型继续响应时超时",
        "post_edit_stall": "在 Edit 工具返回后等待模型继续响应时超时",
        "post_summary_stall": "在修复已完成后的总结阶段超时",
        "post_text_stall": "在助手文本输出后等待进一步响应时超时",
        "follow_up_response_timeout": "在等待模型后续响应时超时",
        "first_response_timeout": "在等待模型首响应时超时",
        "client_connect_timeout": "在初始化模型客户端时超时",
        "issue_hard_timeout": "在单个 issue 执行过程中超时",
    }
    if normalized_stage in stage_map:
        return stage_map[normalized_stage]
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
        if str(getattr(result, "boundary_failure_code", "")).startswith("filesystem_"):
            return f"Filesystem boundary rejected the patch after {attempts} attempt(s)"
        return f"Diff reviewer rejected the patch after {attempts} attempt(s)"
    if result.failure_kind == "quality_gate":
        return f"C# quality gate verification failed after {attempts} attempt(s)"
    if result.failure_kind == "review_gate":
        return f"Review gate verification failed after {attempts} attempt(s)"
    if result.failure_kind == "plan_conflict":
        return f"Plan precheck rejected the edit after {attempts} attempt(s)"
    if result.failure_kind == "semantic_precheck":
        return f"Semantic precheck failed after {attempts} attempt(s)"
    if result.failure_kind == "planner_skip":
        return f"Planner skipped the issue after {attempts} attempt(s)"
    if result.failure_kind == "tool_input_invalid":
        return f"Agent emitted invalid Edit/MultiEdit/Write input after {attempts} attempt(s)"
    if result.failure_kind == "no_change":
        return f"Agent completed without modifying any files after {attempts} attempt(s)"
    if result.failure_kind == "rule_validation":
        return f"Rule-specific validation failed after {attempts} attempt(s)"
    if result.failure_kind == "build_tool":
        return f"Build tool execution failed after {attempts} attempt(s)"
    if result.failure_kind == "forbidden_tool":
        if result.build_verification_failed:
            return f"Build verification failed after {attempts} attempt(s) (attempt also used a forbidden tool)"
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
        boundary_failure_code=str(getattr(result, "boundary_failure_code", "")).strip(),
        secondary_boundary_failure_codes=tuple(
            str(item).strip()
            for item in getattr(result, "secondary_boundary_failure_codes", ())
            if str(item).strip()
        ),
        issue_log_path=result.issue_log_path,
        artifact_dir=artifact_dir,
        performance_metrics={
            **dict(getattr(result, "performance_metrics", {}) or {}),
            "attempt_total_duration_seconds": round(duration_seconds, 3),
        },
        rollout_flags=tuple(str(item).strip() for item in getattr(result, "rollout_flags", ()) if str(item).strip()),
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
        final_boundary_failure_code=str(getattr(result, "boundary_failure_code", "")).strip(),
        final_secondary_boundary_failure_codes=tuple(
            str(item).strip()
            for item in getattr(result, "secondary_boundary_failure_codes", ())
            if str(item).strip()
        ),
        artifact_root=artifact_root,
        performance_summary=summarize_issue_performance(
            tuple(attempts),
            rollout_flags=tuple(
                str(item).strip() for item in getattr(result, "rollout_flags", ()) if str(item).strip()
            ),
        ),
        rollout_flags=tuple(
            str(item).strip() for item in getattr(result, "rollout_flags", ()) if str(item).strip()
        ),
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
    lessons_store: LessonsStore | None = None,
    max_build_retries: int = DEFAULT_MAX_BUILD_RETRIES,
) -> FixResult:
    """Retry a single issue fix on build failure, then roll back only that issue if needed."""

    logger = IssueAttemptLogger(repository=repository, issue_key=issue.key, run_label=run_label)
    artifact_writer = ArtifactWriter()
    follow_up_store = FollowUpStore()
    lessons_store = lessons_store or LessonsStore()
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
        compliance_summary = build_compliance_summary(
            getattr(getattr(result, "edit_contract", None), "quality_gate_rules", ()),
            getattr(result, "quality_gate_result", None),
        )
        artifact_writer.write_issue_state(
            issue_state,
            compliance_summary=compliance_summary.to_dict(),
        )
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
            next_retry_context = _carry_forward_blocker_context(retry_context, next_retry_context)
            if _should_retry_build_verification_only(result, next_retry_context):
                logger.write(
                    "Build verification timed out without compiler errors; retrying verification with extended timeout before asking the model to edit again."
                )
                result = _retry_build_verification_with_extended_timeout(
                    workspace_path=workspace_path,
                    result=result,
                )
                if result.success:
                    logger.write("Extended build verification passed; salvaging current patch without another model retry.")
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
                next_retry_context = build_retry_context(
                    workspace_path,
                    result,
                    issue,
                    source_attempt_number=attempt,
                )
                next_retry_context = _carry_forward_blocker_context(retry_context, next_retry_context)
            lessons_store.record_failure(
                repository=repository,
                run_label=run_label,
                issue_key=issue.key,
                issue_rule_id=issue.rule,
                retry_context=next_retry_context,
                scope_mode=str(getattr(getattr(result, "edit_contract", None), "scope_mode", "")).strip(),
                guardrail_mode=str(getattr(result, "guardrail_mode", "")).strip(),
                quality_gate_rule_ids=tuple(
                    rule.rule_id
                    for rule in getattr(getattr(result, "edit_contract", None), "quality_gate_rules", ())
                ),
            )
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
            next_retry_context = _materialize_retry_workspace_files(
                workspace_path=workspace_path,
                issue_key=issue.key,
                retry_context=next_retry_context,
            )
            next_retry_feedback = render_retry_context(next_retry_context)
            if next_retry_feedback:
                logger.write("Next retry feedback for model:\n" + next_retry_feedback)

            should_retry = result.retryable_failure or result.build_verification_failed
            early_abort_threshold = (
                EARLY_RETRY_ABORT_MIN_ATTEMPTS_NO_CHANGE
                if next_retry_context.failure_kind == "no_change"
                else EARLY_RETRY_ABORT_MIN_ATTEMPTS_TOOL_INPUT_INVALID
                if next_retry_context.failure_kind == "tool_input_invalid"
                else EARLY_RETRY_ABORT_MIN_ATTEMPTS_FIRST_RESPONSE_TIMEOUT
                if (
                    next_retry_context.model_timeout_failed
                    and next_retry_context.model_timeout_stage == "first_response_timeout"
                )
                else EARLY_RETRY_ABORT_MIN_ATTEMPTS
            )
            if (
                should_retry
                and attempt < max_build_retries
                and attempt >= early_abort_threshold
                and _should_abort_retry_early(retry_context, next_retry_context)
            ):
                should_retry = False
                result.skip_reason = _build_early_retry_stop_reason(result, attempt, next_retry_context)
                logger.write(
                    "Retry stopped early because failure detail, strategy fingerprint, and diff fingerprint did not change."
                )
            if should_retry and attempt < max_build_retries:
                retry_context = next_retry_context
                retry_feedback = next_retry_feedback or failure_report or (result.error or "")
                logger.write(f"Retrying issue after recoverable failure, next attempt: {attempt + 1}")
                continue

            if should_retry:
                result.skip_reason = _build_final_skip_reason(result, attempt)
            elif not result.skip_reason:
                result.skip_reason = result.error or "Issue fix failed"
            else:
                result.skip_reason = result.skip_reason or result.error or "Issue fix failed"
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
