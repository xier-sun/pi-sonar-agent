from __future__ import annotations

from pi_sonar_agent.core.permission_manager import PermissionContext, PermissionManager
from pi_sonar_agent.core.policy import ToolPolicy
from pi_sonar_agent.core.registry import ToolKind, build_fix_tool_registry
from pi_sonar_agent.core.tool_surface import build_allowed_fix_tool_rules


def test_permission_manager_centralizes_bash_permission_decisions() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Bash", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    manager = PermissionManager(
        registry,
        build_allowed_fix_tool_rules(["Read", "Finish"], include_controlled_bash=True),
    )

    allowed = manager.decide(PermissionContext(tool_name="Bash", payload={"command": "pwd"}))
    blocked = manager.decide(
        PermissionContext(tool_name="Bash", payload={"command": "Remove-Item Foo.cs"})
    )

    assert allowed.allowed is True
    assert allowed.kind == ToolKind.CONTROLLED
    assert allowed.matched_rule == "windows-shell-safe"
    assert blocked.allowed is False
    assert blocked.policy_violation is True


def test_permission_manager_handles_scoped_write_file_creation(tmp_path) -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Write", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    manager = PermissionManager(
        registry,
        build_allowed_fix_tool_rules(
            ["Read", "Edit", "Finish"],
            create_file_tool_roots=("src/generated",),
        ),
        workspace_root=tmp_path,
    )

    allowed = manager.decide(
        PermissionContext(
            tool_name="Write",
            payload={"file_path": "src/generated/NewType.cs", "content": "class NewType {}\n"},
        )
    )
    blocked = manager.decide(
        PermissionContext(
            tool_name="Write",
            payload={"file_path": "src/other/NewType.cs", "content": "class NewType {}\n"},
        )
    )

    assert allowed.allowed is True
    assert allowed.matched_rule == "write-create-file"
    assert blocked.allowed is False
    assert blocked.policy_violation is True


def test_tool_policy_delegates_to_permission_manager() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Finish"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit", "Finish"])

    direct_decision = policy.permission_manager.decide(PermissionContext(tool_name="Read"))
    wrapped_decision = policy.classify("Read")

    assert policy.allowed_tool_names() == policy.permission_manager.allowed_tool_names()
    assert wrapped_decision == direct_decision
