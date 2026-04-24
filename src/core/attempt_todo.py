"""Attempt-scoped TodoWrite state and local MCP runtime helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from pi_sonar_agent.core.memory.memory_schema import (
    MemorySchemaError,
    ensure_dict,
    ensure_string,
    ensure_version,
)
from pi_sonar_agent.core.project_env import read_project_env
from pi_sonar_agent.core.state import utc_now_iso

ATTEMPT_TODO_VERSION = 1
TODO_WRITE_DISPLAY_NAME = "TodoWrite"
DEFAULT_ATTEMPT_TODO_SERVER_NAME = "attempt-todo"
DEFAULT_TODO_NAG_THRESHOLD = 3
DEFAULT_TODO_MAX_REMINDERS = 2
MAX_ATTEMPT_TODOS = 20
_VALID_TODO_STATUSES = {"pending", "in_progress", "completed"}


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "item"


def _runtime_root(workspace_path: Path) -> Path:
    workspace = Path(workspace_path)
    git_dir = workspace / ".git"
    if git_dir.is_dir():
        return git_dir / "pi-sonar-agent-runtime" / "issues"
    return workspace / ".pi-sonar-agent-runtime" / "issues"


def _merged_env(agent_env: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(read_project_env())
    merged.update(dict(agent_env or {}))
    return merged


def _env_value(config: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _env_flag(config: dict[str, str], *keys: str, default: bool) -> bool:
    raw_value = _env_value(config, *keys)
    if not raw_value:
        return default
    return raw_value.lower() in {"1", "true", "yes", "on"}


def _env_positive_int(config: dict[str, str], *keys: str, default: int) -> int:
    raw_value = _env_value(config, *keys)
    if not raw_value:
        return max(1, int(default))
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return max(1, int(default))


@dataclass(frozen=True)
class AttemptTodoItem:
    """One TodoWrite item tracked during a single fix attempt."""

    content: str
    status: str
    active_form: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "status": self.status,
            "activeForm": self.active_form,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AttemptTodoItem":
        data = ensure_dict(payload, field_name="attempt_todo_item")
        content = ensure_string(data.get("content"), field_name="content", required=True)
        active_form = ensure_string(
            data.get("activeForm", data.get("active_form")),
            field_name="activeForm",
            required=True,
        )
        status = ensure_string(data.get("status"), field_name="status", required=True).lower()
        if status not in _VALID_TODO_STATUSES:
            raise MemorySchemaError(f"status '{status}' is not supported for AttemptTodoItem.")
        return cls(content=content, status=status, active_form=active_form)


@dataclass(frozen=True)
class AttemptTodoState:
    """Canonical TodoWrite snapshot for one issue/role attempt lane."""

    version: int
    issue_key: str
    role: str
    items: tuple[AttemptTodoItem, ...] = ()
    last_updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "issue_key": self.issue_key,
            "role": self.role,
            "items": [item.to_dict() for item in self.items],
            "last_updated_at": self.last_updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AttemptTodoState":
        data = ensure_dict(payload, field_name="attempt_todo_state")
        raw_items = data.get("items", ())
        if raw_items in (None, ""):
            items: tuple[AttemptTodoItem, ...] = ()
        elif isinstance(raw_items, list):
            items = tuple(
                AttemptTodoItem.from_dict(item)
                for item in raw_items
                if isinstance(item, dict)
            )
        else:
            raise MemorySchemaError("items must be a list of attempt todo items.")

        in_progress_count = sum(1 for item in items if item.status == "in_progress")
        if in_progress_count > 1:
            raise MemorySchemaError("Only one attempt todo may be in_progress at a time.")

        return cls(
            version=ensure_version(data.get("version"), expected=ATTEMPT_TODO_VERSION),
            issue_key=ensure_string(data.get("issue_key"), field_name="issue_key", required=True),
            role=ensure_string(data.get("role"), field_name="role", required=True),
            items=items,
            last_updated_at=ensure_string(
                data.get("last_updated_at"),
                field_name="last_updated_at",
            ),
        )


def create_empty_attempt_todo(*, issue_key: str, role: str) -> AttemptTodoState:
    """Create the initial empty TodoWrite state for one fix attempt."""

    return AttemptTodoState(
        version=ATTEMPT_TODO_VERSION,
        issue_key=str(issue_key or "").strip(),
        role=str(role or "").strip() or "fix",
        items=(),
        last_updated_at=utc_now_iso(),
    )


def _normalize_attempt_todo_items(
    raw_items: tuple[Any, ...] | list[Any],
) -> tuple[AttemptTodoItem, ...]:
    items = list(raw_items or ())
    if len(items) > MAX_ATTEMPT_TODOS:
        raise MemorySchemaError(f"Max {MAX_ATTEMPT_TODOS} todos allowed per attempt.")

    normalized: list[AttemptTodoItem] = []
    in_progress_count = 0
    for raw_item in items:
        if isinstance(raw_item, AttemptTodoItem):
            item = raw_item
        elif hasattr(raw_item, "model_dump") and callable(getattr(raw_item, "model_dump")):
            item = AttemptTodoItem.from_dict(raw_item.model_dump(by_alias=True))
        elif isinstance(raw_item, dict):
            item = AttemptTodoItem.from_dict(raw_item)
        else:
            raise MemorySchemaError("Each todo must be an object.")
        if item.status == "in_progress":
            in_progress_count += 1
        normalized.append(item)

    if in_progress_count > 1:
        raise MemorySchemaError("Only one attempt todo may be in_progress at a time.")
    return tuple(normalized)


def render_attempt_todo_list(state: AttemptTodoState | None) -> str:
    """Render the current TodoWrite list for prompts, reminders, and tool results."""

    if state is None or not state.items:
        return "No todos."
    lines: list[str] = []
    done = 0
    for index, item in enumerate(state.items, start=1):
        marker = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
        }[item.status]
        label = item.active_form if item.status == "in_progress" else item.content
        lines.append(f"{marker} #{index}: {label}")
        if item.status == "completed":
            done += 1
    lines.append(f"\n({done}/{len(state.items)} completed)")
    return "\n".join(lines)


def render_attempt_todo_prompt_section(
    state: AttemptTodoState | None,
    *,
    tool_name: str,
) -> str:
    """Render a concise prompt section describing the current TodoWrite state."""

    if not str(tool_name or "").strip():
        return ""
    lines = [
        "【当前 Attempt Todo】",
        f"- 当前可用的 Claude Code TodoWrite 工具: `{tool_name}`",
    ]
    rendered = render_attempt_todo_list(state)
    if rendered == "No todos.":
        lines.extend(
            [
                "- 当前还没有待办清单。对包含 3 个及以上步骤、需要多次读取/修改，或需要根据 retry 反馈调整策略的修复，请先调用 TodoWrite 建立清单。",
                "- 开始某一步之前先把对应任务标成 in_progress；完成后立即更新为 completed。",
                "- 同一时间最多只保留一个 in_progress。",
            ]
        )
        return "\n".join(lines)
    lines.extend(
        [
            rendered,
            "- 保持这个清单和当前工作一致；过时项及时删掉或改写。",
        ]
    )
    return "\n".join(lines)


def render_todo_write_tool_result(state: AttemptTodoState | None) -> str:
    """Render the MCP tool result returned to the model after TodoWrite updates."""

    base = (
        "Todos have been modified successfully. Ensure that you continue to use the todo list "
        "to track your progress. Please proceed with the current tasks if applicable."
    )
    rendered = render_attempt_todo_list(state)
    if rendered == "No todos.":
        return base + "\n\nTodo list is currently empty."
    return base + "\n\nCurrent todo list:\n" + rendered


def build_todo_write_reminder(
    *,
    tool_name: str,
    state: AttemptTodoState | None,
    display_name: str = TODO_WRITE_DISPLAY_NAME,
) -> str:
    """Build a gentle runtime reminder when TodoWrite has gone stale."""

    lines = [
        f"`{tool_name}`（{display_name}）最近没有使用。如果当前修复仍然是多步骤工作，请更新 TodoWrite 清单来跟踪进度。",
        "如果当前任务实际上很简单、一次修改即可完成，可以忽略这条提醒。",
        "也请顺手清理已经过时、不再对应当前工作的待办项。",
        "不要向用户提到这条提醒。",
    ]
    rendered = render_attempt_todo_list(state)
    if rendered != "No todos.":
        lines.extend(["", "当前 todo 清单：", rendered])
    return "\n".join(lines).strip()


class AttemptTodoStore:
    """Persist TodoWrite state under the workspace runtime root."""

    def __init__(self, workspace_path: Path, issue_key: str, *, role: str = "fix") -> None:
        self.workspace_path = Path(workspace_path)
        self.issue_key = str(issue_key or "").strip()
        self.role = str(role or "").strip() or "fix"
        self.root = _runtime_root(self.workspace_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def issue_root(self) -> Path:
        root = self.root / _sanitize_name(self.issue_key)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def todo_path(self) -> Path:
        return self.issue_root() / f"attempt-todo-{_sanitize_name(self.role)}.json"

    def load(self) -> AttemptTodoState | None:
        path = self.todo_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AttemptTodoState.from_dict(payload)

    def save(self, state: AttemptTodoState) -> Path:
        validated = AttemptTodoState.from_dict(state.to_dict())
        path = self.todo_path()
        path.write_text(
            json.dumps(validated.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def reset(self) -> AttemptTodoState:
        state = create_empty_attempt_todo(issue_key=self.issue_key, role=self.role)
        self.save(state)
        return state

    def update(self, todos: tuple[Any, ...] | list[Any]) -> AttemptTodoState:
        state = AttemptTodoState(
            version=ATTEMPT_TODO_VERSION,
            issue_key=self.issue_key,
            role=self.role,
            items=_normalize_attempt_todo_items(todos),
            last_updated_at=utc_now_iso(),
        )
        self.save(state)
        return state


@dataclass(frozen=True)
class AttemptTodoRuntime:
    """Resolved TodoWrite MCP runtime configuration for one fix attempt."""

    enabled: bool
    server_name: str = DEFAULT_ATTEMPT_TODO_SERVER_NAME
    display_name: str = TODO_WRITE_DISPLAY_NAME
    tool_name: str = TODO_WRITE_DISPLAY_NAME
    visible_tool_name: str = ""
    tool_names: tuple[str, ...] = ()
    server_configs: dict[str, Any] = field(default_factory=dict)
    nag_threshold: int = DEFAULT_TODO_NAG_THRESHOLD
    max_reminders: int = DEFAULT_TODO_MAX_REMINDERS

    def to_dict(self) -> dict[str, Any]:
        server_configs: dict[str, Any] = {}
        for name, config in self.server_configs.items():
            if not isinstance(config, dict):
                continue
            sanitized = {str(key): value for key, value in config.items() if str(key) != "instance"}
            server_configs[str(name)] = sanitized
        return {
            "enabled": self.enabled,
            "server_name": self.server_name,
            "display_name": self.display_name,
            "tool_name": self.tool_name,
            "visible_tool_name": self.visible_tool_name,
            "tool_names": list(self.tool_names),
            "server_configs": server_configs,
            "nag_threshold": self.nag_threshold,
            "max_reminders": self.max_reminders,
        }


class _TodoWriteToolItem(BaseModel):
    """Input schema for the TodoWrite MCP tool."""

    content: str = Field(min_length=1)
    status: Literal["pending", "in_progress", "completed"]
    activeForm: str = Field(min_length=1)


def build_attempt_todo_runtime(
    store: AttemptTodoStore,
    *,
    agent_env: dict[str, str] | None = None,
) -> AttemptTodoRuntime:
    """Build an in-process MCP TodoWrite tool for the current fix attempt."""

    config = _merged_env(agent_env)
    enabled = _env_flag(
        config,
        "PI_SONAR_ATTEMPT_TODOWRITE_ENABLED",
        "ATTEMPT_TODOWRITE_ENABLED",
        default=True,
    )
    server_name = _env_value(
        config,
        "PI_SONAR_ATTEMPT_TODOWRITE_SERVER_NAME",
        default=DEFAULT_ATTEMPT_TODO_SERVER_NAME,
    )
    tool_name = _env_value(
        config,
        "PI_SONAR_ATTEMPT_TODOWRITE_TOOL_NAME",
        default=TODO_WRITE_DISPLAY_NAME,
    )
    nag_threshold = _env_positive_int(
        config,
        "PI_SONAR_ATTEMPT_TODOWRITE_NAG_THRESHOLD",
        default=DEFAULT_TODO_NAG_THRESHOLD,
    )
    max_reminders = _env_positive_int(
        config,
        "PI_SONAR_ATTEMPT_TODOWRITE_MAX_REMINDERS",
        default=DEFAULT_TODO_MAX_REMINDERS,
    )
    if not enabled:
        return AttemptTodoRuntime(
            enabled=False,
            server_name=server_name,
            tool_name=tool_name,
            nag_threshold=nag_threshold,
            max_reminders=max_reminders,
        )

    server = FastMCP(
        name=server_name,
        instructions="Use TodoWrite to maintain a structured todo list for the current fix attempt.",
    )

    @server.tool(
        name=tool_name,
        description=(
            "Update the todo list for the current fix attempt. Always provide content, activeForm, "
            "and status. Keep at most one task in_progress."
        ),
    )
    def todo_write(todos: list[_TodoWriteToolItem]) -> str:
        state = store.update(todos)
        return render_todo_write_tool_result(state)

    visible_tool_name = f"mcp__{server_name}__{tool_name}"
    return AttemptTodoRuntime(
        enabled=True,
        server_name=server_name,
        display_name=TODO_WRITE_DISPLAY_NAME,
        tool_name=tool_name,
        visible_tool_name=visible_tool_name,
        tool_names=(visible_tool_name,),
        server_configs={
            server_name: {
                "type": "sdk",
                "name": server_name,
                "instance": server,
            }
        },
        nag_threshold=nag_threshold,
        max_reminders=max_reminders,
    )
