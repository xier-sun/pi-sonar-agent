"""Helpers for loading and forwarding model-related environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pi_sonar_agent.core.project_env import BASE_MODEL_ENV_KEYS, read_project_env
from pi_sonar_agent.core.project_env import load_project_env as _load_project_env

FORWARDED_MODEL_ENV_KEYS = BASE_MODEL_ENV_KEYS

STANDARD_MODEL_ALIASES = {
    "default",
    "sonnet",
    "opus",
    "haiku",
    "sonnet[1m]",
    "opus[1m]",
    "opusplan",
}

TIER1_PREFIX = "PI_SONAR_MODEL_TIER1_"
TIER2_PREFIX = "PI_SONAR_MODEL_TIER2_"


@dataclass(frozen=True)
class ModelTierConfig:
    """Resolved environment + explicit model for one model tier."""

    tier_name: str
    explicit_model: str | None
    agent_env: dict[str, str]
    configured: bool = False
    source: str = ""

    @property
    def display_name(self) -> str:
        return str(self.explicit_model or self.tier_name).strip() or self.tier_name


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

    _load_project_env(env_file)
    return None


def _load_file_values(env_file: str | Path = ".env") -> dict[str, str]:
    return read_project_env(Path(env_file))


def _extract_tier_values(
    file_values: dict[str, str],
    *,
    prefix: str,
) -> tuple[dict[str, str], bool]:
    """Map prefixed tier variables back to the base model env keys."""

    tier_values: dict[str, str] = {}
    configured = False
    for key in BASE_MODEL_ENV_KEYS:
        prefixed_key = f"{prefix}{key}"
        value = str(file_values.get(prefixed_key, "")).strip()
        if value:
            tier_values[key] = value
            configured = True
    return tier_values, configured


def _resolve_agent_model_from_values(file_values: dict[str, str]) -> str | None:
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


def _is_standard_model_name(model: str) -> bool:
    normalized = model.strip().lower()
    if not normalized:
        return False
    if normalized in STANDARD_MODEL_ALIASES:
        return True
    if normalized.startswith("claude-"):
        return True
    return bool(normalized.startswith("anthropic.claude-"))


def _build_agent_env_from_values(file_values: dict[str, str]) -> dict[str, str]:
    agent_env: dict[str, str] = {}
    for key in FORWARDED_MODEL_ENV_KEYS:
        value = file_values.get(key, "").strip()
        if value:
            agent_env[key] = value

    if _should_bridge_auth_token_to_api_key(file_values):
        agent_env["ANTHROPIC_API_KEY"] = file_values["ANTHROPIC_AUTH_TOKEN"]
        agent_env["ANTHROPIC_AUTH_TOKEN"] = ""

    if "ANTHROPIC_API_KEY" not in file_values and file_values.get("OPENAI_API_KEY"):
        agent_env["ANTHROPIC_API_KEY"] = file_values["OPENAI_API_KEY"]
    if "ANTHROPIC_BASE_URL" not in file_values and file_values.get("OPENAI_BASE_URL"):
        agent_env["ANTHROPIC_BASE_URL"] = file_values["OPENAI_BASE_URL"]

    if (
        "ANTHROPIC_AUTH_TOKEN" in file_values
        and "ANTHROPIC_API_KEY" not in file_values
        and not _should_bridge_auth_token_to_api_key(file_values)
    ):
        agent_env["ANTHROPIC_API_KEY"] = ""
    if "ANTHROPIC_API_KEY" in file_values and "ANTHROPIC_AUTH_TOKEN" not in file_values:
        agent_env["ANTHROPIC_AUTH_TOKEN"] = ""

    explicit_model = _resolve_agent_model_from_values(file_values)
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


def _default_model_values(file_values: dict[str, str]) -> dict[str, str]:
    tier1_values, tier1_configured = _extract_tier_values(file_values, prefix=TIER1_PREFIX)
    return tier1_values if tier1_configured else file_values


def resolve_model_tiers(env_file: str | Path = ".env") -> dict[str, ModelTierConfig]:
    """Resolve the two-tier model ladder from `.env`.

    `tier1` is the default issue model. When explicit tier1 variables are not
    configured, we fall back to the repository's existing flat model settings so
    the project remains backward compatible.
    """

    file_values = _load_file_values(env_file)
    tier1_values, tier1_configured = _extract_tier_values(file_values, prefix=TIER1_PREFIX)
    tier2_values, tier2_configured = _extract_tier_values(file_values, prefix=TIER2_PREFIX)

    effective_tier1_values = tier1_values if tier1_configured else file_values
    tiers = {
        "tier1": ModelTierConfig(
            tier_name="tier1",
            explicit_model=_resolve_agent_model_from_values(effective_tier1_values),
            agent_env=_build_agent_env_from_values(effective_tier1_values),
            configured=True,
            source="tier1_prefixed" if tier1_configured else "default_env",
        ),
        "tier2": ModelTierConfig(
            tier_name="tier2",
            explicit_model=_resolve_agent_model_from_values(tier2_values) if tier2_configured else None,
            agent_env=_build_agent_env_from_values(tier2_values) if tier2_configured else {},
            configured=tier2_configured,
            source="tier2_prefixed" if tier2_configured else "",
        ),
    }
    return tiers


def build_issue_model_route(
    env_file: str | Path = ".env",
    *,
    second_pass: bool = False,
) -> tuple[ModelTierConfig, ...]:
    """Return the per-issue tier order for the requested pass."""

    tiers = resolve_model_tiers(env_file)
    ordered_names = ("tier2", "tier1") if second_pass else ("tier1", "tier2")
    ordered_tiers: list[ModelTierConfig] = []
    seen_signatures: set[tuple[str | None, tuple[tuple[str, str], ...]]] = set()
    for tier_name in ordered_names:
        tier = tiers[tier_name]
        if not tier.configured:
            continue
        signature = (
            tier.explicit_model,
            tuple(sorted((key, value) for key, value in tier.agent_env.items())),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        ordered_tiers.append(tier)
    return tuple(ordered_tiers)


def second_pass_enabled(env_file: str | Path = ".env") -> bool:
    file_values = _load_file_values(env_file)
    raw_value = str(file_values.get("PI_SONAR_SECOND_PASS_ENABLED", "true")).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def abort_publish_enabled(env_file: str | Path = ".env") -> bool:
    file_values = _load_file_values(env_file)
    raw_value = str(file_values.get("PI_SONAR_ABORT_PUBLISH_ENABLED", "true")).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def resolve_agent_model(env_file: str | Path = ".env") -> str | None:
    """Resolve the configured default model from `.env`.

    We intentionally do not fall back to inherited process model variables here.
    If the project does not explicitly configure a model, the SDK should use its
    own default instead of a machine-level hidden override.
    """

    file_values = _load_file_values(env_file)
    return _resolve_agent_model_from_values(_default_model_values(file_values))


def _validate_agent_values(file_values: dict[str, str], *, label: str = "") -> list[str]:
    errors: list[str] = []
    base_url = (
        file_values.get("ANTHROPIC_BASE_URL")
        or file_values.get("OPENAI_BASE_URL")
        or ""
    ).strip()
    if not base_url:
        return errors

    prefix = f"{label} " if label else ""
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        errors.append(
            f"{prefix}模型 endpoint 配置无效：{base_url}，必须以 http:// 或 https:// 开头。"
        )
        return errors

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        errors.append(f"{prefix}模型 endpoint 配置无效：{base_url}")
    if "/api/coding/paas/v4" in base_url:
        errors.append(
            f"{prefix}当前 ANTHROPIC_BASE_URL 指向 OpenAI 兼容 endpoint："
            f"{base_url}。基于 Claude SDK 的运行方式请改用 Anthropic 兼容 endpoint，"
            "例如 https://open.bigmodel.cn/api/anthropic。"
        )
    return errors


def validate_agent_env(env_file: str | Path = ".env") -> list[str]:
    """Validate model-related environment values before starting the agent."""

    file_values = _load_file_values(env_file)
    errors = _validate_agent_values(_default_model_values(file_values))

    tier2_values, tier2_configured = _extract_tier_values(file_values, prefix=TIER2_PREFIX)
    if tier2_configured:
        errors.extend(_validate_agent_values(tier2_values, label="tier2"))
    return errors


def build_agent_env(env_file: str | Path = ".env") -> dict[str, str]:
    """Build the environment passed to the Claude SDK child process.

    Values from the project `.env` file take precedence over inherited process
    environment variables. When the project configures tier1 explicitly, that
    tier becomes the default model environment for all legacy call sites.
    """

    file_values = _load_file_values(env_file)
    return _build_agent_env_from_values(_default_model_values(file_values))
