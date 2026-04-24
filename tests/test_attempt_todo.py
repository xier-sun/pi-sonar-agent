from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.agent.claude_agent import AgentRuntimeResult, ClaudeFixAgent, SonarIssue
from pi_sonar_agent.core.agent_role_prompts import (
    build_fix_role_system_prompt,
    build_fix_role_user_prompt,
)
from pi_sonar_agent.core.agent_runtime import AgentRuntime, RuntimeTimeouts
from pi_sonar_agent.core.attempt_todo import (
    AttemptTodoStore,
    build_attempt_todo_runtime,
    render_attempt_todo_list,
)
from pi_sonar_agent.core.events import AttemptRuntimeEventKind
from pi_sonar_agent.core.hooks import HookPipeline
from pi_sonar_agent.core.memory.issue_working_memory import create_initial_issue_working_memory
from pi_sonar_agent.core.model_gateway import (
    GatewayRequest,
    ResultEvent,
    TextEvent,
    ToolCallEvent,
)
from pi_sonar_agent.core.policy import ToolPolicy
from pi_sonar_agent.core.registry import build_fix_tool_registry


def _demo_issue() -> SonarIssue:
    return SonarIssue(
        key="issue-todo-demo",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=12,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )


def test_attempt_todo_store_updates_and_renders(tmp_path: Path) -> None:
    store = AttemptTodoStore(tmp_path, "issue-todo-demo", role="fix")
    state = store.update(
        [
            {
                "content": "Read the target method",
                "activeForm": "Reading the target method",
                "status": "completed",
            },
            {
                "content": "Apply the minimal patch",
                "activeForm": "Applying the minimal patch",
                "status": "in_progress",
            },
        ]
    )

    assert store.load() == state
    rendered = render_attempt_todo_list(state)
    assert "[x] #1: Read the target method" in rendered
    assert "[>] #2: Applying the minimal patch" in rendered
    assert "(1/2 completed)" in rendered


def test_build_attempt_todo_runtime_exposes_sdk_mcp_tool(tmp_path: Path) -> None:
    store = AttemptTodoStore(tmp_path, "issue-todo-demo", role="fix")
    runtime = build_attempt_todo_runtime(store)

    assert runtime.enabled is True
    assert runtime.visible_tool_name == "mcp__attempt-todo__TodoWrite"
    assert runtime.tool_names == ("mcp__attempt-todo__TodoWrite",)
    assert runtime.server_configs["attempt-todo"]["type"] == "sdk"


def test_fix_role_prompt_includes_todowrite_guidance(tmp_path: Path) -> None:
    issue = _demo_issue()
    working_memory = create_initial_issue_working_memory(issue)
    store = AttemptTodoStore(tmp_path, issue.key, role="fix")
    todo_state = store.update(
        [
            {
                "content": "Inspect the failing method",
                "activeForm": "Inspecting the failing method",
                "status": "completed",
            },
            {
                "content": "Rewrite the nested branches",
                "activeForm": "Rewriting the nested branches",
                "status": "in_progress",
            },
        ]
    )

    system_prompt = build_fix_role_system_prompt(
        todo_write_tool_name="mcp__attempt-todo__TodoWrite",
    )
    user_prompt = build_fix_role_user_prompt(
        issue=issue,
        code_context="   12 | if (a) { if (b) { Work(); } }",
        file_path_candidates=("src/Foo.cs",),
        working_memory=working_memory,
        attempt_todo_state=todo_state,
        todo_write_tool_name="mcp__attempt-todo__TodoWrite",
        fix_memory=None,
        retry_feedback="",
    )

    assert "TodoWrite 规则" not in system_prompt
    assert "【当前 Attempt Todo】" in user_prompt
    assert "mcp__attempt-todo__TodoWrite" in user_prompt
    assert "[>] #2: Rewriting the nested branches" in user_prompt
    assert "=== DYNAMIC_BOUNDARY ===" in user_prompt
    assert "<system-reminder>" in user_prompt


def test_agent_runtime_sends_todowrite_reminder_after_repeated_non_todo_tools(
    tmp_path: Path,
) -> None:
    store = AttemptTodoStore(tmp_path, "issue-todo-demo", role="fix")
    runtime_config = build_attempt_todo_runtime(store)
    registry = build_fix_tool_registry(
        builtin_tools=["Read", "Edit", "Finish"],
        mcp_tools=[runtime_config.visible_tool_name],
        forbidden_tools={"Bash"},
    )
    policy = ToolPolicy(
        registry,
        ["Read", runtime_config.visible_tool_name, "Finish"],
    )

    class FakeSession:
        def __init__(self) -> None:
            self.sent_messages: list[str] = []

        async def connect(self, timeout_seconds: float) -> None:
            return None

        async def send(self, user_prompt: str) -> None:
            self.sent_messages.append(user_prompt)

        def stream_events(self):
            async def iterate():
                yield ToolCallEvent("Read")
                yield TextEvent("first read complete")
                yield ToolCallEvent("Read")
                yield TextEvent("second read complete")
                yield ToolCallEvent("Read")
                yield TextEvent("third read complete")
                yield ResultEvent(total_cost_usd=0.0, agent_error=None)

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
        def __init__(self) -> None:
            self.session = FakeSession()

        def create_session(self, request: GatewayRequest):
            return self.session

    gateway = FakeGateway()
    runtime = AgentRuntime(
        gateway=gateway,
        tool_policy=policy,
        timeouts=RuntimeTimeouts(
            client_connect_seconds=1,
            first_response_seconds=1,
            follow_up_seconds=1,
            issue_hard_timeout_seconds=5,
            heartbeat_interval_seconds=10,
        ),
        hooks=HookPipeline(),
    )

    result = runtime.run(
        GatewayRequest(
            system_prompt="system",
            user_prompt="user",
            cwd=str(tmp_path),
            tools=("Read", "Edit"),
            allowed_tools=("Read", runtime_config.visible_tool_name, "Finish"),
            max_turns=4,
            max_budget_usd=1.0,
            env={},
            metadata={
                "build_command": "dotnet build",
                "issue_key": "issue-todo-demo",
                "todo_role": "fix",
                "todo_write_tool_name": runtime_config.visible_tool_name,
                "todo_write_display_name": runtime_config.display_name,
                "todo_write_nag_threshold": "3",
                "todo_write_max_reminders": "2",
            },
        )
    )

    assert result.todo_reminder_count == 1
    assert any(
        event.kind == AttemptRuntimeEventKind.TODO_REMINDER_SENT
        for event in result.runtime_events
    )
    assert len(gateway.session.sent_messages) == 2
    assert runtime_config.visible_tool_name in gateway.session.sent_messages[1]


def test_fix_issue_request_includes_todowrite_tool(monkeypatch, tmp_path: Path) -> None:
    agent = ClaudeFixAgent(
        sonar_host="https://sonar.example",
        sonar_token="token",
    )
    issue = _demo_issue()

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    runtime_requests: list[GatewayRequest] = []

    def fake_runtime_run(self, request):
        runtime_requests.append(request)
        return AgentRuntimeResult(
            agent_error=None,
            tool_uses=("Read",),
            last_tool_name="Read",
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path)

    assert runtime_requests
    request = runtime_requests[0]
    assert "mcp__attempt-todo__TodoWrite" in request.allowed_tools
    assert request.metadata["todo_write_tool_name"] == "mcp__attempt-todo__TodoWrite"
    assert result.attempt_todo_state is not None
    assert result.attempt_todo_state.items == ()
