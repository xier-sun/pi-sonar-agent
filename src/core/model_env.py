"""Helpers for loading and forwarding model-related environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import dotenv

FORWARDED_MODEL_ENV_KEYS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)

STANDARD_MODEL_ALIASES = {
    "default",
    "sonnet",
    "opus",
    "haiku",
    "sonnet[1m]",
    "opus[1m]",
    "opusplan",
}


def _is_official_anthropic_host(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.netloc or "").lower()
    return host.endswith("anthropic.com") or host.endswith("claude.ai")


def _should_bridge_auth_token_to_api_key(file_values: dict[str, str]) -> bool:
    base_url = file_values.get("ANTHROPIC_BASE_URL", "").strip()
    if not base_url or _is_official_anthropic_host(base_url):
        return False
    return bool(
        file_values.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        and not file_values.get("ANTHROPIC_API_KEY", "").strip()
    )


def load_project_env(env_file: str | Path = ".env") -> None:
    """Load the project's dotenv file and let it override inherited environment values."""

    dotenv.load_dotenv(dotenv_path=env_file, override=True)


def _load_file_values(env_file: str | Path = ".env") -> dict[str, str]:
    env_path = Path(env_file)
    return {
        key: str(value).strip()
        for key, value in dotenv.dotenv_values(env_path).items()
        if value is not None and str(value).strip()
    }


def resolve_agent_model(env_file: str | Path = ".env") -> str | None:
    """Resolve the configured model from `.env`.

    We intentionally do not fall back to inherited process model variables here.
    If the project does not explicitly configure a model, the SDK should use its
    own default instead of a machine-level hidden override.
    """

    file_values = _load_file_values(env_file)
    return (
        file_values.get("ANTHROPIC_MODEL")
        or file_values.get("ANTHROPIC_CUSTOM_MODEL_OPTION")
        or file_values.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or file_values.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
        or file_values.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or file_values.get("CLAUDE_MODEL")
        or file_values.get("OPENAI_MODEL")
        or None
    )


def validate_agent_env(env_file: str | Path = ".env") -> list[str]:
    """Validate model-related environment values before starting the agent."""

    file_values = _load_file_values(env_file)
    errors: list[str] = []

    base_url = (
        file_values.get("ANTHROPIC_BASE_URL")
        or file_values.get("OPENAI_BASE_URL")
        or ""
    ).strip()
    if base_url:
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            errors.append(
                "模型 endpoint 配置无效："
                f"{base_url}，必须以 http:// 或 https:// 开头。"
            )
        else:
            parsed = urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                errors.append(f"模型 endpoint 配置无效：{base_url}")
            if "/api/coding/paas/v4" in base_url:
                errors.append(
                    "当前 ANTHROPIC_BASE_URL 指向 OpenAI 兼容 endpoint："
                    f"{base_url}。基于 Claude SDK 的运行方式请改用 Anthropic 兼容 endpoint，"
                    "例如 https://open.bigmodel.cn/api/anthropic。"
                )

    return errors


def _is_standard_model_name(model: str) -> bool:
    normalized = model.strip().lower()
    if not normalized:
        return False
    if normalized in STANDARD_MODEL_ALIASES:
        return True
    if normalized.startswith("claude-"):
        return True
    return bool(normalized.startswith("anthropic.claude-"))


def build_agent_env(env_file: str | Path = ".env") -> dict[str, str]:
    """Build the environment passed to the Claude SDK child process.

    Values from the project `.env` file take precedence over inherited process
    environment variables. When the project only provides OpenAI-style keys, we
    mirror them into Anthropic-style keys so the current Claude Code SDK flow
    can still use the same proxy credentials.
    """

    file_values = _load_file_values(env_file)

    agent_env: dict[str, str] = {}
    for key in FORWARDED_MODEL_ENV_KEYS:
        value = file_values.get(key) or os.getenv(key, "").strip()
        if value:
            agent_env[key] = value

    if _should_bridge_auth_token_to_api_key(file_values):
        agent_env["ANTHROPIC_API_KEY"] = file_values["ANTHROPIC_AUTH_TOKEN"]
        agent_env["ANTHROPIC_AUTH_TOKEN"] = ""

    # Keep the current Claude SDK path working even when users only configure
    # OpenAI-compatible proxy variables in `.env`.
    if "ANTHROPIC_API_KEY" not in file_values and file_values.get("OPENAI_API_KEY"):
        agent_env["ANTHROPIC_API_KEY"] = file_values["OPENAI_API_KEY"]
    if "ANTHROPIC_BASE_URL" not in file_values and file_values.get("OPENAI_BASE_URL"):
        agent_env["ANTHROPIC_BASE_URL"] = file_values["OPENAI_BASE_URL"]

    # Keep project-auth configuration authoritative. When the project selects
    # OAuth-style auth tokens, clear any inherited API key so the SDK child
    # process cannot silently fall back to machine-level credentials.
    if (
        "ANTHROPIC_AUTH_TOKEN" in file_values
        and "ANTHROPIC_API_KEY" not in file_values
        and not _should_bridge_auth_token_to_api_key(file_values)
    ):
        agent_env["ANTHROPIC_API_KEY"] = ""
    if "ANTHROPIC_API_KEY" in file_values and "ANTHROPIC_AUTH_TOKEN" not in file_values:
        agent_env["ANTHROPIC_AUTH_TOKEN"] = ""

    explicit_model = resolve_agent_model(env_file)
    custom_option = file_values.get("ANTHROPIC_CUSTOM_MODEL_OPTION", "").strip()
    if explicit_model and not custom_option and not _is_standard_model_name(explicit_model):
        agent_env["ANTHROPIC_CUSTOM_MODEL_OPTION"] = explicit_model
        agent_env["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = file_values.get(
            "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
            explicit_model,
        )
        agent_env["ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION"] = file_values.get(
            "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
            f"Custom model ({explicit_model})",
        )

    # Prevent hidden machine-level model selections from leaking into the SDK
    # child process. Model choice is controlled explicitly via `.env`.
    agent_env["CLAUDE_MODEL"] = file_values.get("CLAUDE_MODEL", "")
    agent_env["OPENAI_MODEL"] = file_values.get("OPENAI_MODEL", "")

    return agent_env
