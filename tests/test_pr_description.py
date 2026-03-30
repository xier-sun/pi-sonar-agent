from __future__ import annotations

from pi_sonar_agent.core.pr_description import (
    PullRequestIssueSummary,
    build_pull_request_description,
)


def test_build_pull_request_description_lists_issue_outcomes() -> None:
    description = build_pull_request_description(
        author="liyinglin@neware.com.cn",
        base_branch="325testai",
        solution_path="OpenAuth.Core/OpenAuth.Core.WebApi.sln",
        build_command='dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"',
        test_command=None,
        successful=2,
        skipped=1,
        failed=0,
        build_passed=True,
        issue_summaries=[
            PullRequestIssueSummary(
                status="FIXED",
                rule="csharpsquid:S6562",
                file_path="OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs",
                line=74,
                message="Always set the DateTimeKind",
                issue_key="issue-fixed-1",
                attempts=1,
                summary="Fixed 1 file(s)",
                changed_files=("OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs",),
            ),
            PullRequestIssueSummary(
                status="SKIPPED",
                rule="csharpsquid:S3776",
                file_path="OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs",
                line=51,
                message="Cognitive complexity too high",
                issue_key="issue-skipped-1",
                attempts=3,
                skip_reason="Build verification failed after 3 attempt(s)",
                issue_log_path="logs/issue_attempts/example.log",
            ),
        ],
    )

    assert "## 运行概览" in description
    assert "- 成功: 2" in description
    assert "- 跳过: 1" in description
    assert "## 审阅提示" in description
    assert "被跳过或失败的 issue 已自动回滚，不包含在当前提交中。" in description
    assert "## 已修复 Issues" in description
    assert "1. csharpsquid:S6562" in description
    assert "Issue Key: issue-fixed-1" in description
    assert "状态: FIXED" in description
    assert "涉及文件: OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs" in description
    assert "审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题" in description
    assert "## 已跳过 Issues" in description
    assert "1. csharpsquid:S3776" in description
    assert "Issue Key: issue-skipped-1" in description
    assert "跳过原因: Build verification failed after 3 attempt(s)" in description
    assert "重试日志: logs/issue_attempts/example.log" in description
