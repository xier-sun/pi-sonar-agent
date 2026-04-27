from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.core.pr_description import (
    ADO_PR_DESCRIPTION_SOFT_LIMIT,
    PullRequestIssueSummary,
    build_compact_pull_request_description,
    build_local_pr_report_path,
    build_pr_attachment_name,
    build_pull_request_description,
    build_summary_pull_request_description,
    write_markdown_report,
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
                compliance_status="pass",
                compliance_summary="Hard quality gates passed.",
                active_quality_gate_rules=("public_xml_docs", "async_signature"),
                soft_quality_gate_findings=1,
                boundary_audit_summary="Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.",
                boundary_audit_findings=("OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs 主区域外变更: 80",),
                boundary_drift_score=1,
            ),
            PullRequestIssueSummary(
                status="SKIPPED",
                rule="csharpsquid:S3776",
                file_path="OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs",
                line=51,
                message="Cognitive complexity too high",
                issue_key="issue-skipped-1",
                attempts=3,
                rule_review_summary=(
                    "主要收口方法: CalculatePenalty",
                    "采用策略: guard_clause_flatten",
                    "本地复杂度估计: 18/30",
                    "签名变更: 否",
                ),
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
    assert "启用规范门禁: public_xml_docs, async_signature" in description
    assert "规范校验: pass | Hard quality gates passed." in description
    assert "规范细项: hard failures=0, soft findings=1" in description
    assert "边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit." in description
    assert "漂移记录: OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs 主区域外变更: 80" in description
    assert "## 已跳过 Issues" in description
    assert "1. csharpsquid:S3776" in description
    assert "Issue Key: issue-skipped-1" in description
    assert "规则专项摘要:" in description
    assert "主要收口方法: CalculatePenalty" in description
    assert "采用策略: guard_clause_flatten" in description
    assert "本地复杂度估计: 18/30" in description
    assert "跳过原因: Build verification failed after 3 attempt(s)" in description
    assert "重试日志: logs/issue_attempts/example.log" in description


def test_build_compact_pull_request_description_is_shorter_for_large_batches() -> None:
    issue_summaries = [
        PullRequestIssueSummary(
            status="FIXED",
            rule=f"csharpsquid:S{6500 + index}",
            file_path=f"OpenAuth.Core/OpenAuth.App/Demo/File{index}.cs",
            line=100 + index,
            message="Very long sonar message " * 5,
            issue_key=f"issue-{index}",
            attempts=3,
            summary="经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。",
            changed_files=(f"OpenAuth.Core/OpenAuth.App/Demo/File{index}.cs",),
        )
        for index in range(1, 10)
    ]

    full_description = build_pull_request_description(
        author="pengxiru@neware.com.cn",
        base_branch="401sonarqube",
        solution_path="OpenAuth.Core/OpenAuth.Core.WebApi.sln",
        build_command='dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"',
        test_command=None,
        successful=9,
        skipped=1,
        failed=0,
        build_passed=True,
        issue_summaries=issue_summaries,
    )
    compact_description = build_compact_pull_request_description(
        author="pengxiru@neware.com.cn",
        base_branch="401sonarqube",
        solution_path="OpenAuth.Core/OpenAuth.Core.WebApi.sln",
        build_command='dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"',
        test_command=None,
        successful=9,
        skipped=1,
        failed=0,
        build_passed=True,
        issue_summaries=issue_summaries,
    )

    assert len(compact_description) < len(full_description)
    assert len(compact_description) < ADO_PR_DESCRIPTION_SOFT_LIMIT
    assert "PR 描述已切换为精简版" in compact_description


def test_build_summary_pull_request_description_is_brief_and_points_to_report() -> None:
    issue_summaries = [
        PullRequestIssueSummary(
            status="FIXED",
            rule=f"csharpsquid:S{6500 + index}",
            file_path=f"OpenAuth.Core/OpenAuth.App/Demo/File{index}.cs",
            line=100 + index,
            message="Very long sonar message " * 5,
            issue_key=f"issue-{index}",
            attempts=3,
            summary="经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。",
            changed_files=(f"OpenAuth.Core/OpenAuth.App/Demo/File{index}.cs",),
        )
        for index in range(1, 20)
    ]

    summary_description = build_summary_pull_request_description(
        author="pengxiru@neware.com.cn",
        base_branch="401sonarqube",
        solution_path="OpenAuth.Core/OpenAuth.Core.WebApi.sln",
        build_command='dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"',
        test_command=None,
        successful=16,
        skipped=10,
        failed=0,
        build_passed=True,
        issue_summaries=issue_summaries,
        report_attachment_name="BI_pengxiru-neware.com.cn_20260401133434.txt",
        report_attachment_url="https://devops.example/pr/attachments/123",
    )

    assert "[BI_pengxiru-neware.com.cn_20260401133434.txt](https://devops.example/pr/attachments/123)" in summary_description
    assert "逐条 issue 处理结果、跳过原因和重试日志请查看 PR 附件中的报告。" in summary_description
    assert len(summary_description) < ADO_PR_DESCRIPTION_SOFT_LIMIT
    assert "1. csharpsquid" not in summary_description


def test_pr_report_paths_and_writer_use_expected_locations(tmp_path) -> None:
    local_path = build_local_pr_report_path(
        repository="BI",
        author="pengxiru@neware.com.cn",
        run_label="20260401133434",
    )
    written_path = write_markdown_report(
        tmp_path,
        local_path,
        "# Report\n",
    )
    attachment_name = build_pr_attachment_name(
        repository="BI",
        author="pengxiru@neware.com.cn",
        run_label="20260401133434",
    )

    assert local_path == Path("logs/pr_descriptions/BI_pengxiru-neware.com.cn_20260401133434.md")
    assert attachment_name == "BI_pengxiru-neware.com.cn_20260401133434.txt"
    assert written_path == tmp_path / local_path
    assert written_path.read_text(encoding="utf-8") == "# Report\n"
