"""Structured Sonar MCP runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class SonarMcpRuntime:
    """Resolved Sonar MCP runtime settings for one issue attempt."""

    enabled: bool
    server_name: str = ""
    mode: str = ""
    read_only: bool = True
    tool_names: tuple[str, ...] = ()
    server_configs: dict[str, Any] = field(default_factory=dict)
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)
