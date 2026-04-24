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
from pi_sonar_agent.core.prompt_pipeline import PromptPipelineBuilder
from pi_sonar_agent.core.attempt_todo import (
    AttemptTodoState,
    render_attempt_todo_prompt_section,
)

QUALITY_GATE_SKILL_PATH = Path(r"C:\Users\neware\.claude\skills\csharp-quality-gate\SKILL.md")
S107_FIX_GUIDE_RELATIVE_PATH = ".pi-sonar-agent-runtime/s107-fix-guide.md"


def load_quality_gate_fix_digest() -> str:
    """Load the minimal quality-gate constraints that should guide the fix agent."""

    try:
        QUALITY_GATE_SKILL_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return "\n".join(
        [
            "- 只吸收与当前 issue 直接相关的最小质量约束；不要顺手补 XML 注释、中文注释、sealed、DI 或命名统一化。",
            "- 对 S3776 等复杂度问题，优先在目标方法体内做最小重写、提前返回、条件扁平化，不要顺手做整段架构重构。",
            "- 新增 async 逻辑前先确认需要真实 await；不要留下 async 无 await、async void 或半截 Async 改名。",
            "- 保持类型与签名稳定；不要为了绕过类型问题引入 dynamic、宽泛 object 参数或不必要的新 DTO。",
            "- 优先删除本轮引入的冗余局部变量、无用 using 和死代码，但不要为纯风格做额外大改。",
        ]
    )


def load_quality_gate_review_digest() -> str:
    """Load a compact review-only digest derived from the quality-gate skill."""

    try:
        QUALITY_GATE_SKILL_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return "\n".join(
        [
            "- 只审查当前 patch 是否值得进入编译，不做修复设计，不扩大发散范围。",
            "- 重点看当前 issue 是否真正改到目标方法，而不是只移动变量、改调用点或做无关整理。",
            "- 重点看是否引入明显的语法、类型、签名、async 或作用域风险。",
            "- 对 S3776，请基于当前代码判断是否已实质降低复杂度；不要要求 fix agent 额外提供复杂度数值证明或完整方法说明。",
            "- S3776 最终是否满足 <=30 由编译后的 post-check 再确认；review 阶段只拦明显跑偏或明显硬风险。",
            "- decision=retry 时，constraints 只给 1-3 条最小可执行约束；不得要求同步重构相似 sibling 方法。",
        ]
    )


def load_quality_gate_main_digest() -> str:
    """Load the minimal compile-gating policy used by the main decision agent."""

    try:
        QUALITY_GATE_SKILL_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return "\n".join(
        [
            "- 只判断当前 patch 是否值得进入编译，不重做 review，也不设计修法。",
            "- 只有当 patch 已改到当前 issue 目标方法、没有明显语法/类型/async/签名硬风险时，才允许 compile。",
            "- 不要因为 XML 注释、sealed、static、中文注释等非当前 issue 必要项而拒绝进入编译。",
            "- 如果 review 已 approve，而 main 看不到新的明确 blocker，优先进入编译而不是继续空转。",
            "- decision=retry 时，constraints 只保留进入下一轮前最关键的 1-3 条约束。",
        ]
    )


def _build_rule_specific_fix_requirements(issue: Any) -> str:
    if str(getattr(issue, "rule", "") or "").strip() != "csharpsquid:S107":
        return ""
    return "\n".join(
        [
            "【当前规则的硬约束】",
            "- S107 只有在目标方法最终签名参数总数降到 <=7 时才算修复完成；8 个或 9 个参数仍然算失败。",
            "- 改完后必须重新读取目标方法声明，按顶层参数重新计数，确认 <=7 后再结束本轮。",
            "- 不要提交“方向正确但仍未达阈值”的半成品；例如只合并两个参数但总数仍 >7，不算完成。",
            f"- 遇到复杂 S107（例如参数仍明显 >9、调用点不止一个、或混有多组 batch/preloaded/calculation state）时，先读取 `{S107_FIX_GUIDE_RELATIVE_PATH}` 再动手。",
            "- 如果当前方法是 private/internal 且调用点可控，优先收敛成同文件局部参数对象或私有上下文类型。",
            "- 优先一次性完成上下文类型、目标方法签名、方法体参数访问和全部调用点更新；不要靠一连串零碎替换把 turns 耗尽。",
        ]
    )


def _build_rule_specific_review_requirements(issue: Any) -> str:
    if str(getattr(issue, "rule", "") or "").strip() != "csharpsquid:S107":
        return ""
    return "\n".join(
        [
            "【当前规则的审查要点】",
            "- 对 S107，只有当目标方法当前签名参数总数已 <=7 时才能 approve。",
            "- 如果当前签名仍然 >7，必须 retry；不要因为“方向正确”“已合并部分参数”或“编译通过”而放行。",
            "- 请直接查看当前目标方法签名并重数顶层参数个数；tuple、局部变量或中间包装只按最终方法签名计数。",
        ]
    )


def _build_rule_specific_main_requirements(issue: Any) -> str:
    if str(getattr(issue, "rule", "") or "").strip() != "csharpsquid:S107":
        return ""
    return "\n".join(
        [
            "【当前规则的编译门槛】",
            "- 对 S107，只有当目标方法当前签名参数总数已 <=7 时才允许 compile。",
            "- 如果 review 或 patch 摘要已经表明“当前仍为 8/9 个参数”或“方向正确但未达阈值”，必须输出 retry。",
        ]
    )


def build_fix_role_system_prompt(*, todo_write_tool_name: str = "") -> str:
    pipeline = PromptPipelineBuilder()
    pipeline.add_core(
        "fix_role",
        """你是 Fix 子Agent，职责只有一个：在当前工作区内直接修改代码，完成当前 Sonar issue 的最小修复。

规则：
- 只修当前 issue，不顺手修其他问题
- 只允许修改已有文件；禁止创建文件、删除文件、重命名文件
- 优先直接对给出的仓库相对路径使用 Read/Edit/Write；路径不确定时先用 Glob/Grep，再用 Bash 做只读搜索或诊断
- Edit 必须同时提供 file_path、old_string、new_string；MultiEdit 必须提供 file_path 和至少一个 edits 项；Write 只能重写已有文件
- 优先做更小、更直接的局部修改；如果上一轮策略被否定，必须换一种更小的修法
- 不要输出长篇解释；直接读代码、编辑、完成后简短说明修法
""",
    )
    return pipeline.build().prompt


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
    pipeline = PromptPipelineBuilder()
    pipeline.add_core(
        "review_role",
        """你是 Review 子Agent，职责是审查当前 patch 是否符合 C# 代码质量门禁和当前 issue 修复目标。

规则：
- 你不修改代码，只做审查
- 你不是 Fix 子Agent，不做修复设计，不输出重构方案，不扩大发散到相似方法或相邻问题
- 重点判断：当前 issue 是否看起来已修、当前 patch 是否值得进入编译、是否会引入明显的编译/运行风险
- 只基于当前 issue、当前 patch、目标方法附近代码和给定门禁做判断
- 对 S3776，不要要求“复杂度数值证明”“完整方法说明”“额外解释提取了哪些逻辑”；你应直接阅读代码自行判断
- 对 S3776，最终是否满足 <=30 由编译后的 post-check 继续确认；review 阶段只拦明显跑偏和明显硬风险
- 如果不能明确 approve，就返回 retry；但 retry 只能给 1-3 条下一轮可直接执行的最小约束
- findings 用于解释你看到的事实；constraints 用于告诉 Fix 子Agent 下一轮具体怎么改
- 输出必须是 JSON，对象字段固定为:
  {"decision":"approve|retry","summary":"...","findings":["..."],"constraints":["..."]}
""",
    )
    return pipeline.build().prompt


def build_main_role_system_prompt() -> str:
    pipeline = PromptPipelineBuilder()
    pipeline.add_core(
        "main_role",
        """你是 Main 裁决 Agent，职责是综合 Fix 子Agent和 Review 子Agent的结果，决定当前 patch 是否进入编译阶段。

规则：
- 你不修改代码
- 你的任务不是做风格评论，而是判断“现在值不值得编译”
- 如果 patch 方向明显不对，返回 retry
- 如果 patch 已基本符合 issue 和代码规范要求，返回 compile
- 如果返回 retry，constraints 里必须写出 Fix 子Agent 下一轮可执行的约束
- 输出必须是 JSON，对象字段固定为:
  {"decision":"compile|retry","summary":"...","constraints":["..."]}
""",
    )
    return pipeline.build().prompt


def _render_role_prompt(
    *,
    intro: str,
    core_sections: list[str],
    support_sections: list[str],
    dynamic_sections: list[str],
) -> str:
    pipeline = PromptPipelineBuilder()
    pipeline.add_core("intro", intro)
    for index, section in enumerate(core_sections, start=1):
        pipeline.add_core(f"core_{index}", section)
    for index, section in enumerate(support_sections, start=1):
        pipeline.add_support(f"support_{index}", section)
    for index, section in enumerate(dynamic_sections, start=1):
        pipeline.add_dynamic(f"dynamic_{index}", section)
    return pipeline.build().prompt


def build_fix_role_user_prompt(
    *,
    issue: Any,
    code_context: str,
    file_path_candidates: tuple[str, ...],
    working_memory: IssueWorkingMemory | None = None,
    attempt_todo_state: AttemptTodoState | None = None,
    todo_write_tool_name: str = "",
    fix_memory: ChildAgentMemory | None = None,
    retry_feedback: str = "",
) -> str:
    candidate_lines = "\n".join(f"- {item}" for item in file_path_candidates if str(item).strip())
    primary_candidate = _select_primary_candidate(issue, file_path_candidates)
    retry_feedback_text = _summarize_fix_retry_feedback(retry_feedback)
    quality_gate = load_quality_gate_fix_digest()
    rule_specific_requirements = _build_rule_specific_fix_requirements(issue)
    core_sections = [
        "【当前问题】",
        f"- Issue Key: {getattr(issue, 'key', '')}",
        f"- 规则ID: {getattr(issue, 'rule', '')}",
        f"- 问题描述: {getattr(issue, 'message', '')}",
        f"- 主文件相对路径: {primary_candidate or '未知'}",
        f"- 行号: {getattr(issue, 'line', '')}",
        "【问题代码】",
        str(code_context or "").strip(),
        "【候选相对路径】",
        candidate_lines or "- 无",
        "【执行要求】\n"
        + "\n".join(
            [
                "- 只修当前 issue",
                "- 只修改已有文件；禁止创建/删除文件",
                "- 外层会统一决定是否编译；本轮优先把 patch 修对",
                "- 完成后简短说明你改了什么即可",
            ]
        ),
    ]
    support_sections: list[str] = []
    if quality_gate:
        support_sections.append("【Fix 质量约束】\n" + quality_gate)
    if rule_specific_requirements:
        support_sections.append(rule_specific_requirements)
    dynamic_sections = [
        render_issue_working_memory(working_memory),
        render_attempt_todo_prompt_section(
            attempt_todo_state,
            tool_name=todo_write_tool_name,
        ),
        render_child_agent_memory(fix_memory),
        "【工具使用提醒】\n"
        + "\n".join(
            [
                "- 读取和编辑时只使用上面的仓库相对路径，不要先尝试带前导 / 的路径。",
                "- 优先直接 Read/Edit/Write 主文件相对路径；路径不确定时先用 Glob/Grep，最后才用 Bash 做只读搜索。",
                "- Edit 必须带 file_path、old_string、new_string；不要发送空 Edit。",
                "- MultiEdit 必须带 file_path 和至少一个 edits 项；Write 只允许重写已有文件。",
                "- 如果当前修复明显不是一步就能完成，并且 TodoWrite 工具可用，请先维护当前 attempt 的执行清单再继续动手。",
            ]
        ),
    ]
    if retry_feedback_text:
        dynamic_sections.append("【上轮失败信息】\n" + retry_feedback_text)
    return _render_role_prompt(
        intro="请直接修复当前 Sonar issue。",
        core_sections=core_sections,
        support_sections=support_sections,
        dynamic_sections=dynamic_sections,
    )


def build_review_role_user_prompt(
    *,
    issue: Any,
    code_context: str,
    patch_summary: str,
    current_file_content: str,
    working_memory: IssueWorkingMemory | None,
    review_memory: ChildAgentMemory | None,
) -> str:
    quality_gate = load_quality_gate_review_digest()
    rule_specific_requirements = _build_rule_specific_review_requirements(issue)
    target_excerpt = _build_review_target_excerpt(
        issue=issue,
        code_context=code_context,
        current_file_content=current_file_content,
    )
    state_digest = _build_review_state_digest(
        working_memory=working_memory,
        review_memory=review_memory,
    )
    core_sections = [
        "【当前问题】",
        f"- Issue Key: {getattr(issue, 'key', '')}",
        f"- 规则ID: {getattr(issue, 'rule', '')}",
        f"- 问题描述: {getattr(issue, 'message', '')}",
        f"- 目标文件: {_normalize_relative_path(str(getattr(issue, 'file_path', '') or '')) or '未知'}",
        f"- 目标行: {getattr(issue, 'line', '')}",
        "【当前 patch 摘要】",
        str(patch_summary or "").strip(),
        target_excerpt,
        "【输出要求】\n"
        + "\n".join(
            [
                "- 只输出 JSON",
                '- decision 只能是 "approve" 或 "retry"',
                "- summary 必须是一句明确结论，不能留空",
                "- findings 只写当前 patch 的事实判断，不写泛泛风格建议",
                "- constraints 用于给 Fix 子Agent 下一轮的明确约束；decision=retry 时 constraints 至少提供 1 条",
                "- constraints 必须限制在当前 issue 目标方法和当前 patch，不要要求同步改相似 sibling 方法",
            ]
        ),
    ]
    support_sections: list[str] = []
    if quality_gate:
        support_sections.append("【Review 门禁要点】\n" + quality_gate)
    if rule_specific_requirements:
        support_sections.append(rule_specific_requirements)
    dynamic_sections = [state_digest]
    return _render_role_prompt(
        intro="请只做 patch 审查，判断当前 patch 是否已经足够进入编译阶段。",
        core_sections=core_sections,
        support_sections=support_sections,
        dynamic_sections=dynamic_sections,
    )


def _build_review_target_excerpt(*, issue: Any, code_context: str, current_file_content: str) -> str:
    sections = ["【目标方法附近代码】"]
    original_context = str(code_context or "").strip()
    if original_context:
        sections.extend(
            [
                "- 原始定位片段:",
                original_context,
            ]
        )
    current_excerpt = _slice_review_file_excerpt(
        current_file_content,
        line_hint=getattr(issue, "line", 0),
        window=26,
        max_chars=2600,
    )
    if current_excerpt:
        sections.extend(
            [
                "- 当前文件局部摘录:",
                current_excerpt,
            ]
        )
    if len(sections) == 1:
        sections.append("- 无可用代码摘录")
    return "\n".join(sections).strip()


def _slice_review_file_excerpt(
    current_file_content: str,
    *,
    line_hint: Any,
    window: int,
    max_chars: int,
) -> str:
    text = str(current_file_content or "").replace("\r\n", "\n")
    if not text.strip():
        return ""
    lines = text.split("\n")
    try:
        line_number = int(line_hint or 0)
    except (TypeError, ValueError):
        line_number = 0
    if line_number > 0:
        start = max(line_number - 1 - window, 0)
        end = min(line_number - 1 + window + 1, len(lines))
        excerpt_lines = lines[start:end]
    else:
        excerpt_lines = lines[: min(len(lines), window * 2)]
        start = 0
    numbered = [f"{start + index + 1:>4} | {line}" for index, line in enumerate(excerpt_lines)]
    excerpt = "\n".join(numbered).strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[: max_chars - 3].rstrip() + "..."


def _build_review_state_digest(
    *,
    working_memory: IssueWorkingMemory | None,
    review_memory: ChildAgentMemory | None,
) -> str:
    lines = ["【当前审查状态】"]
    if working_memory is not None:
        workspace_state = str(getattr(working_memory, "authoritative_workspace_state", "") or "").strip()
        if workspace_state:
            lines.append(f"- 当前工作区状态: {workspace_state}")
        rollback_reason = str(getattr(working_memory, "rollback_reason", "") or "").strip()
        if rollback_reason:
            lines.append(f"- 回滚说明: {rollback_reason}")
        latest_verification = str(getattr(working_memory, "latest_verification", "") or "").strip()
        if latest_verification:
            lines.append(f"- 最近验证: {latest_verification}")
        rejected = tuple(getattr(working_memory, "rejected_strategies", ()) or ())
        if rejected:
            lines.append("- 已否定策略: " + "；".join(str(item).strip() for item in rejected[:3] if str(item).strip()))
    if review_memory is not None:
        latest_summary = str(getattr(review_memory, "latest_summary", "") or "").strip()
        if latest_summary:
            lines.append(f"- 上轮 review 摘要: {latest_summary}")
        latest_constraints = tuple(getattr(review_memory, "latest_constraints", ()) or ())
        if latest_constraints:
            lines.append("- 上轮 review 约束: " + "；".join(str(item).strip() for item in latest_constraints[:3] if str(item).strip()))
    if len(lines) == 1:
        lines.append("- 无额外审查状态")
    return "\n".join(lines).strip()


def build_main_role_user_prompt(
    *,
    issue: Any,
    patch_summary: str,
    review_result: dict[str, Any],
    working_memory: IssueWorkingMemory | None,
    main_memory: ChildAgentMemory | None,
) -> str:
    quality_gate = load_quality_gate_main_digest()
    rule_specific_requirements = _build_rule_specific_main_requirements(issue)
    core_sections = [
        "【当前问题】",
        f"- Issue Key: {getattr(issue, 'key', '')}",
        f"- 规则ID: {getattr(issue, 'rule', '')}",
        f"- 问题描述: {getattr(issue, 'message', '')}",
        "【当前 patch 摘要】",
        str(patch_summary or "").strip(),
        "【Review 子Agent结论】",
        json.dumps(review_result or {}, ensure_ascii=False, indent=2),
        "【裁决规则】\n"
        + "\n".join(
            [
                '- 如果 patch 方向明显不对，输出 {"decision":"retry", ...}',
                '- 如果 patch 已具备进入编译的价值，输出 {"decision":"compile", ...}',
                '- 如果输出 retry，constraints 至少包含 1 条下一轮可执行约束',
                "- 只输出 JSON",
            ]
        ),
    ]
    support_sections: list[str] = []
    if quality_gate:
        support_sections.append("【Main 裁决门禁】\n" + quality_gate)
    if rule_specific_requirements:
        support_sections.append(rule_specific_requirements)
    dynamic_sections = [
        render_issue_working_memory(working_memory),
        render_child_agent_memory(main_memory),
    ]
    return _render_role_prompt(
        intro="请裁决当前 patch 是否值得进入编译阶段。",
        core_sections=core_sections,
        support_sections=support_sections,
        dynamic_sections=dynamic_sections,
    )
