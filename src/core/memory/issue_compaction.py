"""Issue-level context compaction helpers for simple-loop prompting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pi_sonar_agent.core.memory.issue_working_memory import (
    IssueWorkingMemory,
    merge_issue_working_memory,
)
from pi_sonar_agent.core.memory.working_memory_store import WorkingMemoryStore
from pi_sonar_agent.core.retry_context import RetryContext, RetryHistoryItem
from pi_sonar_agent.fixers.rule_profiles import load_rule_catalog

DEFAULT_USER_PROMPT_TOKEN_BUDGET = 9000
DEFAULT_USER_PROMPT_CHAR_PRECHECK = 6500
RETRY_COMPACTION_ATTEMPT_THRESHOLD = 4
COMPACT_HISTORY_MAX_ITEMS = 5


@dataclass(frozen=True)
class IssueCompactionDecision:
    """Decision and metadata for one prompt compaction pass."""

    applied: bool = False
    reason: str = ""
    estimated_tokens: int = 0
    token_budget: int = 0
    estimator: str = "heuristic"
    boundary_note: str = ""
    compacted_history_summary: str = ""
    latest_failure_excerpt: str = ""
    compact_summary_path: str = ""
    compact_brief: str = ""


def resolve_prompt_token_budget(rule_id: str) -> int:
    """Resolve a safe per-prompt token budget for the current rule."""

    budget = DEFAULT_USER_PROMPT_TOKEN_BUDGET
    try:
        profile = load_rule_catalog().get(rule_id)
    except Exception:
        profile = None
    if profile is None:
        return budget
    max_token_budget = int(getattr(profile, "max_token_budget", 0) or 0)
    if max_token_budget <= 0:
        return budget
    return max(2500, min(budget, max_token_budget))


def estimate_token_count(text: str, model_hint: str = "") -> tuple[int, str]:
    """Estimate token usage with optional tiktoken support and a deterministic fallback."""

    normalized = str(text or "")
    if not normalized:
        return 0, "heuristic-empty"

    try:
        import tiktoken  # type: ignore

        encoding = None
        model_name = str(model_hint or "").strip()
        if model_name:
            try:
                encoding = tiktoken.encoding_for_model(model_name)
            except Exception:
                encoding = None
        if encoding is None:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(normalized)), f"tiktoken:{getattr(encoding, 'name', 'unknown')}"
    except Exception:
        ascii_chars = sum(1 for char in normalized if ord(char) < 128)
        non_ascii_chars = max(len(normalized) - ascii_chars, 0)
        line_breaks = normalized.count("\n")
        punctuation = sum(1 for char in normalized if char in "{}[]()<>=:;,.`|/-_")
        estimate = max(
            1,
            (ascii_chars // 4) + non_ascii_chars + (line_breaks // 2) + (punctuation // 6),
        )
        return estimate, "heuristic"


def build_compacted_history_summary(retry_context: RetryContext | None) -> str:
    """Render a compacted retry history summary suitable for working memory."""

    if retry_context is None:
        return ""

    recent_items = tuple(getattr(retry_context, "retry_history_items", ()) or ())
    compacted_items = tuple(getattr(retry_context, "retry_history_compacted_items", ()) or ())
    total_attempts = int(getattr(retry_context, "retry_history_total_attempts", 0) or 0)

    summary_lines: list[str] = []
    if total_attempts > 0:
        summary_lines.append(f"累计 {total_attempts} 轮尝试。")

    older_items = (*compacted_items, *recent_items[:-2]) if len(recent_items) > 2 else compacted_items
    rendered_items = [
        _render_history_item(item)
        for item in older_items[:COMPACT_HISTORY_MAX_ITEMS]
        if _render_history_item(item)
    ]
    if rendered_items:
        summary_lines.append("更早历史已压缩为: " + " | ".join(rendered_items))

    if recent_items:
        recent_rendered = [
            _render_history_item(item)
            for item in recent_items[-2:]
            if _render_history_item(item)
        ]
        if recent_rendered:
            summary_lines.append("最近两轮: " + " | ".join(recent_rendered))

    return " ".join(summary_lines).strip()


def build_latest_failure_excerpt(retry_context: RetryContext | None) -> str:
    """Build a compact latest-failure excerpt after compaction."""

    if retry_context is None:
        return ""

    sections: list[str] = []
    summary = str(getattr(retry_context, "summary", "") or "").strip()
    if summary:
        sections.append(summary)
    fingerprint = str(getattr(retry_context, "primary_failure_fingerprint", "") or "").strip()
    if fingerprint:
        sections.append(f"主失败指纹: {fingerprint}")
    compiler_errors = tuple(getattr(retry_context, "compiler_errors", ()) or ())
    if compiler_errors:
        rendered_errors = []
        for item in compiler_errors[:2]:
            code = str(getattr(item, "code", "") or "").strip()
            message = str(getattr(item, "message", "") or "").strip()
            if code or message:
                rendered_errors.append(f"{code}: {message}".strip(": "))
        if rendered_errors:
            sections.append("关键编译错误: " + " | ".join(rendered_errors))
    guidance = [
        str(item).strip()
        for item in getattr(retry_context, "guidance", ()) or ()
        if str(item).strip()
    ]
    if guidance:
        sections.append("重试约束: " + "；".join(guidance[:3]))
    workspace_note = str(getattr(retry_context, "workspace_state_note", "") or "").strip()
    if workspace_note:
        sections.append(workspace_note)
    return "\n".join(section for section in sections if section).strip()


def build_authoritative_compact_brief(
    working_memory: IssueWorkingMemory | None,
    retry_context: RetryContext | None,
) -> str:
    """Build a deterministic compact brief with the minimum facts needed to continue."""

    if working_memory is None:
        return ""

    goal = str(getattr(working_memory, "current_goal", "") or "").strip() or "继续修复当前 issue。"

    completed_actions: list[str] = []
    for item in (
        str(getattr(working_memory, "latest_strategy_summary", "") or "").strip(),
        str(getattr(working_memory, "latest_patch_summary", "") or "").strip(),
        str(getattr(working_memory, "latest_verification", "") or "").strip(),
        str(getattr(working_memory, "best_known_patch_state", "") or "").strip(),
    ):
        if item and item not in completed_actions:
            completed_actions.append(item)
    if retry_context is not None:
        for history_item in getattr(retry_context, "retry_history_items", ()) or ():
            headline = str(getattr(history_item, "headline", "") or "").strip()
            if headline and headline not in completed_actions:
                completed_actions.append(headline)
            if len(completed_actions) >= 4:
                break
    if not completed_actions:
        completed_actions.append("当前没有可复用的历史动作摘要，请先读取当前文件状态再继续。")

    files = [
        str(item).strip()
        for item in getattr(working_memory, "files_inspected", ()) or ()
        if str(item).strip()
    ]
    if retry_context is not None:
        for item in getattr(retry_context, "changed_files", ()) or ():
            text = str(item).strip()
            if text and text not in files:
                files.append(text)
    if not files:
        files.append("当前还没有稳定的文件轨迹，请先读取目标文件。")

    constraints: list[str] = []
    workspace_state = str(getattr(working_memory, "authoritative_workspace_state", "") or "").strip()
    if workspace_state:
        constraints.append(f"当前工作区状态: {workspace_state}")
    for item in getattr(working_memory, "accepted_constraints", ()) or ():
        text = str(item).strip()
        if text and text not in constraints:
            constraints.append(text)
    for item in getattr(working_memory, "rejected_strategies", ()) or ():
        text = str(item).strip()
        if text and text not in constraints:
            constraints.append(f"避免: {text}")
    rollback_reason = str(getattr(working_memory, "rollback_reason", "") or "").strip()
    if rollback_reason and rollback_reason not in constraints:
        constraints.append(rollback_reason)
    if not constraints:
        constraints.append("继续保持最小改动，优先遵守当前 contract 和质量门禁。")

    next_action = (
        str(getattr(working_memory, "next_action", "") or "").strip()
        or "先读取当前文件和目标位置，再决定下一步编辑。"
    )

    lines = [
        "1. 当前任务目标",
        f"- {goal}",
        "2. 已完成的关键动作",
        *(f"- {item}" for item in completed_actions[:4]),
        "3. 已修改或重点查看过的文件",
        *(f"- {item}" for item in files[:6]),
        "4. 关键决定与约束",
        *(f"- {item}" for item in constraints[:6]),
        "5. 下一步应该做什么",
        f"- {next_action}",
    ]
    return "\n".join(lines).strip()


def maybe_compact_issue_prompt(
    *,
    issue_key: str,
    rule_id: str,
    workspace_path: Path | None,
    working_memory: IssueWorkingMemory | None,
    retry_context: RetryContext | None,
    draft_prompt: str,
    model_hint: str = "",
) -> tuple[IssueWorkingMemory | None, IssueCompactionDecision]:
    """Apply issue-level prompt compaction when budgets or retry depth require it."""

    estimated_tokens, estimator = estimate_token_count(draft_prompt, model_hint=model_hint)
    token_budget = resolve_prompt_token_budget(rule_id)
    prompt_chars = len(str(draft_prompt or ""))
    retry_attempts = int(getattr(retry_context, "retry_history_total_attempts", 0) or 0)
    reason = _resolve_compaction_reason(
        retry_attempts=retry_attempts,
        prompt_chars=prompt_chars,
        estimated_tokens=estimated_tokens,
        token_budget=token_budget,
    )
    if not reason or working_memory is None:
        return working_memory, IssueCompactionDecision(
            applied=False,
            reason=reason,
            estimated_tokens=estimated_tokens,
            token_budget=token_budget,
            estimator=estimator,
        )

    boundary_note = (
        "更早细节已压缩，以下摘要为当前权威状态。请按当前工作记忆继续，"
        "不要重复追逐已经失效的旧错误。"
    )
    compacted_history_summary = build_compacted_history_summary(retry_context)
    latest_failure_excerpt = build_latest_failure_excerpt(retry_context)
    compact_brief = build_authoritative_compact_brief(working_memory, retry_context)

    updated_memory = merge_issue_working_memory(
        working_memory,
        compacted_history_summary=compacted_history_summary,
        compact_boundary_note=boundary_note,
        increment_compaction_generation=True,
    )

    compact_summary_path = ""
    if workspace_path is not None and str(issue_key or "").strip():
        store = WorkingMemoryStore(workspace_path)
        written_path = store.save_compact_summary(
            issue_key,
            _build_compact_summary_document(
                issue_key=issue_key,
                updated_memory=updated_memory,
                retry_context=retry_context,
                estimated_tokens=estimated_tokens,
                token_budget=token_budget,
                estimator=estimator,
                reason=reason,
                compact_brief=compact_brief,
            ),
        )
        try:
            compact_summary_path = written_path.relative_to(workspace_path).as_posix()
        except Exception:
            compact_summary_path = written_path.as_posix()
        updated_memory = merge_issue_working_memory(
            updated_memory,
            compact_summary_path=compact_summary_path,
        )

    return updated_memory, IssueCompactionDecision(
        applied=True,
        reason=reason,
        estimated_tokens=estimated_tokens,
        token_budget=token_budget,
        estimator=estimator,
        boundary_note=boundary_note,
        compacted_history_summary=compacted_history_summary,
        latest_failure_excerpt=latest_failure_excerpt,
        compact_summary_path=compact_summary_path,
        compact_brief=compact_brief,
    )


def _resolve_compaction_reason(
    *,
    retry_attempts: int,
    prompt_chars: int,
    estimated_tokens: int,
    token_budget: int,
) -> str:
    if retry_attempts >= RETRY_COMPACTION_ATTEMPT_THRESHOLD:
        return "retry_depth"
    if estimated_tokens >= int(token_budget * 0.85):
        return "token_budget"
    if prompt_chars >= DEFAULT_USER_PROMPT_CHAR_PRECHECK:
        return "char_precheck"
    return ""


def _render_history_item(item: RetryHistoryItem) -> str:
    attempt = int(getattr(item, "attempt_number", 0) or 0)
    headline = str(getattr(item, "headline", "") or "").strip()
    failure_kind = str(getattr(item, "failure_kind", "") or "").strip()
    if attempt <= 0 and not headline and not failure_kind:
        return ""
    if headline and failure_kind:
        return f"Attempt {attempt}/{failure_kind}: {headline}"
    if headline:
        return f"Attempt {attempt}: {headline}"
    return f"Attempt {attempt}: {failure_kind}"


def _build_compact_summary_document(
    *,
    issue_key: str,
    updated_memory: IssueWorkingMemory,
    retry_context: RetryContext | None,
    estimated_tokens: int,
    token_budget: int,
    estimator: str,
    reason: str,
    compact_brief: str = "",
) -> str:
    lines = [
        f"# Compact Summary for {issue_key}",
        "",
        f"- reason: {reason}",
        f"- estimator: {estimator}",
        f"- estimated_tokens: {estimated_tokens}",
        f"- token_budget: {token_budget}",
        f"- compaction_generation: {updated_memory.compaction_generation}",
        "",
        "## Current Working Memory",
        "",
        f"- current_goal: {updated_memory.current_goal}",
        f"- authoritative_workspace_state: {updated_memory.authoritative_workspace_state}",
        f"- best_known_patch_state: {updated_memory.best_known_patch_state}",
        f"- latest_strategy_summary: {updated_memory.latest_strategy_summary}",
        f"- latest_patch_summary: {updated_memory.latest_patch_summary}",
        f"- latest_verification: {updated_memory.latest_verification}",
        f"- latest_retryable_failure: {updated_memory.latest_retryable_failure}",
        f"- rollback_reason: {updated_memory.rollback_reason}",
        f"- compacted_history_summary: {updated_memory.compacted_history_summary}",
        f"- next_action: {updated_memory.next_action}",
    ]
    if compact_brief:
        lines.extend(
            [
                "",
                "## Authoritative Compact Brief",
                "",
                compact_brief,
            ]
        )
    if retry_context is not None:
        lines.extend(
            [
                "",
                "## Retry Context",
                "",
                f"- failure_kind: {retry_context.failure_kind}",
                f"- primary_failure_fingerprint: {retry_context.primary_failure_fingerprint}",
                f"- retry_history_total_attempts: {retry_context.retry_history_total_attempts}",
                "",
                build_latest_failure_excerpt(retry_context) or "- (none)",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
