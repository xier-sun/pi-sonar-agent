"""Legacy scope-guard helpers used during gradual guardrail migration."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pi_sonar_agent.agent.rule_policies import (
    CONDITIONAL_CHAIN_SCOPE_MODE,
    CONTROL_BLOCK_SCOPE_MODE,
    DECLARATION_COMMENT_SCOPE_MODE,
    EXPRESSION_REWRITE_SCOPE_MODE,
    LOOP_REWRITE_SCOPE_MODE,
    METHOD_SCOPE_MODE,
    STATEMENT_SCOPE_MODE,
    get_rule_policy,
)
from pi_sonar_agent.core.attempt_changes import AttemptFileChangeBuilder
from pi_sonar_agent.core.boundary_policy import BoundaryPolicy
from pi_sonar_agent.core.issue_contract import EditContract

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue


CONTROL_FLOW_PREFIXES = (
    "if",
    "else",
    "for",
    "foreach",
    "while",
    "switch",
    "case",
    "catch",
    "using",
    "lock",
    "return",
    "do",
    "try",
)


@dataclass(frozen=True)
class IssueEditScope:
    """Allowed edit scope for a single Sonar issue."""

    start_line: int
    end_line: int
    validation_start_line: int
    validation_end_line: int
    mode: str


class LegacyScopeGuard:
    """Best-effort scope calculation and validation for legacy guardrail mode."""

    @staticmethod
    def _resolve_allowed_line_ranges(
        scope: IssueEditScope,
        edit_contract: EditContract | None = None,
    ) -> tuple[tuple[int, int], ...]:
        if edit_contract is not None:
            contract_ranges = BoundaryPolicy.contract_line_ranges(edit_contract)
            if contract_ranges:
                return contract_ranges
        return ((scope.validation_start_line, scope.validation_end_line),)

    @staticmethod
    def _format_allowed_line_ranges(
        allowed_line_ranges: tuple[tuple[int, int], ...],
    ) -> str:
        normalized_ranges = BoundaryPolicy.normalize_line_ranges(allowed_line_ranges)
        if not normalized_ranges:
            return ""
        return ", ".join(f"{start_line}-{end_line}" for start_line, end_line in normalized_ranges)

    @staticmethod
    def _looks_like_method_signature(header_text: str) -> bool:
        normalized = " ".join(str(header_text or "").split())
        if "(" not in normalized or ")" not in normalized:
            return False

        lower = normalized.lower()
        if any(lower.startswith(f"{prefix} ") for prefix in CONTROL_FLOW_PREFIXES):
            return False
        if normalized.endswith(";"):
            return False
        return "=" not in normalized.split("(", 1)[0] or any(
            token in lower
            for token in ("public ", "private ", "protected ", "internal ", "async ", "static ")
        )

    @classmethod
    def _find_enclosing_method_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        if not lines:
            return None

        search_start = max(1, issue_line - 80)
        search_end = min(len(lines), issue_line + 5)

        for candidate in range(issue_line, search_start - 1, -1):
            header_end = min(len(lines), candidate + 3)
            header_text = " ".join(line.strip() for line in lines[candidate - 1:header_end] if line.strip())
            if not cls._looks_like_method_signature(header_text):
                continue

            brace_line = None
            for line_number in range(candidate, min(len(lines), candidate + 5) + 1):
                if "{" in lines[line_number - 1]:
                    brace_line = line_number
                    break
            if brace_line is None or brace_line > search_end:
                continue

            depth = 0
            started = False
            for line_number in range(brace_line, len(lines) + 1):
                current = lines[line_number - 1]
                depth += current.count("{")
                if current.count("{"):
                    started = True
                depth -= current.count("}")
                if started and depth <= 0:
                    return candidate, line_number

        return None

    @staticmethod
    def _find_next_non_empty_line(lines: list[str], start_line: int) -> int | None:
        for line_number in range(max(start_line, 1), len(lines) + 1):
            if lines[line_number - 1].strip():
                return line_number
        return None

    @staticmethod
    def _line_starts_with_keyword(line_text: str, *keywords: str) -> bool:
        stripped = str(line_text or "").strip()
        return any(
            stripped == keyword
            or stripped.startswith(f"{keyword} ")
            or stripped.startswith(f"{keyword}(")
            for keyword in keywords
        )

    @classmethod
    def _looks_like_control_statement_header(cls, line_text: str) -> bool:
        return cls._line_starts_with_keyword(
            line_text,
            "if",
            "else if",
            "else",
            "for",
            "foreach",
            "while",
            "using",
        )

    @classmethod
    def _find_control_header_end(cls, lines: list[str], start_line: int) -> int:
        total_lines = len(lines)
        paren_depth = 0
        saw_paren = False

        for line_number in range(start_line, min(total_lines, start_line + 12) + 1):
            current = lines[line_number - 1]
            stripped = current.strip()

            if stripped.startswith("else") and "if" not in stripped:
                return line_number

            paren_depth += current.count("(")
            if current.count("("):
                saw_paren = True
            paren_depth -= current.count(")")

            if saw_paren and paren_depth <= 0 and ")" in current:
                return line_number

        return start_line

    @staticmethod
    def _find_matching_brace_end(lines: list[str], brace_line: int) -> int | None:
        depth = 0
        started = False
        for line_number in range(brace_line, len(lines) + 1):
            current = lines[line_number - 1]
            depth += current.count("{")
            if current.count("{"):
                started = True
            depth -= current.count("}")
            if started and depth <= 0:
                return line_number
        return None

    @classmethod
    def _find_statement_end_from_line(cls, lines: list[str], start_line: int) -> int:
        end_line = min(max(start_line, 1), len(lines))
        while end_line < len(lines):
            current = lines[end_line - 1]
            if cls._is_statement_boundary(current):
                break
            end_line += 1
        return end_line

    @classmethod
    def _find_control_statement_range_from_header(
        cls,
        lines: list[str],
        header_start: int,
    ) -> tuple[int, int]:
        total_lines = len(lines)
        header_end = cls._find_control_header_end(lines, header_start)
        brace_start = None

        for line_number in range(header_start, min(total_lines, header_end + 2) + 1):
            if "{" in lines[line_number - 1]:
                brace_start = line_number
                break

        if brace_start is not None:
            brace_end = cls._find_matching_brace_end(lines, brace_start)
            if brace_end is not None:
                return header_start, brace_end

        body_start = cls._find_next_non_empty_line(lines, header_end + 1)
        if body_start is None:
            return header_start, header_end

        if lines[body_start - 1].strip().startswith("{"):
            brace_end = cls._find_matching_brace_end(lines, body_start)
            if brace_end is not None:
                return header_start, brace_end

        return header_start, cls._find_statement_end_from_line(lines, body_start)

    @classmethod
    def _find_control_statement_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        search_start = max(1, issue_line - 8)
        for candidate in range(issue_line, search_start - 1, -1):
            if not cls._looks_like_control_statement_header(lines[candidate - 1]):
                continue
            return cls._find_control_statement_range_from_header(lines, candidate)
        return None

    @staticmethod
    def _looks_like_attribute_line(line_text: str) -> bool:
        stripped = str(line_text or "").strip()
        return stripped.startswith("[") or stripped.endswith("]")

    @classmethod
    def _find_declaration_comment_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int]:
        total_lines = len(lines)
        declaration_start = min(max(issue_line, 1), total_lines)

        while declaration_start > 1 and cls._looks_like_attribute_line(lines[declaration_start - 2]):
            declaration_start -= 1

        declaration_end = declaration_start
        while declaration_end < total_lines:
            current = lines[declaration_end - 1].strip()
            if current.endswith("{") or current.endswith(";") or current.endswith("=>"):
                break
            declaration_end += 1

        return declaration_start, declaration_end

    @classmethod
    def _find_conditional_chain_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        search_start = max(1, issue_line - 12)
        candidate = None
        for line_number in range(issue_line, search_start - 1, -1):
            if cls._line_starts_with_keyword(lines[line_number - 1], "if", "else if"):
                candidate = line_number
                break

        if candidate is None:
            return None

        if cls._line_starts_with_keyword(lines[candidate - 1], "else", "else if"):
            for line_number in range(candidate - 1, search_start - 1, -1):
                if not cls._line_starts_with_keyword(lines[line_number - 1], "if"):
                    continue
                _, branch_end = cls._find_control_statement_range_from_header(lines, line_number)
                next_line = cls._find_next_non_empty_line(lines, branch_end + 1)
                if next_line == candidate:
                    candidate = line_number
                    break

        for line_number in range(candidate - 1, search_start - 1, -1):
            if not cls._line_starts_with_keyword(lines[line_number - 1], "if"):
                continue
            outer_start, outer_end = cls._find_control_statement_range_from_header(lines, line_number)
            if outer_start <= candidate <= outer_end:
                candidate = line_number

        chain_start = candidate
        _, chain_end = cls._find_control_statement_range_from_header(lines, chain_start)
        cursor = chain_end + 1

        while True:
            next_line = cls._find_next_non_empty_line(lines, cursor)
            if next_line is None or not cls._line_starts_with_keyword(lines[next_line - 1], "else", "else if"):
                break
            _, chain_end = cls._find_control_statement_range_from_header(lines, next_line)
            cursor = chain_end + 1

        return chain_start, chain_end

    @staticmethod
    def _looks_like_expression_rewrite_anchor(line_text: str) -> bool:
        stripped = str(line_text or "").strip()
        if not stripped:
            return False
        return (
            "=>" in stripped
            or stripped in {"{", "("}
            or stripped.endswith("=")
            or stripped.endswith("=>")
            or stripped.endswith("return")
        )

    @classmethod
    def _find_expression_rewrite_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int]:
        start_line, end_line = cls._find_enclosing_statement_range(lines, issue_line)
        search_start = max(1, start_line - 8)
        rewrite_start = start_line

        for candidate in range(start_line - 1, search_start - 1, -1):
            current = lines[candidate - 1].strip()
            if not current:
                rewrite_start = candidate
                continue
            if cls._looks_like_expression_rewrite_anchor(current):
                rewrite_start = candidate
                continue
            break

        return rewrite_start, end_line

    @classmethod
    def _find_loop_rewrite_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        search_start = max(1, issue_line - 8)
        for candidate in range(issue_line, search_start - 1, -1):
            if not cls._line_starts_with_keyword(lines[candidate - 1], "for", "foreach", "while"):
                continue

            loop_start, loop_end = cls._find_control_statement_range_from_header(lines, candidate)
            rewrite_end = loop_end
            next_line = cls._find_next_non_empty_line(lines, loop_end + 1)
            if next_line is not None and cls._line_starts_with_keyword(
                lines[next_line - 1],
                "return",
                "throw",
            ):
                rewrite_end = cls._find_statement_end_from_line(lines, next_line)
            return loop_start, rewrite_end

        return None

    @staticmethod
    def _is_statement_boundary(line_text: str) -> bool:
        stripped = str(line_text or "").strip()
        if not stripped:
            return False
        return (
            stripped.endswith(";")
            or stripped.endswith("{")
            or stripped.endswith("}")
            or stripped.startswith("#")
        )

    @classmethod
    def _find_enclosing_statement_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int]:
        total_lines = max(len(lines), 1)
        start_line = min(max(issue_line, 1), total_lines)
        end_line = start_line

        while start_line > 1:
            previous = lines[start_line - 2]
            if cls._is_statement_boundary(previous):
                break
            start_line -= 1

        while end_line < total_lines:
            current = lines[end_line - 1]
            if cls._is_statement_boundary(current):
                break
            end_line += 1

        return start_line, end_line

    @classmethod
    def build_issue_edit_scope(
        cls,
        issue: SonarIssue,
        lines: list[str],
    ) -> IssueEditScope:
        total_lines = max(len(lines), 1)
        policy = get_rule_policy(issue.rule)
        scope_mode = policy.scope_mode
        logical_range: tuple[int, int] | None = None

        if scope_mode == METHOD_SCOPE_MODE:
            logical_range = cls._find_enclosing_method_range(lines, issue.line)
        elif scope_mode == CONTROL_BLOCK_SCOPE_MODE:
            logical_range = cls._find_control_statement_range(lines, issue.line)
        elif scope_mode == DECLARATION_COMMENT_SCOPE_MODE:
            logical_range = cls._find_declaration_comment_range(lines, issue.line)
        elif scope_mode == CONDITIONAL_CHAIN_SCOPE_MODE:
            logical_range = cls._find_conditional_chain_range(lines, issue.line)
        elif scope_mode == EXPRESSION_REWRITE_SCOPE_MODE:
            logical_range = cls._find_expression_rewrite_range(lines, issue.line)
        elif scope_mode == LOOP_REWRITE_SCOPE_MODE:
            logical_range = cls._find_loop_rewrite_range(lines, issue.line)

        if logical_range is None:
            logical_range = cls._find_enclosing_statement_range(lines, issue.line)
            scope_mode = STATEMENT_SCOPE_MODE
            validation_start_line = max(1, logical_range[0] - policy.validation_leading_lines)
            validation_end_line = min(total_lines, logical_range[1] + policy.validation_trailing_lines)
        else:
            validation_start_line = max(1, logical_range[0] - policy.validation_leading_lines)
            validation_end_line = min(total_lines, logical_range[1] + policy.validation_trailing_lines)

        start_line, end_line = logical_range
        return IssueEditScope(
            start_line=start_line,
            end_line=end_line,
            validation_start_line=validation_start_line,
            validation_end_line=validation_end_line,
            mode=scope_mode,
        )

    @staticmethod
    def build_scope_guidance(issue: SonarIssue, scope: IssueEditScope | None) -> str:
        if scope is None:
            return (
                "- 只允许修改 SonarQube 指向的那一处问题。\n"
                "- 不要顺手修复本文件中其他相同规则或相同写法的问题。"
            )

        if scope.mode == METHOD_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的目标方法。\n"
                f"- 如果必须提取 private 辅助方法，只能新增在该方法后方紧邻区域，且不要超过第 {scope.validation_end_line} 行。\n"
                "- 新增的辅助方法只能服务当前 issue 对应的方法，不要改动本文件其他方法中的同类问题。"
            )

        if scope.mode == CONTROL_BLOCK_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的当前控制语句及其直接代码块。\n"
                "- 如果需要补大括号，只能在这段控制语句周围新增必要的 { }，不要扩展到旁边的分支、循环或其他语句。"
            )

        if scope.mode == DECLARATION_COMMENT_SCOPE_MODE:
            return (
                f"- 只允许在第 {scope.start_line}-{scope.end_line} 行对应的公开成员声明前添加或调整 XML 注释。\n"
                "- 注释必须紧贴当前声明或其 attribute，不要顺手修改其他成员的注释内容。"
            )

        if scope.mode == CONDITIONAL_CHAIN_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的当前 if/else 条件链。\n"
                "- 只调整这条条件链本身，不要顺手改方法里的其他条件分支。"
            )

        if scope.mode == EXPRESSION_REWRITE_SCOPE_MODE:
            return (
                f"- 只允许在第 {scope.validation_start_line}-{scope.validation_end_line} 行附近重写当前表达式，核心问题位于第 {scope.start_line}-{scope.end_line} 行。\n"
                "- 优先把当前嵌套 ?: 改成局部变量、if/else 或语句 lambda，然后在原位置回填结果。\n"
                "- 不要新增类级 private/helper 方法，不要把辅助逻辑提到类尾、其他方法或同文件其他位置。"
            )

        if scope.mode == LOOP_REWRITE_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的当前循环改写范围。\n"
                "- 可以把当前 foreach/for/while 重写成 LINQ、Any、FirstOrDefault 等等价表达式。\n"
                "- 如果循环后紧跟着与该查找/过滤逻辑配套的 return 或 throw，也可以一并改写，但不要扩展到后续无关语句。"
            )

        return (
            f"- 只允许修改包含 issue 行的这条语句，当前允许范围是第 {scope.start_line}-{scope.end_line} 行。\n"
            "- 如果本文件其他地方也有相同写法或相同规则问题，不要顺手修改。"
        )

    @staticmethod
    def extract_changed_line_numbers(diff_text: str) -> set[int]:
        return AttemptFileChangeBuilder.extract_touched_line_numbers(diff_text)

    @staticmethod
    def find_out_of_scope_lines(scope: IssueEditScope, changed_lines: set[int]) -> list[int]:
        return list(
            BoundaryPolicy.find_outside_lines(
                changed_lines,
                ((scope.validation_start_line, scope.validation_end_line),),
            )
        )

    @staticmethod
    def build_content_diff(
        original_content: str,
        current_content: str,
        relative_path: str,
    ) -> str:
        return AttemptFileChangeBuilder.build_content_diff(
            original_content,
            current_content,
            relative_path,
        )

    @classmethod
    def validate_issue_edit_scope(
        cls,
        workspace_path: Path,
        issue: SonarIssue,
        scope: IssueEditScope | None,
        *,
        edit_contract: EditContract | None = None,
        original_content: str | None = None,
        current_content: str | None = None,
    ) -> str | None:
        if scope is None:
            return None

        relative_path = issue.file_path.lstrip("/").replace("\\", "/")
        diff_text = ""
        if original_content is not None and current_content is not None:
            diff_text = cls.build_content_diff(original_content, current_content, relative_path)
        else:
            try:
                result = subprocess.run(
                    ["git", "diff", "--unified=0", "--", relative_path],
                    cwd=str(workspace_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            except Exception:
                return None

            if result.returncode != 0:
                return None
            diff_text = result.stdout

        changed_lines = cls.extract_changed_line_numbers(diff_text)
        if not changed_lines:
            return None

        allowed_line_ranges = cls._resolve_allowed_line_ranges(scope, edit_contract)
        offending_lines = list(
            BoundaryPolicy.find_outside_lines(
                changed_lines,
                allowed_line_ranges,
            )
        )
        if not offending_lines:
            return None

        offending_text = ", ".join(str(line) for line in offending_lines[:12])
        return (
            "Issue changes exceeded the allowed Sonar edit scope.\n"
            f"Allowed lines: {cls._format_allowed_line_ranges(allowed_line_ranges)}\n"
            f"Changed lines outside scope: {offending_text}\n"
            "只允许修复 Sonar 指向的这一处代码，不要顺手修改本文件其他同类位置。"
        )
