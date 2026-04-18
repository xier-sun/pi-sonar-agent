"""Execution mode helpers for headless simple-loop flows."""

from __future__ import annotations

from pi_sonar_agent.core.project_env import read_project_env

SIMPLE_LOOP_EXECUTION_MODE = "simple_loop"


def normalize_execution_mode(
    value: str | None,
    *,
    default: str = SIMPLE_LOOP_EXECUTION_MODE,
) -> str:
    """Normalize configured execution mode to the only supported runtime mode."""

    del value
    del default
    return SIMPLE_LOOP_EXECUTION_MODE


def resolve_execution_mode(agent_env: dict[str, str] | None = None) -> str:
    """Resolve issue execution mode from agent env or project env."""

    raw_value = (
        (agent_env or {}).get("ISSUE_EXECUTION_MODE")
        or read_project_env().get("ISSUE_EXECUTION_MODE", "")
    )
    return normalize_execution_mode(raw_value)


def is_simple_loop_execution_mode(value: str | None) -> bool:
    """Return True when the current execution mode is simple_loop."""

    return normalize_execution_mode(value) == SIMPLE_LOOP_EXECUTION_MODE
