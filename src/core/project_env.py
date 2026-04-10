"""Project-scoped environment helpers.

These helpers intentionally treat the repository `.env` file as the
authoritative configuration source for project-managed keys. Machine-level
environment variables must not silently override project configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import dotenv

MODEL_ENV_KEYS = (
    "ANTHROPIC_MODEL",
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
    "CLAUDE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)

PROJECT_ENV_KEYS = MODEL_ENV_KEYS + (
    "SONARQUBE_HOST",
    "SONARQUBE_TOKEN",
    "SONARQUBE_ORG",
    "ADO_BASE_URL",
    "ADO_ORG",
    "ADO_PROJECT",
    "ADO_PAT",
    "WORKSPACE_ROOT",
    "PROJECT_KEY",
    "REPOSITORY",
    "AUTHOR",
    "BUILD_COMMAND",
    "TEST_COMMAND",
    "SOLUTION_PATH",
    "MAX_ISSUES",
    "ISSUE_GUARDRAIL_MODE",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
    "DB_CONNECT_TIMEOUT",
    "DINGTALK_APPKEY",
    "DINGTALK_APPSECRET",
    "DINGTALK_AGENTID",
    "DINGTALK_WEBHOOK",
    "DINGTALK_SECRET",
    "PI_SONAR_ENABLE_CONTROLLED_BASH",
    "PI_SONAR_PERF_SHORT_FORM_PROMPT",
    "PI_SONAR_PERF_FAST_PATH",
    "PI_SONAR_PERF_PLAN_FIRST_COMPLEX_RULES",
    "PI_SONAR_PERF_LAYERED_VERIFICATION",
    "PI_SONAR_PERF_PATCH_SALVAGE",
    "PI_SONAR_PERF_CONTINUATION_RETRY",
    "PI_SONAR_PERF_FAST_PATH_MAX_TURNS",
    "PI_SONAR_PERF_CONTINUATION_RETRY_LIMIT",
)


def read_project_env(env_file: str | Path = ".env") -> dict[str, str]:
    """Read normalized values from the repository `.env` file only."""

    env_path = Path(env_file)
    return {
        key: str(value).strip()
        for key, value in dotenv.dotenv_values(env_path).items()
        if value is not None and str(value).strip()
    }


def load_project_env(env_file: str | Path = ".env") -> dict[str, str]:
    """Load the repository `.env` and clear managed machine-env fallbacks."""

    file_values = read_project_env(env_file)
    dotenv.load_dotenv(dotenv_path=env_file, override=True)

    # Prevent project-managed keys from silently falling back to machine-level
    # values when the repository `.env` intentionally omits them.
    for key in PROJECT_ENV_KEYS:
        if key not in file_values:
            os.environ.pop(key, None)

    return file_values


def resolve_project_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_file: str | Path = ".env",
) -> Mapping[str, str]:
    """Return the explicit env mapping or the repository `.env` values."""

    if environ is not None:
        return environ
    return read_project_env(env_file)
