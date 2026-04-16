from __future__ import annotations

from pi_sonar_agent.core.claude_adapter import ClaudeAdapter, ClaudeSDKDependencies
from pi_sonar_agent.core.mcp_servers import build_sonar_mcp_runtime


def test_build_sonar_mcp_runtime_filters_mutating_tools_in_read_only_mode() -> None:
    runtime = build_sonar_mcp_runtime(
        {
            "SONAR_MCP_ENABLED": "true",
            "SONAR_MCP_MODE": "stdio",
            "SONAR_MCP_COMMAND": "sonarqube-mcp-server",
            "SONAR_MCP_ARGS": "--stdio --log-level info",
            "SONAR_MCP_TOOLS": ",".join(
                [
                    "mcp__sonarqube__search_issues",
                    "mcp__sonarqube__get_rule",
                    "mcp__sonarqube__git_push",
                    "mcp__sonarqube__create_pull_request",
                ]
            ),
            "SONAR_MCP_READ_ONLY": "true",
            "SONAR_MCP_URL": "https://sonar.example",
            "SONAR_MCP_TOKEN": "token",
            "SONAR_MCP_WORKSPACE_MOUNT": "D:/workspace",
        }
    )

    assert runtime.enabled is True
    assert runtime.server_name == "sonarqube"
    assert runtime.mode == "stdio"
    assert runtime.read_only is True
    assert runtime.tool_names == (
        "mcp__sonarqube__search_issues",
        "mcp__sonarqube__get_rule",
    )
    assert runtime.server_configs["sonarqube"]["command"] == "sonarqube-mcp-server"
    assert runtime.server_configs["sonarqube"]["args"] == ["--stdio", "--log-level", "info"]
    assert runtime.server_configs["sonarqube"]["env"]["SONARQUBE_URL"] == "https://sonar.example"
    assert runtime.server_configs["sonarqube"]["env"]["SONARQUBE_TOKEN"] == "token"


def test_claude_adapter_create_session_passes_mcp_servers_to_sdk_options() -> None:
    recorded: dict[str, object] = {}

    adapter = ClaudeAdapter(
        ClaudeSDKDependencies(
            client_cls=lambda options: recorded.setdefault("options", options) or object(),
            options_cls=lambda **kwargs: kwargs,
            assistant_message_cls=object,
            result_message_cls=object,
            text_block_cls=object,
            tool_use_block_cls=object,
        )
    )

    request = ClaudeAdapter.build_request(
        agent_env={},
        explicit_model=None,
        cwd="workspace",
        system_prompt="system",
        user_prompt="user",
        tools=("Read", "Edit"),
        allowed_tools=("Read", "Edit", "mcp__sonarqube__search_issues"),
        max_turns=4,
        max_budget_usd=1.0,
        stderr_handler=None,
        build_command="dotnet build",
        mcp_servers={
            "sonarqube": {
                "type": "stdio",
                "command": "sonarqube-mcp-server",
                "args": ["--stdio"],
            }
        },
    )
    adapter.create_session(request)

    assert request.mcp_servers["sonarqube"]["command"] == "sonarqube-mcp-server"
    assert recorded["options"]["mcp_servers"]["sonarqube"]["args"] == ["--stdio"]
