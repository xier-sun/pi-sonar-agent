"""Controlled shell helpers for the issue-fix runtime."""

from __future__ import annotations

from collections.abc import Iterable

from pi_sonar_agent.core.project_env import read_project_env

BASE_BUILTIN_FIX_TOOLS = ("Read", "Edit", "MultiEdit")
CREATE_FILE_TOOL = "Write"
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


def build_fix_runtime_tools(*, include_create_file_tool: bool = False) -> tuple[str, ...]:
    """Return the runtime builtin tool list for issue fixing."""

    tools = list(BASE_BUILTIN_FIX_TOOLS)
    if include_create_file_tool:
        tools.append(CREATE_FILE_TOOL)
    if controlled_bash_enabled():
        tools.append(CONTROLLED_BASH_TOOL)
    return tuple(tools)


def render_visible_tool_summary(visible_tools: Iterable[str]) -> str:
    """Render a stable, deduplicated tool list for prompt/log display."""

    normalized = tuple(
        dict.fromkeys(str(name).strip() for name in visible_tools if str(name).strip())
    )
    return ", ".join(normalized)


def build_allowed_fix_tool_rules(
    base_allowed_tools: Iterable[str],
    *,
    include_controlled_bash: bool | None = None,
    bash_file_creation_roots: Iterable[str] = (),
    create_file_tool_roots: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return exact tool names for the SDK allowlist."""

    enabled = controlled_bash_enabled() if include_controlled_bash is None else include_controlled_bash
    allowed = [str(name) for name in base_allowed_tools if str(name).strip()]
    create_roots = tuple(
        dict.fromkeys(
            str(root or "").replace("\\", "/").strip().strip("/")
            for root in create_file_tool_roots
            if str(root or "").strip().strip("/")
        )
    )
    if create_roots and CREATE_FILE_TOOL not in allowed:
        allowed.append(CREATE_FILE_TOOL)
    for root in create_roots:
        allowed.append(f"{CREATE_FILE_TOOL}(create_file_under={root})")
    if enabled:
        allowed.append(CONTROLLED_BASH_TOOL)
        for root in bash_file_creation_roots:
            normalized = str(root or "").replace("\\", "/").strip().strip("/")
            if normalized:
                allowed.append(f"{CONTROLLED_BASH_TOOL}(create_file_under={normalized})")
    if FINISH_TOOL not in allowed:
        allowed.append(FINISH_TOOL)
    return tuple(dict.fromkeys(allowed))


def render_controlled_bash_prompt_constraints(
    *,
    enabled: bool | None = None,
    allow_file_creation: bool = False,
    allow_build_commands: bool = True,
    allowed_new_file_roots: Iterable[str] = (),
) -> tuple[str, ...]:
    """Render prompt constraints describing how the shell may be used safely."""

    use_bash = controlled_bash_enabled() if enabled is None else enabled
    if not use_bash:
        return ()

    normalized_roots = tuple(
        dict.fromkeys(
            str(root or "").replace("\\", "/").strip().strip("/")
            for root in allowed_new_file_roots
            if str(root or "").strip().strip("/")
        )
    )
    constraints = [
        "如果使用 shell 工具（工具名 Bash），请只写 bash 兼容命令；不要写 PowerShell 或 CMD 语法。",
        "允许使用 Bash 做搜索、查看、诊断、echo 等无害操作。",
        "如果路径不确定，优先使用 prompt 中给出的仓库相对路径候选；不要靠手工拼接仓库根目录反复试错。",
        "优先使用单条无副作用的诊断命令；不要把 Bash 当成源码编辑器。",
    ]
    if not allow_build_commands:
        constraints.append(
            "不要在 Bash 中执行 dotnet restore/build/test、msbuild 或 nuget restore；构建与验证由外层流程统一执行。"
        )
    if allow_file_creation and normalized_roots:
        constraints.append(
            "如果需要创建尚不存在的新文件，优先使用 Write 工具；Write 仅用于第一次创建新文件，不要用来重写已有文件。"
        )
        constraints.append(
            "当前 attempt 允许用 Bash 新建文件，但仅限 bash 兼容创建命令，且新文件必须落在以下目录内："
            + ", ".join(normalized_roots)
            + "。"
        )
        constraints.append(
            "除已声明的新文件创建外，仍严禁用 shell 删除文件、覆盖已有文件、移动/重命名文件或直接改写已有源码；已有文件修改仍使用 Edit。"
        )
    else:
        constraints.append(
            "严禁用 shell 删除文件、创建文件、覆盖文件或通过 shell 直接改写源文件；已有文件修改仍使用 Edit。"
        )
    return tuple(constraints)
