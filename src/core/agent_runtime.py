"""Single-attempt model runtime with hooks and tool policy."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anyio

from pi_sonar_agent.core.hooks import AttemptFinalizeContext, HookPipeline, ToolCallContext
from pi_sonar_agent.core.model_gateway import (
    GatewayAbortResult,
    GatewayRequest,
    ResultEvent,
    TextEvent,
    ToolCallEvent,
    TraceEvent,
    format_abort_result,
    format_timeout_abort_suffix,
)
from pi_sonar_agent.core.policy import ToolPolicy, ToolPolicyHook, ToolUsageTracker


@dataclass(frozen=True)
class RuntimeTimeouts:
    """Timeout settings for a single issue-fix runtime loop."""

    client_connect_seconds: float
    first_response_seconds: float
    follow_up_seconds: float
    issue_hard_timeout_seconds: float
    heartbeat_interval_seconds: float


@dataclass(frozen=True)
class AgentRuntimeResult:
    """Summary returned by the runtime after a single issue attempt."""

    agent_error: str | None = None
    tool_uses: tuple[str, ...] = ()
    forbidden_tool_uses: tuple[str, ...] = ()
    last_tool_name: str | None = None
    saw_build_tool: bool = False


class AgentRuntimeError(RuntimeError):
    """Runtime error carrying partial tool-usage/result facts."""

    def __init__(self, cause: BaseException, partial_result: AgentRuntimeResult) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.partial_result = partial_result


class AgentRuntime:
    """Run a single issue attempt against a model gateway."""

    def __init__(
        self,
        *,
        gateway,
        tool_policy: ToolPolicy,
        timeouts: RuntimeTimeouts,
        hooks: HookPipeline | None = None,
        run_sync: Callable[[Any], Any] | None = None,
    ) -> None:
        self.gateway = gateway
        self.tool_policy = tool_policy
        self.timeouts = timeouts
        self.hooks = hooks or HookPipeline()
        self.run_sync = run_sync or anyio.run

    def run(self, request: GatewayRequest) -> AgentRuntimeResult:
        """Run a normalized issue attempt synchronously."""

        async def run_with_deadline() -> AgentRuntimeResult:
            return await self._run_with_deadline(request)

        result = self.run_sync(run_with_deadline)
        if result is None:
            return AgentRuntimeResult()
        return result

    async def _run_with_deadline(self, request: GatewayRequest) -> AgentRuntimeResult:
        tracker = ToolUsageTracker()
        hook_pipeline = HookPipeline((ToolPolicyHook(self.tool_policy, tracker), *self.hooks._hooks))
        session = self.gateway.create_session(request)
        last_abort_result: GatewayAbortResult | None = None
        agent_error: str | None = None
        run_started_at = time.monotonic()
        status_lock = threading.Lock()
        status_state: dict[str, object] = {
            "phase": "initializing",
            "last_activity_at": run_started_at,
            "first_response_received": False,
        }
        heartbeat_stop = threading.Event()
        captured_exception: BaseException | None = None

        def build_result() -> AgentRuntimeResult:
            tool_uses, forbidden_tool_uses, last_tool_name, saw_build_tool = tracker.snapshot()
            return AgentRuntimeResult(
                agent_error=agent_error,
                tool_uses=tool_uses,
                forbidden_tool_uses=forbidden_tool_uses,
                last_tool_name=last_tool_name,
                saw_build_tool=saw_build_tool,
            )

        def update_status(phase: str, *, first_response: bool = False) -> None:
            now = time.monotonic()
            with status_lock:
                status_state["phase"] = phase
                status_state["last_activity_at"] = now
                if first_response:
                    status_state["first_response_received"] = True

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(self.timeouts.heartbeat_interval_seconds):
                with status_lock:
                    phase = str(status_state["phase"])
                    last_activity_at = float(status_state["last_activity_at"])
                    first_response_received = bool(status_state["first_response_received"])
                now = time.monotonic()
                total_elapsed = int(now - run_started_at)
                idle_elapsed = int(now - last_activity_at)
                if first_response_received:
                    print(
                        "  [WAIT] "
                        f"当前阶段={phase}，距上次 SDK 消息 {idle_elapsed}s，总耗时 {total_elapsed}s",
                        flush=True,
                    )
                else:
                    if phase == "client_connecting":
                        waiting_text = "仍在等待 Claude SDK Client 初始化完成"
                    elif phase == "sending_query":
                        waiting_text = "请求发送中，仍在等待模型首响应"
                    elif phase == "waiting_for_first_response":
                        waiting_text = "仍在等待模型首响应"
                    else:
                        waiting_text = "仍在等待 SDK 进入可响应状态"
                    print(
                        "  [WAIT] "
                        f"{waiting_text}，当前阶段={phase}，已等待 {total_elapsed}s",
                        flush=True,
                    )

        async def abort_session(reason: str) -> GatewayAbortResult:
            nonlocal last_abort_result

            update_status(f"aborting:{reason}")
            last_abort_result = await session.abort(reason)
            abort_trace = format_abort_result(last_abort_result)
            if abort_trace:
                print(f"  [TRACE] {abort_trace}", flush=True)
            return last_abort_result

        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name="claude-fix-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

        print(
            "  [TRACE] Agent 启动: "
            f"endpoint={request.metadata.get('endpoint', '(sdk default)')}, "
            f"model={request.metadata.get('model_display', '(sdk default)')}, "
            f"mode={request.metadata.get('mode', 'default')}, "
            f"build={request.metadata.get('build_command', 'dotnet build')}",
            flush=True,
        )

        async def run_once() -> AgentRuntimeResult:
            nonlocal agent_error, captured_exception

            print("  [TRACE] 正在初始化 Claude SDK Client...", flush=True)
            update_status("client_connecting")
            try:
                await session.connect(self.timeouts.client_connect_seconds)
            except asyncio.TimeoutError as exc:
                captured_exception = exc
                abort_result = await abort_session("client_connect_timeout")
                raise TimeoutError(
                    f"Claude SDK Client 在 {self.timeouts.client_connect_seconds} 秒内未完成初始化"
                    + format_timeout_abort_suffix(abort_result)
                ) from exc

            try:
                print("  [TRACE] 已创建 Claude SDK Client，准备发送请求...", flush=True)
                update_status("sending_query")
                await session.send(request.user_prompt)
                print("  [TRACE] 请求已发送，等待模型首响应...", flush=True)
                update_status("waiting_for_first_response")

                response_stream = session.stream_events()
                while True:
                    try:
                        timeout_seconds = (
                            self.timeouts.first_response_seconds
                            if not bool(status_state["first_response_received"])
                            else self.timeouts.follow_up_seconds
                        )
                        event = await asyncio.wait_for(anext(response_stream), timeout=timeout_seconds)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as exc:
                        captured_exception = exc
                        if not bool(status_state["first_response_received"]):
                            abort_result = await abort_session("first_response_timeout")
                            raise TimeoutError(
                                f"模型在 {self.timeouts.first_response_seconds} 秒内没有返回首个响应"
                                + format_timeout_abort_suffix(abort_result)
                            ) from exc
                        abort_result = await abort_session("follow_up_response_timeout")
                        raise TimeoutError(
                            f"模型在 {self.timeouts.follow_up_seconds} 秒内没有返回后续响应"
                            + format_timeout_abort_suffix(abort_result)
                        ) from exc

                    if isinstance(event, ToolCallEvent):
                        update_status(f"tool:{event.name}", first_response=True)
                        decision = self.tool_policy.classify(event.name)
                        context = ToolCallContext(tool_name=event.name, decision=decision)
                        hook_pipeline.before_tool_call(context)
                        print(f"  Using tool: {event.name}", flush=True)
                        hook_pipeline.after_tool_call(context)
                    elif isinstance(event, TextEvent):
                        update_status("assistant_text", first_response=True)
                        print(f"  Claude: {event.text[:200]}...", flush=True)
                    elif isinstance(event, ResultEvent):
                        update_status("result_message", first_response=True)
                        print(f"  Done. Cost: ${event.total_cost_usd:.4f}", flush=True)
                        agent_error = event.agent_error
                    elif isinstance(event, TraceEvent):
                        update_status(f"sdk_message:{event.message_type}", first_response=True)
                        print(f"  [TRACE] 收到 SDK 消息类型: {event.message_type}", flush=True)
            except asyncio.CancelledError as exc:
                captured_exception = exc
                await abort_session("issue_hard_timeout")
                raise
            finally:
                await session.close()

            tool_uses, forbidden_tool_uses, last_tool_name, saw_build_tool = tracker.snapshot()
            finalize_context = AttemptFinalizeContext(
                agent_error=agent_error,
                tool_uses=tool_uses,
                forbidden_tool_uses=forbidden_tool_uses,
                last_tool_name=last_tool_name,
                saw_build_tool=saw_build_tool,
                exception=captured_exception,
            )
            hook_pipeline.before_attempt_finalize(finalize_context)
            hook_pipeline.after_attempt_finalize(finalize_context)
            return build_result()

        try:
            return await asyncio.wait_for(
                run_once(),
                timeout=self.timeouts.issue_hard_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            exc_text = str(exc)
            if any(
                marker in exc_text
                for marker in ("没有返回首个响应", "没有返回后续响应", "未完成初始化")
            ):
                raise AgentRuntimeError(exc, build_result()) from exc
            timeout_error = TimeoutError(
                f"单个 issue 在 {self.timeouts.issue_hard_timeout_seconds} 秒内未完成"
                + format_timeout_abort_suffix(last_abort_result)
            )
            raise AgentRuntimeError(timeout_error, build_result()) from exc
        except Exception as exc:
            raise AgentRuntimeError(exc, build_result()) from exc
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
