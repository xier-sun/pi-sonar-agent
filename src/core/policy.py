"""Tool policy and hook helpers for issue-fix runtime."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from pi_sonar_agent.core.hooks import RuntimeHook, ToolCallContext
from pi_sonar_agent.core.registry import ToolKind, ToolRegistry
from pi_sonar_agent.core.tool_surface import CONTROLLED_BASH_TOOL, CONTROLLED_SHELL_DISPLAY_NAME

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
class ToolDecision:
    """Classification and allowlist decision for a single tool."""

    tool_name: str
    allowed: bool
    kind: ToolKind
    tags: tuple[str, ...] = ()
    reason: str = ""
    matched_rule: str = ""
    policy_violation: bool = False
    severity: str = "none"


@dataclass
class ToolUsageTracker:
    """Accumulated tool-usage facts for a single issue attempt."""

    tool_uses: list[str] = field(default_factory=list)
    forbidden_tool_uses: list[str] = field(default_factory=list)
    warning_tool_uses: list[str] = field(default_factory=list)
    last_tool_name: str | None = None
    saw_build_tool: bool = False

    def snapshot(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str | None, bool]:
        """Return an immutable snapshot for runtime results."""

        return (
            tuple(self.tool_uses),
            tuple(self.forbidden_tool_uses),
            tuple(self.warning_tool_uses),
            self.last_tool_name,
            self.saw_build_tool,
        )


class ToolPolicy:
    """Classify tool uses and expose the runtime allowlist."""

    def __init__(self, registry: ToolRegistry, allowed_tools: Iterable[str]) -> None:
        self.registry = registry
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

    _HIGH_RISK_SHELL_PATTERNS = (
        re.compile(r"(?i)\b(remove-item|erase|rmdir|del|rd|rm|ri)\b"),
        re.compile(r"(?i)\b(new-item|mkdir|ni|md)\b"),
        re.compile(r"(?i)\b(set-content|add-content|out-file|tee-object|copy-item|move-item|rename-item)\b"),
        re.compile(r"(?i)\b(copy|move|ren)\b"),
        re.compile(r"(?i)\b(echo|write-output)\b[^;&|\r\n]*(>>|>)(?!\s*(\$null|nul|/dev/null)\b)"),
        re.compile(r"(?i)\btype\s+nul\b[^;&|\r\n]*(>>|>)"),
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

    @classmethod
    def _is_high_risk_shell_command(cls, command: str) -> bool:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return True
        return any(pattern.search(normalized_command) for pattern in cls._HIGH_RISK_SHELL_PATTERNS)

    @classmethod
    def _is_harmless_shell_command(cls, command: str) -> bool:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            return False
        if cls._is_high_risk_shell_command(normalized_command):
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

    def classify(self, tool_name: str, payload: dict[str, object] | None = None) -> ToolDecision:
        """Return the classification/allowance decision for a tool."""

        normalized_tool_name = normalize_tool_name(tool_name)
        spec = self.registry.get(normalized_tool_name)
        if spec is None:
            return ToolDecision(
                tool_name=normalized_tool_name or tool_name,
                allowed=False,
                kind=ToolKind.UNKNOWN,
                reason="Tool is not registered for the issue-fix runtime.",
                policy_violation=True,
                severity="critical",
            )

        if normalized_tool_name == CONTROLLED_BASH_TOOL:
            command = self._normalize_shell_command(payload)
            if normalized_tool_name in self._allowed_lookup or normalized_tool_name in self._scoped_rules:
                if self._is_high_risk_shell_command(command):
                    return ToolDecision(
                        tool_name=normalized_tool_name,
                        allowed=False,
                        kind=spec.kind,
                        tags=spec.tags,
                        reason="Bash command would mutate the filesystem or create/delete files.",
                        policy_violation=True,
                        severity="critical",
                    )
                return ToolDecision(
                    tool_name=normalized_tool_name,
                    allowed=True,
                    kind=spec.kind,
                    tags=spec.tags,
                    matched_rule="windows-shell-safe",
                )
            if self._is_harmless_shell_command(command):
                return ToolDecision(
                    tool_name=normalized_tool_name,
                    allowed=False,
                    kind=spec.kind,
                    tags=spec.tags,
                    reason="Shell tool is not enabled, but the attempted command is read-only/diagnostic.",
                    policy_violation=False,
                    severity="warning",
                )
            return ToolDecision(
                tool_name=normalized_tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Shell tool is not enabled for the current issue-fix runtime.",
                policy_violation=True,
                severity="critical",
            )

        if spec.kind == ToolKind.FORBIDDEN:
            return ToolDecision(
                tool_name=normalized_tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Tool is explicitly forbidden during issue fixing.",
                policy_violation=True,
                severity="critical",
            )

        if normalized_tool_name in self._allowed_lookup:
            return ToolDecision(
                tool_name=normalized_tool_name,
                allowed=True,
                kind=spec.kind,
                tags=spec.tags,
            )

        if spec.kind == ToolKind.CONTROLLED:
            return ToolDecision(
                tool_name=normalized_tool_name,
                allowed=False,
                kind=spec.kind,
                tags=spec.tags,
                reason="Tool is controlled by the outer workflow, not the model runtime.",
                severity="warning",
            )

        return ToolDecision(
            tool_name=normalized_tool_name,
            allowed=False,
            kind=spec.kind,
            tags=spec.tags,
            reason="Tool is registered but not in the current allowlist.",
            policy_violation=True,
            severity="critical",
        )

    def is_forbidden_tool(self, tool_name: str, payload: dict[str, object] | None = None) -> bool:
        """Return True when the tool use violates the current runtime policy."""

        return self.classify(tool_name, payload).policy_violation

    def is_build_tool(self, tool_name: str) -> bool:
        """Return True when the tool is a controlled build/test tool."""

        decision = self.classify(tool_name)
        return decision.kind == ToolKind.CONTROLLED and "build" in decision.tags


class ToolPolicyHook(RuntimeHook):
    """Record tool-usage facts for the current attempt."""

    def __init__(self, policy: ToolPolicy, tracker: ToolUsageTracker) -> None:
        self.policy = policy
        self.tracker = tracker

    def before_tool_call(self, context: ToolCallContext) -> None:
        return None

    @staticmethod
    def _format_policy_violation_label(context: ToolCallContext) -> str:
        if context.tool_name != CONTROLLED_BASH_TOOL:
            return context.tool_name
        command = str((context.payload or {}).get("command") or "").strip()
        if not command:
            return CONTROLLED_SHELL_DISPLAY_NAME
        preview = command if len(command) <= 80 else command[:77].rstrip() + "..."
        return f"{CONTROLLED_SHELL_DISPLAY_NAME}({preview})"

    def after_tool_call(self, context: ToolCallContext) -> None:
        tool_name = context.tool_name
        self.tracker.tool_uses.append(tool_name)
        self.tracker.last_tool_name = tool_name
        if context.decision.policy_violation:
            self.tracker.forbidden_tool_uses.append(self._format_policy_violation_label(context))
        elif context.decision.severity == "warning":
            self.tracker.warning_tool_uses.append(self._format_policy_violation_label(context))
        if context.decision.kind == ToolKind.CONTROLLED and "build" in context.decision.tags:
            self.tracker.saw_build_tool = True

    def before_attempt_finalize(self, context) -> None:
        return None

    def after_attempt_finalize(self, context) -> None:
        return None
