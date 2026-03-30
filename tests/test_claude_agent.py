from __future__ import annotations

from pi_sonar_agent.agent.claude_agent import (
    BUILTIN_FIX_TOOLS,
    MCP_FIX_TOOLS,
    ClaudeFixAgent,
    SonarIssue,
)


def test_build_user_prompt_includes_rule_reason_and_fix_guidance() -> None:
    issue = SonarIssue(
        key="issue-1",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=51,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  51 | if (condition) { ... }",
        "- 只允许修改第 45-80 行的目标方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
    )

    assert "规则名称: Cognitive Complexity of methods should not be too high" in prompt
    assert "Issue Key: issue-1" in prompt
    assert "【SonarQube 规则说明（问题原因/风险）】" in prompt
    assert "嵌套条件和循环会提高认知复杂度。" in prompt
    assert "【SonarQube 修复建议】" in prompt
    assert "提取私有方法，减少嵌套层级。" in prompt
    assert "【允许修改范围】" in prompt
    assert "只允许修改第 45-80 行的目标方法。" in prompt
    assert "不要顺手修复本文件中其他位置的相同规则问题" in prompt
    assert "【推荐构建命令】" in prompt
    assert 'dotnet build "src/Foo.sln"' in prompt


def test_fix_issue_fails_when_agent_makes_no_changes(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-2",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(claude_agent_module.anyio, "run", lambda func: None)

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.error == "Agent completed without modifying any files"


def test_builtin_tool_policy_allows_editing_tools_without_bash() -> None:
    assert BUILTIN_FIX_TOOLS == ["Read", "Edit", "MultiEdit", "Write", "Grep", "Glob"]
    assert "Bash" not in BUILTIN_FIX_TOOLS
    assert "mcp__sonar-fix__run_build" in MCP_FIX_TOOLS


def test_fix_issue_fails_when_local_build_verification_fails(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(claude_agent_module.anyio, "run", lambda func: None)

    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "compile failed"

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.error == "Issue changes failed local build verification"
    assert result.build_command == 'dotnet build "src/Foo.sln"'
    assert result.build_output == "compile failed"
    assert result.build_verification_failed is True


def test_scope_validation_rejects_out_of_scope_lines() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-4",
            rule="csharpsquid:S6562",
            message="DateTime 应显式指定 Kind",
            line=20,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [f"line {index}" for index in range(1, 41)],
    )

    changed_lines = ClaudeFixAgent._extract_changed_line_numbers(
        "@@ -20,1 +20,1 @@\n@@ -32,1 +32,1 @@\n"
    )
    offending_lines = ClaudeFixAgent._find_out_of_scope_lines(scope, changed_lines)

    assert scope.start_line == 12
    assert scope.validation_end_line == 28
    assert offending_lines == [32]
