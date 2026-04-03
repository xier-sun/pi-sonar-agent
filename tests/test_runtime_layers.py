from __future__ import annotations

import asyncio

from pi_sonar_agent.core.agent_runtime import AgentRuntime, RuntimeTimeouts
from pi_sonar_agent.core.claude_adapter import ClaudeAdapter, ClaudeSDKDependencies
from pi_sonar_agent.core.hooks import HookPipeline
from pi_sonar_agent.core.model_gateway import (
    GatewayRequest,
    ResultEvent,
    TextEvent,
    ToolCallEvent,
)
from pi_sonar_agent.core.policy import ToolPolicy
from pi_sonar_agent.core.registry import ToolKind, build_fix_tool_registry
from pi_sonar_agent.core.resource_loader import ResourceLoader


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


def test_tool_policy_classifies_allowed_build_and_forbidden_tools() -> None:
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Write"],
        mcp_tools=[],
        forbidden_tools={"Bash", "mcp__sonar-fix__git_push"},
    )
    policy = ToolPolicy(registry, ["Read", "Edit", "Write"])

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

    assert request.model is None
    assert request.env["CLAUDE_MODEL"] == "glm-4.7"
    assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in request.env
    assert request.extra_args == {"bare": None}
    assert request.metadata["mode"] == "bare"


def test_claude_adapter_session_translates_sdk_messages() -> None:
    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeToolUseBlock("Read"), FakeTextBlock("hello")]

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

    assert isinstance(events[0], ToolCallEvent)
    assert events[0].name == "Read"
    assert isinstance(events[1], TextEvent)
    assert events[1].text == "hello"
    assert isinstance(events[2], ResultEvent)
    assert events[2].total_cost_usd == 0.1
