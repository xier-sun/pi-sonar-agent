"""Tool registration helpers for issue-fix runtime policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from pi_sonar_agent.core.state import serialize_state
from pi_sonar_agent.core.tool_surface import (
    CONTROLLED_BASH_TOOL,
    FINISH_TOOL,
    build_allowed_fix_tool_rules,
    controlled_bash_enabled,
)

READ_ONLY_FIX_TOOLS = frozenset({"Read", "Grep", "Glob"})
WRITE_FIX_TOOLS = frozenset({"Edit", "MultiEdit", "Write"})
CONTROLLED_FIX_TOOLS = frozenset({"Bash", "Finish"})
BUILD_TOOL_NAMES = frozenset({"mcp__sonar-fix__run_build"})
READ_ONLY_MCP_MARKERS = frozenset({"issue", "rule", "source", "quality", "analysis", "project"})
MUTATING_MCP_MARKERS = frozenset({"git_", "commit", "push", "add", "write", "create", "update", "delete", "run_build"})


class ToolKind(str, Enum):
    """Normalized tool kinds used by the fix runtime."""

    READ_ONLY = "read_only"
    WRITE = "write"
    CONTROLLED = "controlled"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ToolSpec:
    """Metadata describing a single runtime-visible tool."""

    name: str
    kind: ToolKind
    tags: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class VisibleToolset:
    """Single source of truth for runtime-visible tools and why others are hidden."""

    runtime_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    visible_tools: tuple[str, ...]
    hidden_tools: tuple[str, ...]
    disabled_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return serialize_state(self)


class ToolRegistry:
    """Central registry for tool metadata and classification."""

    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> ToolSpec | None:
        """Return the spec for a tool when known."""

        return self._specs.get(name)

    def names(
        self,
        *,
        include_kinds: set[ToolKind] | None = None,
        exclude_kinds: set[ToolKind] | None = None,
    ) -> tuple[str, ...]:
        """Return tool names filtered by tool kind when requested."""

        include = include_kinds or set(ToolKind)
        exclude = exclude_kinds or set()
        return tuple(
            spec.name
            for spec in self._specs.values()
            if spec.kind in include and spec.kind not in exclude
        )


def _canonical_tool_name(name: str) -> str:
    tool_name = str(name or "").strip()
    if not tool_name:
        return ""
    if tool_name.startswith(f"{CONTROLLED_BASH_TOOL}("):
        return CONTROLLED_BASH_TOOL
    return tool_name


def build_fix_tool_registry(
    builtin_tools: Iterable[str],
    mcp_tools: Iterable[str],
    forbidden_tools: Iterable[str],
) -> ToolRegistry:
    """Build the default tool registry for single-issue fix attempts."""

    specs: list[ToolSpec] = []
    seen: set[str] = set()

    def register(spec: ToolSpec) -> None:
        if spec.name in seen:
            return
        specs.append(spec)
        seen.add(spec.name)

    for tool_name in builtin_tools:
        if tool_name in READ_ONLY_FIX_TOOLS:
            register(ToolSpec(name=tool_name, kind=ToolKind.READ_ONLY))
        elif tool_name in WRITE_FIX_TOOLS:
            register(ToolSpec(name=tool_name, kind=ToolKind.WRITE))
        elif tool_name in CONTROLLED_FIX_TOOLS:
            tags: tuple[str, ...] = ()
            description = ""
            if tool_name == "Bash":
                tags = ("shell", "scoped")
                description = "Bash-compatible shell tool constrained by runtime command policy."
            elif tool_name == "Finish":
                tags = ("finish",)
                description = "Terminal completion marker for the current issue attempt."
            register(
                ToolSpec(
                    name=tool_name,
                    kind=ToolKind.CONTROLLED,
                    tags=tags,
                    description=description,
                )
            )
        else:
            register(ToolSpec(name=tool_name, kind=ToolKind.UNKNOWN))

    for tool_name in mcp_tools:
        lowered = str(tool_name).lower()
        if any(marker in lowered for marker in MUTATING_MCP_MARKERS):
            kind = ToolKind.WRITE
        elif any(marker in lowered for marker in READ_ONLY_MCP_MARKERS):
            kind = ToolKind.READ_ONLY
        else:
            kind = ToolKind.UNKNOWN
        register(ToolSpec(name=tool_name, kind=kind, tags=("mcp",)))

    for tool_name in BUILD_TOOL_NAMES:
        register(
            ToolSpec(
                name=tool_name,
                kind=ToolKind.CONTROLLED,
                tags=("build",),
                description="Build/test tool managed by the outer workflow.",
            )
        )

    register(
        ToolSpec(
            name=FINISH_TOOL,
            kind=ToolKind.CONTROLLED,
            tags=("finish",),
            description="Terminal completion marker for the current issue attempt.",
        )
    )

    for tool_name in forbidden_tools:
        tags: tuple[str, ...] = ()
        if tool_name == "Bash":
            tags = ("shell",)
        elif "git_" in tool_name:
            tags = ("git",)
        register(ToolSpec(name=tool_name, kind=ToolKind.FORBIDDEN, tags=tags))

    return ToolRegistry(specs)


def build_visible_toolset(
    registry: ToolRegistry,
    base_allowed_tools: Iterable[str],
    *,
    include_controlled_bash: bool | None = None,
    bash_file_creation_roots: Iterable[str] = (),
    create_file_tool_roots: Iterable[str] = (),
) -> VisibleToolset:
    """Build the canonical tool surface used by prompt, policy, and logs."""

    enabled = controlled_bash_enabled() if include_controlled_bash is None else include_controlled_bash
    allowed_tools = build_allowed_fix_tool_rules(
        base_allowed_tools,
        include_controlled_bash=enabled,
        bash_file_creation_roots=bash_file_creation_roots,
        create_file_tool_roots=create_file_tool_roots,
    )
    visible_tools = tuple(
        dict.fromkeys(
            normalized
            for normalized in (_canonical_tool_name(name) for name in allowed_tools)
            if normalized
        )
    )
    runtime_tools = registry.names(exclude_kinds={ToolKind.FORBIDDEN})
    hidden_tools: list[str] = []
    disabled_reasons: dict[str, str] = {}
    visible_lookup = set(visible_tools)
    for tool_name in runtime_tools:
        if tool_name in visible_lookup:
            continue
        hidden_tools.append(tool_name)
        spec = registry.get(tool_name)
        if tool_name == CONTROLLED_BASH_TOOL and not enabled:
            reason = "controlled_bash_disabled"
        elif tool_name.startswith("mcp__"):
            reason = "mcp_tool_not_visible"
        elif spec is not None and spec.kind == ToolKind.FORBIDDEN:
            reason = "forbidden"
        elif spec is not None and spec.kind == ToolKind.UNKNOWN:
            reason = "unknown_tool_not_visible"
        else:
            reason = "not_visible"
        disabled_reasons[tool_name] = reason
    return VisibleToolset(
        runtime_tools=runtime_tools,
        allowed_tools=allowed_tools,
        visible_tools=visible_tools,
        hidden_tools=tuple(hidden_tools),
        disabled_reasons=disabled_reasons,
    )
