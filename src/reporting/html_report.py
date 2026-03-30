"""HTML report generation for fix runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class FixRunReport:
    """Report for a fix run."""

    run_timestamp: str
    author: str
    project_key: str
    repository: str
    total_issues: int = 0
    total_issues_found: int = 0
    manual_only_issues: int = 0
    successful_fixes: int = 0
    failed_fixes: int = 0
    status: str = "running"
    error: str = ""
    build_gate_enabled: bool = True
    format_gate_enabled: bool = True
    sonar_gate_enabled: bool = False
    sonar_gate_passed: bool = False
    build_passed: bool = False
    build_command: str = ""
    build_duration_seconds: float = 0
    pr_url: str = ""
    processable_issue_details: list[dict[str, Any]] = field(default_factory=list)
    manual_only_issue_details: list[dict[str, Any]] = field(default_factory=list)
    successful_issue_details: list[dict[str, Any]] = field(default_factory=list)
    failed_issue_details: list[dict[str, Any]] = field(default_factory=list)


def write_html_run_report(report: FixRunReport, output_path: Path) -> None:
    """Write an HTML report for a fix run."""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SonarQube Fix Report - {report.author}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            padding: 24px;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #0066cc;
            padding-bottom: 10px;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin: 20px 0;
        }}
        .stat-card {{
            background: #f8f9fa;
            padding: 16px;
            border-radius: 6px;
            border-left: 4px solid #0066cc;
        }}
        .stat-card.success {{ border-left-color: #28a745; }}
        .stat-card.warning {{ border-left-color: #ffc107; }}
        .stat-card.error {{ border-left-color: #dc3545; }}
        .stat-value {{
            font-size: 32px;
            font-weight: bold;
            color: #333;
        }}
        .stat-label {{
            font-size: 14px;
            color: #666;
            margin-top: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f8f9fa;
            font-weight: 600;
            color: #333;
        }}
        tr:hover {{ background: #f8f9fa; }}
        .severity-BLOCKER {{ color: #dc3545; font-weight: bold; }}
        .severity-CRITICAL {{ color: #fd7e14; font-weight: bold; }}
        .severity-MAJOR {{ color: #ffc107; }}
        .severity-MINOR {{ color: #17a2b8; }}
        .status-badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-success {{ background: #d4edda; color: #155724; }}
        .status-failed {{ background: #f8d7da; color: #721c24; }}
        .status-pending {{ background: #fff3cd; color: #856404; }}
        .pr-link {{
            color: #0066cc;
            text-decoration: none;
        }}
        .pr-link:hover {{ text-decoration: underline; }}
        .error-message {{
            background: #f8d7da;
            color: #721c24;
            padding: 12px;
            border-radius: 4px;
            margin: 10px 0;
        }}
        .timestamp {{
            color: #666;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>SonarQube 代码修复报告</h1>
        <p class="timestamp">运行时间: {report.run_timestamp}</p>
        <p><strong>作者:</strong> {report.author}</p>
        <p><strong>项目:</strong> {report.project_key}</p>
        <p><strong>仓库:</strong> {report.repository}</p>

        <div class="summary">
            <div class="stat-card">
                <div class="stat-value">{report.total_issues_found}</div>
                <div class="stat-label">发现的问题</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{report.total_issues}</div>
                <div class="stat-label">可处理问题</div>
            </div>
            <div class="stat-card success">
                <div class="stat-value">{report.successful_fixes}</div>
                <div class="stat-label">成功修复</div>
            </div>
            <div class="stat-card error">
                <div class="stat-value">{report.failed_fixes}</div>
                <div class="stat-label">修复失败</div>
            </div>
        </div>

        {f'<p><strong>PR 链接:</strong> <a class="pr-link" href="{report.pr_url}">{report.pr_url}</a></p>' if report.pr_url else ''}

        {f'<div class="error-message">{report.error}</div>' if report.error else ''}

        {f'<p><strong>构建状态:</strong> {"✅ 通过" if report.build_passed else "❌ 失败"}</p>' if report.build_gate_enabled else ''}
        {f'<p><strong>构建命令:</strong> {report.build_command}</p>' if report.build_command else ''}
        {f'<p><strong>构建耗时:</strong> {report.build_duration_seconds:.1f} 秒</p>' if report.build_duration_seconds > 0 else ''}

        {"<h2>成功修复的问题</h2>" + _render_issues_table(report.successful_issue_details, "success") if report.successful_issue_details else ""}

        {"<h2>修复失败的问题</h2>" + _render_issues_table(report.failed_issue_details, "failed") if report.failed_issue_details else ""}

        {"<h2>可处理的问题</h2>" + _render_issues_table(report.processable_issue_details, "pending") if report.processable_issue_details else ""}

        {"<h2>仅需人工处理的问题</h2>" + _render_issues_table(report.manual_only_issue_details, "pending") if report.manual_only_issue_details else ""}
    </div>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def _render_issues_table(issues: list[dict[str, Any]], status_type: str) -> str:
    """Render issues as an HTML table."""
    if not issues:
        return "<p>无</p>"

    rows = []
    for issue in issues:
        severity = issue.get("severity", "MINOR")
        rows.append(f"""
            <tr>
                <td class="severity-{severity}">{severity}</td>
                <td>{issue.get("rule", "")}</td>
                <td>{issue.get("file_path", "")}</td>
                <td>{issue.get("line", "")}</td>
                <td>{issue.get("message", "")[:100]}...</td>
                <td><span class="status-badge status-{status_type}">{issue.get("status", "pending")}</span></td>
            </tr>
        """)

    return f"""
        <table>
            <thead>
                <tr>
                    <th>严重程度</th>
                    <th>规则</th>
                    <th>文件</th>
                    <th>行号</th>
                    <th>消息</th>
                    <th>状态</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
    """


def write_json_run_report(report: FixRunReport, output_path: Path) -> None:
    """Write a JSON report for a fix run."""
    data = {
        "run_timestamp": report.run_timestamp,
        "author": report.author,
        "project_key": report.project_key,
        "repository": report.repository,
        "total_issues": report.total_issues,
        "total_issues_found": report.total_issues_found,
        "manual_only_issues": report.manual_only_issues,
        "successful_fixes": report.successful_fixes,
        "failed_fixes": report.failed_fixes,
        "status": report.status,
        "error": report.error,
        "build_gate_enabled": report.build_gate_enabled,
        "format_gate_enabled": report.format_gate_enabled,
        "sonar_gate_enabled": report.sonar_gate_enabled,
        "sonar_gate_passed": report.sonar_gate_passed,
        "build_passed": report.build_passed,
        "build_command": report.build_command,
        "build_duration_seconds": report.build_duration_seconds,
        "pr_url": report.pr_url,
        "processable_issue_details": report.processable_issue_details,
        "manual_only_issue_details": report.manual_only_issue_details,
        "successful_issue_details": report.successful_issue_details,
        "failed_issue_details": report.failed_issue_details,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_issue_audit_entry(
    issue: dict[str, Any],
    status: str,
    reason: str = "",
) -> dict[str, Any]:
    """Build an issue audit entry."""
    text_range = issue.get("textRange", {}) or {}
    return {
        "key": issue.get("key", ""),
        "rule": issue.get("rule", ""),
        "severity": issue.get("severity", ""),
        "type": issue.get("type", ""),
        "message": issue.get("message", ""),
        "file_path": issue.get("file_path", ""),
        "line": issue.get("line") or text_range.get("startLine", 0),
        "status": status,
        "reason": reason,
        "component": issue.get("component", ""),
    }