"""Patch/edit policy derived from a single issue edit contract."""

from __future__ import annotations

from collections.abc import Iterable

from pi_sonar_agent.core.boundary_capabilities import HELPER_EXTRACT_CAPABILITY
from pi_sonar_agent.core.issue_contract import EditContract


class EditorPolicy:
    """Derive prompt/tool restrictions from an edit contract."""

    @staticmethod
    def allowed_tool_names(default_allowed_tools: Iterable[str], edit_contract: EditContract) -> tuple[str, ...]:
        """Return the SDK allowlist for the current guardrail mode."""

        tools = tuple(dict.fromkeys(str(name) for name in default_allowed_tools if str(name).strip()))
        return tools

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
            lines.append("- 允许使用 Write 创建当前还不存在的新文件，但只能创建到声明目录内。")
            lines.append("- 新建文件优先用于当前规则明确需要的新增类型或参数对象，不要借机扩散重构范围。")
        if edit_contract.patch_only:
            lines.append("- 当前 attempt 默认采用 patch-only 策略；允许使用 Edit/MultiEdit/Write 修改已有文件。")
            if not edit_contract.allow_file_creation:
                lines.append("- Write 只能用于重写已有文件，不能借此创建新文件。")
        if HELPER_EXTRACT_CAPABILITY not in set(edit_contract.allowed_capabilities):
            lines.append("- 当前 attempt 禁止新增 private helper / private method；必须在现有方法体内收口逻辑。")
        return "\n".join(lines)
