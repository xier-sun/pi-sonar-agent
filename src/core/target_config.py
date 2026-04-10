"""Target configuration resolution helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.git_gateway import resolve_base_branch
from pi_sonar_agent.core.project_env import resolve_project_env


@dataclass(frozen=True)
class TargetConfig:
    """Resolved target configuration used by run entrypoints."""

    project_key: str
    repository: str
    author: str
    reviewer_email: str
    dingtalk_userid: str
    base_branch: str
    base_branch_source: str
    build_command: str | None
    test_command: str | None
    solution_path: str | None
    max_issues: int


def resolve_cli_target_config(
    args: Any,
    target_defaults: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    default_base_branch: str = "develop",
    default_max_issues: int = 0,
) -> TargetConfig:
    """Resolve target config for the single-target CLI entrypoint."""

    env = resolve_project_env(environ)
    base_branch_selection = resolve_base_branch(
        cli_branch=_arg_value(args, "base_branch"),
        configured_branch=_text_value(target_defaults.get("base_branch")),
        default_branch=default_base_branch,
    )

    max_issues_arg = getattr(args, "max_issues", None)
    if max_issues_arg is None:
        raw_max_issues = _text_value(env.get("MAX_ISSUES")) or _text_value(target_defaults.get("max_issues"))
        max_issues = int(raw_max_issues) if raw_max_issues else default_max_issues
    else:
        max_issues = int(max_issues_arg)

    return TargetConfig(
        project_key=(
            _arg_value(args, "project_key")
            or _text_value(env.get("PROJECT_KEY"))
            or _text_value(target_defaults.get("project_key"))
        ),
        repository=(
            _arg_value(args, "repository")
            or _text_value(env.get("REPOSITORY"))
            or _text_value(target_defaults.get("repository"))
        ),
        author=(
            _arg_value(args, "author")
            or _text_value(env.get("AUTHOR"))
            or _text_value(target_defaults.get("author"))
        ),
        reviewer_email=_text_value(target_defaults.get("reviewer_email")),
        dingtalk_userid=_text_value(target_defaults.get("dingtalk_userid")),
        base_branch=base_branch_selection.branch,
        base_branch_source=base_branch_selection.source,
        build_command=(
            _arg_value(args, "build_command")
            or _text_value(env.get("BUILD_COMMAND"))
            or _text_value(target_defaults.get("build_command"))
            or "dotnet build"
        ),
        test_command=_none_if_empty(
            _arg_value(args, "test_command")
            or _text_value(env.get("TEST_COMMAND"))
            or _text_value(target_defaults.get("test_command"))
        ),
        solution_path=_none_if_empty(
            _arg_value(args, "solution_path")
            or _text_value(env.get("SOLUTION_PATH"))
            or _text_value(target_defaults.get("solution_path"))
        ),
        max_issues=max_issues,
    )


def resolve_batch_target_config(
    target: Mapping[str, Any],
    *,
    default_base_branch: str = "develop",
    default_max_issues: int = 3,
) -> TargetConfig:
    """Resolve target config for the batch entrypoint."""

    base_branch_selection = resolve_base_branch(
        configured_branch=_text_value(target.get("base_branch")),
        default_branch=default_base_branch,
    )

    raw_max_issues = target.get("max_issues", default_max_issues)
    max_issues = int(raw_max_issues)

    return TargetConfig(
        project_key=_text_value(target.get("project_key")),
        repository=_text_value(target.get("repository")),
        author=_text_value(target.get("author")),
        reviewer_email=_text_value(target.get("reviewer_email")),
        dingtalk_userid=_text_value(target.get("dingtalk_userid")),
        base_branch=base_branch_selection.branch,
        base_branch_source=base_branch_selection.source,
        build_command=_none_if_empty(_text_value(target.get("build_command"))),
        test_command=_none_if_empty(_text_value(target.get("test_command"))),
        solution_path=_none_if_empty(_text_value(target.get("solution_path"))),
        max_issues=max_issues,
    )


def missing_required_target_fields(config: TargetConfig) -> list[str]:
    """Return required field names that are still missing."""

    missing: list[str] = []
    if not config.project_key:
        missing.append("project_key")
    if not config.repository:
        missing.append("repository")
    if not config.author:
        missing.append("author")
    return missing


def _arg_value(args: Any, name: str) -> str:
    return _text_value(getattr(args, name, ""))


def _text_value(value: Any) -> str:
    return str(value or "").strip()


def _none_if_empty(value: str) -> str | None:
    return value or None
