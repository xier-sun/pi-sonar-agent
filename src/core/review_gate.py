"""Model-reviewed gate overrides for ambiguous verifier findings."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from pi_sonar_agent.core.claude_adapter import ClaudeAdapter, ClaudeSDKDependencies
from pi_sonar_agent.core.model_env import build_agent_env, resolve_agent_model
from pi_sonar_agent.core.model_gateway import ResultEvent, TextEvent
from pi_sonar_agent.core.propagation_verifier import PropagationCheckResult
from pi_sonar_agent.core.project_env import read_project_env
from pi_sonar_agent.core.quality_gate import QualityGateResult, QualityGateViolation
from pi_sonar_agent.core.state import serialize_state

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue

REVIEWABLE_QUALITY_GATE_RULE_IDS = frozenset({"cognitive_complexity"})

REVIEW_GATE_SYSTEM_PROMPT = """你是一个严格、保守、可审计的 C# 代码审核员。

你的职责只有审核，不负责改代码。

审核原则:
1. 你只能审核输入里明确列出的候选门禁发现，绝对不能新增新的 hard blocker。
2. 只有在发现“已被 patch 客观满足”或“候选发现明显误判/过宽”时，才允许 waive。
3. 只要存在不确定性，就保持严格，输出 confirm。
4. 不要讨论修复方案，不要给泛泛建议，只判断当前 patch 是否应该被这些候选发现拦下。
5. 你的输出必须是纯 JSON，不能有 markdown、解释性前缀或代码块。

输出 JSON schema:
{
  "overall_decision": "pass" | "retry",
  "summary": "一句话总结",
  "decisions": [
    {
      "finding_id": "候选 finding 的唯一 ID",
      "decision": "waive" | "confirm",
      "reason": "简洁、基于证据的判断理由"
    }
  ],
  "feedback": [
    "如果结论是 retry，给修复模型的下一轮具体反馈"
  ]
}
"""


@dataclass(frozen=True)
class ReviewGateFinding:
    """One ambiguous verifier finding that a model reviewer may audit."""

    finding_id: str
    source: str
    title: str
    message: str
    file: str = ""
    line: int = 0
    symbol: str = ""
    evidence: str = ""
    context_snippets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewGateDecision:
    """Model verdict for one reviewable finding."""

    finding_id: str
    decision: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class ReviewGateResult:
    """Structured review-gate verdict."""

    status: str
    summary: str
    invoked: bool = False
    model_display: str = ""
    findings: tuple[ReviewGateFinding, ...] = ()
    decisions: tuple[ReviewGateDecision, ...] = ()
    feedback: tuple[str, ...] = ()
    raw_response: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    @property
    def waived_finding_ids(self) -> tuple[str, ...]:
        return tuple(
            item.finding_id
            for item in self.decisions
            if item.decision == "waive" and item.finding_id
        )

    @property
    def confirmed_finding_ids(self) -> tuple[str, ...]:
        return tuple(
            item.finding_id
            for item in self.decisions
            if item.decision != "waive" and item.finding_id
        )

    def is_waived(self, finding_id: str) -> bool:
        normalized = str(finding_id or "").strip()
        return normalized in set(self.waived_finding_ids)

    def to_retry_message(self) -> str:
        if self.status != "retry":
            return ""

        lines = [
            "Review gate rejected the patch after auditing the ambiguous gate findings.",
            self.summary,
        ]
        if self.decisions:
            lines.append("Audit Decisions:")
            finding_map = {item.finding_id: item for item in self.findings}
            for index, decision in enumerate(self.decisions, start=1):
                finding = finding_map.get(decision.finding_id)
                label = decision.finding_id
                if finding is not None and finding.title:
                    label = f"{label} ({finding.title})"
                detail = f"{index}. [{decision.decision}] {label}"
                if decision.reason:
                    detail += f": {decision.reason}"
                lines.append(detail)
        if self.feedback:
            lines.append("Audit Feedback:")
            lines.extend(f"- {item}" for item in self.feedback if str(item).strip())
        if self.error:
            lines.append(f"Review Gate Error: {self.error}")
        return "\n".join(lines)


class ReviewGateAgent:
    """Run a separate, no-tools audit session for ambiguous gate findings."""

    DEFAULT_TIMEOUT_SECONDS = 90
    MAX_TURNS = 2
    MAX_BUDGET_USD = 1.0

    @staticmethod
    def _sdk_dependencies() -> ClaudeSDKDependencies:
        return ClaudeSDKDependencies(
            client_cls=ClaudeSDKClient,
            options_cls=ClaudeAgentOptions,
            assistant_message_cls=AssistantMessage,
            result_message_cls=ResultMessage,
            text_block_cls=TextBlock,
            tool_use_block_cls=ToolUseBlock,
        )

    @staticmethod
    def _is_official_anthropic_base_url(base_url: str) -> bool:
        parsed = urlparse(str(base_url or "").strip())
        host = (parsed.netloc or "").lower()
        return host.endswith("anthropic.com") or host.endswith("claude.ai")

    @classmethod
    def _build_review_gate_agent_env(cls, project_env: dict[str, str]) -> dict[str, str]:
        agent_env = build_agent_env()

        review_base_url = str(project_env.get("PI_SONAR_REVIEW_GATE_BASE_URL", "")).strip()
        review_api_key = str(project_env.get("PI_SONAR_REVIEW_GATE_API_KEY", "")).strip()
        review_auth_token = str(project_env.get("PI_SONAR_REVIEW_GATE_AUTH_TOKEN", "")).strip()

        effective_base_url = review_base_url or str(agent_env.get("ANTHROPIC_BASE_URL", "")).strip()
        if review_base_url:
            agent_env["ANTHROPIC_BASE_URL"] = review_base_url

        if review_api_key:
            agent_env["ANTHROPIC_API_KEY"] = review_api_key
            agent_env["ANTHROPIC_AUTH_TOKEN"] = review_auth_token if review_auth_token else ""
            return agent_env

        if review_auth_token:
            if effective_base_url and not cls._is_official_anthropic_base_url(effective_base_url):
                agent_env["ANTHROPIC_API_KEY"] = review_auth_token
                agent_env["ANTHROPIC_AUTH_TOKEN"] = ""
            else:
                agent_env["ANTHROPIC_AUTH_TOKEN"] = review_auth_token
                agent_env["ANTHROPIC_API_KEY"] = ""

        return agent_env

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _clip(value: str, *, max_chars: int) -> str:
        text = str(value or "").replace("\r\n", "\n").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(file_path or "").replace("\\", "/").lstrip("/")

    @classmethod
    def _snippet_for_range(
        cls,
        workspace_path: Path,
        file_path: str,
        start_line: int,
        end_line: int,
        *,
        fallback_radius: int = 6,
        max_chars: int = 2400,
    ) -> str:
        normalized_path = cls._normalize_path(file_path)
        target_path = workspace_path / normalized_path
        if not target_path.exists():
            return ""
        try:
            lines = target_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return ""

        if not lines:
            return ""

        if start_line <= 0:
            start = 1
            end = min(len(lines), fallback_radius * 2)
        else:
            start = max(1, start_line - fallback_radius)
            desired_end = end_line if end_line >= start_line else start_line
            end = min(len(lines), desired_end + fallback_radius)
        snippet = "\n".join(
            f"{index} | {lines[index - 1]}"
            for index in range(start, end + 1)
        )
        return cls._clip(snippet, max_chars=max_chars)

    @classmethod
    def _parse_symbol_range(
        cls,
        symbol: str,
        line_fallback: int,
    ) -> tuple[int, int]:
        match = re.search(r"@(\d+)-(\d+)$", str(symbol or "").strip())
        if match:
            return int(match.group(1)), int(match.group(2))
        if line_fallback > 0:
            return line_fallback, line_fallback
        return 0, 0

    @classmethod
    def _build_propagation_finding(
        cls,
        *,
        workspace_path: Path,
        edit_contract: Any,
        propagation_check_result: PropagationCheckResult,
    ) -> ReviewGateFinding:
        repair_plan = getattr(edit_contract, "repair_plan", None)
        snippets: list[str] = []
        for target in tuple(getattr(repair_plan, "verification_targets", ()) or ())[:4]:
            snippet = cls._snippet_for_range(
                workspace_path,
                getattr(target, "file", ""),
                cls._safe_int(getattr(target, "start_line", 0)),
                cls._safe_int(getattr(target, "end_line", 0)),
            )
            if not snippet:
                continue
            snippets.append(
                "\n".join(
                    [
                        f"Target: {getattr(target, 'file', '')}:{getattr(target, 'start_line', 0)}-{getattr(target, 'end_line', 0)}",
                        f"Kind: {getattr(target, 'kind', '')}",
                        snippet,
                    ]
                )
            )
        return ReviewGateFinding(
            finding_id="propagation",
            source="propagation",
            title="Signature propagation verification",
            message=propagation_check_result.summary,
            file=str(getattr(repair_plan, "primary_file", "") or ""),
            evidence=cls._clip(
                propagation_check_result.to_retry_message()
                or propagation_check_result.summary,
                max_chars=2800,
            ),
            context_snippets=tuple(snippets),
        )

    @classmethod
    def _quality_gate_finding_id(
        cls,
        violation: QualityGateViolation,
        index: int,
    ) -> str:
        return (
            f"quality_gate:{violation.rule_id}:{cls._normalize_path(violation.file)}:"
            f"{int(violation.line or 0)}:{index}"
        )

    @classmethod
    def _build_quality_gate_finding(
        cls,
        *,
        workspace_path: Path,
        violation: QualityGateViolation,
        index: int,
    ) -> ReviewGateFinding:
        start_line, end_line = cls._parse_symbol_range(violation.symbol, violation.line)
        snippet = cls._snippet_for_range(
            workspace_path,
            violation.file,
            start_line,
            end_line,
        )
        return ReviewGateFinding(
            finding_id=cls._quality_gate_finding_id(violation, index),
            source="quality_gate",
            title=violation.title,
            message=violation.message,
            file=violation.file,
            line=int(violation.line or 0),
            symbol=violation.symbol,
            evidence=cls._clip(violation.evidence, max_chars=1800),
            context_snippets=((snippet,) if snippet else ()),
        )

    @classmethod
    def collect_reviewable_findings(
        cls,
        *,
        workspace_path: Path,
        edit_contract: Any,
        propagation_check_result: PropagationCheckResult,
        quality_gate_result: QualityGateResult,
    ) -> tuple[ReviewGateFinding, ...]:
        findings: list[ReviewGateFinding] = []
        if propagation_check_result.status == "retry":
            findings.append(
                cls._build_propagation_finding(
                    workspace_path=workspace_path,
                    edit_contract=edit_contract,
                    propagation_check_result=propagation_check_result,
                )
            )
        for index, violation in enumerate(quality_gate_result.violations, start=1):
            if violation.rule_id not in REVIEWABLE_QUALITY_GATE_RULE_IDS:
                continue
            findings.append(
                cls._build_quality_gate_finding(
                    workspace_path=workspace_path,
                    violation=violation,
                    index=index,
                )
            )
        return tuple(findings)

    @classmethod
    def has_nonreviewable_blockers(
        cls,
        *,
        propagation_check_result: PropagationCheckResult,
        quality_gate_result: QualityGateResult,
        reviewer_status: str,
        rule_validation_message: str,
    ) -> bool:
        if reviewer_status == "retry":
            return True
        if rule_validation_message:
            return True
        if quality_gate_result.status == "retry":
            for violation in quality_gate_result.violations:
                if violation.rule_id not in REVIEWABLE_QUALITY_GATE_RULE_IDS:
                    return True
        return False

    @classmethod
    def apply_waivers(
        cls,
        *,
        propagation_check_result: PropagationCheckResult,
        quality_gate_result: QualityGateResult,
        review_gate_result: ReviewGateResult,
    ) -> tuple[PropagationCheckResult, QualityGateResult]:
        effective_propagation = propagation_check_result
        if propagation_check_result.status == "retry" and review_gate_result.is_waived("propagation"):
            effective_propagation = PropagationCheckResult(
                status="pass",
                summary="Review gate waived the propagation concern for this patch.",
            )

        if quality_gate_result.status != "retry":
            return effective_propagation, quality_gate_result

        remaining_violations: list[QualityGateViolation] = []
        waived_count = 0
        for index, violation in enumerate(quality_gate_result.violations, start=1):
            finding_id = cls._quality_gate_finding_id(violation, index)
            if review_gate_result.is_waived(finding_id):
                waived_count += 1
                continue
            remaining_violations.append(violation)

        if not waived_count:
            return effective_propagation, quality_gate_result

        if remaining_violations:
            effective_quality_gate = QualityGateResult(
                status="retry",
                summary=(
                    f"Quality gate still has {len(remaining_violations)} hard violation(s) "
                    f"after review gate waived {waived_count} reviewable finding(s)."
                ),
                applied_rule_ids=quality_gate_result.applied_rule_ids,
                violations=tuple(remaining_violations),
                soft_findings=quality_gate_result.soft_findings,
            )
        else:
            summary = "Hard quality gates passed after review gate waived all reviewable blockers."
            if quality_gate_result.soft_findings:
                summary += f" Recorded {len(quality_gate_result.soft_findings)} soft reviewer finding(s)."
            effective_quality_gate = QualityGateResult(
                status="pass",
                summary=summary,
                applied_rule_ids=quality_gate_result.applied_rule_ids,
                soft_findings=quality_gate_result.soft_findings,
            )
        return effective_propagation, effective_quality_gate

    @classmethod
    def _build_changed_file_payload(
        cls,
        reviewed_changes: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for change in reviewed_changes[:4]:
            diff_text = cls._clip(getattr(change, "diff_text", ""), max_chars=3200)
            if not diff_text:
                continue
            payload.append(
                {
                    "file": getattr(change, "file", ""),
                    "changed_lines": list(getattr(change, "changed_lines", ())[:40]),
                    "diff_excerpt": diff_text,
                }
            )
        return payload

    @classmethod
    def _build_user_prompt(
        cls,
        *,
        issue: SonarIssue,
        findings: tuple[ReviewGateFinding, ...],
        reviewed_changes: tuple[Any, ...],
        edit_contract: Any,
    ) -> str:
        repair_plan = getattr(edit_contract, "repair_plan", None)
        payload = {
            "issue": {
                "issue_key": issue.key,
                "rule_id": issue.rule,
                "message": issue.message,
                "file_path": issue.file_path,
                "line": issue.line,
            },
            "repair_plan": {
                "primary_file": str(getattr(repair_plan, "primary_file", "") or ""),
                "primary_method_name": str(getattr(repair_plan, "primary_method_name", "") or ""),
                "proposed_method_name": str(getattr(repair_plan, "proposed_method_name", "") or ""),
                "selected_archetype": str(getattr(repair_plan, "selected_archetype", "") or ""),
                "verification_targets": [
                    target.to_dict()
                    for target in tuple(getattr(repair_plan, "verification_targets", ()) or ())[:8]
                ],
            },
            "changed_files": cls._build_changed_file_payload(reviewed_changes),
            "candidate_findings": [item.to_dict() for item in findings],
            "instructions": [
                "只审核 candidate_findings 里的候选发现。",
                "如果某个候选发现已经被当前 patch 客观满足，或者明显属于扫描/分类过宽，输出 waive。",
                "如果无法确定，就输出 confirm。",
                "不能新增新的 hard blocker。",
                "请输出纯 JSON。",
            ],
        }
        return (
            "请审核当前 patch 是否应该被这些候选门禁发现拦下。"
            "只根据输入证据做判断，不能发明新问题。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        return raw.strip()

    @classmethod
    def _parse_response_payload(cls, raw_response: str) -> dict[str, Any]:
        text = cls._strip_json_fence(raw_response)
        if not text:
            raise ValueError("empty review response")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    @classmethod
    def _build_result_from_payload(
        cls,
        *,
        findings: tuple[ReviewGateFinding, ...],
        model_display: str,
        raw_response: str,
        payload: dict[str, Any],
    ) -> ReviewGateResult:
        finding_map = {item.finding_id: item for item in findings}
        decisions_by_id: dict[str, ReviewGateDecision] = {}
        for item in payload.get("decisions", []):
            if not isinstance(item, dict):
                continue
            finding_id = str(item.get("finding_id", "")).strip()
            if finding_id not in finding_map:
                continue
            decision = str(item.get("decision", "")).strip().lower()
            if decision not in {"waive", "confirm"}:
                decision = "confirm"
            decisions_by_id[finding_id] = ReviewGateDecision(
                finding_id=finding_id,
                decision=decision,
                reason=str(item.get("reason", "")).strip(),
            )

        # Missing per-finding decisions default to confirm.
        for finding in findings:
            decisions_by_id.setdefault(
                finding.finding_id,
                ReviewGateDecision(
                    finding_id=finding.finding_id,
                    decision="confirm",
                    reason="Review agent did not explicitly waive this finding.",
                ),
            )

        decisions = tuple(decisions_by_id[item.finding_id] for item in findings)
        all_waived = bool(decisions) and all(item.decision == "waive" for item in decisions)
        overall_decision = str(payload.get("overall_decision", "")).strip().lower()
        status = "pass" if overall_decision == "pass" and all_waived else "retry"
        feedback = tuple(
            str(item).strip()
            for item in payload.get("feedback", [])
            if str(item).strip()
        )
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            if status == "pass":
                summary = "Review gate waived all reviewable blockers."
            else:
                summary = "Review gate confirmed that at least one reviewable blocker still requires a retry."

        return ReviewGateResult(
            status=status,
            summary=summary,
            invoked=True,
            model_display=model_display,
            findings=findings,
            decisions=decisions,
            feedback=feedback,
            raw_response=raw_response,
        )

    @classmethod
    async def _review_async(
        cls,
        *,
        workspace_path: Path,
        issue: SonarIssue,
        findings: tuple[ReviewGateFinding, ...],
        reviewed_changes: tuple[Any, ...],
        edit_contract: Any,
        timeout_seconds: int,
        explicit_model: str | None,
        agent_env: dict[str, str],
    ) -> ReviewGateResult:
        gateway = ClaudeAdapter(cls._sdk_dependencies())
        user_prompt = cls._build_user_prompt(
            issue=issue,
            findings=findings,
            reviewed_changes=reviewed_changes,
            edit_contract=edit_contract,
        )
        request = ClaudeAdapter.build_request(
            agent_env=agent_env,
            explicit_model=explicit_model,
            cwd=str(workspace_path),
            system_prompt=REVIEW_GATE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            tools=(),
            allowed_tools=(),
            max_turns=cls.MAX_TURNS,
            max_budget_usd=cls.MAX_BUDGET_USD,
            stderr_handler=None,
            build_command="review_gate",
        )
        session = gateway.create_session(request)
        text_parts: list[str] = []
        agent_error = ""

        try:
            await session.connect(timeout_seconds)
            await session.send(user_prompt)
            async for event in session.stream_events():
                if isinstance(event, TextEvent) and event.text.strip():
                    text_parts.append(event.text)
                elif isinstance(event, ResultEvent) and event.agent_error:
                    agent_error = str(event.agent_error).strip()
        finally:
            with contextlib.suppress(Exception):
                await session.close()

        raw_response = "".join(text_parts).strip()
        if agent_error:
            return ReviewGateResult(
                status="retry",
                summary="Review gate session returned an agent error; kept the original blocker conservatively.",
                invoked=True,
                model_display=str(request.metadata.get("model_display", "")),
                findings=findings,
                decisions=tuple(
                    ReviewGateDecision(
                        finding_id=item.finding_id,
                        decision="confirm",
                        reason="Review agent returned an execution error.",
                    )
                    for item in findings
                ),
                raw_response=raw_response,
                error=agent_error,
            )
        try:
            payload = cls._parse_response_payload(raw_response)
        except Exception as exc:
            return ReviewGateResult(
                status="retry",
                summary="Review gate did not return valid JSON; kept the original blocker conservatively.",
                invoked=True,
                model_display=str(request.metadata.get("model_display", "")),
                findings=findings,
                decisions=tuple(
                    ReviewGateDecision(
                        finding_id=item.finding_id,
                        decision="confirm",
                        reason="Review agent response was invalid or unparsable.",
                    )
                    for item in findings
                ),
                raw_response=raw_response,
                error=str(exc),
            )
        return cls._build_result_from_payload(
            findings=findings,
            model_display=str(request.metadata.get("model_display", "")),
            raw_response=raw_response,
            payload=payload,
        )

    @classmethod
    def review(
        cls,
        *,
        workspace_path: Path,
        issue: SonarIssue,
        reviewed_changes: tuple[Any, ...],
        edit_contract: Any,
        propagation_check_result: PropagationCheckResult,
        quality_gate_result: QualityGateResult,
        reviewer_status: str,
        rule_validation_message: str,
    ) -> ReviewGateResult:
        findings = cls.collect_reviewable_findings(
            workspace_path=workspace_path,
            edit_contract=edit_contract,
            propagation_check_result=propagation_check_result,
            quality_gate_result=quality_gate_result,
        )
        if not findings:
            return ReviewGateResult(
                status="not_applicable",
                summary="No reviewable verifier findings were present for this attempt.",
            )

        if cls.has_nonreviewable_blockers(
            propagation_check_result=propagation_check_result,
            quality_gate_result=quality_gate_result,
            reviewer_status=reviewer_status,
            rule_validation_message=rule_validation_message,
        ):
            return ReviewGateResult(
                status="not_applicable",
                summary="Deterministic hard blockers were present; skipped the model review gate.",
                findings=findings,
            )

        project_env = read_project_env()
        enabled = str(project_env.get("PI_SONAR_REVIEW_GATE_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not enabled:
            return ReviewGateResult(
                status="not_applicable",
                summary="Review gate is disabled by project configuration.",
                findings=findings,
            )

        timeout_seconds = cls._safe_int(project_env.get("PI_SONAR_REVIEW_GATE_TIMEOUT_SECONDS")) or cls.DEFAULT_TIMEOUT_SECONDS
        explicit_model = (
            str(project_env.get("PI_SONAR_REVIEW_GATE_MODEL", "")).strip()
            or resolve_agent_model()
        )
        agent_env = cls._build_review_gate_agent_env(project_env)

        try:
            return asyncio.run(
                cls._review_async(
                    workspace_path=workspace_path,
                    issue=issue,
                    findings=findings,
                    reviewed_changes=reviewed_changes,
                    edit_contract=edit_contract,
                    timeout_seconds=timeout_seconds,
                    explicit_model=explicit_model,
                    agent_env=agent_env,
                )
            )
        except Exception as exc:
            return ReviewGateResult(
                status="retry",
                summary="Review gate invocation failed; kept the original blocker conservatively.",
                invoked=True,
                model_display=str(explicit_model or ""),
                findings=findings,
                decisions=tuple(
                    ReviewGateDecision(
                        finding_id=item.finding_id,
                        decision="confirm",
                        reason="Review gate invocation failed before a verdict was produced.",
                    )
                    for item in findings
                ),
                error=str(exc),
            )
