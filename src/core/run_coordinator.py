"""Shared target execution coordinator."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.git_gateway import GitRepositoryGateway
from pi_sonar_agent.core.preflight import (
    RuntimeEnvironment,
    ensure_remote_branch_exists,
    ensure_workspace_writable,
)
from pi_sonar_agent.core.run_logging import format_removed_workspaces
from pi_sonar_agent.core.state import (
    IssueState,
    TargetState,
    derive_target_status,
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

        def finalize_target_result(result: TargetRunResult) -> TargetRunResult:
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
            )
            target_summary_path = artifact_writer.write_target_state(target_state)
            self.state_store.record_target_state(
                target_state,
                successful=result.successful,
                skipped=result.skipped,
                failed=result.failed,
                build_passed=result.build_passed,
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

        if not issues:
            return finalize_target_result(
                TargetRunResult(
                    ok=True,
                    issues=0,
                    successful=0,
                    skipped=0,
                    failed=0,
                    build_passed=True,
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

        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

        try:
            git_gateway.clone_branch(workspace, target_config.base_branch)
        except Exception as exc:
            raise RuntimeError(f"仓库克隆失败: {exc}") from exc

        successful = 0
        failed = 0
        skipped = 0
        issue_summaries: list[PullRequestIssueSummary] = []
        build_command = target_config.build_command or "dotnet build"
        test_command = target_config.test_command
        solution_path = target_config.solution_path

        for index, issue in enumerate(issues, 1):
            rule_id = issue.get("rule", "")
            component = issue.get("component", "")
            file_path = component.split(":", 1)[-1].replace("\\", "/")
            line = issue.get("line", 0)

            print(f"\n[{index}/{len(issues)}] 修复: {rule_id} → {file_path}:{line}")

            sonar_issue = SonarIssue(
                key=issue.get("key", ""),
                rule=rule_id,
                message=issue.get("message", ""),
                line=line,
                component=component,
                severity=issue.get("severity", ""),
                issue_type=issue.get("type", ""),
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
                line_number=sonar_issue.line,
            )
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
            if isinstance(result.issue_state, IssueState):
                issue_states.append(result.issue_state)

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
                        rule=rule_id,
                        file_path=file_path,
                        line=line,
                        message=issue.get("message", ""),
                        issue_key=issue.get("key", ""),
                        attempts=result.attempts,
                        summary=issue_summary_text,
                        changed_files=tuple(
                            change.get("file", "") for change in result.changes if change.get("file")
                        ),
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
                    skipped += 1
                    if result.failure_kind == "policy_skip":
                        issue_summary_text = "该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。"
                    else:
                        issue_summary_text = "达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。"
                    issue_summaries.append(
                        PullRequestIssueSummary(
                            status="SKIPPED",
                            rule=rule_id,
                            file_path=file_path,
                            line=line,
                            message=issue.get("message", ""),
                            issue_key=issue.get("key", ""),
                            attempts=result.attempts,
                            summary=issue_summary_text,
                            skip_reason=result.skip_reason or message,
                            issue_log_path=result.issue_log_path,
                        )
                    )
                else:
                    failed += 1
                    issue_summary_text = "修复未完成，当前 issue 未纳入本 PR。"
                    issue_summaries.append(
                        PullRequestIssueSummary(
                            status="FAILED",
                            rule=rule_id,
                            file_path=file_path,
                            line=line,
                            message=issue.get("message", ""),
                            issue_key=issue.get("key", ""),
                            attempts=result.attempts,
                            summary=issue_summary_text,
                            skip_reason=result.error or message,
                            issue_log_path=result.issue_log_path,
                        )
                    )

        print(f"\n[INFO] 修复完成: 成功 {successful}, 跳过 {skipped}, 失败 {failed}")

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
                build_passed=build_passed,
                issue_summaries=issue_summaries,
            )

            git_gateway.publish_branch(
                workspace,
                branch,
                f"fix: 修复 {successful} 个 SonarQube 问题",
            )

            try:
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
            f"失败: {failed}, 构建: {'通过' if build_passed else '失败'}"
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
                pr_url=pr_url,
                pr_error=pr_error,
            )
        )
