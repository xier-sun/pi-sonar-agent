"""Issue-level planning and edit-contract generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

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
from pi_sonar_agent.core.boundary_capabilities import (
    BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
    BOUNDARY_PROFILE_DECLARATION_ANCHOR,
    BOUNDARY_PROFILE_MEMBER_CLUSTER,
    HELPER_EXTRACT_CAPABILITY,
    METHOD_CLUSTER_DELETE_CAPABILITY,
    MULTI_FILE_REFACTOR_CAPABILITY,
    NEW_TYPE_ADD_CAPABILITY,
    SIGNATURE_CHANGE_CAPABILITY,
    resolve_boundary_capabilities,
    resolve_boundary_profile,
)
from pi_sonar_agent.core.issue_contract import (
    ContractContextSnippet,
    ContractTargetSymbol,
    EditContract,
)
from pi_sonar_agent.core.lessons_store import LessonsStore, PlannerLesson
from pi_sonar_agent.core.perf_flags import PerformanceFlags, load_performance_flags
from pi_sonar_agent.core.quality_gate import QualityGateCatalog, load_default_quality_gate_catalog
from pi_sonar_agent.core.repair_plan import (
    PlanPrecheckResult,
    RepairHelperPlan,
    RepairPlan,
    RepairPropagationTarget,
)
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.scope_guard import LegacyScopeGuard


@dataclass(frozen=True)
class IssuePlan:
    """Planner output for a single issue attempt."""

    strategy: str
    edit_contract: EditContract
    prompt_guidance: str
    validation_plan: tuple[str, ...]
    skip_reason: str = ""


class IssuePlanner:
    """Build structured edit contracts from issue metadata and scope hints."""

    _FAST_PATH_RULES = frozenset(
        {
            "csharpsquid:S1481",
            "csharpsquid:S125",
            "csharpsquid:S1144",
        }
    )
    _PLAN_FIRST_RULES = frozenset(
        {
            "csharpsquid:S3776",
            "csharpsquid:S1144",
            "csharpsquid:S107",
        }
    )
    _PLAN_FIRST_COMPLEX_CAPABILITIES = frozenset(
        {
            HELPER_EXTRACT_CAPABILITY,
            SIGNATURE_CHANGE_CAPABILITY,
            NEW_TYPE_ADD_CAPABILITY,
            MULTI_FILE_REFACTOR_CAPABILITY,
            METHOD_CLUSTER_DELETE_CAPABILITY,
        }
    )
    _PRIVATE_METHOD_PATTERN = re.compile(r"\bprivate\b")
    _METHOD_NAME_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(file_path or "").replace("\\", "/").lstrip("/")

    @staticmethod
    def _normalize_source_lines(source_lines: tuple[str, ...] | list[str] | None) -> list[str]:
        if not source_lines:
            return []
        return [str(line) for line in source_lines]

    @staticmethod
    def _infer_symbol_name(scope_mode: str, start_line: int, end_line: int) -> str:
        symbol_kind_map = {
            METHOD_SCOPE_MODE: "method",
            CONTROL_BLOCK_SCOPE_MODE: "control_block",
            DECLARATION_COMMENT_SCOPE_MODE: "declaration",
            CONDITIONAL_CHAIN_SCOPE_MODE: "conditional_chain",
            EXPRESSION_REWRITE_SCOPE_MODE: "expression_region",
            LOOP_REWRITE_SCOPE_MODE: "loop_region",
            STATEMENT_SCOPE_MODE: "statement",
        }
        symbol_kind = symbol_kind_map.get(scope_mode, "statement")
        if start_line > 0 and end_line > 0:
            return f"{symbol_kind}@{start_line}-{end_line}"
        return symbol_kind

    @staticmethod
    def _allowed_change_kinds(scope_mode: str) -> tuple[str, ...]:
        default = ("direct-fix", "extract-local", "guard-clause-adjustment")
        per_mode = {
            METHOD_SCOPE_MODE: (*default, "extract-private-helper"),
            CONTROL_BLOCK_SCOPE_MODE: ("direct-fix", "block-bracing", "condition-adjustment"),
            DECLARATION_COMMENT_SCOPE_MODE: ("xml-doc-update", "attribute-adjacent-comment"),
            CONDITIONAL_CHAIN_SCOPE_MODE: ("condition-rewrite", "branch-consolidation", "extract-local"),
            EXPRESSION_REWRITE_SCOPE_MODE: ("condition-rewrite", "lambda-rewrite", "extract-local"),
            LOOP_REWRITE_SCOPE_MODE: ("loop-rewrite", "linq-rewrite", "extract-local"),
            STATEMENT_SCOPE_MODE: default,
        }
        return per_mode.get(scope_mode, default)

    @staticmethod
    def _rule_specific_change_kinds(rule_id: str) -> tuple[str, ...]:
        normalized_rule_id = str(rule_id or "").strip()
        if normalized_rule_id == "csharpsquid:S125":
            return ("adjacent-cleanup",)
        return ()

    @staticmethod
    def _review_hints(scope_mode: str) -> tuple[str, ...]:
        hints = [
            "flag unrelated edits in the same file",
            "record incidental findings instead of editing them",
        ]
        if scope_mode in {EXPRESSION_REWRITE_SCOPE_MODE, LOOP_REWRITE_SCOPE_MODE, METHOD_SCOPE_MODE}:
            hints.append("allow local scaffolding that is required for the main fix")
        if scope_mode == DECLARATION_COMMENT_SCOPE_MODE:
            hints.append("ignore adjacent XML comment formatting noise when it stays attached to the same declaration")
        return tuple(hints)

    @staticmethod
    def _rule_specific_review_hints(rule_id: str) -> tuple[str, ...]:
        normalized_rule_id = str(rule_id or "").strip()
        if normalized_rule_id == "csharpsquid:S125":
            return (
                "allow immediate adjacent cleanup when removing commented-out code would otherwise leave a dead local variable",
            )
        return ()

    @staticmethod
    def _line_range_symbol_name(label: str, start_line: int, end_line: int) -> str:
        return f"{label}@{start_line}-{end_line}"

    @staticmethod
    def _find_previous_non_empty_line(source_lines: list[str], start_line: int) -> int | None:
        for line_number in range(min(start_line, len(source_lines)), 1 - 1, -1):
            if source_lines[line_number - 1].strip():
                return line_number
        return None

    @staticmethod
    def _trim_range_to_non_empty_content(
        source_lines: list[str],
        start_line: int,
        end_line: int,
    ) -> tuple[int, int]:
        normalized_start = max(1, min(start_line, end_line))
        normalized_end = min(len(source_lines), max(start_line, end_line))
        if normalized_start > normalized_end:
            return start_line, end_line

        while normalized_start <= normalized_end and not source_lines[normalized_start - 1].strip():
            normalized_start += 1
        while normalized_end >= normalized_start and not source_lines[normalized_end - 1].strip():
            normalized_end -= 1

        if normalized_start > normalized_end:
            return start_line, end_line
        return normalized_start, normalized_end

    @staticmethod
    def _looks_like_local_declaration(
        source_lines: list[str],
        start_line: int,
        end_line: int,
    ) -> bool:
        snippet = " ".join(
            line.strip()
            for line in source_lines[start_line - 1: end_line]
            if line.strip()
        )
        normalized = snippet.strip()
        if not normalized or not normalized.endswith(";"):
            return False
        lowered = normalized.lower()
        if lowered.startswith(("return ", "if ", "for ", "foreach ", "while ", "switch ", "case ", "throw ")):
            return False
        return " = " in normalized or normalized.startswith("var ")

    @staticmethod
    def _dedupe_symbols(
        symbols: tuple[ContractTargetSymbol, ...] | list[ContractTargetSymbol],
    ) -> tuple[ContractTargetSymbol, ...]:
        results: list[ContractTargetSymbol] = []
        seen: set[tuple[str, str, int, int]] = set()
        for symbol in symbols:
            key = (symbol.file, symbol.symbol, symbol.start_line, symbol.end_line)
            if key in seen:
                continue
            results.append(symbol)
            seen.add(key)
        return tuple(results)

    @staticmethod
    def _dedupe_ranges(
        ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    ) -> tuple[tuple[int, int], ...]:
        normalized: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for start_line, end_line in ranges:
            if start_line <= 0 or end_line <= 0:
                continue
            value = (min(start_line, end_line), max(start_line, end_line))
            if value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return tuple(normalized)

    @staticmethod
    def _format_numbered_snippet(
        source_lines: list[str],
        start_line: int,
        end_line: int,
    ) -> str:
        bounded_start = max(1, min(start_line, end_line))
        bounded_end = min(len(source_lines), max(start_line, end_line))
        if bounded_start > bounded_end or not source_lines:
            return ""
        return "\n".join(
            f"{line_number:4d} | {source_lines[line_number - 1]}"
            for line_number in range(bounded_start, bounded_end + 1)
        )

    @staticmethod
    def _dedupe_context_snippets(
        snippets: tuple[ContractContextSnippet, ...] | list[ContractContextSnippet],
    ) -> tuple[ContractContextSnippet, ...]:
        results: list[ContractContextSnippet] = []
        seen: set[tuple[str, str, int, int]] = set()
        for snippet in snippets:
            key = (snippet.file, snippet.label, snippet.start_line, snippet.end_line)
            if key in seen:
                continue
            results.append(snippet)
            seen.add(key)
        return tuple(results)

    @classmethod
    def _build_prefetched_context(
        cls,
        *,
        normalized_path: str,
        issue_line: int,
        source_lines: list[str],
        source_file_map: dict[str, list[str]] | None,
        validation_line_range: tuple[int, int],
        allowed_line_ranges: tuple[tuple[int, int], ...],
        allowed_related_symbols: tuple[ContractTargetSymbol, ...],
        fast_path_enabled: bool,
    ) -> tuple[ContractContextSnippet, ...]:
        if not source_lines:
            return ()

        snippets: list[ContractContextSnippet] = []
        snippet_budget = 6 if fast_path_enabled else 4

        issue_window_start = max(1, issue_line - 2)
        issue_window_end = min(len(source_lines), issue_line + 2)
        issue_window_content = cls._format_numbered_snippet(
            source_lines,
            issue_window_start,
            issue_window_end,
        )
        if issue_window_content:
            snippets.append(
                ContractContextSnippet(
                    file=normalized_path,
                    label="issue_window",
                    reason="Primary issue window around the Sonar line.",
                    start_line=issue_window_start,
                    end_line=issue_window_end,
                    content=issue_window_content,
                )
            )

        for symbol in allowed_related_symbols:
            symbol_lines = source_lines if symbol.file == normalized_path else (source_file_map or {}).get(symbol.file, [])
            content = cls._format_numbered_snippet(symbol_lines, symbol.start_line, symbol.end_line)
            if not content:
                continue
            snippets.append(
                ContractContextSnippet(
                    file=symbol.file,
                    label=symbol.symbol,
                    reason=symbol.reason,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    content=content,
                )
            )

        for start_line, end_line in allowed_line_ranges:
            if validation_line_range and (start_line, end_line) == validation_line_range:
                continue
            content = cls._format_numbered_snippet(source_lines, start_line, end_line)
            if not content:
                continue
            snippets.append(
                ContractContextSnippet(
                    file=normalized_path,
                    label=f"allowed_range@{start_line}-{end_line}",
                    reason="Additional allowed range declared by the current edit contract.",
                    start_line=start_line,
                    end_line=end_line,
                    content=content,
                )
            )

        return cls._dedupe_context_snippets(snippets[:snippet_budget])

    @classmethod
    def _resolve_contract_ranges(
        cls,
        *,
        normalized_path: str,
        boundary_profile: str,
        issue_line: int,
        scope_start_line: int,
        scope_end_line: int,
        validation_line_range: tuple[int, int],
        source_lines: list[str],
    ) -> tuple[tuple[tuple[int, int], ...], tuple[ContractTargetSymbol, ...]]:
        allowed_ranges: list[tuple[int, int]] = []
        related_symbols: list[ContractTargetSymbol] = []
        if validation_line_range:
            allowed_ranges.append(validation_line_range)
        if not source_lines:
            return cls._dedupe_ranges(allowed_ranges), ()

        def add_related(label: str, start_line: int, end_line: int, reason: str) -> None:
            if start_line <= 0 or end_line <= 0:
                return
            allowed_ranges.append((start_line, end_line))
            related_symbols.append(
                ContractTargetSymbol(
                    file=normalized_path,
                    symbol=cls._line_range_symbol_name(label, start_line, end_line),
                    reason=reason,
                    start_line=start_line,
                    end_line=end_line,
                )
            )

        if boundary_profile == BOUNDARY_PROFILE_DECLARATION_ANCHOR:
            previous_non_empty = cls._find_previous_non_empty_line(source_lines, issue_line - 1)
            if previous_non_empty is not None:
                previous_start, previous_end = LegacyScopeGuard._find_enclosing_statement_range(
                    source_lines,
                    previous_non_empty,
                )
                previous_start, previous_end = cls._trim_range_to_non_empty_content(
                    source_lines,
                    previous_start,
                    previous_end,
                )
                if (
                    previous_end < issue_line
                    and issue_line - previous_end <= 2
                    and cls._looks_like_local_declaration(source_lines, previous_start, previous_end)
                ):
                    add_related(
                        "declaration_anchor",
                        previous_start,
                        previous_end,
                        "Nearby declaration anchor range for delete-only local cleanup.",
                    )
                    return cls._dedupe_ranges(allowed_ranges), cls._dedupe_symbols(related_symbols)

            candidate_lines: list[int] = []
            for candidate_line in (
                issue_line,
                max(issue_line - 1, 1),
                min(issue_line + 1, len(source_lines)),
            ):
                if 1 <= candidate_line <= len(source_lines) and candidate_line not in candidate_lines:
                    candidate_lines.append(candidate_line)

            if previous_non_empty is not None and previous_non_empty not in candidate_lines:
                candidate_lines.append(previous_non_empty)

            for candidate_line in candidate_lines:
                start_line, end_line = LegacyScopeGuard._find_enclosing_statement_range(
                    source_lines,
                    candidate_line,
                )
                start_line, end_line = cls._trim_range_to_non_empty_content(
                    source_lines,
                    start_line,
                    end_line,
                )
                if end_line < issue_line and issue_line - end_line <= 2:
                    add_related(
                        "declaration_anchor",
                        start_line,
                        end_line,
                        "Nearby declaration anchor range for delete-only local cleanup.",
                    )
                    break
                if (
                    start_line <= issue_line <= end_line
                    or end_line >= max(validation_line_range[0] - 1, 1)
                ):
                    add_related(
                        "declaration_anchor",
                        start_line,
                        end_line,
                        "Declaration anchor range for delete-only local cleanup.",
                    )
                    break

        elif boundary_profile == BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP and validation_line_range:
            previous_non_empty = cls._find_previous_non_empty_line(
                source_lines,
                validation_line_range[0] - 1,
            )
            if previous_non_empty is not None:
                start_line, end_line = LegacyScopeGuard._find_enclosing_statement_range(
                    source_lines,
                    previous_non_empty,
                )
                start_line, end_line = cls._trim_range_to_non_empty_content(
                    source_lines,
                    start_line,
                    end_line,
                )
                if end_line < validation_line_range[0] and validation_line_range[0] - end_line <= 2:
                    add_related(
                        "adjacent_cleanup",
                        start_line,
                        end_line,
                        "Immediate adjacent cleanup range coupled to the commented-out code.",
                    )

        elif boundary_profile == BOUNDARY_PROFILE_MEMBER_CLUSTER:
            search_line = max(scope_end_line + 1, 1)
            cluster_end = scope_end_line
            while search_line <= len(source_lines):
                next_line = LegacyScopeGuard._find_next_non_empty_line(source_lines, search_line)
                if next_line is None:
                    break
                gap_lines = source_lines[max(cluster_end, 0): max(next_line - 1, 0)]
                if any(line.strip() and not line.strip().startswith(("//", "///", "/*", "*")) for line in gap_lines):
                    break
                method_range = LegacyScopeGuard._find_enclosing_method_range(source_lines, next_line)
                if method_range is None or method_range[0] <= cluster_end:
                    break
                header_text = " ".join(
                    line.strip()
                    for line in source_lines[method_range[0] - 1: min(len(source_lines), method_range[0] + 3)]
                    if line.strip()
                )
                if not cls._PRIVATE_METHOD_PATTERN.search(header_text):
                    break
                add_related(
                    "method_cluster",
                    method_range[0],
                    method_range[1],
                    "Adjacent private helper range that may become unused with the primary member.",
                )
                cluster_end = method_range[1]
                search_line = method_range[1] + 1

        return cls._dedupe_ranges(allowed_ranges), cls._dedupe_symbols(related_symbols)

    @staticmethod
    def _format_quality_gate_rules(
        edit_contract: EditContract,
        *,
        enforcement: str,
    ) -> str:
        rules = tuple(
            rule for rule in edit_contract.quality_gate_rules if rule.enforcement == enforcement
        )
        if not rules:
            return ""

        label = "Hard Quality Gates" if enforcement == "hard" else "Soft Quality Signals"
        return (
            f"- {label}: "
            + "; ".join(f"{rule.rule_id} ({rule.title})" for rule in rules)
        )

    @staticmethod
    def _format_quality_gate_hints(edit_contract: EditContract) -> str:
        hints = [
            f"{rule.rule_id}: {rule.prompt_hint}"
            for rule in edit_contract.quality_gate_rules
            if str(rule.prompt_hint).strip()
        ]
        if not hints:
            return ""
        return "- Quality Gate Notes: " + " | ".join(hints)

    @classmethod
    def _parse_method_header(
        cls,
        source_lines: list[str],
        start_line: int,
        end_line: int,
    ) -> dict[str, object] | None:
        if not source_lines or start_line <= 0 or end_line <= 0:
            return None
        header_lines: list[str] = []
        for line_number in range(start_line, min(end_line, start_line + 6) + 1):
            text = source_lines[line_number - 1].strip()
            if not text:
                continue
            header_lines.append(text)
            if "{" in text:
                break
        if not header_lines:
            return None
        header_text = " ".join(header_lines)
        name_matches = cls._METHOD_NAME_PATTERN.findall(header_text)
        if not name_matches:
            return None
        method_name = name_matches[-1]
        normalized_header = f" {header_text} "
        access = next(
            (
                token
                for token in ("public", "private", "protected", "internal")
                if f" {token} " in normalized_header
            ),
            "",
        )
        is_async = " async " in normalized_header
        prefix = header_text.split(f"{method_name}(", 1)[0].strip()
        return_type = prefix.split()[-1] if prefix else ""
        return {
            "name": method_name,
            "access": access,
            "is_async": is_async,
            "return_type": return_type,
            "signature": header_text,
            "start_line": start_line,
            "end_line": end_line,
        }

    @classmethod
    def _find_primary_method_descriptor(
        cls,
        source_lines: list[str],
        issue_line: int,
        scope_start_line: int,
        scope_end_line: int,
    ) -> dict[str, object] | None:
        if not source_lines:
            return None
        method_range = LegacyScopeGuard._find_enclosing_method_range(source_lines, issue_line)
        if method_range is None and scope_start_line > 0 and scope_end_line > 0:
            method_range = (scope_start_line, scope_end_line)
        if method_range is None:
            return None
        parsed = cls._parse_method_header(source_lines, method_range[0], method_range[1])
        if parsed is not None:
            return parsed

        search_start = max(1, method_range[0] - 3)
        for candidate_start in range(search_start, method_range[0] + 1):
            parsed = cls._parse_method_header(source_lines, candidate_start, method_range[1])
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _iter_workspace_cs_files(workspace_path: Path) -> tuple[Path, ...]:
        if not workspace_path.exists():
            return ()
        protected_dirs = {".git", ".agent_workspaces", "logs", "bin", "obj", "__pycache__"}
        files: list[Path] = []
        for path in workspace_path.rglob("*.cs"):
            try:
                relative_parts = path.relative_to(workspace_path).parts
            except ValueError:
                relative_parts = path.parts
            if any(part in protected_dirs for part in relative_parts):
                continue
            files.append(path)
        return tuple(sorted(files))

    @staticmethod
    def _normalize_workspace_relative_path(workspace_path: Path, path: Path) -> str:
        return str(path.relative_to(workspace_path)).replace("\\", "/")

    @staticmethod
    def _load_workspace_source_map(
        workspace_path: Path | None,
        normalized_paths: tuple[str, ...] | list[str],
    ) -> dict[str, list[str]]:
        if workspace_path is None:
            return {}

        source_map: dict[str, list[str]] = {}
        for normalized_path in normalized_paths:
            relative_path = str(normalized_path or "").replace("\\", "/").lstrip("/")
            if not relative_path:
                continue
            candidate = workspace_path / relative_path
            if not candidate.is_file():
                continue
            try:
                source_map[relative_path] = candidate.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
        return source_map

    @staticmethod
    def _looks_like_method_declaration_line(line: str, method_name: str) -> bool:
        stripped = str(line or "").strip()
        if not stripped or f"{method_name}(" not in stripped:
            return False
        if any(
            stripped.startswith(prefix)
            for prefix in ("await ", "return ", "if ", "for ", "foreach ", "while ", "switch ", "case ")
        ):
            return False
        normalized = f" {stripped} "
        declaration_tokens = (
            " public ",
            " private ",
            " protected ",
            " internal ",
            " virtual ",
            " override ",
            " async ",
            " Task ",
            " Task<",
            " ValueTask ",
            " ValueTask<",
        )
        if any(token in normalized for token in declaration_tokens):
            return True
        if stripped.endswith(";"):
            prefix = stripped.split(f"{method_name}(", 1)[0].strip()
            if not prefix:
                return False
            first_token = prefix.split()[0]
            return first_token not in {"await", "return", "if", "for", "foreach", "while", "switch", "case"}
        return False

    @classmethod
    def _scan_signature_propagation_targets(
        cls,
        *,
        workspace_path: Path | None,
        normalized_path: str,
        method_descriptor: dict[str, object] | None,
        scope_start_line: int,
        scope_end_line: int,
    ) -> tuple[RepairPropagationTarget, ...]:
        if workspace_path is None or method_descriptor is None:
            return ()

        method_name = str(method_descriptor.get("name", "")).strip()
        proposed_method_name = str(method_descriptor.get("proposed_name", "")).strip()
        if not method_name or not proposed_method_name or method_name == proposed_method_name:
            return ()

        issue_path = cls._normalize_path(normalized_path)
        results: list[RepairPropagationTarget] = []
        seen: set[tuple[str, str, int, int]] = set()

        for path in cls._iter_workspace_cs_files(workspace_path):
            relative_path = cls._normalize_workspace_relative_path(workspace_path, path)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for line_number, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("//") or stripped.startswith("///"):
                    continue

                if relative_path == issue_path and scope_start_line <= line_number <= scope_end_line:
                    continue

                kind = ""
                reason = ""
                if f"nameof({method_name})" in stripped:
                    kind = "nameof_ref"
                    reason = (
                        f"`nameof({method_name})` will need to follow the renamed async method "
                        f"`{proposed_method_name}`."
                    )
                elif re.search(rf"\b{re.escape(method_name)}\s*\(", stripped):
                    if cls._looks_like_method_declaration_line(stripped, method_name):
                        if relative_path == issue_path:
                            continue
                        kind = "signature_declaration"
                        reason = (
                            f"Declaration or interface member referencing `{method_name}` must stay aligned "
                            f"with `{proposed_method_name}`."
                        )
                    else:
                        kind = "callsite"
                        reason = (
                            f"Callsite invoking `{method_name}` must be updated when the method is renamed "
                            f"to `{proposed_method_name}`."
                        )
                else:
                    continue

                symbol = f"{kind}@{line_number}-{line_number}"
                key = (relative_path, symbol, line_number, line_number)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    RepairPropagationTarget(
                        file=relative_path,
                        symbol=symbol,
                        kind=kind,
                        reason=reason,
                        start_line=line_number,
                        end_line=line_number,
                    )
                )

        return tuple(results)

    @classmethod
    def _should_enable_plan_first(
        cls,
        *,
        rule_id: str,
        retry_context: RetryContext | None,
        allowed_capabilities: tuple[str, ...],
        fast_path_enabled: bool,
        performance_flags: PerformanceFlags,
    ) -> bool:
        if not performance_flags.plan_first_complex_rules:
            return False
        if fast_path_enabled:
            return False
        if retry_context is not None and retry_context.failure_kind == "plan_conflict":
            return True
        normalized_rule_id = str(rule_id or "").strip()
        if normalized_rule_id in cls._PLAN_FIRST_RULES:
            return True
        return any(item in cls._PLAN_FIRST_COMPLEX_CAPABILITIES for item in allowed_capabilities)

    @classmethod
    def _build_repair_plan(
        cls,
        *,
        rule_id: str,
        workspace_path: Path | None,
        normalized_path: str,
        target_symbol: ContractTargetSymbol,
        allowed_related_symbols: tuple[ContractTargetSymbol, ...],
        allowed_capabilities: tuple[str, ...],
        quality_gate_rules,
        source_lines: list[str],
        issue_line: int,
        scope_start_line: int,
        scope_end_line: int,
    ) -> RepairPlan:
        normalized_rule_id = str(rule_id or "").strip()
        repair_shape = "statement_fix"
        if METHOD_CLUSTER_DELETE_CAPABILITY in allowed_capabilities:
            repair_shape = "member_cluster_delete"
        elif HELPER_EXTRACT_CAPABILITY in allowed_capabilities:
            repair_shape = "method_rewrite_with_helpers"
        elif SIGNATURE_CHANGE_CAPABILITY in allowed_capabilities:
            repair_shape = "signature_adjustment"

        method_descriptor = cls._find_primary_method_descriptor(
            source_lines,
            issue_line,
            scope_start_line,
            scope_end_line,
        )
        expected_quality_gates = tuple(rule.rule_id for rule in quality_gate_rules)
        requires_signature_change = False
        requires_propagation = False
        risk_notes: list[str] = []
        helper_plans: list[RepairHelperPlan] = []
        new_helpers: list[str] = []
        method_name = ""
        proposed_method_name = ""
        propagation_targets: tuple[RepairPropagationTarget, ...] = ()

        if method_descriptor is not None:
            method_name = str(method_descriptor.get("name", "")).strip()
            if (
                method_name
                and "async_signature" in expected_quality_gates
                and bool(method_descriptor.get("is_async"))
                and not method_name.endswith("Async")
            ):
                requires_signature_change = True
                proposed_method_name = f"{method_name}Async"
                requires_propagation = str(method_descriptor.get("access", "")).strip() in {
                    "public",
                    "protected",
                    "internal",
                }
                method_descriptor["proposed_name"] = proposed_method_name
                risk_notes.append(
                    f"目标方法 {method_name} 是异步方法但未使用 Async 后缀；如当前 patch 触达该方法签名，可能需要显式允许 signature_change。"
                )
                if requires_propagation:
                    propagation_targets = cls._scan_signature_propagation_targets(
                        workspace_path=workspace_path,
                        normalized_path=normalized_path,
                        method_descriptor=method_descriptor,
                        scope_start_line=scope_start_line,
                        scope_end_line=scope_end_line,
                    )
                    if propagation_targets:
                        risk_notes.append(
                            f"公开方法签名改名预计需要同步 {len(propagation_targets)} 处接口/调用点/nameof 传播目标。"
                        )
                    else:
                        risk_notes.append(
                            "目标方法是公开方法；如果需要改名，必须显式识别并同步接口、调用点和 nameof 引用。"
                        )

        if normalized_rule_id == "csharpsquid:S3776" and HELPER_EXTRACT_CAPABILITY in allowed_capabilities:
            new_helpers.append("private helper(s) extracted from the target method")
            helper_plans.append(
                RepairHelperPlan(
                    name="extracted_private_helper",
                    is_async=False,
                    reason="Only keep helpers async when they contain a real await.",
                    await_source="Prefer sync helper extraction unless the helper owns an awaited call.",
                )
            )
            risk_notes.append("提取 helper 时，只有真实包含 await 的 helper 才保留 async；否则改为同步 helper。")

        if METHOD_CLUSTER_DELETE_CAPABILITY in allowed_capabilities:
            risk_notes.append("删除未使用 private 成员时，只允许扩大到合同已声明的紧邻 private helper cluster。")

        target_symbols = [target_symbol.symbol]
        target_symbols.extend(symbol.symbol for symbol in allowed_related_symbols)
        if method_descriptor is not None:
            method_name = str(method_descriptor.get("name", "")).strip()
            if method_name and method_name not in target_symbols:
                target_symbols.insert(0, method_name)

        return RepairPlan(
            repair_shape=repair_shape,
            primary_file=normalized_path,
            primary_method_name=method_name,
            proposed_method_name=proposed_method_name,
            target_symbols=tuple(dict.fromkeys(item for item in target_symbols if str(item).strip())),
            new_helpers=tuple(dict.fromkeys(new_helpers)),
            helper_async_map=tuple(helper_plans),
            requires_signature_change=requires_signature_change,
            requires_propagation=requires_propagation,
            requires_new_type=False,
            propagation_targets=propagation_targets,
            expected_boundary_capabilities=tuple(allowed_capabilities),
            expected_quality_gates=expected_quality_gates,
            risk_notes=tuple(dict.fromkeys(risk_notes)),
        )

    @classmethod
    def _precheck_repair_plan(
        cls,
        *,
        repair_plan: RepairPlan | None,
        allowed_capabilities: tuple[str, ...],
    ) -> PlanPrecheckResult:
        if repair_plan is None:
            return PlanPrecheckResult()

        details: list[str] = []
        guidance: list[str] = []
        if repair_plan.requires_signature_change and SIGNATURE_CHANGE_CAPABILITY not in allowed_capabilities:
            details.append("当前结构化 plan 预计需要修改方法签名/名称，但 EditContract 未声明 signature_change capability。")
            guidance.append("如果该规则必须修改方法名或方法签名，请先让 planner/contract 显式放开 signature_change。")
            guidance.append("如果不允许改签名，则应在 edit 前调整计划，避免进入必然失败的 attempt。")
            return PlanPrecheckResult(
                status="conflict",
                blocking=True,
                code="signature_change_not_allowed",
                summary="Plan 预检发现本次修复需要 signature_change，但当前 contract 不允许该能力。",
                details=tuple(details),
                guidance=tuple(guidance),
            )

        if repair_plan.requires_propagation and not repair_plan.propagation_targets:
            details.append("当前结构化 plan 预计需要对公开方法做签名传播，但尚未识别到接口/调用点/nameof 传播目标。")
            guidance.append("先识别需要联动的接口声明、调用点和 nameof 引用，再进入 edit。")
            guidance.append("如果没有可靠的传播目标，请保持当前公开方法签名不变，避免进入必然失败的 attempt。")
            return PlanPrecheckResult(
                status="conflict",
                blocking=True,
                code="signature_propagation_targets_missing",
                summary="Plan 预检发现本次修复需要签名传播，但当前尚未识别到传播目标。",
                details=tuple(details),
                guidance=tuple(guidance),
            )

        if (
            repair_plan.requires_propagation
            and any(
                target.file and target.file != repair_plan.primary_file
                for target in repair_plan.propagation_targets
            )
            and MULTI_FILE_REFACTOR_CAPABILITY not in allowed_capabilities
        ):
            details.append("当前结构化 plan 预计需要联动修改接口或其他文件中的调用点，但 EditContract 未声明 multi_file_refactor capability。")
            guidance.append("如果签名变更会传播到接口/调用点，请先让 planner/contract 显式放开 multi_file_refactor。")
            guidance.append("如果不允许联动修改其他文件，则应保持当前公开方法签名不变。")
            return PlanPrecheckResult(
                status="conflict",
                blocking=True,
                code="signature_propagation_not_allowed",
                summary="Plan 预检发现本次修复需要多文件签名传播，但当前 contract 不允许该能力。",
                details=tuple(details),
                guidance=tuple(guidance),
            )

        if repair_plan.requires_new_type and NEW_TYPE_ADD_CAPABILITY not in allowed_capabilities:
            details.append("当前结构化 plan 预计需要新增类型，但 EditContract 未声明 new_type_add capability。")
            guidance.append("如确需新增类型，先放开 new_type_add；否则继续保持单文件/无新类型的修复形状。")
            return PlanPrecheckResult(
                status="conflict",
                blocking=True,
                code="new_type_not_allowed",
                summary="Plan 预检发现本次修复需要新增类型，但当前 contract 不允许该能力。",
                details=tuple(details),
                guidance=tuple(guidance),
            )

        details.append("Plan 与当前 contract/quality gate 的显式前置约束没有发现直接冲突。")
        return PlanPrecheckResult(
            status="pass",
            blocking=False,
            code="plan_precheck_passed",
            summary="Plan 预检通过，可以进入 edit 阶段。",
            details=tuple(details),
        )

    @staticmethod
    def _lesson_review_hints(planner_lessons: tuple[PlannerLesson, ...]) -> tuple[str, ...]:
        hints: list[str] = []
        for lesson in planner_lessons:
            for guidance in lesson.guidance:
                text = str(guidance or "").strip()
                if text and text not in hints:
                    hints.append(text)
        return tuple(hints)

    @classmethod
    def _apply_boundary_lessons(
        cls,
        *,
        planner_lessons: tuple[PlannerLesson, ...],
        normalized_path: str,
        validation_line_range: tuple[int, int],
        source_lines: list[str],
        allowed_line_ranges: tuple[tuple[int, int], ...],
        allowed_related_symbols: tuple[ContractTargetSymbol, ...],
    ) -> tuple[tuple[tuple[int, int], ...], tuple[ContractTargetSymbol, ...]]:
        ranges = list(allowed_line_ranges)
        symbols = list(allowed_related_symbols)
        if not validation_line_range:
            return cls._dedupe_ranges(ranges), cls._dedupe_symbols(symbols)

        lesson_codes = {
            str(lesson.boundary_failure_code or "").strip()
            for lesson in planner_lessons
            if str(lesson.boundary_failure_code or "").strip()
        }

        def add_lesson_range(label: str, start_line: int, end_line: int, reason: str) -> None:
            if start_line <= 0 or end_line <= 0:
                return
            ranges.append((start_line, end_line))
            symbols.append(
                ContractTargetSymbol(
                    file=normalized_path,
                    symbol=cls._line_range_symbol_name(label, start_line, end_line),
                    reason=reason,
                    start_line=start_line,
                    end_line=end_line,
                )
            )

        if "scope_symbol_anchor_miss" in lesson_codes and not any(
            symbol.symbol.startswith("declaration_anchor@") for symbol in symbols
        ):
            previous_non_empty = cls._find_previous_non_empty_line(source_lines, validation_line_range[0] - 1)
            candidate_line = previous_non_empty or max(validation_line_range[0] - 1, 1)
            add_lesson_range(
                "declaration_anchor_lesson",
                candidate_line,
                candidate_line,
                "Fallback declaration anchor learned from recent boundary failures.",
            )

        if "adjacent_cleanup_not_declared" in lesson_codes and not any(
            symbol.symbol.startswith("adjacent_cleanup@")
            or symbol.symbol.startswith("adjacent_cleanup_lesson@")
            for symbol in symbols
        ):
            previous_non_empty = cls._find_previous_non_empty_line(source_lines, validation_line_range[0] - 1)
            candidate_line = previous_non_empty or max(validation_line_range[0] - 1, 1)
            add_lesson_range(
                "adjacent_cleanup_lesson",
                candidate_line,
                candidate_line,
                "Fallback adjacent cleanup anchor learned from recent boundary failures.",
            )

        return cls._dedupe_ranges(ranges), cls._dedupe_symbols(symbols)

    @staticmethod
    def render_contract_guidance(edit_contract: EditContract) -> str:
        """Render a prompt-friendly summary of the edit contract."""

        lines = [
            "【Edit Contract】",
            f"- Issue Key: {edit_contract.issue_key}",
            f"- Guardrail Mode: {edit_contract.guardrail_mode}",
            "- Target Files: " + ", ".join(edit_contract.target_files),
        ]
        if edit_contract.target_symbols:
            lines.append(
                "- Target Symbols: "
                + "; ".join(
                    f"{symbol.symbol} ({symbol.reason})"
                    for symbol in edit_contract.target_symbols
                )
            )
        if edit_contract.allowed_related_symbols:
            lines.append(
                "- Related Symbols: "
                + "; ".join(
                    f"{symbol.symbol} ({symbol.reason})"
                    for symbol in edit_contract.allowed_related_symbols
                )
            )
        if edit_contract.prefetched_context:
            lines.append(
                "- Prefetched Context: "
                + "; ".join(
                    f"{snippet.label} [{snippet.start_line}-{snippet.end_line}]"
                    for snippet in edit_contract.prefetched_context
                )
            )
        if edit_contract.boundary_profile:
            lines.append(f"- Boundary Profile: {edit_contract.boundary_profile}")
        lines.append(f"- Execution Profile: {edit_contract.execution_profile}")
        if edit_contract.rollout_flags:
            lines.append("- Rollout Flags: " + ", ".join(edit_contract.rollout_flags))
        if edit_contract.allowed_capabilities:
            lines.append("- Allowed Capabilities: " + ", ".join(edit_contract.allowed_capabilities))
        if edit_contract.allowed_change_kinds:
            lines.append("- Allowed Change Kinds: " + ", ".join(edit_contract.allowed_change_kinds))
        if edit_contract.forbidden_change_kinds:
            lines.append("- Forbidden Change Kinds: " + ", ".join(edit_contract.forbidden_change_kinds))
        if edit_contract.validation_line_range:
            lines.append(
                "- Validation Window: "
                f"{edit_contract.validation_line_range[0]}-{edit_contract.validation_line_range[1]}"
            )
        hard_quality_gates = IssuePlanner._format_quality_gate_rules(edit_contract, enforcement="hard")
        if hard_quality_gates:
            lines.append(hard_quality_gates)
        soft_quality_gates = IssuePlanner._format_quality_gate_rules(edit_contract, enforcement="soft")
        if soft_quality_gates:
            lines.append(soft_quality_gates)
        quality_gate_hints = IssuePlanner._format_quality_gate_hints(edit_contract)
        if quality_gate_hints:
            lines.append(quality_gate_hints)
        for lesson in edit_contract.planner_lessons:
            detail = f"- Recent Lesson [{lesson.source} x{lesson.count}]: {lesson.summary}"
            if lesson.guidance:
                detail += f" | Guidance: {lesson.guidance[0]}"
            lines.append(detail)
        lines.append("- Follow Up Policy: " + edit_contract.follow_up_policy)
        if edit_contract.patch_only:
            lines.append("- Editing Mode: patch-only, prefer Edit/MultiEdit over whole-file rewrite")
        return "\n".join(lines)

    @staticmethod
    def render_repair_plan_guidance(edit_contract: EditContract) -> str:
        """Render the structured repair plan for complex rules when enabled."""

        repair_plan = getattr(edit_contract, "repair_plan", None)
        if repair_plan is None or not bool(getattr(edit_contract, "plan_first_enabled", False)):
            return ""

        lines = [
            "【Repair Plan】",
            "- 当前规则启用 Plan-First 模式，先遵守下面的结构化修复形状，再开始编辑。",
            f"- Repair Shape: {repair_plan.repair_shape}",
        ]
        if repair_plan.target_symbols:
            lines.append("- Target Symbols: " + ", ".join(repair_plan.target_symbols))
        if repair_plan.primary_method_name:
            lines.append(f"- Primary Method: {repair_plan.primary_method_name}")
        if repair_plan.proposed_method_name:
            lines.append(f"- Proposed Method Name: {repair_plan.proposed_method_name}")
        if repair_plan.new_helpers:
            lines.append("- Planned Helpers: " + ", ".join(repair_plan.new_helpers))
        if repair_plan.helper_async_map:
            lines.append(
                "- Helper Async Map: "
                + "; ".join(
                    f"{helper.name}={'async' if helper.is_async else 'sync'} ({helper.reason})"
                    for helper in repair_plan.helper_async_map
                )
            )
        lines.append(
            "- Requires Signature Change: "
            + ("yes" if repair_plan.requires_signature_change else "no")
        )
        lines.append(
            "- Requires New Type: " + ("yes" if repair_plan.requires_new_type else "no")
        )
        lines.append(
            "- Requires Propagation: " + ("yes" if repair_plan.requires_propagation else "no")
        )
        if repair_plan.propagation_targets:
            lines.append(
                "- Propagation Targets: "
                + "; ".join(
                    f"{target.file}:{target.symbol} ({target.reason})"
                    for target in repair_plan.propagation_targets
                )
            )
        if repair_plan.expected_boundary_capabilities:
            lines.append(
                "- Expected Boundary Capabilities: "
                + ", ".join(repair_plan.expected_boundary_capabilities)
            )
        if repair_plan.expected_quality_gates:
            lines.append(
                "- Expected Quality Gates: " + ", ".join(repair_plan.expected_quality_gates)
            )
        for note in repair_plan.risk_notes:
            lines.append(f"- Risk Note: {note}")

        plan_precheck = getattr(edit_contract, "plan_precheck", None)
        if plan_precheck is not None:
            lines.append(
                f"- Plan Precheck: {plan_precheck.status} ({plan_precheck.code or 'no-code'})"
            )
            if plan_precheck.summary:
                lines.append(f"- Plan Precheck Summary: {plan_precheck.summary}")
            for detail in getattr(plan_precheck, "details", ()) or ():
                lines.append(f"- Plan Detail: {detail}")
            for guidance in getattr(plan_precheck, "guidance", ()) or ():
                lines.append(f"- Plan Guidance: {guidance}")

        return "\n".join(lines)

    @classmethod
    def _should_enable_fast_path(
        cls,
        *,
        rule_id: str,
        retry_context: RetryContext | None,
        allowed_capabilities: tuple[str, ...],
        allowed_related_symbols: tuple[ContractTargetSymbol, ...],
        performance_flags: PerformanceFlags,
    ) -> bool:
        if not performance_flags.fast_path:
            return False
        if retry_context is not None:
            return False
        if str(rule_id or "").strip() not in cls._FAST_PATH_RULES:
            return False
        if any(
            item in {"signature_change", "helper_extract", "new_type_add", "multi_file_refactor"}
            for item in allowed_capabilities
        ):
            return False
        return len(allowed_related_symbols) <= 2

    @classmethod
    def plan_issue(
        cls,
        *,
        issue_key: str,
        rule_id: str,
        file_path: str,
        issue_line: int,
        guardrail_mode: str,
        scope_mode: str = "",
        scope_start_line: int = 0,
        scope_end_line: int = 0,
        validation_start_line: int = 0,
        validation_end_line: int = 0,
        source_lines: tuple[str, ...] | list[str] | None = None,
        workspace_path: Path | None = None,
        retry_context: RetryContext | None = None,
        workspace_rules: str = "",
        quality_gate_catalog: QualityGateCatalog | None = None,
        lessons_store: LessonsStore | None = None,
        performance_flags: PerformanceFlags | None = None,
    ) -> IssuePlan:
        """Build an issue plan and edit contract from runtime context."""

        normalized_path = cls._normalize_path(file_path)
        catalog = quality_gate_catalog or load_default_quality_gate_catalog()
        effective_performance_flags = performance_flags or load_performance_flags()
        quality_gate_rules = catalog.rules_for_path(normalized_path)
        normalized_scope_mode = str(scope_mode or STATEMENT_SCOPE_MODE)
        policy = get_rule_policy(rule_id)
        boundary_profile = resolve_boundary_profile(
            normalized_scope_mode,
            policy.boundary_profile,
        )
        allowed_capabilities = resolve_boundary_capabilities(
            normalized_scope_mode,
            policy.boundary_capabilities,
        )
        planner_lessons: tuple[PlannerLesson, ...] = ()
        if retry_context is not None:
            planner_lessons = (lessons_store or LessonsStore()).load_planner_lessons(
                issue_rule_id=rule_id,
                failure_kind=retry_context.failure_kind,
                scope_mode=normalized_scope_mode,
                guardrail_mode=guardrail_mode,
                boundary_failure_code=(
                    retry_context.boundary_failure.code
                    if retry_context.boundary_failure is not None
                    else ""
                ),
                quality_gate_rule_ids=tuple(rule.rule_id for rule in quality_gate_rules),
            )
        symbol = ContractTargetSymbol(
            file=normalized_path,
            symbol=cls._infer_symbol_name(normalized_scope_mode, scope_start_line, scope_end_line),
            reason=f"Sonar issue is located near line {issue_line}",
            start_line=scope_start_line,
            end_line=scope_end_line,
        )
        validation_plan = ("build", "diff_review")
        if guardrail_mode == "scope":
            validation_plan = ("build", "scope_review", "diff_review")

        forbidden_change_kinds = (
            "drive-by-refactor",
            "whole-file-format",
            "touch-unrelated-files",
            "touch-unrelated-tests",
        )
        if retry_context and retry_context.failure_kind == "no_change":
            strategy = "apply a concrete minimal fix and immediately validate it"
        elif retry_context and retry_context.failure_kind in {"build", "build_tool"}:
            strategy = "repair the last failed patch with the smallest compile-safe delta"
        else:
            strategy = "apply the smallest issue-focused patch"

        if workspace_rules.strip():
            strategy = f"{strategy}; also respect repository working rules"
        if planner_lessons:
            strategy = f"{strategy}; avoid the repeated failure patterns captured in recent lessons"

        validation_line_range = (
            (validation_start_line, validation_end_line)
            if validation_start_line and validation_end_line
            else ()
        )
        normalized_source_lines = cls._normalize_source_lines(source_lines)
        allowed_line_ranges, allowed_related_symbols = cls._resolve_contract_ranges(
            normalized_path=normalized_path,
            boundary_profile=boundary_profile,
            issue_line=issue_line,
            scope_start_line=scope_start_line,
            scope_end_line=scope_end_line,
            validation_line_range=validation_line_range,
            source_lines=normalized_source_lines,
        )
        allowed_line_ranges, allowed_related_symbols = cls._apply_boundary_lessons(
            planner_lessons=planner_lessons,
            normalized_path=normalized_path,
            validation_line_range=validation_line_range,
            source_lines=normalized_source_lines,
            allowed_line_ranges=allowed_line_ranges,
            allowed_related_symbols=allowed_related_symbols,
        )
        fast_path_enabled = cls._should_enable_fast_path(
            rule_id=rule_id,
            retry_context=retry_context,
            allowed_capabilities=allowed_capabilities,
            allowed_related_symbols=allowed_related_symbols,
            performance_flags=effective_performance_flags,
        )
        plan_first_enabled = cls._should_enable_plan_first(
            rule_id=rule_id,
            retry_context=retry_context,
            allowed_capabilities=allowed_capabilities,
            fast_path_enabled=fast_path_enabled,
            performance_flags=effective_performance_flags,
        )
        repair_plan = (
            cls._build_repair_plan(
                rule_id=rule_id,
                workspace_path=workspace_path,
                normalized_path=normalized_path,
                target_symbol=symbol,
                allowed_related_symbols=allowed_related_symbols,
                allowed_capabilities=allowed_capabilities,
                quality_gate_rules=quality_gate_rules,
                source_lines=normalized_source_lines,
                issue_line=issue_line,
                scope_start_line=scope_start_line,
                scope_end_line=scope_end_line,
            )
            if plan_first_enabled
            else None
        )
        if (
            repair_plan is not None
            and repair_plan.requires_signature_change
        ):
            propagation_targets = tuple(getattr(repair_plan, "propagation_targets", ()) or ())
            promoted_capabilities = list(allowed_capabilities)
            if not propagation_targets and not repair_plan.requires_propagation:
                promoted_capabilities.append(SIGNATURE_CHANGE_CAPABILITY)
            elif propagation_targets:
                promoted_capabilities.append(SIGNATURE_CHANGE_CAPABILITY)
                if any(target.file and target.file != normalized_path for target in propagation_targets):
                    promoted_capabilities.append(MULTI_FILE_REFACTOR_CAPABILITY)
            normalized_promoted_capabilities = tuple(dict.fromkeys(promoted_capabilities))
            if normalized_promoted_capabilities != allowed_capabilities:
                allowed_capabilities = normalized_promoted_capabilities
                repair_plan = cls._build_repair_plan(
                    rule_id=rule_id,
                    workspace_path=workspace_path,
                    normalized_path=normalized_path,
                    target_symbol=symbol,
                    allowed_related_symbols=allowed_related_symbols,
                    allowed_capabilities=allowed_capabilities,
                    quality_gate_rules=quality_gate_rules,
                    source_lines=normalized_source_lines,
                    issue_line=issue_line,
                    scope_start_line=scope_start_line,
                    scope_end_line=scope_end_line,
                )
        plan_precheck = cls._precheck_repair_plan(
            repair_plan=repair_plan,
            allowed_capabilities=allowed_capabilities,
        )
        execution_profile = "fast_path_short_form" if fast_path_enabled else "full_path"
        if plan_first_enabled and not fast_path_enabled:
            execution_profile = "plan_first_full_path"
        propagation_symbols = tuple(
            ContractTargetSymbol(
                file=target.file,
                symbol=target.symbol,
                reason=target.reason,
                start_line=target.start_line,
                end_line=target.end_line,
            )
            for target in getattr(repair_plan, "propagation_targets", ()) or ()
        )
        combined_related_symbols = cls._dedupe_symbols(
            (*allowed_related_symbols, *propagation_symbols)
        )
        target_files = tuple(
            dict.fromkeys(
                (
                    normalized_path,
                    *(
                        target.file
                        for target in getattr(repair_plan, "propagation_targets", ()) or ()
                        if str(target.file or "").strip()
                    ),
                )
            )
        )
        source_file_map = cls._load_workspace_source_map(
            workspace_path,
            target_files,
        )
        prefetched_context = cls._build_prefetched_context(
            normalized_path=normalized_path,
            issue_line=issue_line,
            source_lines=normalized_source_lines,
            source_file_map=source_file_map,
            validation_line_range=validation_line_range,
            allowed_line_ranges=allowed_line_ranges,
            allowed_related_symbols=combined_related_symbols,
            fast_path_enabled=fast_path_enabled,
        )
        edit_contract = EditContract(
            issue_key=issue_key,
            rule_id=rule_id,
            guardrail_mode=guardrail_mode,
            target_files=target_files,
            target_symbols=(symbol,),
            allowed_related_symbols=combined_related_symbols,
            boundary_profile=boundary_profile,
            allowed_capabilities=allowed_capabilities,
            allowed_change_kinds=(
                *cls._allowed_change_kinds(normalized_scope_mode),
                *cls._rule_specific_change_kinds(rule_id),
            ),
            forbidden_change_kinds=forbidden_change_kinds,
            validation_plan=validation_plan,
            follow_up_policy="record_only",
            review_hints=(
                *cls._review_hints(normalized_scope_mode),
                *cls._rule_specific_review_hints(rule_id),
                *cls._lesson_review_hints(planner_lessons),
            ),
            quality_gate_rules=quality_gate_rules,
            planner_lessons=planner_lessons,
            prefetched_context=prefetched_context,
            execution_profile=execution_profile,
            fast_path_enabled=fast_path_enabled,
            plan_first_enabled=plan_first_enabled,
            rollout_flags=effective_performance_flags.enabled_flags(),
            scope_mode=normalized_scope_mode,
            target_line_range=((scope_start_line, scope_end_line) if scope_start_line and scope_end_line else ()),
            validation_line_range=validation_line_range,
            allowed_line_ranges=allowed_line_ranges,
            repair_plan=repair_plan,
            plan_precheck=plan_precheck,
            patch_only=True,
        )
        if fast_path_enabled:
            strategy = (
                f"{strategy}; execute in short-form fast path and finish immediately after the patch is complete"
            )
        elif plan_first_enabled:
            strategy = (
                f"{strategy}; use plan-first execution and honor the structured repair plan before editing"
            )
            if plan_precheck.blocking:
                strategy = (
                    f"{strategy}; stop before edit if the plan precheck reports a blocking contract or quality-gate conflict"
                )
        prompt_guidance_sections = [
            cls.render_contract_guidance(edit_contract),
            cls.render_repair_plan_guidance(edit_contract),
        ]
        return IssuePlan(
            strategy=strategy,
            edit_contract=edit_contract,
            prompt_guidance="\n\n".join(section for section in prompt_guidance_sections if section),
            validation_plan=validation_plan,
        )
