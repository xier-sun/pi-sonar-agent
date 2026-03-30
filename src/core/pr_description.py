"""Pull request description helpers."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """Build a review-friendly pull request description."""

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
