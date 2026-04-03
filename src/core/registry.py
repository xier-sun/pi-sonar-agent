"""Tool registration helpers for issue-fix runtime policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

READ_ONLY_FIX_TOOLS = frozenset({"Read", "Grep", "Glob"})
WRITE_FIX_TOOLS = frozenset({"Edit", "MultiEdit", "Write"})
BUILD_TOOL_NAMES = frozenset({"mcp__sonar-fix__run_build"})


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
        else:
            register(ToolSpec(name=tool_name, kind=ToolKind.UNKNOWN))

    for tool_name in mcp_tools:
        register(ToolSpec(name=tool_name, kind=ToolKind.WRITE))

    for tool_name in BUILD_TOOL_NAMES:
        register(
            ToolSpec(
                name=tool_name,
                kind=ToolKind.CONTROLLED,
                tags=("build",),
                description="Build/test tool managed by the outer workflow.",
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
