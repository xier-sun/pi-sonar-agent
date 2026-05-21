"""Shared target execution coordinator."""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.git_gateway import GitRepositoryGateway
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.preflight import (
    RuntimeEnvironment,
    ensure_remote_branch_exists,
    ensure_workspace_writable,
)
from pi_sonar_agent.core.repo_capability import detect_repo_capability
from pi_sonar_agent.core.run_logging import current_run_timestamp, format_removed_workspaces
from pi_sonar_agent.core.state import (
    IssueState,
    TargetState,
    derive_target_status,
    summarize_target_performance,
    utc_now_iso,
)
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.core.target_config import TargetConfig
from pi_sonar_agent.core.workspace import prune_old_workspaces


@dataclass(frozen=True)
class TargetRunOptions:
    """Execution options for a single target run."""

    run_label: str
    keep_workspace: bool = False
    skip_build: bool = False
    show_banner: bool = False
    prune_workspaces: bool = True


@dataclass(frozen=True)
class TargetRunResult:
    """Result summary for a single target run."""

    ok: bool
    issues: int
    successful: int
    skipped: int
    failed: int
    build_passed: bool
    policy_skipped: int = 0
    pr_url: str = ""
    pr_error: str = ""
    status: str = ""
    target_artifact_root: str = ""
    target_summary_path: str = ""
    target_state: TargetState | None = None


@dataclass(frozen=True)
class _IssueExecutionRecord:
    """Final execution record retained for one issue in the target summary."""

    issue_payload: dict[str, Any]
    sonar_issue: Any
    result: Any


class RunCoordinator:
    """Shared coordinator for single-target execution."""

    def __init__(self, runtime_env: RuntimeEnvironment) -> None:
        self.runtime_env = runtime_env
        self.state_store = RunStateStore()

    def run_target(
        self,
        target_config: TargetConfig,
        options: TargetRunOptions,
    ) -> TargetRunResult:
        """Run the end-to-end fix workflow for one target."""

        from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
        from pi_sonar_agent.agent.rule_policies import collect_skipped_rule_ids
        from pi_sonar_agent.core.db_client import create_mysql_client_from_env
        from pi_sonar_agent.core.dingtalk import create_dingtalk_client_from_env
        from pi_sonar_agent.core.issue_retry import process_issue_with_retries
        from pi_sonar_agent.core.model_env import (
            abort_publish_enabled,
            build_issue_model_route,
            resolve_model_tiers,
            second_pass_enabled,
        )
        from pi_sonar_agent.core.project_env import read_project_env
        from pi_sonar_agent.core.pr_description import (
            PullRequestIssueSummary,
            build_local_pr_report_path,
            build_pr_attachment_name,
            build_pull_request_description,
            build_summary_pull_request_description,
            write_markdown_report,
        )
        from pi_sonar_agent.core.quality_gate import build_compliance_summary
        from pi_sonar_agent.core.recipient_resolution import resolve_recipients
        from pi_sonar_agent.fixers.build_gate import (
            format_build_failure_report,
            resolve_build_command,
            run_local_build,
        )
        from pi_sonar_agent.integrations.ado import AzureDevOpsClient
        from pi_sonar_agent.integrations.sonar import SonarQubeClient

        if options.show_banner:
            print(f"\n{'=' * 60}")
            print(
                f"处理: {target_config.author} | "
                f"{target_config.project_key} | {target_config.repository}"
            )
            print(f"{'=' * 60}")

        print(
            f"Base Branch: {target_config.base_branch} "
            f"(source: {target_config.base_branch_source})"
        )
        artifact_writer = ArtifactWriter()
        target_started_at = utc_now_iso()
        issue_states: list[IssueState] = []
        total_issues = 0
        successful = 0
        failed = 0
        skipped = 0
        policy_skipped = 0
        issue_processing_started = False
        issue_records: dict[str, _IssueExecutionRecord] = {}
        issue_order: list[str] = []
        abort_error = ""
        abort_startup_failure = False
        abort_before_first_issue = True
        abort_publish_allowed = abort_publish_enabled()
        project_env = read_project_env()
        pr_auto_complete_enabled = str(
            project_env.get("ADO_PR_AUTO_COMPLETE_ENABLED", "false")
        ).strip().lower() in {"1", "true", "yes", "on"}
        pr_delete_source_branch_on_complete = str(
            project_env.get("ADO_PR_DELETE_SOURCE_BRANCH_ON_COMPLETE", "false")
        ).strip().lower() in {"1", "true", "yes", "on"}
        pr_auto_complete_identity_override = str(
            project_env.get("ADO_PR_AUTO_COMPLETE_IDENTITY_ID", "")
        ).strip()

        def parse_iso_datetime(value: str) -> datetime | None:
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None

        def persist_pr_business_records(
            *,
            pr_id: int | None,
            pr_url: str,
            pr_title: str,
            pr_status: str,
            source_branch: str,
            pr_description_path: str,
            pr_attachment_name: str,
            pr_attachment_url: str,
            auto_complete_set_succeeded: bool,
        ) -> None:
            db_client = getattr(self.state_store, "db_client", None)
            if db_client is None:
                return
            try:
                attempted_count = successful + skipped + failed
                effective_fix_rate_value = (
                    round(successful / attempted_count, 4)
                    if attempted_count > 0
                    else None
                )
                second_pass_issue_count_value = sum(
                    1
                    for record in issue_records.values()
                    if getattr(record.result, "issue_state", None) is not None
                    and any(
                        str(attempt.performance_metrics.get("model_route_pass", "") or "").strip() == "second_pass"
                        for attempt in record.result.issue_state.attempts
                    )
                )
                target_state_value = (
                    getattr(getattr(self.state_store, "target_state", None), "status", None)
                    or (issue_states and derive_target_status(
                        total_issues=total_issues,
                        successful=successful,
                        skipped=skipped,
                        failed=failed,
                        build_passed=build_passed,
                    ))
                )
                target_status_text = (
                    str(getattr(target_state_value, "value", target_state_value or "")).strip()
                    or ("partial" if abort_error else ("succeeded" if build_passed else "failed"))
                )
                pr_record_id = db_client.insert_pull_request_record(
                    run_label=options.run_label,
                    project_key=target_config.project_key,
                    repository=target_config.repository,
                    author=target_config.author,
                    base_branch=target_config.base_branch,
                    source_branch=source_branch,
                    pr_id=pr_id,
                    pr_url=pr_url,
                    pr_title=pr_title,
                    pr_status=pr_status,
                    pr_description_path=pr_description_path,
                    pr_attachment_name=pr_attachment_name,
                    pr_attachment_url=pr_attachment_url,
                    target_status=target_status_text,
                    partial_pr=bool(abort_error or not build_passed),
                    build_passed_before_pr=build_passed,
                    total_issues=total_issues,
                    successful_issues=successful,
                    skipped_issues=skipped,
                    failed_issues=failed,
                    policy_skipped_issues=policy_skipped,
                    first_pass_issue_count=len(issues),
                    second_pass_issue_count=second_pass_issue_count_value,
                    used_second_pass=bool(
                        second_pass_allowed and second_pass_issue_count_value > 0
                    ),
                    used_abort_publish=bool(abort_error and pr_url),
                    effective_fix_rate=effective_fix_rate_value,
                    tier1_model=str(getattr(model_tiers.get("tier1"), "display_name", "") or ""),
                    tier2_model=str(getattr(model_tiers.get("tier2"), "display_name", "") or ""),
                    auto_complete_enabled=pr_auto_complete_enabled,
                    auto_complete_set_succeeded=auto_complete_set_succeeded,
                    delete_source_branch_on_complete=pr_delete_source_branch_on_complete,
                    summary_artifact_path=str(local_pr_report_path.as_posix() if local_pr_report_path else ""),
                    error_message=pr_error or "",
                    started_at=parse_iso_datetime(target_started_at),
                    finished_at=parse_iso_datetime(utc_now_iso()),
                )

                issue_rows: list[dict[str, Any]] = []
                attempt_rows: list[dict[str, Any]] = []
                for issue_key in issue_order:
                    record = issue_records[issue_key]
                    result = record.result
                    issue_state = getattr(result, "issue_state", None)
                    if not isinstance(issue_state, IssueState):
                        continue
                    issue_payload = record.issue_payload
                    issue_summary = next((item for item in issue_summaries if item.issue_key == issue_key), None)
                    attempts = tuple(issue_state.attempts or ())
                    first_pass_result = ""
                    second_pass_result = ""
                    resolved_in_second_pass = False
                    final_tier = ""
                    build_passed_flag = False
                    post_fix_issue_status = ""
                    if isinstance(getattr(result, "post_fix_check_result", None), dict):
                        post_fix_issue_status = str(
                            result.post_fix_check_result.get("issue_status", "") or ""
                        ).strip()
                    for attempt in attempts:
                        metrics = dict(attempt.performance_metrics or {})
                        pass_name = str(metrics.get("model_route_pass", "") or "").strip()
                        tier_name = str(metrics.get("model_route_tier", "") or "").strip()
                        model_name = str(metrics.get("model_route_model", "") or "").strip()
                        if pass_name == "first_pass":
                            first_pass_result = first_pass_result or attempt.status.value
                        elif pass_name == "second_pass":
                            second_pass_result = second_pass_result or attempt.status.value
                        if tier_name:
                            final_tier = tier_name
                        build_passed_flag = build_passed_flag or bool(attempt.build_passed)
                        attempt_rows.append(
                            {
                                "run_label": options.run_label,
                                "issue_key": issue_key,
                                "attempt_number": attempt.attempt_number,
                                "pass_name": pass_name or "first_pass",
                                "tier_name": tier_name or None,
                                "model_name": model_name or None,
                                "attempt_status": attempt.status.value,
                                "failure_kind": attempt.failure_kind or None,
                                "retry_reason": str(attempt.retry_reason.value if hasattr(attempt.retry_reason, "value") else attempt.retry_reason or "") or None,
                                "retryable_failure": bool(attempt.retryable_failure),
                                "build_passed": bool(attempt.build_passed),
                                "build_verification_failed": bool(attempt.build_verification_failed),
                                "model_timeout_stage": str(metrics.get("model_timeout_stage", "") or "") or None,
                                "patch_salvaged": bool(metrics.get("patch_salvaged")),
                                "fast_path_enabled": bool(metrics.get("fast_path_enabled")),
                                "execution_profile": str(metrics.get("execution_profile", "") or getattr(result, "execution_profile", "") or "") or None,
                                "guardrail_mode": str(metrics.get("guardrail_mode", "") or getattr(result, "guardrail_mode", "") or "") or None,
                                "execution_mode": str(getattr(result, "execution_mode", "") or "") or None,
                                "changed_files_json": json.dumps(list(attempt.changed_files or ()), ensure_ascii=False),
                                "performance_metrics_json": json.dumps(metrics, ensure_ascii=False),
                                "summary": attempt.summary or None,
                                "error": attempt.error or None,
                                "skip_reason": attempt.skip_reason or None,
                                "artifact_dir": attempt.artifact_dir or None,
                                "attempt_events_path": (
                                    str(Path(attempt.artifact_dir) / "attempt_events.jsonl")
                                    if attempt.artifact_dir
                                    else None
                                ),
                                "started_at": parse_iso_datetime(attempt.started_at),
                                "finished_at": parse_iso_datetime(attempt.finished_at),
                                "duration_seconds": float(attempt.duration_seconds),
                            }
                        )
                    if issue_state.status.value == "fixed" and second_pass_result == "succeeded":
                        resolved_in_second_pass = True
                    issue_rows.append(
                        {
                            "run_label": options.run_label,
                            "project_key": target_config.project_key,
                            "repository": target_config.repository,
                            "author": target_config.author,
                            "issue_key": issue_key,
                            "rule_id": issue_state.rule_id,
                            "severity": str(issue_payload.get("severity", "") or "") or None,
                            "issue_type": str(issue_payload.get("type", "") or "") or None,
                            "file_path": issue_state.file_path,
                            "line_number": issue_state.line,
                            "message": str(issue_payload.get("message", "") or "") or None,
                            "final_status": issue_state.status.value,
                            "included_in_pr": issue_state.status.value == "fixed",
                            "attempt_count": len(attempts),
                            "final_failure_kind": issue_state.final_failure_kind or None,
                            "final_error": issue_state.final_error or None,
                            "final_skip_reason": issue_state.final_skip_reason or None,
                            "final_summary": issue_state.final_summary or None,
                            "first_pass_result": first_pass_result or None,
                            "second_pass_result": second_pass_result or None,
                            "resolved_in_second_pass": resolved_in_second_pass,
                            "tier_used_for_final_result": final_tier or None,
                            "build_passed": build_passed_flag,
                            "post_fix_issue_status": post_fix_issue_status or None,
                            "boundary_failure_code": issue_state.final_boundary_failure_code or None,
                            "secondary_boundary_failure_codes": json.dumps(
                                list(issue_state.final_secondary_boundary_failure_codes or ()),
                                ensure_ascii=False,
                            ),
                            "quality_gate_status": (
                                issue_summary.compliance_status if issue_summary else ""
                            ) or None,
                            "hard_quality_gate_failures": int(
                                issue_summary.hard_quality_gate_failures if issue_summary else 0
                            ),
                            "soft_quality_gate_findings": int(
                                issue_summary.soft_quality_gate_findings if issue_summary else 0
                            ),
                            "boundary_drift_score": int(
                                issue_summary.boundary_drift_score if issue_summary else 0
                            ),
                            "rule_review_summary_json": json.dumps(
                                list(issue_summary.rule_review_summary if issue_summary else ()),
                                ensure_ascii=False,
                            ),
                            "changed_files_json": json.dumps(
                                list(issue_summary.changed_files if issue_summary else ()),
                                ensure_ascii=False,
                            ),
                            "issue_artifact_root": issue_state.artifact_root or None,
                            "issue_log_path": (
                                issue_summary.issue_log_path if issue_summary else getattr(result, "issue_log_path", "")
                            ) or None,
                        }
                    )
                db_client.insert_pull_request_issue_records(pr_record_id, issue_rows)
                db_client.insert_pull_request_attempt_records(pr_record_id, attempt_rows)
            except Exception as exc:
                print(f"[WARN] PR 结果落库失败，但不影响当前流程：{exc}")

        def finalize_target_result(result: TargetRunResult) -> TargetRunResult:
            rollout_flags = load_performance_flags().enabled_flags()
            target_state = TargetState(
                run_label=options.run_label,
                project_key=target_config.project_key,
                repository=target_config.repository,
                author=target_config.author,
                base_branch=target_config.base_branch,
                status=derive_target_status(
                    total_issues=total_issues,
                    successful=result.successful,
                    skipped=result.skipped,
                    failed=result.failed,
                    build_passed=result.build_passed,
                ),
                issues=tuple(issue_states),
                started_at=target_started_at,
                finished_at=utc_now_iso(),
                performance_summary=summarize_target_performance(
                    tuple(issue_states),
                    rollout_flags=rollout_flags,
                ),
                rollout_flags=rollout_flags,
            )
            target_summary_path = artifact_writer.write_target_state(target_state)
            self.state_store.record_target_state(
                target_state,
                successful=result.successful,
                skipped=result.skipped,
                failed=result.failed,
                build_passed=result.build_passed,
                policy_skipped=result.policy_skipped,
                pr_url=result.pr_url,
                pr_error=result.pr_error,
                artifact_path=target_summary_path.as_posix(),
            )
            return replace(
                result,
                status=target_state.status.value,
                target_artifact_root=target_summary_path.parent.as_posix(),
                target_summary_path=target_summary_path.as_posix(),
                target_state=target_state,
            )

        def abort_target(*, error: str, startup_failure: bool) -> TargetRunResult:
            normalized_error = str(error).strip() or "target aborted"
            self.state_store.record_target_aborted(
                run_label=options.run_label,
                project_key=target_config.project_key,
                repository=target_config.repository,
                author=target_config.author,
                base_branch=target_config.base_branch,
                total_issues=total_issues,
                error=normalized_error,
                before_first_issue=not issue_processing_started,
                startup_failure=startup_failure,
                payload={
                    "scope_audit_mode": "scope_soft_audit",
                    "completed_issue_count": len(issue_states),
                },
            )
            print(f"[ERR] target aborted: {normalized_error}")
            return finalize_target_result(
                TargetRunResult(
                    ok=False,
                    issues=total_issues,
                    successful=successful,
                    skipped=skipped,
                    failed=failed,
                    build_passed=False,
                    policy_skipped=policy_skipped,
                    pr_error=normalized_error,
                )
            )

        def extract_boundary_audit(result) -> tuple[str, tuple[str, ...], int]:
            reviewer_result = getattr(result, "reviewer_result", None)
            if hasattr(reviewer_result, "to_dict"):
                reviewer_result = reviewer_result.to_dict()
            if not isinstance(reviewer_result, dict):
                return "", (), 0

            summary = str(reviewer_result.get("summary", "")).strip()
            metrics_payload = reviewer_result.get("metrics", {})
            metrics = metrics_payload if isinstance(metrics_payload, dict) else {}
            drift_score = int(metrics.get("drift_score", 0) or 0)
            findings: list[str] = []
            for item in reviewer_result.get("violations", []):
                if not isinstance(item, dict):
                    continue
                violation_type = str(item.get("type", "")).strip()
                file_path = str(item.get("file", "")).strip()
                changed_lines = [
                    int(line)
                    for line in item.get("changed_lines", [])
                    if str(line).strip()
                ]
                if violation_type == "extra_touched_file":
                    findings.append(f"额外触达文件: {file_path}")
                    continue
                if violation_type == "outside_primary_region":
                    line_preview = ", ".join(str(line) for line in changed_lines[:8]) or "无行号"
                    findings.append(f"{file_path} 主区域外变更: {line_preview}")
                    continue
                if violation_type == "forbidden_path":
                    findings.append(f"触达受保护路径: {file_path}")
                    continue
                if violation_type == "file_created":
                    findings.append(f"尝试新建文件: {file_path}")
                    continue
                if violation_type == "file_deleted":
                    findings.append(f"尝试删除文件: {file_path}")
                    continue
            return summary, tuple(dict.fromkeys(item for item in findings if item)), drift_score

        def extract_s3776_review_summary(result) -> tuple[str, ...]:
            issue_state = getattr(result, "issue_state", None)
            if str(getattr(issue_state, "rule_id", "") or "").strip() != "csharpsquid:S3776":
                return ()

            def parse_patch_summary_facts(summary_text: str) -> dict[str, str]:
                facts: dict[str, str] = {}
                for raw_line in str(summary_text or "").splitlines():
                    line = str(raw_line or "").strip()
                    if not line or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    normalized_key = str(key or "").strip().lower()
                    normalized_value = str(value or "").strip()
                    if normalized_key and normalized_value:
                        facts[normalized_key] = normalized_value
                return facts

            def load_json_file(path: Path) -> dict[str, Any]:
                if not path.exists():
                    return {}
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return {}
                return payload if isinstance(payload, dict) else {}

            def parse_method_name_from_prefetched_context(content: str) -> str:
                for raw_line in str(content or "").splitlines():
                    line = re.sub(r"^\s*\d+\s*\|\s*", "", str(raw_line or "").strip())
                    match = re.search(
                        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                        line,
                    )
                    if (
                        match
                        and any(
                            token in line
                            for token in ("public ", "private ", "protected ", "internal ")
                        )
                    ):
                        return str(match.group(1) or "").strip()
                return ""

            def parse_patch_diff(diff_text: str, *, target_method_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
                helper_names: list[str] = []
                call_names: list[str] = []
                normalized_target = str(target_method_name or "").strip()
                for raw_line in str(diff_text or "").splitlines():
                    if not raw_line.startswith("+") or raw_line.startswith("+++"):
                        continue
                    line = raw_line[1:].strip()
                    declaration_match = re.match(
                        r"(?:private|public|protected|internal)\s+(?:static\s+)?(?:async\s+)?[^\(]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                        line,
                    )
                    if declaration_match:
                        helper_name = str(declaration_match.group(1) or "").strip()
                        if helper_name and helper_name != normalized_target and helper_name not in helper_names:
                            helper_names.append(helper_name)
                        continue
                    call_match = re.search(r"\b([A-Z][A-Za-z0-9_]*)\s*\(", line)
                    if call_match:
                        call_name = str(call_match.group(1) or "").strip()
                        if call_name and call_name != normalized_target and call_name not in call_names:
                            call_names.append(call_name)
                return tuple(helper_names), tuple(call_names)

            notes: list[str] = []
            primary_method_name = ""
            repair_plan = getattr(result, "repair_plan", None) or getattr(
                getattr(result, "edit_contract", None),
                "repair_plan",
                None,
            )
            if repair_plan is not None:
                primary_method_name = str(
                    getattr(repair_plan, "primary_method_name", "") or ""
                ).strip()
                if primary_method_name:
                    notes.append(f"主要收口方法: {primary_method_name}")
                selected_archetype = str(getattr(repair_plan, "selected_archetype", "") or "").strip()
                if selected_archetype:
                    notes.append(f"采用策略: {selected_archetype}")
                fallback_archetype = str(getattr(repair_plan, "fallback_archetype", "") or "").strip()
                if fallback_archetype:
                    notes.append(f"备用策略: {fallback_archetype}")
                new_helpers = tuple(str(item).strip() for item in getattr(repair_plan, "new_helpers", ()) if str(item).strip())
                if new_helpers:
                    notes.append("新增/拆分 helper: " + ", ".join(new_helpers))
                notes.append(
                    "签名变更: " + ("是" if bool(getattr(repair_plan, "requires_signature_change", False)) else "否")
                )
                notes.append(
                    "新增类型: " + ("是" if bool(getattr(repair_plan, "requires_new_type", False)) else "否")
                )
                impact_summary = str(getattr(repair_plan, "impact_summary", "") or "").strip()
                if impact_summary:
                    notes.append(f"影响摘要: {impact_summary}")

            issue_check_method_name = ""
            post_fix_check = getattr(result, "post_fix_check_result", None)
            if isinstance(post_fix_check, dict):
                issue_check = post_fix_check.get("issue_check")
                if isinstance(issue_check, dict):
                    metrics = issue_check.get("metrics")
                    if isinstance(metrics, dict):
                        issue_check_method_name = str(metrics.get("method_name", "") or "").strip()
                        try:
                            complexity = int(metrics.get("estimated_cognitive_complexity"))
                            fail_threshold = int(metrics.get("fail_threshold"))
                        except (TypeError, ValueError):
                            complexity = 0
                            fail_threshold = 0
                        if complexity > 0 and fail_threshold > 0:
                            notes.append(
                                f"本地复杂度估计: {complexity}/{fail_threshold}"
                            )
                issue_status = str(post_fix_check.get("issue_status", "") or "").strip().upper()
                if issue_status:
                    notes.append(f"本地规则判定: {issue_status}")

            working_memory = getattr(result, "issue_working_memory", None)
            patch_summary_text = ""
            if working_memory is not None:
                patch_summary_text = str(
                    getattr(working_memory, "latest_patch_summary", "") or ""
                ).strip()
                if not patch_summary_text:
                    patch_summary_text = str(
                        getattr(working_memory, "best_known_patch_state", "") or ""
                    ).strip()
            patch_facts = parse_patch_summary_facts(patch_summary_text)
            fallback_primary_method = (
                primary_method_name
                or issue_check_method_name
                or str(patch_facts.get("target_method", "") or "").strip()
            )
            if fallback_primary_method and not any(
                note.startswith("主要收口方法: ") for note in notes
            ):
                notes.append(f"主要收口方法: {fallback_primary_method}")

            changed_methods = tuple(
                item.strip()
                for item in str(patch_facts.get("changed_methods", "") or "").split(",")
                if item.strip()
            )
            if changed_methods:
                notes.append("触达方法: " + ", ".join(changed_methods))

            scope = str(patch_facts.get("scope", "") or "").strip().lower()
            scope_label = {
                "target_method_only": "仅目标方法内收口",
                "target_file_expanded": "目标文件内扩展到相邻方法",
                "cross_file": "跨文件联动修改",
                "target_file_unclear": "目标文件内改动，精确范围待复核",
            }.get(scope, "")
            if scope_label:
                notes.append(f"改动范围: {scope_label}")

            risk_flags = tuple(
                item.strip().lower()
                for item in str(patch_facts.get("risk_flags", "") or "").split(",")
                if item.strip()
            )
            if "helper_added" in risk_flags:
                notes.append("新增/拆分 helper: 是")
            elif changed_methods:
                notes.append(
                    "新增/拆分 helper: "
                    + ("否" if len(changed_methods) <= 1 else "需结合代码复核")
                )

            target_preview = str(patch_facts.get("target_preview", "") or "").strip()
            if target_preview:
                notes.append(f"局部改动预览: {target_preview}")

            if "helper_added" in risk_flags:
                notes.append(
                    "重点审阅: 确认目标方法与新增 helper 之间只是搬移局部分支/循环逻辑，未改变业务判断条件和返回结果。"
                )
            elif scope == "target_method_only":
                notes.append(
                    "重点审阅: 确认复杂度下降主要来自条件扁平化、早返回或局部重排，而不是业务逻辑变化。"
                )
            elif scope == "target_file_expanded":
                notes.append(
                    "重点审阅: 除目标方法外被触达的相邻方法是否只是配套收口，没有扩散到额外业务逻辑。"
                )

            performance_metrics = dict(getattr(result, "performance_metrics", {}) or {})
            route_tier = str(performance_metrics.get("model_route_tier", "") or "").strip()
            route_pass = str(performance_metrics.get("model_route_pass", "") or "").strip()

            artifact_target_method = ""
            artifact_helper_names: tuple[str, ...] = ()
            artifact_call_names: tuple[str, ...] = ()
            latest_attempt = None
            if issue_state is not None:
                attempts = tuple(getattr(issue_state, "attempts", ()) or ())
                if attempts:
                    latest_attempt = attempts[-1]
            attempt_artifact_dir = Path(str(getattr(latest_attempt, "artifact_dir", "") or "").strip())
            if attempt_artifact_dir.exists():
                prompt_context_payload = load_json_file(attempt_artifact_dir / "prompt_context.json")
                prefetched_context = prompt_context_payload.get("prefetched_context")
                if isinstance(prefetched_context, list):
                    for item in prefetched_context:
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("label", "") or "").strip() != "target_method_full":
                            continue
                        artifact_target_method = parse_method_name_from_prefetched_context(
                            str(item.get("content", "") or "")
                        )
                        if artifact_target_method:
                            break
                patch_diff_path = attempt_artifact_dir / "patch.diff"
                if patch_diff_path.exists():
                    try:
                        patch_diff_text = patch_diff_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        patch_diff_text = ""
                    artifact_helper_names, artifact_call_names = parse_patch_diff(
                        patch_diff_text,
                        target_method_name=(
                            fallback_primary_method
                            or artifact_target_method
                            or issue_check_method_name
                        ),
                    )

            effective_primary_method = (
                fallback_primary_method
                or artifact_target_method
                or issue_check_method_name
            )
            if effective_primary_method and not any(
                note.startswith("主要收口方法: ") for note in notes
            ):
                notes.append(f"主要收口方法: {effective_primary_method}")

            effective_changed_methods = changed_methods or artifact_call_names
            if effective_changed_methods and not any(
                note.startswith("触达方法: ") for note in notes
            ):
                notes.append("触达方法: " + ", ".join(effective_changed_methods))

            if artifact_helper_names and not any(
                note.startswith("提取私有方法: ") for note in notes
            ):
                notes.append("提取私有方法: " + ", ".join(artifact_helper_names))

            if artifact_call_names and not any(
                note.startswith("目标方法现在通过调用: ") for note in notes
            ):
                notes.append("目标方法现在通过调用: " + ", ".join(artifact_call_names))

            if artifact_helper_names and effective_primary_method and not any(
                note.startswith("主要修改: ") for note in notes
            ):
                notes.append(
                    "主要修改: 将 "
                    f"{effective_primary_method} 中的复杂逻辑下沉到私有 helper，主方法改为编排式调用。"
                )

            if artifact_helper_names and not any(
                note.startswith("重点审阅: ") for note in notes
            ):
                notes.append(
                    "重点审阅: 确认目标方法与新增 helper 之间只是搬移局部分支/循环逻辑，未改变业务判断条件、数据聚合顺序和返回结果。"
                )

            if route_tier:
                notes.append(f"最终模型梯次: {route_tier}")
            if route_pass == "second_pass":
                notes.append("该复杂度问题在第二轮增强修复中收敛。")

            return tuple(dict.fromkeys(item for item in notes if item))

        def mark_target_abort(*, error: str, startup_failure: bool = False) -> None:
            nonlocal abort_error, abort_startup_failure, abort_before_first_issue
            normalized_error = str(error).strip() or "target aborted"
            if abort_error:
                return
            abort_error = normalized_error
            abort_startup_failure = startup_failure
            abort_before_first_issue = not issue_processing_started
            self.state_store.record_target_aborted(
                run_label=options.run_label,
                project_key=target_config.project_key,
                repository=target_config.repository,
                author=target_config.author,
                base_branch=target_config.base_branch,
                total_issues=total_issues,
                error=normalized_error,
                before_first_issue=abort_before_first_issue,
                startup_failure=startup_failure,
                payload={
                    "scope_audit_mode": "scope_soft_audit",
                    "completed_issue_count": len(issue_states),
                },
            )
            print(f"[ERR] target aborted: {normalized_error}")

        def refresh_issue_rollup() -> list[PullRequestIssueSummary]:
            nonlocal issue_states, successful, skipped, failed, policy_skipped
            issue_states = [
                record.result.issue_state
                for issue_key in issue_order
                for record in [issue_records[issue_key]]
                if isinstance(record.result.issue_state, IssueState)
            ]

            summaries: list[PullRequestIssueSummary] = []
            successful = 0
            skipped = 0
            failed = 0
            policy_skipped = 0
            for issue_key in issue_order:
                record = issue_records[issue_key]
                result = record.result
                sonar_issue = record.sonar_issue
                issue_payload = record.issue_payload
                issue_rule = sonar_issue.rule
                issue_line = sonar_issue.start_line or sonar_issue.line
                file_path = sonar_issue.file_path.lstrip("/")
                compliance_summary = build_compliance_summary(
                    getattr(getattr(result, "edit_contract", None), "quality_gate_rules", ()),
                    getattr(result, "quality_gate_result", None),
                )
                boundary_audit_summary, boundary_audit_findings, boundary_drift_score = extract_boundary_audit(result)

                if result.success:
                    successful += 1
                    local_issue_status = str(
                        getattr(getattr(result, "post_fix_check_result", None), "get", lambda _key, _default=None: None)("issue_status", None)
                        if isinstance(getattr(result, "post_fix_check_result", None), dict)
                        else getattr(getattr(result, "post_fix_check_result", None), "issue_status", "")
                    ).strip()
                    issue_summary_text = "已完成修复，并通过该 issue 的本地构建验证。"
                    if local_issue_status == "UNKNOWN":
                        issue_summary_text = "已完成修复并通过本地构建验证，但当前规则缺少可靠的本地判定器，最终状态待 Sonar 正式分析确认。"
                    if result.attempts > 1:
                        issue_summary_text = (
                            f"经过 {result.attempts} 次尝试后，已完成修复，并通过该 issue 的本地构建验证。"
                        )
                        if local_issue_status == "UNKNOWN":
                            issue_summary_text = (
                                f"经过 {result.attempts} 次尝试后，已完成修复并通过本地构建验证，但当前规则缺少可靠的本地判定器，最终状态待 Sonar 正式分析确认。"
                            )
                    summaries.append(
                        PullRequestIssueSummary(
                            status="FIXED",
                            rule=issue_rule,
                            file_path=file_path,
                            line=issue_line,
                            message=issue_payload.get("message", ""),
                            issue_key=issue_payload.get("key", ""),
                            attempts=result.attempts,
                            summary=issue_summary_text,
                            changed_files=tuple(
                                change.get("file", "") for change in result.changes if change.get("file")
                            ),
                            compliance_status=compliance_summary.status,
                            compliance_summary=compliance_summary.summary,
                            active_quality_gate_rules=tuple(
                                check.rule_id for check in compliance_summary.checks
                            ),
                            hard_quality_gate_failures=compliance_summary.failed_rule_count,
                            soft_quality_gate_findings=compliance_summary.soft_finding_count,
                            boundary_audit_summary=boundary_audit_summary,
                            boundary_audit_findings=boundary_audit_findings,
                            boundary_drift_score=boundary_drift_score,
                            rule_review_summary=extract_s3776_review_summary(result),
                        )
                    )
                    continue

                message = result.skip_reason or result.error or "修复失败"
                if result.skipped:
                    if result.failure_kind == "policy_skip":
                        policy_skipped += 1
                        issue_summary_text = "该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。"
                    else:
                        skipped += 1
                        issue_summary_text = "达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。"
                    summaries.append(
                        PullRequestIssueSummary(
                            status="SKIPPED",
                            rule=issue_rule,
                            file_path=file_path,
                            line=issue_line,
                            message=issue_payload.get("message", ""),
                            issue_key=issue_payload.get("key", ""),
                            attempts=result.attempts,
                            summary=issue_summary_text,
                            skip_reason=result.skip_reason or message,
                            issue_log_path=result.issue_log_path,
                            compliance_status=compliance_summary.status,
                            compliance_summary=compliance_summary.summary,
                            active_quality_gate_rules=tuple(
                                check.rule_id for check in compliance_summary.checks
                            ),
                            hard_quality_gate_failures=compliance_summary.failed_rule_count,
                            soft_quality_gate_findings=compliance_summary.soft_finding_count,
                            boundary_audit_summary=boundary_audit_summary,
                            boundary_audit_findings=boundary_audit_findings,
                            boundary_drift_score=boundary_drift_score,
                            rule_review_summary=extract_s3776_review_summary(result),
                        )
                    )
                    continue

                failed += 1
                summaries.append(
                    PullRequestIssueSummary(
                        status="FAILED",
                        rule=issue_rule,
                        file_path=file_path,
                        line=issue_line,
                        message=issue_payload.get("message", ""),
                        issue_key=issue_payload.get("key", ""),
                        attempts=result.attempts,
                        summary="修复未完成，当前 issue 未纳入本 PR。",
                        skip_reason=result.error or message,
                        issue_log_path=result.issue_log_path,
                        compliance_status=compliance_summary.status,
                        compliance_summary=compliance_summary.summary,
                        active_quality_gate_rules=tuple(
                            check.rule_id for check in compliance_summary.checks
                        ),
                        hard_quality_gate_failures=compliance_summary.failed_rule_count,
                        soft_quality_gate_findings=compliance_summary.soft_finding_count,
                        boundary_audit_summary=boundary_audit_summary,
                        boundary_audit_findings=boundary_audit_findings,
                        boundary_drift_score=boundary_drift_score,
                        rule_review_summary=extract_s3776_review_summary(result),
                    )
                )
            return summaries

        def _result_failure_texts(result: Any) -> str:
            parts = [
                str(getattr(result, "failure_kind", "") or "").strip(),
                str(getattr(result, "error", "") or "").strip(),
                str(getattr(result, "skip_reason", "") or "").strip(),
                str(getattr(result, "build_output", "") or "").strip(),
            ]
            return "\n".join(part for part in parts if part).lower()

        def is_model_availability_result(result: Any) -> bool:
            if bool(getattr(result, "success", False)):
                return False
            failure_kind = str(getattr(result, "failure_kind", "") or "").strip()
            if failure_kind == "model_timeout":
                return True
            if failure_kind not in {"runtime_error", "fix_agent", "review_gate"}:
                return False
            text = _result_failure_texts(result)
            markers = (
                "429",
                "rate limit",
                "too many requests",
                "quota",
                "overloaded",
                "service unavailable",
                "bad gateway",
                "gateway timeout",
                "timed out",
                "timeout",
                "connection reset",
                "connection aborted",
                "connection timed out",
                "temporarily unavailable",
                "provider unavailable",
                "未完成初始化",
                "没有返回首个响应",
                "没有返回后续响应",
            )
            return any(marker in text for marker in markers)

        def is_model_availability_exception(exc: BaseException) -> bool:
            text = str(exc or "").strip().lower()
            markers = (
                "429",
                "rate limit",
                "too many requests",
                "quota",
                "overloaded",
                "service unavailable",
                "bad gateway",
                "gateway timeout",
                "timed out",
                "timeout",
                "connection reset",
                "connection aborted",
                "connection timed out",
                "temporarily unavailable",
                "provider unavailable",
                "没有返回首个响应",
                "没有返回后续响应",
                "未完成初始化",
            )
            return any(marker in text for marker in markers)

        def should_retry_in_second_pass(result: Any) -> bool:
            if bool(getattr(result, "success", False)):
                return False
            return str(getattr(result, "failure_kind", "") or "").strip() != "policy_skip"

        def _clip_handoff_text(value: str, *, max_chars: int = 420, max_lines: int = 14) -> str:
            text = str(value or "").replace("\r\n", "\n").strip()
            if not text:
                return ""
            lines = text.splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                text = "\n".join(lines).rstrip() + "\n..."
            else:
                text = "\n".join(lines)
            if len(text) > max_chars:
                text = text[: max_chars - 3].rstrip() + "..."
            return text

        def build_model_handoff_feedback_from_result(
            *,
            result: Any,
            from_tier_name: str,
            from_tier_model: str,
            to_tier_model: str,
            reason_label: str,
        ) -> str:
            failure_kind = str(getattr(result, "failure_kind", "") or "").strip() or "unknown_failure"
            summary = (
                str(getattr(result, "skip_reason", "") or "").strip()
                or str(getattr(result, "error", "") or "").strip()
                or str(getattr(result, "summary", "") or "").strip()
            )
            details = _clip_handoff_text(
                str(getattr(result, "build_output", "") or "").strip(),
            )
            parts = [
                "【模型切梯交接】",
                f"- 来源模型: {from_tier_model or from_tier_name}",
                f"- 目标模型: {to_tier_model}",
                f"- 触发原因: {reason_label}",
                f"- 上一梯结果: {failure_kind}",
            ]
            if summary:
                parts.append(f"- 摘要: {summary}")
            if details:
                parts.extend(
                    [
                        "【失败细节摘要】",
                        details,
                    ]
                )
            parts.append(
                "【交接要求】\n"
                + "\n".join(
                    [
                        "- 当前 issue 的工作区和 working memory 已保留，请先读取当前文件状态再继续。",
                        "- 结合上面的失败信息换一种修法，不要机械重复上一梯已经失败的动作。",
                    ]
                )
            )
            return "\n".join(part for part in parts if str(part).strip())

        def audit_print(message: str) -> None:
            print(f"[{current_run_timestamp()}] {message}")

        def build_issue_marker(*, index: int, total: int, second_pass: bool) -> str:
            if second_pass:
                return f"[SECOND PASS {index}/{total}]"
            return f"[{index}/{total}]"

        def build_issue_descriptor(sonar_issue: SonarIssue) -> str:
            issue_line = sonar_issue.start_line or sonar_issue.line
            file_path = sonar_issue.file_path.lstrip("/")
            return f"{sonar_issue.rule} → {file_path}:{issue_line} ({sonar_issue.key})"

        def log_issue_start(*, index: int, total: int, sonar_issue: SonarIssue, second_pass: bool) -> None:
            audit_print(
                f"{build_issue_marker(index=index, total=total, second_pass=second_pass)} "
                f"开始修复: {build_issue_descriptor(sonar_issue)}"
            )

        def log_issue_finish(
            *,
            index: int,
            total: int,
            sonar_issue: SonarIssue,
            second_pass: bool,
            result,
            duration_seconds: float,
        ) -> None:
            marker = build_issue_marker(index=index, total=total, second_pass=second_pass)
            descriptor = build_issue_descriptor(sonar_issue)
            duration_label = f"{duration_seconds:.1f}s"
            if result.success:
                suffix = f", attempts={result.attempts}" if result.attempts > 1 else ""
                summary = str(result.summary or "修复成功").strip()
                audit_print(
                    f"{marker} 修复完成: {descriptor}; 用时={duration_label}; "
                    f"结果=success{suffix}; 摘要={summary}"
                )
                return

            outcome = "skipped" if result.skipped else "failed"
            reason = str(
                result.skip_reason or result.error or getattr(result, "failure_kind", "") or "修复失败"
            ).strip()
            failure_kind = str(getattr(result, "failure_kind", "") or "").strip()
            failure_suffix = f"; failure_kind={failure_kind}" if failure_kind else ""
            audit_print(
                f"{marker} 修复结束: {descriptor}; 用时={duration_label}; "
                f"结果={outcome}{failure_suffix}; 原因={reason}"
            )
            return

        def build_model_handoff_feedback_from_exception(
            *,
            exc: BaseException,
            from_tier_name: str,
            from_tier_model: str,
            to_tier_model: str,
            reason_label: str,
        ) -> str:
            details = _clip_handoff_text(str(exc or ""))
            parts = [
                "【模型切梯交接】",
                f"- 来源模型: {from_tier_model or from_tier_name}",
                f"- 目标模型: {to_tier_model}",
                f"- 触发原因: {reason_label}",
                "- 上一梯结果: provider/session error",
            ]
            if details:
                parts.append(f"- 摘要: {details}")
            parts.append(
                "【交接要求】\n"
                + "\n".join(
                    [
                        "- 上一梯在当前 issue 上未完成有效响应，请直接基于当前工作区状态重新接手。",
                        "- 先读取当前文件和已有提示，再继续修复，不要假设上一梯已经完成任何关键修改。",
                    ]
                )
            )
            return "\n".join(part for part in parts if str(part).strip())

        def build_second_pass_seed_feedback(*, previous_result: Any) -> str:
            failure_kind = str(getattr(previous_result, "failure_kind", "") or "").strip() or "unresolved"
            summary = (
                str(getattr(previous_result, "skip_reason", "") or "").strip()
                or str(getattr(previous_result, "error", "") or "").strip()
                or str(getattr(previous_result, "summary", "") or "").strip()
            )
            details = _clip_handoff_text(str(getattr(previous_result, "build_output", "") or "").strip())
            parts = [
                "【第二轮增强修复交接】",
                f"- 第一轮最终状态: {failure_kind}",
            ]
            if summary:
                parts.append(f"- 第一轮摘要: {summary}")
            if details:
                parts.extend(
                    [
                        "【第一轮失败细节摘要】",
                        details,
                    ]
                )
            parts.append(
                "【第二轮要求】\n"
                + "\n".join(
                    [
                        "- 当前 issue 在第一轮未解决，请基于当前工作区和已有 working memory 换一种更强策略。",
                        "- 优先避免重复第一轮已经证明无效的修法。",
                    ]
                )
            )
            return "\n".join(part for part in parts if str(part).strip())

        try:
            ado_client = AzureDevOpsClient(
                self.runtime_env.ado_base_url,
                self.runtime_env.ado_project,
                self.runtime_env.ado_pat,
                organization=self.runtime_env.ado_org,
            )
            print("[INFO] 运行启动前校验...")
            ensure_workspace_writable(self.runtime_env.workspace_root)
            repo_url = ado_client.get_remote_url(target_config.repository)
            git_gateway = GitRepositoryGateway(remote_url=repo_url, pat=self.runtime_env.ado_pat)
            ensure_remote_branch_exists(
                remote_url=repo_url,
                branch=target_config.base_branch,
                pat=self.runtime_env.ado_pat,
                git_gateway=git_gateway,
            )
            print(
                "[INFO] 启动前校验通过: "
                f"workspace={self.runtime_env.workspace_root}, "
                f"base_branch={target_config.base_branch}"
            )

            print("正在解析收件人...")
            mysql_client = create_mysql_client_from_env()
            try:
                recipients = resolve_recipients(
                    author=target_config.author,
                    configured_reviewer_email=target_config.reviewer_email,
                    configured_dingtalk_userid=target_config.dingtalk_userid,
                    mysql_client=mysql_client,
                )
            except Exception as exc:
                print(f"[WARN] 收件人解析失败: {exc}")
                recipients = resolve_recipients(
                    author=target_config.author,
                    configured_reviewer_email=target_config.reviewer_email,
                    configured_dingtalk_userid=target_config.dingtalk_userid,
                    mysql_client=None,
                )
            finally:
                if mysql_client:
                    mysql_client.disconnect()

            print(
                f"Reviewer: {recipients.reviewer_email or '(none)'} "
                f"(source: {recipients.reviewer_source})"
            )
            print(
                f"DingTalk UserId: {recipients.dingtalk_userid or '(unresolved)'} "
                f"(source: {recipients.dingtalk_source})"
            )

            sonar_client = SonarQubeClient(
                self.runtime_env.sonar_host,
                self.runtime_env.sonar_token,
                self.runtime_env.sonar_org,
            )
            model_tiers = resolve_model_tiers()
            first_pass_route = build_issue_model_route(second_pass=False)
            second_pass_route = build_issue_model_route(second_pass=True)
            second_pass_allowed = second_pass_enabled()
            if first_pass_route:
                first_pass_preview = " -> ".join(tier.display_name for tier in first_pass_route)
                print(f"[INFO] 第一轮模型路由: {first_pass_preview}")
            if second_pass_allowed and second_pass_route:
                second_pass_preview = " -> ".join(tier.display_name for tier in second_pass_route)
                print(f"[INFO] 第二轮模型路由: {second_pass_preview}")
            dingtalk_client = create_dingtalk_client_from_env()

            print("正在获取 SonarQube issues...")
            issues = sonar_client.get_open_issues(
                project_key=target_config.project_key,
                author=target_config.author,
            )
            print(f"发现 {len(issues)} 个 issues")

            skipped_rule_ids = collect_skipped_rule_ids()
            if skipped_rule_ids:
                pre_filter_count = len(issues)
                issues = [
                    item
                    for item in issues
                    if str(item.get("rule", "")).strip() not in skipped_rule_ids
                ]
                policy_filtered = pre_filter_count - len(issues)
                if policy_filtered:
                    print(
                        "已排除 "
                        f"{policy_filtered} 个策略跳过规则的 issues "
                        f"({', '.join(sorted(skipped_rule_ids))})"
                    )

            if target_config.issue_keys:
                issue_by_key = {
                    str(item.get("key", "")).strip(): item
                    for item in issues
                    if str(item.get("key", "")).strip()
                }
                filtered_issues = [
                    issue_by_key[key]
                    for key in target_config.issue_keys
                    if key in issue_by_key
                ]
                missing_issue_keys = [
                    key
                    for key in target_config.issue_keys
                    if key not in issue_by_key
                ]
                issues = filtered_issues
                print(f"按 issue_keys 过滤后保留 {len(issues)} 个")
                if missing_issue_keys:
                    preview = ", ".join(missing_issue_keys[:5])
                    if len(missing_issue_keys) > 5:
                        preview += f" ... (+{len(missing_issue_keys) - 5} more)"
                    print(f"[WARN] issue_keys 中有 {len(missing_issue_keys)} 个未命中当前 Sonar issues: {preview}")

            if target_config.skip_issue_keys:
                skip_issue_key_set = set(target_config.skip_issue_keys)
                issue_by_key_before_skip = {
                    str(item.get("key", "")).strip(): item
                    for item in issues
                    if str(item.get("key", "")).strip()
                }
                pre_filter_count = len(issues)
                issues = [
                    item
                    for item in issues
                    if str(item.get("key", "")).strip() not in skip_issue_key_set
                ]
                print(f"按 skip_issue_keys 排除后保留 {len(issues)} 个")
                missing_skip_issue_keys = [
                    key
                    for key in target_config.skip_issue_keys
                    if key not in issue_by_key_before_skip
                ]
                if missing_skip_issue_keys:
                    preview = ", ".join(missing_skip_issue_keys[:5])
                    if len(missing_skip_issue_keys) > 5:
                        preview += f" ... (+{len(missing_skip_issue_keys) - 5} more)"
                    print(
                        f"[WARN] skip_issue_keys 中有 {len(missing_skip_issue_keys)} 个未命中当前待处理 issues: {preview}"
                    )

            if target_config.max_issues > 0:
                issues = issues[:target_config.max_issues]
                print(f"限制处理 {len(issues)} 个")
            total_issues = len(issues)
            self.state_store.record_target_started(
                run_label=options.run_label,
                project_key=target_config.project_key,
                repository=target_config.repository,
                author=target_config.author,
                base_branch=target_config.base_branch,
                total_issues=total_issues,
            )
        except Exception as exc:
            return abort_target(error=str(exc), startup_failure=True)

        if not issues:
            return finalize_target_result(
                TargetRunResult(
                    ok=True,
                    issues=0,
                    successful=0,
                    skipped=0,
                    failed=0,
                    build_passed=True,
                    policy_skipped=0,
                )
            )

        print("[INFO] 准备仓库...")
        if options.prune_workspaces:
            prune_result = prune_old_workspaces(self.runtime_env.workspace_root, keep_latest=1)
            if prune_result.removed:
                print(f"已清理 {len(prune_result.removed)} 个旧工作区")
                for removed in format_removed_workspaces(prune_result.removed):
                    print(f"  - {removed}")
            if prune_result.failed:
                print(f"[WARN] 有 {len(prune_result.failed)} 个旧工作区目录未能删除，请关闭占用进程后重试")
                for failed_workspace in format_removed_workspaces(prune_result.failed):
                    print(f"  - {failed_workspace}")
        else:
            print("[INFO] 当前任务已启用并行安全模式，跳过全局工作区清理")
        self.runtime_env.workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = self.runtime_env.workspace_root / f"fix_{target_config.repository}_{options.run_label}"
        performance_flags = load_performance_flags()
        clone_depth = performance_flags.git_clone_depth or None

        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

        try:
            if clone_depth:
                print(f"[INFO] 使用浅克隆准备仓库: depth={clone_depth}")
            else:
                print("[INFO] 使用完整历史克隆准备仓库")
            git_gateway.clone_branch(
                workspace,
                target_config.base_branch,
                depth=clone_depth,
            )
            if hasattr(git_gateway, "install_local_excludes"):
                git_gateway.install_local_excludes(workspace)
        except Exception as exc:
            return abort_target(error=f"仓库克隆失败: {exc}", startup_failure=True)

        try:
            repo_capability = detect_repo_capability(workspace)
            runtime_dir = workspace / ".pi-sonar-agent-runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            capability_path = runtime_dir / "repo_capability.json"
            capability_path.write_text(
                json.dumps(repo_capability.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"[INFO] 仓库能力指纹: {repo_capability.summary()}")
            print(f"[INFO] 仓库能力工件已写入: {capability_path.relative_to(workspace).as_posix()}")
        except Exception as exc:
            print(f"[WARN] 仓库能力指纹生成失败: {exc}")

        build_command = target_config.build_command or "dotnet build"
        test_command = target_config.test_command
        solution_path = target_config.solution_path
        def build_agent_for_tier(tier) -> ClaudeFixAgent:
            return ClaudeFixAgent(
                self.runtime_env.sonar_host,
                self.runtime_env.sonar_token,
                self.runtime_env.sonar_org,
                agent_env=dict(tier.agent_env),
                model=tier.explicit_model,
            )

        def annotate_routed_result(result, *, tier_name: str, tier_model: str, pass_name: str) -> None:
            performance_metrics = dict(getattr(result, "performance_metrics", {}) or {})
            performance_metrics.update(
                {
                    "model_route_tier": tier_name,
                    "model_route_model": tier_model,
                    "model_route_pass": pass_name,
                }
            )
            result.performance_metrics = performance_metrics

        def log_issue_outcome(result) -> None:
            if result.success:
                suffix = f" (attempt {result.attempts})" if result.attempts > 1 else ""
                print(f"  [OK] {result.summary}{suffix}")
                return

            label = "[SKIP]" if result.skipped else "[ERR]"
            message = result.skip_reason or result.error or "修复失败"
            print(f"  {label} {message}")
            if result.build_output:
                failure_report = format_build_failure_report(
                    {
                        "error": result.error or "",
                        "build_command": result.build_command,
                        "build_output": result.build_output,
                    },
                    max_lines=40,
                )
                if failure_report:
                    print("  [ISSUE BUILD LOG]")
                    print(failure_report)
            if result.issue_log_path:
                print(f"  [ISSUE LOG] {result.issue_log_path}")

        def run_issue_with_model_route(
            *,
            issue_payload: dict[str, Any],
            sonar_issue: SonarIssue,
            issue_build_command: str,
            second_pass: bool,
            seed_retry_feedback: str = "",
        ):
            nonlocal issue_processing_started
            route = second_pass_route if second_pass else first_pass_route
            pass_name = "second_pass" if second_pass else "first_pass"
            pass_label = "第二轮" if second_pass else "第一轮"
            last_exception: BaseException | None = None
            last_result = None
            handoff_feedback = str(seed_retry_feedback or "").strip()
            for route_index, tier in enumerate(route, start=1):
                issue_line = sonar_issue.start_line or sonar_issue.line
                audit_print(
                    f"[MODEL] {pass_label} issue={sonar_issue.key} "
                    f"{route_index}/{len(route)}: {tier.display_name}"
                )
                self.state_store.record_issue_started(
                    run_label=options.run_label,
                    repository=target_config.repository,
                    author=target_config.author,
                    project_key=target_config.project_key,
                    issue_key=sonar_issue.key,
                    rule_id=sonar_issue.rule,
                    file_path=sonar_issue.file_path,
                    line_number=issue_line,
                )
                issue_processing_started = True
                try:
                    result = process_issue_with_retries(
                        agent=build_agent_for_tier(tier),
                        issue=sonar_issue,
                        workspace_path=workspace,
                        build_command=issue_build_command,
                        repository=target_config.repository,
                        run_label=options.run_label,
                        author=target_config.author,
                        project_key=target_config.project_key,
                        state_store=self.state_store,
                        seed_retry_feedback=handoff_feedback,
                        second_pass=second_pass,
                    )
                except Exception as exc:
                    if is_model_availability_exception(exc) and route_index < len(route):
                        last_exception = exc
                        next_tier = route[route_index]
                        handoff_feedback = build_model_handoff_feedback_from_exception(
                            exc=exc,
                            from_tier_name=tier.tier_name,
                            from_tier_model=tier.display_name,
                            to_tier_model=next_tier.display_name,
                            reason_label="provider unavailable / timeout",
                        )
                        audit_print(
                            f"[WARN] 当前模型不可用，切换到下一梯: "
                            f"{tier.display_name} -> {next_tier.display_name} ({exc})"
                        )
                        continue
                    raise

                annotate_routed_result(
                    result,
                    tier_name=tier.tier_name,
                    tier_model=tier.display_name,
                    pass_name=pass_name,
                )
                if is_model_availability_result(result) and route_index < len(route):
                    last_result = result
                    next_tier = route[route_index]
                    handoff_feedback = build_model_handoff_feedback_from_result(
                        result=result,
                        from_tier_name=tier.tier_name,
                        from_tier_model=tier.display_name,
                        to_tier_model=next_tier.display_name,
                        reason_label="model availability failure",
                    )
                    audit_print(
                        f"[WARN] 当前模型不可用，切换到下一梯: "
                        f"{tier.display_name} -> {next_tier.display_name}"
                    )
                    continue
                return result

            if last_result is not None:
                return last_result
            if last_exception is not None:
                raise last_exception
            raise RuntimeError("No configured model tier is available for the current issue.")

        for index, issue in enumerate(issues, 1):
            if abort_error:
                break
            sonar_issue = SonarIssue.from_api_payload(issue)
            print()
            log_issue_start(index=index, total=len(issues), sonar_issue=sonar_issue, second_pass=False)

            issue_build_command = resolve_build_command(build_command, solution_path)
            issue_started_monotonic = time.monotonic()
            try:
                result = run_issue_with_model_route(
                    issue_payload=issue,
                    sonar_issue=sonar_issue,
                    issue_build_command=issue_build_command,
                    second_pass=False,
                    seed_retry_feedback="",
                )
            except Exception as exc:
                audit_print(
                    f"{build_issue_marker(index=index, total=len(issues), second_pass=False)} "
                    f"异常中断: {build_issue_descriptor(sonar_issue)}; 错误={exc}"
                )
                mark_target_abort(error=f"issue pipeline failed: {exc}", startup_failure=False)
                break

            issue_records[sonar_issue.key] = _IssueExecutionRecord(
                issue_payload=issue,
                sonar_issue=sonar_issue,
                result=result,
            )
            if sonar_issue.key not in issue_order:
                issue_order.append(sonar_issue.key)
            log_issue_outcome(result)
            log_issue_finish(
                index=index,
                total=len(issues),
                sonar_issue=sonar_issue,
                second_pass=False,
                result=result,
                duration_seconds=time.monotonic() - issue_started_monotonic,
            )
            refresh_issue_rollup()
            if is_model_availability_result(result):
                mark_target_abort(
                    error=(
                        f"all model tiers were unavailable while processing issue "
                        f"{sonar_issue.key}: {result.error or result.skip_reason or result.failure_kind}"
                    ),
                    startup_failure=False,
                )
                break

        if (
            not abort_error
            and second_pass_allowed
            and model_tiers["tier2"].configured
            and tuple(item.display_name for item in second_pass_route) != tuple(item.display_name for item in first_pass_route)
        ):
            unresolved_issues = [
                issue_records[issue_key].issue_payload
                for issue_key in issue_order
                if should_retry_in_second_pass(issue_records[issue_key].result)
            ]
            if unresolved_issues:
                print()
                audit_print(
                    f"[INFO] 第一轮结束后仍有 {len(unresolved_issues)} 个 unresolved issues，开始第二轮增强修复..."
                )
            for index, issue in enumerate(unresolved_issues, 1):
                if abort_error:
                    break
                sonar_issue = SonarIssue.from_api_payload(issue)
                print()
                log_issue_start(
                    index=index,
                    total=len(unresolved_issues),
                    sonar_issue=sonar_issue,
                    second_pass=True,
                )
                issue_build_command = resolve_build_command(build_command, solution_path)
                issue_started_monotonic = time.monotonic()
                try:
                    previous_result = issue_records[sonar_issue.key].result
                    result = run_issue_with_model_route(
                        issue_payload=issue,
                        sonar_issue=sonar_issue,
                        issue_build_command=issue_build_command,
                        second_pass=True,
                        seed_retry_feedback=build_second_pass_seed_feedback(
                            previous_result=previous_result,
                        ),
                    )
                except Exception as exc:
                    audit_print(
                        f"{build_issue_marker(index=index, total=len(unresolved_issues), second_pass=True)} "
                        f"异常中断: {build_issue_descriptor(sonar_issue)}; 错误={exc}"
                    )
                    mark_target_abort(error=f"second-pass issue pipeline failed: {exc}", startup_failure=False)
                    break
                issue_records[sonar_issue.key] = _IssueExecutionRecord(
                    issue_payload=issue,
                    sonar_issue=sonar_issue,
                    result=result,
                )
                log_issue_outcome(result)
                log_issue_finish(
                    index=index,
                    total=len(unresolved_issues),
                    sonar_issue=sonar_issue,
                    second_pass=True,
                    result=result,
                    duration_seconds=time.monotonic() - issue_started_monotonic,
                )
                refresh_issue_rollup()
                if is_model_availability_result(result):
                    mark_target_abort(
                        error=(
                            f"all model tiers were unavailable during second pass for issue "
                            f"{sonar_issue.key}: {result.error or result.skip_reason or result.failure_kind}"
                        ),
                        startup_failure=False,
                    )
                    break

        issue_summaries = refresh_issue_rollup()

        attempted = successful + skipped + failed
        effective_rate = (
            f"{successful}/{attempted} ({int(successful * 100 / attempted)}%)"
            if attempted
            else "N/A"
        )
        print(
            f"\n[INFO] 修复完成: 成功 {successful}, 跳过 {skipped}, "
            f"失败 {failed}, 策略排除 {policy_skipped}, 有效修复率 {effective_rate}"
        )

        build_passed = True
        final_build_result: dict[str, object] | None = None
        if successful > 0 and not options.skip_build:
            print("[INFO] 运行构建验证...")
            final_build_result = run_local_build(
                workspace,
                build_command,
                test_command,
                1800,
                solution_path=solution_path,
            )
            build_passed = bool(final_build_result["succeeded"])
            print(f"  构建: {'[OK] 通过' if build_passed else '[ERR] 失败'}")
            if not build_passed:
                failure_report = format_build_failure_report(final_build_result)
                if failure_report:
                    print("  [BUILD LOG]")
                    print(failure_report)

        pr_url = ""
        pr_error = ""
        pr_report_markdown = ""
        local_pr_report_path: Path | None = None
        pr_attachment_name = ""
        pr_attachment_url = ""
        created_pr_id: int | None = None
        created_pr_title = ""
        created_pr_status = ""
        created_pr_source_branch = ""
        auto_complete_set_succeeded = False
        if issue_records:
            pr_description = build_pull_request_description(
                author=target_config.author,
                base_branch=target_config.base_branch,
                solution_path=solution_path,
                build_command=(
                    str(final_build_result.get("build_command", build_command))
                    if final_build_result
                    else build_command
                ),
                test_command=(
                    str(final_build_result.get("test_command", test_command or "")) or None
                    if final_build_result
                    else test_command
                ),
                successful=successful,
                skipped=skipped,
                failed=failed,
                policy_skipped=policy_skipped,
                build_passed=build_passed,
                issue_summaries=issue_summaries,
            )
            pr_report_markdown = pr_description
            local_pr_report_path = write_markdown_report(
                Path("."),
                build_local_pr_report_path(
                    repository=target_config.repository,
                    author=target_config.author,
                    run_label=options.run_label,
                ),
                pr_report_markdown,
            )
            print(f"[INFO] PR 详细说明已保存: {local_pr_report_path.as_posix()}")

        should_create_pr = (
            (build_passed and successful > 0 and not options.skip_build)
            or (bool(abort_error) and abort_publish_allowed and successful > 0)
        )
        if should_create_pr:
            if abort_error:
                print("[INFO] 检测到 target 中断，优先发布已修结果...")
            print("[INFO] 创建 Pull Request...")
            branch = f"fix/sonar-{target_config.author.split('@')[0]}-{options.run_label}"
            pr_title = (
                f"Partial Fix: 修复 {successful} 个 SonarQube 问题"
                if abort_error or not build_passed
                else f"Fix: 修复 {successful} 个 SonarQube 问题"
            )
            pr_description = build_summary_pull_request_description(
                author=target_config.author,
                base_branch=target_config.base_branch,
                solution_path=solution_path,
                build_command=(
                    str(final_build_result.get("build_command", build_command))
                    if final_build_result
                    else build_command
                ),
                test_command=(
                    str(final_build_result.get("test_command", test_command or "")) or None
                    if final_build_result
                    else test_command
                ),
                successful=successful,
                skipped=skipped,
                failed=failed,
                policy_skipped=policy_skipped,
                build_passed=build_passed,
                issue_summaries=issue_summaries,
            )

            try:
                git_gateway.publish_branch(
                    workspace,
                    branch,
                    f"fix: 修复 {successful} 个 SonarQube 问题",
                )
                pr = ado_client.create_pull_request(
                    repository=target_config.repository,
                    title=pr_title,
                    description=pr_description,
                    source_branch=branch,
                    target_branch=target_config.base_branch,
                    reviewer_email=recipients.reviewer_email or None,
                )
                created_pr_id = pr.pr_id
                created_pr_title = pr_title
                created_pr_status = "created"
                created_pr_source_branch = branch
                pr_url = pr.url
                print(f"  PR: {pr_url}")
                if pr_auto_complete_enabled and hasattr(ado_client, "set_pull_request_auto_complete"):
                    auto_complete_identity_id = (
                        pr_auto_complete_identity_override or str(getattr(pr, "created_by_id", "") or "").strip()
                    )
                    if auto_complete_identity_id:
                        try:
                            ado_client.set_pull_request_auto_complete(
                                repository=target_config.repository,
                                pull_request_id=pr.pr_id,
                                identity_id=auto_complete_identity_id,
                                delete_source_branch=pr_delete_source_branch_on_complete,
                            )
                            auto_complete_set_succeeded = True
                            created_pr_status = "auto_complete_set"
                            delete_text = "，完成后删除源分支" if pr_delete_source_branch_on_complete else ""
                            print(f"[INFO] PR 已设置自动完成{delete_text}")
                        except Exception as exc:
                            print(f"[WARN] 设置 PR 自动完成失败，但不影响当前 PR：{exc}")
                    else:
                        print("[WARN] 未解析到可用的 ADO identity，跳过 PR 自动完成设置")
                attachment_name = build_pr_attachment_name(
                    repository=target_config.repository,
                    author=target_config.author,
                    run_label=options.run_label,
                )
                pr_attachment_name = attachment_name
                try:
                    attachment = ado_client.upload_pull_request_attachment(
                        repository=target_config.repository,
                        pull_request_id=pr.pr_id,
                        file_name=attachment_name,
                        content=pr_report_markdown,
                    )
                    print(
                        "[INFO] PR 详细报告附件已上传: "
                        f"{attachment.file_name} -> {attachment.url}"
                    )
                    pr_attachment_name = attachment.file_name
                    pr_attachment_url = attachment.url
                    updated_description = build_summary_pull_request_description(
                        author=target_config.author,
                        base_branch=target_config.base_branch,
                        solution_path=solution_path,
                        build_command=(
                            str(final_build_result.get("build_command", build_command))
                            if final_build_result
                            else build_command
                        ),
                        test_command=(
                            str(final_build_result.get("test_command", test_command or "")) or None
                            if final_build_result
                            else test_command
                        ),
                        successful=successful,
                        skipped=skipped,
                        failed=failed,
                        policy_skipped=policy_skipped,
                        build_passed=build_passed,
                        issue_summaries=issue_summaries,
                        report_attachment_name=attachment.file_name,
                        report_attachment_url=attachment.url,
                    )
                    ado_client.update_pull_request_description(
                        repository=target_config.repository,
                        pull_request_id=pr.pr_id,
                        description=updated_description,
                    )
                except Exception as exc:
                    warning = f"PR 已创建，但详细报告附件上传失败: {exc}"
                    print(f"[WARN] {warning}")
                    pr_error = warning
            except Exception as exc:
                pr_error = str(exc)
                created_pr_title = pr_title
                created_pr_status = "failed"
                created_pr_source_branch = branch
                print(f"[WARN] PR 创建失败: {pr_error}")

        if abort_error and not pr_url and not pr_error:
            pr_error = abort_error

        if issue_records:
            if not created_pr_status:
                if should_create_pr:
                    created_pr_status = "failed" if pr_error else "created"
                else:
                    created_pr_status = "not_created"
            persist_pr_business_records(
                pr_id=created_pr_id,
                pr_url=pr_url,
                pr_title=created_pr_title or (
                    (
                        f"Partial Fix: 修复 {successful} 个 SonarQube 问题"
                        if successful > 0 and (abort_error or not build_passed)
                        else f"Fix: 修复 {successful} 个 SonarQube 问题"
                    )
                    if successful > 0
                    else f"No PR Created: {target_config.author} Sonar 修复结果留档"
                ),
                pr_status=created_pr_status,
                source_branch=created_pr_source_branch or (
                    f"fix/sonar-{target_config.author.split('@')[0]}-{options.run_label}"
                ),
                pr_description_path=local_pr_report_path.as_posix() if local_pr_report_path else "",
                pr_attachment_name=pr_attachment_name,
                pr_attachment_url=pr_attachment_url,
                auto_complete_set_succeeded=auto_complete_set_succeeded,
            )

        should_notify = bool(dingtalk_client and pr_url)
        if should_notify:
            try:
                dingtalk_client.send_run_notification(
                    author=target_config.author,
                    total_issues=len(issues),
                    successful=successful,
                    skipped=skipped,
                    failed=failed,
                    policy_skipped=policy_skipped,
                    pr_url=pr_url,
                    dingtalk_userid=recipients.dingtalk_userid,
                    warning_message=(f"PR 创建失败：{pr_error}" if pr_error else None),
                    force_warn=bool(pr_error),
                )
                print("[INFO] 钉钉通知发送成功")
            except Exception as exc:
                print(f"[WARN] 通知失败: {exc}")

        if not options.keep_workspace and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

        print(f"\n{'=' * 50}")
        print(
            f"完成! 成功: {successful}, 跳过: {skipped}, "
            f"失败: {failed}, 策略排除: {policy_skipped}, "
            f"构建: {'通过' if build_passed else '失败'}"
        )
        if abort_error:
            print(f"中断原因: {abort_error}")
        if pr_url:
            print(f"PR: {pr_url}")
        elif pr_error:
            print(f"PR 创建失败: {pr_error}")

        return finalize_target_result(
            TargetRunResult(
                ok=build_passed and not bool(abort_error),
                issues=total_issues,
                successful=successful,
                skipped=skipped,
                failed=failed,
                build_passed=build_passed,
                policy_skipped=policy_skipped,
                pr_url=pr_url,
                pr_error=pr_error,
            )
        )
