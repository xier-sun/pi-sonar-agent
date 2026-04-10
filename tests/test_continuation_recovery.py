from __future__ import annotations

from pi_sonar_agent.core.continuation_recovery import ContinuationRecovery
from pi_sonar_agent.core.events import AttemptRuntimeEvent, AttemptRuntimeEventKind


def test_continuation_recovery_builds_compact_resume_prompt() -> None:
    events = (
        AttemptRuntimeEvent(
            kind=AttemptRuntimeEventKind.TOOL_CALLED,
            sequence=1,
            stage="tool:Read",
            payload={
                "tool_name": "Read",
                "tool_payload": {"file_path": r"C:\GIT.NEWARE.WORK\BI\src\Foo.cs"},
                "tool_preview": '{"file_path":"C:\\\\GIT.NEWARE.WORK\\\\BI\\\\src\\\\Foo.cs"}',
                "read_preview": "   1 | class Foo\n   2 | {",
            },
        ),
        AttemptRuntimeEvent(
            kind=AttemptRuntimeEventKind.ASSISTANT_TEXT_DELTA,
            sequence=2,
            stage="assistant_text",
            payload={"preview": "先确认问题所在，然后删除未使用变量。"},
        ),
    )

    context = ContinuationRecovery.build_context(
        events=events,
        timeout_stage="post_read_stall",
        continuation_index=1,
        last_progress_stage="tool:Read",
        last_tool_name="Read",
        changed_files=(),
    )
    prompt = ContinuationRecovery.build_prompt("base prompt", context)

    assert context.saw_absolute_workspace_path is True
    assert "【继续上一轮修复，不要从头分析】" in prompt
    assert "post_read_stall" in prompt
    assert "绝对路径" in prompt
    assert "先确认问题所在" in prompt
    assert "class Foo" in prompt
