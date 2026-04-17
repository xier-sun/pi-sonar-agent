"""Canonical issue-level working memory used by simple-loop prompting."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from pi_sonar_agent.core.memory.memory_schema import (
    MemorySchemaError,
    ensure_string,
    ensure_tuple_of_strings,
    ensure_version,
)
from pi_sonar_agent.core.state import serialize_state, utc_now_iso

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue


ISSUE_WORKING_MEMORY_VERSION = 1


@dataclass(frozen=True)
class IssueWorkingMemory:
    """Single source of truth for the current issue working state."""

    version: int
    issue_key: str
    rule_id: str = ""
    current_goal: str = ""
    authoritative_workspace_state: str = ""
    best_known_patch_state: str = ""
    latest_strategy_summary: str = ""
    latest_patch_summary: str = ""
    accepted_constraints: tuple[str, ...] = ()
    rejected_strategies: tuple[str, ...] = ()
    stale_evidence: tuple[str, ...] = ()
    files_inspected: tuple[str, ...] = ()
    symbols_touched: tuple[str, ...] = ()
    latest_verification: str = ""
    latest_retryable_failure: str = ""
    rollback_reason: str = ""
    compacted_history_summary: str = ""
    compact_boundary_note: str = ""
    compact_summary_path: str = ""
    next_action: str = ""
    compaction_generation: int = 0
    last_updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IssueWorkingMemory":
        if not isinstance(payload, dict):
            raise MemorySchemaError("IssueWorkingMemory payload must be an object.")
        return cls(
            version=ensure_version(
                payload.get("version"),
                expected=ISSUE_WORKING_MEMORY_VERSION,
            ),
            issue_key=ensure_string(payload.get("issue_key"), field_name="issue_key", required=True),
            rule_id=ensure_string(payload.get("rule_id"), field_name="rule_id"),
            current_goal=ensure_string(payload.get("current_goal"), field_name="current_goal"),
            authoritative_workspace_state=ensure_string(
                payload.get("authoritative_workspace_state"),
                field_name="authoritative_workspace_state",
            ),
            best_known_patch_state=ensure_string(
                payload.get("best_known_patch_state"),
                field_name="best_known_patch_state",
            ),
            latest_strategy_summary=ensure_string(
                payload.get("latest_strategy_summary"),
                field_name="latest_strategy_summary",
            ),
            latest_patch_summary=ensure_string(
                payload.get("latest_patch_summary"),
                field_name="latest_patch_summary",
            ),
            accepted_constraints=ensure_tuple_of_strings(
                payload.get("accepted_constraints"),
                field_name="accepted_constraints",
            ),
            rejected_strategies=ensure_tuple_of_strings(
                payload.get("rejected_strategies"),
                field_name="rejected_strategies",
            ),
            stale_evidence=ensure_tuple_of_strings(
                payload.get("stale_evidence"),
                field_name="stale_evidence",
            ),
            files_inspected=ensure_tuple_of_strings(
                payload.get("files_inspected"),
                field_name="files_inspected",
            ),
            symbols_touched=ensure_tuple_of_strings(
                payload.get("symbols_touched"),
                field_name="symbols_touched",
            ),
            latest_verification=ensure_string(
                payload.get("latest_verification"),
                field_name="latest_verification",
            ),
            latest_retryable_failure=ensure_string(
                payload.get("latest_retryable_failure"),
                field_name="latest_retryable_failure",
            ),
            rollback_reason=ensure_string(
                payload.get("rollback_reason"),
                field_name="rollback_reason",
            ),
            compacted_history_summary=ensure_string(
                payload.get("compacted_history_summary"),
                field_name="compacted_history_summary",
            ),
            compact_boundary_note=ensure_string(
                payload.get("compact_boundary_note"),
                field_name="compact_boundary_note",
            ),
            compact_summary_path=ensure_string(
                payload.get("compact_summary_path"),
                field_name="compact_summary_path",
            ),
            next_action=ensure_string(payload.get("next_action"), field_name="next_action"),
            compaction_generation=int(payload.get("compaction_generation", 0) or 0),
            last_updated_at=ensure_string(
                payload.get("last_updated_at"),
                field_name="last_updated_at",
            ),
        )


def create_initial_issue_working_memory(issue: SonarIssue) -> IssueWorkingMemory:
    """Create the initial working-memory snapshot before the first attempt."""

    normalized_path = str(getattr(issue, "file_path", "") or "").replace("\\", "/").lstrip("/")
    line = int(getattr(issue, "start_line", 0) or getattr(issue, "line", 0) or 0)
    goal = (
        f"修复 {issue.rule} 在 {normalized_path}:{line} 的 Sonar issue，"
        "保持最小改动、编译通过，并优先保留业务语义。"
    )
    return IssueWorkingMemory(
        version=ISSUE_WORKING_MEMORY_VERSION,
        issue_key=str(getattr(issue, "key", "") or "").strip(),
        rule_id=str(getattr(issue, "rule", "") or "").strip(),
        current_goal=goal,
        authoritative_workspace_state="issue_baseline",
        accepted_constraints=(
            "只处理当前 Issue Key。",
            "优先保持公开行为和签名稳定。",
        ),
        files_inspected=((normalized_path,) if normalized_path else ()),
        next_action="先读取当前问题文件和定位片段，再做最小修复。",
        last_updated_at=utc_now_iso(),
    )


def merge_issue_working_memory(
    current: IssueWorkingMemory,
    *,
    authoritative_workspace_state: str | None = None,
    best_known_patch_state: str | None = None,
    latest_strategy_summary: str | None = None,
    latest_patch_summary: str | None = None,
    accepted_constraints: tuple[str, ...] | None = None,
    rejected_strategies: tuple[str, ...] | None = None,
    stale_evidence: tuple[str, ...] | None = None,
    files_inspected: tuple[str, ...] | None = None,
    symbols_touched: tuple[str, ...] | None = None,
    latest_verification: str | None = None,
    latest_retryable_failure: str | None = None,
    rollback_reason: str | None = None,
    compacted_history_summary: str | None = None,
    compact_boundary_note: str | None = None,
    compact_summary_path: str | None = None,
    next_action: str | None = None,
    current_goal: str | None = None,
    increment_compaction_generation: bool = False,
) -> IssueWorkingMemory:
    """Return an updated immutable working-memory snapshot."""

    return replace(
        current,
        current_goal=(current.current_goal if current_goal is None else str(current_goal).strip()),
        authoritative_workspace_state=(
            current.authoritative_workspace_state
            if authoritative_workspace_state is None
            else str(authoritative_workspace_state).strip()
        ),
        best_known_patch_state=(
            current.best_known_patch_state
            if best_known_patch_state is None
            else str(best_known_patch_state).strip()
        ),
        latest_strategy_summary=(
            current.latest_strategy_summary
            if latest_strategy_summary is None
            else str(latest_strategy_summary).strip()
        ),
        latest_patch_summary=(
            current.latest_patch_summary
            if latest_patch_summary is None
            else str(latest_patch_summary).strip()
        ),
        accepted_constraints=_merge_string_tuples(
            current.accepted_constraints,
            accepted_constraints,
        ),
        rejected_strategies=_merge_string_tuples(
            current.rejected_strategies,
            rejected_strategies,
        ),
        stale_evidence=_merge_string_tuples(current.stale_evidence, stale_evidence),
        files_inspected=_merge_string_tuples(current.files_inspected, files_inspected),
        symbols_touched=_merge_string_tuples(current.symbols_touched, symbols_touched),
        latest_verification=(
            current.latest_verification
            if latest_verification is None
            else str(latest_verification).strip()
        ),
        latest_retryable_failure=(
            current.latest_retryable_failure
            if latest_retryable_failure is None
            else str(latest_retryable_failure).strip()
        ),
        rollback_reason=(
            current.rollback_reason if rollback_reason is None else str(rollback_reason).strip()
        ),
        compacted_history_summary=(
            current.compacted_history_summary
            if compacted_history_summary is None
            else str(compacted_history_summary).strip()
        ),
        compact_boundary_note=(
            current.compact_boundary_note
            if compact_boundary_note is None
            else str(compact_boundary_note).strip()
        ),
        compact_summary_path=(
            current.compact_summary_path
            if compact_summary_path is None
            else str(compact_summary_path).strip()
        ),
        next_action=(current.next_action if next_action is None else str(next_action).strip()),
        compaction_generation=(
            current.compaction_generation + 1
            if increment_compaction_generation
            else current.compaction_generation
        ),
        last_updated_at=utc_now_iso(),
    )


def render_issue_working_memory(memory: IssueWorkingMemory | None) -> str:
    """Render the canonical working-memory snapshot for prompting."""

    if memory is None:
        return ""
    lines = ["【当前工作记忆】"]
    if memory.current_goal:
        lines.append(f"- 当前目标: {memory.current_goal}")
    if memory.authoritative_workspace_state:
        lines.append(f"- 当前工作区状态: {memory.authoritative_workspace_state}")
    if memory.best_known_patch_state:
        lines.append(f"- 当前最优 patch: {memory.best_known_patch_state}")
    if memory.latest_strategy_summary:
        lines.append(f"- 最近修法: {memory.latest_strategy_summary}")
    if memory.latest_patch_summary:
        lines.append(f"- 最近改动摘要: {memory.latest_patch_summary}")
    if memory.accepted_constraints:
        lines.append(f"- 已确认约束: {'; '.join(memory.accepted_constraints[:4])}")
    if memory.rejected_strategies:
        lines.append(f"- 已否定策略: {'; '.join(memory.rejected_strategies[:4])}")
    if memory.latest_verification:
        lines.append(f"- 最近验证结果: {memory.latest_verification}")
    if memory.latest_retryable_failure:
        lines.append(f"- 最近失败主因: {memory.latest_retryable_failure}")
    if memory.rollback_reason:
        lines.append(f"- 回滚说明: {memory.rollback_reason}")
    if memory.compact_boundary_note:
        lines.append(f"- 压缩边界: {memory.compact_boundary_note}")
    if memory.compacted_history_summary:
        lines.append(f"- 历史压缩摘要: {memory.compacted_history_summary}")
    if memory.compact_summary_path:
        lines.append(f"- 压缩摘要工件: {memory.compact_summary_path}")
    if memory.stale_evidence:
        lines.append(f"- 已失效旧证据: {'; '.join(memory.stale_evidence[:3])}")
    if memory.next_action:
        lines.append(f"- 本轮下一步: {memory.next_action}")
    return "\n".join(lines)


def _merge_string_tuples(
    current: tuple[str, ...],
    new_values: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if new_values is None:
        return tuple(str(item).strip() for item in current if str(item).strip())
    seen: list[str] = []
    for item in (*current, *new_values):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.append(text)
    return tuple(seen)
