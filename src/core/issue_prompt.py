"""Prompt composition helpers for single-issue fix attempts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_sonar_agent.agent.rule_policies import get_rule_policy
from pi_sonar_agent.core.memory.issue_compaction import maybe_compact_issue_prompt
from pi_sonar_agent.core.memory.issue_working_memory import (
    IssueWorkingMemory,
    render_issue_working_memory,
)
from pi_sonar_agent.core.registry import BUILD_TOOL_NAMES
from pi_sonar_agent.core.resource_loader import ResourceLoader
from pi_sonar_agent.core.retry_context import RetryContext, render_retry_context
from pi_sonar_agent.core.simple_mode import is_simple_loop_execution_mode
from pi_sonar_agent.core.state import serialize_state
from pi_sonar_agent.core.tool_surface import (
    render_controlled_bash_prompt_constraints,
    render_visible_tool_summary,
)

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue


SIMPLE_LOOP_SYSTEM_PROMPT = """你是一个严格的 .NET/C# 资深工程师，专门修复 SonarQube 问题。

当前运行在 headless simple-loop 模式。

目标：
1. 只修当前 issue
2. 产出最小、可编译的 patch
3. 让外层流程统一执行 build 和 post-check

硬约束：
- 只使用当前运行时真正可见的工具
- 只使用仓库内相对路径
- 不要执行 git add / git commit / git push
- 不要通过 shell 直接改写已有源码
- 不要自行执行 dotnet restore/build/test；构建与验证由外层统一执行
- 不要输出长篇推理，不要做无关重构，不要顺手修其他 issue
"""


SIMPLE_LOOP_USER_PROMPT_TEMPLATE = """请只修复以下 SonarQube issue，并优先完成最小可编译修复。

【当前问题】
- Issue Key: {issue_key}
- 规则ID: {rule_id}
- 问题描述: {message}
- 严重程度: {severity}
- 文件路径: {file_path}
- 报错行号: {line}

【精确定位】
{issue_location_guidance}

【SonarQube 修复建议】
{rule_fix_guidance}

【问题代码】
{code_context}

{working_memory_section}
{rule_guard_section}

【允许修改范围】
{scope_guidance}

{tool_surface_section}
{retry_feedback_section}

【执行要求】
- 先完成当前 issue 的最小可编译修复
- 只使用仓库内相对路径；优先直接操作下面这些候选路径：
{file_path_candidates}
- 如果上一轮策略失败或已回滚，请换一种更小的修法，不要机械重复已撤销的改法
- 外层统一执行 build 和 post-check；本轮不要自行构建
- 不要顺手修复本文件中其他 issue，不要做大重构
"""


@dataclass(frozen=True)
class PromptBuildResult:
    """Concrete prompt text plus section-level budget metadata."""

    prompt: str
    target_chars: int
    section_chars: dict[str, int] = field(default_factory=dict)
    truncated_sections: tuple[str, ...] = ()
    externalized_sections: tuple[str, ...] = ()
    reference_document_path: str = ""
    estimated_tokens: int = 0
    token_budget: int = 0
    token_estimator: str = "heuristic"
    compaction_applied: bool = False
    compaction_reason: str = ""
    issue_working_memory: IssueWorkingMemory | None = None

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class PromptBudgetReport:
    """Merged system/user prompt budget summary stored in artifacts."""

    system_chars: int
    user_chars: int
    system_target_chars: int
    user_target_chars: int
    system_within_target: bool
    user_within_target: bool
    system_sections: dict[str, int] = field(default_factory=dict)
    user_sections: dict[str, int] = field(default_factory=dict)
    truncated_sections: tuple[str, ...] = ()
    externalized_sections: tuple[str, ...] = ()
    reference_document_path: str = ""
    estimated_tokens: int = 0
    token_budget: int = 0
    token_estimator: str = "heuristic"
    compaction_applied: bool = False
    compaction_reason: str = ""
    compaction_generation: int = 0
    compact_summary_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


class IssuePromptBuilder:
    """Compose the system and user prompts for single-issue fix attempts."""

    SYSTEM_PROMPT_TARGET_CHARS = 6000
    USER_PROMPT_TARGET_CHARS = 8000
    PROMPT_INLINE_THRESHOLD = 2500
    REFERENCE_DOC_RELATIVE_PATH = ".git/pi-sonar-agent-runtime/sonar_fix_reference.md"
    RULE_DESCRIPTION_MAX_CHARS = 900
    RULE_FIX_MAX_CHARS = 700
    CODE_CONTEXT_MAX_CHARS = 2400
    PREFETCHED_CONTEXT_MAX_CHARS = 1800
    EXECUTION_MODE_MAX_CHARS = 700
    WORKING_MEMORY_MAX_CHARS = 1400

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def normalize_prompt_text(value: str, fallback: str) -> str:
        """Normalize prompt text so the model always gets usable guidance."""

        text = str(value or "").strip()
        return text if text else fallback

    @classmethod
    def _clip_section(
        cls,
        value: str,
        fallback: str,
        *,
        max_chars: int,
        max_lines: int,
        section_name: str,
        truncated_sections: list[str],
    ) -> str:
        text = cls.normalize_prompt_text(value, fallback)
        clipped = ResourceLoader.truncate_for_prompt(text, max_chars, max_lines=max_lines)
        if clipped != text:
            truncated_sections.append(section_name)
        return clipped

    @classmethod
    def _render_location_reference(
        cls,
        file_path: str,
        text_range: dict[str, Any] | None,
        fallback_line: int = 0,
    ) -> str:
        normalized_path = cls.render_workspace_relative_path(file_path)
        raw_range = text_range if isinstance(text_range, dict) else {}
        start_line = cls._safe_int(raw_range.get("startLine")) or fallback_line
        end_line = cls._safe_int(raw_range.get("endLine")) or start_line
        start_offset = raw_range.get("startOffset")
        end_offset = raw_range.get("endOffset")

        if not start_line:
            return normalized_path

        if start_offset is None and end_offset is None:
            return f"{normalized_path}:{start_line}"

        start_col = cls._safe_int(start_offset) + 1
        end_col = cls._safe_int(end_offset) + 1 if end_offset is not None else start_col
        if end_line and end_line != start_line:
            return f"{normalized_path}:{start_line}:{start_col}-{end_line}:{end_col}"
        if end_col > start_col:
            return f"{normalized_path}:{start_line}:{start_col}-{end_col}"
        return f"{normalized_path}:{start_line}:{start_col}"

    @classmethod
    def build_issue_location_guidance(cls, issue: SonarIssue) -> str:
        lines = [
            f"- 主定位: {cls._render_location_reference(issue.file_path, issue.text_range, issue.line)}"
        ]

        seen_locations: set[tuple[str, str, str]] = set()
        flow_index = 0
        for flow in getattr(issue, "flows", ()) or ():
            if not isinstance(flow, dict):
                continue
            for location in flow.get("locations", ()) or ():
                if not isinstance(location, dict):
                    continue
                component = str(location.get("component", "")).strip()
                message = str(location.get("msg", "")).strip()
                reference = cls._render_location_reference(
                    component.split(":", 1)[-1] if component else issue.file_path,
                    location.get("textRange"),
                )
                signature = (reference, component, message)
                if signature in seen_locations:
                    continue
                seen_locations.add(signature)
                flow_index += 1
                detail = f"- 关联位置 {flow_index}: {reference}"
                if message:
                    detail += f" | {message}"
                lines.append(detail)
                if flow_index >= 4:
                    return "\n".join(lines)
        return "\n".join(lines)

    @staticmethod
    def build_rule_guard_section(
        rule_id: str,
        *,
        retry_context: RetryContext | None = None,
        execution_mode: str = "",
    ) -> str:
        """Render rule-specific prompt guards when configured."""

        policy = get_rule_policy(rule_id)
        guards: list[str] = []
        if retry_context is not None:
            retry_fingerprints = {
                str(item).strip()
                for item in getattr(retry_context, "failure_fingerprints", ()) or ()
                if str(item).strip()
            }
            if retry_fingerprints.intersection(
                {"helper_extraction_type_break", "nullable_type_mismatch"}
            ):
                guards.extend(
                    (
                        "本轮硬约束：禁止新增 helper/private 方法，必须在当前方法体内收口复杂度或类型流转。",
                        "本轮硬约束：禁止使用 dynamic 作为参数、返回值或中间兜底类型。",
                        "本轮硬约束：禁止让匿名类型或 nullable-heavy 状态跨方法边界流动。",
                    )
                )
        if not guards:
            return ""

        deduped_guards: list[str] = []
        seen: set[str] = set()
        for item in guards:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            deduped_guards.append(normalized)
            seen.add(normalized)

        lines = ["【当前规则的额外约束】"]
        lines.extend(f"- {item}" for item in deduped_guards)
        return "\n".join(lines)

    @classmethod
    def _estimate_prompt_chars(cls, sections: tuple[str, ...] | list[str]) -> int:
        return sum(len(str(section or "")) for section in sections)

    @classmethod
    def _reference_doc_path(cls, workspace_path: Path) -> Path:
        git_dir = workspace_path / ".git"
        if git_dir.is_dir():
            return workspace_path / cls.REFERENCE_DOC_RELATIVE_PATH
        return workspace_path / ".pi-sonar-agent-runtime" / "sonar_fix_reference.md"

    @classmethod
    def _build_reference_document(
        cls,
        *,
        rule_description: str,
        rule_fix_guidance: str,
        quality_gate_section: str,
        rule_guard_section: str,
        edit_contract_section: str,
        repair_plan_section: str,
        prefetched_context_section: str,
        tool_surface_section: str,
        execution_mode_section: str,
    ) -> str:
        sections = ["# Sonar Fix Reference", "", "以下内容为本次修复的详细约束与参考资料。"]
        for title, content in (
            ("规则说明", rule_description),
            ("修复建议", rule_fix_guidance),
            ("质量门禁", quality_gate_section),
            ("规则额外约束", rule_guard_section),
            ("Edit Contract", edit_contract_section),
            ("Repair Plan", repair_plan_section),
            ("预取上下文", prefetched_context_section),
            ("工具策略", tool_surface_section),
            ("执行模式", execution_mode_section),
        ):
            text = str(content or "").strip()
            if not text:
                continue
            sections.extend(["", f"## {title}", "", text])
        return "\n".join(sections).strip() + "\n"

    @classmethod
    def _maybe_externalize_reference_sections(
        cls,
        *,
        workspace_path: Path | None,
        rule_description: str,
        rule_fix_guidance: str,
        quality_gate_section: str,
        rule_guard_section: str,
        edit_contract_section: str,
        repair_plan_section: str,
        prefetched_context_section: str,
        tool_surface_section: str,
        execution_mode_section: str,
        code_context: str,
        retry_feedback_section: str,
    ) -> tuple[dict[str, str], tuple[str, ...], str]:
        inline_sections = {
            "rule_description": rule_description,
            "rule_fix_guidance": rule_fix_guidance,
            "quality_gate_section": quality_gate_section,
            "rule_guard_section": rule_guard_section,
            "edit_contract_section": edit_contract_section,
            "repair_plan_section": repair_plan_section,
            "prefetched_context_section": prefetched_context_section,
            "tool_surface_section": tool_surface_section,
            "execution_mode_section": execution_mode_section,
        }
        if workspace_path is None:
            return inline_sections, (), ""

        total_chars = cls._estimate_prompt_chars(
            (
                rule_description,
                rule_fix_guidance,
                quality_gate_section,
                rule_guard_section,
                edit_contract_section,
                repair_plan_section,
                prefetched_context_section,
                tool_surface_section,
                execution_mode_section,
                code_context,
                retry_feedback_section,
            )
        )
        if total_chars <= cls.PROMPT_INLINE_THRESHOLD:
            return inline_sections, (), ""

        reference_path = cls._reference_doc_path(workspace_path)
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference_path.write_text(
            cls._build_reference_document(
                rule_description=rule_description,
                rule_fix_guidance=rule_fix_guidance,
                quality_gate_section=quality_gate_section,
                rule_guard_section=rule_guard_section,
                edit_contract_section=edit_contract_section,
                repair_plan_section=repair_plan_section,
                prefetched_context_section=prefetched_context_section,
                tool_surface_section=tool_surface_section,
                execution_mode_section=execution_mode_section,
            ),
            encoding="utf-8",
        )
        reference_relative_path = reference_path.relative_to(workspace_path).as_posix()
        reference_hint = (
            f"详细约束已写入 `{reference_relative_path}`。"
            "优先按主 prompt 直接修复；需要核对门禁或 repair plan 细节时再读取该文件。"
        )
        externalized = (
            "quality_gate_section",
            "rule_guard_section",
            "edit_contract_section",
            "repair_plan_section",
            "prefetched_context_section",
            "tool_surface_section",
            "execution_mode_section",
        )
        inline_sections.update(
            {
                "quality_gate_section": "【C# 代码质量门禁】\n" + reference_hint,
                "rule_guard_section": "【当前规则的额外约束】\n- 详细规则约束见参考文件；先按最小改动直接修复。",
                "edit_contract_section": f"【Edit Contract】\n- {reference_hint}",
                "repair_plan_section": "【Repair Plan】\n- 详细 repair plan 见参考文件；先执行主 prompt 中的核心修复方向。",
                "prefetched_context_section": "【预取上下文】\n- 详细上下文见参考文件；先围绕问题位置和 contract 修复。",
                "tool_surface_section": f"【工具策略】\n- {reference_hint}",
                "execution_mode_section": "【执行模式】\n- 执行细节见参考文件；优先做最小、可回滚的改动。",
            }
        )
        return inline_sections, externalized, reference_relative_path

    @classmethod
    def build_system_prompt_result(
        cls,
        workspace_path: Path,
        *,
        execution_mode: str = "",
    ) -> PromptBuildResult:
        """Compose the fix system prompt with optional workspace rules."""

        base_prompt = SIMPLE_LOOP_SYSTEM_PROMPT
        prompt = ResourceLoader.compose_system_prompt(
            base_prompt,
            workspace_path,
            max_chars=cls.SYSTEM_PROMPT_TARGET_CHARS,
            max_project_rule_chars=1400,
            max_workspace_rule_chars=1400,
        )
        return PromptBuildResult(
            prompt=prompt,
            target_chars=cls.SYSTEM_PROMPT_TARGET_CHARS,
            section_chars={"system_prompt": len(prompt)},
            truncated_sections=(
                ("system_prompt",)
                if len(prompt) >= cls.SYSTEM_PROMPT_TARGET_CHARS
                and ResourceLoader.TRUNCATION_NOTICE in prompt
                else ()
            ),
        )

    @classmethod
    def build_prompt_budget_report(
        cls,
        system_result: PromptBuildResult,
        user_result: PromptBuildResult,
    ) -> PromptBudgetReport:
        return PromptBudgetReport(
            system_chars=len(system_result.prompt),
            user_chars=len(user_result.prompt),
            system_target_chars=system_result.target_chars,
            user_target_chars=user_result.target_chars,
            system_within_target=len(system_result.prompt) <= system_result.target_chars,
            user_within_target=len(user_result.prompt) <= user_result.target_chars,
            system_sections=dict(system_result.section_chars),
            user_sections=dict(user_result.section_chars),
            truncated_sections=tuple(
                dict.fromkeys(system_result.truncated_sections + user_result.truncated_sections)
            ),
            externalized_sections=user_result.externalized_sections,
            reference_document_path=user_result.reference_document_path,
            estimated_tokens=user_result.estimated_tokens,
            token_budget=user_result.token_budget,
            token_estimator=user_result.token_estimator,
            compaction_applied=user_result.compaction_applied,
            compaction_reason=user_result.compaction_reason,
            compaction_generation=int(
                getattr(getattr(user_result, "issue_working_memory", None), "compaction_generation", 0) or 0
            ),
            compact_summary_path=str(
                getattr(getattr(user_result, "issue_working_memory", None), "compact_summary_path", "") or ""
            ).strip(),
        )

    @classmethod
    def build_system_prompt(cls, workspace_path: Path, *, execution_mode: str = "") -> str:
        """Backwards-compatible string-only system prompt builder."""

        return cls.build_system_prompt_result(
            workspace_path,
            execution_mode=execution_mode,
        ).prompt

    @staticmethod
    def render_workspace_relative_path(file_path: str) -> str:
        """Render the issue path as a workspace-relative path for the model."""

        normalized = str(file_path or "").replace("\\", "/").strip()
        if normalized.startswith("/"):
            normalized = normalized[1:]
        return normalized or "."

    @classmethod
    def build_workspace_relative_candidates(
        cls,
        file_path: str,
        workspace_path: Path | None = None,
    ) -> tuple[str, ...]:
        """Render stable path candidates relative to the runtime cwd."""

        primary = cls.render_workspace_relative_path(file_path)
        candidates: list[str] = [primary]
        if workspace_path is not None:
            workspace_name = str(workspace_path.name or "").replace("\\", "/").strip("/")
            if workspace_name and primary.startswith(workspace_name + "/"):
                candidates.append(primary[len(workspace_name) + 1 :])
        return tuple(dict.fromkeys(item for item in candidates if str(item).strip()))

    @staticmethod
    def _normalize_visible_tool_names(
        visible_tool_names: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(str(name).strip() for name in visible_tool_names if str(name).strip())
        )

    @classmethod
    def _has_visible_build_tool(
        cls,
        visible_tool_names: tuple[str, ...] | list[str],
    ) -> bool:
        normalized = cls._normalize_visible_tool_names(visible_tool_names)
        return any(
            name in BUILD_TOOL_NAMES
            or name == "run_build"
            or name.endswith("__run_build")
            for name in normalized
        )

    @classmethod
    def _build_build_command_section(
        cls,
        build_command: str,
        visible_tool_names: tuple[str, ...] | list[str],
        *,
        execution_mode: str = "",
    ) -> str:
        normalized_build_command = cls.normalize_prompt_text(build_command, "dotnet build")
        normalized_visible_tools = cls._normalize_visible_tool_names(visible_tool_names)
        if is_simple_loop_execution_mode(execution_mode):
            return "【构建执行】\n构建与 post-check 由外层统一执行；本轮不要自行运行 dotnet restore/build/test。"
        if normalized_visible_tools and not cls._has_visible_build_tool(normalized_visible_tools):
            return (
                "【构建执行】\n"
                "构建与验证由外层流程统一执行；本轮不要在 Bash 中执行 "
                "dotnet restore/build/test、msbuild 或 nuget restore。"
            )
        return f"【推荐构建命令】\n{normalized_build_command}"

    @classmethod
    def _build_tool_surface_section(
        cls,
        *,
        visible_tool_names: tuple[str, ...],
        edit_contract: Any | None,
        execution_mode: str,
    ) -> str:
        bash_visible = "Bash" in set(visible_tool_names)
        allow_build_commands = not visible_tool_names or cls._has_visible_build_tool(visible_tool_names)
        if is_simple_loop_execution_mode(execution_mode):
            lines: list[str] = []
            if visible_tool_names:
                lines.append(f"【当前可用工具】\n- {render_visible_tool_summary(visible_tool_names)}")
            if bash_visible:
                bash_line = "- 优先直接对给出的相对路径使用 Read/Edit/Write；路径不确定时先用 Glob/Grep，再用 Bash 做只读搜索或诊断。"
                if bool(getattr(edit_contract, "allow_file_creation", False)):
                    bash_line += " 如需新建允许的新文件，优先使用 Write。"
                lines.append(bash_line)
            return "\n".join(lines)

        tool_surface_lines: list[str] = []
        if visible_tool_names:
            tool_surface_lines.append(
                "当前 attempt 可用工具: " + render_visible_tool_summary(visible_tool_names)
            )
        bash_constraints = render_controlled_bash_prompt_constraints(
            enabled=bash_visible,
            allow_file_creation=bool(getattr(edit_contract, "allow_file_creation", False)),
            allow_build_commands=allow_build_commands,
            allowed_new_file_roots=getattr(edit_contract, "allowed_new_file_roots", ()) or (),
        )
        if bash_constraints:
            tool_surface_lines.extend(bash_constraints)
        if tool_surface_lines:
            return "【工具策略】\n" + "\n".join(f"- {item}" for item in tool_surface_lines)
        return ""

    @classmethod
    def _render_retry_feedback_section(
        cls,
        *,
        retry_feedback_text: str,
        retry_context: RetryContext | None,
        working_memory: IssueWorkingMemory | None,
        truncated_sections: list[str],
    ) -> str:
        normalized_feedback = cls.normalize_prompt_text(retry_feedback_text, "").strip()
        if not normalized_feedback:
            return ""

        rollback_reason = (
            str(getattr(working_memory, "rollback_reason", "") or "").strip()
            if working_memory is not None
            else ""
        )
        stale_evidence = tuple(
            str(item).strip()
            for item in getattr(working_memory, "stale_evidence", ()) or ()
            if str(item).strip()
        ) if working_memory is not None else ()
        if rollback_reason or stale_evidence:
            lines = ["【历史失败线索】"]
            lines.append(
                "以下失败信息来自已撤销 patch，仅作历史线索；请以当前工作区状态和当前代码内容为准。"
            )
            if retry_context is not None:
                summary = str(getattr(retry_context, "summary", "") or "").strip()
                if summary:
                    lines.append(f"- 上轮失败摘要: {summary}")
                fingerprint = str(
                    getattr(retry_context, "primary_failure_fingerprint", "") or ""
                ).strip()
                if fingerprint:
                    lines.append(f"- 上轮失败指纹: {fingerprint}")
                compiler_codes = tuple(
                    dict.fromkeys(
                        str(item.code).strip()
                        for item in getattr(retry_context, "compiler_errors", ())
                        if str(item.code).strip()
                    )
                )
                if compiler_codes:
                    lines.append(f"- 上轮编译错误码: {', '.join(compiler_codes[:4])}")
            if stale_evidence:
                lines.append(f"- 已失效旧证据: {'; '.join(stale_evidence[:3])}")
            if rollback_reason:
                lines.append(f"- 回滚说明: {rollback_reason}")
            lines.append("请更换策略继续修复，不要机械重复上一轮已撤销的改法。")
            return "\n".join(lines)

        clipped = ResourceLoader.truncate_for_prompt(
            normalized_feedback,
            1800,
            max_lines=80,
        )
        if clipped != normalized_feedback:
            truncated_sections.append("retry_feedback_section")
        return (
            "【上次尝试的失败信息】\n"
            f"{clipped}\n\n"
            "请基于这些失败原因重新修复，避免再次引入相同的问题。"
        )

    @staticmethod
    def _render_compaction_boundary_section(
        working_memory: IssueWorkingMemory | None,
    ) -> str:
        if working_memory is None:
            return ""
        boundary_note = str(getattr(working_memory, "compact_boundary_note", "") or "").strip()
        if not boundary_note:
            return ""
        lines = ["【上下文压缩边界】", boundary_note]
        compact_path = str(getattr(working_memory, "compact_summary_path", "") or "").strip()
        if compact_path:
            lines.append(f"- 详细压缩摘要见: `{compact_path}`")
        compacted_history = str(getattr(working_memory, "compacted_history_summary", "") or "").strip()
        if compacted_history:
            lines.append(f"- 历史压缩摘要: {compacted_history}")
        return "\n".join(lines)

    @staticmethod
    def _render_durable_memory_section(edit_contract: Any | None) -> str:
        lessons = tuple(getattr(edit_contract, "planner_lessons", ()) or ())
        if not lessons:
            return ""

        lines = ["【长期参考】"]
        rendered_count = 0
        for lesson in lessons[:2]:
            summary = str(getattr(lesson, "summary", "") or "").strip()
            if not summary:
                continue
            source = str(getattr(lesson, "source", "") or "").strip() or "lesson"
            selection_mode = str(getattr(lesson, "selection_mode", "") or "").strip()
            selection_reason = str(getattr(lesson, "selection_reason", "") or "").strip()
            header = f"- [{source}"
            if selection_mode:
                header += f"/{selection_mode}"
            header += f"] {summary}"
            lines.append(header)
            if selection_reason:
                lines.append(f"  选中原因: {selection_reason}")
            guidance = tuple(
                str(item).strip()
                for item in getattr(lesson, "guidance", ()) or ()
                if str(item).strip()
            )
            if guidance:
                lines.append(f"  建议: {guidance[0]}")
            rendered_count += 1
        if rendered_count == 0:
            return ""
        lines.append("这些长期经验只作为补充参考；当前工作记忆和当前代码状态优先。")
        return "\n".join(lines)

    @classmethod
    def build_user_prompt_result(
        cls,
        issue: SonarIssue,
        code_context: str,
        quality_gate_text: str,
        scope_guidance: str,
        rule_details: dict[str, str],
        build_command: str,
        retry_feedback: str = "",
        retry_context: RetryContext | None = None,
        edit_contract_section: str = "",
        repair_plan_section: str = "",
        prefetched_context_section: str = "",
        execution_mode_section: str = "",
        workspace_path: Path | None = None,
        edit_contract: Any | None = None,
        visible_tool_names: tuple[str, ...] | list[str] = (),
        working_memory: IssueWorkingMemory | None = None,
        model_hint: str = "",
    ) -> PromptBuildResult:
        """Build the issue-specific user prompt and budget metadata."""

        truncated_sections: list[str] = []

        effective_working_memory = working_memory
        rendered_retry_context = render_retry_context(retry_context)
        retry_feedback_text = cls.normalize_prompt_text(
            rendered_retry_context or retry_feedback,
            "",
        ).strip()
        execution_mode = str(getattr(edit_contract, "execution_mode", "") or "").strip()
        quality_gate_section = ""
        quality_gate = cls.normalize_prompt_text(quality_gate_text, "").strip()
        if quality_gate:
            quality_gate_section = "【C# 代码质量门禁】\n" + ResourceLoader.truncate_for_prompt(
                quality_gate,
                1800,
                max_lines=60,
            )
            if quality_gate not in quality_gate_section:
                truncated_sections.append("quality_gate_section")

        rule_guard_section = cls.build_rule_guard_section(
            issue.rule,
            retry_context=retry_context,
            execution_mode=execution_mode,
        )
        visible_tool_list = cls._normalize_visible_tool_names(visible_tool_names)
        build_command_section = cls._build_build_command_section(
            build_command,
            visible_tool_list,
            execution_mode=execution_mode,
        )
        tool_surface_section = cls._build_tool_surface_section(
            visible_tool_names=visible_tool_list,
            edit_contract=edit_contract,
            execution_mode=execution_mode,
        )

        rule_description = cls._clip_section(
            rule_details.get("description", ""),
            "SonarQube 未返回规则说明，请结合问题描述和代码上下文分析根因。",
            max_chars=cls.RULE_DESCRIPTION_MAX_CHARS,
            max_lines=24,
            section_name="rule_description",
            truncated_sections=truncated_sections,
        )
        rule_fix_guidance = cls._clip_section(
            rule_details.get("how_to_fix", ""),
            "SonarQube 未返回修复建议，请基于规则说明进行最小化修复。",
            max_chars=cls.RULE_FIX_MAX_CHARS,
            max_lines=20,
            section_name="rule_fix_guidance",
            truncated_sections=truncated_sections,
        )
        compact_code_context = cls._clip_section(
            code_context,
            "未提供代码上下文，请先读取问题文件再动手。",
            max_chars=cls.CODE_CONTEXT_MAX_CHARS,
            max_lines=90,
            section_name="code_context",
            truncated_sections=truncated_sections,
        )
        compact_prefetched_context = cls._clip_section(
            prefetched_context_section,
            "",
            max_chars=cls.PREFETCHED_CONTEXT_MAX_CHARS,
            max_lines=70,
            section_name="prefetched_context_section",
            truncated_sections=truncated_sections,
        )
        compact_execution_mode = cls._clip_section(
            execution_mode_section,
            "",
            max_chars=cls.EXECUTION_MODE_MAX_CHARS,
            max_lines=28,
            section_name="execution_mode_section",
            truncated_sections=truncated_sections,
        )
        compact_working_memory = cls._clip_section(
            render_issue_working_memory(effective_working_memory),
            "",
            max_chars=cls.WORKING_MEMORY_MAX_CHARS,
            max_lines=40,
            section_name="working_memory_section",
            truncated_sections=truncated_sections,
        )
        compact_edit_contract = cls._clip_section(
            edit_contract_section,
            "",
            max_chars=1400,
            max_lines=60,
            section_name="edit_contract_section",
            truncated_sections=truncated_sections,
        )
        compact_repair_plan = cls._clip_section(
            repair_plan_section,
            "",
            max_chars=1400,
            max_lines=60,
            section_name="repair_plan_section",
            truncated_sections=truncated_sections,
        )

        workspace_relative_file_path = cls.render_workspace_relative_path(issue.file_path)
        file_path_candidates = "\n".join(
            f"- {candidate}"
            for candidate in cls.build_workspace_relative_candidates(issue.file_path, workspace_path)
        )
        durable_memory_section = ""
        retry_feedback_section = cls._render_retry_feedback_section(
            retry_feedback_text=retry_feedback_text,
            retry_context=retry_context,
            working_memory=effective_working_memory,
            truncated_sections=truncated_sections,
        )

        inline_sections = {
            "rule_description": "",
            "rule_fix_guidance": rule_fix_guidance,
            "quality_gate_section": "",
            "rule_guard_section": rule_guard_section,
            "edit_contract_section": "",
            "repair_plan_section": "",
            "prefetched_context_section": "",
            "tool_surface_section": tool_surface_section,
            "execution_mode_section": "",
        }
        externalized_sections = ()
        reference_document_path = ""

        prompt_template = SIMPLE_LOOP_USER_PROMPT_TEMPLATE

        draft_prompt = prompt_template.format(
            issue_key=issue.key,
            rule_id=issue.rule,
            rule_name=cls.normalize_prompt_text(rule_details.get("name", ""), "未提供"),
            message=issue.message,
            severity=issue.severity,
            file_path=workspace_relative_file_path,
            line=issue.start_line or issue.line,
            issue_location_guidance=cls.build_issue_location_guidance(issue),
            rule_description=inline_sections["rule_description"],
            rule_fix_guidance=inline_sections["rule_fix_guidance"],
            code_context=compact_code_context,
            working_memory_section=compact_working_memory,
            quality_gate_section=inline_sections["quality_gate_section"],
            rule_guard_section=inline_sections["rule_guard_section"],
            edit_contract_section=inline_sections["edit_contract_section"],
            repair_plan_section=inline_sections["repair_plan_section"],
            prefetched_context_section=inline_sections["prefetched_context_section"],
            tool_surface_section=inline_sections["tool_surface_section"],
            execution_mode_section=inline_sections["execution_mode_section"],
            scope_guidance=cls.normalize_prompt_text(
                scope_guidance,
                "- 只允许修改 SonarQube 指向的那一处问题，不要修改本文件其他同类位置。",
            ),
            build_command_section=build_command_section,
            retry_feedback_section=retry_feedback_section,
            durable_memory_section=durable_memory_section,
            file_path_candidates=file_path_candidates,
        ).strip()
        effective_working_memory, compaction_decision = maybe_compact_issue_prompt(
            issue_key=issue.key,
            rule_id=issue.rule,
            workspace_path=workspace_path,
            working_memory=effective_working_memory,
            retry_context=retry_context,
            draft_prompt=draft_prompt,
            model_hint=model_hint,
        )
        if compaction_decision.applied:
            compact_working_memory = cls._clip_section(
                render_issue_working_memory(effective_working_memory),
                "",
                max_chars=cls.WORKING_MEMORY_MAX_CHARS,
                max_lines=40,
                section_name="working_memory_section_compacted",
                truncated_sections=truncated_sections,
            )
            compact_boundary_section = cls._render_compaction_boundary_section(effective_working_memory)
            latest_retry_feedback_section = cls._render_retry_feedback_section(
                retry_feedback_text=compaction_decision.latest_failure_excerpt or retry_feedback_text,
                retry_context=retry_context,
                working_memory=effective_working_memory,
                truncated_sections=truncated_sections,
            )
            retry_feedback_section = "\n".join(
                item
                for item in (
                    compact_boundary_section,
                    latest_retry_feedback_section,
                )
                if str(item).strip()
            )

        prompt = prompt_template.format(
            issue_key=issue.key,
            rule_id=issue.rule,
            rule_name=cls.normalize_prompt_text(rule_details.get("name", ""), "未提供"),
            message=issue.message,
            severity=issue.severity,
            file_path=workspace_relative_file_path,
            line=issue.start_line or issue.line,
            issue_location_guidance=cls.build_issue_location_guidance(issue),
            rule_description=inline_sections["rule_description"],
            rule_fix_guidance=inline_sections["rule_fix_guidance"],
            code_context=compact_code_context,
            working_memory_section=compact_working_memory,
            quality_gate_section=inline_sections["quality_gate_section"],
            rule_guard_section=inline_sections["rule_guard_section"],
            edit_contract_section=inline_sections["edit_contract_section"],
            repair_plan_section=inline_sections["repair_plan_section"],
            prefetched_context_section=inline_sections["prefetched_context_section"],
            tool_surface_section=inline_sections["tool_surface_section"],
            execution_mode_section=inline_sections["execution_mode_section"],
            scope_guidance=cls.normalize_prompt_text(
                scope_guidance,
                "- 只允许修改 SonarQube 指向的那一处问题，不要修改本文件其他同类位置。",
            ),
            build_command_section=build_command_section,
            retry_feedback_section=retry_feedback_section,
            durable_memory_section=durable_memory_section,
            file_path_candidates=file_path_candidates,
        ).strip()

        if len(prompt) > cls.USER_PROMPT_TARGET_CHARS:
            compact_code_context = cls._clip_section(
                code_context,
                "未提供代码上下文，请先读取问题文件再动手。",
                max_chars=1600,
                max_lines=60,
                section_name="code_context_compact_retry",
                truncated_sections=truncated_sections,
            )
            prompt = prompt_template.format(
                issue_key=issue.key,
                rule_id=issue.rule,
                rule_name=cls.normalize_prompt_text(rule_details.get("name", ""), "未提供"),
                message=issue.message,
                severity=issue.severity,
                file_path=workspace_relative_file_path,
                line=issue.start_line or issue.line,
                issue_location_guidance=cls.build_issue_location_guidance(issue),
                rule_description=inline_sections["rule_description"],
                rule_fix_guidance=inline_sections["rule_fix_guidance"],
                code_context=compact_code_context,
                working_memory_section=compact_working_memory,
                quality_gate_section=inline_sections["quality_gate_section"],
                rule_guard_section=inline_sections["rule_guard_section"],
                edit_contract_section=inline_sections["edit_contract_section"],
                repair_plan_section=inline_sections["repair_plan_section"],
                prefetched_context_section=inline_sections["prefetched_context_section"],
                tool_surface_section=inline_sections["tool_surface_section"],
                execution_mode_section=inline_sections["execution_mode_section"],
                scope_guidance=cls.normalize_prompt_text(
                    scope_guidance,
                    "- 只允许修改 SonarQube 指向的那一处问题，不要修改本文件其他同类位置。",
                ),
                build_command_section=build_command_section,
                retry_feedback_section=retry_feedback_section,
                durable_memory_section=durable_memory_section,
                file_path_candidates=file_path_candidates,
            ).strip()

        return PromptBuildResult(
            prompt=prompt,
            target_chars=cls.USER_PROMPT_TARGET_CHARS,
            section_chars={
                "rule_description": len(inline_sections["rule_description"]),
                "rule_fix_guidance": len(inline_sections["rule_fix_guidance"]),
                "code_context": len(compact_code_context),
                "working_memory_section": len(compact_working_memory),
                "quality_gate_section": len(inline_sections["quality_gate_section"]),
                "rule_guard_section": len(inline_sections["rule_guard_section"]),
                "edit_contract_section": len(inline_sections["edit_contract_section"]),
                "repair_plan_section": len(inline_sections["repair_plan_section"]),
                "prefetched_context_section": len(inline_sections["prefetched_context_section"]),
                "tool_surface_section": len(inline_sections["tool_surface_section"]),
                "execution_mode_section": len(inline_sections["execution_mode_section"]),
                "build_command_section": len(build_command_section),
                "retry_feedback_section": len(retry_feedback_section),
                "durable_memory_section": len(durable_memory_section),
            },
            truncated_sections=tuple(dict.fromkeys(truncated_sections)),
            externalized_sections=externalized_sections,
            reference_document_path=reference_document_path,
            estimated_tokens=compaction_decision.estimated_tokens,
            token_budget=compaction_decision.token_budget,
            token_estimator=compaction_decision.estimator,
            compaction_applied=compaction_decision.applied,
            compaction_reason=compaction_decision.reason,
            issue_working_memory=effective_working_memory,
        )

    @classmethod
    def build_user_prompt(
        cls,
        issue: SonarIssue,
        code_context: str,
        quality_gate_text: str,
        scope_guidance: str,
        rule_details: dict[str, str],
        build_command: str,
        retry_feedback: str = "",
        retry_context: RetryContext | None = None,
        edit_contract_section: str = "",
        repair_plan_section: str = "",
        prefetched_context_section: str = "",
        execution_mode_section: str = "",
        workspace_path: Path | None = None,
        edit_contract: Any | None = None,
        visible_tool_names: tuple[str, ...] | list[str] = (),
        working_memory: IssueWorkingMemory | None = None,
        model_hint: str = "",
    ) -> str:
        """Backwards-compatible string-only user prompt builder."""

        return cls.build_user_prompt_result(
            issue,
            code_context,
            quality_gate_text,
            scope_guidance,
            rule_details,
            build_command,
            retry_feedback=retry_feedback,
            retry_context=retry_context,
            edit_contract_section=edit_contract_section,
            repair_plan_section=repair_plan_section,
            prefetched_context_section=prefetched_context_section,
            execution_mode_section=execution_mode_section,
            workspace_path=workspace_path,
            edit_contract=edit_contract,
            visible_tool_names=visible_tool_names,
            working_memory=working_memory,
            model_hint=model_hint,
        ).prompt
