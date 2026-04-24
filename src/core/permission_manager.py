"""Centralized permission decisions for issue-fix runtime tool use."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.registry import ToolKind, ToolRegistry
from pi_sonar_agent.core.tool_surface import CONTROLLED_BASH_TOOL

_TOOL_NAME_WRAPPER_PATTERN = re.compile(r"</?[^>]+>")
_TOOL_NAME_ALIASES = {
    "readasync": "Read",
    "editasync": "Edit",
    "multieditasync": "MultiEdit",
    "writeasync": "Write",
}


def normalize_tool_name(tool_name: str) -> str:
    """Collapse SDK/model wrapper noise around tool names to the registry form."""

    raw_value = str(tool_name or "").strip()
    if not raw_value:
        return ""
    normalized = _TOOL_NAME_WRAPPER_PATTERN.sub("", raw_value).strip()
    if not normalized:
        normalized = raw_value
    return _TOOL_NAME_ALIASES.get(normalized.lower(), normalized)


@dataclass(frozen=True)
class PermissionDecision:
    """Unified permission result for a single runtime action."""

    tool_name: str
    allowed: bool
    kind: ToolKind
    tags: tuple[str, ...] = ()
    reason: str = ""
    matched_rule: str = ""
    policy_violation: bool = False
    severity: str = "none"


@dataclass(frozen=True)
class PermissionContext:
    """Normalized permission input for one runtime action."""

    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)


class PermissionManager:
    """Single permission gate used before the runtime processes a tool call."""

    _DESTRUCTIVE_SHELL_PATTERNS = (
        re.compile(r"(?i)\b(remove-item|erase|rmdir|del|rd|rm|ri)\b"),
        re.compile(r"(?i)\b(set-content|add-content|out-file|tee-object|copy-item|move-item|rename-item)\b"),
        re.compile(r"(?i)\b(copy|move|ren)\b"),
        re.compile(r"(?i)\b(echo|write-output)\b[^;&|\r\n]*(>>|>)(?!\s*(\$null|nul|/dev/null)\b)"),
        re.compile(r"(?i)\btype\s+nul\b[^;&|\r\n]*(>>|>)"),
    )
    _BASH_FILE_CREATE_PATTERNS = (
        re.compile(
            r"""(?mx)
            (?<![<\d])
            >
            \s*
            (?![&])
            (?:
                "([^"\r\n]+)"
                |
                '([^'\r\n]+)'
                |
                ([^\s;&|]+)
            )
            """
        ),
        re.compile(
            r"""(?imx)
            \btouch\b
            \s+
            (?:
                "([^"\r\n]+)"
                |
                '([^'\r\n]+)'
                |
                ([^\s;&|]+)
            )
            """
        ),
    )
    _BASH_CREATE_COMMAND_MARKERS = (
        re.compile(r"(?im)\bmkdir\s+-p\b"),
        re.compile(r"(?im)\btouch\b"),
        re.compile(r"(?m)(?<![<\d])>(?![&])"),
    )
    _HARMLESS_SHELL_COMMANDS = frozenset(
        {
            "cd",
            "ls",
            "dir",
            "pwd",
            "cat",
            "type",
            "rg",
            "grep",
            "find",
            "where",
            "echo",
            "get-childitem",
            "get-location",
        }
    )

    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: tuple[str, ...] | list[str],
        *,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self._workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None
        self._allowed_tools = tuple(
            dict.fromkeys(
                normalized
                for name in allowed_tools
                if (normalized := normalize_tool_name(str(name)))
            )
        )
        exact_tools: list[str] = []
        scoped_rules: dict[str, list[str]] = {}
        for item in self._allowed_tools:
            parsed = self._parse_scoped_rule(item)
            if parsed is None:
                exact_tools.append(item)
                continue
            tool_name, rule_content = parsed
            scoped_rules.setdefault(tool_name, []).append(rule_content)

        self._allowed_lookup = frozenset(exact_tools)
        self._scoped_rules = {
            tool_name: tuple(rules)
            for tool_name, rules in scoped_rules.items()
        }

    def allowed_tool_names(self) -> tuple[str, ...]:
        """Return the stable allowlist passed to the SDK gateway."""

        return self._allowed_tools

    @staticmethod
    def _parse_scoped_rule(value: str) -> tuple[str, str] | None:
        if not value.endswith(")") or "(" not in value:
            return None
        tool_name, _, raw_rule = value.partition("(")
        normalized_tool_name = normalize_tool_name(tool_name)
        if not normalized_tool_name:
            return None
        rule_content = raw_rule[:-1].strip()
        if not rule_content or rule_content == "*":
            return None
        return normalized_tool_name, rule_content

    @staticmethod
    def _normalize_shell_command(payload: dict[str, object] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("command", "cmd"):
            value = payload.get(key)
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _is_destructive_shell_command(cls, command: str) -> bool:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return True
        return any(pattern.search(normalized_command) for pattern in cls._DESTRUCTIVE_SHELL_PATTERNS)

    @staticmethod
    def _normalize_relative_path(value: str) -> str:
        text = str(value or "").replace("\\", "/").strip().strip("'\"")
        while text.startswith("./"):
            text = text[2:]
        return text.strip().lstrip("/")

    @classmethod
    def _normalize_tool_path(cls, payload: dict[str, object] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("file_path", "path"):
            value = payload.get(key)
            normalized = cls._normalize_relative_path(str(value or ""))
            if normalized:
                return normalized
        return ""

    @classmethod
    def _extract_bash_created_paths(cls, command: str) -> tuple[str, ...]:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return ()
        paths: list[str] = []
        for pattern in cls._BASH_FILE_CREATE_PATTERNS:
            for match in pattern.finditer(normalized_command):
                candidate = next(
                    (group for group in match.groups() if str(group or "").strip()),
                    "",
                )
                normalized_candidate = cls._normalize_relative_path(candidate)
                if normalized_candidate:
                    paths.append(normalized_candidate)
        return tuple(dict.fromkeys(paths))

    @classmethod
    def _create_roots(cls, scoped_rules: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        roots: list[str] = []
        for rule in scoped_rules:
            text = str(rule or "").strip()
            prefix = "create_file_under="
            if not text.startswith(prefix):
                continue
            normalized = cls._normalize_relative_path(text[len(prefix) :])
            if normalized:
                roots.append(normalized.rstrip("/"))
        return tuple(dict.fromkeys(roots))

    @classmethod
    def _resolve_workspace_target(cls, workspace_root: Path | None, relative_path: str) -> Path | None:
        if workspace_root is None:
            return None
        normalized = cls._normalize_relative_path(relative_path)
        if not normalized:
            return None
        candidate = (workspace_root / normalized.replace("/", "\\")).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return None
        return candidate

    @classmethod
    def _is_allowed_create_path(
        cls,
        path: str,
        *,
        allowed_roots: tuple[str, ...],
    ) -> bool:
        normalized_path = cls._normalize_relative_path(path)
        if not normalized_path:
            return False
        if ":" in normalized_path.split("/", 1)[0]:
            return False
        return any(
            root == "." or normalized_path == root or normalized_path.startswith(root + "/")
            for root in allowed_roots
        )

    def _allows_write_file_creation(
        self,
        payload: dict[str, object] | None,
        *,
        scoped_rules: tuple[str, ...] | list[str],
    ) -> bool:
        allowed_roots = self._create_roots(scoped_rules)
        target_path = self._normalize_tool_path(payload)
        if not allowed_roots or not target_path:
            return False
        if not self._is_allowed_create_path(target_path, allowed_roots=allowed_roots):
            return False
        resolved = self._resolve_workspace_target(self._workspace_root, target_path)
        if resolved is not None and resolved.exists():
            return False
        return True

    @classmethod
    def _allows_bash_file_creation(
        cls,
        command: str,
        *,
        scoped_rules: tuple[str, ...] | list[str],
    ) -> bool:
        allowed_roots = cls._create_roots(scoped_rules)
        if not allowed_roots:
            return False
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return False
        if cls._is_destructive_shell_command(normalized_command):
            return False
        if not any(pattern.search(normalized_command) for pattern in cls._BASH_CREATE_COMMAND_MARKERS):
            return False
        created_paths = cls._extract_bash_created_paths(normalized_command)
        if not created_paths:
            return False
        return all(
            cls._is_allowed_create_path(path, allowed_roots=allowed_roots)
            for path in created_paths
        )

    @classmethod
    def _is_file_creation_shell_command(cls, command: str) -> bool:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return False
        return any(pattern.search(normalized_command) for pattern in cls._BASH_CREATE_COMMAND_MARKERS)

    @classmethod
    def _is_harmless_shell_command(cls, command: str) -> bool:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return False
        if cls._is_destructive_shell_command(normalized_command):
            return False
        segments = re.split(r"&&|\|\||[;|]", normalized_command)
        meaningful_segments = [segment.strip() for segment in segments if segment.strip()]
        if not meaningful_segments:
            return False
        for segment in meaningful_segments:
            token = segment.split(None, 1)[0].strip().lower()
            if token not in cls._HARMLESS_SHELL_COMMANDS:
                return False
        return True

    def decide(self, context: PermissionContext) -> PermissionDecision:
        """Return the permission decision for one tool use."""

        payload = dict(context.payload or {})
        normalized_tool_name = normalize_tool_name(context.tool_name)
        spec = self.registry.get(normalized_tool_name)
        if spec is None:
            return PermissionDecision(
                tool_name=normalized_tool_name or context.tool_name,
                allowed=False,
                kind=ToolKind.UNKNOWN,
                reason="Tool is not registered for the issue-fix runtime.",
                policy_violation=True,
                severity="critical",
            )

        if normalized_tool_name == CONTROLLED_BASH_TOOL:
            command = self._normalize_shell_command(payload)
            scoped_rules = self._scoped_rules.get(normalized_tool_name, ())
            if normalized_tool_name in self._allowed_lookup or normalized_tool_name in self._scoped_rules:
                if self._allows_bash_file_creation(command, scoped_rules=scoped_rules):
                    return PermissionDecision(
                        tool_name=normalized_tool_name,
                        allowed=True,
                        kind=spec.kind,
                        tags=spec.tags + ("file_create",),
                        matched_rule="windows-shell-create-file",
                    )
                if self._is_file_creation_shell_command(command):
                    return PermissionDecision(
                        tool_name=normalized_tool_name,
                        allowed=False,
                        kind=spec.kind,
                        tags=spec.tags,
                        reason="Bash file creation is only allowed for declared rule/path scopes.",
                        policy_violation=True,
                        severity="critical",
                    )
                if self._is_destructive_shell_command(command):
                    return PermissionDecision(
                        tool_name=normalized_tool_name,
                        allowed=False,
                        kind=spec.kind,
                        tags=spec.tags,
                        reason="Bash command would mutate the filesystem or create/delete files.",
                        policy_violation=True,
                        severity="critical",
                    )
                return PermissionDecision(
                    tool_name=normalized_tool_name,
                    allowed=True,
                    kind=spec.kind,
                    tags=spec.tags,
                    matched_rule="windows-shell-safe",
                )
            if self._is_harmless_shell_command(command):
                return PermissionDecision(
                    tool_name=normalized_tool_name,
                    allowed=False,
                    kind=spec.kind,
                    tags=spec.tags,
                    reason="Shell tool is not enabled, but the attempted command is read-only/diagnostic.",
                    policy_violation=False,
                    severity="warning",
                )
            return PermissionDecision(
                tool_name=normalized_tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Shell tool is not enabled for the current issue-fix runtime.",
                policy_violation=True,
                severity="critical",
            )

        if normalized_tool_name == "Write":
            scoped_rules = self._scoped_rules.get(normalized_tool_name, ())
            if scoped_rules:
                if self._allows_write_file_creation(payload, scoped_rules=scoped_rules):
                    return PermissionDecision(
                        tool_name=normalized_tool_name,
                        allowed=True,
                        kind=spec.kind,
                        tags=spec.tags + ("file_create",),
                        matched_rule="write-create-file",
                    )
                return PermissionDecision(
                    tool_name=normalized_tool_name,
                    allowed=False,
                    kind=spec.kind,
                    tags=spec.tags,
                    reason="Write is only allowed for creating new files inside the declared create-file roots.",
                    policy_violation=True,
                    severity="critical",
                )
            if normalized_tool_name in self._allowed_lookup:
                target_path = self._normalize_tool_path(payload)
                resolved = self._resolve_workspace_target(self._workspace_root, target_path)
                if target_path and resolved is not None and not resolved.exists():
                    return PermissionDecision(
                        tool_name=normalized_tool_name,
                        allowed=False,
                        kind=spec.kind,
                        tags=spec.tags,
                        reason="Write may rewrite existing files, but creating new files is not allowed in the current issue-fix runtime.",
                        policy_violation=True,
                        severity="critical",
                    )

        if spec.kind == ToolKind.FORBIDDEN:
            return PermissionDecision(
                tool_name=normalized_tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Tool is explicitly forbidden during issue fixing.",
                policy_violation=True,
                severity="critical",
            )

        if normalized_tool_name in self._allowed_lookup:
            return PermissionDecision(
                tool_name=normalized_tool_name,
                allowed=True,
                kind=spec.kind,
                tags=spec.tags,
            )

        if spec.kind == ToolKind.CONTROLLED:
            return PermissionDecision(
                tool_name=normalized_tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Tool is controlled by the outer workflow, not the model runtime.",
                severity="warning",
            )

        return PermissionDecision(
            tool_name=normalized_tool_name,
            allowed=False,
            kind=spec.kind,
            tags=spec.tags,
            reason="Tool is registered but not in the current allowlist.",
            policy_violation=True,
            severity="critical",
        )

    def is_forbidden_tool(self, tool_name: str, payload: dict[str, object] | None = None) -> bool:
        """Return True when the tool use violates the current permission set."""

        return self.decide(PermissionContext(tool_name=tool_name, payload=dict(payload or {}))).policy_violation

    def is_build_tool(self, tool_name: str) -> bool:
        """Return True when the tool is a controlled build/test tool."""

        decision = self.decide(PermissionContext(tool_name=tool_name))
        return decision.kind == ToolKind.CONTROLLED and "build" in decision.tags
