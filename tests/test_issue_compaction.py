from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.memory.issue_compaction import maybe_compact_issue_prompt
from pi_sonar_agent.core.memory.issue_working_memory import create_initial_issue_working_memory
from pi_sonar_agent.core.retry_context import RetryContext, RetryHistoryItem


def test_issue_compaction_writes_compact_summary_and_updates_working_memory(tmp_path: Path) -> None:
    issue = SonarIssue(
        key="ISSUE-COMPACT",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=41,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    working_memory = create_initial_issue_working_memory(issue)
    retry_context = RetryContext(
        source_attempt_number=5,
        issue_rule_id="csharpsquid:S3776",
        failure_kind="build",
        summary="Issue changes failed local build verification",
        primary_failure_fingerprint="helper_extraction_type_break",
        failure_fingerprints=("helper_extraction_type_break",),
        retry_history_total_attempts=5,
        retry_history_items=(
            RetryHistoryItem(
                attempt_number=1,
                failure_kind="build",
                primary_failure_fingerprint="helper_extraction_type_break",
                headline="CS1503 caused by helper extraction",
            ),
            RetryHistoryItem(
                attempt_number=2,
                failure_kind="reviewer",
                primary_failure_fingerprint="helper_extract_disabled",
                headline="Private helper was rejected by reviewer",
            ),
        ),
    )

    updated_memory, decision = maybe_compact_issue_prompt(
        issue_key=issue.key,
        rule_id=issue.rule,
        workspace_path=tmp_path,
        working_memory=working_memory,
        retry_context=retry_context,
        draft_prompt="x" * 400,
        model_hint="MiniMax-M2.7",
    )

    assert decision.applied is True
    assert decision.reason == "retry_depth"
    assert updated_memory is not None
    assert updated_memory.compaction_generation == 1
    assert updated_memory.compact_boundary_note
    assert updated_memory.compacted_history_summary
    assert updated_memory.compact_summary_path
    assert "1. 当前任务目标" in decision.compact_brief
    assert "2. 已完成的关键动作" in decision.compact_brief
    assert "3. 已修改或重点查看过的文件" in decision.compact_brief
    assert "4. 关键决定与约束" in decision.compact_brief
    assert "5. 下一步应该做什么" in decision.compact_brief
    compact_summary_path = tmp_path / updated_memory.compact_summary_path
    assert compact_summary_path.exists()
    compact_summary = compact_summary_path.read_text(encoding="utf-8")
    assert "## Authoritative Compact Brief" in compact_summary
    assert "1. 当前任务目标" in compact_summary
