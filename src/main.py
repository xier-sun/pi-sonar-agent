"""Main entry point for pi-sonar-agent.

All fixes are handled by Claude Code Agent - simple and powerful.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from pi_sonar_agent.core.model_env import (
    build_agent_env,
    load_project_env,
    resolve_agent_model,
)

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

    # Defer heavy imports so `python run.py --help` works even before optional deps are ready.
    from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
    from pi_sonar_agent.core.dingtalk import create_dingtalk_client_from_env
    from pi_sonar_agent.core.issue_retry import process_issue_with_retries
    from pi_sonar_agent.core.pr_description import (
        PullRequestIssueSummary,
        build_pull_request_description,
    )
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
    reviewer_email = (
        os.getenv("REVIEWER_EMAIL", "").strip()
        or target_defaults.get("reviewer_email", "")
    )
    dingtalk_userid = (
        os.getenv("DINGTALK_USERID", "").strip()
        or target_defaults.get("dingtalk_userid", "")
    )
    base_branch = (
        (args.base_branch or "").strip()
        or os.getenv("BASE_BRANCH", "").strip()
        or target_defaults.get("base_branch", "")
        or DEFAULT_BASE_BRANCH
    )
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
    timestamp = time.strftime("%Y%m%d%H%M%S")
    workspace = Path(args.workspace_root) / f"fix_{repository}_{timestamp}"
    workspace.mkdir(parents=True, exist_ok=True)

    clone_result = subprocess.run(
        f'git clone -b {base_branch} --single-branch "{repo_url}" "{workspace}"',
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if clone_result.returncode != 0:
        raise RuntimeError(f"仓库克隆失败: {(clone_result.stderr or clone_result.stdout or '').strip()}")

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
    should_create_pr = build_passed and successful > 0 and not args.skip_build
    if should_create_pr:
        print("[INFO] 创建 Pull Request...")

        branch = f"fix/sonar-{author.split('@')[0]}-{timestamp}"
        subprocess.run(f"git checkout -b {branch}", shell=True, cwd=str(workspace), check=True)
        subprocess.run("git add -A", shell=True, cwd=str(workspace), check=True)
        subprocess.run(
            f'git commit -m "fix: 修复 {successful} 个 SonarQube 问题"',
            shell=True,
            cwd=str(workspace),
        )
        subprocess.run(f"git push -u origin {branch}", shell=True, cwd=str(workspace), check=True)

        pr_description = build_pull_request_description(
            author=author,
            base_branch=base_branch,
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

        pr = ado_client.create_pull_request(
            repository=repository,
            title=f"Fix: 修复 {successful} 个 SonarQube 问题",
            description=pr_description,
            source_branch=branch,
            target_branch=base_branch,
            reviewer_email=reviewer_email or None,
        )
        pr_url = pr.url
        print(f"  PR: {pr_url}")

    # Notify
    if dingtalk_client and pr_url:
        try:
            dingtalk_client.send_run_notification(
                author=author,
                total_issues=len(issues),
                successful=successful,
                failed=failed + skipped,
                pr_url=pr_url,
                dingtalk_userid=dingtalk_userid or None,
            )
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


if __name__ == "__main__":
    main()
