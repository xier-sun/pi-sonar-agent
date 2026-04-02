"""Batch runner for pi-sonar-agent.

Reads targets.json and runs fix for each target.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
from pi_sonar_agent.core.db_client import create_mysql_client_from_env
from pi_sonar_agent.core.dingtalk import create_dingtalk_client_from_env
from pi_sonar_agent.core.issue_retry import process_issue_with_retries
from pi_sonar_agent.core.model_env import (
    build_agent_env,
    load_project_env,
    resolve_agent_model,
)
from pi_sonar_agent.core.pr_description import (
    PullRequestIssueSummary,
    build_local_pr_report_path,
    build_pull_request_description,
    build_repository_pr_report_path,
    build_summary_pull_request_description,
    write_markdown_report,
)
from pi_sonar_agent.core.recipient_resolution import resolve_recipients
from pi_sonar_agent.core.run_logging import (
    RunLogSession,
    format_removed_workspaces,
    run_command_logged,
)
from pi_sonar_agent.core.workspace import prune_old_workspaces
from pi_sonar_agent.fixers.build_gate import (
    format_build_failure_report,
    resolve_build_command,
    run_local_build,
)
from pi_sonar_agent.integrations.ado import AzureDevOpsClient
from pi_sonar_agent.integrations.sonar import SonarQubeClient

load_project_env()


DEFAULT_MAX_ISSUES = 3
DEFAULT_BASE_BRANCH = "develop"
DEFAULT_BUILD_TIMEOUT = 1800


def load_targets(config_path: Path) -> list[dict]:
    """Load targets from JSON config file."""
    if not config_path.exists():
        raise RuntimeError(f"未找到配置文件: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("targets.json 根节点必须是数组")

    return data


def run_for_target(target: dict) -> dict:
    """Run fix for a single target."""
    project_key = target["project_key"]
    repository = target["repository"]
    author = target["author"]
    configured_reviewer_email = str(target.get("reviewer_email", "")).strip()
    configured_dingtalk_userid = str(target.get("dingtalk_userid", "")).strip()
    max_issues = target.get("max_issues", DEFAULT_MAX_ISSUES)
    clone_branch = DEFAULT_BASE_BRANCH
    configured_target_branch = str(target.get("base_branch", "")).strip()
    target_branch = configured_target_branch or DEFAULT_BASE_BRANCH
    target_branch_source = "targets.json.base_branch" if configured_target_branch else "default"
    build_command = target.get("build_command")
    test_command = target.get("test_command")
    solution_path = str(target.get("solution_path", "")).strip() or None

    # Get env
    import os

    sonar_host = os.getenv("SONARQUBE_HOST", "http://localhost:9000")
    sonar_token = os.getenv("SONARQUBE_TOKEN")
    sonar_org = os.getenv("SONARQUBE_ORG")
    ado_base_url = os.getenv("ADO_BASE_URL")
    ado_org = os.getenv("ADO_ORG")
    ado_project = os.getenv("ADO_PROJECT")
    ado_pat = os.getenv("ADO_PAT")
    workspace_root = Path(os.getenv("WORKSPACE_ROOT", ".agent_workspaces"))

    print(f"\n{'=' * 60}")
    print(f"处理: {author} | {project_key} | {repository}")
    print(f"{'=' * 60}")
    print(f"Clone Branch: {clone_branch} (source: fixed)")
    print(f"Target Branch: {target_branch} (source: {target_branch_source})")

    print("正在解析收件人...")
    mysql_client = create_mysql_client_from_env()
    try:
        recipients = resolve_recipients(
            author=author,
            configured_reviewer_email=configured_reviewer_email,
            configured_dingtalk_userid=configured_dingtalk_userid,
            mysql_client=mysql_client,
        )
    except Exception as exc:
        print(f"[WARN] 收件人解析失败: {exc}")
        recipients = resolve_recipients(
            author=author,
            configured_reviewer_email=configured_reviewer_email,
            configured_dingtalk_userid=configured_dingtalk_userid,
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

    # Initialize clients
    sonar_client = SonarQubeClient(sonar_host, sonar_token, sonar_org)
    ado_client = AzureDevOpsClient(ado_base_url, ado_project, ado_pat, organization=ado_org)
    agent = ClaudeFixAgent(
        sonar_host,
        sonar_token,
        sonar_org,
        agent_env=build_agent_env(),
        model=resolve_agent_model(),
    )
    dingtalk = create_dingtalk_client_from_env()

    # Get issues
    issues = sonar_client.get_open_issues(project_key=project_key, author=author)
    print(f"发现 {len(issues)} 个 issues")

    if not issues:
        return {"ok": True, "issues": 0, "successful": 0, "failed": 0}

    issues = issues[:max_issues]

    # Prepare workspace
    timestamp = time.strftime("%Y%m%d%H%M%S")
    repo_url = ado_client.get_remote_url(repository)
    prune_result = prune_old_workspaces(workspace_root, keep_latest=1)
    if prune_result.removed:
        print(f"已清理 {len(prune_result.removed)} 个旧工作区")
        for removed in format_removed_workspaces(prune_result.removed):
            print(f"  - {removed}")
    if prune_result.failed:
        print(f"[WARN] 有 {len(prune_result.failed)} 个旧工作区目录未能删除，请关闭占用进程后重试")
        for failed_workspace in format_removed_workspaces(prune_result.failed):
            print(f"  - {failed_workspace}")
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = workspace_root / f"{repository}_{timestamp}"

    if workspace.exists():
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)

    try:
        run_command_logged(
            f'git clone -b {clone_branch} --single-branch "{repo_url}" "{workspace}"',
        )
    except Exception as exc:
        raise RuntimeError(f"仓库克隆失败: {exc}") from exc

    if target_branch != clone_branch:
        print(f"切换工作基线到目标分支: {target_branch}")
        try:
            run_command_logged(
                f'git fetch origin "{target_branch}"',
                cwd=workspace,
            )
            run_command_logged(
                f'git checkout -B "{target_branch}" FETCH_HEAD',
                cwd=workspace,
            )
        except Exception as exc:
            raise RuntimeError(f"目标分支准备失败: {exc}") from exc

    # Process issues
    successful = 0
    failed = 0
    skipped = 0
    issue_summaries: list[PullRequestIssueSummary] = []

    for i, issue in enumerate(issues, 1):
        rule_id = issue.get("rule", "")
        component = issue.get("component", "")
        file_path = component.split(":", 1)[-1].replace("\\", "/")
        line = issue.get("line", 0)

        print(f"\n[{i}/{len(issues)}] {rule_id} → {file_path}:{line}")

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
        result = process_issue_with_retries(
            agent=agent,
            issue=sonar_issue,
            workspace_path=workspace,
            build_command=issue_build_command,
            repository=repository,
            run_label=timestamp,
        )

        if result.success:
            suffix = f" (attempt {result.attempts})" if result.attempts > 1 else ""
            print(f"  [OK] {result.summary}{suffix}")
            successful += 1
            issue_summary_text = "已完成修复，并通过该 issue 的本地构建验证。"
            if result.attempts > 1:
                issue_summary_text = f"经过 {result.attempts} 次尝试后，已完成修复，并通过该 issue 的本地构建验证。"
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
                    changed_files=tuple(change.get("file", "") for change in result.changes if change.get("file")),
                )
            )
        else:
            label = "[SKIP]" if result.skipped else "[ERR]"
            message = result.skip_reason or result.error or "失败"
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
                    print("  [Issue Build Log]")
                    print(failure_report)
            if result.issue_log_path:
                print(f"  [Issue Log] {result.issue_log_path}")
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

    print(f"\n[Issue Summary] 成功 {successful}, 跳过 {skipped}, 失败 {failed}")

    # Build verification
    build_passed = True
    final_build_result: dict[str, object] | None = None
    if successful > 0 and not target.get("skip_build_gate"):
        print("\n[Build] 验证...")
        cmd = build_command or os.getenv("BUILD_COMMAND", "dotnet build")
        final_build_result = run_local_build(
            workspace,
            cmd,
            test_command,
            DEFAULT_BUILD_TIMEOUT,
            solution_path=solution_path,
        )
        build_passed = bool(final_build_result["succeeded"])
        print(f"  {'[OK] 通过' if build_passed else '[ERR] 失败'}")
        if not build_passed:
            failure_report = format_build_failure_report(final_build_result)
            if failure_report:
                print("[Build Log]")
                print(failure_report)

    # Create PR
    pr_url = ""
    pr_error = ""
    pr_report_markdown = ""
    local_pr_report_path: Path | None = None
    should_create_pr = build_passed and successful > 0 and not target.get("skip_build_gate")
    if successful > 0:
        pr_description = build_pull_request_description(
            author=author,
            base_branch=target_branch,
            solution_path=solution_path,
            build_command=(
                str(final_build_result.get("build_command", cmd))
                if final_build_result
                else (build_command or "")
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
                repository=repository,
                author=author,
                run_label=timestamp,
            ),
            pr_report_markdown,
        )
        print(f"  [INFO] PR 详细说明已保存: {local_pr_report_path.as_posix()}")

    if should_create_pr:
        print("\n[PR] 创建...")
        branch = f"fix/sonar-{author.split('@')[0]}-{timestamp}"
        repo_pr_report_path = build_repository_pr_report_path(
            repository=repository,
            author=author,
            run_label=timestamp,
        )
        repo_pr_report_file = write_markdown_report(
            workspace,
            repo_pr_report_path,
            pr_report_markdown,
        )
        print(f"  [INFO] PR 详细报告文件: {repo_pr_report_path}")
        print(f"  [INFO] PR 详细报告绝对路径: {repo_pr_report_file.as_posix()}")

        pr_description = build_summary_pull_request_description(
            author=author,
            base_branch=target_branch,
            solution_path=solution_path,
            build_command=(
                str(final_build_result.get("build_command", cmd))
                if final_build_result
                else (build_command or "")
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
            report_path=repo_pr_report_path,
        )

        run_command_logged(f"git checkout -b {branch}", cwd=workspace)
        run_command_logged("git add -A", cwd=workspace)
        run_command_logged(
            f'git commit -m "fix: 修复 {successful} 个 SonarQube 问题"',
            cwd=workspace,
        )
        run_command_logged(f"git push -u origin {branch}", cwd=workspace)

        try:
            pr = ado_client.create_pull_request(
                repository=repository,
                title=f"Fix: 修复 {successful} 个 SonarQube 问题",
                description=pr_description,
                source_branch=branch,
                target_branch=target_branch,
                reviewer_email=recipients.reviewer_email or None,
            )
            pr_url = pr.url
            print(f"  [OK] {pr_url}")
        except Exception as exc:
            pr_error = str(exc)
            print(f"  [WARN] PR 创建失败: {pr_error}")

    # Notify
    should_notify = bool(dingtalk and (pr_url or pr_error or successful or skipped or failed))
    if should_notify:
        try:
            dingtalk.send_run_notification(
                author,
                len(issues),
                successful,
                failed + skipped,
                pr_url,
                dingtalk_userid=recipients.dingtalk_userid,
                warning_message=(f"PR 创建失败：{pr_error}" if pr_error else None),
                force_warn=bool(pr_error),
            )
            print("  [OK] 钉钉通知发送成功")
        except Exception as exc:
            print(f"  [WARN] 钉钉通知失败: {exc}")

    # Cleanup
    if not target.get("keep_workspace") and workspace.exists():
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)

    return {
        "ok": build_passed,
        "issues": len(issues),
        "successful": successful,
        "skipped": skipped,
        "failed": failed,
        "build_passed": build_passed,
        "pr_url": pr_url,
        "pr_error": pr_error,
    }


def main():
    """Main entry point."""
    import sys

    config_path = Path("data/targets.json")

    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])

    run_label = time.strftime("%Y%m%d%H%M%S")
    with RunLogSession(run_label=run_label, prefix="batch") as log_session:
        print(f"[INFO] 运行日志: {log_session.log_path.as_posix()}")
        targets = load_targets(config_path)
        print(f"加载 {len(targets)} 个目标")

        total_successful = 0
        total_skipped = 0
        total_failed = 0
        total_prs = 0

        for target in targets:
            result = run_for_target(target)
            total_successful += result.get("successful", 0)
            total_skipped += result.get("skipped", 0)
            total_failed += result.get("failed", 0)
            if result.get("pr_url"):
                total_prs += 1

        # Summary
        print(f"\n{'=' * 60}")
        print("BATTLE REPORT")
        print(f"{'=' * 60}")
        print(f"Total Fixed     : {total_successful}")
        print(f"Total Skipped   : {total_skipped}")
        print(f"Total Failed    : {total_failed}")
        print(f"Total PRs       : {total_prs}")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
