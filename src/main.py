"""Main entry point for pi-sonar-agent.

All fixes are handled by Claude Code Agent - simple and powerful.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from pi_sonar_agent.core.model_env import (
    build_agent_env,
    load_project_env,
    resolve_agent_model,
    validate_agent_env,
)
from pi_sonar_agent.core.run_logging import (
    RunLogSession,
    format_removed_workspaces,
    run_command_logged,
)
from pi_sonar_agent.core.workspace import prune_old_workspaces

# Load environment variables
load_project_env()


DEFAULT_MAX_ISSUES = 0
DEFAULT_BASE_BRANCH = "develop"
DEFAULT_BUILD_TIMEOUT = 1800


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="使用 Claude Code Agent 自动修复 SonarQube 问题")
    parser.add_argument("--project-key", help="SonarQube 项目 Key")
    parser.add_argument("--repository", help="Azure DevOps 仓库名")
    parser.add_argument("--author", help="处理该作者的 issue")
    parser.add_argument("--max-issues", type=int, help="最大处理数量")
    parser.add_argument("--base-branch", help="基线分支")
    parser.add_argument("--build-command", help="构建命令")
    parser.add_argument("--test-command", help="测试命令")
    parser.add_argument("--solution-path", help=".sln 或 .csproj 路径")
    parser.add_argument("--workspace-root", default=".agent_workspaces", help="工作区目录")
    parser.add_argument("--keep-workspace", action="store_true", help="保留工作区")
    parser.add_argument("--skip-build", action="store_true", help="跳过构建验证")
    return parser.parse_args()


def require_env(name: str) -> str:
    """Get required environment variable."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量: {name}")
    return value


def load_default_target() -> dict[str, str]:
    """Load first target from data/targets.json for zero-arg runs."""
    target_file = Path("data/targets.json")
    if not target_file.exists():
        return {}
    try:
        import json

        data = json.loads(target_file.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            item = data[0]
            if isinstance(item, dict):
                return {
                    "project_key": str(item.get("project_key", "")).strip(),
                    "repository": str(item.get("repository", "")).strip(),
                    "author": str(item.get("author", "")).strip(),
                    "reviewer_email": str(item.get("reviewer_email", "")).strip(),
                    "dingtalk_userid": str(item.get("dingtalk_userid", "")).strip(),
                    "base_branch": str(item.get("base_branch", "")).strip(),
                    "build_command": str(item.get("build_command", "")).strip(),
                    "test_command": str(item.get("test_command", "")).strip(),
                    "solution_path": str(item.get("solution_path", "")).strip(),
                    "max_issues": str(item.get("max_issues", "")).strip(),
                }
    except Exception:
        return {}
    return {}


def main():
    """Main entry point."""
    args = parse_args()
    target_defaults = load_default_target()
    run_label = time.strftime("%Y%m%d%H%M%S")

    with RunLogSession(run_label=run_label) as log_session:
        print(f"[INFO] 运行日志: {log_session.log_path.as_posix()}")

        # Defer heavy imports so `python run.py --help` works even before optional deps are ready.
        from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
        from pi_sonar_agent.core.db_client import create_mysql_client_from_env
        from pi_sonar_agent.core.dingtalk import create_dingtalk_client_from_env
        from pi_sonar_agent.core.issue_retry import process_issue_with_retries
        from pi_sonar_agent.core.pr_description import (
            PullRequestIssueSummary,
            build_local_pr_report_path,
            build_pull_request_description,
            build_repository_pr_report_path,
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

        # Load config
        sonar_host = require_env("SONARQUBE_HOST")
        sonar_token = require_env("SONARQUBE_TOKEN")
        sonar_org = os.getenv("SONARQUBE_ORG", "").strip() or None

        ado_base_url = require_env("ADO_BASE_URL")
        ado_org = os.getenv("ADO_ORG", "").strip() or None
        ado_project = require_env("ADO_PROJECT")
        ado_pat = require_env("ADO_PAT")

        model_env_errors = validate_agent_env()
        if model_env_errors:
            raise RuntimeError("模型配置无效:\n- " + "\n- ".join(model_env_errors))

        project_key = (
            (args.project_key or "").strip()
            or os.getenv("PROJECT_KEY", "").strip()
            or target_defaults.get("project_key", "")
        )
        repository = (
            (args.repository or "").strip()
            or os.getenv("REPOSITORY", "").strip()
            or target_defaults.get("repository", "")
        )
        author = (
            (args.author or "").strip()
            or os.getenv("AUTHOR", "").strip()
            or target_defaults.get("author", "")
        )
        configured_reviewer_email = target_defaults.get("reviewer_email", "")
        configured_dingtalk_userid = target_defaults.get("dingtalk_userid", "")
        clone_branch = DEFAULT_BASE_BRANCH
        clone_branch_source = "fixed"
        if (args.base_branch or "").strip():
            target_branch = (args.base_branch or "").strip()
            target_branch_source = "args.base_branch"
        elif target_defaults.get("base_branch", ""):
            target_branch = target_defaults.get("base_branch", "")
            target_branch_source = "targets.json.base_branch"
        else:
            target_branch = DEFAULT_BASE_BRANCH
            target_branch_source = "default"
        build_command = (
            (args.build_command or "").strip()
            or os.getenv("BUILD_COMMAND", "").strip()
            or target_defaults.get("build_command", "")
            or "dotnet build"
        )
        test_command = (
            (args.test_command or "").strip()
            or os.getenv("TEST_COMMAND", "").strip()
            or target_defaults.get("test_command", "")
            or None
        )
        solution_path = (
            (args.solution_path or "").strip()
            or os.getenv("SOLUTION_PATH", "").strip()
            or target_defaults.get("solution_path", "")
            or None
        )

        max_issues = args.max_issues
        if max_issues is None:
            raw_max_issues = os.getenv("MAX_ISSUES", "").strip() or target_defaults.get("max_issues", "")
            max_issues = int(raw_max_issues) if raw_max_issues else DEFAULT_MAX_ISSUES

        if not project_key:
            raise RuntimeError("缺少 project_key，请传 --project-key 或在 .env 设置 PROJECT_KEY")
        if not repository:
            raise RuntimeError("缺少 repository，请传 --repository 或在 .env 设置 REPOSITORY")
        if not author:
            raise RuntimeError("缺少 author，请传 --author 或在 .env 设置 AUTHOR")

        print("[INFO] 正在解析收件人...")
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
            "[INFO] Reviewer: "
            f"{recipients.reviewer_email or '(none)'} "
            f"(source: {recipients.reviewer_source})"
        )
        print(
            "[INFO] DingTalk UserId: "
            f"{recipients.dingtalk_userid or '(unresolved)'} "
            f"(source: {recipients.dingtalk_source})"
        )
        print(f"[INFO] Clone Branch: {clone_branch} (source: {clone_branch_source})")
        print(f"[INFO] Target Branch: {target_branch} (source: {target_branch_source})")

        # Initialize clients
        sonar_client = SonarQubeClient(sonar_host, sonar_token, sonar_org)
        ado_client = AzureDevOpsClient(
            ado_base_url,
            ado_project,
            ado_pat,
            organization=ado_org,
        )
        dingtalk_client = create_dingtalk_client_from_env()

        print("[INFO] 正在获取 SonarQube issues...")

        # Get issues
        issues = sonar_client.get_open_issues(
            project_key=project_key,
            author=author,
        )

        print(f"[INFO] 发现 {len(issues)} 个 issues")

        if not issues:
            print("[INFO] 没有待处理的 issues")
            return

        # Limit issues
        if max_issues > 0:
            issues = issues[:max_issues]
            print(f"[INFO] 限制处理 {len(issues)} 个")

        # Clone repository
        print("[INFO] 准备仓库...")
        repo_url = ado_client.get_remote_url(repository)
        workspace_root = Path(args.workspace_root)
        prune_result = prune_old_workspaces(workspace_root, keep_latest=1)
        if prune_result.removed:
            print(f"[INFO] 已清理 {len(prune_result.removed)} 个旧工作区")
            for removed in format_removed_workspaces(prune_result.removed):
                print(f"  - {removed}")
        if prune_result.failed:
            print(f"[WARN] 有 {len(prune_result.failed)} 个旧工作区目录未能删除，请关闭占用进程后重试")
            for failed_workspace in format_removed_workspaces(prune_result.failed):
                print(f"  - {failed_workspace}")
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = workspace_root / f"fix_{repository}_{run_label}"

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
            print(f"[INFO] 切换工作基线到目标分支: {target_branch}")
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

        # Initialize Claude Agent
        agent = ClaudeFixAgent(
            sonar_host=sonar_host,
            sonar_token=sonar_token,
            sonar_org=sonar_org,
            agent_env=build_agent_env(),
            model=resolve_agent_model(),
        )

        # Process each issue
        successful = 0
        failed = 0
        skipped = 0
        issue_summaries: list[PullRequestIssueSummary] = []

        for i, issue in enumerate(issues, 1):
            rule_id = issue.get("rule", "")
            component = issue.get("component", "")
            file_path = component.split(":", 1)[-1].replace("\\", "/")
            line = issue.get("line", 0)

            print(f"\n[{i}/{len(issues)}] 修复: {rule_id} → {file_path}:{line}")

            # Convert to SonarIssue
            sonar_issue = SonarIssue(
                key=issue.get("key", ""),
                rule=rule_id,
                message=issue.get("message", ""),
                line=line,
                component=component,
                severity=issue.get("severity", ""),
                issue_type=issue.get("type", ""),
            )

            # Fix via Claude Agent
            issue_build_command = resolve_build_command(build_command, solution_path)
            result = process_issue_with_retries(
                agent=agent,
                issue=sonar_issue,
                workspace_path=workspace,
                build_command=issue_build_command,
                repository=repository,
                run_label=run_label,
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

        # Build verification
        build_passed = True
        final_build_result: dict[str, object] | None = None
        if successful > 0 and not args.skip_build:
            print("[INFO] 运行构建验证...")
            final_build_result = run_local_build(
                workspace,
                build_command,
                test_command,
                DEFAULT_BUILD_TIMEOUT,
                solution_path=solution_path,
            )
            build_passed = bool(final_build_result["succeeded"])
            print(f"  构建: {'[OK] 通过' if build_passed else '[ERR] 失败'}")
            if not build_passed:
                failure_report = format_build_failure_report(final_build_result)
                if failure_report:
                    print("  [BUILD LOG]")
                    print(failure_report)

        # Create PR
        pr_url = ""
        pr_error = ""
        pr_report_markdown = ""
        local_pr_report_path: Path | None = None
        should_create_pr = build_passed and successful > 0 and not args.skip_build
        if successful > 0:
            pr_description = build_pull_request_description(
                author=author,
                base_branch=target_branch,
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
                    repository=repository,
                    author=author,
                    run_label=run_label,
                ),
                pr_report_markdown,
            )
            print(f"[INFO] PR 详细说明已保存: {local_pr_report_path.as_posix()}")

        if should_create_pr:
            print("[INFO] 创建 Pull Request...")

            branch = f"fix/sonar-{author.split('@')[0]}-{run_label}"
            repo_pr_report_path = build_repository_pr_report_path(
                repository=repository,
                author=author,
                run_label=run_label,
            )
            repo_pr_report_file = write_markdown_report(
                workspace,
                repo_pr_report_path,
                pr_report_markdown,
            )
            print(f"[INFO] PR 详细报告文件: {repo_pr_report_path}")
            print(f"[INFO] PR 详细报告绝对路径: {repo_pr_report_file.as_posix()}")

            pr_description = build_summary_pull_request_description(
                author=author,
                base_branch=target_branch,
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
                print(f"  PR: {pr_url}")
            except Exception as exc:
                pr_error = str(exc)
                print(f"[WARN] PR 创建失败: {pr_error}")

        # Notify
        should_notify = bool(dingtalk_client and (pr_url or pr_error or successful or skipped or failed))
        if should_notify:
            try:
                dingtalk_client.send_run_notification(
                    author=author,
                    total_issues=len(issues),
                    successful=successful,
                    failed=failed + skipped,
                    pr_url=pr_url,
                    dingtalk_userid=recipients.dingtalk_userid,
                    warning_message=(f"PR 创建失败：{pr_error}" if pr_error else None),
                    force_warn=bool(pr_error),
                )
                print("[INFO] 钉钉通知发送成功")
            except Exception as e:
                print(f"[WARN] 通知失败: {e}")

        # Cleanup
        if not args.keep_workspace and workspace.exists():
            import shutil

            shutil.rmtree(workspace, ignore_errors=True)

        # Summary
        print(f"\n{'=' * 50}")
        print(f"完成! 成功: {successful}, 跳过: {skipped}, 失败: {failed}, 构建: {'通过' if build_passed else '失败'}")
        if pr_url:
            print(f"PR: {pr_url}")
        elif pr_error:
            print(f"PR 创建失败: {pr_error}")


if __name__ == "__main__":
    main()
