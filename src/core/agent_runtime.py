"""Single-attempt model runtime with hooks and tool policy."""

from __future__ import annotations

import asyncio
import difflib
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
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.policy import ToolPolicy, ToolPolicyHook, ToolUsageTracker, normalize_tool_name
from pi_sonar_agent.core.project_env import MODEL_ENV_KEYS

EDIT_NUDGE_THRESHOLD = 4
MAX_EDIT_NUDGES = 2
INVALID_WRITE_TOOL_INPUT_BURST_THRESHOLD = 2


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
    warning_tool_uses: tuple[str, ...] = ()
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
    edit_nudge_count: int = 0
    successful_edit_count: int = 0
    invalid_write_tool_input_count: int = 0
    runtime_events: tuple[Any, ...] = ()


class AgentRuntimeError(RuntimeError):
    """Runtime error carrying partial tool-usage/result facts."""

    def __init__(self, cause: BaseException, partial_result: AgentRuntimeResult) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.partial_result = partial_result


def _normalize_match_text(value: str) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").split())


def _extract_invalid_write_tool_input_from_preview(preview: str) -> str:
    text = str(preview or "").strip()
    if "InputValidationError" not in text:
        return ""
    if not any(
        marker in text
        for marker in ("file_path", "old_string", "new_string", "edits", "content")
    ):
        return ""
    return text


def _is_write_tool_error_preview(preview: str) -> bool:
    text = str(preview or "").strip()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "<tool_use_error>",
            "InputValidationError",
            "String to replace not found",
            "No changes to make",
        )
    )


def _extract_edit_search_requests(
    tool_name: str,
    raw_payload: dict[str, Any] | None,
) -> tuple[tuple[str, str], ...]:
    payload = dict(raw_payload or {})
    file_path = str(payload.get("file_path") or payload.get("path") or "").strip()
    if not file_path:
        return ()
    if tool_name == "Edit":
        old_string = str(payload.get("old_string") or "")
        if old_string.strip():
            return ((file_path, old_string),)
        return ()
    if tool_name == "MultiEdit":
        requests: list[tuple[str, str]] = []
        for item in payload.get("edits", []) or []:
            if not isinstance(item, dict):
                continue
            old_string = str(item.get("old_string") or "")
            if old_string.strip():
                requests.append((file_path, old_string))
        return tuple(requests)
    return ()


def _find_closest_edit_snippet(
    *,
    cwd: str,
    file_path: str,
    old_string: str,
) -> tuple[int, int, str, float] | None:
    candidate = Path(cwd) / str(file_path or "").replace("/", "\\")
    if not candidate.exists() or not candidate.is_file():
        return None
    try:
        file_lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    old_lines = str(old_string or "").splitlines()
    if not file_lines or not old_lines:
        return None

    comparison_old_lines = old_lines[:80]
    comparison_old_text = "\n".join(comparison_old_lines)
    normalized_old = _normalize_match_text(comparison_old_text)
    if not normalized_old:
        return None

    anchor = next(
        (
            _normalize_match_text(line)
            for line in comparison_old_lines
            if len(_normalize_match_text(line)) >= 8
        ),
        "",
    )
    candidate_starts = [
        index
        for index, line in enumerate(file_lines)
        if anchor and anchor in _normalize_match_text(line)
    ]
    if not candidate_starts:
        candidate_starts = list(range(len(file_lines)))

    base_window = max(1, len(comparison_old_lines))
    window_sizes = tuple(
        sorted(
            {
                max(1, base_window - 1),
                base_window,
                min(len(file_lines), base_window + 1),
            }
        )
    )
    best_match: tuple[int, int, str, float] | None = None
    for start_index in candidate_starts[:200]:
        for window_size in window_sizes:
            end_index = min(len(file_lines), start_index + window_size)
            if end_index <= start_index:
                continue
            snippet = "\n".join(file_lines[start_index:end_index])
            score = difflib.SequenceMatcher(
                None,
                normalized_old,
                _normalize_match_text(snippet),
            ).ratio()
            if best_match is None or score > best_match[3]:
                best_match = (start_index + 1, end_index, snippet, score)
    return best_match


def _maybe_enrich_edit_failure(
    *,
    agent_error: str | None,
    cwd: str,
    last_edit_tool_name: str,
    last_edit_raw_payload: dict[str, Any] | None,
) -> str | None:
    error_text = str(agent_error or "").strip()
    if "String to replace not found" not in error_text:
        return agent_error

    match_sections: list[str] = []
    for file_path, old_string in _extract_edit_search_requests(last_edit_tool_name, last_edit_raw_payload):
        closest = _find_closest_edit_snippet(cwd=cwd, file_path=file_path, old_string=old_string)
        if closest is None:
            continue
        start_line, end_line, snippet, score = closest
        numbered = "\n".join(
            f"{start_line + index:4d} | {line}"
            for index, line in enumerate(snippet.splitlines())
        )
        match_sections.append(
            "\n".join(
                (
                    f"Closest snippet for retry: {file_path}:{start_line}-{end_line} (match={score:.2f})",
                    numbered,
                    "Retry hint: reuse the exact snippet above as old_string before applying the next edit.",
                )
            )
        )
        if len(match_sections) >= 2:
            break
    if not match_sections:
        return agent_error
    return f"{error_text}\n\n" + "\n\n".join(match_sections)


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
        performance_flags = load_performance_flags()
        edit_failure_feedback_enabled = bool(
            getattr(performance_flags, "edit_failure_context_feedback", True)
        )
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
        first_response_deadline_at: float | None = None
        last_progress_at: float | None = None
        event_stream = AttemptEventStream(
            run_label=str(request.metadata.get("run_label", "")),
            issue_key=str(request.metadata.get("issue_key", "")),
            attempt_number=int(request.metadata.get("attempt_number", 0) or 0),
        )
        pending_tool_result_name: str | None = None
        last_edit_tool_name = ""
        last_edit_raw_payload: dict[str, Any] = {}
        consecutive_non_edit_calls = 0
        pending_edit_nudge = False
        edit_nudge_count = 0
        successful_edit_count = 0
        invalid_write_tool_input_count = 0
        invalid_write_tool_input_message = ""

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

        def count_lines(value: str) -> int:
            return len(str(value or "").splitlines())

        def summarize_prompt(
            value: str,
            *,
            preview_chars: int,
            include_preview: bool,
        ) -> dict[str, Any]:
            text = str(value or "")
            summary: dict[str, Any] = {
                "chars": len(text),
                "lines": count_lines(text),
            }
            if include_preview:
                summary["preview"] = format_preview(text, max_chars=preview_chars)
            return summary

        def summarize_env_value(key: str, value: str) -> str:
            key_text = str(key or "").upper()
            text = str(value or "")
            if any(marker in key_text for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                return "<redacted>"
            return format_preview(text, max_chars=240)

        def summarize_model_env(env: dict[str, str]) -> dict[str, str]:
            summary: dict[str, str] = {}
            for key in sorted(MODEL_ENV_KEYS):
                if key not in env:
                    continue
                summary[key] = summarize_env_value(key, str(env.get(key, "")))
            return summary

        def summarize_request_config() -> dict[str, Any]:
            metadata_summary: dict[str, Any] = {}
            for key in (
                "run_label",
                "issue_key",
                "attempt_number",
                "execution_profile",
                "fast_path_enabled",
                "build_command",
                "endpoint",
                "model_display",
                "mode",
                "mcp_servers",
                "mcp_tools_count",
                "mcp_mode",
                "mcp_read_only",
                "mcp_warning",
            ):
                value = request.metadata.get(key)
                if value not in (None, ""):
                    metadata_summary[key] = value
            return {
                "cwd": request.cwd,
                "max_turns": request.max_turns,
                "max_budget_usd": request.max_budget_usd,
                "tools": list(request.tools),
                "allowed_tools": list(request.allowed_tools),
                "mcp_servers": list(request.mcp_servers),
                "extra_args": dict(request.extra_args),
                "sdk_env": summarize_model_env(request.env),
                "metadata": metadata_summary,
            }

        def build_request_snapshot(*, include_prompt_previews: bool) -> dict[str, Any]:
            return {
                "request": summarize_request_config(),
                "system_prompt": summarize_prompt(
                    request.system_prompt,
                    preview_chars=1600,
                    include_preview=include_prompt_previews,
                ),
                "user_prompt": summarize_prompt(
                    request.user_prompt,
                    preview_chars=1600,
                    include_preview=include_prompt_previews,
                ),
            }

        def print_prompt_block(label: str, value: str, *, max_chars: int) -> None:
            preview = format_preview(value, max_chars=max_chars)
            prompt_summary = summarize_prompt(value, preview_chars=max_chars, include_preview=False)
            print(
                f"  [{label}] chars={prompt_summary['chars']}, lines={prompt_summary['lines']}",
                flush=True,
            )
            if preview:
                print(f"  [{label} BEGIN]", flush=True)
                print(preview, flush=True)
                print(f"  [{label} END]", flush=True)

        def print_request_snapshot(reason: str, *, include_prompt_previews: bool) -> None:
            snapshot = build_request_snapshot(include_prompt_previews=include_prompt_previews)
            snapshot["reason"] = reason
            print("  [REQUEST SNAPSHOT]", flush=True)
            print(format_payload(snapshot), flush=True)

        def maybe_print_retry_context(event: TraceEvent) -> None:
            payload = dict(event.payload or {})
            if str(event.message_type or "") != "SystemMessage":
                return
            if str(payload.get("subtype") or "") != "api_retry":
                return

            attempt = payload.get("attempt")
            max_retries = payload.get("max_retries")
            error_status = payload.get("error_status")
            error = payload.get("error")
            retry_delay_ms = payload.get("retry_delay_ms")
            session_id = payload.get("session_id")
            request_uuid = payload.get("uuid")
            delay_text = ""
            if isinstance(retry_delay_ms, (int, float)):
                delay_text = f"{retry_delay_ms:.0f}ms"
            elif retry_delay_ms not in (None, ""):
                delay_text = str(retry_delay_ms)
            print(
                "  [TRACE] SDK api_retry: "
                f"attempt={attempt}/{max_retries}, "
                f"status={error_status}, "
                f"error={error or '(unknown)'}, "
                f"retry_delay={delay_text or '(unknown)'}, "
                f"session_id={session_id or '(unknown)'}, "
                f"uuid={request_uuid or '(unknown)'}",
                flush=True,
            )
            print_request_snapshot("api_retry", include_prompt_previews=False)

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
            tool_uses, forbidden_tool_uses, warning_tool_uses, last_tool_name, saw_build_tool = tracker.snapshot()
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
                warning_tool_uses=warning_tool_uses,
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
                edit_nudge_count=edit_nudge_count,
                successful_edit_count=successful_edit_count,
                invalid_write_tool_input_count=invalid_write_tool_input_count,
                runtime_events=event_stream.snapshot(),
            )

        def update_status(
            phase: str,
            *,
            first_response: bool = False,
            meaningful_progress: bool = False,
        ) -> None:
            nonlocal first_response_at, last_progress_at, last_progress_stage
            now = time.monotonic()
            with status_lock:
                status_state["phase"] = phase
                status_state["last_activity_at"] = now
                if first_response:
                    status_state["first_response_received"] = True
            if first_response and first_response_at is None:
                first_response_at = now
            if meaningful_progress:
                last_progress_at = now
                last_progress_stage = phase

        def trace_event_counts_as_progress(event: TraceEvent) -> bool:
            return str(event.message_type or "") != "SystemMessage"

        def compute_event_timeout_seconds() -> float:
            now = time.monotonic()
            if not bool(status_state["first_response_received"]):
                return (first_response_deadline_at or now) - now
            return ((last_progress_at or now) + self.timeouts.follow_up_seconds) - now

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
                "request": build_request_snapshot(include_prompt_previews=False),
            },
        )

        async def run_once() -> AgentRuntimeResult:
            nonlocal agent_error, assistant_text_chars, assistant_text_events, captured_exception, first_edit_at, saw_result_event, timeout_stage, pending_tool_result_name, last_edit_tool_name, last_edit_raw_payload, edit_nudge_count, consecutive_non_edit_calls, pending_edit_nudge, first_response_deadline_at, successful_edit_count, invalid_write_tool_input_count, invalid_write_tool_input_message

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
                print_prompt_block("SYSTEM PROMPT", request.system_prompt, max_chars=4000)
                print_request_snapshot("before_send", include_prompt_previews=False)
                print_prompt_block("USER MESSAGE", request.user_prompt, max_chars=8000)
                prompt_lines = count_lines(request.user_prompt)
                event_stream.emit(
                    AttemptRuntimeEventKind.USER_MESSAGE_SENT,
                    stage="sending_query",
                    payload={
                        "chars": len(request.user_prompt or ""),
                        "lines": prompt_lines,
                        "preview": format_preview(request.user_prompt, max_chars=1500),
                        "request": build_request_snapshot(include_prompt_previews=False),
                    },
                )
                await session.send(request.user_prompt)
                print("  [TRACE] 请求已发送，等待模型首响应...", flush=True)
                update_status("waiting_for_first_response")
                first_response_deadline_at = time.monotonic() + self.timeouts.first_response_seconds

                async def maybe_send_edit_nudge(trigger_stage: str) -> None:
                    nonlocal pending_edit_nudge, edit_nudge_count
                    if not pending_edit_nudge or successful_edit_count > 0:
                        return
                    if edit_nudge_count >= MAX_EDIT_NUDGES:
                        pending_edit_nudge = False
                        return
                    nudge_message = (
                        "你已经连续多次只做读取/搜索，还没有任何代码修改。"
                        "现在请立即使用 Edit、MultiEdit，或在允许的新文件场景下使用 Write，对当前 Sonar issue 落盘修改。"
                        "不要继续扩展读取范围；如果你确认当前约束下无法安全修复，请直接明确说明原因。"
                    )
                    pending_edit_nudge = False
                    edit_nudge_count += 1
                    print(
                        "  [TRACE] Edit nudge 已发送: "
                        f"index={edit_nudge_count}, stage={trigger_stage}",
                        flush=True,
                    )
                    event_stream.emit(
                        AttemptRuntimeEventKind.EDIT_NUDGE_SENT,
                        stage=trigger_stage,
                        payload={
                            "index": edit_nudge_count,
                            "threshold": EDIT_NUDGE_THRESHOLD,
                            "message": nudge_message,
                        },
                    )
                    update_status("edit_nudge", first_response=True)
                    await session.send(nudge_message)

                async def finalize_pending_tool_result(
                    *,
                    preview: str = "",
                ) -> bool:
                    nonlocal pending_tool_result_name, first_edit_at, successful_edit_count
                    nonlocal consecutive_non_edit_calls, pending_edit_nudge
                    nonlocal invalid_write_tool_input_count, invalid_write_tool_input_message
                    nonlocal agent_error

                    tool_name = str(pending_tool_result_name or "").strip()
                    if not tool_name:
                        return False

                    event_stream.emit(
                        AttemptRuntimeEventKind.TOOL_RESULT_RECEIVED,
                        stage=f"tool_result:{tool_name}",
                        payload={"tool_name": tool_name},
                    )
                    pending_tool_result_name = None

                    if tool_name not in {"Edit", "MultiEdit", "Write"}:
                        await maybe_send_edit_nudge("tool_result")
                        return False

                    invalid_message = _extract_invalid_write_tool_input_from_preview(preview)
                    if invalid_message:
                        invalid_write_tool_input_count += 1
                        invalid_write_tool_input_message = invalid_message
                        if invalid_write_tool_input_count >= INVALID_WRITE_TOOL_INPUT_BURST_THRESHOLD:
                            agent_error = (
                                "Invalid write tool input burst detected; stop this attempt and retry with a precise patch.\n\n"
                                + invalid_message
                            )
                            event_stream.emit(
                                AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                                stage="tool_input_invalid_burst",
                                payload={
                                    "success": False,
                                    "failure_kind": "tool_input_invalid",
                                    "count": invalid_write_tool_input_count,
                                },
                            )
                            await abort_session("invalid_write_tool_input_burst")
                            return True
                        return False

                    if _is_write_tool_error_preview(preview):
                        return False

                    if first_edit_at is None:
                        first_edit_at = time.monotonic()
                    successful_edit_count += 1
                    consecutive_non_edit_calls = 0
                    pending_edit_nudge = False
                    return False

                response_stream = session.stream_events()
                while True:
                    try:
                        timeout_seconds = compute_event_timeout_seconds()
                        if timeout_seconds <= 0:
                            raise asyncio.TimeoutError()
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
                            first_response_diagnostic = await diagnose_connect_timeout()
                            timeout_message = (
                                f"模型在 {self.timeouts.first_response_seconds} 秒内没有返回首个响应"
                            )
                            if first_response_diagnostic:
                                timeout_message += f"\n{first_response_diagnostic}"
                            raise TimeoutError(
                                timeout_message + format_timeout_abort_suffix(abort_result)
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
                        tool_name = normalize_tool_name(event.name)
                        update_status(
                            f"tool:{tool_name}",
                            first_response=True,
                            meaningful_progress=True,
                        )
                        if tool_name in {"Edit", "MultiEdit", "Write"}:
                            pending_edit_nudge = False
                        elif successful_edit_count <= 0:
                            consecutive_non_edit_calls += 1
                            if consecutive_non_edit_calls >= EDIT_NUDGE_THRESHOLD:
                                pending_edit_nudge = True
                                consecutive_non_edit_calls = 0
                        if tool_name in {"Edit", "MultiEdit", "Write"}:
                            last_edit_tool_name = tool_name
                            last_edit_raw_payload = dict(event.raw_payload or {})
                        pending_tool_result_name = tool_name
                        decision = self.tool_policy.classify(tool_name, event.payload)
                        context = ToolCallContext(
                            tool_name=tool_name,
                            decision=decision,
                            payload=event.payload,
                            preview=event.preview,
                        )
                        hook_pipeline.before_tool_call(context)
                        print(f"  Using tool: {tool_name}", flush=True)
                        if event.preview:
                            print(f"  [TOOL INPUT] {tool_name}", flush=True)
                            print(event.preview, flush=True)
                        read_preview = render_read_preview(tool_name, event.payload)
                        if read_preview:
                            print(f"  [READ PREVIEW] {tool_name}", flush=True)
                            print(read_preview, flush=True)
                        event_stream.emit(
                            AttemptRuntimeEventKind.TOOL_CALLED,
                            stage=f"tool:{tool_name}",
                            payload={
                                "tool_name": tool_name,
                                "raw_tool_name": event.name,
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
                        update_status(
                            "assistant_text",
                            first_response=True,
                            meaningful_progress=True,
                        )
                        if pending_tool_result_name:
                            should_abort = await finalize_pending_tool_result()
                            if should_abort:
                                break
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
                        update_status(
                            "result_message",
                            first_response=True,
                            meaningful_progress=True,
                        )
                        if pending_tool_result_name:
                            should_abort = await finalize_pending_tool_result(
                                preview=str(getattr(event, "agent_error", "") or ""),
                            )
                            if should_abort:
                                break
                        saw_result_event = True
                        print(f"  Done. Cost: ${event.total_cost_usd:.4f}", flush=True)
                        agent_error = event.agent_error
                        if edit_failure_feedback_enabled:
                            agent_error = _maybe_enrich_edit_failure(
                                agent_error=agent_error,
                                cwd=request.cwd,
                                last_edit_tool_name=last_edit_tool_name,
                                last_edit_raw_payload=last_edit_raw_payload,
                            )
                    elif isinstance(event, TraceEvent):
                        trace_counts_as_progress = trace_event_counts_as_progress(event)
                        update_status(
                            f"sdk_message:{event.message_type}",
                            first_response=trace_counts_as_progress,
                            meaningful_progress=trace_counts_as_progress,
                        )
                        if pending_tool_result_name:
                            should_abort = await finalize_pending_tool_result(
                                preview=str(event.preview or ""),
                            )
                            if should_abort:
                                break
                        trace_label = "THINKING" if "Thinking" in event.message_type else "TRACE"
                        print(f"  [{trace_label}] 收到 SDK 消息类型: {event.message_type}", flush=True)
                        if event.preview:
                            print(event.preview, flush=True)
                        maybe_print_retry_context(event)
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

            tool_uses, forbidden_tool_uses, warning_tool_uses, last_tool_name, saw_build_tool = tracker.snapshot()
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
                    "edit_nudge_count": edit_nudge_count,
                    "successful_edit_count": successful_edit_count,
                    "invalid_write_tool_input_count": invalid_write_tool_input_count,
                    "warning_tool_uses": list(warning_tool_uses),
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
