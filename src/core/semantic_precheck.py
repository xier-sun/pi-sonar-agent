"""Light-weight semantic prechecks that run before fast/full build."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.quality_gate_verifier import QualityGateVerifier
from pi_sonar_agent.core.state import serialize_state

_METHOD_DECLARATION_PATTERN = re.compile(
    r"^\s*(?:public|private|protected|internal|static|async|sealed|override|virtual|partial|\s)+[\w<>\[\],?.]+\s+[A-Za-z_]\w*\s*\(",
)
_TYPE_DECLARATION_PATTERN = re.compile(
    r"^\s*(?:public|private|protected|internal|static|sealed|abstract|partial|\s)*(?:class|record|struct|interface)\s+[A-Za-z_]\w*",
)
_NON_PRIVATE_METHOD_DECLARATION_PATTERN = re.compile(
    r"^\s*(?:public|protected|internal)\s+(?:static\s+|async\s+|virtual\s+|override\s+|sealed\s+|partial\s+)*[\w<>\[\],?.]+\s+[A-Za-z_]\w*\s*\(",
)
_ANONYMOUS_OBJECT_PATTERN = re.compile(r"\bnew\s*\{")
_COMMENT_PREFIXES = ("///", "//", "/*", "*")


@dataclass(frozen=True)
class SemanticPrecheckFinding:
    """One semantic precheck blocker detected before build."""

    finding_id: str
    title: str
    message: str
    file: str
    line: int = 0
    evidence: str = ""
    retry_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class SemanticPrecheckResult:
    """Result of the lightweight semantic precheck stage."""

    status: str
    summary: str
    findings: tuple[SemanticPrecheckFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    def to_retry_message(self) -> str:
        if self.status != "retry" or not self.findings:
            return ""
        lines = ["Semantic precheck failed before build. Fix these semantic blockers first:"]
        for index, finding in enumerate(self.findings, start=1):
            detail = f"{index}. [{finding.finding_id}] {finding.title}: {finding.message}"
            if finding.file:
                location = finding.file
                if finding.line > 0:
                    location = f"{location}:{finding.line}"
                detail += f" | location: {location}"
            lines.append(detail)
            if finding.evidence:
                lines.append(f"   Evidence: {finding.evidence}")
            if finding.retry_hint:
                lines.append(f"   Retry Hint: {finding.retry_hint}")
        return "\n".join(lines)


class SemanticPrecheck:
    """Run light-weight semantic blockers before the heavier verification stages."""

    @classmethod
    def review(
        cls,
        *,
        issue_file_path: str,
        edit_contract: EditContract,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        current_issue_file_content: str | None,
    ) -> SemanticPrecheckResult:
        findings: list[SemanticPrecheckFinding] = []
        findings.extend(
            cls._from_quality_gate_violations(
                QualityGateVerifier._validate_language_feature_compatibility(
                    edit_contract=edit_contract,
                    reviewed_changes=reviewed_changes,
                )
            )
        )
        findings.extend(
            cls._detect_async_without_await(
                issue_file_path=issue_file_path,
                reviewed_changes=reviewed_changes,
                current_issue_file_content=current_issue_file_content,
            )
        )
        findings.extend(
            cls._detect_anonymous_type_helper_boundary(
                issue_file_path=issue_file_path,
                reviewed_changes=reviewed_changes,
                current_issue_file_content=current_issue_file_content,
            )
        )
        findings.extend(
            cls._detect_dynamic_helper_signature_boundary(
                issue_file_path=issue_file_path,
                edit_contract=edit_contract,
                reviewed_changes=reviewed_changes,
                current_issue_file_content=current_issue_file_content,
            )
        )
        findings.extend(
            cls._detect_partial_signature_propagation(
                reviewed_changes=reviewed_changes,
                edit_contract=edit_contract,
            )
        )
        findings.extend(
            cls._detect_repair_plan_contract_drift(
                reviewed_changes=reviewed_changes,
                edit_contract=edit_contract,
            )
        )
        deduped = cls._dedupe_findings(findings)
        if deduped:
            return SemanticPrecheckResult(
                status="retry",
                summary=f"Semantic precheck rejected the patch with {len(deduped)} blocker(s).",
                findings=deduped,
            )
        return SemanticPrecheckResult(
            status="pass",
            summary="Semantic precheck passed.",
        )

    @staticmethod
    def _dedupe_findings(
        findings: list[SemanticPrecheckFinding],
    ) -> tuple[SemanticPrecheckFinding, ...]:
        results: list[SemanticPrecheckFinding] = []
        seen: set[tuple[str, str, int]] = set()
        for finding in findings:
            key = (finding.finding_id, finding.file, finding.line)
            if key in seen:
                continue
            seen.add(key)
            results.append(finding)
        return tuple(results)

    @staticmethod
    def _from_quality_gate_violations(violations) -> list[SemanticPrecheckFinding]:
        return [
            SemanticPrecheckFinding(
                finding_id=str(item.rule_id or "").strip() or "language_feature_compatibility",
                title=str(item.title or "").strip() or "仓库语言特性兼容性",
                message=str(item.message or "").strip(),
                file=str(item.file or "").strip(),
                line=int(item.line or 0),
                evidence=str(item.evidence or "").strip(),
                retry_hint=str(item.retry_hint or "").strip(),
            )
            for item in violations
        ]

    @classmethod
    def _detect_async_without_await(
        cls,
        *,
        issue_file_path: str,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        current_issue_file_content: str | None,
    ) -> list[SemanticPrecheckFinding]:
        if current_issue_file_content is None:
            return []
        normalized_issue_file = QualityGateVerifier._normalize_path(issue_file_path)
        primary_change = next(
            (
                change
                for change in reviewed_changes
                if QualityGateVerifier._normalize_path(change.file) == normalized_issue_file
            ),
            None,
        )
        if primary_change is None:
            return []
        lines = current_issue_file_content.splitlines()
        changed_lines = tuple(
            sorted(
                line
                for line in primary_change.after_changed_lines
                if 0 < int(line) <= len(lines)
            )
        )
        if not changed_lines:
            return []
        touched_methods = QualityGateVerifier._collect_touched_methods(lines, changed_lines)
        violations = QualityGateVerifier._validate_async_requires_await(
            normalized_issue_file,
            lines,
            touched_methods,
            retry_hint="如果 helper 没有真实 await，请保持同步。",
        )
        return [
            SemanticPrecheckFinding(
                finding_id="async_without_await",
                title=str(item.title or "").strip() or "异步方法必须真正 await",
                message=str(item.message or "").strip(),
                file=str(item.file or "").strip(),
                line=int(item.line or 0),
                evidence=str(item.evidence or "").strip(),
                retry_hint=str(item.retry_hint or "").strip(),
            )
            for item in violations
        ]

    @classmethod
    def _iter_added_lines(
        cls,
        reviewed_changes: tuple[ReviewedFileChange, ...],
    ) -> tuple[tuple[str, int, str], ...]:
        return QualityGateVerifier._collect_added_source_lines(reviewed_changes)

    @classmethod
    def _detect_anonymous_type_helper_boundary(
        cls,
        *,
        issue_file_path: str,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        current_issue_file_content: str | None,
    ) -> list[SemanticPrecheckFinding]:
        if current_issue_file_content is None:
            return []

        normalized_issue_file = QualityGateVerifier._normalize_path(issue_file_path)
        primary_change = next(
            (
                change
                for change in reviewed_changes
                if QualityGateVerifier._normalize_path(change.file) == normalized_issue_file
            ),
            None,
        )
        if primary_change is None:
            return []

        lines = current_issue_file_content.splitlines()
        if not lines:
            return []

        added_lines = {
            line_number: text
            for file_path, line_number, text in cls._iter_added_lines(reviewed_changes)
            if file_path == normalized_issue_file and 0 < int(line_number) <= len(lines)
        }
        if not added_lines:
            return []

        helper_windows = cls._collect_added_helper_windows(
            lines=lines,
            added_line_numbers=tuple(sorted(added_lines)),
        )
        findings: list[SemanticPrecheckFinding] = []
        for helper_window in helper_windows:
            anonymous_line = cls._find_added_anonymous_projection_in_helper(
                lines=lines,
                helper_window=helper_window,
                added_lines=added_lines,
            )
            if anonymous_line is None:
                continue
            line_number, evidence = anonymous_line
            findings.append(
                SemanticPrecheckFinding(
                    finding_id="anonymous_type_helper_boundary",
                    title="匿名类型跨 helper 边界风险",
                    message="当前 patch 在新增 helper 内部直接构造匿名类型，容易在 helper 提取后丢失类型推断。",
                    file=normalized_issue_file,
                    line=line_number,
                    evidence=evidence,
                    retry_hint="匿名类型保持在当前方法内，或改用已有命名类型，不要直接跨 helper 传递。",
                )
            )
        return findings

    @classmethod
    def _collect_added_helper_windows(
        cls,
        *,
        lines: list[str],
        added_line_numbers: tuple[int, ...],
    ) -> tuple[Any, ...]:
        windows: list[Any] = []
        seen: set[tuple[int, int]] = set()
        added_line_lookup = set(added_line_numbers)
        for line_number in added_line_numbers:
            stripped = lines[line_number - 1].strip()
            if not stripped or stripped.startswith(_COMMENT_PREFIXES):
                continue
            if not _METHOD_DECLARATION_PATTERN.match(stripped):
                continue
            method_window = QualityGateVerifier._build_method_window(lines, line_number)
            if method_window is None:
                continue
            key = (int(method_window.declaration_line), int(method_window.end_line))
            if key in seen or int(method_window.declaration_line) not in added_line_lookup:
                continue
            seen.add(key)
            windows.append(method_window)
        return tuple(windows)

    @classmethod
    def _find_added_anonymous_projection_in_helper(
        cls,
        *,
        lines: list[str],
        helper_window: Any,
        added_lines: dict[int, str],
    ) -> tuple[int, str] | None:
        start_line = max(1, int(getattr(helper_window, "start_line", 0) or 0))
        end_line = min(len(lines), int(getattr(helper_window, "end_line", 0) or 0))
        if start_line <= 0 or end_line <= 0 or start_line > end_line:
            return None

        for line_number in range(start_line, end_line + 1):
            raw_text = added_lines.get(line_number)
            if raw_text is None:
                continue
            stripped = raw_text.strip()
            if not stripped or stripped.startswith(_COMMENT_PREFIXES):
                continue
            if _ANONYMOUS_OBJECT_PATTERN.search(stripped):
                return line_number, stripped

            next_text = added_lines.get(line_number + 1, "")
            if (
                stripped.endswith("new")
                and next_text.strip().startswith("{")
                and line_number + 1 <= end_line
            ):
                return line_number, f"{stripped} {next_text.strip()}"
        return None

    @classmethod
    def _detect_dynamic_helper_signature_boundary(
        cls,
        *,
        issue_file_path: str,
        edit_contract: EditContract,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        current_issue_file_content: str | None,
    ) -> list[SemanticPrecheckFinding]:
        if current_issue_file_content is None:
            return []
        if str(getattr(edit_contract, "rule_id", "") or "").strip() != "csharpsquid:S3776":
            return []

        normalized_issue_file = QualityGateVerifier._normalize_path(issue_file_path)
        lines = current_issue_file_content.splitlines()
        if not lines:
            return []

        added_line_numbers = tuple(
            sorted(
                line_number
                for file_path, line_number, _ in cls._iter_added_lines(reviewed_changes)
                if file_path == normalized_issue_file and 0 < int(line_number) <= len(lines)
            )
        )
        if not added_line_numbers:
            return []

        findings: list[SemanticPrecheckFinding] = []
        for helper_window in cls._collect_added_helper_windows(
            lines=lines,
            added_line_numbers=added_line_numbers,
        ):
            signature_text = str(getattr(helper_window, "signature", "") or "").strip()
            if not signature_text or "dynamic" not in signature_text:
                continue
            findings.append(
                SemanticPrecheckFinding(
                    finding_id="dynamic_helper_signature_boundary",
                    title="helper 签名不应退化为 dynamic",
                    message="当前 patch 新增 helper 使用 dynamic 签名承载状态，容易破坏匿名类型、nullable 和泛型推断。",
                    file=normalized_issue_file,
                    line=int(getattr(helper_window, "declaration_line", 0) or 0),
                    evidence=signature_text,
                    retry_hint="不要把 helper 参数或返回值写成 dynamic；保持调用点 concrete type 完整一致，或者把逻辑留在当前方法内。",
                )
            )
        return findings

    @classmethod
    def _detect_partial_signature_propagation(
        cls,
        *,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        edit_contract: EditContract,
    ) -> list[SemanticPrecheckFinding]:
        repair_plan = getattr(edit_contract, "repair_plan", None)
        if repair_plan is None:
            return []
        proposed_method_name = str(getattr(repair_plan, "proposed_method_name", "") or "").strip()
        propagation_targets = tuple(getattr(repair_plan, "propagation_targets", ()) or ())
        if not proposed_method_name or not propagation_targets:
            return []

        touched_targets = {
            (QualityGateVerifier._normalize_path(change.file), int(line))
            for change in reviewed_changes
            for line in change.after_changed_lines
        }
        declared_targets = [
            target
            for target in propagation_targets
            if str(target.file or "").strip() and int(target.start_line or 0) > 0
        ]
        if not declared_targets:
            return []

        renamed_primary = any(
            proposed_method_name in text
            for _, _, text in cls._iter_added_lines(reviewed_changes)
        )
        if not renamed_primary:
            return []

        missing_targets = [
            target
            for target in declared_targets
            if (
                QualityGateVerifier._normalize_path(target.file),
                int(target.start_line or 0),
            )
            not in touched_targets
        ]
        if not missing_targets:
            return []

        first_missing = missing_targets[0]
        return [
            SemanticPrecheckFinding(
                finding_id="signature_propagation_incomplete",
                title="签名传播未完成",
                message="当前 patch 已开始改签名，但声明的传播目标仍有未触达项。",
                file=str(first_missing.file or "").strip(),
                line=int(first_missing.start_line or 0),
                evidence=str(first_missing.symbol or "").strip(),
                retry_hint="如果没有把所有声明的接口/调用点/nameof 目标一起改完，就不要继续这次签名重构。",
            )
        ]

    @classmethod
    def _detect_repair_plan_contract_drift(
        cls,
        *,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        edit_contract: EditContract,
    ) -> list[SemanticPrecheckFinding]:
        repair_plan = getattr(edit_contract, "repair_plan", None)
        if repair_plan is None:
            return []

        findings: list[SemanticPrecheckFinding] = []
        added_lines = cls._iter_added_lines(reviewed_changes)
        if not added_lines:
            return findings

        if not bool(getattr(repair_plan, "requires_new_type", False)):
            for file_path, line_number, text in added_lines:
                stripped = str(text or "").strip()
                if not stripped or stripped.startswith(("///", "//", "/*", "*")):
                    continue
                if not _TYPE_DECLARATION_PATTERN.match(stripped):
                    continue
                findings.append(
                    SemanticPrecheckFinding(
                        finding_id="repair_plan_new_type_forbidden",
                        title="Repair plan 禁止新增类型",
                        message="当前 repair plan 明确要求无新增类型，但 patch 引入了新的类型声明。",
                        file=file_path,
                        line=line_number,
                        evidence=stripped,
                        retry_hint="保持当前修复为无新类型方案；如果复杂度重构需要承载状态，优先留在原方法内或只提取 private helper。",
                    )
                )
                break

        if not bool(getattr(repair_plan, "requires_signature_change", False)):
            for file_path, line_number, text in added_lines:
                stripped = str(text or "").strip()
                if not stripped or stripped.startswith(("///", "//", "/*", "*")):
                    continue
                if not _NON_PRIVATE_METHOD_DECLARATION_PATTERN.match(stripped):
                    continue
                findings.append(
                    SemanticPrecheckFinding(
                        finding_id="repair_plan_signature_change_forbidden",
                        title="Repair plan 禁止改签名",
                        message="当前 repair plan 要求保持签名稳定，但 patch 改动了非 private 方法声明。",
                        file=file_path,
                        line=line_number,
                        evidence=stripped,
                        retry_hint="保持现有 public/protected/internal 方法签名不变，把重构收敛到方法体或 private/local helper。",
                    )
                )
                break

        return findings
