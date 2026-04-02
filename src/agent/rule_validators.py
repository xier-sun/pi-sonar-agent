"""Local rule validators for post-fix verification."""

from __future__ import annotations


def validate_rule_fix(
    *,
    validator_name: str,
    issue_line: int,
    file_content: str,
) -> str:
    """Run a local validator and return an error message when validation fails."""

    normalized = str(validator_name or "").strip()
    if not normalized:
        return ""

    if normalized == "nested_ternary_removed":
        return _validate_nested_ternary_removed(issue_line=issue_line, file_content=file_content)

    return ""


def _is_statement_boundary(line_text: str) -> bool:
    """Return True when the line looks like a C# statement boundary."""

    stripped = str(line_text or "").strip()
    if not stripped:
        return False
    return (
        stripped.endswith(";")
        or stripped.endswith("{")
        or stripped.endswith("}")
        or stripped.startswith("#")
    )


def _find_statement_range(lines: list[str], issue_line: int) -> tuple[int, int]:
    """Find a narrow statement range around the issue line."""

    total_lines = max(len(lines), 1)
    start_line = min(max(issue_line, 1), total_lines)
    end_line = start_line

    while start_line > 1:
        previous = lines[start_line - 2]
        if _is_statement_boundary(previous):
            break
        start_line -= 1

    while end_line < total_lines:
        current = lines[end_line - 1]
        if _is_statement_boundary(current):
            break
        end_line += 1

    return start_line, end_line


def _strip_comments_and_literals(text: str) -> str:
    """Remove comments and string/char literals from C# code."""

    result: list[str] = []
    index = 0
    length = len(text)
    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_char = False
    is_verbatim = False

    while index < length:
        current = text[index]
        nxt = text[index + 1] if index + 1 < length else ""

        if in_line_comment:
            if current == "\n":
                in_line_comment = False
                result.append(current)
            index += 1
            continue

        if in_block_comment:
            if current == "*" and nxt == "/":
                in_block_comment = False
                index += 2
            else:
                if current == "\n":
                    result.append(current)
                index += 1
            continue

        if in_string:
            if is_verbatim:
                if current == '"' and nxt == '"':
                    index += 2
                    continue
                if current == '"':
                    in_string = False
                    is_verbatim = False
                index += 1
                continue

            if current == "\\":
                index += 2
                continue
            if current == '"':
                in_string = False
            index += 1
            continue

        if in_char:
            if current == "\\":
                index += 2
                continue
            if current == "'":
                in_char = False
            index += 1
            continue

        if current == "/" and nxt == "/":
            in_line_comment = True
            index += 2
            continue
        if current == "/" and nxt == "*":
            in_block_comment = True
            index += 2
            continue
        if current == "@" and nxt == '"':
            in_string = True
            is_verbatim = True
            index += 2
            continue
        if current == '"':
            in_string = True
            index += 1
            continue
        if current == "'":
            in_char = True
            index += 1
            continue

        result.append(current)
        index += 1

    return "".join(result)


def _count_conditional_ternary_operators(text: str) -> int:
    """Count likely conditional-operator question marks in the text."""

    count = 0
    for index, current in enumerate(text):
        if current != "?":
            continue

        previous = text[index - 1] if index > 0 else ""
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if nxt in {".", "?", "["}:
            continue
        if previous == "?":
            continue
        count += 1

    return count


def _validate_nested_ternary_removed(*, issue_line: int, file_content: str) -> str:
    """Fail when the target statement still contains nested ternary operators."""

    lines = str(file_content or "").splitlines()
    if not lines:
        return ""

    start_line, end_line = _find_statement_range(lines, issue_line)
    statement_text = "\n".join(lines[start_line - 1:end_line])
    normalized_statement = _strip_comments_and_literals(statement_text)
    ternary_count = _count_conditional_ternary_operators(normalized_statement)
    if ternary_count <= 1:
        return ""

    snippet = "\n".join(f"{line_no:4d} | {lines[line_no - 1]}" for line_no in range(start_line, end_line + 1))
    return "\n".join(
        [
            "Rule-specific validation failed: nested ternary expression still exists in the target statement.",
            f"Expected: csharpsquid:S3358 should leave at most one conditional operator in the issue statement near line {issue_line}.",
            "Current statement snippet:",
            snippet,
        ]
    )
