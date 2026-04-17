"""Role-specific child-agent working memory with lightweight compaction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pi_sonar_agent.core.memory.memory_schema import (
    MemorySchemaError,
    ensure_dict,
    ensure_int,
    ensure_string,
    ensure_tuple_of_strings,
    ensure_version,
)
from pi_sonar_agent.core.state import serialize_state, utc_now_iso


CHILD_AGENT_MEMORY_VERSION = 1
CHILD_AGENT_MAX_DETAILED_TURNS = 3
CHILD_AGENT_COMPACTION_CHAR_BUDGET = 2200
CHILD_AGENT_TOKEN_BUDGET = 1200


@dataclass(frozen=True)
class ChildAgentMemoryTurn:
    """One structured memory turn for a child agent role."""

    attempt_number: int
    decision: str = ""
    summary: str = ""
    findings: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    workspace_state: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChildAgentMemoryTurn":
        data = ensure_dict(payload, field_name="child_agent_memory_turn")
        return cls(
            attempt_number=ensure_int(data.get("attempt_number"), field_name="attempt_number"),
            decision=ensure_string(data.get("decision"), field_name="decision"),
            summary=ensure_string(data.get("summary"), field_name="summary"),
            findings=ensure_tuple_of_strings(data.get("findings"), field_name="findings"),
            constraints=ensure_tuple_of_strings(
                data.get("constraints"),
                field_name="constraints",
            ),
            workspace_state=ensure_string(
                data.get("workspace_state"),
                field_name="workspace_state",
            ),
            timestamp=ensure_string(data.get("timestamp"), field_name="timestamp"),
        )


@dataclass(frozen=True)
class ChildAgentMemory:
    """Canonical per-role child-agent memory."""

    version: int
    issue_key: str
    role: str
    current_focus: str = ""
    latest_summary: str = ""
    latest_decision: str = ""
    latest_findings: tuple[str, ...] = ()
    latest_constraints: tuple[str, ...] = ()
    latest_workspace_state: str = ""
    compacted_history_summary: str = ""
    compact_boundary_note: str = ""
    next_action: str = ""
    compaction_generation: int = 0
    turns: tuple[ChildAgentMemoryTurn, ...] = ()
    last_updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChildAgentMemory":
        data = ensure_dict(payload, field_name="child_agent_memory")
        raw_turns = data.get("turns", ())
        if raw_turns in (None, ""):
            turns: tuple[ChildAgentMemoryTurn, ...] = ()
        elif isinstance(raw_turns, list):
            turns = tuple(
                ChildAgentMemoryTurn.from_dict(item)
                for item in raw_turns
                if isinstance(item, dict)
            )
        else:
            raise MemorySchemaError("turns must be a list of child-agent memory turns.")

        return cls(
            version=ensure_version(
                data.get("version"),
                expected=CHILD_AGENT_MEMORY_VERSION,
            ),
            issue_key=ensure_string(data.get("issue_key"), field_name="issue_key", required=True),
            role=ensure_string(data.get("role"), field_name="role", required=True),
            current_focus=ensure_string(data.get("current_focus"), field_name="current_focus"),
            latest_summary=ensure_string(data.get("latest_summary"), field_name="latest_summary"),
            latest_decision=ensure_string(data.get("latest_decision"), field_name="latest_decision"),
            latest_findings=ensure_tuple_of_strings(
                data.get("latest_findings"),
                field_name="latest_findings",
            ),
            latest_constraints=ensure_tuple_of_strings(
                data.get("latest_constraints"),
                field_name="latest_constraints",
            ),
            latest_workspace_state=ensure_string(
                data.get("latest_workspace_state"),
                field_name="latest_workspace_state",
            ),
            compacted_history_summary=ensure_string(
                data.get("compacted_history_summary"),
                field_name="compacted_history_summary",
            ),
            compact_boundary_note=ensure_string(
                data.get("compact_boundary_note"),
                field_name="compact_boundary_note",
            ),
            next_action=ensure_string(data.get("next_action"), field_name="next_action"),
            compaction_generation=ensure_int(
                data.get("compaction_generation"),
                field_name="compaction_generation",
            ),
            turns=turns,
            last_updated_at=ensure_string(
                data.get("last_updated_at"),
                field_name="last_updated_at",
            ),
        )


def create_initial_child_agent_memory(*, issue_key: str, role: str, focus: str) -> ChildAgentMemory:
    """Create the initial child-agent memory snapshot."""

    return ChildAgentMemory(
        version=CHILD_AGENT_MEMORY_VERSION,
        issue_key=str(issue_key or "").strip(),
        role=str(role or "").strip(),
        current_focus=str(focus or "").strip(),
        next_action=str(focus or "").strip(),
        last_updated_at=utc_now_iso(),
    )


def append_child_agent_memory_turn(
    current: ChildAgentMemory,
    *,
    attempt_number: int,
    decision: str,
    summary: str,
    findings: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    workspace_state: str = "",
    next_action: str = "",
    current_focus: str | None = None,
) -> ChildAgentMemory:
    """Append one role-memory turn and compact older details when needed."""

    next_turn = ChildAgentMemoryTurn(
        attempt_number=int(attempt_number or 0),
        decision=str(decision or "").strip(),
        summary=str(summary or "").strip(),
        findings=tuple(str(item).strip() for item in findings if str(item).strip()),
        constraints=tuple(str(item).strip() for item in constraints if str(item).strip()),
        workspace_state=str(workspace_state or "").strip(),
        timestamp=utc_now_iso(),
    )
    turns = tuple((*current.turns, next_turn))
    updated = replace(
        current,
        current_focus=(
            current.current_focus if current_focus is None else str(current_focus or "").strip()
        ),
        latest_summary=next_turn.summary,
        latest_decision=next_turn.decision,
        latest_findings=next_turn.findings,
        latest_constraints=next_turn.constraints,
        latest_workspace_state=next_turn.workspace_state,
        next_action=str(next_action or "").strip(),
        turns=turns,
        last_updated_at=utc_now_iso(),
    )
    return compact_child_agent_memory(updated)


def compact_child_agent_memory(memory: ChildAgentMemory) -> ChildAgentMemory:
    """Keep recent role turns detailed and compact older history into one summary."""

    turns = tuple(memory.turns or ())
    if not turns:
        return memory

    rendered = render_child_agent_memory(memory)
    estimated_tokens = _estimate_token_count(rendered)
    if (
        len(turns) <= CHILD_AGENT_MAX_DETAILED_TURNS
        and len(rendered) <= CHILD_AGENT_COMPACTION_CHAR_BUDGET
        and estimated_tokens <= CHILD_AGENT_TOKEN_BUDGET
    ):
        return memory

    older_turns = turns[:-CHILD_AGENT_MAX_DETAILED_TURNS]
    recent_turns = turns[-CHILD_AGENT_MAX_DETAILED_TURNS:]
    compact_lines = [
        _render_turn_line(item)
        for item in older_turns
        if _render_turn_line(item)
    ]
    compact_summary = " | ".join(compact_lines).strip()
    if memory.compacted_history_summary:
        compact_summary = " ".join(
            part
            for part in (memory.compacted_history_summary, compact_summary)
            if str(part).strip()
        ).strip()
    return replace(
        memory,
        compacted_history_summary=compact_summary,
        compact_boundary_note=(
            "更早子agent细节已压缩，以下仅保留最近几轮和权威摘要。"
        ),
        compaction_generation=memory.compaction_generation + 1,
        turns=recent_turns,
        last_updated_at=utc_now_iso(),
    )


def render_child_agent_memory(memory: ChildAgentMemory | None) -> str:
    """Render a concise prompt-friendly child-agent memory section."""

    if memory is None:
        return ""
    role_label = {
        "fix": "Fix 子Agent记忆",
        "review": "Review 子Agent记忆",
        "main": "Main 裁决记忆",
    }.get(str(memory.role or "").strip(), "子Agent记忆")
    lines = [f"【{role_label}】"]
    if memory.current_focus:
        lines.append(f"- 当前关注: {memory.current_focus}")
    if memory.latest_summary:
        lines.append(f"- 最近摘要: {memory.latest_summary}")
    if memory.latest_decision:
        lines.append(f"- 最近结论: {memory.latest_decision}")
    if memory.latest_constraints:
        lines.append("- 最近约束: " + "；".join(memory.latest_constraints[:3]))
    if memory.compacted_history_summary:
        lines.append(f"- 历史压缩摘要: {memory.compacted_history_summary}")
    if memory.compact_boundary_note:
        lines.append(f"- 压缩边界: {memory.compact_boundary_note}")
    if memory.turns:
        lines.append("- 最近几轮:")
        for item in memory.turns[-2:]:
            rendered = _render_turn_line(item)
            if rendered:
                lines.append(f"  - {rendered}")
    if memory.next_action:
        lines.append(f"- 下一步: {memory.next_action}")
    return "\n".join(lines).strip()


def _render_turn_line(turn: ChildAgentMemoryTurn) -> str:
    parts = [f"attempt {turn.attempt_number}"]
    if turn.decision:
        parts.append(f"decision={turn.decision}")
    if turn.summary:
        parts.append(turn.summary)
    if turn.constraints:
        parts.append("constraints=" + "；".join(turn.constraints[:2]))
    return " | ".join(part for part in parts if str(part).strip()).strip()


def _estimate_token_count(text: str) -> int:
    normalized = str(text or "")
    if not normalized:
        return 0
    ascii_chars = sum(1 for char in normalized if ord(char) < 128)
    non_ascii_chars = max(len(normalized) - ascii_chars, 0)
    punctuation = sum(1 for char in normalized if char in "{}[]()<>=:;,.`|/-_")
    line_breaks = normalized.count("\n")
    return max(1, (ascii_chars // 4) + non_ascii_chars + (punctuation // 6) + (line_breaks // 2))
