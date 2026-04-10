"""Single-attempt model runtime with hooks and tool policy."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from pi_sonar_agent.core.events import AttemptEventStream, AttemptRuntimeEventKind
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
    total_duration_seconds: float = 0.0
    time_to_first_model_content_seconds: float = 0.0
    time_after_first_edit_to_finalize_seconds: float = 0.0
    tool_call_count: int = 0
    read_call_count: int = 0
    edit_call_count: int = 0
    assistant_text_events: int = 0
    assistant_text_chars: int = 0
    timeout_stage: str = ""
    last_progress_stage: str = ""
    saw_result_event: bool = False
    continuation_retry_count: int = 0
    continuation_recovered: bool = False
    continuation_timeout_stages: tuple[str, ...] = ()
    runtime_events: tuple[Any, ...] = ()


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
        first_response_at: float | None = None
        first_edit_at: float | None = None
        last_progress_stage = "initializing"
        assistant_text_events = 0
        assistant_text_chars = 0
        timeout_stage = ""
        saw_result_event = False
        event_stream = AttemptEventStream(
            run_label=str(request.metadata.get("run_label", "")),
            issue_key=str(request.metadata.get("issue_key", "")),
            attempt_number=int(request.metadata.get("attempt_number", 0) or 0),
        )
        pending_tool_result_name: str | None = None

        def format_preview(value: str, *, max_chars: int = 1200) -> str:
            text = str(value or "").replace("\r\n", "\n").strip()
            if len(text) <= max_chars:
                return text
            return text[: max_chars - 3].rstrip() + "..."

        def format_payload(payload: dict[str, Any]) -> str:
            if not payload:
                return ""
            try:
                return format_preview(json.dumps(payload, ensure_ascii=False, indent=2), max_chars=2400)
            except TypeError:
                return format_preview(str(payload), max_chars=2400)

        def render_read_preview(tool_name: str, payload: dict[str, Any]) -> str:
            if tool_name != "Read":
                return ""
            path_text = str(payload.get("file_path") or payload.get("path") or "").strip()
            if not path_text:
                return ""
            candidate = Path(request.cwd) / path_text
            if not candidate.exists() or not candidate.is_file():
                return ""
            try:
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                return ""

            offset = payload.get("offset")
            limit = payload.get("limit")
            start_line = payload.get("start_line")
            end_line = payload.get("end_line")
            start = 1
            end = min(len(lines), 20)
            if isinstance(start_line, int) and isinstance(end_line, int):
                start = max(1, start_line)
                end = min(len(lines), end_line)
            elif isinstance(offset, int):
                start = max(1, offset)
                limit_value = limit if isinstance(limit, int) and limit > 0 else 20
                end = min(len(lines), start + limit_value - 1)
            if end < start:
                return ""
            numbered = "\n".join(
                f"{index:4d} | {lines[index - 1]}"
                for index in range(start, end + 1)
            )
            return format_preview(numbered, max_chars=2400)

        def build_result() -> AgentRuntimeResult:
            tool_uses, forbidden_tool_uses, last_tool_name, saw_build_tool = tracker.snapshot()
            total_duration_seconds = max(0.0, time.monotonic() - run_started_at)
            time_to_first_model_content_seconds = 0.0
            if first_response_at is not None:
                time_to_first_model_content_seconds = max(0.0, first_response_at - run_started_at)
            time_after_first_edit_to_finalize_seconds = 0.0
            if first_edit_at is not None:
                time_after_first_edit_to_finalize_seconds = max(0.0, time.monotonic() - first_edit_at)
            read_call_count = sum(1 for item in tool_uses if item == "Read")
            edit_call_count = sum(1 for item in tool_uses if item in {"Edit", "MultiEdit", "Write"})
            return AgentRuntimeResult(
                agent_error=agent_error,
                tool_uses=tool_uses,
                forbidden_tool_uses=forbidden_tool_uses,
                last_tool_name=last_tool_name,
                saw_build_tool=saw_build_tool,
                total_duration_seconds=round(total_duration_seconds, 3),
                time_to_first_model_content_seconds=round(time_to_first_model_content_seconds, 3),
                time_after_first_edit_to_finalize_seconds=round(time_after_first_edit_to_finalize_seconds, 3),
                tool_call_count=len(tool_uses),
                read_call_count=read_call_count,
                edit_call_count=edit_call_count,
                assistant_text_events=assistant_text_events,
                assistant_text_chars=assistant_text_chars,
                timeout_stage=timeout_stage,
                last_progress_stage=last_progress_stage,
                saw_result_event=saw_result_event,
                runtime_events=event_stream.snapshot(),
            )

        def update_status(phase: str, *, first_response: bool = False) -> None:
            nonlocal first_response_at, last_progress_stage
            now = time.monotonic()
            with status_lock:
                status_state["phase"] = phase
                status_state["last_activity_at"] = now
                if first_response:
                    status_state["first_response_received"] = True
            if first_response and first_response_at is None:
                first_response_at = now
            last_progress_stage = phase

        def classify_follow_up_timeout_stage() -> str:
            last_tool_name = tracker.last_tool_name or ""
            if last_progress_stage.startswith("tool:Read") or last_tool_name == "Read":
                return "post_read_stall"
            if last_progress_stage.startswith("tool:") and last_tool_name in {"Edit", "MultiEdit", "Write"}:
                return "post_edit_stall"
            if last_progress_stage == "assistant_text" and first_edit_at is not None:
                return "post_summary_stall"
            if last_progress_stage == "assistant_text":
                return "post_text_stall"
            return "follow_up_response_timeout"

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

        async def diagnose_connect_timeout() -> str:
            diagnose = getattr(session, "diagnose_connect_timeout", None)
            if not callable(diagnose):
                return ""
            try:
                return str(await diagnose()).strip()
            except Exception as exc:
                return f"连接诊断失败：{exc}"

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
        event_stream.emit(
            AttemptRuntimeEventKind.ATTEMPT_STARTED,
            stage="initializing",
            payload={
                "build_command": str(request.metadata.get("build_command", "")),
                "execution_profile": str(request.metadata.get("execution_profile", "")),
                "fast_path_enabled": str(request.metadata.get("fast_path_enabled", "")),
                "allowed_tools": list(request.allowed_tools),
                "max_turns": request.max_turns,
            },
        )

        async def run_once() -> AgentRuntimeResult:
            nonlocal agent_error, assistant_text_chars, assistant_text_events, captured_exception, first_edit_at, saw_result_event, timeout_stage, pending_tool_result_name

            print("  [TRACE] 正在初始化 Claude SDK Client...", flush=True)
            update_status("client_connecting")
            try:
                await session.connect(self.timeouts.client_connect_seconds)
            except asyncio.TimeoutError as exc:
                captured_exception = exc
                connect_diagnostic = await diagnose_connect_timeout()
                abort_result = await abort_session("client_connect_timeout")
                timeout_message = (
                    f"Claude SDK Client 在 {self.timeouts.client_connect_seconds} 秒内未完成初始化"
                )
                if connect_diagnostic:
                    timeout_message += f"\n{connect_diagnostic}"
                timeout_message += format_timeout_abort_suffix(abort_result)
                raise TimeoutError(timeout_message) from exc

            try:
                print("  [TRACE] 已创建 Claude SDK Client，准备发送请求...", flush=True)
                update_status("sending_query")
                prompt_preview = format_preview(request.user_prompt, max_chars=8000)
                prompt_lines = len(str(request.user_prompt or "").splitlines())
                print(
                    "  [USER MESSAGE] "
                    f"chars={len(request.user_prompt or '')}, lines={prompt_lines}",
                    flush=True,
                )
                if prompt_preview:
                    print("  [USER MESSAGE BEGIN]", flush=True)
                    print(prompt_preview, flush=True)
                    print("  [USER MESSAGE END]", flush=True)
                event_stream.emit(
                    AttemptRuntimeEventKind.USER_MESSAGE_SENT,
                    stage="sending_query",
                    payload={
                        "chars": len(request.user_prompt or ""),
                        "lines": prompt_lines,
                        "preview": format_preview(request.user_prompt, max_chars=1500),
                    },
                )
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
                            timeout_stage = "first_response_timeout"
                            event_stream.emit(
                                AttemptRuntimeEventKind.TIMEOUT_CLASSIFIED,
                                stage=timeout_stage,
                                payload={"reason": "first_response_timeout"},
                            )
                            abort_result = await abort_session("first_response_timeout")
                            raise TimeoutError(
                                f"模型在 {self.timeouts.first_response_seconds} 秒内没有返回首个响应"
                                + format_timeout_abort_suffix(abort_result)
                            ) from exc
                        timeout_stage = classify_follow_up_timeout_stage()
                        event_stream.emit(
                            AttemptRuntimeEventKind.TIMEOUT_CLASSIFIED,
                            stage=timeout_stage,
                            payload={"reason": "follow_up_response_timeout"},
                        )
                        abort_result = await abort_session("follow_up_response_timeout")
                        raise TimeoutError(
                            f"模型在 {self.timeouts.follow_up_seconds} 秒内没有返回后续响应"
                            f"\n阶段分类: {timeout_stage}"
                            + format_timeout_abort_suffix(abort_result)
                        ) from exc

                    if isinstance(event, ToolCallEvent):
                        update_status(f"tool:{event.name}", first_response=True)
                        if event.name in {"Edit", "MultiEdit", "Write"} and first_edit_at is None:
                            first_edit_at = time.monotonic()
                        pending_tool_result_name = event.name
                        decision = self.tool_policy.classify(event.name, event.payload)
                        context = ToolCallContext(
                            tool_name=event.name,
                            decision=decision,
                            payload=event.payload,
                            preview=event.preview,
                        )
                        hook_pipeline.before_tool_call(context)
                        print(f"  Using tool: {event.name}", flush=True)
                        if event.preview:
                            print(f"  [TOOL INPUT] {event.name}", flush=True)
                            print(event.preview, flush=True)
                        read_preview = render_read_preview(event.name, event.payload)
                        if read_preview:
                            print(f"  [READ PREVIEW] {event.name}", flush=True)
                            print(read_preview, flush=True)
                        event_stream.emit(
                            AttemptRuntimeEventKind.TOOL_CALLED,
                            stage=f"tool:{event.name}",
                            payload={
                                "tool_name": event.name,
                                "allowed": decision.allowed,
                                "tool_kind": decision.kind.value,
                                "matched_rule": decision.matched_rule,
                                "policy_violation": decision.policy_violation,
                                "tool_payload": event.payload,
                                "tool_preview": event.preview,
                                "read_preview": read_preview,
                            },
                        )
                        hook_pipeline.after_tool_call(context)
                    elif isinstance(event, TextEvent):
                        update_status("assistant_text", first_response=True)
                        if pending_tool_result_name:
                            event_stream.emit(
                                AttemptRuntimeEventKind.TOOL_RESULT_RECEIVED,
                                stage=f"tool_result:{pending_tool_result_name}",
                                payload={"tool_name": pending_tool_result_name},
                            )
                            pending_tool_result_name = None
                        assistant_text_events += 1
                        assistant_text_chars += len(event.text or "")
                        print(f"  Claude: {event.text[:200]}...", flush=True)
                        event_stream.emit(
                            AttemptRuntimeEventKind.ASSISTANT_TEXT_DELTA,
                            stage="assistant_text",
                            payload={
                                "text_length": len(event.text or ""),
                                "preview": str((event.text or "")[:80]),
                                "block_type": event.block_type,
                            },
                        )
                    elif isinstance(event, ResultEvent):
                        update_status("result_message", first_response=True)
                        if pending_tool_result_name:
                            event_stream.emit(
                                AttemptRuntimeEventKind.TOOL_RESULT_RECEIVED,
                                stage=f"tool_result:{pending_tool_result_name}",
                                payload={"tool_name": pending_tool_result_name},
                            )
                            pending_tool_result_name = None
                        saw_result_event = True
                        print(f"  Done. Cost: ${event.total_cost_usd:.4f}", flush=True)
                        agent_error = event.agent_error
                    elif isinstance(event, TraceEvent):
                        update_status(f"sdk_message:{event.message_type}", first_response=True)
                        if pending_tool_result_name:
                            event_stream.emit(
                                AttemptRuntimeEventKind.TOOL_RESULT_RECEIVED,
                                stage=f"tool_result:{pending_tool_result_name}",
                                payload={"tool_name": pending_tool_result_name},
                            )
                            pending_tool_result_name = None
                        trace_label = "THINKING" if "Thinking" in event.message_type else "TRACE"
                        print(f"  [{trace_label}] 收到 SDK 消息类型: {event.message_type}", flush=True)
                        if event.preview:
                            print(event.preview, flush=True)
                        event_stream.emit(
                            AttemptRuntimeEventKind.SDK_TRACE,
                            stage=f"sdk_message:{event.message_type}",
                            payload={
                                "message_type": event.message_type,
                                "preview": event.preview,
                                "payload": event.payload,
                            },
                        )
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
            event_stream.emit(
                AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                stage="completed",
                payload={
                    "agent_error": agent_error or "",
                    "tool_call_count": len(tool_uses),
                    "saw_result_event": saw_result_event,
                },
            )
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
            timeout_stage = "issue_hard_timeout"
            event_stream.emit(
                AttemptRuntimeEventKind.TIMEOUT_CLASSIFIED,
                stage=timeout_stage,
                payload={"reason": "issue_hard_timeout"},
            )
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
