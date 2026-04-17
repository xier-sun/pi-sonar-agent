"""Prompt builders for main/fix/review child-agent orchestration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.memory.child_agent_memory import (
    ChildAgentMemory,
    render_child_agent_memory,
)
from pi_sonar_agent.core.memory.issue_working_memory import (
    IssueWorkingMemory,
    render_issue_working_memory,
)

QUALITY_GATE_SKILL_PATH = Path(r"C:\Users\neware\.claude\skills\csharp-quality-gate\SKILL.md")


def load_quality_gate_skill_excerpt(*, max_chars: int = 2200) -> str:
    """Load a concise quality-gate reference for the review agent."""

    try:
        text = QUALITY_GATE_SKILL_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def build_fix_role_system_prompt() -> str:
    return """你是 Fix 子Agent，职责只有一个：在当前工作区内直接修改代码，完成当前 Sonar issue 的最小修复。

规则：
- 只修当前 issue，不顺手修其他问题
- 只允许修改已有文件；禁止创建文件、删除文件、重命名文件
- 优先直接对给出的仓库相对路径使用 Read/Edit/MultiEdit/Write；只有候选路径都不对时才用 Bash 做只读搜索
- Edit 必须同时提供 file_path、old_string、new_string；MultiEdit 必须提供 file_path 和至少一个 edits 项；Write 只能重写已有文件
- 不要自行执行 dotnet restore/build/test；编译由外层统一执行
- 不要输出长篇解释；直接读代码、编辑、完成后简短说明修法
- 如果上一轮策略被否定，必须换一种更小的修法，不要机械重复
"""


def _normalize_relative_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _select_primary_candidate(issue: Any, file_path_candidates: tuple[str, ...]) -> str:
    for item in file_path_candidates:
        normalized = _normalize_relative_path(str(item))
        if normalized:
            return normalized
    return _normalize_relative_path(str(getattr(issue, "file_path", "") or ""))


def _summarize_fix_retry_feedback(retry_feedback: str) -> str:
    text = str(retry_feedback or "").strip()
    if not text:
        return ""
    normalized = " ".join(text.split()).lower()
    if "required parameter `old_string` is missing" in normalized or "missing:old_string" in normalized:
        return "\n".join(
            [
                "上轮失败原因：Edit 调用缺少 old_string，导致没有真正落盘修改。",
                "- 先用 Read 读取更小的目标片段，再复制精确代码到 old_string。",
                "- Edit 必须提供 file_path、old_string、new_string。",
                "- 如果需要一次改多处，使用 MultiEdit(file_path, edits=[...])。",
            ]
        )
    if "invalid write tool input burst" in normalized or "inputvalidationerror" in normalized:
        return "\n".join(
            [
                "上轮失败原因：发出了无效的 Edit/MultiEdit/Write 工具调用，本轮先精确 Read，再提交完整编辑参数。",
                "- Edit: file_path + old_string + new_string",
                "- MultiEdit: file_path + edits[{old_string,new_string}]",
                "- Write: 只用于重写已存在文件，不要拿来创建新文件。",
            ]
        )
    text = re.sub(r"\n?\{.*?\"content\".*?\}\s*$", "", text, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= 900:
        return text
    return text[:897].rstrip() + "..."


def build_review_role_system_prompt() -> str:
    return """你是 Review 子Agent，职责是审查当前 patch 是否符合 C# 代码质量门禁和当前 issue 修复目标。

规则：
- 你不修改代码，只做审查
- 重点判断：当前 issue 是否看起来已修、代码是否违反核心 C# 质量门禁、是否会在编译或运行时引入明显风险
- 只基于当前 patch、当前代码和给定质量门禁做判断
- 输出必须是 JSON，对象字段固定为:
  {"decision":"approve|retry","summary":"...","findings":["..."],"constraints":["..."]}
"""


def build_main_role_system_prompt() -> str:
    return """你是 Main 裁决 Agent，职责是综合 Fix 子Agent和 Review 子Agent的结果，决定当前 patch 是否进入编译阶段。

规则：
- 你不修改代码
- 你的任务不是做风格评论，而是判断“现在值不值得编译”
- 如果 patch 方向明显不对，返回 retry
- 如果 patch 已基本符合 issue 和代码规范要求，返回 compile
- 输出必须是 JSON，对象字段固定为:
  {"decision":"compile|retry","summary":"...","constraints":["..."]}
"""


def build_fix_role_user_prompt(
    *,
    issue: Any,
    code_context: str,
    file_path_candidates: tuple[str, ...],
    working_memory: IssueWorkingMemory | None,
    fix_memory: ChildAgentMemory | None,
    retry_feedback: str,
) -> str:
    candidate_lines = "\n".join(f"- {item}" for item in file_path_candidates if str(item).strip())
    primary_candidate = _select_primary_candidate(issue, file_path_candidates)
    retry_feedback_text = _summarize_fix_retry_feedback(retry_feedback)
    sections = [
        "请直接修复当前 Sonar issue。",
        "【当前问题】",
        f"- Issue Key: {getattr(issue, 'key', '')}",
        f"- 规则ID: {getattr(issue, 'rule', '')}",
        f"- 问题描述: {getattr(issue, 'message', '')}",
        f"- 主文件相对路径: {primary_candidate or '未知'}",
        f"- 行号: {getattr(issue, 'line', '')}",
        "",
        "【问题代码】",
        str(code_context or "").strip(),
        "",
        render_issue_working_memory(working_memory),
        "",
        render_child_agent_memory(fix_memory),
        "",
        "【候选相对路径】",
        candidate_lines or "- 无",
    ]
    if retry_feedback_text:
        sections.extend(
            [
                "",
                "【上轮失败信息】",
                retry_feedback_text,
            ]
        )
    sections.extend(
        [
            "",
            "【工具使用提醒】",
            "- 读取和编辑时只使用上面的仓库相对路径，不要先尝试带前导 / 的路径。",
            "- 优先直接 Read 主文件相对路径；只有候选路径都失败时才用 Bash 搜索。",
            "- Edit 必须带 file_path、old_string、new_string；不要发送空 Edit。",
            "- MultiEdit 必须带 file_path 和至少一个 edits 项；Write 只允许重写已有文件。",
            "",
            "【执行要求】",
            "- 只修当前 issue",
            "- 只修改已有文件；禁止创建/删除文件",
            "- 不要自行构建",
            "- 完成后简短说明你改了什么即可",
        ]
    )
    return "\n".join(section for section in sections if str(section).strip()).strip()


def build_review_role_user_prompt(
    *,
    issue: Any,
    code_context: str,
    patch_summary: str,
    current_file_content: str,
    working_memory: IssueWorkingMemory | None,
    review_memory: ChildAgentMemory | None,
) -> str:
    quality_gate = load_quality_gate_skill_excerpt()
    sections = [
        "请审查当前 patch 是否已经足够进入编译阶段。",
        "【当前问题】",
        f"- Issue Key: {getattr(issue, 'key', '')}",
        f"- 规则ID: {getattr(issue, 'rule', '')}",
        f"- 问题描述: {getattr(issue, 'message', '')}",
        "",
        "【原始定位上下文】",
        str(code_context or "").strip(),
        "",
        "【当前 patch 摘要】",
        str(patch_summary or "").strip(),
        "",
        "【当前文件内容】",
        str(current_file_content or "").strip(),
        "",
        render_issue_working_memory(working_memory),
        "",
        render_child_agent_memory(review_memory),
    ]
    if quality_gate:
        sections.extend(
            [
                "",
                "【C# 质量门禁参考】",
                quality_gate,
            ]
        )
    sections.extend(
        [
            "",
            "【输出要求】",
            "- 只输出 JSON",
            '- decision 只能是 "approve" 或 "retry"',
            "- findings 用于说明你看到的风险或通过点",
            "- constraints 用于给 Fix 子Agent 下一轮的明确约束",
        ]
    )
    return "\n".join(section for section in sections if str(section).strip()).strip()


def build_main_role_user_prompt(
    *,
    issue: Any,
    patch_summary: str,
    review_result: dict[str, Any],
    working_memory: IssueWorkingMemory | None,
    main_memory: ChildAgentMemory | None,
) -> str:
    sections = [
        "请裁决当前 patch 是否值得进入编译阶段。",
        "【当前问题】",
        f"- Issue Key: {getattr(issue, 'key', '')}",
        f"- 规则ID: {getattr(issue, 'rule', '')}",
        f"- 问题描述: {getattr(issue, 'message', '')}",
        "",
        "【当前 patch 摘要】",
        str(patch_summary or "").strip(),
        "",
        "【Review 子Agent结论】",
        json.dumps(review_result or {}, ensure_ascii=False, indent=2),
        "",
        render_issue_working_memory(working_memory),
        "",
        render_child_agent_memory(main_memory),
        "",
        "【裁决规则】",
        '- 如果 patch 方向明显不对，输出 {"decision":"retry", ...}',
        '- 如果 patch 已具备进入编译的价值，输出 {"decision":"compile", ...}',
        "- 只输出 JSON",
    ]
    return "\n".join(section for section in sections if str(section).strip()).strip()
