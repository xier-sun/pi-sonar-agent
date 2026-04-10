from __future__ import annotations

import json

from pi_sonar_agent.core.lessons_store import LessonsStore
from pi_sonar_agent.core.retry_context import (
    BoundaryFailureContext,
    QualityGateFailureContext,
    QualityGateViolationContext,
    RetryContext,
    ScopeViolationContext,
)


def test_lessons_store_records_patterns_and_returns_planner_lessons(tmp_path) -> None:
    store = LessonsStore(tmp_path / "lessons")
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="quality_gate",
        summary="Quality gate rejected the patch.",
        guidance=("先修复质量门禁问题。",),
        boundary_failure=BoundaryFailureContext(
            code="scope_symbol_anchor_miss",
            summary="Patch missed the declaration anchor range.",
        ),
        scope_violation=ScopeViolationContext(
            raw_output="Issue changes exceeded the allowed Sonar edit scope.",
            allowed_lines="2224-2224",
            changed_lines_outside_scope="2223",
            constraints=("- 只保留当前 issue 所需改动。",),
        ),
        quality_gate_failure=QualityGateFailureContext(
            summary="C# quality gate rejected the patch.",
            violations=(
                QualityGateViolationContext(
                    rule_id="async_signature",
                    title="异步签名规范",
                    message="Async method must end with Async.",
                    retry_hint="将异步方法名改为 Async 结尾。",
                ),
            ),
        ),
    )

    store.record_failure(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-1",
        issue_rule_id="csharpsquid:S3776",
        retry_context=retry_context,
        scope_mode="method",
        guardrail_mode="contract_review",
        quality_gate_rule_ids=("async_signature",),
    )

    assert store.quality_gate_lessons_path.exists()
    assert store.boundary_patterns_path.exists()
    assert store.rule_patterns_path.exists()

    quality_lines = store.quality_gate_lessons_path.read_text(encoding="utf-8").splitlines()
    assert len(quality_lines) == 1
    assert json.loads(quality_lines[0])["quality_gate_rule_ids"] == ["async_signature"]

    planner_lessons = store.load_planner_lessons(
        issue_rule_id="csharpsquid:S3776",
        failure_kind="quality_gate",
        scope_mode="method",
        guardrail_mode="contract_review",
        boundary_failure_code="scope_symbol_anchor_miss",
        quality_gate_rule_ids=("async_signature",),
    )

    assert planner_lessons
    assert any(lesson.source == "quality_gate_lesson" for lesson in planner_lessons)
    assert any("Async method must end with Async" in lesson.summary for lesson in planner_lessons)
    assert any(lesson.boundary_failure_code == "scope_symbol_anchor_miss" for lesson in planner_lessons)
