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
        if edit_contract.patch_only:
            lines.append("- 当前 attempt 默认采用 patch-only 策略；除非绝对必要，不要使用 whole-file write。")
        return "\n".join(lines)
