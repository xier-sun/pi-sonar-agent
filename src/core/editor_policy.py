"""Patch/edit policy derived from a single issue edit contract."""

from __future__ import annotations

from collections.abc import Iterable

from pi_sonar_agent.core.issue_contract import EditContract


class EditorPolicy:
    """Derive prompt/tool restrictions from an edit contract."""

    @staticmethod
    def allowed_tool_names(default_allowed_tools: Iterable[str], edit_contract: EditContract) -> tuple[str, ...]:
        """Return the SDK allowlist for the current guardrail mode."""

        tools = tuple(dict.fromkeys(str(name) for name in default_allowed_tools if str(name).strip()))
        if not edit_contract.patch_only:
            return tools
        if edit_contract.allow_file_creation:
            return tools
        return tuple(tool for tool in tools if tool != "Write")

    @staticmethod
    def render_prompt_constraints(edit_contract: EditContract) -> str:
        """Render extra prompt constraints for patch-only issue fixing."""

        lines = [
            "【编辑策略】",
            "- 优先使用精确 patch 或局部编辑，不要整文件重写。",
            "- 只允许修改 Edit Contract 声明的目标文件和目标符号。",
            "- 如果发现同文件相邻技术债，不要顺手修，记录到 follow-up。",
        ]
        if edit_contract.allow_file_creation and edit_contract.allowed_new_file_roots:
            lines.append(
                "- 当前 attempt 允许新增文件，但仅限以下目录："
                + ", ".join(edit_contract.allowed_new_file_roots)
                + "。"
            )
            lines.append("- 新建文件优先使用 Write，且只允许创建当前还不存在的新文件。")
            lines.append("- 新建文件优先用于当前规则明确需要的新增类型或参数对象，不要借机扩散重构范围。")
        if edit_contract.patch_only:
            lines.append("- 当前 attempt 默认采用 patch-only 策略；已有文件继续使用 Edit/MultiEdit，只有新文件创建才允许用 Write。")
        return "\n".join(lines)
