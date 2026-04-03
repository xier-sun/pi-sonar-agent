"""Deterministic fixers for pattern-based code issues.

This module handles simple, pattern-matching code fixes that don't require
LLM or Agent assistance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Rules that can be fixed deterministically
UNUSED_VARIABLE_RULES = {"csharpsquid:S1481", "external_roslyn:CS0219"}
USELESS_ASSIGNMENT_RULES = {"csharpsquid:S1854"}


@dataclass(frozen=True)
class IssueGroup:
    """Group of related issues in the same file."""

    group_key: str
    file_path: str
    rule: str
    issues: tuple[dict[str, Any], ...]
    start_line: int
    end_line: int
    symbol_names: tuple[str, ...]


@dataclass(frozen=True)
class DeterministicFixResult:
    """Result of a deterministic fix operation."""

    updated_content: str
    strategy: str
    summary: str


def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_issue_line(issue: dict[str, Any]) -> int:
    """Get the line number of an issue."""
    text_range = issue.get("textRange", {}) or {}
    return safe_int(text_range.get("startLine") or issue.get("line"))


def extract_issue_symbol(issue: dict[str, Any]) -> str | None:
    """Extract symbol name from issue message."""
    message = str(issue.get("message", "")).strip()
    if not message:
        return None

    # Try various quote patterns
    quote_patterns = [
        r"'([A-Za-z_][A-Za-z0-9_]*)'",
        r'"([A-Za-z_][A-Za-z0-9_]*)"',
        r"“([A-Za-z_][A-Za-z0-9_]*)”",
        r"'([A-Za-z_][A-Za-z0-9_]*)'",
    ]
    for pattern in quote_patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1)

    # Fallback: variable name pattern
    fallback_match = re.search(
        r"(?:variable|变量)\s*([A-Za-z_][A-Za-z0-9_]*)",
        message,
        flags=re.IGNORECASE,
    )
    if fallback_match:
        return fallback_match.group(1)

    return None


def build_issue_groups(issues: list[dict[str, Any]]) -> list[IssueGroup]:
    """Group related issues together for batch fixing."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    group_order: list[tuple[str, str, str]] = []

    def sort_key(issue: dict[str, Any]) -> tuple[str, int, str]:
        return (
            str(issue.get("file_path", "")),
            -get_issue_line(issue),
            str(issue.get("key", "")),
        )

    for issue in sorted(issues, key=sort_key):
        file_path = str(issue.get("file_path", "")).strip()
        rule = str(issue.get("rule", "")).strip()
        issue_line = get_issue_line(issue)
        symbol_name = extract_issue_symbol(issue)

        # Determine discriminator based on rule type
        if rule in UNUSED_VARIABLE_RULES:
            discriminator = f"line:{issue_line}"
        elif rule in USELESS_ASSIGNMENT_RULES and symbol_name:
            discriminator = f"symbol:{symbol_name}"
        else:
            discriminator = f"issue:{issue.get('key', '')}"

        group_key = (file_path, rule, discriminator)
        if group_key not in grouped:
            grouped[group_key] = []
            group_order.append(group_key)
        grouped[group_key].append(issue)

    issue_groups: list[IssueGroup] = []
    for file_path, rule, discriminator in group_order:
        bucket = grouped[(file_path, rule, discriminator)]
        lines = [line for line in (get_issue_line(issue) for issue in bucket) if line > 0]
        symbol_names = tuple(
            dict.fromkeys(
                symbol_name
                for symbol_name in (extract_issue_symbol(issue) for issue in bucket)
                if symbol_name
            )
        )
        issue_groups.append(
            IssueGroup(
                group_key=f"{file_path}|{rule}|{discriminator}",
                file_path=file_path,
                rule=rule,
                issues=tuple(bucket),
                start_line=min(lines) if lines else 0,
                end_line=max(lines) if lines else 0,
                symbol_names=symbol_names,
            )
        )

    return issue_groups


def apply_deterministic_fix(
    file_content: str,
    issue_group: IssueGroup,
) -> DeterministicFixResult | None:
    """Apply a deterministic fix based on rule type."""
    if issue_group.rule in UNUSED_VARIABLE_RULES:
        return _apply_unused_deconstruction_fix(file_content, issue_group)
    if issue_group.rule in USELESS_ASSIGNMENT_RULES:
        return _apply_useless_assignment_fix(file_content, issue_group)
    return None


def _apply_unused_deconstruction_fix(
    file_content: str,
    issue_group: IssueGroup,
) -> DeterministicFixResult | None:
    """Fix unused variables in tuple deconstruction."""
    if not issue_group.symbol_names or issue_group.start_line <= 0:
        return None

    lines = file_content.splitlines(keepends=True)
    statement_start, statement_end = _find_statement_bounds(lines, issue_group.start_line - 1)
    statement = "".join(lines[statement_start: statement_end + 1])

    # Find tuple deconstruction pattern
    deconstruction_match = re.search(
        r"(?P<prefix>\b(?:var|[A-Za-z_][\w<>\[\],?. ]*)\s*)\((?P<vars>[^)]*)\)(?P<suffix>\s*=\s*.*)",
        statement,
        flags=re.DOTALL,
    )
    if not deconstruction_match:
        return None

    vars_section = deconstruction_match.group("vars")
    prefix = deconstruction_match.group("prefix")
    suffix = deconstruction_match.group("suffix")

    # Split and analyze variables
    var_list = [v.strip() for v in vars_section.split(",")]
    unused_symbols = set(issue_group.symbol_names)

    # Replace unused variables with _
    new_vars = []
    for var in var_list:
        var_name = var.strip()
        if var_name in unused_symbols:
            new_vars.append("_")
        else:
            new_vars.append(var_name)

    # Build new statement
    new_statement = f"{prefix}({', '.join(new_vars)}){suffix}"

    # Replace in content
    new_content = file_content.replace(statement, new_statement, 1)

    if new_content == file_content:
        return None

    return DeterministicFixResult(
        updated_content=new_content,
        strategy="unused_variable_fix",
        summary=f"Replaced {len(unused_symbols)} unused variable(s) with _",
    )


def _apply_useless_assignment_fix(
    file_content: str,
    issue_group: IssueGroup,
) -> DeterministicFixResult | None:
    """Fix useless assignments (assignments to variables that are never read)."""
    if not issue_group.symbol_names or issue_group.start_line <= 0:
        return None

    lines = file_content.splitlines(keepends=True)
    statement_start, statement_end = _find_statement_bounds(lines, issue_group.start_line - 1)

    # Look for assignments like "var x = ...;" where x is never used
    for symbol in issue_group.symbol_names:
        pattern = rf"(\bvar\s+{re.escape(symbol)}\s*=\s*[^;]+;)"
        match = re.search(pattern, "".join(lines[statement_start:statement_end + 1]))
        if match:
            # Remove the entire assignment line
            old_line = lines[statement_start]
            new_line = re.sub(
                rf"(\bvar\s+{re.escape(symbol)}\s*=\s*[^;]+;)",
                "",
                old_line,
            )
            new_line = re.sub(r"^\s*$", "", new_line)  # Remove empty lines

            if new_line != old_line:
                lines[statement_start] = new_line
                new_content = "".join(lines)

                return DeterministicFixResult(
                    updated_content=new_content,
                    strategy="useless_assignment_fix",
                    summary=f"Removed assignment to unused variable '{symbol}'",
                )

    return None


def _find_statement_bounds(
    lines: list[str],
    target_line: int,
) -> tuple[int, int]:
    """Find the start and end of a statement containing the target line."""
    if target_line < 0 or target_line >= len(lines):
        return (target_line, target_line)

    # Find statement start (go back to line with =, {, or statement start)
    start = target_line
    for i in range(target_line, -1, -1):
        line = lines[i].strip()
        if not line or line.startswith("//"):
            start = i + 1
            continue
        if "=" in line or "{" in line or line.startswith("var "):
            start = i
            break

    # Find statement end (go forward to semicolon or closing brace)
    end = target_line
    brace_count = 0
    for i in range(target_line, len(lines)):
        line = lines[i]
        brace_count += line.count("{") - line.count("}")
        if ";" in line and brace_count == 0:
            end = i
            break
        if brace_count < 0:
            end = i
            break

    return (start, end)


# Convenience function
def can_fix_deterministically(rule_id: str) -> bool:
    """Check if a rule can be fixed deterministically."""
    return rule_id in UNUSED_VARIABLE_RULES or rule_id in USELESS_ASSIGNMENT_RULES
