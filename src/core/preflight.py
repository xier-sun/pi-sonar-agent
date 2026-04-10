"""Shared runtime preflight validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pi_sonar_agent.core.git_gateway import GitRepositoryGateway
from pi_sonar_agent.core.model_env import validate_agent_env
from pi_sonar_agent.core.project_env import resolve_project_env


@dataclass(frozen=True)
class RuntimeEnvironment:
    """Resolved runtime environment for a run entrypoint."""

    sonar_host: str
    sonar_token: str
    sonar_org: str | None
    ado_base_url: str
    ado_org: str | None
    ado_project: str
    ado_pat: str
    workspace_root: Path


def load_runtime_environment(
    *,
    environ: Mapping[str, str] | None = None,
    default_workspace_root: str = ".agent_workspaces",
    validate_model_environment: bool = True,
) -> RuntimeEnvironment:
    """Validate and resolve the environment needed to run the agent."""

    env = resolve_project_env(environ)

    if validate_model_environment:
        model_env_errors = validate_agent_env()
        if model_env_errors:
            raise RuntimeError("模型配置无效:\n- " + "\n- ".join(model_env_errors))

    return RuntimeEnvironment(
        sonar_host=require_env("SONARQUBE_HOST", env),
        sonar_token=require_env("SONARQUBE_TOKEN", env),
        sonar_org=_none_if_empty(env.get("SONARQUBE_ORG")),
        ado_base_url=require_env("ADO_BASE_URL", env),
        ado_org=_none_if_empty(env.get("ADO_ORG")),
        ado_project=require_env("ADO_PROJECT", env),
        ado_pat=require_env("ADO_PAT", env),
        workspace_root=Path(_text_value(env.get("WORKSPACE_ROOT")) or default_workspace_root),
    )


def require_env(name: str, environ: Mapping[str, str] | None = None) -> str:
    """Get a required environment variable with a user-friendly error."""

    env = resolve_project_env(environ)
    value = _text_value(env.get(name))
    if not value:
        raise RuntimeError(f"缺少环境变量: {name}")
    return value


def ensure_workspace_writable(workspace_root: Path) -> None:
    """Ensure the workspace root can be created and written to."""

    probe_path = workspace_root / ".pi-sonar-agent-write-check"
    try:
        workspace_root.mkdir(parents=True, exist_ok=True)
        probe_path.write_text("ok", encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"WORKSPACE_ROOT 不可写: {workspace_root} ({exc})") from exc
    finally:
        try:
            if probe_path.exists():
                probe_path.unlink()
        except Exception:
            pass


def ensure_remote_branch_exists(
    *,
    remote_url: str,
    branch: str,
    pat: str,
    git_gateway: GitRepositoryGateway | None = None,
) -> None:
    """Ensure the configured base branch exists on the remote repository."""

    gateway = git_gateway or GitRepositoryGateway(remote_url=remote_url, pat=pat)
    exists = gateway.branch_exists(branch)
    if not exists:
        raise RuntimeError(f"远端基线分支不存在: {branch} ({gateway.redacted_remote_url})")


def _text_value(value: str | None) -> str:
    return str(value or "").strip()


def _none_if_empty(value: str | None) -> str | None:
    normalized = _text_value(value)
    return normalized or None
