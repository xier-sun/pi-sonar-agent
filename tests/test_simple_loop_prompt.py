from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent
from pi_sonar_agent.core.boundary_capabilities import METHOD_REWRITE_CAPABILITY
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.issue_prompt import IssuePromptBuilder
from pi_sonar_agent.core.lessons_store import PlannerLesson
from pi_sonar_agent.core.memory.issue_working_memory import IssueWorkingMemory
from pi_sonar_agent.core.scope_guard import IssueEditScope
from pi_sonar_agent.core.retry_context import RetryContext, RetryHistoryItem


def test_simple_loop_execution_mode_section_includes_catalog_guidance() -> None:
    section = ClaudeFixAgent._build_execution_mode_section(
        EditContract(
            issue_key="ISSUE-S3776",
            rule_id="csharpsquid:S3776",
            guardrail_mode="scope_only",
            target_files=("src/Foo.cs",),
            execution_mode="simple_loop",
        )
    )

    assert "headless simple-loop execution" in section
    assert "自检:" not in section
    assert "不要展开长篇推理" in section


def test_simple_loop_rule_guards_add_hard_constraints_after_type_shape_retry() -> None:
    section = IssuePromptBuilder.build_rule_guard_section(
        "csharpsquid:S3776",
        retry_context=RetryContext(
            source_attempt_number=2,
            failure_kind="build",
            failure_fingerprints=("helper_extraction_type_break", "nullable_type_mismatch"),
            primary_failure_fingerprint="helper_extraction_type_break",
        ),
        execution_mode="simple_loop",
    )

    assert "禁止新增 helper/private 方法" in section
    assert "禁止使用 dynamic" in section
    assert "禁止让匿名类型或 nullable-heavy 状态跨方法边界流动" in section
    assert "IQueryable<T>" not in section


def test_simple_loop_rule_guards_include_refactor_safety_constraints_without_retry() -> None:
    section = IssuePromptBuilder.build_rule_guard_section(
        "csharpsquid:S3358",
        execution_mode="simple_loop",
    )

    assert section == ""


def test_simple_loop_rule_guards_include_s107_parameter_threshold() -> None:
    section = IssuePromptBuilder.build_rule_guard_section(
        "csharpsquid:S107",
        execution_mode="simple_loop",
    )

    assert "S107 只有在目标方法最终签名参数总数降到 <=7 时才算修复完成" in section
    assert "重新读取目标方法声明并重数顶层参数" in section
    assert "方向正确但仍 >7 个参数" in section
    assert ".pi-sonar-agent-runtime/s107-fix-guide.md" in section
    assert "先读取" in section


def test_scope_guidance_does_not_suggest_helper_extract_when_capability_is_disabled() -> None:
    issue = type("Issue", (), {"rule": "csharpsquid:S3776"})()
    scope = IssueEditScope(
        start_line=41,
        end_line=171,
        validation_start_line=41,
        validation_end_line=250,
        mode="method",
    )
    edit_contract = EditContract(
        issue_key="ISSUE-HELPER-GUARD",
        rule_id="csharpsquid:S3776",
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        allowed_capabilities=(METHOD_REWRITE_CAPABILITY,),
        execution_mode="simple_loop",
    )

    guidance = ClaudeFixAgent._build_scope_guidance(issue, scope, edit_contract)

    assert "如果必须提取 private 辅助方法" not in guidance
    assert "当前 retry 已禁用 helper/private method 提取" in guidance


def test_user_prompt_includes_working_memory_before_retry_feedback() -> None:
    prompt = IssuePromptBuilder.build_user_prompt(
        issue=type(
            "Issue",
            (),
            {
                "key": "ISSUE-1",
                "rule": "csharpsquid:S3776",
                "message": "认知复杂度过高",
                "severity": "MAJOR",
                "file_path": "/src/Foo.cs",
                "line": 42,
                "start_line": 42,
                "end_line": 42,
                "text_range": {"startLine": 42, "endLine": 42},
                "flows": (),
            },
        )(),
        code_context="  42 | if (a) { if (b) { Do(); } }",
        quality_gate_text="",
        scope_guidance="- 只改当前 issue",
        rule_details={"name": "复杂度", "description": "desc", "how_to_fix": "fix"},
        build_command="dotnet build",
        retry_feedback="上一轮尝试失败：本地构建未通过。",
        retry_context=RetryContext(
            source_attempt_number=2,
            failure_kind="build",
            summary="Issue changes failed local build verification",
        ),
        working_memory=IssueWorkingMemory(
            version=1,
            issue_key="ISSUE-1",
            rule_id="csharpsquid:S3776",
            current_goal="修复当前 issue",
            authoritative_workspace_state="issue_baseline",
            latest_retryable_failure="上一轮构建失败",
            next_action="先读取当前文件，再换一种修法。",
            last_updated_at="2026-04-17T00:00:00+00:00",
        ),
    )

    assert "【当前工作记忆】" in prompt
    assert "当前工作区状态: issue_baseline" in prompt
    assert "【上次尝试的失败信息】" in prompt
    assert "=== DYNAMIC_BOUNDARY ===" in prompt
    assert "<system-reminder>" in prompt
    assert prompt.index("【当前工作记忆】") < prompt.index("【上次尝试的失败信息】")


def test_user_prompt_downgrades_retry_feedback_to_historical_after_rollback() -> None:
    prompt = IssuePromptBuilder.build_user_prompt(
        issue=type(
            "Issue",
            (),
            {
                "key": "ISSUE-2",
                "rule": "csharpsquid:S3776",
                "message": "认知复杂度过高",
                "severity": "MAJOR",
                "file_path": "/src/Foo.cs",
                "line": 42,
                "start_line": 42,
                "end_line": 42,
                "text_range": {"startLine": 42, "endLine": 42},
                "flows": (),
            },
        )(),
        code_context="  42 | if (a) { if (b) { Do(); } }",
        quality_gate_text="",
        scope_guidance="- 只改当前 issue",
        rule_details={"name": "复杂度", "description": "desc", "how_to_fix": "fix"},
        build_command="dotnet build",
        retry_feedback="上一轮尝试失败：本地构建未通过。",
        retry_context=RetryContext(
            source_attempt_number=2,
            failure_kind="build",
            summary="Issue changes failed local build verification",
            primary_failure_fingerprint="nullable_type_mismatch",
        ),
        working_memory=IssueWorkingMemory(
            version=1,
            issue_key="ISSUE-2",
            rule_id="csharpsquid:S3776",
            current_goal="修复当前 issue",
            authoritative_workspace_state="issue_baseline",
            stale_evidence=("CS1503 at src/Foo.cs:42 - cannot convert anonymous type",),
            rollback_reason="上一轮 patch 已撤销，工作区回到 issue baseline。",
            latest_retryable_failure="上一轮构建失败",
            next_action="先读取当前文件，再换一种修法。",
            last_updated_at="2026-04-17T00:00:00+00:00",
        ),
    )

    assert "【历史失败线索】" in prompt
    assert "已撤销 patch" in prompt
    assert "已失效旧证据" in prompt
    assert "【上次尝试的失败信息】" not in prompt


def test_user_prompt_adds_compaction_boundary_after_retry_depth(tmp_path: Path) -> None:
    result = IssuePromptBuilder.build_user_prompt_result(
        issue=type(
            "Issue",
            (),
            {
                "key": "ISSUE-3",
                "rule": "csharpsquid:S3776",
                "message": "认知复杂度过高",
                "severity": "MAJOR",
                "file_path": "/src/Foo.cs",
                "line": 42,
                "start_line": 42,
                "end_line": 42,
                "text_range": {"startLine": 42, "endLine": 42},
                "flows": (),
            },
        )(),
        code_context="  42 | if (a) { if (b) { Do(); } }",
        quality_gate_text="",
        scope_guidance="- 只改当前 issue",
        rule_details={"name": "复杂度", "description": "desc", "how_to_fix": "fix"},
        build_command="dotnet build",
        retry_context=RetryContext(
            source_attempt_number=5,
            failure_kind="build",
            issue_rule_id="csharpsquid:S3776",
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
            ),
        ),
        workspace_path=tmp_path,
        working_memory=IssueWorkingMemory(
            version=1,
            issue_key="ISSUE-3",
            rule_id="csharpsquid:S3776",
            current_goal="修复当前 issue",
            authoritative_workspace_state="issue_baseline",
            latest_retryable_failure="上一轮构建失败",
            next_action="先读取当前文件，再换一种修法。",
            last_updated_at="2026-04-17T00:00:00+00:00",
        ),
        model_hint="MiniMax-M2.7",
    )

    assert result.compaction_applied is True
    assert "【上下文压缩边界】" in result.prompt
    assert "1. 当前任务目标" in result.prompt
    assert "2. 已完成的关键动作" in result.prompt
    assert "3. 已修改或重点查看过的文件" in result.prompt
    assert "4. 关键决定与约束" in result.prompt
    assert "5. 下一步应该做什么" in result.prompt
    assert result.issue_working_memory is not None
    assert result.issue_working_memory.compaction_generation == 1


def test_user_prompt_surfaces_durable_memory_in_support_layer() -> None:
    edit_contract = EditContract(
        issue_key="ISSUE-4",
        rule_id="csharpsquid:S3776",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        execution_mode="simple_loop",
        planner_lessons=(
            PlannerLesson(
                source="rule_pattern",
                summary="避免再次提取 helper。",
                guidance=("改回原方法体内收口。",),
                selection_mode="rule_plus_fingerprint",
                selection_reason="rule_id + failure_fingerprint exact match",
            ),
            PlannerLesson(
                source="quality_gate_lesson",
                summary="避免再引入 async_without_await。",
                guidance=("新增 helper 默认同步。",),
                selection_mode="rule_exact",
                selection_reason="quality_gate_rules=async_requires_await",
            ),
            PlannerLesson(
                source="ignored",
                summary="这条不应注入 prompt。",
                guidance=("忽略。",),
            ),
        ),
    )

    prompt = IssuePromptBuilder.build_user_prompt(
        issue=type(
            "Issue",
            (),
            {
                "key": "ISSUE-4",
                "rule": "csharpsquid:S3776",
                "message": "认知复杂度过高",
                "severity": "MAJOR",
                "file_path": "/src/Foo.cs",
                "line": 42,
                "start_line": 42,
                "end_line": 42,
                "text_range": {"startLine": 42, "endLine": 42},
                "flows": (),
            },
        )(),
        code_context="  42 | if (a) { if (b) { Do(); } }",
        quality_gate_text="",
        scope_guidance="- 只改当前 issue",
        rule_details={"name": "复杂度", "description": "desc", "how_to_fix": "fix"},
        build_command="dotnet build",
        retry_feedback="上一轮尝试失败：本地构建未通过。",
        retry_context=RetryContext(
            source_attempt_number=2,
            failure_kind="build",
            summary="Issue changes failed local build verification",
        ),
        edit_contract=edit_contract,
    )

    assert "【长期参考】" in prompt
    assert "避免再次提取 helper。" in prompt
    assert "避免再引入 async_without_await。" in prompt
    assert "【上次尝试的失败信息】" in prompt
    assert prompt.index("【长期参考】") < prompt.index("=== DYNAMIC_BOUNDARY ===")


def test_simple_loop_prompt_omits_heavy_sections_by_default() -> None:
    prompt = IssuePromptBuilder.build_user_prompt(
        issue=type(
            "Issue",
            (),
            {
                "key": "ISSUE-MINIMAL",
                "rule": "csharpsquid:S3776",
                "message": "认知复杂度过高",
                "severity": "MAJOR",
                "file_path": "/src/Foo.cs",
                "line": 42,
                "start_line": 42,
                "end_line": 42,
                "text_range": {"startLine": 42, "endLine": 42},
                "flows": (),
            },
        )(),
        code_context="  42 | if (a) { if (b) { Do(); } }",
        quality_gate_text="不要注入",
        scope_guidance="- 只改当前 issue",
        rule_details={"name": "复杂度", "description": "desc", "how_to_fix": "fix"},
        build_command="dotnet build",
        edit_contract=EditContract(
            issue_key="ISSUE-MINIMAL",
            rule_id="csharpsquid:S3776",
            guardrail_mode="scope",
            target_files=("src/Foo.cs",),
            execution_mode="simple_loop",
        ),
        visible_tool_names=("Read", "Edit", "Bash"),
    )

    assert "【SonarQube 规则说明（问题原因/风险）】" not in prompt
    assert "【长期参考】" not in prompt
    assert "【执行模式】" not in prompt
    assert "【工具策略】" not in prompt
    assert "【当前可用工具】" in prompt
