"""Claude SDK implementation of the normalized model gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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

    def __init__(self, client_manager: Any, dependencies: ClaudeSDKDependencies) -> None:
        self._dependencies = dependencies
        self._controller = _ClaudeSDKSessionController(client_manager)
        self._client: Any | None = None

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
                    for block in message.content:
                        if isinstance(block, self._dependencies.tool_use_block_cls):
                            yield ToolCallEvent(name=block.name)
                        elif isinstance(block, self._dependencies.text_block_cls) and block.text.strip():
                            yield TextEvent(text=block.text)
                        else:
                            yield TraceEvent(message_type=type(block).__name__)
                elif isinstance(message, self._dependencies.result_message_cls):
                    yield ResultEvent(
                        total_cost_usd=float(message.total_cost_usd or 0.0),
                        agent_error=self._extract_agent_error(message),
                    )
                else:
                    yield TraceEvent(message_type=type(message).__name__)

        return iterate()

    async def abort(self, reason: str) -> GatewayAbortResult:
        return await self._controller.abort(reason)

    async def close(self) -> GatewayAbortResult:
        return await self._controller.close()

    @staticmethod
    def _extract_agent_error(message: Any) -> str | None:
        if not getattr(message, "is_error", False):
            return None

        details: list[str] = []
        result = getattr(message, "result", "")
        if result and str(result).strip():
            details.append(str(result).strip())

        errors = getattr(message, "errors", ())
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
        return ClaudeGatewaySession(client_manager, self._dependencies)
