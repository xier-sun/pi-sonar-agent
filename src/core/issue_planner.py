"""Issue-level planning and edit-contract generation."""

from __future__ import annotations

from dataclasses import dataclass

from pi_sonar_agent.agent.rule_policies import (
    CONDITIONAL_CHAIN_SCOPE_MODE,
    CONTROL_BLOCK_SCOPE_MODE,
    DECLARATION_COMMENT_SCOPE_MODE,
    EXPRESSION_REWRITE_SCOPE_MODE,
    LOOP_REWRITE_SCOPE_MODE,
    METHOD_SCOPE_MODE,
    STATEMENT_SCOPE_MODE,
)
from pi_sonar_agent.core.issue_contract import ContractTargetSymbol, EditContract
from pi_sonar_agent.core.quality_gate import QualityGateCatalog, load_default_quality_gate_catalog
from pi_sonar_agent.core.retry_context import RetryContext


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

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(file_path or "").replace("\\", "/").lstrip("/")

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
        lines.append("- Follow Up Policy: " + edit_contract.follow_up_policy)
        if edit_contract.patch_only:
            lines.append("- Editing Mode: patch-only, prefer Edit/MultiEdit over whole-file rewrite")
        return "\n".join(lines)

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
        retry_context: RetryContext | None = None,
        workspace_rules: str = "",
        quality_gate_catalog: QualityGateCatalog | None = None,
    ) -> IssuePlan:
        """Build an issue plan and edit contract from runtime context."""

        normalized_path = cls._normalize_path(file_path)
        catalog = quality_gate_catalog or load_default_quality_gate_catalog()
        quality_gate_rules = catalog.rules_for_path(normalized_path)
        normalized_scope_mode = str(scope_mode or STATEMENT_SCOPE_MODE)
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

        validation_line_range = (
            (validation_start_line, validation_end_line)
            if validation_start_line and validation_end_line
            else ()
        )
        edit_contract = EditContract(
            issue_key=issue_key,
            rule_id=rule_id,
            guardrail_mode=guardrail_mode,
            target_files=(normalized_path,),
            target_symbols=(symbol,),
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
            ),
            quality_gate_rules=quality_gate_rules,
            scope_mode=normalized_scope_mode,
            target_line_range=((scope_start_line, scope_end_line) if scope_start_line and scope_end_line else ()),
            validation_line_range=validation_line_range,
            allowed_line_ranges=((validation_line_range,) if validation_line_range else ()),
            patch_only=True,
        )
        return IssuePlan(
            strategy=strategy,
            edit_contract=edit_contract,
            prompt_guidance=cls.render_contract_guidance(edit_contract),
            validation_plan=validation_plan,
        )
