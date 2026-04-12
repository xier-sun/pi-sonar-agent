"""Prompt composition helpers for single-issue fix attempts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_sonar_agent.agent.rule_policies import get_rule_policy
from pi_sonar_agent.core.resource_loader import ResourceLoader
from pi_sonar_agent.core.retry_context import RetryContext, render_retry_context
from pi_sonar_agent.core.tool_surface import render_controlled_bash_prompt_constraints

if TYPE_CHECKING:
    from pi_sonar_agent.agent.claude_agent import SonarIssue


SONAR_FIX_SYSTEM_PROMPT = """你是一个极其严格的 .NET/C# 架构师，专门负责修复 SonarQube 代码质量问题。

你的任务：
1. 仔细分析 SonarQube 报告的代码问题
2. 理解问题的根本原因
3. 应用最小化、精确的修复
4. 确保修复不会破坏现有功能

修复原则：
1. 最小改动：只修改必须修改的代码，不要做无关的格式化或重构
2. 安全第一：确保修复后代码能编译通过
3. 保持语义：不改变方法的业务逻辑
4. 使用工具：只使用当前运行时真正可用的工具（Read、Edit、MultiEdit、受控 Bash）；必要时先用 Bash 做无害搜索，再读取文件精确理解上下文，最后进行修复
5. 禁止污染：绝对不要使用 git add、git commit、git push 或任何自行提交/推送操作；如果使用 shell 工具，只允许无害的 bash 兼容命令
6. 构建由外层流程统一执行：你只负责代码修改，不要自行尝试运行构建或测试

重要约束：
- 绝对不要使用省略号(...)或注释掉代码
- 绝对不要删除整个方法或类
- 修复后如果可能，运行 build 验证
- 不要在修复阶段执行任何 git 提交、push 或通过 shell 直接改写源码；提交由外层流程统一处理
- 外层流程会在你完成修改后自动运行推荐构建命令并根据错误信息重试；你不需要自己运行构建工具
- 对复杂度类问题，默认优先保持现有公开签名和可见性不变，优先采用 private/local/sync-first 的重构

工作流程：
1. 使用 Read 工具读取有问题的源文件
2. 使用 Edit 或 MultiEdit 工具进行精确修复
3. 完成必要修改后直接结束，外层流程会自动进行构建验证
"""


SONAR_FIX_USER_PROMPT_TEMPLATE = """请修复以下 SonarQube 代码问题：

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

{quality_gate_section}
{rule_guard_section}
{edit_contract_section}
{repair_plan_section}
{prefetched_context_section}
{tool_surface_section}
{execution_mode_section}

【允许修改范围】
{scope_guidance}

【推荐构建命令】
{build_command}

{retry_feedback_section}

请按照以下步骤操作：
1. 读取源文件 {file_path} 确认问题
2. 分析问题的根本原因
3. 应用精确修复
4. 完成后直接结束；外层流程会自动使用上述推荐构建命令验证修复

注意：
- 当前只处理这个 Issue Key，不要扩展修复同文件、同方法里的其他 issue
- 只修复本问题，不要做无关改动
- 不要顺手修复本文件中其他位置的相同规则问题
- 严禁使用 git_add、git_commit、git_push 或任何自行提交/推送动作
- 不要调用 git 工具污染当前工作区基线；只允许直接修改文件
- 如果使用 shell 工具（工具名 Bash），只写 bash 兼容命令；允许搜索、查看、诊断、echo 等无害操作
- 严禁通过 shell 删除文件、创建文件、覆盖文件或直接改写源码
- 外层流程会负责 build/test/retry，不要自行尝试运行构建或测试
- 读取和编辑文件时只使用当前仓库内的相对路径，不要使用 `C:\\...` 这类绝对路径
- 当前优先直接操作的问题文件相对路径候选：
{file_path_candidates}
- 如果第一个路径不存在，优先尝试更短的候选相对路径；不要用 Bash 通过拼接仓库根目录反复试错
- 如果 Edit Contract 明确声明了额外传播目标文件，只能在这些相对路径内同步修改签名、接口声明、调用点和 `nameof(...)`
- 保持代码风格一致
- 确保修复后能编译通过
"""


class IssuePromptBuilder:
    """Compose the system and user prompts for single-issue fix attempts."""

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
    def build_rule_guard_section(rule_id: str) -> str:
        """Render rule-specific prompt guards when configured."""

        guards = get_rule_policy(rule_id).prompt_guards
        if not guards:
            return ""

        lines = ["【当前规则的额外约束】"]
        lines.extend(f"- {item}" for item in guards)
        return "\n".join(lines)

    @staticmethod
    def build_system_prompt(workspace_path: Path) -> str:
        """Compose the fix system prompt with optional workspace rules."""

        return ResourceLoader.compose_system_prompt(
            SONAR_FIX_SYSTEM_PROMPT,
            workspace_path,
        )

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
    ) -> str:
        """Build the issue-specific user prompt."""

        rendered_retry_context = render_retry_context(retry_context)
        retry_feedback_text = cls.normalize_prompt_text(
            rendered_retry_context or retry_feedback,
            "",
        ).strip()
        retry_feedback_section = ""
        if retry_feedback_text:
            retry_feedback_section = (
                "【上次尝试的构建失败信息】\n"
                f"{retry_feedback_text}\n\n"
                "请基于这些失败原因重新修复，避免再次引入相同的编译错误。"
            )

        quality_gate_section = ""
        quality_gate = cls.normalize_prompt_text(quality_gate_text, "").strip()
        if quality_gate:
            quality_gate_section = f"【C# 代码质量门禁】\n{quality_gate}"

        rule_guard_section = cls.build_rule_guard_section(issue.rule)
        tool_surface_section = ""
        bash_constraints = render_controlled_bash_prompt_constraints()
        if bash_constraints:
            tool_surface_section = "【工具策略】\n" + "\n".join(
                f"- {item}" for item in bash_constraints
            )

        workspace_relative_file_path = cls.render_workspace_relative_path(issue.file_path)
        file_path_candidates = "\n".join(
            f"- {candidate}"
            for candidate in cls.build_workspace_relative_candidates(issue.file_path, workspace_path)
        )

        return SONAR_FIX_USER_PROMPT_TEMPLATE.format(
            issue_key=issue.key,
            rule_id=issue.rule,
            rule_name=cls.normalize_prompt_text(rule_details.get("name", ""), "未提供"),
            message=issue.message,
            severity=issue.severity,
            file_path=workspace_relative_file_path,
            line=issue.start_line or issue.line,
            issue_location_guidance=cls.build_issue_location_guidance(issue),
            rule_description=cls.normalize_prompt_text(
                rule_details.get("description", ""),
                "SonarQube 未返回规则说明，请结合问题描述和代码上下文分析根因。",
            ),
            rule_fix_guidance=cls.normalize_prompt_text(
                rule_details.get("how_to_fix", ""),
                "SonarQube 未返回修复建议，请基于规则说明进行最小化修复。",
            ),
            code_context=code_context,
            quality_gate_section=quality_gate_section,
            rule_guard_section=rule_guard_section,
            edit_contract_section=cls.normalize_prompt_text(edit_contract_section, ""),
            repair_plan_section=cls.normalize_prompt_text(repair_plan_section, ""),
            prefetched_context_section=cls.normalize_prompt_text(prefetched_context_section, ""),
            tool_surface_section=cls.normalize_prompt_text(tool_surface_section, ""),
            execution_mode_section=cls.normalize_prompt_text(execution_mode_section, ""),
            scope_guidance=cls.normalize_prompt_text(
                scope_guidance,
                "- 只允许修改 SonarQube 指向的那一处问题，不要修改本文件其他同类位置。",
            ),
            build_command=cls.normalize_prompt_text(build_command, "dotnet build"),
            retry_feedback_section=retry_feedback_section,
            file_path_candidates=file_path_candidates,
        )
