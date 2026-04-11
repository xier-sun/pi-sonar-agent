"""Normalized model-gateway contracts and runtime events."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

StderrHandler = Callable[[str], None]


@dataclass(frozen=True)
class GatewayRequest:
    """Normalized request passed from the agent runtime to a model gateway."""

    system_prompt: str
    user_prompt: str
    cwd: str
    tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    max_turns: int
    max_budget_usd: float
    env: dict[str, str]
    model: str | None = None
    extra_args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    stderr_handler: StderrHandler | None = None


@dataclass(frozen=True)
class GatewayAbortResult:
    """Summary of how a gateway session was interrupted and cleaned up."""

    reason: str
    actions: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def format_abort_result(abort_result: GatewayAbortResult | None) -> str:
    """Format abort details for logs and timeout diagnostics."""

    if abort_result is None:
        return ""

    sections = [f"取消原因: {abort_result.reason}"]
    if abort_result.actions:
        sections.append("清理动作: " + ", ".join(abort_result.actions))
    if abort_result.errors:
        sections.append("清理异常: " + " | ".join(abort_result.errors))
    return "\n".join(sections)


def format_timeout_abort_suffix(abort_result: GatewayAbortResult | None) -> str:
    """Append abort details to timeout messages when available."""

    abort_details = format_abort_result(abort_result)
    if not abort_details:
        return ""
    return "\n" + abort_details


@dataclass(frozen=True)
class ToolCallEvent:
    """Normalized tool-call event emitted by a model gateway."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    preview: str = ""


@dataclass(frozen=True)
class TextEvent:
    """Normalized assistant text event emitted by a model gateway."""

    text: str
    block_type: str = "TextBlock"


@dataclass(frozen=True)
class ResultEvent:
    """Normalized final result event emitted by a model gateway."""

    total_cost_usd: float = 0.0
    agent_error: str | None = None


@dataclass(frozen=True)
class TraceEvent:
    """Normalized trace event for unhandled SDK payload types."""

    message_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    preview: str = ""


GatewayEvent = ToolCallEvent | TextEvent | ResultEvent | TraceEvent


class ModelGatewaySession(Protocol):
    """Gateway-managed model session used by AgentRuntime."""

    async def connect(self, timeout_seconds: float) -> None:
        """Initialize the underlying SDK client/session."""

    async def send(self, user_prompt: str) -> None:
        """Send the user prompt to the underlying model session."""

    def stream_events(self) -> AsyncIterator[GatewayEvent]:
        """Yield normalized gateway events."""

    async def abort(self, reason: str) -> GatewayAbortResult:
        """Interrupt the current model session and clean up resources."""

    async def close(self) -> GatewayAbortResult:
        """Close the current model session cleanly."""

    async def diagnose_connect_timeout(self) -> str:
        """Return a short diagnostic string for connect-time failures when available."""


class ModelGateway(Protocol):
    """Factory for gateway sessions used by AgentRuntime."""

    def create_session(self, request: GatewayRequest) -> ModelGatewaySession:
        """Create a new gateway session for a single issue attempt."""
