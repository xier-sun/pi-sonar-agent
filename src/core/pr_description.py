"""Pull request description helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ADO_PR_DESCRIPTION_SOFT_LIMIT = 3800
DEFAULT_LOCAL_PR_REPORT_ROOT = "logs/pr_descriptions"


@dataclass(frozen=True)
class PullRequestIssueSummary:
    """Summary of a single issue outcome for PR descriptions."""

    status: str
    rule: str
    file_path: str
    line: int
    message: str
    issue_key: str = ""
    attempts: int = 1
    summary: str = ""
    skip_reason: str = ""
    issue_log_path: str = ""
    changed_files: tuple[str, ...] = field(default_factory=tuple)


def _dedupe_preserve_order(values: tuple[str, ...]) -> tuple[str, ...]:
    """Remove duplicate values while preserving order."""

    return tuple(dict.fromkeys(item for item in values if item))


def _sanitize_path_segment(value: str) -> str:
    """Sanitize a path segment for markdown report file names."""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return normalized.strip("-._") or "run"


def build_local_pr_report_path(*, repository: str, author: str, run_label: str) -> Path:
    """Build the local persisted markdown report path for a PR run."""

    repository_name = _sanitize_path_segment(repository)
    author_name = _sanitize_path_segment(author)
    return Path(DEFAULT_LOCAL_PR_REPORT_ROOT) / f"{repository_name}_{author_name}_{run_label}.md"


def build_pr_attachment_name(*, repository: str, author: str, run_label: str) -> str:
    """Build the uploaded PR attachment file name for a run report."""

    repository_name = _sanitize_path_segment(repository)
    author_name = _sanitize_path_segment(author)
    return f"{repository_name}_{author_name}_{run_label}.txt"


def write_markdown_report(base_dir: Path, relative_path: str | Path, content: str) -> Path:
    """Write a markdown report relative to the provided base directory."""

    target_path = base_dir / Path(relative_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return target_path


def _render_issue_item(index: int, item: PullRequestIssueSummary) -> list[str]:
    """Render a single issue entry."""

    lines = [
        f"{index}. {item.rule}",
        f"   - Issue Key: {item.issue_key or '未提供'}",
        f"   - 状态: {item.status}",
        f"   - 位置: {item.file_path}:{item.line}",
        f"   - Sonar 问题: {item.message}",
        f"   - 尝试次数: {item.attempts}",
    ]

    changed_files = _dedupe_preserve_order(item.changed_files)
    if item.summary:
        lines.append(f"   - 处理结果: {item.summary}")
    if changed_files:
        lines.append(f"   - 涉及文件: {', '.join(changed_files)}")
        lines.append("   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。")
    if item.skip_reason:
        lines.append(f"   - 跳过原因: {item.skip_reason}")
    if item.issue_log_path:
        lines.append(f"   - 重试日志: {item.issue_log_path}")
    return lines


def _format_changed_files_summary(
    changed_files: tuple[str, ...],
    *,
    max_items: int = 5,
) -> str | None:
    """Render a concise changed-file summary for short PR descriptions."""

    unique_files = _dedupe_preserve_order(changed_files)
    if not unique_files:
        return None

    preview = list(unique_files[:max_items])
    if len(unique_files) > max_items:
        preview.append(f"等 {len(unique_files)} 个文件")
    return ", ".join(preview)


def build_pull_request_description(
    *,
    author: str,
    base_branch: str,
    solution_path: str | None,
    build_command: str,
    test_command: str | None,
    successful: int,
    skipped: int,
    failed: int,
    build_passed: bool,
    issue_summaries: list[PullRequestIssueSummary],
) -> str:
    """Build a detailed markdown report for the pull request run."""

    fixed_items = [item for item in issue_summaries if item.status == "FIXED"]
    skipped_items = [item for item in issue_summaries if item.status == "SKIPPED"]
    failed_items = [item for item in issue_summaries if item.status == "FAILED"]

    changed_files = _dedupe_preserve_order(
        tuple(path for item in fixed_items for path in item.changed_files if path)
    )

    lines = [
        "自动修复 SonarQube issues",
        "",
        "## 运行概览",
        f"- 作者: {author}",
        f"- 基线分支: {base_branch}",
        f"- 最终构建: {'通过' if build_passed else '失败'}",
        f"- 成功: {successful}",
        f"- 跳过: {skipped}",
        f"- 失败: {failed}",
        f"- 构建命令: {build_command}",
    ]

    if solution_path:
        lines.append(f"- 解决方案: {solution_path}")
    if test_command:
        lines.append(f"- 测试命令: {test_command}")

    lines.extend([
        "",
        "## 审阅提示",
        "- 本 PR 只包含最终构建验证通过的修复。",
        "- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。",
    ])
    if changed_files:
        lines.append(f"- 建议优先审阅这些文件: {', '.join(changed_files)}")

    lines.extend(["", "## 已修复 Issues"])
    if fixed_items:
        for index, item in enumerate(fixed_items, start=1):
            lines.extend(_render_issue_item(index, item))
    else:
        lines.append("- 无成功修复的 issue")

    lines.extend(["", "## 已跳过 Issues"])
    if skipped_items:
        for index, item in enumerate(skipped_items, start=1):
            lines.extend(_render_issue_item(index, item))
    else:
        lines.append("- 无跳过 issue")

    lines.extend(["", "## 失败 Issues"])
    if failed_items:
        for index, item in enumerate(failed_items, start=1):
            lines.extend(_render_issue_item(index, item))
    else:
        lines.append("- 无失败 issue")

    return "\n".join(lines).strip()


def build_summary_pull_request_description(
    *,
    author: str,
    base_branch: str,
    solution_path: str | None,
    build_command: str,
    test_command: str | None,
    successful: int,
    skipped: int,
    failed: int,
    build_passed: bool,
    issue_summaries: list[PullRequestIssueSummary],
    report_attachment_name: str | None = None,
    report_attachment_url: str | None = None,
) -> str:
    """Build a short Azure DevOps-friendly PR description."""

    fixed_items = [item for item in issue_summaries if item.status == "FIXED"]
    changed_files = _dedupe_preserve_order(
        tuple(path for item in fixed_items for path in item.changed_files if path)
    )
    changed_files_summary = _format_changed_files_summary(changed_files)

    lines = [
        "自动修复 SonarQube issues",
        "",
        "## 运行概览",
        f"- 作者: {author}",
        f"- 基线分支: {base_branch}",
        f"- 最终构建: {'通过' if build_passed else '失败'}",
        f"- 成功: {successful}",
        f"- 跳过: {skipped}",
        f"- 失败: {failed}",
        f"- 构建命令: {build_command}",
    ]

    if solution_path:
        lines.append(f"- 解决方案: {solution_path}")
    if test_command:
        lines.append(f"- 测试命令: {test_command}")
    if changed_files_summary:
        lines.append(f"- 主要改动文件: {changed_files_summary}")
    if report_attachment_name and report_attachment_url:
        lines.append(
            "- 详细修复报告附件: "
            f"[{report_attachment_name}]({report_attachment_url})"
        )
    elif report_attachment_name:
        lines.append(f"- 详细修复报告附件: {report_attachment_name}")

    lines.extend(
        [
            "",
            "## 审阅提示",
            "- 本 PR 仅包含最终构建验证通过的修复。",
            "- 跳过或失败的 issue 未纳入当前提交。",
            "- 逐条 issue 处理结果、跳过原因和重试日志请查看 PR 附件中的报告。",
        ]
    )

    return "\n".join(lines).strip()


def build_compact_pull_request_description(
    *,
    author: str,
    base_branch: str,
    solution_path: str | None,
    build_command: str,
    test_command: str | None,
    successful: int,
    skipped: int,
    failed: int,
    build_passed: bool,
    issue_summaries: list[PullRequestIssueSummary],
) -> str:
    """Build a compact PR description for Azure DevOps size-sensitive cases."""

    fixed_items = [item for item in issue_summaries if item.status == "FIXED"]
    skipped_items = [item for item in issue_summaries if item.status == "SKIPPED"]
    failed_items = [item for item in issue_summaries if item.status == "FAILED"]
    changed_files = _dedupe_preserve_order(
        tuple(path for item in fixed_items for path in item.changed_files if path)
    )

    lines = [
        "自动修复 SonarQube issues",
        "",
        "## 运行概览",
        f"- 作者: {author}",
        f"- 基线分支: {base_branch}",
        f"- 最终构建: {'通过' if build_passed else '失败'}",
        f"- 成功: {successful}",
        f"- 跳过: {skipped}",
        f"- 失败: {failed}",
        f"- 构建命令: {build_command}",
    ]

    if solution_path:
        lines.append(f"- 解决方案: {solution_path}")
    if test_command:
        lines.append(f"- 测试命令: {test_command}")
    if changed_files:
        lines.append(f"- 涉及文件: {', '.join(changed_files)}")

    lines.extend(
        [
            "",
            "## 审阅提示",
            "- 当前 issue 数量较多，PR 描述已切换为精简版。",
            "- 详细重试日志和逐条处理结果请查看运行日志与 issue attempt 日志。",
            "",
            "## 已修复 Issues",
        ]
    )

    if fixed_items:
        for index, item in enumerate(fixed_items, start=1):
            lines.append(
                f"{index}. {item.rule} | {item.file_path}:{item.line} | {item.message} | 尝试 {item.attempts} 次"
            )
    else:
        lines.append("- 无成功修复的 issue")

    lines.extend(["", "## 已跳过 / 失败 Issues"])
    skipped_or_failed = [*skipped_items, *failed_items]
    if skipped_or_failed:
        for index, item in enumerate(skipped_or_failed, start=1):
            reason = item.skip_reason or item.summary or "见运行日志"
            lines.append(
                f"{index}. {item.status} | {item.rule} | {item.file_path}:{item.line} | {reason}"
            )
    else:
        lines.append("- 无跳过或失败 issue")

    return "\n".join(lines).strip()
