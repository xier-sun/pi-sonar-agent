"""Post-edit C# quality-gate verification for single-issue patches."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.quality_gate import (
    QualityGateResult,
    QualityGateSoftFinding,
    QualityGateViolation,
)

_CONTROL_KEYWORDS = {
    "if",
    "else",
    "for",
    "foreach",
    "while",
    "switch",
    "catch",
    "using",
    "lock",
    "return",
    "new",
}
_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_QUERY_SYNTAX_PATTERN = re.compile(
    r"\bfrom\s+\w+\s+in\b|\bjoin\s+\w+\s+in\b|\bgroup\s+\w+\s+by\b",
    re.IGNORECASE,
)
_FINANCE_ENGLISH_PATTERN = re.compile(
    r"\b(penalty|receivable|accounting|inventory|order\s+cancel|interest)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _MethodWindow:
    name: str
    access: str
    return_type: str
    signature: str
    declaration_line: int
    start_line: int
    end_line: int
    is_async: bool
    is_static: bool

    @property
    def symbol(self) -> str:
        return f"{self.name}@{self.declaration_line}-{self.end_line}"


@dataclass(frozen=True)
class _TypeDeclaration:
    name: str
    kind: str
    access: str
    declaration_line: int
    start_line: int
    end_line: int
    is_sealed: bool
    has_inheritance: bool

    @property
    def symbol(self) -> str:
        return f"{self.kind} {self.name}@{self.declaration_line}-{self.end_line}"


class QualityGateVerifier:
    """Verify the active quality-gate rules for one issue attempt."""

    @classmethod
    def review(
        cls,
        *,
        issue_file_path: str,
        edit_contract: EditContract,
        reviewed_changes: tuple[ReviewedFileChange, ...],
        original_issue_file_content: str | None,
        current_issue_file_content: str | None,
    ) -> QualityGateResult:
        """Validate the current patch against the structured C# quality gate."""

        rules = tuple(edit_contract.quality_gate_rules)
        if not rules or current_issue_file_content is None:
            return QualityGateResult(
                status="pass",
                summary="No active quality gates were evaluated for this attempt.",
                applied_rule_ids=tuple(rule.rule_id for rule in rules),
            )

        issue_file = cls._normalize_path(issue_file_path)
        primary_change = cls._select_issue_file_change(issue_file, reviewed_changes)
        changed_lines = (
            tuple(sorted({line for line in primary_change.quality_gate_changed_lines if line > 0}))
            if primary_change is not None
            else ()
        )
        if not changed_lines:
            return QualityGateResult(
                status="pass",
                summary="No post-edit changed lines were available for quality-gate review.",
                applied_rule_ids=tuple(rule.rule_id for rule in rules),
            )

        lines = current_issue_file_content.splitlines()
        touched_methods = cls._collect_touched_methods(lines, changed_lines)
        touched_types = cls._collect_touched_types(lines, changed_lines)
        touched_public_declarations = cls._collect_touched_public_declarations(
            lines,
            changed_lines,
            touched_methods,
            touched_types,
        )
        added_comment_lines = cls._collect_added_comment_lines(primary_change.diff_text if primary_change else "")

        hard_violations: list[QualityGateViolation] = []
        soft_findings: list[QualityGateSoftFinding] = []

        for rule in rules:
            if rule.rule_id == "public_xml_docs":
                hard_violations.extend(
                    cls._validate_public_xml_docs(
                        issue_file,
                        lines,
                        touched_public_declarations,
                        retry_hint=rule.retry_hint,
                    )
                )
            elif rule.rule_id == "async_signature":
                hard_violations.extend(
                    cls._validate_async_signature(
                        issue_file,
                        touched_methods,
                        retry_hint=rule.retry_hint,
                    )
                )
            elif rule.rule_id == "async_requires_await":
                hard_violations.extend(
                    cls._validate_async_requires_await(
                        issue_file,
                        lines,
                        touched_methods,
                        retry_hint=rule.retry_hint,
                    )
                )
            elif rule.rule_id == "linq_method_syntax":
                hard_violations.extend(
                    cls._validate_linq_query_syntax(
                        issue_file,
                        lines,
                        changed_lines,
                        retry_hint=rule.retry_hint,
                    )
                )
            elif rule.rule_id == "cognitive_complexity":
                hard_violations.extend(
                    cls._validate_cognitive_complexity(
                        issue_file,
                        lines,
                        touched_methods,
                        retry_hint=rule.retry_hint,
                    )
                )
            elif rule.rule_id == "static_preferred":
                soft_findings.extend(cls._review_static_preference(issue_file, lines, touched_methods))
            elif rule.rule_id == "sealed_preferred":
                soft_findings.extend(cls._review_sealed_preference(issue_file, touched_types))
            elif rule.rule_id == "business_comments_chinese":
                soft_findings.extend(cls._review_business_comment_language(issue_file, added_comment_lines))
            elif rule.rule_id == "finance_terms_chinese":
                soft_findings.extend(cls._review_finance_comment_terms(issue_file, added_comment_lines))

        if hard_violations:
            return QualityGateResult(
                status="retry",
                summary=f"Quality gate rejected the patch with {len(hard_violations)} hard violation(s).",
                applied_rule_ids=tuple(rule.rule_id for rule in rules),
                violations=tuple(hard_violations),
                soft_findings=tuple(soft_findings),
            )

        summary = "Hard quality gates passed."
        if soft_findings:
            summary = f"{summary} Recorded {len(soft_findings)} soft reviewer finding(s)."
        return QualityGateResult(
            status="pass",
            summary=summary,
            applied_rule_ids=tuple(rule.rule_id for rule in rules),
            soft_findings=tuple(soft_findings),
        )

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(file_path or "").replace("\\", "/").lstrip("/")

    @classmethod
    def _select_issue_file_change(
        cls,
        issue_file_path: str,
        reviewed_changes: tuple[ReviewedFileChange, ...],
    ) -> ReviewedFileChange | None:
        normalized_issue_file = cls._normalize_path(issue_file_path)
        for change in reviewed_changes:
            if cls._normalize_path(change.file) == normalized_issue_file:
                return change
        return None

    @classmethod
    def _collect_touched_methods(
        cls,
        lines: list[str],
        changed_lines: tuple[int, ...],
    ) -> tuple[_MethodWindow, ...]:
        methods: dict[tuple[int, int], _MethodWindow] = {}
        for line_number in changed_lines:
            method = cls._find_enclosing_method(lines, line_number)
            if method is not None:
                methods[(method.declaration_line, method.end_line)] = method
        return tuple(methods.values())

    @classmethod
    def _collect_touched_types(
        cls,
        lines: list[str],
        changed_lines: tuple[int, ...],
    ) -> tuple[_TypeDeclaration, ...]:
        declarations: dict[tuple[int, int], _TypeDeclaration] = {}
        for line_number in changed_lines:
            declaration = cls._find_nearby_type_declaration(lines, line_number)
            if declaration is not None:
                declarations[(declaration.declaration_line, declaration.end_line)] = declaration
        return tuple(declarations.values())

    @classmethod
    def _collect_touched_public_declarations(
        cls,
        lines: list[str],
        changed_lines: tuple[int, ...],
        touched_methods: tuple[_MethodWindow, ...],
        touched_types: tuple[_TypeDeclaration, ...],
    ) -> tuple[dict[str, object], ...]:
        declarations: list[dict[str, object]] = []
        for method in touched_methods:
            if method.access == "public":
                declarations.append(
                    {
                        "kind": "method",
                        "name": method.name,
                        "line": method.declaration_line,
                        "signature": method.signature,
                        "symbol": method.symbol,
                    }
                )

        seen_property_lines: set[int] = set()
        for line_number in changed_lines:
            property_decl = cls._find_public_property_declaration(lines, line_number)
            if property_decl is not None and property_decl["line"] not in seen_property_lines:
                seen_property_lines.add(int(property_decl["line"]))
                declarations.append(property_decl)

        seen_type_lines: set[int] = set()
        for declaration in touched_types:
            if declaration.access == "public" and declaration.declaration_line not in seen_type_lines:
                seen_type_lines.add(declaration.declaration_line)
                declarations.append(
                    {
                        "kind": declaration.kind,
                        "name": declaration.name,
                        "line": declaration.declaration_line,
                        "signature": lines[declaration.declaration_line - 1].strip(),
                        "symbol": declaration.symbol,
                    }
                )

        return tuple(declarations)

    @classmethod
    def _find_enclosing_method(cls, lines: list[str], target_line: int) -> _MethodWindow | None:
        total_lines = len(lines)
        if total_lines <= 0:
            return None

        start_limit = max(1, target_line - 80)
        for candidate_line in range(min(target_line, total_lines), start_limit - 1, -1):
            method = cls._build_method_window(lines, candidate_line)
            if method is not None and method.start_line <= target_line <= method.end_line:
                return method
        return None

    @classmethod
    def _build_method_window(cls, lines: list[str], candidate_line: int) -> _MethodWindow | None:
        total_lines = len(lines)
        first_line = lines[candidate_line - 1].strip()
        if not first_line or first_line.startswith(("///", "//", "*", "[")):
            return None
        signature_lines: list[str] = []
        signature_end_line = 0
        saw_open_paren = False
        paren_balance = 0

        for line_number in range(candidate_line, min(total_lines, candidate_line + 8) + 1):
            current_line = lines[line_number - 1]
            signature_lines.append(current_line.strip())
            paren_balance += current_line.count("(") - current_line.count(")")
            saw_open_paren = saw_open_paren or "(" in current_line
            if saw_open_paren and paren_balance <= 0 and (
                "{" in current_line or "=>" in current_line or current_line.strip().endswith("{")
            ):
                signature_end_line = line_number
                break

        if signature_end_line <= 0:
            return None

        signature_text = " ".join(part for part in signature_lines if part).strip()
        if not cls._looks_like_method_signature(signature_text):
            return None

        body_end_line = cls._find_block_end_line(lines, candidate_line, signature_end_line)
        if body_end_line <= 0:
            return None

        prefix = signature_text.split("(", 1)[0].strip()
        tokens = [item for item in re.split(r"\s+", prefix) if item]
        if len(tokens) < 2:
            return None
        name = tokens[-1]
        if name in _CONTROL_KEYWORDS:
            return None

        access = next(
            (token for token in tokens if token in {"public", "private", "protected", "internal"}),
            "",
        )
        return_type = tokens[-2] if len(tokens) >= 2 else ""
        return _MethodWindow(
            name=name,
            access=access,
            return_type=return_type,
            signature=signature_text,
            declaration_line=candidate_line,
            start_line=candidate_line,
            end_line=body_end_line,
            is_async="async" in tokens or "Task" in return_type or "ValueTask" in return_type,
            is_static="static" in tokens,
        )

    @staticmethod
    def _looks_like_method_signature(signature_text: str) -> bool:
        if "(" not in signature_text or ")" not in signature_text:
            return False
        prefix = signature_text.split("(", 1)[0].strip()
        if not prefix or "=" in prefix:
            return False
        name = prefix.split()[-1]
        return name not in _CONTROL_KEYWORDS

    @classmethod
    def _find_block_end_line(
        cls,
        lines: list[str],
        declaration_line: int,
        signature_end_line: int,
    ) -> int:
        brace_balance = 0
        saw_open_brace = False
        for line_number in range(declaration_line, len(lines) + 1):
            current_line = lines[line_number - 1]
            if "=>" in current_line and line_number >= signature_end_line:
                return line_number
            open_count = current_line.count("{")
            close_count = current_line.count("}")
            if open_count > 0:
                saw_open_brace = True
            if saw_open_brace:
                brace_balance += open_count - close_count
                if brace_balance <= 0:
                    return line_number
        return 0

    @classmethod
    def _find_nearby_type_declaration(
        cls,
        lines: list[str],
        line_number: int,
    ) -> _TypeDeclaration | None:
        for candidate_line in range(max(1, line_number - 2), min(len(lines), line_number) + 1):
            stripped = lines[candidate_line - 1].strip()
            match = re.search(
                r"\b(?P<access>public|internal|private|protected)?\s*"
                r"(?P<modifiers>(?:sealed|abstract|static)\s+)*"
                r"(?P<kind>class|record)\s+"
                r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
                r"(?P<inheritance>\s*:\s*[^\{]+)?",
                stripped,
            )
            if match is None:
                continue

            end_line = cls._find_block_end_line(lines, candidate_line, candidate_line)
            if end_line <= 0:
                end_line = candidate_line
            modifiers = match.group("modifiers") or ""
            return _TypeDeclaration(
                name=match.group("name"),
                kind=match.group("kind"),
                access=(match.group("access") or "").strip(),
                declaration_line=candidate_line,
                start_line=candidate_line,
                end_line=end_line,
                is_sealed="sealed" in modifiers,
                has_inheritance=bool(match.group("inheritance")),
            )
        return None

    @staticmethod
    def _find_public_property_declaration(
        lines: list[str],
        line_number: int,
    ) -> dict[str, object] | None:
        stripped = lines[line_number - 1].strip()
        if "public" not in stripped:
            return None
        if "(" in stripped:
            return None
        if "{ get;" not in stripped and "{get;" not in stripped and "=>" not in stripped:
            return None
        match = re.search(r"\bpublic\b.+?\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|=>)", stripped)
        if match is None:
            return None
        return {
            "kind": "property",
            "name": match.group("name"),
            "line": line_number,
            "signature": stripped,
            "symbol": f"{match.group('name')}@{line_number}",
        }

    @staticmethod
    def _collect_added_comment_lines(diff_text: str) -> tuple[tuple[int, str], ...]:
        results: list[tuple[int, str]] = []
        current_line = 0
        for raw_line in diff_text.splitlines():
            if raw_line.startswith("@@ "):
                match = re.search(r"\+(\d+)", raw_line)
                current_line = int(match.group(1)) if match else 0
                continue
            if raw_line.startswith("+++"):
                continue
            if raw_line.startswith("+"):
                text = raw_line[1:].strip()
                if text.startswith("//") or text.startswith("/*") or text.startswith("*"):
                    results.append((current_line, text))
                current_line += 1
                continue
            if raw_line.startswith("-"):
                continue
            if current_line > 0:
                current_line += 1
        return tuple(results)

    @staticmethod
    def _collect_xml_doc(lines: list[str], declaration_line: int) -> list[str]:
        doc_lines: list[str] = []
        line_number = declaration_line - 1
        while line_number >= 1:
            stripped = lines[line_number - 1].strip()
            if stripped.startswith("///"):
                doc_lines.insert(0, stripped)
                line_number -= 1
                continue
            if not stripped:
                line_number -= 1
                continue
            break
        return doc_lines

    @staticmethod
    def _parse_method_parameter_names(signature: str) -> tuple[str, ...]:
        match = re.search(r"\((?P<params>.*)\)", signature)
        if match is None:
            return ()
        params_text = match.group("params").strip()
        if not params_text:
            return ()
        names: list[str] = []
        for item in params_text.split(","):
            part = re.sub(r"=\s*.+$", "", item).strip()
            if not part:
                continue
            tokens = [token for token in re.split(r"\s+", part) if token]
            if tokens:
                names.append(tokens[-1].strip("@"))
        return tuple(names)

    @classmethod
    def _validate_public_xml_docs(
        cls,
        file_path: str,
        lines: list[str],
        declarations: tuple[dict[str, object], ...],
        *,
        retry_hint: str,
    ) -> list[QualityGateViolation]:
        violations: list[QualityGateViolation] = []
        for declaration in declarations:
            line_number = int(declaration["line"])
            doc_lines = cls._collect_xml_doc(lines, line_number)
            doc_text = "\n".join(doc_lines)
            if not doc_lines:
                violations.append(
                    QualityGateViolation(
                        rule_id="public_xml_docs",
                        title="公开成员 XML 文档完整",
                        message=f"{declaration['kind']} {declaration['name']} 缺少 XML 文档注释。",
                        file=file_path,
                        line=line_number,
                        symbol=str(declaration["symbol"]),
                        evidence=str(declaration["signature"]),
                        retry_hint=retry_hint,
                    )
                )
                continue
            if "<summary>" not in doc_text or "</summary>" not in doc_text:
                violations.append(
                    QualityGateViolation(
                        rule_id="public_xml_docs",
                        title="公开成员 XML 文档完整",
                        message=f"{declaration['kind']} {declaration['name']} 的 XML 文档缺少完整 <summary>。",
                        file=file_path,
                        line=line_number,
                        symbol=str(declaration["symbol"]),
                        evidence=doc_text,
                        retry_hint=retry_hint,
                    )
                )
                continue

            if declaration["kind"] != "method":
                continue

            params = cls._parse_method_parameter_names(str(declaration["signature"]))
            for param in params:
                if f'<param name="{param}">' not in doc_text:
                    violations.append(
                        QualityGateViolation(
                            rule_id="public_xml_docs",
                            title="公开成员 XML 文档完整",
                            message=f"公开方法 {declaration['name']} 缺少参数 {param} 的 <param> 文档。",
                            file=file_path,
                            line=line_number,
                            symbol=str(declaration["symbol"]),
                            evidence=doc_text,
                            retry_hint=retry_hint,
                        )
                    )
                    break

            signature_text = str(declaration["signature"])
            has_return_value = not re.search(r"\bvoid\b", signature_text)
            if has_return_value and "<returns>" not in doc_text:
                violations.append(
                    QualityGateViolation(
                        rule_id="public_xml_docs",
                        title="公开成员 XML 文档完整",
                        message=f"公开方法 {declaration['name']} 有返回值但缺少 <returns> 文档。",
                        file=file_path,
                        line=line_number,
                        symbol=str(declaration["symbol"]),
                        evidence=doc_text,
                        retry_hint=retry_hint,
                    )
                )
        return violations

    @staticmethod
    def _validate_async_signature(
        file_path: str,
        touched_methods: tuple[_MethodWindow, ...],
        *,
        retry_hint: str,
    ) -> list[QualityGateViolation]:
        violations: list[QualityGateViolation] = []
        for method in touched_methods:
            if not method.is_async:
                continue
            if "async void" in method.signature:
                violations.append(
                    QualityGateViolation(
                        rule_id="async_signature",
                        title="异步签名规范",
                        message=f"异步方法 {method.name} 使用了 async void。",
                        file=file_path,
                        line=method.declaration_line,
                        symbol=method.symbol,
                        evidence=method.signature,
                        retry_hint=retry_hint,
                    )
                )
            if not method.name.endswith("Async"):
                violations.append(
                    QualityGateViolation(
                        rule_id="async_signature",
                        title="异步签名规范",
                        message=f"异步方法 {method.name} 没有以 Async 结尾。",
                        file=file_path,
                        line=method.declaration_line,
                        symbol=method.symbol,
                        evidence=method.signature,
                        retry_hint=retry_hint,
                    )
                )
            if "Task" not in method.return_type and "ValueTask" not in method.return_type:
                violations.append(
                    QualityGateViolation(
                        rule_id="async_signature",
                        title="异步签名规范",
                        message=f"异步方法 {method.name} 的返回类型不是 Task/Task<T>。",
                        file=file_path,
                        line=method.declaration_line,
                        symbol=method.symbol,
                        evidence=method.signature,
                        retry_hint=retry_hint,
                    )
                )
        return violations

    @staticmethod
    def _validate_async_requires_await(
        file_path: str,
        lines: list[str],
        touched_methods: tuple[_MethodWindow, ...],
        *,
        retry_hint: str,
    ) -> list[QualityGateViolation]:
        violations: list[QualityGateViolation] = []
        for method in touched_methods:
            if "async" not in method.signature:
                continue
            body_text = "\n".join(lines[method.start_line - 1:method.end_line])
            if "await " in body_text:
                continue
            violations.append(
                QualityGateViolation(
                    rule_id="async_requires_await",
                    title="异步方法必须真正 await",
                    message=f"异步方法 {method.name} 没有实际 await。",
                    file=file_path,
                    line=method.declaration_line,
                    symbol=method.symbol,
                    evidence=method.signature,
                    retry_hint=retry_hint,
                )
            )
        return violations

    @staticmethod
    def _validate_linq_query_syntax(
        file_path: str,
        lines: list[str],
        changed_lines: tuple[int, ...],
        *,
        retry_hint: str,
    ) -> list[QualityGateViolation]:
        violations: list[QualityGateViolation] = []
        for line_number in changed_lines:
            if line_number <= 0 or line_number > len(lines):
                continue
            text = lines[line_number - 1].strip()
            if not _QUERY_SYNTAX_PATTERN.search(text):
                continue
            violations.append(
                QualityGateViolation(
                    rule_id="linq_method_syntax",
                    title="LINQ 优先方法语法",
                    message="当前 patch 引入了 query syntax，请改成方法语法。",
                    file=file_path,
                    line=line_number,
                    evidence=text,
                    retry_hint=retry_hint,
                )
            )
        return violations

    @classmethod
    def _validate_cognitive_complexity(
        cls,
        file_path: str,
        lines: list[str],
        touched_methods: tuple[_MethodWindow, ...],
        *,
        retry_hint: str,
    ) -> list[QualityGateViolation]:
        violations: list[QualityGateViolation] = []
        for method in touched_methods:
            body_text = "\n".join(lines[method.start_line - 1:method.end_line])
            complexity = cls._estimate_cognitive_complexity(body_text)
            if complexity <= 30:
                continue
            violations.append(
                QualityGateViolation(
                    rule_id="cognitive_complexity",
                    title="单方法认知复杂度不超过 30",
                    message=f"当前触达的方法 {method.name} 估算认知复杂度为 {complexity}，超过 30。",
                    file=file_path,
                    line=method.declaration_line,
                    symbol=method.symbol,
                    evidence=method.signature,
                    retry_hint=retry_hint,
                )
            )
        return violations

    @staticmethod
    def _estimate_cognitive_complexity(body_text: str) -> int:
        score = 0
        nesting = 0
        for raw_line in body_text.splitlines():
            line = raw_line.split("//", 1)[0]
            stripped = line.strip()
            if not stripped:
                continue
            nesting = max(0, nesting - stripped.count("}"))
            for keyword in ("if", "for", "foreach", "while", "switch", "catch"):
                if re.search(rf"\b{keyword}\b", stripped):
                    score += 1 + nesting
            score += stripped.count("&&") + stripped.count("||")
            score += stripped.count("?")
            nesting += stripped.count("{")
        return score

    @staticmethod
    def _review_static_preference(
        file_path: str,
        lines: list[str],
        touched_methods: tuple[_MethodWindow, ...],
    ) -> list[QualityGateSoftFinding]:
        findings: list[QualityGateSoftFinding] = []
        for method in touched_methods:
            if method.access != "private" or method.is_static:
                continue
            body_text = "\n".join(lines[method.start_line - 1:method.end_line])
            if "this." in body_text or re.search(r"\b_[A-Za-z]\w*", body_text):
                continue
            findings.append(
                QualityGateSoftFinding(
                    rule_id="static_preferred",
                    title="新增纯辅助方法优先 static",
                    message=f"私有方法 {method.name} 看起来不依赖实例状态，可考虑标记为 static。",
                    file=file_path,
                    line=method.declaration_line,
                    symbol=method.symbol,
                    evidence=method.signature,
                )
            )
        return findings

    @staticmethod
    def _review_sealed_preference(
        file_path: str,
        touched_types: tuple[_TypeDeclaration, ...],
    ) -> list[QualityGateSoftFinding]:
        findings: list[QualityGateSoftFinding] = []
        for declaration in touched_types:
            if declaration.kind != "class":
                continue
            if declaration.is_sealed or declaration.has_inheritance:
                continue
            findings.append(
                QualityGateSoftFinding(
                    rule_id="sealed_preferred",
                    title="新增不继承的类优先 sealed",
                    message=f"类 {declaration.name} 当前没有继承关系，可评估是否需要 sealed。",
                    file=file_path,
                    line=declaration.declaration_line,
                    symbol=declaration.symbol,
                    evidence=declaration.symbol,
                )
            )
        return findings

    @staticmethod
    def _review_business_comment_language(
        file_path: str,
        added_comment_lines: tuple[tuple[int, str], ...],
    ) -> list[QualityGateSoftFinding]:
        findings: list[QualityGateSoftFinding] = []
        for line_number, text in added_comment_lines:
            if _CHINESE_PATTERN.search(text):
                continue
            findings.append(
                QualityGateSoftFinding(
                    rule_id="business_comments_chinese",
                    title="业务注释优先中文",
                    message="当前 patch 新增了非中文业务注释，建议改成简洁专业的中文。",
                    file=file_path,
                    line=line_number,
                    evidence=text,
                )
            )
        return findings

    @staticmethod
    def _review_finance_comment_terms(
        file_path: str,
        added_comment_lines: tuple[tuple[int, str], ...],
    ) -> list[QualityGateSoftFinding]:
        normalized_path = file_path.lower()
        finance_path = any(part in normalized_path for part in ("finance", "receipt", "account", "order"))
        findings: list[QualityGateSoftFinding] = []
        if not finance_path:
            return findings
        for line_number, text in added_comment_lines:
            if _CHINESE_PATTERN.search(text):
                continue
            if not _FINANCE_ENGLISH_PATTERN.search(text):
                continue
            findings.append(
                QualityGateSoftFinding(
                    rule_id="finance_terms_chinese",
                    title="财务术语保持专业中文",
                    message="当前财务/业务注释仍使用英文术语，建议改成专业中文表达。",
                    file=file_path,
                    line=line_number,
                    evidence=text,
                )
            )
        return findings
