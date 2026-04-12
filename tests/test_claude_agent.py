from __future__ import annotations

import asyncio

from pi_sonar_agent.agent.claude_agent import (
    BUILTIN_FIX_TOOLS,
    MCP_FIX_TOOLS,
    ClaudeFixAgent,
    SonarIssue,
)
from pi_sonar_agent.agent.rule_policies import (
    CONDITIONAL_CHAIN_SCOPE_MODE,
    CONTROL_BLOCK_SCOPE_MODE,
    DECLARATION_COMMENT_SCOPE_MODE,
    EXPRESSION_REWRITE_SCOPE_MODE,
    LOOP_REWRITE_SCOPE_MODE,
)
from pi_sonar_agent.agent.rule_validators import validate_rule_fix
from pi_sonar_agent.core.agent_runtime import AgentRuntimeError, AgentRuntimeResult
from pi_sonar_agent.core.events import AttemptRuntimeEvent, AttemptRuntimeEventKind
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.quality_gate import QualityGateRule
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.scope_guard import IssueEditScope
from pi_sonar_agent.core.tool_surface import build_allowed_fix_tool_rules


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
        "异步方法必须使用 async/await。",
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
    assert "【C# 代码质量门禁】" in prompt
    assert "异步方法必须使用 async/await。" in prompt
    assert "【允许修改范围】" in prompt
    assert "只允许修改第 45-80 行的目标方法。" in prompt
    assert "不要顺手修复本文件中其他位置的相同规则问题" in prompt
    assert "【推荐构建命令】" in prompt
    assert 'dotnet build "src/Foo.sln"' in prompt
    assert "- 文件路径: src/Foo.cs" in prompt
    assert "当前优先直接操作的问题文件相对路径候选：" in prompt
    assert "- src/Foo.cs" in prompt
    assert "调用 finish 标记完成" not in prompt


def test_build_user_prompt_includes_workspace_relative_path_candidates(tmp_path) -> None:
    issue = SonarIssue(
        key="issue-path-candidates",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=10,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  10 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"',
        workspace_path=tmp_path / ".agent_workspaces" / "fix_BI" / "OpenAuth.Core",
    )

    assert "- OpenAuth.Core/OpenAuth.App/Finance/Foo.cs" in prompt
    assert "- OpenAuth.App/Finance/Foo.cs" in prompt
    assert "不要用 Bash 通过拼接仓库根目录反复试错" in prompt


def test_build_user_prompt_renders_structured_retry_context() -> None:
    issue = SonarIssue(
        key="issue-retry-context",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    retry_context = RetryContext(
        source_attempt_number=1,
        failure_kind="build",
        error="Issue changes failed local build verification",
        raw_output="build failed",
        guidance=("先修复这些编译错误，再确认 Sonar 问题仍然被修复。",),
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | foreach (var item in items) { ... }",
        "",
        "- 只允许修改第 18-24 行。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        retry_context=retry_context,
    )

    assert "【上次尝试的构建失败信息】" in prompt
    assert "build failed" in prompt
    assert "请基于这些失败原因重新修复" in prompt


def test_build_user_prompt_includes_precise_sonar_location_guidance() -> None:
    issue = SonarIssue(
        key="issue-location",
        rule="csharpsquid:S3358",
        message="Extract this nested ternary operation into an independent statement.",
        line=92,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
        text_range={
            "startLine": 92,
            "endLine": 93,
            "startOffset": 8,
            "endOffset": 24,
        },
        flows=(
            {
                "locations": (
                    {
                        "component": "BI:src/Bar.cs",
                        "msg": "Related branch condition.",
                        "textRange": {"startLine": 16, "endLine": 16, "startOffset": 12, "endOffset": 28},
                    },
                ),
            },
        ),
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  92 | value = condition ? a : b;",
        "",
        "- 只允许修改当前表达式。",
        {
            "name": "Ternary operators should not be nested",
            "description": "嵌套三元表达式会降低可读性。",
            "how_to_fix": "将内层条件提取为独立语句。",
        },
        'dotnet build "src/Foo.sln"',
    )

    assert "【SonarQube 精确定位】" in prompt
    assert "- 主定位: src/Foo.cs:92:9-93:25" in prompt
    assert "- 关联位置 1: src/Bar.cs:16:13-29 | Related branch condition." in prompt
    assert "- 报错行号: 92" in prompt


def test_load_csharp_quality_gate_only_for_csharp_files() -> None:
    csharp_issue = SonarIssue(
        key="issue-cs",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    text_issue = SonarIssue(
        key="issue-txt",
        rule="generic:rule",
        message="文本问题",
        line=1,
        component="BI:notes.txt",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    csharp_gate = ClaudeFixAgent._load_csharp_quality_gate(csharp_issue)
    text_gate = ClaudeFixAgent._load_csharp_quality_gate(text_issue)

    assert "C# 代码质量与架构规范门禁" in csharp_gate
    assert "异步标准" in csharp_gate
    assert text_gate == ""


def test_build_agent_extra_args_uses_bare_for_third_party_provider() -> None:
    extra_args = ClaudeFixAgent._build_agent_extra_args(
        {
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_API_KEY": "token",
        }
    )

    assert extra_args == {"bare": None}


def test_build_agent_extra_args_keeps_default_mode_for_first_party_provider() -> None:
    extra_args = ClaudeFixAgent._build_agent_extra_args(
        {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_API_KEY": "token",
        }
    )

    assert extra_args == {}


def test_build_sdk_child_env_strips_model_env_for_third_party_provider() -> None:
    child_env = ClaudeFixAgent._build_sdk_child_env(
        {
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_API_KEY": "token",
            "ANTHROPIC_CUSTOM_MODEL_OPTION": "glm-4.7",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
            "CLAUDE_MODEL": "glm-4.7",
        }
    )

    assert child_env["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert child_env["ANTHROPIC_API_KEY"] == "token"
    assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in child_env
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in child_env
    assert "CLAUDE_MODEL" not in child_env


def test_resolve_sdk_model_uses_env_for_third_party_provider() -> None:
    raw_env = {
        "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
        "ANTHROPIC_API_KEY": "token",
    }
    child_env = ClaudeFixAgent._build_sdk_child_env(raw_env)

    sdk_model = ClaudeFixAgent._resolve_sdk_model(raw_env, child_env, "glm-4.7")

    assert sdk_model is None
    assert child_env["CLAUDE_MODEL"] == "glm-4.7"


def test_load_csharp_quality_gate_uses_repo_markdown_as_single_source(
    monkeypatch,
    tmp_path,
) -> None:
    issue = SonarIssue(
        key="issue-cs-skill",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    gate_file = tmp_path / "csharp-quality-gate.md"
    gate_file.write_text(
        "\n".join(
            [
                "---",
                '{"version":1,"rules":[{"rule_id":"demo","title":"Demo","summary":"Demo","enforcement":"hard"}]}',
                "---",
                "",
                "# C# 代码质量与架构规范门禁",
                "",
                "* **[强制] XML 文档注释**：所有公开的类、方法、属性、实体都必须有完整的 XML 文档注释。",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(ClaudeFixAgent, "QUALITY_GATE_PATHS", (gate_file,))

    gate = ClaudeFixAgent._load_csharp_quality_gate(issue)

    assert '"version":1' not in gate
    assert "# C# 代码质量与架构规范门禁" in gate
    assert "所有公开的类、方法、属性、实体都必须有完整的 XML 文档注释" in gate


def test_load_csharp_quality_gate_prefers_active_rule_summary_when_contract_present() -> None:
    issue = SonarIssue(
        key="issue-cs-active-rules",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    edit_contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        quality_gate_rules=(
            QualityGateRule(
                rule_id="cognitive_complexity",
                title="单方法认知复杂度不超过 30",
                summary="触达方法的认知复杂度不得超过 30。",
                enforcement="hard",
                prompt_hint="优先通过拆小局部逻辑或提取 private helper 降低复杂度。",
            ),
        ),
    )

    gate = ClaudeFixAgent._load_csharp_quality_gate(issue, edit_contract)

    assert "本次修复只需遵守下面这些已启用的质量门禁规则" in gate
    assert "cognitive_complexity 单方法认知复杂度不超过 30" in gate
    assert "优先通过拆小局部逻辑或提取 private helper 降低复杂度" in gate
    assert "# C# 代码质量与架构规范门禁" not in gate


def test_default_quality_gate_source_points_to_repo_file() -> None:
    assert len(ClaudeFixAgent.QUALITY_GATE_PATHS) == 1
    assert str(ClaudeFixAgent.QUALITY_GATE_PATHS[0]).replace("\\", "/").endswith(
        "data/csharp-quality-gate.md"
    )


def test_build_user_prompt_includes_rule_specific_guards() -> None:
    issue = SonarIssue(
        key="issue-guards",
        rule="csharpsquid:S3267",
        message="循环可简化为 LINQ",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | foreach (var item in items) { ... }",
        "",
        "- 只允许修改第 18-24 行。",
        {
            "name": "Loops should be simplified with LINQ expressions",
            "description": "某些循环可以被 LINQ 表达式替代。",
            "how_to_fix": "在安全时使用 LINQ。",
        },
        'dotnet build "src/Foo.sln"',
    )

    assert "【当前规则的额外约束】" in prompt
    assert "IQueryable" in prompt
    assert "await" in prompt
    assert "不要为了满足规则把简单循环改成更难读" in prompt


def test_rule_validator_rejects_unresolved_nested_ternary() -> None:
    message = validate_rule_fix(
        validator_name="nested_ternary_removed",
        issue_line=3,
        file_content="\n".join(
            [
                "var result = foo",
                "    ? (bar ? 1 : 0)",
                "    : 2;",
            ]
        ),
    )

    assert "nested ternary expression still exists" in message
    assert "csharpsquid:S3358" in message


def test_rule_validator_accepts_single_level_ternary() -> None:
    message = validate_rule_fix(
        validator_name="nested_ternary_removed",
        issue_line=2,
        file_content="\n".join(
            [
                "var parsed = int.TryParse(value, out var temp) ? temp : 0;",
                "var result = hasValue ? parsed : 0;",
            ]
        ),
    )

    assert message == ""


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
    assert result.retryable_failure is True
    assert result.failure_kind == "no_change"


def test_fix_issue_classifies_empty_edit_payload_as_tool_input_invalid(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-invalid-edit",
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
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_runtime_run(self, request):
        return AgentRuntimeResult(
            tool_uses=("Read", "Edit"),
            last_tool_name="Edit",
            tool_call_count=2,
            read_call_count=1,
            edit_call_count=1,
            runtime_events=(
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.TOOL_CALLED,
                    sequence=1,
                    stage="tool:Edit",
                    payload={"tool_name": "Edit", "tool_payload": {}},
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.SDK_TRACE,
                    sequence=2,
                    stage="sdk_message:UserMessage",
                    payload={
                        "preview": (
                            "{\"content\": [{\"content_preview\": "
                            "\"<tool_use_error>InputValidationError: Edit failed due to the following issues:\\n"
                            "The required parameter `file_path` is missing\\n"
                            "The required parameter `old_string` is missing\\n"
                            "The required parameter `new_string` is missing</tool_use_error>\"}]}"
                        )
                    },
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    sequence=3,
                    stage="completed",
                    payload={"success": False},
                ),
            ),
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "tool_input_invalid"
    assert result.error == "Model emitted an invalid Edit/MultiEdit call"
    assert "InputValidationError" in result.build_output


def test_fix_issue_skips_policy_managed_rule_before_running_agent(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-skip",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=12,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: (_ for _ in ()).throw(AssertionError("skip rule should not read rule details")),
    )

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.skipped is True
    assert result.failure_kind == "policy_skip"
    assert "默认跳过" in result.skip_reason


def test_fix_issue_downgrades_complex_plan_instead_of_hard_blocking(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-plan-conflict",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    runtime_requests: list[object] = []

    def fake_runtime_run(self, request):
        runtime_requests.append(request)
        return AgentRuntimeResult(
            agent_error=None,
            tool_uses=("Read",),
            last_tool_name="Read",
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.failure_kind == "no_change"
    assert result.repair_plan is not None
    assert result.plan_precheck is not None
    assert result.plan_precheck.status == "pass"
    assert result.repair_plan.requires_signature_change is False
    assert result.repair_plan.selected_archetype == "signature_preserving_refactor"
    assert runtime_requests


def test_builtin_tool_policy_allows_editing_tools_without_bash() -> None:
    assert BUILTIN_FIX_TOOLS == ["Read", "Edit", "MultiEdit"]
    assert "Bash" not in BUILTIN_FIX_TOOLS
    assert MCP_FIX_TOOLS == []
    assert "mcp__sonar-fix__git_add" not in MCP_FIX_TOOLS
    assert "mcp__sonar-fix__git_commit" not in MCP_FIX_TOOLS
    assert "mcp__sonar-fix__git_push" not in MCP_FIX_TOOLS


def test_allowed_fix_tool_rules_append_controlled_bash_rules() -> None:
    allowed_tools = build_allowed_fix_tool_rules(["Read", "Edit"], include_controlled_bash=True)

    assert "Read" in allowed_tools
    assert "Edit" in allowed_tools
    assert "Bash" in allowed_tools
    assert "Finish" in allowed_tools


def test_claude_fix_tool_policy_allows_finish_and_harmless_shell() -> None:
    policy = ClaudeFixAgent._build_fix_tool_policy()

    finish_decision = policy.classify("Finish")
    echo_decision = policy.classify("Bash", {"command": "echo 修复完成"})
    delete_decision = policy.classify("Bash", {"command": "Remove-Item Foo.cs"})

    assert finish_decision.allowed is True
    assert echo_decision.allowed is True
    assert delete_decision.allowed is False
    assert delete_decision.policy_violation is True


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
    assert "compile failed" in result.build_output
    assert result.build_verification_failed is True
    assert result.retryable_failure is True
    assert result.failure_kind == "build"


def test_fix_issue_retries_when_rule_specific_validation_fails(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3d",
        rule="csharpsquid:S3358",
        message="嵌套三元运算符",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "var value = foo",
                "    ? (bar ? 1 : 0)",
                "    : 2;",
            ]
        ),
        encoding="utf-8",
    )

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

    def fake_run(func) -> None:
        source_file.write_text(
            "\n".join(
                [
                    "var value = foo",
                    "    ? (bar ? 1 : 0)",
                    "    : 2;",
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(claude_agent_module.anyio, "run", fake_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "rule_validation"
    assert "nested ternary expression still exists" in result.build_output


def test_fix_issue_retries_when_run_build_tool_crashes(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-build-tool",
        rule="csharpsquid:S6580",
        message="DateTime.TryParse 应指定 format provider",
        line=4,
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

    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeToolUseBlock("mcp__sonar-fix__run_build")]

    async def fake_receive_response():
        yield FakeAssistantMessage()
        raise RuntimeError("Command failed with exit code 3 (exit code: 3)\nError output: build stderr")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def query(self, prompt):
            return None

        def receive_response(self):
            return fake_receive_response()

    monkeypatch.setattr(claude_agent_module, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_agent_module, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())

    class FakeCompletedProcess:
        returncode = 1
        stdout = "build stdout"
        stderr = "src/Foo.cs(4,1): error CS0103: name not found [Foo.csproj]"

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.error == "Build tool execution failed"
    assert result.retryable_failure is True
    assert result.build_verification_failed is True
    assert result.failure_kind == "build_tool"
    assert "run_build 工具执行异常" in result.build_output
    assert "本地回退构建 Exit code: 1" in result.build_output
    assert "error CS0103: name not found" in result.build_output


def test_fix_issue_retries_when_model_first_response_times_out(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-model-timeout",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
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

    monkeypatch.setattr(
        claude_agent_module.anyio,
        "run",
        lambda func: (_ for _ in ()).throw(TimeoutError("模型在 120 秒内没有返回首个响应")),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "model_timeout"
    assert result.error == "Model response timed out"
    assert "没有返回首个响应" in result.build_output


def test_fix_issue_retries_when_follow_up_response_times_out(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-follow-up-timeout",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
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
    events: list[str] = []

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeTextBlock("still working")]

    async def fake_receive_response():
        yield FakeAssistantMessage()
        await asyncio.sleep(1)

    class FakeClient:
        async def __aenter__(self):
            events.append("__aenter__")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("__aexit__")
            return None

        async def query(self, prompt):
            return None

        async def interrupt(self):
            events.append("interrupt")
            return None

        def receive_response(self):
            return fake_receive_response()

    monkeypatch.setattr(claude_agent_module, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_agent_module, "TextBlock", FakeTextBlock)
    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())
    monkeypatch.setattr(claude_agent_module, "FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(claude_agent_module, "ISSUE_HARD_TIMEOUT_SECONDS", 10)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "model_timeout"
    assert result.error == "Model response timed out"
    assert "没有返回后续响应" in result.build_output
    assert "清理动作: interrupt, close_response_stream, disconnect" in result.build_output
    assert events == [
        "__aenter__",
        "interrupt",
        "__aexit__",
        "__aenter__",
        "interrupt",
        "__aexit__",
        "__aenter__",
        "interrupt",
        "__aexit__",
    ]
    assert result.performance_metrics["continuation_retry_count"] == 2


def test_fix_issue_retries_when_issue_runtime_exceeds_deadline(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-hard-timeout",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
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
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    events: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            events.append("__aenter__")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("__aexit__")
            return None

        async def query(self, prompt):
            await asyncio.sleep(1)

        async def interrupt(self):
            events.append("interrupt")
            return None

        def receive_response(self):
            raise AssertionError("receive_response should not be reached before deadline")

    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())
    monkeypatch.setattr(claude_agent_module, "FIRST_RESPONSE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(claude_agent_module, "FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(claude_agent_module, "ISSUE_HARD_TIMEOUT_SECONDS", 0.01)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "model_timeout"
    assert result.error == "Model response timed out"
    assert "单个 issue 在" in result.build_output
    assert "清理动作: interrupt, disconnect" in result.build_output
    assert events == ["__aenter__", "interrupt", "__aexit__"]


def test_fix_issue_retries_when_forbidden_tool_is_used(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-forbidden-tool",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
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
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_attempt_head_changed",
        staticmethod(lambda workspace_path: False),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeToolUseBlock("mcp__sonar-fix__git_commit")]

    class FakeResultMessage:
        def __init__(self) -> None:
            self.total_cost_usd = 0.1
            self.is_error = False
            self.result = ""
            self.errors = []

    async def fake_receive_response():
        yield FakeAssistantMessage()
        yield FakeResultMessage()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def query(self, prompt):
            return None

        def receive_response(self):
            return fake_receive_response()

    monkeypatch.setattr(claude_agent_module, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_agent_module, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude_agent_module, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())

    monkeypatch.setattr(
        ClaudeFixAgent,
        "_run_local_build_fallback",
        classmethod(lambda cls, workspace_path, build_command: (True, "fallback build ok")),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "forbidden_tool"
    assert result.error == "Forbidden tool used during issue fix"
    assert "git_commit" in result.build_output
    assert "fallback build ok" in result.build_output


def test_collect_modified_files_detects_attempt_local_commit(monkeypatch, tmp_path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    tracked_file = repo / "tracked.cs"
    tracked_file.write_text("class Foo {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.cs"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    ClaudeFixAgent._capture_attempt_workspace_state(repo)
    try:
        tracked_file.write_text("class Foo { int Value => 1; }\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.cs"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "attempt"], cwd=repo, check=True)

        changed_files = ClaudeFixAgent._collect_modified_files(repo)

        assert changed_files == ["tracked.cs"]
        assert ClaudeFixAgent._attempt_head_changed(repo) is True
    finally:
        ClaudeFixAgent._cleanup_attempt_workspace_state(repo)


def test_fix_issue_runs_build_when_scope_soft_drift_is_ignored(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3b",
        rule="csharpsquid:S3358",
        message="嵌套三元运算符",
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
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_validate_issue_edit_scope",
        staticmethod(
            lambda workspace_path, issue, scope, **kwargs: "Issue changes exceeded the allowed Sonar edit scope."
        ),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(claude_agent_module.anyio, "run", lambda func: None)

    build_calls: list[str] = []

    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "src/Foo.cs(3,1): error CS0103: name not found [Foo.csproj]"

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        return FakeCompletedProcess()

    monkeypatch.setattr(claude_agent_module.subprocess, "run", fake_run)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.error == "Issue changes failed local build verification"
    assert "error CS0103" in result.build_output
    assert result.retryable_failure is True
    assert result.failure_kind == "build"
    assert build_calls == ["build"]


def test_fix_issue_salvages_patch_after_post_edit_timeout(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-salvage",
        rule="csharpsquid:S1481",
        message="移除未使用变量",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "        var unused = 1;",
                    "    }",
                    "}",
                    "",
                ]
            ),
        encoding="utf-8",
    )

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

    def fake_runtime_run(self, request):
        source_file.write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        raise AgentRuntimeError(
            TimeoutError("模型在 180 秒内没有返回后续响应\n阶段分类: post_edit_stall"),
            AgentRuntimeResult(
                tool_uses=("Read", "Edit"),
                last_tool_name="Edit",
                total_duration_seconds=12.0,
                time_to_first_model_content_seconds=1.2,
                time_after_first_edit_to_finalize_seconds=10.8,
                tool_call_count=2,
                read_call_count=1,
                edit_call_count=1,
                timeout_stage="post_edit_stall",
                last_progress_stage="tool:Edit",
            ),
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert result.patch_salvaged is True
    assert result.model_timeout_stage == "post_edit_stall"
    assert result.performance_metrics["patch_salvaged"] is True
    assert result.performance_metrics["model_timeout_stage"] == "post_edit_stall"
    assert result.performance_metrics["build_invoked"] is True


def test_fix_issue_scope_validation_ignores_previous_successful_changes_in_same_file(
    monkeypatch,
    tmp_path,
) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3c",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=10,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
                [
                    "class Foo",
                    "{",
                    "    private int _value = 1;",
                    "",
                    "    /// <summary>",
                    "    /// Demo.",
                    "    /// </summary>",
                    "    public void Demo()",
                    "    {",
                    "        var now = DateTime.Now;",
                    "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Simulate a previous issue that already modified the same file before the current issue starts.
    source_file.write_text(
        "\n".join(
                [
                    "class Foo",
                    "{",
                    "    private int _value = 2;",
                    "",
                    "    /// <summary>",
                    "    /// Demo.",
                    "    /// </summary>",
                    "    public void Demo()",
                    "    {",
                    "        var now = DateTime.Now;",
                    "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

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

    def fake_run(func) -> None:
        source_file.write_text(
            "\n".join(
                    [
                        "class Foo",
                        "{",
                        "    private int _value = 2;",
                        "",
                        "    /// <summary>",
                        "    /// Demo.",
                        "    /// </summary>",
                        "    public void Demo()",
                        "    {",
                        "        var now = DateTime.UtcNow;",
                        "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(claude_agent_module.anyio, "run", fake_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert result.build_passed is True
    assert "allowed Sonar edit scope" not in result.build_output


def test_fix_issue_continues_same_issue_after_follow_up_timeout(
    monkeypatch,
    tmp_path,
) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-continuation",
        rule="csharpsquid:S1481",
        message="Remove this unused local variable.",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var unused = 1;",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    seen_prompts: list[str] = []
    seen_change_probes = {"count": 0}

    def fake_collect_modified_files(workspace_path):
        seen_change_probes["count"] += 1
        if len(seen_prompts) >= 2:
            return ["src/Foo.cs"]
        return []

    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(fake_collect_modified_files),
    )

    def fake_runtime_run(self, request):
        seen_prompts.append(request.user_prompt)
        if len(seen_prompts) == 1:
            raise AgentRuntimeError(
                TimeoutError("模型在 180 秒内没有返回后续响应\n阶段分类: post_read_stall"),
                AgentRuntimeResult(
                    tool_uses=("Read",),
                    last_tool_name="Read",
                    total_duration_seconds=8.0,
                    time_to_first_model_content_seconds=1.0,
                    tool_call_count=1,
                    read_call_count=1,
                    timeout_stage="post_read_stall",
                    last_progress_stage="tool:Read",
                    runtime_events=(
                        AttemptRuntimeEvent(
                            kind=AttemptRuntimeEventKind.ATTEMPT_STARTED,
                            sequence=1,
                            stage="initializing",
                        ),
                        AttemptRuntimeEvent(
                            kind=AttemptRuntimeEventKind.TOOL_CALLED,
                            sequence=2,
                            stage="tool:Read",
                            payload={
                                "tool_name": "Read",
                                "tool_payload": {"file_path": r"C:\GIT.NEWARE.WORK\BI\src\Foo.cs"},
                                "tool_preview": '{"file_path":"C:\\\\GIT.NEWARE.WORK\\\\BI\\\\src\\\\Foo.cs"}',
                                "read_preview": "   4 |     {\n   5 |         var unused = 1;",
                            },
                        ),
                    ),
                ),
            )

        source_file.write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return AgentRuntimeResult(
            tool_uses=("Read", "Edit"),
            last_tool_name="Edit",
            total_duration_seconds=6.0,
            time_to_first_model_content_seconds=0.8,
            time_after_first_edit_to_finalize_seconds=2.0,
            tool_call_count=2,
            read_call_count=1,
            edit_call_count=1,
            continuation_retry_count=1,
            continuation_recovered=True,
            continuation_timeout_stages=("post_read_stall",),
            runtime_events=(
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_STARTED,
                    sequence=1,
                    stage="initializing",
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.TOOL_CALLED,
                    sequence=2,
                    stage="tool:Edit",
                    payload={"tool_name": "Edit"},
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    sequence=3,
                    stage="completed",
                    payload={"success": True},
                ),
            ),
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert len(seen_prompts) == 2
    assert "【继续上一轮修复，不要从头分析】" in seen_prompts[1]
    assert "绝对路径" in seen_prompts[1]
    assert "当前优先直接操作的问题文件相对路径候选：" in seen_prompts[1]
    assert "- src/Foo.cs" in seen_prompts[1]
    assert result.performance_metrics["continuation_retry_count"] == 1
    assert result.performance_metrics["continuation_recovered"] is True
    assert "post_read_stall" in result.performance_metrics["continuation_timeout_stages"]
    assert any(
        event.kind == AttemptRuntimeEventKind.CONTINUATION_REQUESTED
        for event in result.attempt_events
    )


def test_scope_validation_rejects_out_of_scope_lines() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-4",
            rule="csharpsquid:S6562",
            message="DateTime 应显式指定 Kind",
            line=4,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "public void Demo()",
            "{",
            '    var name = "demo";',
            "    var cuffOffDate = new DateTime(",
            "        2024,",
            "        6,",
            "        1);",
            "    var another = DateTime.Now;",
            "}",
        ],
    )

    changed_lines = ClaudeFixAgent._extract_changed_line_numbers(
        "\n".join(
            [
                "@@ -4,1 +4,1 @@",
                "-    var cuffOffDate = new DateTime(",
                "+    var cuffOffDate = DateTime.SpecifyKind(",
                "@@ -8,1 +8,1 @@",
                "-    var another = DateTime.Now;",
                "+    var another = DateTime.UtcNow;",
            ]
        )
    )
    offending_lines = ClaudeFixAgent._find_out_of_scope_lines(scope, changed_lines)

    assert scope.start_line == 4
    assert scope.validation_start_line == 4
    assert scope.validation_end_line == 7
    assert offending_lines == [8]


def test_scope_validation_uses_before_coordinates_for_pure_delete() -> None:
    scope = IssueEditScope(
        start_line=2224,
        end_line=2224,
        validation_start_line=2224,
        validation_end_line=2224,
        mode="statement",
    )

    changed_lines = ClaudeFixAgent._extract_changed_line_numbers(
        "\n".join(
            [
                "@@ -2224 +2223,0 @@",
                "-        var orderNum = result.OrderNum();",
            ]
        )
    )

    offending_lines = ClaudeFixAgent._find_out_of_scope_lines(scope, changed_lines)

    assert changed_lines == {2224}
    assert offending_lines == []


def test_build_issue_edit_scope_expands_window_for_s125_adjacent_cleanup() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-s125",
            rule="csharpsquid:S125",
            message="移除被注释掉的代码段",
            line=6,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "class Foo",
            "{",
            "    void Demo()",
            "    {",
            "        var temp = 1;",
            "        // old code",
            "        Run();",
            "    }",
            "}",
        ],
    )

    assert scope.mode == "statement"
    assert scope.validation_start_line < 6
    assert scope.validation_end_line >= 6


def test_build_issue_edit_scope_supports_control_block_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-block",
            rule="csharpsquid:S2681",
            message="多行代码块必须使用大括号",
            line=2,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "public void Demo()",
            "if (enabled)",
            "    Run();",
            "return;",
        ],
    )

    assert scope.mode == CONTROL_BLOCK_SCOPE_MODE
    assert scope.start_line == 2
    assert scope.end_line == 3
    assert scope.validation_start_line == 2
    assert scope.validation_end_line == 4


def test_build_issue_edit_scope_supports_declaration_comment_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-doc",
            rule="external_roslyn:CS1591",
            message="公开成员缺少 XML 注释",
            line=4,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "namespace Demo",
            "{",
            "    [HttpGet]",
            "    public IActionResult List(",
            "        int id)",
            "    {",
            "        return Ok(id);",
            "    }",
            "}",
        ],
    )

    assert scope.mode == DECLARATION_COMMENT_SCOPE_MODE
    assert scope.start_line == 3
    assert scope.end_line == 6
    assert scope.validation_start_line == 1
    assert scope.validation_end_line == 9


def test_build_issue_edit_scope_supports_conditional_chain_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-conditional",
            rule="csharpsquid:S1871",
            message="两个分支实现相同",
            line=7,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "if (a)",
            "{",
            "    Foo();",
            "}",
            "else if (b)",
            "{",
            "    Foo();",
            "}",
            "else",
            "{",
            "    Foo();",
            "}",
        ],
    )

    assert scope.mode == CONDITIONAL_CHAIN_SCOPE_MODE
    assert scope.start_line == 1
    assert scope.end_line == 12
    assert scope.validation_start_line == 1
    assert scope.validation_end_line == 12


def test_build_issue_edit_scope_supports_expression_rewrite_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-expression",
            rule="csharpsquid:S3358",
            message="嵌套三元运算符",
            line=5,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "var result = items.Select(x => new",
            "{",
            "    Name = x.Name,",
            "    Score = foo",
            "        ? (bar ? 1 : 0)",
            "        : 2,",
            "}).ToList();",
        ],
    )

    assert scope.mode == EXPRESSION_REWRITE_SCOPE_MODE
    assert scope.start_line == 1
    assert scope.end_line == 7
    assert scope.validation_start_line == 1
    assert scope.validation_end_line == 7


def test_build_issue_edit_scope_supports_loop_rewrite_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-loop",
            rule="csharpsquid:S3267",
            message="循环可简化为 LINQ",
            line=4,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "private static string? FindMatch(IEnumerable<Item> items, string target)",
            "{",
            "    foreach (var item in items)",
            "    {",
            "        if (item.Name == target)",
            "        {",
            "            return item.Name;",
            "        }",
            "    }",
            "",
            "    return null;",
            "}",
        ],
    )

    assert scope.mode == LOOP_REWRITE_SCOPE_MODE
    assert scope.start_line == 3
    assert scope.end_line == 11
    assert scope.validation_start_line == 3
    assert scope.validation_end_line == 12
