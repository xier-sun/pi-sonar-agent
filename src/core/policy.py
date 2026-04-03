"""Tool policy and hook helpers for issue-fix runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pi_sonar_agent.core.hooks import RuntimeHook, ToolCallContext
from pi_sonar_agent.core.registry import ToolKind, ToolRegistry


@dataclass(frozen=True)
class ToolDecision:
    """Classification and allowlist decision for a single tool."""

    tool_name: str
    allowed: bool
    kind: ToolKind
    tags: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class ToolUsageTracker:
    """Accumulated tool-usage facts for a single issue attempt."""

    tool_uses: list[str] = field(default_factory=list)
    forbidden_tool_uses: list[str] = field(default_factory=list)
    last_tool_name: str | None = None
    saw_build_tool: bool = False

    def snapshot(self) -> tuple[tuple[str, ...], tuple[str, ...], str | None, bool]:
        """Return an immutable snapshot for runtime results."""

        return (
            tuple(self.tool_uses),
            tuple(self.forbidden_tool_uses),
            self.last_tool_name,
            self.saw_build_tool,
        )


class ToolPolicy:
    """Classify tool uses and expose the runtime allowlist."""

    def __init__(self, registry: ToolRegistry, allowed_tools: Iterable[str]) -> None:
        self.registry = registry
        self._allowed_tools = tuple(
            dict.fromkeys(str(name) for name in allowed_tools if str(name).strip())
        )
        self._allowed_lookup = frozenset(self._allowed_tools)

    def allowed_tool_names(self) -> tuple[str, ...]:
        """Return the stable allowlist passed to the SDK gateway."""

        return self._allowed_tools

    def classify(self, tool_name: str) -> ToolDecision:
        """Return the classification/allowance decision for a tool."""

        spec = self.registry.get(tool_name)
        if spec is None:
            return ToolDecision(
                tool_name=tool_name,
                allowed=False,
                kind=ToolKind.UNKNOWN,
                reason="Tool is not registered for the issue-fix runtime.",
            )

        if spec.kind == ToolKind.FORBIDDEN:
            return ToolDecision(
                tool_name=tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Tool is explicitly forbidden during issue fixing.",
            )

        if tool_name in self._allowed_lookup:
            return ToolDecision(
                tool_name=tool_name,
                allowed=True,
                kind=spec.kind,
                tags=spec.tags,
            )

        if spec.kind == ToolKind.CONTROLLED:
            return ToolDecision(
                tool_name=tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Tool is controlled by the outer workflow, not the model runtime.",
            )

        return ToolDecision(
            tool_name=tool_name,
            allowed=False,
            kind=spec.kind,
            tags=spec.tags,
            reason="Tool is registered but not in the current allowlist.",
        )

    def is_forbidden_tool(self, tool_name: str) -> bool:
        """Return True when the tool is explicitly forbidden."""

        return self.classify(tool_name).kind == ToolKind.FORBIDDEN

    def is_build_tool(self, tool_name: str) -> bool:
        """Return True when the tool is a controlled build/test tool."""

        decision = self.classify(tool_name)
        return decision.kind == ToolKind.CONTROLLED and "build" in decision.tags


class ToolPolicyHook(RuntimeHook):
    """Record tool-usage facts for the current attempt."""

    def __init__(self, policy: ToolPolicy, tracker: ToolUsageTracker) -> None:
        self.policy = policy
        self.tracker = tracker

    def before_tool_call(self, context: ToolCallContext) -> None:
        return None

    def after_tool_call(self, context: ToolCallContext) -> None:
        tool_name = context.tool_name
        self.tracker.tool_uses.append(tool_name)
        self.tracker.last_tool_name = tool_name
        if self.policy.is_forbidden_tool(tool_name):
            self.tracker.forbidden_tool_uses.append(tool_name)
        if self.policy.is_build_tool(tool_name):
            self.tracker.saw_build_tool = True

    def before_attempt_finalize(self, context) -> None:
        return None

    def after_attempt_finalize(self, context) -> None:
        return None
