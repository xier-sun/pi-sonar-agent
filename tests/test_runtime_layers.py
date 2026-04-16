from __future__ import annotations

import asyncio

import pytest

import pi_sonar_agent.core.claude_adapter as claude_adapter_module
from pi_sonar_agent.core.agent_runtime import AgentRuntime, AgentRuntimeError, RuntimeTimeouts
from pi_sonar_agent.core.claude_adapter import ClaudeAdapter, ClaudeSDKDependencies
from pi_sonar_agent.core.events import AttemptRuntimeEventKind
from pi_sonar_agent.core.hooks import HookPipeline
from pi_sonar_agent.core.model_gateway import (
    GatewayRequest,
    ResultEvent,
    TextEvent,
    ToolCallEvent,
    TraceEvent,
)
from pi_sonar_agent.core.policy import ToolPolicy
from pi_sonar_agent.core.registry import ToolKind, build_fix_tool_registry, build_visible_toolset
from pi_sonar_agent.core.resource_loader import ResourceLoader
from pi_sonar_agent.core.tool_surface import build_allowed_fix_tool_rules


def test_resource_loader_compose_system_prompt_prefers_workspace_rules(tmp_path) -> None:
    claude_file = tmp_path / "CLAUDE.md"
    claude_file.write_text(
        "\n".join(
            [
                "---",
                "name: demo",
                "---",
                "",
                "# Repo Rules",
                "",
                "- Keep patches small.",
            ]
        ),
        encoding="utf-8",
    )

    prompt = ResourceLoader.compose_system_prompt("base prompt", tmp_path)

    assert prompt.startswith("base prompt")
    assert "【仓库长期规则】" in prompt
    assert "name: demo" not in prompt
    assert "Keep patches small." in prompt


def test_resource_loader_sanitizes_workspace_absolute_path_hints(tmp_path) -> None:
    claude_file = tmp_path / "CLAUDE.md"
    claude_file.write_text(
        "\n".join(
            [
                "# Repo Rules",
                "",
                "所有命令在仓库根目录 `c:\\GIT.NEWARE.WORK\\BI\\OpenAuth.Core\\` 执行",
            ]
        ),
        encoding="utf-8",
    )

    rules = ResourceLoader.load_workspace_rules(tmp_path)

    assert "`<workspace-root>`" in rules
    assert "c:\\git.neware.work" not in rules.lower()
    assert "只使用仓库相对路径" in rules


def test_resource_loader_loads_json_front_matter_and_markdown_body(tmp_path) -> None:
    gate_file = tmp_path / "csharp-quality-gate.md"
    gate_file.write_text(
        "\n".join(
            [
                "---",
                '{"version":1,"rules":[{"rule_id":"demo","title":"Demo","summary":"Demo","enforcement":"hard"}]}',
                "---",
                "",
                "# Demo Gate",
                "",
                "- Keep it small.",
            ]
        ),
        encoding="utf-8",
    )

    path, metadata, body = ResourceLoader.load_json_front_matter((gate_file,))

    assert path == gate_file
    assert metadata["version"] == 1
    assert metadata["rules"][0]["rule_id"] == "demo"
    assert body.startswith("# Demo Gate")


def test_tool_policy_classifies_allowed_build_and_forbidden_tools() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Write", "Finish"],
        mcp_tools=[],
        forbidden_tools={"Bash", "mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit", "Write", "Finish"])

    read_decision = policy.classify("Read")
    build_decision = policy.classify("mcp__sonar-fix__run_build")
    forbidden_decision = policy.classify("Bash")

    assert read_decision.allowed is True
    assert read_decision.kind == ToolKind.READ_ONLY
    assert build_decision.allowed is False
    assert build_decision.kind == ToolKind.CONTROLLED
    assert policy.is_build_tool("mcp__sonar-fix__run_build") is True
    assert forbidden_decision.allowed is False
    assert forbidden_decision.kind == ToolKind.FORBIDDEN
    assert policy.is_forbidden_tool("Bash") is True


def test_tool_policy_allows_bash_commands_but_rejects_filesystem_mutation() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Bash", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(
        registry,
        build_allowed_fix_tool_rules(["Read", "Finish"], include_controlled_bash=True),
    )

    allowed = policy.classify("Bash", {"command": "find . -name FinanceHomeApp.cs"})
    harmless_echo = policy.classify("Bash", {"command": "echo 修复完成"})
    blocked_delete = policy.classify("Bash", {"command": "Remove-Item Foo.cs"})
    blocked_write = policy.classify("Bash", {"command": "Set-Content Foo.cs 'class Foo {}'"})
    finish_allowed = policy.classify("Finish")

    assert allowed.allowed is True
    assert allowed.kind == ToolKind.CONTROLLED
    assert allowed.matched_rule == "windows-shell-safe"
    assert harmless_echo.allowed is True
    assert blocked_delete.allowed is False
    assert blocked_delete.policy_violation is True
    assert blocked_write.allowed is False
    assert blocked_write.policy_violation is True
    assert finish_allowed.allowed is True
    assert policy.is_forbidden_tool("Bash", {"command": "Remove-Item Foo.cs"}) is True


def test_tool_policy_allows_scoped_bash_file_creation() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Bash", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(
        registry,
        build_allowed_fix_tool_rules(
            ["Read", "Finish"],
            include_controlled_bash=True,
            bash_file_creation_roots=("src/generated",),
        ),
    )

    allowed = policy.classify(
        "Bash",
        {"command": "mkdir -p src/generated && cat <<'EOF' > src/generated/NewType.cs\nclass NewType {}\nEOF"},
    )
    blocked = policy.classify(
        "Bash",
        {"command": "mkdir -p src/other && cat <<'EOF' > src/other/NewType.cs\nclass NewType {}\nEOF"},
    )

    assert allowed.allowed is True
    assert allowed.matched_rule == "windows-shell-create-file"
    assert blocked.allowed is False
    assert blocked.policy_violation is True


def test_tool_policy_allows_scoped_write_file_creation(tmp_path) -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Write", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(
        registry,
        build_allowed_fix_tool_rules(
            ["Read", "Edit", "Finish"],
            create_file_tool_roots=("src/generated",),
        ),
        workspace_root=tmp_path,
    )

    allowed = policy.classify(
        "Write",
        {"file_path": "src/generated/NewType.cs", "content": "class NewType {}\n"},
    )

    assert allowed.allowed is True
    assert allowed.matched_rule == "write-create-file"


def test_tool_policy_blocks_write_to_existing_file_when_only_create_file_is_allowed(tmp_path) -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Write", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    existing_file = tmp_path / "src" / "generated" / "Existing.cs"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_text("class Existing {}\n", encoding="utf-8")
    policy = ToolPolicy(
        registry,
        build_allowed_fix_tool_rules(
            ["Read", "Edit", "Finish"],
            create_file_tool_roots=("src/generated",),
        ),
        workspace_root=tmp_path,
    )

    blocked = policy.classify(
        "Write",
        {"file_path": "src/generated/Existing.cs", "content": "class Existing { }\n"},
    )

    assert blocked.allowed is False
    assert blocked.policy_violation is True


def test_build_visible_toolset_keeps_prompt_runtime_and_policy_in_sync() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "MultiEdit", "Write", "Bash", "Finish"],
        mcp_tools=["mcp__sonarqube__issue_show", "mcp__sonarqube__project_overview"],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )

    toolset = build_visible_toolset(
        registry,
        ["Read", "Edit", "MultiEdit", "mcp__sonarqube__issue_show"],
        include_controlled_bash=True,
        bash_file_creation_roots=("src/generated",),
        create_file_tool_roots=("src/generated",),
    )

    assert "Bash" in toolset.visible_tools
    assert "Write" in toolset.visible_tools
    assert "Finish" in toolset.visible_tools
    assert "Write(create_file_under=src/generated)" in toolset.allowed_tools
    assert "Bash(create_file_under=src/generated)" in toolset.allowed_tools
    assert "mcp__sonarqube__project_overview" in toolset.hidden_tools
    assert toolset.disabled_reasons["mcp__sonarqube__project_overview"] == "mcp_tool_not_visible"


def test_tool_policy_normalizes_wrapped_tool_names() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "MultiEdit", "Bash", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(
        registry,
        build_allowed_fix_tool_rules(["Read", "Edit", "MultiEdit", "Finish"], include_controlled_bash=True),
    )

    bash_decision = policy.classify("<tool_call>Bash</tool_call>", {"command": "pwd"})
    edit_decision = policy.classify("<tool_call>EditAsync</tool_call>", {"file_path": "Foo.cs"})
    multiedit_decision = policy.classify("MultiEditAsync", {"file_path": "Foo.cs", "edits": []})

    assert bash_decision.tool_name == "Bash"
    assert bash_decision.allowed is True
    assert bash_decision.policy_violation is False
    assert edit_decision.tool_name == "Edit"
    assert edit_decision.allowed is True
    assert multiedit_decision.tool_name == "MultiEdit"
    assert multiedit_decision.allowed is True


def test_tool_policy_downgrades_harmless_disallowed_shell_to_warning() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Bash", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit", "Finish"])

    decision = policy.classify("Bash", {"command": "pwd"})

    assert decision.allowed is False
    assert decision.policy_violation is False
    assert decision.severity == "warning"
    assert policy.is_forbidden_tool("Bash", {"command": "pwd"}) is False


def test_tool_policy_allows_benign_chained_shell_diagnostics() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "MultiEdit", "Bash", "Finish"],
        mcp_tools=[],
        forbidden_tools={"mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(
        registry,
        build_allowed_fix_tool_rules(["Read", "Edit", "MultiEdit", "Finish"], include_controlled_bash=True),
    )

    decision = policy.classify(
        "Bash",
        {
            "command": "ls \"OpenAuth.Core/OpenAuth.App/Foo.cs\" 2>&1 || echo \"---\" && ls \"OpenAuth.Core/OpenAuth.App\" 2>&1"
        },
    )

    assert decision.allowed is True
    assert decision.policy_violation is False


def test_agent_runtime_runs_hooks_and_collects_tool_usage() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])

    class HookSpy:
        def __init__(self) -> None:
            self.before_tools: list[str] = []
            self.after_tools: list[str] = []
            self.finalized: list[tuple[str, ...]] = []

        def before_tool_call(self, context) -> None:
            self.before_tools.append(context.tool_name)

        def after_tool_call(self, context) -> None:
            self.after_tools.append(context.tool_name)

        def after_attempt_finalize(self, context) -> None:
            self.finalized.append(context.tool_uses)

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                yield ToolCallEvent("Read")
                yield ToolCallEvent("mcp__sonar-fix__run_build")
                yield TextEvent("done")
                yield ResultEvent(total_cost_usd=0.25, agent_error=None)

            return iterate()

        async def abort(self, reason: str):
            raise AssertionError("abort should not be called")

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    hook_spy = HookSpy()
    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
        hooks=HookPipeline([hook_spy]),
    )

    result = runtime.run(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd=".",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
            metadata={"build_command": "dotnet build"},
        )
    )

    assert result.tool_uses == ("Read", "mcp__sonar-fix__run_build")
    assert result.last_tool_name == "mcp__sonar-fix__run_build"
    assert result.saw_build_tool is True
    assert result.runtime_events[0].kind == AttemptRuntimeEventKind.ATTEMPT_STARTED
    assert any(event.kind == AttemptRuntimeEventKind.USER_MESSAGE_SENT for event in result.runtime_events)
    assert any(event.kind == AttemptRuntimeEventKind.TOOL_CALLED for event in result.runtime_events)
    assert any(event.kind == AttemptRuntimeEventKind.ASSISTANT_TEXT_DELTA for event in result.runtime_events)
    assert result.runtime_events[-1].kind == AttemptRuntimeEventKind.ATTEMPT_FINISHED
    assert hook_spy.before_tools == ["Read", "mcp__sonar-fix__run_build"]
    assert hook_spy.after_tools == ["Read", "mcp__sonar-fix__run_build"]
    assert hook_spy.finalized == [("Read", "mcp__sonar-fix__run_build")]


def test_claude_adapter_build_request_handles_third_party_provider() -> None:
    request = ClaudeAdapter.build_request(
        agent_env={
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_API_KEY": "token",
            "ANTHROPIC_CUSTOM_MODEL_OPTION": "glm-4.7",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
        },
        explicit_model="glm-4.7",
        cwd="workspace",
        system_prompt="system",
        user_prompt="user",
        tools=("Read", "Edit"),
        allowed_tools=("Read", "Edit"),
        max_turns=6,
        max_budget_usd=2.5,
        stderr_handler=None,
        build_command="dotnet build",
    )

    assert request.model == "glm-4.7"
    assert request.env["ANTHROPIC_MODEL"] == "glm-4.7"
    assert "CLAUDE_MODEL" not in request.env
    assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in request.env
    assert request.extra_args == {
        "setting-sources": "project,local",
        "bare": None,
    }
    assert request.metadata["mode"] == "bare"


def test_claude_adapter_session_translates_sdk_messages() -> None:
    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input = {"file_path": "Foo.cs", "offset": 10, "limit": 5}

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeThinkingBlock:
        def __init__(self, thinking: str) -> None:
            self.thinking = thinking

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [
                FakeThinkingBlock("first inspect the file"),
                FakeToolUseBlock("Read"),
                FakeTextBlock("hello"),
            ]

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

        async def query(self, prompt: str) -> None:
            return None

        def receive_response(self):
            return fake_receive_response()

    adapter = ClaudeAdapter(
        ClaudeSDKDependencies(
            client_cls=lambda options: FakeClient(),
            options_cls=lambda **kwargs: kwargs,
            assistant_message_cls=FakeAssistantMessage,
            result_message_cls=FakeResultMessage,
            text_block_cls=FakeTextBlock,
            tool_use_block_cls=FakeToolUseBlock,
        )
    )

    session = adapter.create_session(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd="workspace",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
        )
    )

    async def collect_events():
        await session.connect(1)
        await session.send("user")
        events = [event async for event in session.stream_events()]
        await session.close()
        return events

    events = asyncio.run(collect_events())

    assert isinstance(events[0], TraceEvent)
    assert events[0].message_type == "FakeThinkingBlock"
    assert "first inspect the file" in events[0].preview
    assert isinstance(events[1], ToolCallEvent)
    assert events[1].name == "Read"
    assert events[1].payload["file_path"] == "Foo.cs"
    assert events[1].raw_payload["file_path"] == "Foo.cs"
    assert isinstance(events[2], TextEvent)
    assert events[2].text == "hello"
    assert isinstance(events[3], ResultEvent)
    assert events[3].total_cost_usd == 0.1


def test_claude_adapter_session_normalizes_wrapped_tool_name() -> None:
    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name
            self.input = {"command": "pwd"}

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeToolUseBlock("<tool_call>Bash</tool_call>")]

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

        async def query(self, prompt: str) -> None:
            return None

        def receive_response(self):
            return fake_receive_response()

    adapter = ClaudeAdapter(
        ClaudeSDKDependencies(
            client_cls=lambda options: FakeClient(),
            options_cls=lambda **kwargs: kwargs,
            assistant_message_cls=FakeAssistantMessage,
            result_message_cls=FakeResultMessage,
            text_block_cls=object,
            tool_use_block_cls=FakeToolUseBlock,
        )
    )

    session = adapter.create_session(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd="workspace",
            tools=("Read", "Bash"),
            allowed_tools=("Read", "Bash"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
        )
    )

    async def collect_events():
        await session.connect(1)
        await session.send("user")
        events = [event async for event in session.stream_events()]
        await session.close()
        return events

    events = asyncio.run(collect_events())

    assert isinstance(events[0], ToolCallEvent)
    assert events[0].name == "Bash"
    assert events[0].payload["command"] == "pwd"


def test_claude_adapter_session_tolerates_none_assistant_content_and_none_error_list() -> None:
    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = None

    class FakeResultMessage:
        def __init__(self) -> None:
            self.total_cost_usd = 0.1
            self.is_error = True
            self.result = "agent failed"
            self.errors = None

    async def fake_receive_response():
        yield FakeAssistantMessage()
        yield FakeResultMessage()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def query(self, prompt: str) -> None:
            return None

        def receive_response(self):
            return fake_receive_response()

    adapter = ClaudeAdapter(
        ClaudeSDKDependencies(
            client_cls=lambda options: FakeClient(),
            options_cls=lambda **kwargs: kwargs,
            assistant_message_cls=FakeAssistantMessage,
            result_message_cls=FakeResultMessage,
            text_block_cls=object,
            tool_use_block_cls=object,
        )
    )

    session = adapter.create_session(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd="workspace",
            tools=("Read",),
            allowed_tools=("Read",),
            max_turns=2,
            max_budget_usd=1.0,
            env={},
        )
    )

    async def collect_events():
        await session.connect(1)
        await session.send("user")
        events = [event async for event in session.stream_events()]
        await session.close()
        return events

    events = asyncio.run(collect_events())

    assert len(events) == 1
    assert isinstance(events[0], ResultEvent)
    assert events[0].agent_error == "agent failed"


def test_claude_gateway_timeout_probe_passes_explicit_model_for_third_party_provider(
    monkeypatch,
) -> None:
    adapter = ClaudeAdapter(
        ClaudeSDKDependencies(
            client_cls=lambda options: None,
            options_cls=lambda **kwargs: kwargs,
            assistant_message_cls=object,
            result_message_cls=object,
            text_block_cls=object,
            tool_use_block_cls=object,
        )
    )
    request = ClaudeAdapter.build_request(
        agent_env={
            "ANTHROPIC_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding",
            "ANTHROPIC_API_KEY": "token",
            "ANTHROPIC_CUSTOM_MODEL_OPTION": "glm-4.7",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
        },
        explicit_model="glm-4.7",
        cwd="workspace",
        system_prompt="system",
        user_prompt="user",
        tools=("Read", "Edit"),
        allowed_tools=("Read", "Edit"),
        max_turns=6,
        max_budget_usd=2.5,
        stderr_handler=None,
        build_command="dotnet build",
    )
    session = adapter.create_session(request)
    recorded: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (b"OK", b"")

    async def fake_create_subprocess_exec(*command, **kwargs):
        recorded["command"] = command
        recorded["env"] = kwargs.get("env", {})
        return FakeProcess()

    monkeypatch.setattr(claude_adapter_module, "_resolve_sdk_cli_path", lambda: "claude")
    monkeypatch.setattr(
        claude_adapter_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    diagnostic = asyncio.run(session.diagnose_connect_timeout())

    assert diagnostic == "连接诊断：Claude CLI 最小请求可用，返回：OK"
    assert recorded["command"] == (
        "claude",
        "--print",
        "--model",
        "glm-4.7",
        "--setting-sources",
        "project,local",
        "--bare",
        "Reply with OK only.",
    )
    assert recorded["env"]["ANTHROPIC_MODEL"] == "glm-4.7"
    assert "CLAUDE_MODEL" not in recorded["env"]


def test_agent_runtime_classifies_follow_up_timeout_after_edit() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                yield ToolCallEvent("Edit")
                await asyncio.sleep(0.05)
                yield ResultEvent(total_cost_usd=0.1, agent_error=None)

            return iterate()

        async def abort(self, reason: str):
            class AbortResult:
                reason = reason
                actions = ("interrupt", "disconnect")
                errors = ()

            return AbortResult()

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=0.01,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    with pytest.raises(AgentRuntimeError) as exc_info:
        runtime.run(
            GatewayRequest(
                system_prompt="system",
                user_prompt="user",
                cwd="workspace",
                tools=("Read", "Edit"),
                allowed_tools=("Read", "Edit"),
                max_turns=4,
                max_budget_usd=1.0,
                env={},
            )
        )

    assert exc_info.value.partial_result.timeout_stage == "post_edit_stall"


def test_agent_runtime_reports_connect_timeout_diagnostic() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            raise asyncio.TimeoutError()

        async def diagnose_connect_timeout(self) -> str:
            return "连接诊断：Failed to authenticate. API Error: 403 quota exhausted"

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                if False:
                    yield None

            return iterate()

        async def abort(self, reason: str):
            class AbortResult:
                actions = ("disconnect",)
                errors = ()

            result = AbortResult()
            result.reason = reason
            return result

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    with pytest.raises(AgentRuntimeError) as exc_info:
        runtime.run(
            GatewayRequest(
                system_prompt="system",
                user_prompt="user",
                cwd="workspace",
                tools=("Read", "Edit"),
                allowed_tools=("Read", "Edit"),
                max_turns=4,
                max_budget_usd=1.0,
                env={},
            )
        )

    assert "未完成初始化" in str(exc_info.value)
    assert "Failed to authenticate" in str(exc_info.value)


def test_agent_runtime_does_not_treat_system_retry_events_as_first_response() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])
    abort_reasons: list[str] = []

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def diagnose_connect_timeout(self) -> str:
            return "连接诊断：使用同配置执行最小 CLI 请求时也在 12 秒内无响应。"

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                while True:
                    yield TraceEvent(
                        "SystemMessage",
                        payload={"subtype": "api_retry"},
                        preview='{"subtype":"api_retry"}',
                    )
                    await asyncio.sleep(0.02)

            return iterate()

        async def abort(self, reason: str):
            abort_reasons.append(reason)

            class AbortResult:
                actions = ("disconnect",)
                errors = ()

            result = AbortResult()
            result.reason = reason
            return result

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=0.05,
            follow_up_seconds=0.05,
            issue_hard_timeout_seconds=0.5,
            heartbeat_interval_seconds=10,
        ),
    )

    with pytest.raises(AgentRuntimeError) as exc_info:
        runtime.run(
            GatewayRequest(
                system_prompt="system",
                user_prompt="user",
                cwd="workspace",
                tools=("Read", "Edit"),
                allowed_tools=("Read", "Edit"),
                max_turns=4,
                max_budget_usd=1.0,
                env={},
            )
        )

    assert exc_info.value.partial_result.timeout_stage == "first_response_timeout"
    assert abort_reasons == ["first_response_timeout"]
    assert "连接诊断：使用同配置执行最小 CLI 请求时也在 12 秒内无响应。" in str(exc_info.value)


def test_agent_runtime_keeps_follow_up_timeout_classification_when_only_system_retries_arrive() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])
    abort_reasons: list[str] = []

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                yield ToolCallEvent("Read")
                while True:
                    yield TraceEvent(
                        "SystemMessage",
                        payload={"subtype": "api_retry"},
                        preview='{"subtype":"api_retry"}',
                    )
                    await asyncio.sleep(0.02)

            return iterate()

        async def abort(self, reason: str):
            abort_reasons.append(reason)

            class AbortResult:
                actions = ("disconnect",)
                errors = ()

            result = AbortResult()
            result.reason = reason
            return result

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=0.05,
            follow_up_seconds=0.05,
            issue_hard_timeout_seconds=0.5,
            heartbeat_interval_seconds=10,
        ),
    )

    with pytest.raises(AgentRuntimeError) as exc_info:
        runtime.run(
            GatewayRequest(
                system_prompt="system",
                user_prompt="user",
                cwd="workspace",
                tools=("Read", "Edit"),
                allowed_tools=("Read", "Edit"),
                max_turns=4,
                max_budget_usd=1.0,
                env={},
            )
        )

    assert exc_info.value.partial_result.timeout_stage == "post_read_stall"
    assert abort_reasons == ["follow_up_response_timeout"]


def test_agent_runtime_logs_request_snapshot_before_send(capsys) -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                yield ResultEvent(total_cost_usd=0.0)

            return iterate()

        async def abort(self, reason: str):
            raise AssertionError("abort should not be called")

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    runtime.run(
        GatewayRequest(
            system_prompt="system prompt line",
            user_prompt="user prompt line",
            cwd="workspace",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={
                "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                "ANTHROPIC_MODEL": "glm-5-turbo",
                "ANTHROPIC_API_KEY": "token-123456",
                "SONARQUBE_TOKEN": "should-not-appear",
            },
            metadata={
                "endpoint": "https://open.bigmodel.cn/api/anthropic",
                "model_display": "glm-5-turbo",
                "mode": "bare",
                "build_command": "dotnet build",
            },
        )
    )

    output = capsys.readouterr().out

    assert "[SYSTEM PROMPT]" in output
    assert "[REQUEST SNAPSHOT]" in output
    assert '"reason": "before_send"' in output
    assert '"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"' in output
    assert '"ANTHROPIC_MODEL": "glm-5-turbo"' in output
    assert '"ANTHROPIC_API_KEY": "<redacted>"' in output
    assert "token-123456" not in output
    assert "should-not-appear" not in output


def test_agent_runtime_logs_request_snapshot_when_sdk_reports_api_retry(capsys) -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                yield TraceEvent(
                    "SystemMessage",
                    payload={
                        "subtype": "api_retry",
                        "attempt": 2,
                        "max_retries": 10,
                        "retry_delay_ms": 1500.0,
                        "error_status": 503,
                        "error": "server_error",
                        "session_id": "session-123",
                        "uuid": "uuid-123",
                    },
                    preview='{"subtype":"api_retry","error_status":503}',
                )
                yield ResultEvent(total_cost_usd=0.0)

            return iterate()

        async def abort(self, reason: str):
            raise AssertionError("abort should not be called")

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    runtime.run(
        GatewayRequest(
            system_prompt="system prompt line",
            user_prompt="user prompt line",
            cwd="workspace",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={
                "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                "ANTHROPIC_MODEL": "glm-5-turbo",
            },
            metadata={
                "endpoint": "https://open.bigmodel.cn/api/anthropic",
                "model_display": "glm-5-turbo",
                "mode": "bare",
                "build_command": "dotnet build",
            },
        )
    )

    output = capsys.readouterr().out

    assert "SDK api_retry: attempt=2/10, status=503, error=server_error" in output
    assert '"reason": "api_retry"' in output
    assert output.count("[REQUEST SNAPSHOT]") >= 2


def test_agent_runtime_appends_closest_edit_snippet_for_string_not_found(tmp_path) -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])
    target_file = tmp_path / "Foo.cs"
    target_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        return value;",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                yield ToolCallEvent(
                    "Edit",
                    payload={
                        "file_path": "Foo.cs",
                        "old_string_preview": "return val;",
                        "old_string_length": 11,
                    },
                    raw_payload={
                        "file_path": "Foo.cs",
                        "old_string": "return val;",
                        "new_string": "return value;",
                    },
                )
                yield ResultEvent(total_cost_usd=0.1, agent_error="String to replace not found")

            return iterate()

        async def abort(self, reason: str):
            raise AssertionError("abort should not be called")

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    result = runtime.run(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd=str(tmp_path),
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
        )
    )

    assert "String to replace not found" in (result.agent_error or "")
    assert "Closest snippet for retry: Foo.cs:5-5" in (result.agent_error or "")
    assert "return value;" in (result.agent_error or "")


def test_agent_runtime_sends_edit_nudge_after_repeated_non_edit_calls() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])
    sent_prompts: list[str] = []

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            sent_prompts.append(user_prompt)

        def stream_events(self):
            async def iterate():
                for _ in range(4):
                    yield ToolCallEvent("Read")
                    yield TraceEvent("UserMessage", preview="已读取文件")
                yield ResultEvent(total_cost_usd=0.1, agent_error=None)

            return iterate()

        async def abort(self, reason: str):
            raise AssertionError("abort should not be called")

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    result = runtime.run(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd="workspace",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
        )
    )

    assert sent_prompts[0] == "user"
    assert len(sent_prompts) == 2
    assert "立即使用 Edit、MultiEdit" in sent_prompts[1]
    assert "Write" in sent_prompts[1]
    assert result.edit_nudge_count == 1
    assert any(event.kind == AttemptRuntimeEventKind.EDIT_NUDGE_SENT for event in result.runtime_events)


def test_agent_runtime_aborts_after_invalid_write_tool_input_burst() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])
    abort_reasons: list[str] = []

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            return None

        def stream_events(self):
            async def iterate():
                for _ in range(2):
                    yield ToolCallEvent("Edit")
                    yield TraceEvent(
                        "UserMessage",
                        preview=(
                            "<tool_use_error>InputValidationError: Edit failed due to the following issues:\n"
                            "The required parameter `old_string` is missing\n"
                            "The required parameter `new_string` is missing</tool_use_error>"
                        ),
                    )

            return iterate()

        async def abort(self, reason: str):
            abort_reasons.append(reason)

            class AbortResult:
                actions = ("disconnect",)
                errors = ()

            result = AbortResult()
            result.reason = reason
            return result

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    result = runtime.run(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd="workspace",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
        )
    )

    assert "Invalid write tool input burst detected" in (result.agent_error or "")
    assert result.invalid_write_tool_input_count == 2
    assert result.successful_edit_count == 0
    assert abort_reasons == ["invalid_write_tool_input_burst"]
    assert any(
        event.kind == AttemptRuntimeEventKind.ATTEMPT_FINISHED
        and event.payload.get("failure_kind") == "tool_input_invalid"
        for event in result.runtime_events
    )


def test_agent_runtime_keeps_edit_nudge_enabled_after_failed_edit() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit"],
        mcp_tools=[],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit"])
    sent_prompts: list[str] = []

    class FakeSession:
        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            sent_prompts.append(user_prompt)

        def stream_events(self):
            async def iterate():
                yield ToolCallEvent("Edit")
                yield TraceEvent(
                    "UserMessage",
                    preview=(
                        "<tool_use_error>InputValidationError: Edit failed due to the following issues:\n"
                        "The required parameter `old_string` is missing\n"
                        "The required parameter `new_string` is missing</tool_use_error>"
                    ),
                )
                for _ in range(4):
                    yield ToolCallEvent("Read")
                    yield TraceEvent("UserMessage", preview="已读取文件")
                yield ResultEvent(total_cost_usd=0.1, agent_error=None)

            return iterate()

        async def abort(self, reason: str):
            raise AssertionError("abort should not be called")

        async def close(self):
            class Result:
                reason = "normal_shutdown"
                actions = ("disconnect",)
                errors = ()

            return Result()

    class FakeGateway:
        def create_session(self, request: GatewayRequest):
            return FakeSession()

    runtime = AgentRuntime(
        gateway=FakeGateway(),
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
    )

    result = runtime.run(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd="workspace",
            tools=("Read", "Edit"),
            allowed_tools=("Read", "Edit"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
        )
    )

    assert len(sent_prompts) == 2
    assert "立即使用 Edit、MultiEdit" in sent_prompts[1]
    assert result.edit_nudge_count == 1
    assert result.successful_edit_count == 0
    assert result.invalid_write_tool_input_count == 1
