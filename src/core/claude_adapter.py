"""Claude SDK implementation of the normalized model gateway."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pi_sonar_agent.core.model_gateway import (
    GatewayAbortResult,
    GatewayRequest,
    ModelGateway,
    ModelGatewaySession,
    ResultEvent,
    TextEvent,
    ToolCallEvent,
    TraceEvent,
)
from pi_sonar_agent.core.policy import normalize_tool_name
from pi_sonar_agent.core.project_env import MODEL_ENV_KEYS

THIRD_PARTY_MODEL_ENV_KEYS = (
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "CLAUDE_MODEL",
    "OPENAI_MODEL",
)

_CONNECT_DIAGNOSTIC_CACHE: dict[tuple[str, str, str], str] = {}


@dataclass(frozen=True)
class ClaudeSDKDependencies:
    """SDK classes injected by ClaudeFixAgent for compatibility and testing."""

    client_cls: Any
    options_cls: Any
    assistant_message_cls: type[Any]
    result_message_cls: type[Any]
    text_block_cls: type[Any]
    tool_use_block_cls: type[Any]


class _ClaudeSDKSessionController:
    """Own the Claude SDK session lifecycle for a single gateway session."""

    def __init__(self, client_manager: Any) -> None:
        self.client_manager = client_manager
        self.client: Any | None = None
        self.response_stream: Any | None = None
        self._closed = False

    async def connect(self, timeout_seconds: float) -> Any:
        self.client = await asyncio.wait_for(
            self.client_manager.__aenter__(),
            timeout=timeout_seconds,
        )
        return self.client

    def bind_response_stream(self, response_stream: Any) -> Any:
        self.response_stream = response_stream
        return response_stream

    async def abort(self, reason: str) -> GatewayAbortResult:
        return await self._shutdown(reason, interrupt=True)

    async def close(self) -> GatewayAbortResult:
        return await self._shutdown("normal_shutdown", interrupt=False)

    async def _shutdown(self, reason: str, *, interrupt: bool) -> GatewayAbortResult:
        if self._closed:
            return GatewayAbortResult(reason=reason, actions=("already_closed",))

        actions: list[str] = []
        errors: list[str] = []

        if interrupt:
            interrupt_call = getattr(self.client, "interrupt", None)
            if callable(interrupt_call):
                try:
                    await interrupt_call()
                    actions.append("interrupt")
                except Exception as exc:
                    errors.append(f"interrupt failed: {exc}")

        response_stream = self.response_stream
        self.response_stream = None
        aclose = getattr(response_stream, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
                actions.append("close_response_stream")
            except Exception as exc:
                errors.append(f"close_response_stream failed: {exc}")

        exit_call = getattr(self.client_manager, "__aexit__", None)
        if callable(exit_call):
            try:
                await exit_call(None, None, None)
                actions.append("disconnect")
            except Exception as exc:
                errors.append(f"disconnect failed: {exc}")

        self._closed = True
        return GatewayAbortResult(
            reason=reason,
            actions=tuple(actions),
            errors=tuple(errors),
        )


class ClaudeGatewaySession(ModelGatewaySession):
    """Claude SDK-backed gateway session that emits normalized events."""

    def __init__(
        self,
        client_manager: Any,
        dependencies: ClaudeSDKDependencies,
        request: GatewayRequest,
    ) -> None:
        self._dependencies = dependencies
        self._controller = _ClaudeSDKSessionController(client_manager)
        self._client: Any | None = None
        self._request = request

    async def connect(self, timeout_seconds: float) -> None:
        self._client = await self._controller.connect(timeout_seconds)

    async def send(self, user_prompt: str) -> None:
        if self._client is None:
            raise RuntimeError("Claude gateway session is not connected")
        await self._client.query(user_prompt)

    def stream_events(self):
        if self._client is None:
            raise RuntimeError("Claude gateway session is not connected")

        async def iterate():
            response_stream = self._controller.bind_response_stream(self._client.receive_response())
            async for message in response_stream:
                if isinstance(message, self._dependencies.assistant_message_cls):
                    content_blocks = getattr(message, "content", ()) or ()
                    if isinstance(content_blocks, (str, bytes)):
                        content_iterable = (content_blocks,)
                    else:
                        try:
                            content_iterable = tuple(content_blocks)
                        except TypeError:
                            payload = _extract_trace_payload(message)
                            payload["content_error"] = "assistant message content was not iterable"
                            yield TraceEvent(
                                message_type=type(message).__name__,
                                payload=payload,
                                preview=_build_preview_from_payload(payload),
                            )
                            continue
                    for block in content_iterable:
                        if isinstance(block, self._dependencies.tool_use_block_cls):
                            payload = _extract_tool_payload(block)
                            raw_payload = _extract_raw_tool_payload(block)
                            normalized_tool_name = normalize_tool_name(getattr(block, "name", ""))
                            yield ToolCallEvent(
                                name=normalized_tool_name or str(getattr(block, "name", "")),
                                payload=payload,
                                raw_payload=raw_payload,
                                preview=_build_preview_from_payload(payload),
                            )
                        elif isinstance(block, self._dependencies.text_block_cls) and block.text.strip():
                            yield TextEvent(text=block.text, block_type=type(block).__name__)
                        else:
                            payload = _extract_trace_payload(block)
                            yield TraceEvent(
                                message_type=type(block).__name__,
                                payload=payload,
                                preview=_build_preview_from_payload(payload),
                            )
                elif isinstance(message, self._dependencies.result_message_cls):
                    yield ResultEvent(
                        total_cost_usd=float(message.total_cost_usd or 0.0),
                        agent_error=self._extract_agent_error(message),
                    )
                else:
                    payload = _extract_trace_payload(message)
                    yield TraceEvent(
                        message_type=type(message).__name__,
                        payload=payload,
                        preview=_build_preview_from_payload(payload),
                    )

        return iterate()

    async def abort(self, reason: str) -> GatewayAbortResult:
        return await self._controller.abort(reason)

    async def close(self) -> GatewayAbortResult:
        return await self._controller.close()

    async def diagnose_connect_timeout(self) -> str:
        cli_path = _resolve_sdk_cli_path()
        endpoint = str(self._request.env.get("ANTHROPIC_BASE_URL", "")).strip()
        cache_model = str(
            self._request.model
            or self._request.env.get("CLAUDE_MODEL")
            or self._request.env.get("OPENAI_MODEL")
            or self._request.metadata.get("model_display", "")
        ).strip()
        cache_key = (cli_path, endpoint, cache_model)
        cached = _CONNECT_DIAGNOSTIC_CACHE.get(cache_key)
        if cached is not None:
            return cached

        command = [cli_path, "--print"]
        explicit_model = str(self._request.model or "").strip()
        if explicit_model:
            command.extend(["--model", explicit_model])
        for flag, value in self._request.extra_args.items():
            if value is None:
                command.append(f"--{flag}")
            else:
                command.extend([f"--{flag}", str(value)])
        command.append("Reply with OK only.")

        env = {
            key: value
            for key, value in os.environ.items()
            if key != "CLAUDECODE" and key not in MODEL_ENV_KEYS
        }
        env.update(self._request.env)
        env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=self._request.cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            diagnostic = f"连接诊断失败：无法启动 Claude CLI 探针: {exc}"
            _CONNECT_DIAGNOSTIC_CACHE[cache_key] = diagnostic
            return diagnostic

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=12)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            diagnostic = "连接诊断：使用同配置执行最小 CLI 请求时也在 12 秒内无响应。"
            _CONNECT_DIAGNOSTIC_CACHE[cache_key] = diagnostic
            return diagnostic

        stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        combined = stdout_text or stderr_text
        diagnostic = _summarize_connect_probe_output(combined, process.returncode)
        _CONNECT_DIAGNOSTIC_CACHE[cache_key] = diagnostic
        return diagnostic

    @staticmethod
    def _extract_agent_error(message: Any) -> str | None:
        if not getattr(message, "is_error", False):
            return None

        details: list[str] = []
        result = getattr(message, "result", "")
        if result and str(result).strip():
            details.append(str(result).strip())

        errors = getattr(message, "errors", ()) or ()
        details.extend(str(item).strip() for item in errors if str(item).strip())

        if not details:
            return "Agent execution failed"
        return " | ".join(dict.fromkeys(details))


class ClaudeAdapter(ModelGateway):
    """Create Claude SDK sessions behind a normalized gateway interface."""

    def __init__(self, dependencies: ClaudeSDKDependencies) -> None:
        self._dependencies = dependencies

    @staticmethod
    def uses_third_party_anthropic_provider(agent_env: dict[str, str]) -> bool:
        """Return True when the configured Anthropic endpoint is not first-party."""

        base_url = (agent_env.get("ANTHROPIC_BASE_URL") or "").strip()
        if not base_url:
            return False

        parsed = urlparse(base_url)
        host = (parsed.netloc or "").lower()
        if not host:
            return False

        return not (host.endswith("anthropic.com") or host.endswith("claude.ai"))

    @classmethod
    def build_agent_extra_args(cls, agent_env: dict[str, str]) -> dict[str, Any]:
        """Build provider-specific CLI compatibility arguments."""

        if cls.uses_third_party_anthropic_provider(agent_env):
            return {"bare": None}
        return {}

    @classmethod
    def build_sdk_child_env(cls, agent_env: dict[str, str]) -> dict[str, str]:
        """Sanitize the env passed to the Claude CLI."""

        child_env = {key: value for key, value in agent_env.items() if str(value).strip()}
        if cls.uses_third_party_anthropic_provider(agent_env):
            for key in THIRD_PARTY_MODEL_ENV_KEYS:
                child_env.pop(key, None)
        return child_env

    @classmethod
    def resolve_sdk_model(
        cls,
        agent_env: dict[str, str],
        child_env: dict[str, str],
        explicit_model: str | None,
    ) -> str | None:
        """Resolve how the selected model should be passed to Claude CLI."""

        if cls.uses_third_party_anthropic_provider(agent_env):
            model_value = str(explicit_model or "").strip()
            if model_value:
                child_env["CLAUDE_MODEL"] = model_value
            return None
        return explicit_model

    @staticmethod
    def display_agent_endpoint(agent_env: dict[str, str]) -> str:
        """Build a safe endpoint string for runtime logs."""

        endpoint = (
            agent_env.get("ANTHROPIC_BASE_URL")
            or agent_env.get("OPENAI_BASE_URL")
            or ""
        ).strip()
        return endpoint or "(sdk default)"

    @staticmethod
    def display_agent_model(agent_env: dict[str, str], explicit_model: str | None) -> str:
        """Build a safe model string for runtime logs."""

        candidates = [
            explicit_model,
            agent_env.get("ANTHROPIC_CUSTOM_MODEL_OPTION"),
            agent_env.get("CLAUDE_MODEL"),
            agent_env.get("OPENAI_MODEL"),
            agent_env.get("ANTHROPIC_DEFAULT_SONNET_MODEL"),
        ]
        for item in candidates:
            value = str(item or "").strip()
            if value:
                return value
        return "(sdk default)"

    @classmethod
    def build_request(
        cls,
        *,
        agent_env: dict[str, str],
        explicit_model: str | None,
        cwd: str,
        system_prompt: str,
        user_prompt: str,
        tools: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        max_turns: int,
        max_budget_usd: float,
        stderr_handler: Any,
        build_command: str,
    ) -> GatewayRequest:
        """Build a normalized gateway request from Claude-specific config."""

        extra_args = cls.build_agent_extra_args(agent_env)
        sdk_env = cls.build_sdk_child_env(agent_env)
        sdk_model = cls.resolve_sdk_model(agent_env, sdk_env, explicit_model)
        mode = "bare" if "bare" in extra_args else "default"

        return GatewayRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            cwd=cwd,
            tools=tools,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            env=sdk_env,
            model=sdk_model,
            extra_args=extra_args,
            metadata={
                "endpoint": cls.display_agent_endpoint(agent_env),
                "model_display": cls.display_agent_model(agent_env, explicit_model),
                "mode": mode,
                "build_command": build_command,
            },
            stderr_handler=stderr_handler,
        )

    def create_session(self, request: GatewayRequest) -> ModelGatewaySession:
        """Create a Claude SDK session for the given normalized request."""

        options = self._dependencies.options_cls(
            tools=list(request.tools),
            system_prompt=request.system_prompt,
            allowed_tools=list(request.allowed_tools),
            max_turns=request.max_turns,
            max_budget_usd=request.max_budget_usd,
            model=request.model,
            cwd=request.cwd,
            env=request.env,
            stderr=request.stderr_handler,
            extra_args=request.extra_args,
        )
        client_manager = self._dependencies.client_cls(options=options)
        return ClaudeGatewaySession(client_manager, self._dependencies, request)


def _resolve_sdk_cli_path() -> str:
    """Resolve the Claude CLI path using the same precedence as the SDK."""

    import claude_agent_sdk

    cli_name = "claude.exe" if os.name == "nt" else "claude"
    bundled_path = Path(claude_agent_sdk.__file__).resolve().parent / "_bundled" / cli_name
    if bundled_path.exists() and bundled_path.is_file():
        return str(bundled_path)

    which_path = shutil.which("claude")
    if which_path:
        return which_path

    return "claude"


def _truncate_diagnostic_text(value: str, *, max_chars: int = 240) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _summarize_connect_probe_output(output: str, return_code: int | None) -> str:
    text = _truncate_diagnostic_text(output)
    if not text:
        if return_code not in (None, 0):
            return f"连接诊断：Claude CLI 最小请求退出码为 {return_code}，但没有返回可读错误信息。"
        return "连接诊断：Claude CLI 最小请求未返回额外错误信息。"
    if "Failed to authenticate" in text or "API Error" in text:
        return f"连接诊断：{text}"
    if return_code not in (None, 0):
        return f"连接诊断：Claude CLI 最小请求失败（exit={return_code}）：{text}"
    return f"连接诊断：Claude CLI 最小请求可用，返回：{text}"


def _truncate_text(value: str, *, max_chars: int = 600) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _summarize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, dict):
        summarized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {"old_string", "new_string", "content", "command"}:
                text = str(item or "")
                summarized[f"{key_text}_preview"] = _truncate_text(text)
                summarized[f"{key_text}_length"] = len(text)
            else:
                summarized[key_text] = _summarize_value(item)
        return summarized
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        summarized_items = [_summarize_value(item) for item in items[:8]]
        if len(items) > 8:
            summarized_items.append(f"... (+{len(items) - 8} more)")
        return summarized_items
    if hasattr(value, "__dict__"):
        raw = {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
        if raw:
            return _summarize_value(raw)
    return _truncate_text(repr(value))


def _copy_tool_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _copy_tool_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_tool_value(item) for item in value]
    if isinstance(value, set):
        return [_copy_tool_value(item) for item in sorted(value, key=repr)]
    if hasattr(value, "__dict__"):
        raw = {
            str(key): _copy_tool_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
        if raw:
            return raw
    return repr(value)


def _extract_tool_payload(block: Any) -> dict[str, Any]:
    raw_input = getattr(block, "input", None)
    if isinstance(raw_input, dict):
        return {str(key): _summarize_value(value) for key, value in raw_input.items()}
    if raw_input is not None:
        return {"input": _summarize_value(raw_input)}
    return {}


def _extract_raw_tool_payload(block: Any) -> dict[str, Any]:
    raw_input = getattr(block, "input", None)
    if isinstance(raw_input, dict):
        return {str(key): _copy_tool_value(value) for key, value in raw_input.items()}
    if raw_input is not None:
        return {"input": _copy_tool_value(raw_input)}
    return {}


def _extract_trace_payload(value: Any) -> dict[str, Any]:
    candidate_keys = (
        "thinking",
        "text",
        "role",
        "id",
        "name",
        "type",
        "content",
        "result",
        "errors",
    )
    payload: dict[str, Any] = {}
    for key in candidate_keys:
        if hasattr(value, key):
            payload[key] = _summarize_value(getattr(value, key))
    if payload:
        return payload
    if hasattr(value, "__dict__"):
        raw = {
            str(key): _summarize_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
        return raw
    return {"repr": _truncate_text(repr(value))}


def _build_preview_from_payload(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    try:
        return _truncate_text(json.dumps(payload, ensure_ascii=False))
    except TypeError:
        return _truncate_text(str(payload))
