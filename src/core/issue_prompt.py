"""Prompt composition helpers for single-issue fix attempts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_sonar_agent.agent.rule_policies import get_rule_policy
from pi_sonar_agent.core.registry import BUILD_TOOL_NAMES
from pi_sonar_agent.core.resource_loader import ResourceLoader
from pi_sonar_agent.core.retry_context import RetryContext, render_retry_context
from pi_sonar_agent.core.state import serialize_state
from pi_sonar_agent.core.tool_surface import (
    render_controlled_bash_prompt_constraints,
    render_visible_tool_summary,
)

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue


SONAR_FIX_SYSTEM_PROMPT = """你是一个严格的 .NET/C# 资深工程师，专门修复 SonarQube 问题。

目标：
1. 准确理解 Sonar issue 的根因
2. 只做最小且可编译的修复
3. 保持业务语义和公开行为稳定

硬约束：
- 只使用当前运行时真正可见的工具
- 不要执行 git add / git commit / git push
- 不要通过 shell 直接改写已有源码
- build/test/retry 由外层流程统一执行
- 复杂度类问题默认优先保持公开签名不变，优先 private/local/sync-first 重构
"""


SONAR_FIX_USER_PROMPT_TEMPLATE = """请修复以下 SonarQube 代码问题，只处理当前 issue。

【问题详情】
- Issue Key: {issue_key}
- 规则ID: {rule_id}
- 规则名称: {rule_name}
- 问题描述: {message}
- 严重程度: {severity}
- 文件路径: {file_path}
- 报错行号: {line}

【SonarQube 精确定位】
{issue_location_guidance}

【SonarQube 规则说明（问题原因/风险）】
{rule_description}

【SonarQube 修复建议】
{rule_fix_guidance}

【代码上下文】（包含问题行及前后代码）
{code_context}

{prefetched_context_section}
{edit_contract_section}
{repair_plan_section}
{quality_gate_section}
{rule_guard_section}
{tool_surface_section}
{execution_mode_section}

【允许修改范围】
{scope_guidance}

{build_command_section}

{retry_feedback_section}

【执行要求】
- 只修当前 Issue Key，不扩展到同文件其他 issue
- 不做无关重构、批量格式化或路径试错
- 不要顺手修复本文件中其他位置的相同规则问题
- 读取和编辑文件时只使用仓库内相对路径
- 当前优先直接操作的问题文件相对路径候选：
{file_path_candidates}
- 如果第一个路径不存在，优先尝试更短候选路径；不要用 Bash 通过拼接仓库根目录反复试错
- 如果 Edit Contract 明确声明了额外传播目标文件，只能在这些相对路径内同步修改签名、接口声明、调用点和 `nameof(...)`
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
    ) -> str:
        """Render rule-specific prompt guards when configured."""

        policy = get_rule_policy(rule_id)
        guards = list(policy.prompt_guards)
        if retry_context is not None:
            guards.extend(policy.retry_prompt_guards)
        if not guards:
            return ""

        lines = ["【当前规则的额外约束】"]
        lines.extend(f"- {item}" for item in guards)
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
    def build_system_prompt_result(cls, workspace_path: Path) -> PromptBuildResult:
        """Compose the fix system prompt with optional workspace rules."""

        prompt = ResourceLoader.compose_system_prompt(
            SONAR_FIX_SYSTEM_PROMPT,
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
        )

    @classmethod
    def build_system_prompt(cls, workspace_path: Path) -> str:
        """Backwards-compatible string-only system prompt builder."""

        return cls.build_system_prompt_result(workspace_path).prompt

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
    ) -> str:
        normalized_build_command = cls.normalize_prompt_text(build_command, "dotnet build")
        normalized_visible_tools = cls._normalize_visible_tool_names(visible_tool_names)
        if normalized_visible_tools and not cls._has_visible_build_tool(normalized_visible_tools):
            return (
                "【构建执行】\n"
                "构建与验证由外层流程统一执行；本轮不要在 Bash 中执行 "
                "dotnet restore/build/test、msbuild 或 nuget restore。"
            )
        return f"【推荐构建命令】\n{normalized_build_command}"

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
    ) -> PromptBuildResult:
        """Build the issue-specific user prompt and budget metadata."""

        truncated_sections: list[str] = []

        rendered_retry_context = render_retry_context(retry_context)
        retry_feedback_text = cls.normalize_prompt_text(
            rendered_retry_context or retry_feedback,
            "",
        ).strip()
        retry_feedback_section = ""
        if retry_feedback_text:
            retry_feedback_section = (
                "【上次尝试的构建失败信息】\n"
                f"{ResourceLoader.truncate_for_prompt(retry_feedback_text, 1800, max_lines=80)}\n\n"
                "请基于这些失败原因重新修复，避免再次引入相同的编译错误。"
            )
            if retry_feedback_text not in retry_feedback_section:
                truncated_sections.append("retry_feedback_section")

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

        rule_guard_section = cls.build_rule_guard_section(issue.rule, retry_context=retry_context)
        tool_surface_section = ""
        visible_tool_list = cls._normalize_visible_tool_names(visible_tool_names)
        allow_build_commands = not visible_tool_list or cls._has_visible_build_tool(visible_tool_list)
        build_command_section = cls._build_build_command_section(build_command, visible_tool_list)
        tool_surface_lines: list[str] = []
        if visible_tool_list:
            tool_surface_lines.append(
                "当前 attempt 可用工具: " + render_visible_tool_summary(visible_tool_list)
            )
        bash_constraints = render_controlled_bash_prompt_constraints(
            allow_file_creation=bool(getattr(edit_contract, "allow_file_creation", False)),
            allow_build_commands=allow_build_commands,
            allowed_new_file_roots=getattr(edit_contract, "allowed_new_file_roots", ()) or (),
        )
        if bash_constraints:
            tool_surface_lines.extend(bash_constraints)
        if tool_surface_lines:
            tool_surface_section = "【工具策略】\n" + "\n".join(
                f"- {item}" for item in tool_surface_lines
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

        inline_sections, externalized_sections, reference_document_path = (
            cls._maybe_externalize_reference_sections(
                workspace_path=workspace_path,
                rule_description=rule_description,
                rule_fix_guidance=rule_fix_guidance,
                quality_gate_section=quality_gate_section,
                rule_guard_section=rule_guard_section,
                edit_contract_section=compact_edit_contract,
                repair_plan_section=compact_repair_plan,
                prefetched_context_section=compact_prefetched_context,
                tool_surface_section=tool_surface_section,
                execution_mode_section=compact_execution_mode,
                code_context=compact_code_context,
                retry_feedback_section=retry_feedback_section,
            )
        )

        prompt = SONAR_FIX_USER_PROMPT_TEMPLATE.format(
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
            prompt = SONAR_FIX_USER_PROMPT_TEMPLATE.format(
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
                file_path_candidates=file_path_candidates,
            ).strip()

        return PromptBuildResult(
            prompt=prompt,
            target_chars=cls.USER_PROMPT_TARGET_CHARS,
            section_chars={
                "rule_description": len(inline_sections["rule_description"]),
                "rule_fix_guidance": len(inline_sections["rule_fix_guidance"]),
                "code_context": len(compact_code_context),
                "quality_gate_section": len(inline_sections["quality_gate_section"]),
                "rule_guard_section": len(inline_sections["rule_guard_section"]),
                "edit_contract_section": len(inline_sections["edit_contract_section"]),
                "repair_plan_section": len(inline_sections["repair_plan_section"]),
                "prefetched_context_section": len(inline_sections["prefetched_context_section"]),
                "tool_surface_section": len(inline_sections["tool_surface_section"]),
                "execution_mode_section": len(inline_sections["execution_mode_section"]),
                "build_command_section": len(build_command_section),
                "retry_feedback_section": len(retry_feedback_section),
            },
            truncated_sections=tuple(dict.fromkeys(truncated_sections)),
            externalized_sections=externalized_sections,
            reference_document_path=reference_document_path,
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
        ).prompt
