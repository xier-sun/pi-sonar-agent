"""Runtime hook pipeline used by AgentRuntime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCallContext:
    """Hook context emitted around a normalized tool call."""

    tool_name: str
    decision: Any
    payload: dict[str, Any]
    preview: str = ""


@dataclass(frozen=True)
class AttemptFinalizeContext:
    """Hook context emitted before and after runtime finalization."""

    agent_error: str | None
    tool_uses: tuple[str, ...]
    forbidden_tool_uses: tuple[str, ...]
    last_tool_name: str | None
    saw_build_tool: bool
    exception: BaseException | None = None


class RuntimeHook(Protocol):
    """Optional hook interface for AgentRuntime lifecycle events."""

    def before_tool_call(self, context: ToolCallContext) -> None:
        """Called before a normalized tool call is processed."""

    def after_tool_call(self, context: ToolCallContext) -> None:
        """Called after a normalized tool call is processed."""

    def before_attempt_finalize(self, context: AttemptFinalizeContext) -> None:
        """Called before AgentRuntime returns or re-raises."""

    def after_attempt_finalize(self, context: AttemptFinalizeContext) -> None:
        """Called after AgentRuntime finalization bookkeeping is complete."""


class HookPipeline:
    """Execute runtime hooks in a stable order."""

    def __init__(self, hooks: Iterable[object] = ()) -> None:
        self._hooks = tuple(hooks)

    def before_tool_call(self, context: ToolCallContext) -> None:
        for hook in self._hooks:
            callback = getattr(hook, "before_tool_call", None)
            if callable(callback):
                callback(context)

    def after_tool_call(self, context: ToolCallContext) -> None:
        for hook in self._hooks:
            callback = getattr(hook, "after_tool_call", None)
            if callable(callback):
                callback(context)

    def before_attempt_finalize(self, context: AttemptFinalizeContext) -> None:
        for hook in self._hooks:
            callback = getattr(hook, "before_attempt_finalize", None)
            if callable(callback):
                callback(context)

    def after_attempt_finalize(self, context: AttemptFinalizeContext) -> None:
        for hook in self._hooks:
            callback = getattr(hook, "after_attempt_finalize", None)
            if callable(callback):
                callback(context)
