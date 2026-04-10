"""Controlled shell helpers for the issue-fix runtime."""

from __future__ import annotations

from collections.abc import Iterable

from pi_sonar_agent.core.project_env import read_project_env

BASE_BUILTIN_FIX_TOOLS = ("Read", "Edit")
CONTROLLED_BASH_TOOL = "Bash"
CONTROLLED_SHELL_DISPLAY_NAME = "Bash"
FINISH_TOOL = "Finish"


def _env_flag(name: str, default: bool) -> bool:
    raw_value = read_project_env().get(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def controlled_bash_enabled() -> bool:
    """Return True when the controlled shell tool should be exposed."""

    return _env_flag("PI_SONAR_ENABLE_CONTROLLED_BASH", True)


def build_fix_runtime_tools() -> tuple[str, ...]:
    """Return the runtime builtin tool list for issue fixing."""

    tools = list(BASE_BUILTIN_FIX_TOOLS)
    if controlled_bash_enabled():
        tools.append(CONTROLLED_BASH_TOOL)
    return tuple(tools)


def build_allowed_fix_tool_rules(
    base_allowed_tools: Iterable[str],
    *,
    include_controlled_bash: bool | None = None,
) -> tuple[str, ...]:
    """Return exact tool names for the SDK allowlist."""

    enabled = controlled_bash_enabled() if include_controlled_bash is None else include_controlled_bash
    allowed = [str(name) for name in base_allowed_tools if str(name).strip()]
    if enabled:
        allowed.append(CONTROLLED_BASH_TOOL)
    if FINISH_TOOL not in allowed:
        allowed.append(FINISH_TOOL)
    return tuple(dict.fromkeys(allowed))


def render_controlled_bash_prompt_constraints(*, enabled: bool | None = None) -> tuple[str, ...]:
    """Render prompt constraints describing how the shell may be used safely."""

    use_bash = controlled_bash_enabled() if enabled is None else enabled
    if not use_bash:
        return ()

    return (
        "如果使用 shell 工具（工具名 Bash），请只写 bash 兼容命令；不要写 PowerShell 或 CMD 语法。",
        "允许使用 Bash 做搜索、查看、诊断、echo 等无害操作。",
        "严禁用 shell 删除文件、创建文件、覆盖文件或通过 shell 直接改写源文件；代码落盘修改仍使用 Edit。",
    )
