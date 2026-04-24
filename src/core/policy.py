"""Compatibility wrapper around the centralized runtime permission manager."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pi_sonar_agent.core.hooks import RuntimeHook, ToolCallContext
from pi_sonar_agent.core.permission_manager import (
    PermissionContext,
    PermissionDecision,
    PermissionManager,
    normalize_tool_name,
)
from pi_sonar_agent.core.registry import ToolKind, ToolRegistry
from pi_sonar_agent.core.tool_surface import CONTROLLED_BASH_TOOL, CONTROLLED_SHELL_DISPLAY_NAME

ToolDecision = PermissionDecision


@dataclass
class ToolUsageTracker:
    """Accumulated tool-usage facts for a single issue attempt."""

    tool_uses: list[str] = field(default_factory=list)
    forbidden_tool_uses: list[str] = field(default_factory=list)
    warning_tool_uses: list[str] = field(default_factory=list)
    last_tool_name: str | None = None
    saw_build_tool: bool = False

    def snapshot(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str | None, bool]:
        """Return an immutable snapshot for runtime results."""

        return (
            tuple(self.tool_uses),
            tuple(self.forbidden_tool_uses),
            tuple(self.warning_tool_uses),
            self.last_tool_name,
            self.saw_build_tool,
        )


class ToolPolicy:
    """Backwards-compatible facade over PermissionManager."""

    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: Iterable[str],
        *,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.permission_manager = PermissionManager(
            registry,
            tuple(allowed_tools),
            workspace_root=workspace_root,
        )

    def allowed_tool_names(self) -> tuple[str, ...]:
        """Return the stable allowlist passed to the SDK gateway."""

        return self.permission_manager.allowed_tool_names()

    def classify(self, tool_name: str, payload: dict[str, object] | None = None) -> ToolDecision:
        """Return the classification/allowance decision for a tool."""

        return self.permission_manager.decide(
            PermissionContext(
                tool_name=tool_name,
                payload=dict(payload or {}),
            )
        )

    def is_forbidden_tool(self, tool_name: str, payload: dict[str, object] | None = None) -> bool:
        """Return True when the tool use violates the current runtime policy."""

        return self.permission_manager.is_forbidden_tool(tool_name, payload)

    def is_build_tool(self, tool_name: str) -> bool:
        """Return True when the tool is a controlled build/test tool."""

        return self.permission_manager.is_build_tool(tool_name)


class ToolPolicyHook(RuntimeHook):
    """Record tool-usage facts for the current attempt."""

    def __init__(self, policy: ToolPolicy, tracker: ToolUsageTracker) -> None:
        self.policy = policy
        self.tracker = tracker

    def before_tool_call(self, context: ToolCallContext) -> None:
        return None

    @staticmethod
    def _format_policy_violation_label(context: ToolCallContext) -> str:
        if context.tool_name != CONTROLLED_BASH_TOOL:
            return context.tool_name
        command = str((context.payload or {}).get("command") or "").strip()
        if not command:
            return CONTROLLED_SHELL_DISPLAY_NAME
        preview = command if len(command) <= 80 else command[:77].rstrip() + "..."
        return f"{CONTROLLED_SHELL_DISPLAY_NAME}({preview})"

    def after_tool_call(self, context: ToolCallContext) -> None:
        tool_name = context.tool_name
        self.tracker.tool_uses.append(tool_name)
        self.tracker.last_tool_name = tool_name
        if context.decision.policy_violation:
            self.tracker.forbidden_tool_uses.append(self._format_policy_violation_label(context))
        elif context.decision.severity == "warning":
            self.tracker.warning_tool_uses.append(self._format_policy_violation_label(context))
        if context.decision.kind == ToolKind.CONTROLLED and "build" in context.decision.tags:
            self.tracker.saw_build_tool = True

    def before_attempt_finalize(self, context) -> None:
        return None

    def after_attempt_finalize(self, context) -> None:
        return None
