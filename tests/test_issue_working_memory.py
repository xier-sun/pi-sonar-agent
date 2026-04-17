from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.memory.evidence_state import EvidenceState
from pi_sonar_agent.core.memory.child_agent_memory import (
    append_child_agent_memory_turn,
    create_initial_child_agent_memory,
)
from pi_sonar_agent.core.memory.issue_working_memory import (
    IssueWorkingMemory,
    create_initial_issue_working_memory,
)
from pi_sonar_agent.core.memory.memory_schema import MemorySchemaError
from pi_sonar_agent.core.memory.working_memory_store import WorkingMemoryStore


def test_working_memory_store_round_trip(tmp_path: Path) -> None:
    issue = SonarIssue(
        key="ISSUE-1",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=41,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    memory = create_initial_issue_working_memory(issue)
    store = WorkingMemoryStore(tmp_path)

    path = store.save(memory)
    loaded = store.load(issue.key)

    assert path.exists()
    assert loaded == memory
    assert loaded is not None
    assert loaded.authoritative_workspace_state == "issue_baseline"
    assert "修复 csharpsquid:S3776" in loaded.current_goal


def test_working_memory_store_rejects_invalid_schema(tmp_path: Path) -> None:
    store = WorkingMemoryStore(tmp_path)
    path = store.working_memory_path("ISSUE-INVALID")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "rule_id": "csharpsquid:S3776"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(MemorySchemaError):
        store.load("ISSUE-INVALID")


def test_issue_working_memory_from_dict_requires_supported_version() -> None:
    with pytest.raises(MemorySchemaError):
        IssueWorkingMemory.from_dict(
            {
                "version": 999,
                "issue_key": "ISSUE-1",
            }
        )


def test_working_memory_store_evidence_round_trip(tmp_path: Path) -> None:
    store = WorkingMemoryStore(tmp_path)
    evidence = EvidenceState(
        version=1,
        evidence_id="compiler:1:1:CS0103:src/Foo.cs:41",
        source_type="compiler_error",
        summary="CS0103 at src/Foo.cs:41 - name not found",
        related_files=("src/Foo.cs",),
        status="stale",
        content_fingerprint="abc123",
        diff_fingerprint="def456",
        superseded_by="restored_issue_baseline",
    )

    path = store.save_evidence("ISSUE-EVIDENCE", (evidence,))
    loaded = store.load_evidence("ISSUE-EVIDENCE")

    assert path.exists()
    assert loaded == (evidence,)


def test_working_memory_store_child_memory_round_trip(tmp_path: Path) -> None:
    store = WorkingMemoryStore(tmp_path)
    memory = create_initial_child_agent_memory(
        issue_key="ISSUE-CHILD",
        role="review",
        focus="审查 patch 是否满足代码门禁。",
    )
    memory = append_child_agent_memory_turn(
        memory,
        attempt_number=1,
        decision="approve",
        summary="当前 patch 值得进入编译阶段。",
        constraints=("继续保持最小改动。",),
        workspace_state="attempt_patch",
        next_action="等待主裁决。",
    )

    path = store.save_child_memory(memory)
    loaded = store.load_child_memory("ISSUE-CHILD", "review")

    assert path.exists()
    assert loaded == memory
    assert loaded is not None
    assert loaded.latest_decision == "approve"


def test_child_memory_compacts_older_turns() -> None:
    memory = create_initial_child_agent_memory(
        issue_key="ISSUE-CHILD-COMPACT",
        role="fix",
        focus="完成最小修复。",
    )
    for attempt in range(1, 6):
        memory = append_child_agent_memory_turn(
            memory,
            attempt_number=attempt,
            decision="retry" if attempt < 5 else "patched",
            summary=f"attempt {attempt} summary",
            constraints=("保持最小改动。",),
            workspace_state="issue_baseline" if attempt < 5 else "attempt_patch",
            next_action="继续修复。",
        )

    assert len(memory.turns) <= 3
    assert memory.compaction_generation >= 1
    assert "attempt 1" in memory.compacted_history_summary
