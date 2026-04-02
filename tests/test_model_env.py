from __future__ import annotations

import os

from claude_agent_sdk import ResultMessage
from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent
from pi_sonar_agent.core.model_env import (
    build_agent_env,
    load_project_env,
    resolve_agent_model,
    validate_agent_env,
)


def test_load_project_env_overrides_existing_values(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "from-process")

    load_project_env(env_file)

    assert os.getenv("OPENAI_API_KEY") == "from-dotenv"


def test_build_agent_env_prefers_dotenv_and_maps_openai_values(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=dotenv-openai-key",
                "OPENAI_BASE_URL=https://proxy.example/v1",
                "OPENAI_MODEL=glm-4.7",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "process-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "process-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    agent_env = build_agent_env(env_file)

    assert agent_env["OPENAI_API_KEY"] == "dotenv-openai-key"
    assert agent_env["OPENAI_BASE_URL"] == "https://proxy.example/v1"
    assert agent_env["ANTHROPIC_API_KEY"] == "dotenv-openai-key"
    assert agent_env["ANTHROPIC_BASE_URL"] == "https://proxy.example/v1"
    assert agent_env["CLAUDE_MODEL"] == ""
    assert agent_env["OPENAI_MODEL"] == "glm-4.7"
    assert agent_env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == "glm-4.7"
    assert agent_env["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] == "glm-4.7"


def test_resolve_agent_model_uses_only_dotenv_values(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_MODEL=sonnet\n", encoding="utf-8")

    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    assert resolve_agent_model(env_file) == "sonnet"


def test_resolve_agent_model_ignores_process_when_dotenv_has_no_model(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=dotenv-openai-key\n", encoding="utf-8")

    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    assert resolve_agent_model(env_file) is None


def test_resolve_agent_model_supports_anthropic_default_sonnet(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_DEFAULT_SONNET_MODEL=glm-4.7\n", encoding="utf-8")

    assert resolve_agent_model(env_file) == "glm-4.7"


def test_standard_claude_model_does_not_register_custom_option(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CLAUDE_MODEL=sonnet\n", encoding="utf-8")

    agent_env = build_agent_env(env_file)

    assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in agent_env


def test_auth_token_clears_inherited_api_key(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ANTHROPIC_AUTH_TOKEN=dotenv-token",
                "ANTHROPIC_BASE_URL=https://api.anthropic.com",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "machine-api-key")

    agent_env = build_agent_env(env_file)

    assert agent_env["ANTHROPIC_AUTH_TOKEN"] == "dotenv-token"
    assert agent_env["ANTHROPIC_API_KEY"] == ""


def test_validate_agent_env_rejects_invalid_base_url(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_BASE_URL=ttps://proxy.example/anthropic\n", encoding="utf-8")

    errors = validate_agent_env(env_file)

    assert errors == [
        "模型 endpoint 配置无效：ttps://proxy.example/anthropic，必须以 http:// 或 https:// 开头。"
    ]


def test_validate_agent_env_rejects_openai_compatible_endpoint_for_anthropic_base_url(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4\n",
        encoding="utf-8",
    )

    errors = validate_agent_env(env_file)

    assert errors == [
        "当前 ANTHROPIC_BASE_URL 指向 OpenAI 兼容 endpoint：https://open.bigmodel.cn/api/coding/paas/v4。基于 Claude SDK 的运行方式请改用 Anthropic 兼容 endpoint，例如 https://open.bigmodel.cn/api/anthropic。"
    ]


def test_build_agent_env_bridges_auth_token_for_custom_anthropic_provider(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic",
                "ANTHROPIC_AUTH_TOKEN=provider-token",
            ]
        ),
        encoding="utf-8",
    )

    agent_env = build_agent_env(env_file)

    assert agent_env["ANTHROPIC_API_KEY"] == "provider-token"
    assert agent_env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_extract_agent_error_uses_result_message_fields() -> None:
    message = ResultMessage(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=True,
        num_turns=1,
        session_id="session-1",
        result="Failed to authenticate",
        errors=["Failed to authenticate", "quota exhausted"],
    )

    assert (
        ClaudeFixAgent._extract_agent_error(message) == "Failed to authenticate | quota exhausted"
    )
