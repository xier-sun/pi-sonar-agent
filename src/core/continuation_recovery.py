"""Helpers for same-context continuation after follow-up response stalls."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pi_sonar_agent.core.events import AttemptRuntimeEvent, AttemptRuntimeEventKind

_ABSOLUTE_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class ContinuationContext:
    """Compact resume facts for continuing a stalled attempt."""

    timeout_stage: str
    continuation_index: int
    last_progress_stage: str = ""
    last_tool_name: str = ""
    changed_files: tuple[str, ...] = ()
    recent_tool_summaries: tuple[str, ...] = ()
    recent_assistant_summaries: tuple[str, ...] = ()
    recent_read_previews: tuple[str, ...] = ()
    saw_absolute_workspace_path: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "timeout_stage": self.timeout_stage,
            "continuation_index": self.continuation_index,
            "last_progress_stage": self.last_progress_stage,
            "last_tool_name": self.last_tool_name,
            "changed_files": list(self.changed_files),
            "recent_tool_summaries": list(self.recent_tool_summaries),
            "recent_assistant_summaries": list(self.recent_assistant_summaries),
            "recent_read_previews": list(self.recent_read_previews),
            "saw_absolute_workspace_path": self.saw_absolute_workspace_path,
        }


class ContinuationRecovery:
    """Build compact continuation prompts from runtime events."""

    @staticmethod
    def _build_prompt_sections(
        context: ContinuationContext,
        *,
        title: str,
        first_fact: str,
        action_guidance: tuple[str, ...],
    ) -> list[str]:
        sections = [
            title,
            f"- 这是同一 issue 的 continuation 第 {context.continuation_index} 次。",
            first_fact,
        ]
        if context.last_progress_stage:
            sections.append(f"- 上一轮最后进度阶段: {context.last_progress_stage}。")
        if context.last_tool_name:
            sections.append(f"- 上一轮最后工具: {context.last_tool_name}。")
        if context.changed_files:
            sections.append(
                "- 当前工作区已检测到变更文件: "
                + ", ".join(context.changed_files)
                + "。"
            )
        sections.extend(action_guidance)
        sections.append("- 只使用仓库相对路径，不要使用 C:\\ 或其他绝对路径。")
        if context.saw_absolute_workspace_path:
            sections.append(
                "- 上一轮出现了绝对路径读文件失败；这次只允许使用仓库相对路径继续。"
            )
        if context.recent_tool_summaries:
            sections.append("最近工具轨迹:")
            sections.extend(f"- {item}" for item in context.recent_tool_summaries)
        if context.recent_assistant_summaries:
            sections.append("最近模型输出摘要:")
            sections.extend(f"- {item}" for item in context.recent_assistant_summaries)
        if context.recent_read_previews:
            sections.append("最近已读取的关键代码片段:")
            sections.extend(context.recent_read_previews)
        return sections

    @staticmethod
    def build_context(
        *,
        events: tuple[AttemptRuntimeEvent, ...] | list[AttemptRuntimeEvent],
        timeout_stage: str,
        continuation_index: int,
        last_progress_stage: str = "",
        last_tool_name: str = "",
        changed_files: tuple[str, ...] = (),
    ) -> ContinuationContext:
        recent_tool_summaries: list[str] = []
        recent_assistant_summaries: list[str] = []
        recent_read_previews: list[str] = []
        saw_absolute_workspace_path = False

        for event in reversed(tuple(events)):
            if (
                event.kind == AttemptRuntimeEventKind.TOOL_CALLED
                and len(recent_tool_summaries) < 3
            ):
                payload = dict(event.payload or {})
                tool_name = str(payload.get("tool_name", "")).strip() or event.stage
                preview = ContinuationRecovery._normalize_preview(
                    str(payload.get("tool_preview", "")).strip(),
                    max_chars=160,
                )
                file_path = str(
                    (payload.get("tool_payload", {}) or {}).get("file_path", "")
                ).strip()
                if file_path and _ABSOLUTE_WINDOWS_PATH_RE.match(file_path):
                    saw_absolute_workspace_path = True
                summary = tool_name
                if file_path:
                    summary += f" file={file_path}"
                if preview:
                    summary += f" | input={preview}"
                recent_tool_summaries.append(summary)

                read_preview = ContinuationRecovery._normalize_preview(
                    str(payload.get("read_preview", "")).strip(),
                    max_chars=320,
                )
                if read_preview and len(recent_read_previews) < 2:
                    recent_read_previews.append(read_preview)
            elif event.kind in {
                AttemptRuntimeEventKind.ASSISTANT_TEXT_DELTA,
                AttemptRuntimeEventKind.SDK_TRACE,
            } and len(recent_assistant_summaries) < 2:
                preview = ContinuationRecovery._normalize_preview(
                    str((event.payload or {}).get("preview", "")).strip(),
                    max_chars=180,
                )
                if preview:
                    recent_assistant_summaries.append(preview)

            if (
                len(recent_tool_summaries) >= 3
                and len(recent_assistant_summaries) >= 2
                and len(recent_read_previews) >= 2
            ):
                break

        return ContinuationContext(
            timeout_stage=str(timeout_stage or "").strip(),
            continuation_index=continuation_index,
            last_progress_stage=str(last_progress_stage or "").strip(),
            last_tool_name=str(last_tool_name or "").strip(),
            changed_files=tuple(str(item).strip() for item in changed_files if str(item).strip()),
            recent_tool_summaries=tuple(reversed(recent_tool_summaries)),
            recent_assistant_summaries=tuple(reversed(recent_assistant_summaries)),
            recent_read_previews=tuple(reversed(recent_read_previews)),
            saw_absolute_workspace_path=saw_absolute_workspace_path,
        )

    @staticmethod
    def build_prompt(base_user_prompt: str, context: ContinuationContext) -> str:
        """Append a compact continuation section to the original user prompt."""

        sections = ContinuationRecovery._build_prompt_sections(
            context,
            title="【继续上一轮修复，不要从头分析】",
            first_fact=f"- 上一轮超时阶段: {context.timeout_stage or 'follow_up_response_timeout'}。",
            action_guidance=(
                "- 不要重头分析，不要重复长篇解释，直接基于当前工作区状态继续。",
                "- 如果修复其实已经完成，请直接结束，不要再补冗长总结。",
            ),
        )

        return f"{base_user_prompt.rstrip()}\n\n" + "\n".join(sections).strip() + "\n"

    @staticmethod
    def build_no_change_prompt(base_user_prompt: str, context: ContinuationContext) -> str:
        """Append a compact continuation section for no-change retries."""

        sections = ContinuationRecovery._build_prompt_sections(
            context,
            title="【继续上一轮修复：你还没有真正修改代码】",
            first_fact="- 上一轮没有产生任何代码修改，当前必须进入编辑阶段。",
            action_guidance=(
                "- 不要再重复读取相同文件或继续做大范围搜索。",
                "- 请直接基于你刚才已经拿到的上下文，使用 Edit 或 MultiEdit 落盘修改。",
                "- 如果你确认当前约束下无法安全修复，请直接明确说明原因，不要再停留在分析状态。",
            ),
        )
        return f"{base_user_prompt.rstrip()}\n\n" + "\n".join(sections).strip() + "\n"

    @staticmethod
    def _normalize_preview(value: str, *, max_chars: int) -> str:
        text = str(value or "").replace("\r\n", "\n").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
