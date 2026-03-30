"""Batch runner for pi-sonar-agent.

Reads targets.json and runs fix for each target.
"""

from __future__ import annotations

import json
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
from pi_sonar_agent.core.dingtalk import create_dingtalk_client_from_env
from pi_sonar_agent.core.issue_retry import process_issue_with_retries
from pi_sonar_agent.core.model_env import (
    build_agent_env,
    load_project_env,
    resolve_agent_model,
)
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
    reviewer_email = str(target.get("reviewer_email", "")).strip()
    dingtalk_userid = str(target.get("dingtalk_userid", "")).strip()
    max_issues = target.get("max_issues", DEFAULT_MAX_ISSUES)
    base_branch = target.get("base_branch", DEFAULT_BASE_BRANCH)
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

    print(f"\n{'=' * 60}")
    print(f"处理: {author} | {project_key} | {repository}")
    print(f"{'=' * 60}")

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
    workspace = Path(f".agent_workspaces/{repository}_{timestamp}")
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
    should_create_pr = build_passed and successful > 0 and not target.get("skip_build_gate")
    if should_create_pr:
        print("\n[PR] 创建...")
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

        pr = ado_client.create_pull_request(
            repository=repository,
            title=f"Fix: 修复 {successful} 个 SonarQube 问题",
            description=pr_description,
            source_branch=branch,
            target_branch=base_branch,
            reviewer_email=reviewer_email or None,
        )
        pr_url = pr.url
        print(f"  [OK] {pr_url}")

    # Notify
    if dingtalk and pr_url:
        with suppress(Exception):
            dingtalk.send_run_notification(
                author,
                len(issues),
                successful,
                failed + skipped,
                pr_url,
                dingtalk_userid=dingtalk_userid or None,
            )

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
    }


def main():
    """Main entry point."""
    import sys

    config_path = Path("data/targets.json")

    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])

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
