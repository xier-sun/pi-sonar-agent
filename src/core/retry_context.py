"""Structured retry context for issue attempts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class CompilerErrorContext:
    """Structured compiler error with optional source snippet."""

    file_path: str
    line: int
    column: int
    code: str
    message: str
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the compiler error to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class ScopeViolationContext:
    """Structured scope violation details for retry analysis."""

    raw_output: str = ""
    allowed_lines: str = ""
    changed_lines_outside_scope: str = ""
    constraints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the scope violation context to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class ReviewViolationContext:
    """Structured diff-review violation details for retry analysis."""

    violation_type: str
    file: str
    reason: str
    changed_lines: tuple[int, ...] = ()
    evidence_hunk: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the reviewer violation to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class ReviewFailureContext:
    """Structured diff-review rejection details for retry analysis."""

    raw_output: str = ""
    summary: str = ""
    constraints: tuple[str, ...] = ()
    violations: tuple[ReviewViolationContext, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the review failure context to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class BoundaryFailureContext:
    """Structured runtime boundary failure classification."""

    code: str
    summary: str = ""
    secondary_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateViolationContext:
    """Structured hard quality-gate violation for retry analysis."""

    rule_id: str
    title: str
    message: str
    file: str = ""
    line: int = 0
    symbol: str = ""
    evidence: str = ""
    retry_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateFailureContext:
    """Structured quality-gate rejection details for retry analysis."""

    summary: str = ""
    violations: tuple[QualityGateViolationContext, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewGateDecisionContext:
    """Structured review-gate decision for retry analysis."""

    finding_id: str
    title: str
    source: str
    decision: str
    reason: str
    file: str = ""
    line: int = 0
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewGateFailureContext:
    """Structured model-audited gate rejection details for retry analysis."""

    summary: str = ""
    decisions: tuple[ReviewGateDecisionContext, ...] = ()
    feedback: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class PlanFailureContext:
    """Structured plan-first precheck conflict for retry analysis."""

    code: str
    summary: str = ""
    details: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class SemanticPrecheckFindingContext:
    """Structured semantic-precheck blocker for retry analysis."""

    finding_id: str
    title: str
    message: str
    file: str = ""
    line: int = 0
    evidence: str = ""
    retry_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class SemanticPrecheckFailureContext:
    """Structured semantic-precheck rejection details for retry analysis."""

    summary: str = ""
    findings: tuple[SemanticPrecheckFindingContext, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class RetryHistoryItem:
    """Compressed memory for a prior retry attempt."""

    attempt_number: int = 0
    failure_kind: str = ""
    failure_detail_key: str = ""
    primary_failure_fingerprint: str = ""
    failure_fingerprints: tuple[str, ...] = ()
    compiler_error_codes: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    strategy_summary: str = ""
    edited_symbols: tuple[str, ...] = ()
    patch_summary: str = ""
    headline: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class RetryContext:
    """Structured retry memory for the next issue attempt."""

    source_attempt_number: int = 0
    issue_rule_id: str = ""
    failure_kind: str = ""
    failure_detail_key: str = ""
    failure_fingerprints: tuple[str, ...] = ()
    primary_failure_fingerprint: str = ""
    failure_fingerprint_repetition: int = 0
    strategy_fingerprint: str = ""
    diff_fingerprint: str = ""
    error: str = ""
    summary: str = ""
    build_command: str = ""
    raw_output: str = ""
    prompt_output: str = ""
    strategy_summary: str = ""
    patch_summary: str = ""
    edited_symbols: tuple[str, ...] = ()
    retryable_failure: bool = False
    build_verification_failed: bool = False
    changed_files: tuple[str, ...] = ()
    compiler_errors: tuple[CompilerErrorContext, ...] = ()
    guidance: tuple[str, ...] = ()
    boundary_failure: BoundaryFailureContext | None = None
    scope_violation: ScopeViolationContext | None = None
    review_failure: ReviewFailureContext | None = None
    quality_gate_failure: QualityGateFailureContext | None = None
    review_gate_failure: ReviewGateFailureContext | None = None
    plan_failure: PlanFailureContext | None = None
    semantic_precheck_failure: SemanticPrecheckFailureContext | None = None
    model_timeout_summary: str = ""
    model_timeout_stage: str = ""
    build_tool_failed: bool = False
    forbidden_tool_failed: bool = False
    model_timeout_failed: bool = False
    patch_salvaged: bool = False
    build_timeout_failed: bool = False
    build_timeout_without_errors: bool = False
    workspace_file_references: tuple[str, ...] = ()
    workspace_read_hint: str = ""
    workspace_state_note: str = ""
    retry_history_items: tuple[RetryHistoryItem, ...] = ()
    retry_history_compacted_items: tuple[RetryHistoryItem, ...] = ()
    retry_history_total_attempts: int = 0
    retry_history_compaction_count: int = 0
    retry_history_char_budget: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the retry context to a JSON-ready dictionary."""

        return serialize_state(self)


RETRY_HISTORY_CHAR_BUDGET = 1100
RETRY_HISTORY_KEEP_RECENT = 3
RETRY_HISTORY_HEADLINE_MAX_CHARS = 180
RETRY_HISTORY_COMPACT_SUMMARY_MAX_CHARS = 420


def _dedupe_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(normalized)
    return results


def _build_quality_gate_guidance(
    quality_gate_failure: QualityGateFailureContext | None,
    *,
    issue_rule_id: str = "",
) -> tuple[str, ...]:
    if quality_gate_failure is None:
        return ()

    guidance: list[str] = []
    normalized_issue_rule = str(issue_rule_id or "").strip()
    violation_rule_ids = {
        str(item.rule_id or "").strip()
        for item in quality_gate_failure.violations
        if str(item.rule_id or "").strip()
    }
    for item in quality_gate_failure.violations:
        if item.retry_hint:
            guidance.append(item.retry_hint)

        if item.rule_id == "public_xml_docs":
            guidance.append("只为当前触达的公开成员补齐 XML 文档，包括 <summary>/<param>/<returns>；不要顺手补无关公开成员。")
            guidance.append("如果这次修复只是为了降低复杂度或删除无用代码，不要新增 public/protected helper、DTO、property 来制造更多 XML 文档负担。")
        elif item.rule_id == "async_signature":
            if "没有以 Async 结尾" in item.message:
                guidance.append("把当前触达的异步方法改成 *Async 结尾；如果这是公开 API 或接口实现，同步接口声明、调用点和 nameof(...)。")
                guidance.append("不要为了凑 *Async 命名去新建或重命名并不真正异步的 helper；优先把新 helper 保持为同步方法。")
            if "返回类型不是 Task/Task<T>" in item.message:
                guidance.append("异步方法返回类型改为 Task 或 Task<T>；纯同步 helper 去掉 async 并改成同步返回。")
            if "async void" in item.message:
                guidance.append("不要保留 async void；除事件处理器外改成 Task/Task<T> 或同步方法。")
        elif item.rule_id == "async_requires_await":
            guidance.append("如果当前方法没有实际 await，就移除 async 并改成同步方法，或直接返回 Task；不要保留空 async。")
            guidance.append("新提取的 helper 默认保持同步；只有 helper 体内真实含有 await 时才允许 async。")

    if normalized_issue_rule == "csharpsquid:S3776" and "cognitive_complexity" not in violation_rule_ids:
        guidance.append("当前 issue 的原始目标仍然是降低 Sonar 指向方法的认知复杂度；补这些门禁时不要把补丁收缩成只修 XML、async 或 LINQ 语法的卫生修复。")
        guidance.append("保留并继续完成已经开始的复杂度重构，优先收口到目标方法的热点分支、嵌套和循环，而不是回退成纯文档或纯语法调整。")
        guidance.append("先修当前门禁，但不要丢掉原始的 S3776 目标；避免完全重写整段逻辑，也不要把复杂度改造整体撤回。")
    else:
        guidance.append("只修这些门禁问题，保留已经通过的其它改动，不要重新大改整段逻辑。")
    return tuple(_dedupe_ordered(guidance))


def _append_quality_gate_details(
    sections: list[str],
    quality_gate_failure: QualityGateFailureContext | None,
    *,
    issue_rule_id: str = "",
) -> None:
    if quality_gate_failure is None:
        return

    sections.append(quality_gate_failure.summary)
    for index, item in enumerate(quality_gate_failure.violations, start=1):
        detail = f"{index}. [{item.rule_id}] {item.title}: {item.message}"
        if item.file:
            location = item.file
            if item.line > 0:
                location = f"{location}:{item.line}"
            detail += f" | location: {location}"
        sections.append(detail)
        if item.evidence:
            sections.append(f"   证据: {item.evidence}")
        if item.retry_hint:
            sections.append(f"   原始提示: {item.retry_hint}")

    guidance = _build_quality_gate_guidance(
        quality_gate_failure,
        issue_rule_id=issue_rule_id,
    )
    if guidance:
        sections.append("本次重改要求:")
        sections.extend(f"- {item}" for item in guidance)


def _append_semantic_precheck_details(
    sections: list[str],
    semantic_precheck_failure: SemanticPrecheckFailureContext | None,
) -> None:
    if semantic_precheck_failure is None:
        return

    sections.append(semantic_precheck_failure.summary)
    for index, item in enumerate(semantic_precheck_failure.findings, start=1):
        detail = f"{index}. [{item.finding_id}] {item.title}: {item.message}"
        if item.file:
            location = item.file
            if item.line > 0:
                location = f"{location}:{item.line}"
            detail += f" | location: {location}"
        sections.append(detail)
        if item.evidence:
            sections.append(f"   证据: {item.evidence}")
        if item.retry_hint:
            sections.append(f"   原始提示: {item.retry_hint}")

    retry_hints = _dedupe_ordered(
        [
            str(item.retry_hint).strip()
            for item in semantic_precheck_failure.findings
            if str(item.retry_hint).strip()
        ]
    )
    if retry_hints:
        sections.append("语义预检重试约束:")
        sections.extend(f"- {item}" for item in retry_hints)


def _append_workspace_retry_references(
    sections: list[str],
    retry_context: RetryContext,
) -> None:
    references = [
        str(item).strip()
        for item in getattr(retry_context, "workspace_file_references", ())
        if str(item).strip()
    ]
    if not references:
        return
    sections.append("如需查看更多本地验证信息，可按需 Read 以下工作区文件：")
    sections.extend(f"- {item}" for item in references)
    read_hint = str(getattr(retry_context, "workspace_read_hint", "") or "").strip()
    if read_hint:
        sections.append(f"读取建议: {read_hint}")


def _normalize_single_line(value: str, *, max_chars: int = 0) -> str:
    text = " ".join(str(value or "").split())
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _first_non_empty(*values: str) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _build_retry_history_headline(retry_context: RetryContext) -> str:
    compiler_codes = ", ".join(
        item.code for item in getattr(retry_context, "compiler_errors", ())[:3] if str(item.code).strip()
    )
    if compiler_codes:
        first_message = _normalize_single_line(
            getattr(retry_context.compiler_errors[0], "message", ""),
            max_chars=90,
        )
        summary = compiler_codes
        if first_message:
            summary += f" | {first_message}"
        return _normalize_single_line(summary, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    if retry_context.semantic_precheck_failure is not None:
        finding_ids = ", ".join(
            item.finding_id
            for item in retry_context.semantic_precheck_failure.findings[:3]
            if str(item.finding_id).strip()
        )
        message = _first_non_empty(
            retry_context.semantic_precheck_failure.summary,
            *(item.message for item in retry_context.semantic_precheck_failure.findings),
            retry_context.error,
        )
        summary = finding_ids or message
        if finding_ids and message and message != finding_ids:
            summary = f"{finding_ids} | {message}"
        return _normalize_single_line(summary, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    if retry_context.quality_gate_failure is not None:
        rule_ids = ", ".join(
            item.rule_id
            for item in retry_context.quality_gate_failure.violations[:3]
            if str(item.rule_id).strip()
        )
        message = _first_non_empty(
            retry_context.quality_gate_failure.summary,
            *(item.message for item in retry_context.quality_gate_failure.violations),
            retry_context.error,
        )
        summary = rule_ids or message
        if rule_ids and message and message != rule_ids:
            summary = f"{rule_ids} | {message}"
        return _normalize_single_line(summary, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    if retry_context.review_gate_failure is not None:
        message = _first_non_empty(
            retry_context.review_gate_failure.summary,
            *(item.reason for item in retry_context.review_gate_failure.decisions),
            retry_context.error,
        )
        return _normalize_single_line(message, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    if retry_context.review_failure is not None:
        message = _first_non_empty(
            retry_context.review_failure.summary,
            *(item.reason for item in retry_context.review_failure.violations),
            retry_context.error,
        )
        return _normalize_single_line(message, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    if retry_context.plan_failure is not None:
        message = _first_non_empty(
            retry_context.plan_failure.summary,
            *(retry_context.plan_failure.details),
            retry_context.error,
        )
        return _normalize_single_line(message, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    if retry_context.boundary_failure is not None:
        message = _first_non_empty(
            retry_context.boundary_failure.summary,
            retry_context.error,
            retry_context.prompt_output,
            retry_context.raw_output,
        )
        return _normalize_single_line(message, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)

    message = _first_non_empty(
        retry_context.summary,
        retry_context.error,
        retry_context.prompt_output,
        retry_context.raw_output,
    )
    return _normalize_single_line(message, max_chars=RETRY_HISTORY_HEADLINE_MAX_CHARS)


def build_retry_history_item(retry_context: RetryContext) -> RetryHistoryItem:
    """Create a compact per-attempt memory item from the current retry context."""

    compiler_error_codes = tuple(
        dict.fromkeys(
            str(item.code).strip()
            for item in getattr(retry_context, "compiler_errors", ())
            if str(item.code).strip()
        )
    )
    return RetryHistoryItem(
        attempt_number=int(getattr(retry_context, "source_attempt_number", 0) or 0),
        failure_kind=str(getattr(retry_context, "failure_kind", "") or "").strip(),
        failure_detail_key=str(getattr(retry_context, "failure_detail_key", "") or "").strip(),
        primary_failure_fingerprint=str(
            getattr(retry_context, "primary_failure_fingerprint", "") or ""
        ).strip(),
        failure_fingerprints=tuple(
            str(item).strip()
            for item in getattr(retry_context, "failure_fingerprints", ())
            if str(item).strip()
        ),
        compiler_error_codes=compiler_error_codes,
        changed_files=tuple(
            str(item).strip()
            for item in getattr(retry_context, "changed_files", ())
            if str(item).strip()
        ),
        strategy_summary=_normalize_single_line(
            str(getattr(retry_context, "strategy_summary", "") or "").strip(),
            max_chars=140,
        ),
        edited_symbols=tuple(
            str(item).strip()
            for item in getattr(retry_context, "edited_symbols", ())
            if str(item).strip()
        ),
        patch_summary=_normalize_single_line(
            str(getattr(retry_context, "patch_summary", "") or "").strip(),
            max_chars=180,
        ),
        headline=_build_retry_history_headline(retry_context),
    )


def _render_retry_history_item(item: RetryHistoryItem) -> str:
    parts = [f"Attempt {item.attempt_number}"]
    if item.failure_kind:
        parts.append(item.failure_kind)
    if item.primary_failure_fingerprint:
        parts.append(item.primary_failure_fingerprint)
    elif item.failure_detail_key:
        parts.append(item.failure_detail_key)
    if item.compiler_error_codes:
        parts.append("codes=" + ",".join(item.compiler_error_codes[:3]))
    if item.strategy_summary:
        parts.append("strategy=" + item.strategy_summary)
    if item.edited_symbols:
        parts.append("symbols=" + ",".join(item.edited_symbols[:3]))
    if item.headline:
        parts.append(item.headline)
    return " | ".join(part for part in parts if str(part).strip())


def _render_repair_summary_section(retry_context: RetryContext) -> str:
    sections: list[str] = []
    workspace_state_note = str(getattr(retry_context, "workspace_state_note", "") or "").strip()
    strategy_summary = str(getattr(retry_context, "strategy_summary", "") or "").strip()
    patch_summary = str(getattr(retry_context, "patch_summary", "") or "").strip()
    edited_symbols = tuple(
        str(item).strip()
        for item in getattr(retry_context, "edited_symbols", ())
        if str(item).strip()
    )
    references = tuple(
        str(item).strip()
        for item in getattr(retry_context, "workspace_file_references", ())
        if str(item).strip()
    )

    if workspace_state_note:
        sections.append(f"- 当前工作区状态: {workspace_state_note}")
    if strategy_summary:
        sections.append(f"- 上次修法: {strategy_summary}")
    if edited_symbols:
        sections.append(f"- 上次触达符号: {', '.join(edited_symbols[:6])}")
    if patch_summary:
        sections.append(f"- 上次改动摘要: {patch_summary}")
    if references:
        sections.append(f"- 相关本地工件: {', '.join(references[:4])}")

    if not sections:
        return ""
    return "【上次修复摘要】\n" + "\n".join(sections)


def _render_compacted_retry_history(compacted_items: tuple[RetryHistoryItem, ...]) -> str:
    if not compacted_items:
        return ""
    rendered = "; ".join(
        _normalize_single_line(_render_retry_history_item(item), max_chars=120)
        for item in compacted_items
    )
    return _normalize_single_line(
        rendered,
        max_chars=RETRY_HISTORY_COMPACT_SUMMARY_MAX_CHARS,
    )


def _render_retry_history_section(
    *,
    compacted_items: tuple[RetryHistoryItem, ...],
    recent_items: tuple[RetryHistoryItem, ...],
    total_attempts: int,
) -> str:
    if total_attempts <= 0:
        return ""

    sections = [
        "【累计重试上下文】",
        f"- 已累计失败 {total_attempts} 次；不要重复已经失败的修法。",
    ]
    if compacted_items:
        compacted_summary = _render_compacted_retry_history(compacted_items)
        if compacted_summary:
            sections.append(
                f"- 更早 {len(compacted_items)} 次已压缩: {compacted_summary}"
            )
    for item in recent_items:
        sections.append(f"- {_render_retry_history_item(item)}")
    return "\n".join(sections)


def merge_retry_context_history(
    previous_retry_context: RetryContext | None,
    current_retry_context: RetryContext,
    *,
    char_budget: int = RETRY_HISTORY_CHAR_BUDGET,
    keep_recent: int = RETRY_HISTORY_KEEP_RECENT,
) -> RetryContext:
    """Carry forward retry memory across attempts and compact older context on demand."""

    compacted_items = list(
        getattr(previous_retry_context, "retry_history_compacted_items", ()) or ()
    )
    recent_items = list(getattr(previous_retry_context, "retry_history_items", ()) or ())
    current_item = build_retry_history_item(current_retry_context)

    if current_item.attempt_number > 0:
        compacted_items = [
            item for item in compacted_items if item.attempt_number != current_item.attempt_number
        ]
        recent_items = [
            item for item in recent_items if item.attempt_number != current_item.attempt_number
        ]
        recent_items.append(current_item)

    recent_items.sort(key=lambda item: item.attempt_number)
    compaction_count = int(
        getattr(previous_retry_context, "retry_history_compaction_count", 0) or 0
    )

    moved_any = False
    while len(recent_items) > max(1, keep_recent):
        compacted_items.append(recent_items.pop(0))
        moved_any = True

    preview = _render_retry_history_section(
        compacted_items=tuple(compacted_items),
        recent_items=tuple(recent_items),
        total_attempts=len(compacted_items) + len(recent_items),
    )
    while len(preview) > max(0, char_budget) and len(recent_items) > 1:
        compacted_items.append(recent_items.pop(0))
        moved_any = True
        preview = _render_retry_history_section(
            compacted_items=tuple(compacted_items),
            recent_items=tuple(recent_items),
            total_attempts=len(compacted_items) + len(recent_items),
        )

    if moved_any:
        compaction_count += 1

    return replace(
        current_retry_context,
        retry_history_items=tuple(recent_items),
        retry_history_compacted_items=tuple(compacted_items),
        retry_history_total_attempts=len(compacted_items) + len(recent_items),
        retry_history_compaction_count=compaction_count,
        retry_history_char_budget=char_budget,
    )


def _prepend_retry_history(
    retry_context: RetryContext,
    body: str,
) -> str:
    history_section = _render_retry_history_section(
        compacted_items=tuple(
            getattr(retry_context, "retry_history_compacted_items", ()) or ()
        ),
        recent_items=tuple(getattr(retry_context, "retry_history_items", ()) or ()),
        total_attempts=int(getattr(retry_context, "retry_history_total_attempts", 0) or 0),
    )
    repair_summary_section = _render_repair_summary_section(retry_context)
    body_text = str(body or "").strip()
    sections = [section for section in (history_section, repair_summary_section, body_text) if section]
    return "\n\n".join(sections)


def render_retry_context(retry_context: RetryContext | None) -> str:
    """Render a structured retry context into the prompt text used by the model."""

    if retry_context is None:
        return ""

    raw_output = str(retry_context.raw_output or "").strip()
    prompt_output = str(retry_context.prompt_output or "").strip()
    effective_output = prompt_output or raw_output
    scope_violation = retry_context.scope_violation
    review_failure = retry_context.review_failure
    quality_gate_failure = retry_context.quality_gate_failure
    review_gate_failure = retry_context.review_gate_failure
    plan_failure = retry_context.plan_failure
    semantic_precheck_failure = retry_context.semantic_precheck_failure
    has_scope_violation = scope_violation is not None
    has_review_failure = review_failure is not None
    has_quality_gate_failure = quality_gate_failure is not None
    has_review_gate_failure = review_gate_failure is not None
    has_plan_failure = plan_failure is not None
    has_semantic_precheck_failure = semantic_precheck_failure is not None
    compiler_errors = list(retry_context.compiler_errors)
    if retry_context.primary_failure_fingerprint:
        repetition = int(getattr(retry_context, "failure_fingerprint_repetition", 0) or 0)
        label = f"失败指纹: {retry_context.primary_failure_fingerprint}"
        if repetition > 1:
            label += f"（连续命中 {repetition} 次）"
        if prompt_output or raw_output:
            raw_output = f"{label}\n{raw_output}" if raw_output else label
            prompt_output = f"{label}\n{prompt_output}" if prompt_output else label

    if not compiler_errors:
        if retry_context.failure_kind == "tool_input_invalid":
            return _prepend_retry_history(
                retry_context,
                "\n".join(
                    [
                        "上次尝试发出了无效的 Edit/MultiEdit/Write 工具调用，导致没有真正落盘修改。",
                        effective_output or "Edit/MultiEdit/Write 缺少必要参数。",
                        "重试约束:",
                        "- Edit 必须提供完整的 file_path、old_string、new_string。",
                        "- MultiEdit 必须提供 file_path 和至少一个有效 edits 项。",
                        "- 如果替换字符串不确定，先 Read 更小范围的代码窗口，再提交精确编辑。",
                        "- 不要发送空工具调用，也不要只输出“Using tool: Edit”而不附带参数。",
                    ]
                ),
            )
        if retry_context.failure_kind == "runtime_contract_violation":
            sections = [
                "上次尝试触发了运行时 contract 护栏，请在本轮直接按护栏要求收缩修法：",
                effective_output or retry_context.error or "当前 patch 违反了运行时 contract。",
                "重试约束:",
                "- 不要重复上一轮已经被运行时护栏拦下的修法。",
                "- 先以当前工作区状态为准，Read 目标方法后再做更小的改动。",
                "- 如果上一轮是 helper/private method 被禁用，本轮必须把逻辑收回当前方法体内。",
            ]
            _append_workspace_retry_references(sections, retry_context)
            return _prepend_retry_history(retry_context, "\n".join(sections))
        if effective_output:
            if has_plan_failure:
                lines = [
                    "上次尝试在 edit 前的 Plan 预检阶段就被拒绝，请先解决计划层冲突：",
                    plan_failure.summary if plan_failure else effective_output,
                ]
                lines.extend(str(item).strip() for item in (plan_failure.details if plan_failure else ()) if str(item).strip())
                if plan_failure and plan_failure.guidance:
                    lines.append("重试约束:")
                    lines.extend(f"- {item}" for item in plan_failure.guidance if str(item).strip())
                return _prepend_retry_history(retry_context, "\n".join(lines))
            if retry_context.failure_kind == "semantic_precheck" and has_semantic_precheck_failure:
                sections = ["上次尝试在 semantic precheck 阶段被拦下，请先解决这些语义阻塞："]
                _append_semantic_precheck_details(sections, semantic_precheck_failure)
                if has_quality_gate_failure:
                    sections.append("相关的质量门禁上下文：")
                    _append_quality_gate_details(
                        sections,
                        quality_gate_failure,
                        issue_rule_id=retry_context.issue_rule_id,
                )
                if effective_output and effective_output not in sections:
                    sections.append("原始运行输出：")
                    sections.append(effective_output)
                return _prepend_retry_history(
                    retry_context,
                    "\n".join(section for section in sections if str(section).strip()),
                )
            if retry_context.boundary_failure is not None and retry_context.boundary_failure.summary:
                effective_output = (
                    f"边界主阻塞原因: {retry_context.boundary_failure.code}\n"
                    f"{retry_context.boundary_failure.summary}\n\n{effective_output}"
                )
            if retry_context.model_timeout_failed:
                timeout_label = retry_context.model_timeout_summary or "在等待模型首响应时超时"
                if retry_context.patch_salvaged:
                    timeout_label += "（已检测到有效 patch 并尝试回收验证）"
                return _prepend_retry_history(
                    retry_context,
                    "\n".join(
                        [
                            f"上次尝试{timeout_label}，请先确认当前模型网关或 provider 可正常返回工具调用响应：",
                            effective_output,
                            "重试约束:",
                            "- 不要更换构建命令，先确认模型能稳定返回工具调用响应。",
                            "- 如果连续超时，优先检查 .env 中的模型 endpoint、token 和 provider 兼容性。",
                        ]
                    ),
                )
            if retry_context.forbidden_tool_failed:
                sections = [
                    "上次尝试使用了被禁止的工具，或污染了当前 issue 的 Git 基线；本次必须严格按下面约束重试：",
                    effective_output,
                    "重试约束:",
                    "- 严禁使用 git_add、git_commit、git_push 或任何自行提交/推送动作。",
                    "- 如果使用 shell 工具（工具名 Bash），只写 bash 兼容命令；默认只允许搜索、查看、诊断等无害操作；若工具策略明确放开新增文件，只能按声明目录创建。",
                    "- 优先使用 prompt 中给出的仓库相对路径候选，不要靠 Bash 拼接仓库根目录反复试错。",
                    "- 严禁通过 shell 删除文件、覆盖已有文件、移动/重命名文件或直接改写已有源码。",
                    "- 修复阶段只负责直接编辑代码；构建与验证由外层流程统一执行。",
                    "- 先根据下面的本地构建输出修复问题，再交由外层流程重新执行构建验证。",
                ]
                if has_scope_violation:
                    sections.extend(
                        [
                            "范围约束:",
                            *(scope_violation.constraints if scope_violation else ()),
                        ]
                    )
                return _prepend_retry_history(retry_context, "\n".join(sections))
            if retry_context.build_tool_failed:
                sections = [
                    "上次尝试在运行构建工具时异常退出，请先处理下面的异常信息和本地回退构建输出：",
                    effective_output,
                    "重试约束:",
                    "- 先修复 stderr 或回退构建输出中暴露的问题，再交由外层流程重新执行构建验证。",
                    "- 如果回退构建已经通过，也要保持补丁稳定，避免下一轮再次破坏编译。",
                ]
                _append_workspace_retry_references(sections, retry_context)
                if has_scope_violation:
                    sections.extend(
                        [
                            "范围约束:",
                            *(scope_violation.constraints if scope_violation else ()),
                        ]
                    )
                return _prepend_retry_history(retry_context, "\n".join(sections))
            if has_scope_violation:
                return _prepend_retry_history(
                    retry_context,
                    "\n".join(
                        [
                            "上次尝试修改了 Sonar 指定范围之外的代码，请严格缩小修改范围：",
                            effective_output,
                            "重试约束:",
                            *(scope_violation.constraints if scope_violation else ()),
                        ]
                    ),
                )
            if has_review_failure:
                return _prepend_retry_history(
                    retry_context,
                    "\n".join(
                        [
                            "上次尝试被变更审查拒绝，请严格缩小当前 patch：",
                            review_failure.raw_output if review_failure else effective_output,
                            "重试约束:",
                            *(review_failure.constraints if review_failure else ()),
                        ]
                    ),
                )
            if has_quality_gate_failure:
                sections = ["上次尝试通过了 build 和范围审查，但没有通过 C# 质量门禁："]
                _append_quality_gate_details(
                    sections,
                    quality_gate_failure,
                    issue_rule_id=retry_context.issue_rule_id,
                )
                return _prepend_retry_history(retry_context, "\n".join(sections))
            if has_review_gate_failure:
                sections = ["上次尝试经过审核 agent 复核后，仍被判定需要继续修改："]
                sections.append(review_gate_failure.summary)
                for index, item in enumerate(review_gate_failure.decisions, start=1):
                    detail = f"{index}. [{item.source}/{item.decision}] {item.title or item.finding_id}"
                    if item.file:
                        location = item.file
                        if item.line > 0:
                            location = f"{location}:{item.line}"
                        detail += f" | location: {location}"
                    sections.append(detail)
                    if item.reason:
                        sections.append(f"   审核理由: {item.reason}")
                    if item.evidence:
                        sections.append(f"   证据: {item.evidence}")
                if review_gate_failure.feedback:
                    sections.append("审核 agent 给出的下一轮要求:")
                    sections.extend(f"- {item}" for item in review_gate_failure.feedback)
                return _prepend_retry_history(retry_context, "\n".join(sections))
            sections = [effective_output]
            _append_workspace_retry_references(sections, retry_context)
            return _prepend_retry_history(
                retry_context,
                "\n".join(section for section in sections if str(section).strip()),
            )
        if has_plan_failure:
            lines = [
                "上次尝试在 edit 前的 Plan 预检阶段被拒绝。",
                plan_failure.summary,
            ]
            if plan_failure.details:
                lines.extend(f"- {item}" for item in plan_failure.details if str(item).strip())
            if plan_failure.guidance:
                lines.append("重试约束:")
                lines.extend(f"- {item}" for item in plan_failure.guidance if str(item).strip())
            return _prepend_retry_history(retry_context, "\n".join(lines))
        if retry_context.failure_kind == "semantic_precheck" and has_semantic_precheck_failure:
            sections = ["上次尝试在 semantic precheck 阶段被拦下，请先解决这些语义阻塞："]
            _append_semantic_precheck_details(sections, semantic_precheck_failure)
            if has_quality_gate_failure:
                sections.append("相关的质量门禁上下文：")
                _append_quality_gate_details(
                    sections,
                    quality_gate_failure,
                    issue_rule_id=retry_context.issue_rule_id,
                )
            return _prepend_retry_history(
                retry_context,
                "\n".join(section for section in sections if str(section).strip()),
            )
        if retry_context.failure_kind == "no_change" or retry_context.error == "Agent completed without modifying any files":
            sections = [
                "上次尝试没有实际修改任何文件。",
                "这次必须对 Sonar 指向的代码真正落盘修改，然后交由外层流程执行构建验证。",
            ]
            if has_quality_gate_failure:
                sections.append("而且前一轮已经明确暴露了这些 C# 质量门禁问题，不能继续忽略：")
                _append_quality_gate_details(
                    sections,
                    quality_gate_failure,
                    issue_rule_id=retry_context.issue_rule_id,
                )
            if has_review_gate_failure:
                sections.append("而且审核 agent 已经给出过这些待处理结论：")
                sections.append(review_gate_failure.summary)
                for index, item in enumerate(review_gate_failure.decisions, start=1):
                    detail = f"{index}. [{item.source}/{item.decision}] {item.title or item.finding_id}"
                    if item.file:
                        location = item.file
                        if item.line > 0:
                            location = f"{location}:{item.line}"
                        detail += f" | location: {location}"
                    sections.append(detail)
                    if item.reason:
                        sections.append(f"   审核理由: {item.reason}")
            sections.extend(
                [
                    "重试约束:",
                    "- 不要只做分析或解释，必须提交实际代码修改。",
                    "- 优先直接修掉上面已经明确指出的门禁/审核问题，不要重新大改整段逻辑。",
                    "- 修改后让外层流程执行构建验证，不要自行在 Bash 中跑 dotnet build/restore/test。",
                    "- 如果这是行级问题，只修改 Sonar 指向的那条语句。",
                ]
            )
            return _prepend_retry_history(retry_context, "\n".join(sections))
        return _prepend_retry_history(retry_context, retry_context.error or "")

    sections: list[str] = []
    if retry_context.model_timeout_failed:
        timeout_label = retry_context.model_timeout_summary or "在等待模型首响应时超时"
        if retry_context.patch_salvaged:
            timeout_label += "（已检测到有效 patch 并尝试回收验证）"
        sections.append(f"上次尝试{timeout_label}。")
        sections.append("先确认当前模型 provider/网关与 Claude SDK 工具调用协议兼容，再继续重试。")
    if retry_context.forbidden_tool_failed:
        sections.append("上次尝试使用了被禁止的工具，或污染了当前 issue 的 Git 基线。")
        sections.append("这次严禁使用 git_add、git_commit、git_push；只允许直接编辑代码，构建验证由外层流程统一执行。")
        sections.append("如果使用 shell 工具（工具名 Bash），只写 bash 兼容命令；默认只允许无害操作；若工具策略明确放开新增文件，只能按声明目录创建。")
        sections.append("优先使用 prompt 中给出的仓库相对路径候选，不要靠 Bash 拼接仓库根目录反复试错。")
        sections.append("严禁通过 shell 删除文件、覆盖已有文件、移动/重命名文件或直接改写已有源码。")
    if retry_context.build_tool_failed:
        sections.append("上次尝试在运行构建工具时异常退出；已附加本地回退构建结果。")
    sections.append("上次尝试引入了以下关键编译错误，请先修复这些错误：")
    for index, item in enumerate(compiler_errors[:12], start=1):
        sections.append(
            f"{index}. {item.code} at {item.file_path}:{item.line}:{item.column}\n"
            f"   错误信息: {item.message}"
        )
        if item.snippet:
            sections.append(f"   出错代码片段:\n{item.snippet}")

    if retry_context.guidance:
        sections.append("重试约束:")
        sections.extend(f"- {item}" for item in retry_context.guidance)
    _append_workspace_retry_references(sections, retry_context)

    if retry_context.boundary_failure is not None:
        sections.extend(
            [
                f"边界主阻塞原因: {retry_context.boundary_failure.code}",
                retry_context.boundary_failure.summary,
            ]
        )
        if retry_context.boundary_failure.secondary_codes:
            sections.append(
                "次级边界原因: " + ", ".join(retry_context.boundary_failure.secondary_codes)
            )

    if has_scope_violation:
        sections.extend(
            [
                "另外，上次修改还越过了 Sonar 允许范围：",
                scope_violation.raw_output if scope_violation else raw_output,
                "范围约束:",
                *(scope_violation.constraints if scope_violation else ()),
            ]
        )
    if has_review_failure:
        sections.extend(
            [
                "另外，Diff reviewer 也拒绝了上次 patch：",
                review_failure.raw_output if review_failure else effective_output,
                "审查约束:",
                *(review_failure.constraints if review_failure else ()),
            ]
        )
    if has_quality_gate_failure:
        sections.append("另外，上次 patch 还没有通过 C# 质量门禁：")
        _append_quality_gate_details(
            sections,
            quality_gate_failure,
            issue_rule_id=retry_context.issue_rule_id,
        )
    if has_review_gate_failure:
        sections.append("另外，上次 patch 经过审核 agent 复核后仍未通过：")
        sections.append(review_gate_failure.summary)
        for index, item in enumerate(review_gate_failure.decisions, start=1):
            detail = f"{index}. [{item.source}/{item.decision}] {item.title or item.finding_id}"
            if item.file:
                location = item.file
                if item.line > 0:
                    location = f"{location}:{item.line}"
                detail += f" | location: {location}"
            sections.append(detail)
            if item.reason:
                sections.append(f"   审核理由: {item.reason}")
        if review_gate_failure.feedback:
            sections.append("审核 agent 给出的下一轮要求:")
            sections.extend(f"- {item}" for item in review_gate_failure.feedback)

    return _prepend_retry_history(retry_context, "\n".join(sections))
