"""Deterministic S107 parameter-object patch generator for Roslyn-routed fixes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pi_sonar_agent.fixers.deterministic import IssueGroup


@dataclass(frozen=True)
class ParameterSpec:
    raw_text: str
    type_text: str
    name: str
    property_name: str


@dataclass(frozen=True)
class ParsedMethod:
    method_name: str
    access_modifier: str
    parameter_count: int
    start_line: int
    end_line: int
    signature_prefix: str
    signature_suffix: str
    indent: str
    member_indent: str
    body_indent: str
    containing_type_name: str
    namespace_name: str
    parameters: tuple[ParameterSpec, ...]
    parameter_object_name: str
    parameter_object_variable_name: str
    relative_path: str

    @property
    def qualified_parameter_object_type(self) -> str:
        if self.namespace_name:
            return f"global::{self.namespace_name}.{self.containing_type_name}.{self.parameter_object_name}"
        return f"global::{self.containing_type_name}.{self.parameter_object_name}"


@dataclass(frozen=True)
class S107ParameterObjectPatchResult:
    applied: bool
    strategy: str
    summary: str
    error: str = ""
    changed_files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _SignatureWindow:
    text: str
    start_index: int
    end_index: int
    indent: str


def generate_s107_parameter_object_patch(
    workspace_path: Path,
    issue_group: IssueGroup,
) -> S107ParameterObjectPatchResult:
    """Generate a conservative parameter-object patch for a safe S107 candidate."""

    relative_path = _normalize_relative_path(issue_group.file_path)
    target_path = workspace_path / relative_path
    if not target_path.exists():
        return S107ParameterObjectPatchResult(
            applied=False,
            strategy="roslyn:s107_target_missing",
            summary="Roslyn-routed S107 patch generation could not locate the issue file.",
            error=f"missing target file: {relative_path}",
        )

    original_text = target_path.read_text(encoding="utf-8")
    lines = original_text.replace("\r\n", "\n").split("\n")
    parsed_method = _find_method_declaration(lines, issue_group.start_line or issue_group.end_line or 0, relative_path)
    if parsed_method is None:
        return S107ParameterObjectPatchResult(
            applied=False,
            strategy="roslyn:s107_method_not_found",
            summary="Roslyn-routed S107 patch generation could not locate a supported method declaration.",
        )

    validation_error = _validate_parsed_method(workspace_path, parsed_method, lines)
    if validation_error:
        return S107ParameterObjectPatchResult(
            applied=False,
            strategy="roslyn:s107_patch_generation_blocked",
            summary="Roslyn-routed S107 candidate did not meet the deterministic patch generator constraints.",
            error=validation_error,
        )

    try:
        rewritten_target_text = _rewrite_invocations(
            original_text,
            parsed_method.relative_path,
            parsed_method,
        )
        transformed_target_text = _apply_target_file_transformation(rewritten_target_text, parsed_method)
    except Exception as exc:  # pragma: no cover - defensive guard
        return S107ParameterObjectPatchResult(
            applied=False,
            strategy="roslyn:s107_patch_generation_failed",
            summary="Roslyn-routed S107 candidate looked safe, but deterministic patch generation failed.",
            error=str(exc),
        )

    changed_files: dict[str, str] = {parsed_method.relative_path: transformed_target_text}
    for file_path in workspace_path.rglob("*.cs"):
        relative = _normalize_relative_path(file_path.relative_to(workspace_path).as_posix())
        if relative == parsed_method.relative_path:
            continue
        if "/bin/" in f"/{relative}" or "/obj/" in f"/{relative}":
            continue
        file_text = file_path.read_text(encoding="utf-8")
        rewritten = _rewrite_invocations(file_text, relative, parsed_method)
        if rewritten != file_text:
            changed_files[relative] = rewritten

    return S107ParameterObjectPatchResult(
        applied=True,
        strategy="roslyn:s107_parameter_object_applied",
        summary=f"Applied a C# 8-safe parameter object refactor for `{parsed_method.method_name}`.",
        changed_files=changed_files,
    )


def _find_method_declaration(lines: list[str], issue_line: int, relative_path: str) -> ParsedMethod | None:
    if not lines:
        return None
    search_start = max(0, int(issue_line or 0) - 7)
    search_end = min(len(lines) - 1, max(int(issue_line or 0) + 4, int(issue_line or 0)))
    seen_starts: set[int] = set()
    candidates: list[ParsedMethod] = []
    for line_index in range(search_start, search_end + 1):
        signature = _collect_signature(lines, line_index)
        if signature is None or signature.start_index in seen_starts:
            continue
        seen_starts.add(signature.start_index)
        parsed = _try_parse_method(lines, signature, relative_path)
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.parameter_count <= 7,
            abs(candidate.start_line - int(issue_line or 0)),
        ),
    )[0]


def _try_parse_method(lines: list[str], signature: _SignatureWindow, relative_path: str) -> ParsedMethod | None:
    match = re.search(
        r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>.*)\)(?P<suffix>.*)$",
        signature.text,
        re.DOTALL,
    )
    if match is None:
        return None
    method_name = match.group("name").strip()
    if not method_name:
        return None
    parameter_texts = _split_top_level(match.group("params"), ",")
    parameters = []
    for raw_parameter in parameter_texts:
        parameter = _parse_parameter_spec(raw_parameter)
        if parameter is None:
            return None
        parameters.append(parameter)
    signature_prefix = signature.text[: match.end("name")].strip()
    normalized_signature = f" {signature.text} "
    access_modifier = next(
        (
            token
            for token in ("private", "internal", "public", "protected")
            if f" {token} " in normalized_signature.lower()
        ),
        "",
    )
    if not access_modifier:
        return None
    containing_type_name = _find_containing_type(lines, signature.start_index)
    if not containing_type_name:
        return None
    namespace_name = _find_namespace(lines, signature.start_index)
    parameter_object_name = f"{method_name}Parameters"
    parameter_object_variable_name = _pick_parameter_object_variable_name(
        "\n".join(lines[signature.end_index : signature.end_index + 24])
    )
    return ParsedMethod(
        method_name=method_name,
        access_modifier=access_modifier,
        parameter_count=len(parameter_texts),
        start_line=signature.start_index + 1,
        end_line=signature.end_index + 1,
        signature_prefix=signature_prefix,
        signature_suffix=match.group("suffix"),
        indent=signature.indent,
        member_indent=signature.indent + "    ",
        body_indent=signature.indent + "        ",
        containing_type_name=containing_type_name,
        namespace_name=namespace_name,
        parameters=tuple(parameters),
        parameter_object_name=parameter_object_name,
        parameter_object_variable_name=parameter_object_variable_name,
        relative_path=relative_path,
    )


def _validate_parsed_method(workspace_path: Path, parsed_method: ParsedMethod, lines: list[str]) -> str:
    normalized_signature = f" {parsed_method.signature_prefix} {parsed_method.signature_suffix} ".lower()
    if parsed_method.parameter_count <= 7:
        return "parameter count no longer exceeds the S107 threshold"
    if parsed_method.access_modifier not in {"private", "internal"}:
        return "public/protected members are intentionally blocked from deterministic S107 patch generation"
    if any(token in normalized_signature for token in (" override ", " virtual ", " partial ", " abstract ", " extern ", "=>", " where ")):
        return "unsupported method shape for deterministic S107 patch generation"
    if any(re.search(r"\b(ref|out|in|params)\b", parameter.raw_text) for parameter in parsed_method.parameters):
        return "parameter modifiers are not supported by deterministic S107 patch generation"
    if _has_leading_metadata(lines, parsed_method.start_line - 1):
        return "methods with leading attributes or XML docs are intentionally skipped"
    if _parameter_object_name_conflicts(lines, parsed_method.parameter_object_name):
        return "parameter object type name already exists in the file"
    property_names = [parameter.property_name for parameter in parsed_method.parameters]
    if len(property_names) != len(set(property_names)):
        return "parameter names would collapse to duplicate property names"
    if _count_method_declarations(workspace_path, parsed_method.method_name) > 1:
        return "multiple declarations share the same method name; deterministic rewrite is intentionally skipped"
    if not _method_body_has_open_brace(lines, parsed_method):
        return "only regular block-bodied methods are supported"
    return ""


def _rewrite_invocations(text: str, relative_path: str, parsed_method: ParsedMethod) -> str:
    masked = _mask_comments_and_strings(text)
    matches = list(re.finditer(rf"\b{re.escape(parsed_method.method_name)}\s*\(", masked))
    if not matches:
        return text
    updated_text = text
    for match in reversed(matches):
        line_number = updated_text.count("\n", 0, match.start()) + 1
        if (
            relative_path == parsed_method.relative_path
            and parsed_method.start_line <= line_number <= parsed_method.end_line
        ):
            continue
        open_paren_index = updated_text.find("(", match.start())
        if open_paren_index < 0:
            continue
        close_paren_index = _find_matching_paren(masked, open_paren_index)
        if close_paren_index < 0:
            continue
        args_text = updated_text[open_paren_index + 1 : close_paren_index]
        if len(_split_top_level(args_text, ",")) != parsed_method.parameter_count:
            continue
        replacement = f"(new {parsed_method.qualified_parameter_object_type}({args_text}))"
        updated_text = (
            updated_text[:open_paren_index]
            + replacement
            + updated_text[close_paren_index + 1 :]
        )
    return updated_text


def _apply_target_file_transformation(text: str, parsed_method: ParsedMethod) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    declaration_start_index = parsed_method.start_line - 1
    parameter_object_block = _build_parameter_object_block(parsed_method)
    lines[declaration_start_index:declaration_start_index] = parameter_object_block
    signature_start_index = declaration_start_index + len(parameter_object_block)
    signature_end_index = parsed_method.end_line - 1 + len(parameter_object_block)
    signature_line = (
        f"{parsed_method.indent}{parsed_method.signature_prefix}"
        f"({parsed_method.parameter_object_name} {parsed_method.parameter_object_variable_name})"
        f"{parsed_method.signature_suffix}"
    ).rstrip()
    lines[signature_start_index : signature_end_index + 1] = [signature_line]
    open_brace_index = (
        signature_start_index
        if "{" in signature_line
        else _find_open_brace_line(lines, signature_start_index)
    )
    if open_brace_index < 0:
        raise ValueError("unsupported method body shape; no opening brace was found")
    local_bindings = _build_local_bindings(parsed_method)
    lines[open_brace_index + 1 : open_brace_index + 1] = local_bindings
    return "\n".join(lines)


def _build_parameter_object_block(parsed_method: ParsedMethod) -> list[str]:
    access_modifier = "private" if parsed_method.access_modifier == "private" else "internal"
    ctor_parameters = ", ".join(parameter.raw_text for parameter in parsed_method.parameters)
    lines = [
        f"{parsed_method.indent}{access_modifier} sealed class {parsed_method.parameter_object_name}",
        f"{parsed_method.indent}{{",
        f"{parsed_method.member_indent}{access_modifier} {parsed_method.parameter_object_name}({ctor_parameters})",
        f"{parsed_method.member_indent}{{",
    ]
    for parameter in parsed_method.parameters:
        lines.append(
            f"{parsed_method.body_indent}{parameter.property_name} = {parameter.name};"
        )
    lines.append(f"{parsed_method.member_indent}}}")
    lines.append("")
    for parameter in parsed_method.parameters:
        lines.append(
            f"{parsed_method.member_indent}{access_modifier} {parameter.type_text} {parameter.property_name} {{ get; }}"
        )
    lines.append(f"{parsed_method.indent}}}")
    lines.append("")
    return lines


def _build_local_bindings(parsed_method: ParsedMethod) -> list[str]:
    lines = [
        f"{parsed_method.body_indent}var {parameter.name} = {parsed_method.parameter_object_variable_name}.{parameter.property_name};"
        for parameter in parsed_method.parameters
    ]
    lines.append("")
    return lines


def _find_open_brace_line(lines: list[str], start_index: int) -> int:
    for index in range(start_index, min(len(lines), start_index + 6)):
        text = lines[index].strip()
        if "=>" in text:
            return -1
        if "{" in text:
            return index
    return -1


def _method_body_has_open_brace(lines: list[str], parsed_method: ParsedMethod) -> bool:
    signature_start_index = parsed_method.start_line - 1
    signature_end_index = parsed_method.end_line - 1
    signature_lines = lines[signature_start_index : signature_end_index + 1]
    if any("=>" in line for line in signature_lines):
        return False
    if any("{" in line for line in signature_lines):
        return True
    return _find_open_brace_line(lines, signature_start_index) >= 0


def _parameter_object_name_conflicts(lines: list[str], type_name: str) -> bool:
    pattern = re.compile(rf"\b(class|struct|record)\s+{re.escape(type_name)}\b")
    return any(pattern.search(line) for line in lines)


def _has_leading_metadata(lines: list[str], declaration_index: int) -> bool:
    for index in range(declaration_index - 1, -1, -1):
        text = lines[index].strip()
        if not text:
            continue
        return text.startswith("[") or text.startswith("///")
    return False


def _count_method_declarations(workspace_path: Path, method_name: str) -> int:
    count = 0
    seen_declarations: set[tuple[str, str, int, int]] = set()
    for file_path in workspace_path.rglob("*.cs"):
        relative = _normalize_relative_path(file_path.relative_to(workspace_path).as_posix())
        if "/bin/" in f"/{relative}" or "/obj/" in f"/{relative}":
            continue
        lines = file_path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
        seen_starts: set[int] = set()
        for line_index in range(len(lines)):
            signature = _collect_signature(lines, line_index)
            if signature is None or signature.start_index in seen_starts:
                continue
            seen_starts.add(signature.start_index)
            parsed = _try_parse_method(lines, signature, relative)
            if parsed is not None and parsed.method_name == method_name:
                declaration_key = (
                    relative,
                    parsed.method_name,
                    parsed.start_line,
                    parsed.end_line,
                )
                if declaration_key in seen_declarations:
                    continue
                seen_declarations.add(declaration_key)
                count += 1
                if count > 1:
                    return count
    return count


def _collect_signature(lines: list[str], start_index: int) -> _SignatureWindow | None:
    if start_index < 0 or start_index >= len(lines):
        return None
    current_line = lines[start_index].strip()
    if (
        not current_line
        or current_line.startswith("//")
        or current_line in {"{", "}"}
        or re.search(r"[A-Za-z_]", current_line) is None
    ):
        return None
    indent = lines[start_index][: len(lines[start_index]) - len(lines[start_index].lstrip())]
    builder: list[str] = []
    open_paren_seen = False
    close_paren_seen = False
    for index in range(start_index, min(len(lines), start_index + 8)):
        text = lines[index].strip()
        if not text or text.startswith("//") or text in {"{", "}"}:
            continue
        builder.append(text)
        open_paren_seen = open_paren_seen or "(" in text
        close_paren_seen = close_paren_seen or ")" in text
        if open_paren_seen and close_paren_seen:
            return _SignatureWindow(
                text=" ".join(builder),
                start_index=start_index,
                end_index=index,
                indent=indent,
            )
    return None


def _find_containing_type(lines: list[str], start_index: int) -> str:
    for index in range(start_index, -1, -1):
        text = lines[index].strip()
        if not text or text.startswith("//"):
            continue
        match = re.search(r"\b(class|struct|record)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b", text)
        if match is not None:
            return match.group("name").strip()
    return ""


def _find_namespace(lines: list[str], start_index: int) -> str:
    for index in range(start_index, -1, -1):
        text = lines[index].strip()
        if not text or text.startswith("//"):
            continue
        match = re.match(r"namespace\s+(?P<name>[A-Za-z0-9_.]+)\s*([;{].*)?$", text)
        if match is not None:
            return match.group("name").strip()
    return ""


def _pick_parameter_object_variable_name(context_text: str) -> str:
    for candidate in ("parameters", "args", "request", "input"):
        if re.search(rf"\b{re.escape(candidate)}\b", context_text) is None:
            return candidate
    return "parameterObject"


def _parse_parameter_spec(raw_parameter: str) -> ParameterSpec | None:
    trimmed = raw_parameter.strip()
    if not trimmed:
        return None
    without_default = _split_top_level(trimmed, "=")[0].strip()
    if not without_default:
        return None
    if any(token in without_default for token in ("[", "]", "=>")) or "delegate" in without_default:
        return None
    match = re.match(
        r"^(?P<type>.+?)\s+(?P<name>@?[A-Za-z_][A-Za-z0-9_]*)$",
        without_default,
        re.DOTALL,
    )
    if match is None:
        return None
    type_text = match.group("type").strip()
    name = match.group("name").strip()
    property_name = _to_pascal_case(name.lstrip("@"))
    if not type_text or not name or not property_name:
        return None
    return ParameterSpec(
        raw_text=without_default,
        type_text=type_text,
        name=name,
        property_name=property_name,
    )


def _to_pascal_case(value: str) -> str:
    normalized = "".join(ch for ch in value if ch.isalnum() or ch == "_")
    if not normalized:
        return ""
    parts = [part for part in normalized.split("_") if part]
    if not parts:
        parts = [normalized]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    angle_depth = 0
    round_depth = 0
    square_depth = 0
    brace_depth = 0
    in_string = False
    in_verbatim_string = False
    in_char = False
    index = 0
    while index < len(text):
        ch = text[index]
        next_ch = text[index + 1] if index + 1 < len(text) else ""
        if in_verbatim_string:
            current.append(ch)
            if ch == '"' and next_ch == '"':
                current.append(next_ch)
                index += 2
                continue
            if ch == '"':
                in_verbatim_string = False
            index += 1
            continue
        if in_string:
            current.append(ch)
            if ch == "\\" and next_ch:
                current.append(next_ch)
                index += 2
                continue
            if ch == '"':
                in_string = False
            index += 1
            continue
        if in_char:
            current.append(ch)
            if ch == "\\" and next_ch:
                current.append(next_ch)
                index += 2
                continue
            if ch == "'":
                in_char = False
            index += 1
            continue
        if ch == "@" and next_ch == '"':
            current.extend([ch, next_ch])
            in_verbatim_string = True
            index += 2
            continue
        if ch == '"':
            current.append(ch)
            in_string = True
            index += 1
            continue
        if ch == "'":
            current.append(ch)
            in_char = True
            index += 1
            continue
        if ch == "<":
            angle_depth += 1
        elif ch == ">":
            angle_depth = max(0, angle_depth - 1)
        elif ch == "(":
            round_depth += 1
        elif ch == ")":
            round_depth = max(0, round_depth - 1)
        elif ch == "[":
            square_depth += 1
        elif ch == "]":
            square_depth = max(0, square_depth - 1)
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)
        if (
            ch == separator
            and angle_depth == 0
            and round_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current.clear()
            index += 1
            continue
        current.append(ch)
        index += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _mask_comments_and_strings(text: str) -> str:
    builder: list[str] = []
    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_verbatim_string = False
    in_char = False
    index = 0
    while index < len(text):
        ch = text[index]
        next_ch = text[index + 1] if index + 1 < len(text) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                builder.append(ch)
            else:
                builder.append(" ")
            index += 1
            continue
        if in_block_comment:
            if ch == "*" and next_ch == "/":
                builder.extend([" ", " "])
                in_block_comment = False
                index += 2
                continue
            builder.append("\n" if ch == "\n" else " ")
            index += 1
            continue
        if in_verbatim_string:
            if ch == '"' and next_ch == '"':
                builder.extend([" ", " "])
                index += 2
                continue
            builder.append("\n" if ch == "\n" else " ")
            if ch == '"':
                in_verbatim_string = False
            index += 1
            continue
        if in_string:
            if ch == "\\" and next_ch:
                builder.extend([" ", "\n" if next_ch == "\n" else " "])
                index += 2
                continue
            builder.append("\n" if ch == "\n" else " ")
            if ch == '"':
                in_string = False
            index += 1
            continue
        if in_char:
            if ch == "\\" and next_ch:
                builder.extend([" ", "\n" if next_ch == "\n" else " "])
                index += 2
                continue
            builder.append("\n" if ch == "\n" else " ")
            if ch == "'":
                in_char = False
            index += 1
            continue
        if ch == "/" and next_ch == "/":
            builder.extend([" ", " "])
            in_line_comment = True
            index += 2
            continue
        if ch == "/" and next_ch == "*":
            builder.extend([" ", " "])
            in_block_comment = True
            index += 2
            continue
        if ch == "@" and next_ch == '"':
            builder.extend(["@", " "])
            in_verbatim_string = True
            index += 2
            continue
        if ch == '"':
            builder.append(" ")
            in_string = True
            index += 1
            continue
        if ch == "'":
            builder.append(" ")
            in_char = True
            index += 1
            continue
        builder.append(ch)
        index += 1
    return "".join(builder)


def _find_matching_paren(masked_text: str, open_paren_index: int) -> int:
    depth = 0
    for index in range(open_paren_index, len(masked_text)):
        ch = masked_text[index]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _normalize_relative_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")
