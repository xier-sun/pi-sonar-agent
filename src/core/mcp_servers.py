"""MCP server configuration assembly for the issue-fix runtime."""

from __future__ import annotations

import json
import shlex
from typing import Any

from pi_sonar_agent.core.project_env import read_project_env
from pi_sonar_agent.core.sonar_mcp_client import SonarMcpRuntime

_MUTATING_TOOL_MARKERS = (
    "git_",
    "commit",
    "push",
    "add",
    "write",
    "create",
    "update",
    "delete",
    "run_build",
)


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


def _split_csv(raw_value: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(raw_value or "").split(",")
        if item.strip()
    )


def _split_args(raw_value: str) -> list[str]:
    text = str(raw_value or "").strip()
    if not text:
        return []
    return [str(item) for item in shlex.split(text, posix=False) if str(item).strip()]


def _filter_read_only_tools(tool_names: tuple[str, ...], *, read_only: bool) -> tuple[str, ...]:
    if not read_only:
        return tool_names
    filtered = [
        tool_name
        for tool_name in tool_names
        if not any(marker in tool_name.lower() for marker in _MUTATING_TOOL_MARKERS)
    ]
    return tuple(dict.fromkeys(filtered))


def build_sonar_mcp_runtime(agent_env: dict[str, str] | None = None) -> SonarMcpRuntime:
    """Build the Sonar MCP runtime configuration from project/agent env."""

    config = _merged_env(agent_env)
    enabled = _env_flag(
        config,
        "SONAR_MCP_ENABLED",
        "PI_SONAR_MCP_ENABLED",
        default=False,
    )
    if not enabled:
        return SonarMcpRuntime(enabled=False, warning="SONAR_MCP_ENABLED is false.")

    server_name = _env_value(
        config,
        "SONAR_MCP_SERVER_NAME",
        "PI_SONAR_MCP_SERVER_NAME",
        default="sonarqube",
    )
    mode = _env_value(
        config,
        "SONAR_MCP_MODE",
        "PI_SONAR_MCP_MODE",
        default="stdio",
    ).lower()
    read_only = _env_flag(
        config,
        "SONAR_MCP_READ_ONLY",
        "PI_SONAR_MCP_READ_ONLY",
        default=True,
    )
    tool_names = _filter_read_only_tools(
        _split_csv(
            _env_value(
                config,
                "SONAR_MCP_TOOLS",
                "PI_SONAR_MCP_TOOLS",
            )
        ),
        read_only=read_only,
    )

    server_configs: dict[str, Any] = {}
    warning = ""
    if mode == "stdio":
        command = _env_value(
            config,
            "SONAR_MCP_COMMAND",
            "PI_SONAR_MCP_COMMAND",
            default="sonarqube-mcp-server",
        )
        args = _split_args(
            _env_value(
                config,
                "SONAR_MCP_ARGS",
                "PI_SONAR_MCP_ARGS",
            )
        )
        server_env: dict[str, str] = {}
        sonar_url = _env_value(config, "SONAR_MCP_URL", "PI_SONAR_MCP_URL")
        sonar_token = _env_value(config, "SONAR_MCP_TOKEN", "PI_SONAR_MCP_TOKEN")
        workspace_mount = _env_value(
            config,
            "SONAR_MCP_WORKSPACE_MOUNT",
            "PI_SONAR_MCP_WORKSPACE_MOUNT",
        )
        if sonar_url:
            server_env["SONARQUBE_URL"] = sonar_url
        if sonar_token:
            server_env["SONARQUBE_TOKEN"] = sonar_token
        if workspace_mount:
            server_env["SONAR_MCP_WORKSPACE_MOUNT"] = workspace_mount
        server_configs[server_name] = {
            "type": "stdio",
            "command": command,
            "args": args,
            "env": server_env,
        }
    elif mode in {"http", "sse"}:
        url = _env_value(
            config,
            "SONAR_MCP_HTTP_URL",
            "PI_SONAR_MCP_HTTP_URL",
            "SONAR_MCP_URL",
            "PI_SONAR_MCP_URL",
        )
        headers: dict[str, str] = {}
        raw_headers = _env_value(
            config,
            "SONAR_MCP_HEADERS_JSON",
            "PI_SONAR_MCP_HEADERS_JSON",
        )
        if raw_headers:
            try:
                parsed_headers = json.loads(raw_headers)
            except json.JSONDecodeError:
                parsed_headers = {}
            if isinstance(parsed_headers, dict):
                headers = {
                    str(key): str(value)
                    for key, value in parsed_headers.items()
                    if str(key).strip() and str(value).strip()
                }
        if url:
            server_configs[server_name] = {"type": mode, "url": url, "headers": headers}
        else:
            warning = f"SONAR_MCP_MODE={mode} but no MCP URL was configured."
    else:
        warning = f"Unsupported SONAR_MCP_MODE: {mode}"

    if not tool_names:
        warning = warning or "Sonar MCP enabled but no read-only tool names were configured."

    return SonarMcpRuntime(
        enabled=True,
        server_name=server_name,
        mode=mode,
        read_only=read_only,
        tool_names=tool_names,
        server_configs=server_configs,
        warning=warning,
    )
