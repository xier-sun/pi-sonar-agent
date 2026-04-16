"""Shared target execution coordinator."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.git_gateway import GitRepositoryGateway
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.preflight import (
    RuntimeEnvironment,
    ensure_remote_branch_exists,
    ensure_workspace_writable,
)
from pi_sonar_agent.core.repo_capability import detect_repo_capability
from pi_sonar_agent.core.run_logging import format_removed_workspaces
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
        from pi_sonar_agent.core.model_env import build_agent_env, resolve_agent_model
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
            agent = ClaudeFixAgent(
                self.runtime_env.sonar_host,
                self.runtime_env.sonar_token,
                self.runtime_env.sonar_org,
                agent_env=build_agent_env(),
                model=resolve_agent_model(),
            )
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
        prune_result = prune_old_workspaces(self.runtime_env.workspace_root, keep_latest=1)
        if prune_result.removed:
            print(f"已清理 {len(prune_result.removed)} 个旧工作区")
            for removed in format_removed_workspaces(prune_result.removed):
                print(f"  - {removed}")
        if prune_result.failed:
            print(f"[WARN] 有 {len(prune_result.failed)} 个旧工作区目录未能删除，请关闭占用进程后重试")
            for failed_workspace in format_removed_workspaces(prune_result.failed):
                print(f"  - {failed_workspace}")
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

        issue_summaries: list[PullRequestIssueSummary] = []
        build_command = target_config.build_command or "dotnet build"
        test_command = target_config.test_command
        solution_path = target_config.solution_path

        for index, issue in enumerate(issues, 1):
            sonar_issue = SonarIssue.from_api_payload(issue)
            issue_rule = sonar_issue.rule
            issue_line = sonar_issue.start_line or sonar_issue.line
            file_path = sonar_issue.file_path.lstrip("/")
            print(
                f"\n[{index}/{len(issues)}] 修复: "
                f"{issue_rule} → {file_path}:{issue_line}"
            )

            issue_build_command = resolve_build_command(build_command, solution_path)
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
                    agent=agent,
                    issue=sonar_issue,
                    workspace_path=workspace,
                    build_command=issue_build_command,
                    repository=target_config.repository,
                    run_label=options.run_label,
                    author=target_config.author,
                    project_key=target_config.project_key,
                    state_store=self.state_store,
                )
            except Exception as exc:
                return abort_target(error=f"issue pipeline failed: {exc}", startup_failure=False)
            if isinstance(result.issue_state, IssueState):
                issue_states.append(result.issue_state)
            compliance_summary = build_compliance_summary(
                getattr(getattr(result, "edit_contract", None), "quality_gate_rules", ()),
                getattr(result, "quality_gate_result", None),
            )
            boundary_audit_summary, boundary_audit_findings, boundary_drift_score = extract_boundary_audit(result)

            if result.success:
                suffix = f" (attempt {result.attempts})" if result.attempts > 1 else ""
                print(f"  [OK] {result.summary}{suffix}")
                successful += 1
                issue_summary_text = "已完成修复，并通过该 issue 的本地构建验证。"
                if result.attempts > 1:
                    issue_summary_text = (
                        f"经过 {result.attempts} 次尝试后，已完成修复，并通过该 issue 的本地构建验证。"
                    )
                issue_summaries.append(
                    PullRequestIssueSummary(
                        status="FIXED",
                        rule=issue_rule,
                        file_path=file_path,
                        line=issue_line,
                        message=issue.get("message", ""),
                        issue_key=issue.get("key", ""),
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
                    )
                )
            else:
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
                if result.skipped:
                    if result.failure_kind == "policy_skip":
                        policy_skipped += 1
                        issue_summary_text = "该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。"
                    else:
                        skipped += 1
                        issue_summary_text = "达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。"
                    issue_summaries.append(
                        PullRequestIssueSummary(
                            status="SKIPPED",
                            rule=issue_rule,
                            file_path=file_path,
                            line=issue_line,
                            message=issue.get("message", ""),
                            issue_key=issue.get("key", ""),
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
                        )
                    )
                else:
                    failed += 1
                    issue_summary_text = "修复未完成，当前 issue 未纳入本 PR。"
                    issue_summaries.append(
                        PullRequestIssueSummary(
                            status="FAILED",
                            rule=issue_rule,
                            file_path=file_path,
                            line=issue_line,
                            message=issue.get("message", ""),
                            issue_key=issue.get("key", ""),
                            attempts=result.attempts,
                            summary=issue_summary_text,
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
                        )
                    )

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
        if successful > 0:
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

        should_create_pr = build_passed and successful > 0 and not options.skip_build
        if should_create_pr:
            print("[INFO] 创建 Pull Request...")
            branch = f"fix/sonar-{target_config.author.split('@')[0]}-{options.run_label}"
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
                    title=f"Fix: 修复 {successful} 个 SonarQube 问题",
                    description=pr_description,
                    source_branch=branch,
                    target_branch=target_config.base_branch,
                    reviewer_email=recipients.reviewer_email or None,
                )
                pr_url = pr.url
                print(f"  PR: {pr_url}")
                attachment_name = build_pr_attachment_name(
                    repository=target_config.repository,
                    author=target_config.author,
                    run_label=options.run_label,
                )
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
                print(f"[WARN] PR 创建失败: {pr_error}")

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
        if pr_url:
            print(f"PR: {pr_url}")
        elif pr_error:
            print(f"PR 创建失败: {pr_error}")

        return finalize_target_result(
            TargetRunResult(
                ok=build_passed,
                issues=len(issues),
                successful=successful,
                skipped=skipped,
                failed=failed,
                build_passed=build_passed,
                policy_skipped=policy_skipped,
                pr_url=pr_url,
                pr_error=pr_error,
            )
        )
