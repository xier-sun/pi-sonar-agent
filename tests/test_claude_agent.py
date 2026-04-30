from __future__ import annotations

import asyncio

from pi_sonar_agent.agent.claude_agent import (
    BUILTIN_FIX_TOOLS,
    MCP_FIX_TOOLS,
    ClaudeFixAgent,
    RoleAgentRunResult,
    SonarIssue,
)
from pi_sonar_agent.core.agent_role_prompts import (
    build_main_role_user_prompt,
    build_review_role_system_prompt,
    build_review_role_user_prompt,
)
from pi_sonar_agent.agent.rule_policies import (
    CONDITIONAL_CHAIN_SCOPE_MODE,
    CONTROL_BLOCK_SCOPE_MODE,
    DECLARATION_COMMENT_SCOPE_MODE,
    EXPRESSION_REWRITE_SCOPE_MODE,
    LOOP_REWRITE_SCOPE_MODE,
    collect_skipped_rule_ids,
    get_rule_policy,
)
from pi_sonar_agent.agent.rule_validators import validate_rule_fix
from pi_sonar_agent.core.agent_runtime import AgentRuntimeError, AgentRuntimeResult
from pi_sonar_agent.core.agent_role_prompts import build_fix_role_user_prompt
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange, ReviewedLineOperation
from pi_sonar_agent.core.events import AttemptRuntimeEvent, AttemptRuntimeEventKind
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.lessons_store import PlannerLesson
from pi_sonar_agent.core.quality_gate import QualityGateRule
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.scope_guard import IssueEditScope
from pi_sonar_agent.core.tool_surface import build_allowed_fix_tool_rules, build_fix_runtime_tools
from pi_sonar_agent.core.memory.child_agent_memory import create_initial_child_agent_memory
from pi_sonar_agent.core.memory.issue_working_memory import IssueWorkingMemory


def _mock_role_compile_flow(monkeypatch, *, review_summary: str = "可以进入编译。", main_summary: str = "可以进入编译。") -> None:
    def fake_prompt_only_role_session(
        cls,
        *,
        role: str,
        workspace_path,
        system_prompt: str,
        user_prompt: str,
        max_turns: int = 4,
        agent_env=None,
        explicit_model=None,
    ) -> RoleAgentRunResult:
        if role == "review":
            return RoleAgentRunResult(
                role="review",
                response_text=f'{{"decision":"approve","summary":"{review_summary}","findings":[],"constraints":[]}}',
            )
        if role == "main":
            return RoleAgentRunResult(
                role="main",
                response_text=f'{{"decision":"compile","summary":"{main_summary}","findings":[],"constraints":[]}}',
            )
        raise AssertionError(f"unexpected role session: {role}")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "_run_prompt_only_role_session",
        classmethod(fake_prompt_only_role_session),
    )


def test_parse_role_decision_extracts_json_payload() -> None:
    decision = ClaudeFixAgent._parse_role_decision(
        raw_text='```json\n{"decision":"approve","summary":"可以进入编译。","findings":["无明显门禁阻塞"],"constraints":["保持最小改动"]}\n```',
        allowed_decisions=("approve", "retry"),
        fallback_decision="retry",
        fallback_summary="fallback",
    )

    assert decision.decision == "approve"
    assert decision.summary == "可以进入编译。"
    assert decision.findings == ("无明显门禁阻塞",)
    assert decision.constraints == ("保持最小改动",)


def test_builtin_fix_tools_include_grep_and_glob() -> None:
    assert "Read" in BUILTIN_FIX_TOOLS
    assert "Grep" in BUILTIN_FIX_TOOLS
    assert "Glob" in BUILTIN_FIX_TOOLS
    assert "Write" in BUILTIN_FIX_TOOLS


def test_build_user_prompt_includes_rule_reason_and_fix_guidance() -> None:
    issue = SonarIssue(
        key="issue-1",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=51,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  51 | if (condition) { ... }",
        "异步方法必须使用 async/await。",
        "- 只允许修改第 45-80 行的目标方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
    )

    assert "Issue Key: issue-1" in prompt
    assert "【SonarQube 修复建议】" in prompt
    assert "提取私有方法，减少嵌套层级。" in prompt
    assert "【C# 代码质量门禁】" in prompt
    assert "异步方法必须使用 async/await。" in prompt
    assert "【允许修改范围】" in prompt
    assert "只允许修改第 45-80 行的目标方法。" in prompt
    assert "【构建执行】" in prompt
    assert "外层统一执行 build 和 post-check；本轮不要自行构建" in prompt
    assert "- 文件路径: src/Foo.cs" in prompt or "- 文件路径: src/Foo.cs".replace("- ", "") in prompt
    assert "- src/Foo.cs" in prompt
    assert "不要顺手修复本文件中其他 issue，不要做大重构" in prompt
    assert "调用 finish 标记完成" not in prompt


def test_build_user_prompt_includes_workspace_relative_path_candidates(tmp_path) -> None:
    issue = SonarIssue(
        key="issue-path-candidates",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=10,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  10 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"',
        workspace_path=tmp_path / ".agent_workspaces" / "fix_BI" / "OpenAuth.Core",
    )

    assert "- OpenAuth.Core/OpenAuth.App/Finance/Foo.cs" in prompt
    assert "- OpenAuth.App/Finance/Foo.cs" in prompt
    assert "只使用仓库内相对路径" in prompt
    assert str(tmp_path) not in prompt


def test_build_user_prompt_renders_structured_retry_context() -> None:
    issue = SonarIssue(
        key="issue-retry-context",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    retry_context = RetryContext(
        source_attempt_number=1,
        failure_kind="build",
        error="Issue changes failed local build verification",
        raw_output="build failed",
        guidance=("先修复这些编译错误，再确认 Sonar 问题仍然被修复。",),
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | foreach (var item in items) { ... }",
        "",
        "- 只允许修改第 18-24 行。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        retry_context=retry_context,
    )

    assert "【上次尝试的失败信息】" in prompt
    assert "build failed" in prompt
    assert "请基于这些失败原因重新修复" in prompt


def test_build_user_prompt_includes_repair_summary_from_retry_context() -> None:
    issue = SonarIssue(
        key="issue-repair-summary",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="runtime_contract_violation",
        error="当前 retry 已禁用 helper_extract。",
        raw_output="当前 retry 已禁用 helper_extract。",
        strategy_summary="archetype=guard_clause_flatten; scope=method",
        patch_summary="files=src/Foo.cs ; symbols=Helper ; preview=+ private void Helper()",
        edited_symbols=("Helper",),
        workspace_state_note="进入本轮前工作区已恢复到 issue baseline；请先 Read 当前文件。",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | foreach (var item in items) { ... }",
        "",
        "- 只允许修改第 18-24 行。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        retry_context=retry_context,
    )

    assert "【上次修复摘要】" in prompt
    assert "上次修法" in prompt
    assert "Helper" in prompt


def test_classify_runtime_contract_agent_error_helper_extract_guard() -> None:
    classified = ClaudeFixAgent._classify_runtime_contract_agent_error(
        "当前 retry 已禁用 helper_extract，但你刚刚仍新增了 private helper/private method。"
    )

    assert classified == (
        "helper_extract_runtime_guard",
        "当前 retry 已禁用 helper_extract，但你刚刚仍新增了 private helper/private method。",
    )


def test_stabilize_review_decision_generates_actionable_retry_constraints() -> None:
    issue = SonarIssue(
        key="issue-review-fallback",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    decision = ClaudeFixAgent._parse_role_decision(
        raw_text="patch 摘要与当前代码不一致，目标方法复杂度仍然偏高。",
        allowed_decisions=("approve", "retry"),
        fallback_decision="retry",
        fallback_summary="Review 子Agent 未给出可用结论。",
    )

    stabilized = ClaudeFixAgent._stabilize_review_decision(
        issue=issue,
        patch_summary="src/Foo.cs: + helper call ; - inline branch",
        decision=decision,
    )

    assert stabilized.decision == "retry"
    assert stabilized.summary == "Review 子Agent要求继续修复，并已生成下一轮可执行约束。"
    assert stabilized.constraints
    assert any("patch 摘要" in item or "当前代码" in item for item in stabilized.constraints)


def test_stabilize_review_decision_approves_s3776_when_retry_only_requests_complexity_proof() -> None:
    issue = SonarIssue(
        key="issue-s3776-proof",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=42,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    decision = ClaudeFixAgent._parse_role_decision(
        raw_text=(
            '{"decision":"retry","summary":"patch 摘要未提供修复前后复杂度数值，无法确认是否降至 <=30。",'
            '"findings":["当前 patch 已改到目标方法","patch 摘要未提供复杂度数值"],'
            '"constraints":["请提供复杂度数值证明"]}'
        ),
        allowed_decisions=("approve", "retry"),
        fallback_decision="retry",
        fallback_summary="Review 子Agent 未给出可用结论。",
    )

    stabilized = ClaudeFixAgent._stabilize_review_decision(
        issue=issue,
        patch_summary="\n".join(
            [
                "target_file=src/Foo.cs",
                "target_method=TargetMethod",
                "touched_target_method=yes",
                "scope=target_method_only",
                "target_preview=+ if (a) return;",
            ]
        ),
        decision=decision,
    )

    assert stabilized.decision == "approve"
    assert "post-check" in stabilized.summary
    assert stabilized.constraints == ()


def test_stabilize_review_decision_approves_when_retry_only_refers_to_baseline_or_external_build_state() -> None:
    issue = SonarIssue(
        key="issue-review-baseline-state",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=42,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    decision = ClaudeFixAgent._parse_role_decision(
        raw_text=(
            '{"decision":"retry","summary":"当前 patch 方向合理，但 build=fail 状态表明存在编译风险，需要确认当前 baseline 代码的完整性后再审查",'
            '"findings":["目标方法已被修改","build=fail 可能来自 baseline 问题","NU1301 无法加载源 https://api.nuget.org/v3/index.json"],'
            '"constraints":["请确认 build=fail 是 baseline 代码问题还是当前 patch 引入的问题"]}'
        ),
        allowed_decisions=("approve", "retry"),
        fallback_decision="retry",
        fallback_summary="Review 子Agent 未给出可用结论。",
    )

    stabilized = ClaudeFixAgent._stabilize_review_decision(
        issue=issue,
        patch_summary="\n".join(
            [
                "target_file=src/Foo.cs",
                "target_method=ProcessAsync",
                "touched_target_method=yes",
                "scope=target_method_only",
                "target_preview=+ if (!matched) continue;",
            ]
        ),
        decision=decision,
    )

    assert stabilized.decision == "approve"
    assert "外部构建/回滚状态" in stabilized.summary
    assert stabilized.constraints == ()


def test_stabilize_main_decision_falls_back_to_compile_after_review_approval() -> None:
    review_decision = ClaudeFixAgent._parse_role_decision(
        raw_text='{"decision":"approve","summary":"可以进入编译。","findings":["方向正确"],"constraints":[]}',
        allowed_decisions=("approve", "retry"),
        fallback_decision="retry",
        fallback_summary="Review 子Agent 未给出可用结论。",
    )
    main_decision = ClaudeFixAgent._parse_role_decision(
        raw_text="",
        allowed_decisions=("compile", "retry"),
        fallback_decision="retry",
        fallback_summary="Main 裁决未批准进入编译阶段。",
    )

    stabilized = ClaudeFixAgent._stabilize_main_decision(
        review_decision=review_decision,
        decision=main_decision,
    )

    assert stabilized.decision == "compile"
    assert stabilized.summary == "可以进入编译。"


def test_review_role_prompts_stay_review_focused() -> None:
    issue = SonarIssue(
        key="issue-review-focus",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=1127,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    system_prompt = build_review_role_system_prompt()
    user_prompt = build_review_role_user_prompt(
        issue=issue,
        code_context="1127 | if (flag) { if (other) { Process(); } }",
        patch_summary="OpenAuth.App/Finance/HistoricalOverdueImportService.cs: 提取了局部条件并改成早返回。",
        current_file_content="\n".join(
            f"line {index}" for index in range(1, 120)
        ),
        working_memory=IssueWorkingMemory(
            version=1,
            issue_key="issue-review-focus",
            rule_id="csharpsquid:S3776",
            current_goal="修复当前 issue",
            authoritative_workspace_state="attempt_patch",
            rollback_reason="上一轮 patch 已撤销，当前 patch 重新生成。",
            rejected_strategies=("不要同步改 sibling 方法",),
            last_updated_at="2026-04-17T00:00:00+00:00",
        ),
        review_memory=create_initial_child_agent_memory(
            issue_key="issue-review-focus",
            role="review",
            focus="只判断当前 patch 是否值得进入编译",
        ),
    )

    assert "你不是 Fix 子Agent" in system_prompt
    assert "不做修复设计" in system_prompt
    assert "不要要求“复杂度数值证明”" in system_prompt
    assert "最终是否满足 <=30 由编译后的 post-check 继续确认" in system_prompt
    assert "【当前 patch 摘要】" in user_prompt
    assert "【目标方法附近代码】" in user_prompt
    assert "【当前文件内容】" not in user_prompt
    assert "【C# 质量门禁参考】" not in user_prompt
    assert "Edit 必须" not in user_prompt
    assert "不要要求同步改相似 sibling 方法" in user_prompt


def test_review_role_prompt_can_focus_on_current_attempt_patch_state() -> None:
    issue = SonarIssue(
        key="issue-review-attempt-patch",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=41,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    user_prompt = build_review_role_user_prompt(
        issue=issue,
        code_context="41 | foreach (var group in groupedOrders) { ... }",
        patch_summary="\n".join(
            [
                "target_file=OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs",
                "target_method=ProcessAsync",
                "touched_target_method=yes",
                "scope=target_method_only",
            ]
        ),
        current_file_content="\n".join(f"line {index}" for index in range(1, 120)),
        working_memory=IssueWorkingMemory(
            version=1,
            issue_key="issue-review-attempt-patch",
            rule_id="csharpsquid:S3776",
            current_goal="修复当前 issue",
            authoritative_workspace_state="attempt_patch",
            latest_verification="当前 patch 已生成，等待 Review 子Agent 基于当前代码审查。",
            next_action="只审查当前 patch。",
            last_updated_at="2026-04-17T00:00:00+00:00",
        ),
        review_memory=create_initial_child_agent_memory(
            issue_key="issue-review-attempt-patch",
            role="review",
            focus="只判断当前 patch 是否值得进入编译",
        ),
    )

    assert "当前工作区状态: attempt_patch" in user_prompt
    assert "等待 Review 子Agent 基于当前代码审查" in user_prompt


def test_fix_role_prompt_includes_minimal_quality_constraints() -> None:
    issue = SonarIssue(
        key="issue-fix-gate",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_fix_role_user_prompt(
        issue=issue,
        code_context="  18 | if (flag) { if (other) { Process(); } }",
        file_path_candidates=("src/Foo.cs",),
        working_memory=None,
        fix_memory=None,
        retry_feedback="",
    )

    assert "【Fix 质量约束】" in prompt
    assert "不要顺手补 XML 注释" in prompt
    assert "不要留下 async 无 await" in prompt
    assert "不要为了绕过类型问题引入 dynamic" in prompt
    assert "【Review 门禁要点】" not in prompt


def test_fix_role_prompt_adds_s107_hard_constraints() -> None:
    issue = SonarIssue(
        key="issue-fix-s107",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=1778,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_fix_role_user_prompt(
        issue=issue,
        code_context="1778 | private async Task ProcessSingleOrderInternal(...)",
        file_path_candidates=("OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs",),
        working_memory=None,
        fix_memory=None,
        retry_feedback="",
    )

    assert "【当前规则的硬约束】" in prompt
    assert "参数总数降到 <=7" in prompt
    assert "8 个或 9 个参数仍然算失败" in prompt
    assert "重新读取目标方法声明" in prompt
    assert ".pi-sonar-agent-runtime/s107-fix-guide.md" in prompt
    assert "先读取" in prompt


def test_fix_role_prompt_surfaces_cross_issue_lessons() -> None:
    issue = SonarIssue(
        key="issue-fix-lesson",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_fix_role_user_prompt(
        issue=issue,
        code_context="  18 | if (flag) { if (other) { Process(); } }",
        file_path_candidates=("src/Foo.cs",),
        planner_lessons=(
            PlannerLesson(
                source="success_pattern",
                summary="成功经验：优先在当前方法体内收口复杂度，再做最小补丁。",
                guidance=("优先保持单文件最小补丁。",),
                selection_mode="rule_exact_success",
                selection_reason="rule_id=csharpsquid:S3776 successful strategy pattern",
                count=3,
            ),
        ),
        working_memory=None,
        fix_memory=None,
        retry_feedback="",
    )

    assert "【长期经验】" in prompt
    assert "当前方法体内收口复杂度" in prompt
    assert "rule_exact_success" in prompt
    assert "命中原因" in prompt


def test_fix_role_prompt_includes_s3776_skill_section() -> None:
    issue = SonarIssue(
        key="issue-fix-s3776-skill",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=32,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_fix_role_user_prompt(
        issue=issue,
        code_context="  32 | if (flag) { if (other) { Process(); } }",
        file_path_candidates=("src/Foo.cs",),
        working_memory=None,
        fix_memory=None,
        retry_feedback="",
    )

    assert "【Skill: S3776 复杂度修复】" in prompt
    assert "只有当目标方法内收口明显不够时，才提取 private helper" in prompt
    assert "提取 helper 后，目标方法应变成更清晰的编排式调用" in prompt


def test_resolve_issue_max_turns_uses_global_floor_of_16() -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-turn-floor",
        rule="csharpsquid:S4487",
        message="移除未读取的 private 字段",
        line=8,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    assert agent._resolve_issue_max_turns(issue) == 16


def test_sync_s107_fix_guide_writes_runtime_copy(tmp_path) -> None:
    written = ClaudeFixAgent._sync_s107_fix_guide(tmp_path)

    assert written == ".pi-sonar-agent-runtime/s107-fix-guide.md"
    runtime_copy = tmp_path / ".pi-sonar-agent-runtime" / "s107-fix-guide.md"
    assert runtime_copy.exists()
    assert "S107 Fix Guide" in runtime_copy.read_text(encoding="utf-8")


def test_review_role_prompt_adds_s107_count_gate() -> None:
    issue = SonarIssue(
        key="issue-review-s107",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=869,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_review_role_user_prompt(
        issue=issue,
        code_context="869 | private void ProcessAcceptanceAndQualityAssurance(...)",
        patch_summary="target_method=ProcessAcceptanceAndQualityAssurance",
        current_file_content="\n".join(f"line {index}" for index in range(1, 120)),
        working_memory=None,
        review_memory=None,
    )

    assert "【当前规则的审查要点】" in prompt
    assert "只有当目标方法当前签名参数总数已 <=7 时才能 approve" in prompt
    assert "方向正确" in prompt
    assert "重数顶层参数个数" in prompt


def test_review_role_prompt_includes_s3776_skill_section() -> None:
    issue = SonarIssue(
        key="issue-review-s3776-skill",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_review_role_user_prompt(
        issue=issue,
        code_context="18 | if (flag) { if (other) { Process(); } }",
        patch_summary="target_method=ProcessOrder\ntouched_target_method=yes",
        current_file_content="\n".join(f"line {index}" for index in range(1, 80)),
        working_memory=None,
        review_memory=None,
    )

    assert "【Skill: S3776 复杂度修复】" in prompt
    assert "重点看复杂度下降是否来自目标方法本身" in prompt
    assert "不要求 patch 提供“复杂度计算证明”" in prompt


def test_build_patch_summary_focuses_on_issue_target_method() -> None:
    issue = SonarIssue(
        key="issue-patch-summary",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    current_file_content = "\n".join(
        [
            "public class FooService",
            "{",
            "    public void TargetMethod()",
            "    {",
            "        if (a)",
            "        {",
            "            Process();",
            "        }",
            "    }",
            "",
            "    private static bool Helper(int value)",
            "    {",
            "        return value > 0;",
            "    }",
            "}",
        ]
    )

    patch_summary = ClaudeFixAgent._build_patch_summary(
        issue=issue,
        edit_contract=EditContract(
            issue_key="issue-patch-summary",
            rule_id="csharpsquid:S3776",
            guardrail_mode="scope",
            target_files=("src/Foo.cs",),
            execution_mode="simple_loop",
        ),
        current_issue_file_content=current_file_content,
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                diff_text="\n".join(
                    [
                        "@@ -5 +5 @@",
                        "-        if (a && b)",
                        "+        if (a)",
                        "@@ -10,0 +11,4 @@",
                        "+    private static bool Helper(int value)",
                        "+    {",
                        "+        return value > 0;",
                        "+    }",
                    ]
                ),
                after_changed_lines=(5, 11, 12, 13, 14),
                line_operations=(
                    ReviewedLineOperation(kind="delete", before_line=5, after_line=5, text="        if (a && b)"),
                    ReviewedLineOperation(kind="add", before_line=5, after_line=5, text="        if (a)"),
                    ReviewedLineOperation(kind="add", before_line=10, after_line=11, text="    private static bool Helper(int value)"),
                    ReviewedLineOperation(kind="add", before_line=10, after_line=12, text="    {"),
                    ReviewedLineOperation(kind="add", before_line=10, after_line=13, text="        return value > 0;"),
                    ReviewedLineOperation(kind="add", before_line=10, after_line=14, text="    }"),
                ),
            ),
        ),
    )

    assert "target_file=src/Foo.cs" in patch_summary
    assert "target_method=TargetMethod" in patch_summary
    assert "touched_target_method=yes" in patch_summary
    assert "changed_methods=TargetMethod, Helper" in patch_summary
    assert "scope=target_file_expanded" in patch_summary
    assert "risk_flags=helper_added, sibling_method_touched" in patch_summary


def test_main_role_prompt_uses_compile_gate_policy() -> None:
    issue = SonarIssue(
        key="issue-main-gate",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=42,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_main_role_user_prompt(
        issue=issue,
        patch_summary="target_file=src/Foo.cs\ntarget_method=TargetMethod\ntouched_target_method=yes",
        review_result={
            "decision": "approve",
            "summary": "可以进入编译。",
            "findings": ["已改到目标方法"],
            "constraints": [],
        },
        working_memory=IssueWorkingMemory(
            version=1,
            issue_key="issue-main-gate",
            rule_id="csharpsquid:S3776",
            current_goal="修复当前 issue",
            authoritative_workspace_state="attempt_patch",
            last_updated_at="2026-04-17T00:00:00+00:00",
        ),
        main_memory=create_initial_child_agent_memory(
            issue_key="issue-main-gate",
            role="main",
            focus="判断是否进入编译",
        ),
    )

    assert "【Main 裁决门禁】" in prompt
    assert "不重做 review，也不设计修法" in prompt
    assert "不要因为 XML 注释、sealed、static、中文注释等非当前 issue 必要项而拒绝进入编译" in prompt
    assert "如果 review 已 approve" in prompt


def test_main_role_prompt_includes_s3776_skill_section() -> None:
    issue = SonarIssue(
        key="issue-main-s3776-skill",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=42,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_main_role_user_prompt(
        issue=issue,
        patch_summary="target_file=src/Foo.cs\ntarget_method=TargetMethod\ntouched_target_method=yes",
        review_result={
            "decision": "approve",
            "summary": "主方法结构已明显收口。",
            "findings": ["已改到目标方法"],
            "constraints": [],
        },
        working_memory=None,
        main_memory=None,
    )

    assert "【Skill: S3776 复杂度修复】" in prompt
    assert "如果当前 patch 只是搬移代码、没有明显简化目标方法结构，优先 retry" in prompt


def test_main_role_prompt_adds_s107_compile_gate() -> None:
    issue = SonarIssue(
        key="issue-main-s107",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=1778,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_main_role_user_prompt(
        issue=issue,
        patch_summary="target_method=ProcessSingleOrderInternal\ncurrent_parameter_count=9",
        review_result={
            "decision": "approve",
            "summary": "方向正确，但当前仍为 9 个参数。",
            "findings": ["已合并部分参数"],
            "constraints": [],
        },
        working_memory=None,
        main_memory=None,
    )

    assert "【当前规则的编译门槛】" in prompt
    assert "只有当目标方法当前签名参数总数已 <=7 时才允许 compile" in prompt
    assert "方向正确但未达阈值" in prompt


def test_build_user_prompt_renders_workspace_retry_references() -> None:
    issue = SonarIssue(
        key="issue-retry-workspace-files",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    retry_context = RetryContext(
        source_attempt_number=2,
        failure_kind="build",
        error="Issue changes failed local build verification",
        raw_output="full build output",
        prompt_output="构建验证超时，没有发现明确编译错误。",
        build_timeout_failed=True,
        build_timeout_without_errors=True,
        workspace_file_references=(
            ".pi-sonar-agent-runtime/retry/issue/attempt-02-build-summary.txt",
            ".pi-sonar-agent-runtime/retry/issue/attempt-02-build-tail.log",
        ),
        workspace_read_hint="先看 summary，再按需看 tail；不要一次性读取整份大日志。",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | foreach (var item in items) { ... }",
        "",
        "- 只允许修改第 18-24 行。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        retry_context=retry_context,
    )

    assert "构建验证超时，没有发现明确编译错误。" in prompt
    assert ".pi-sonar-agent-runtime/retry/issue/attempt-02-build-summary.txt" in prompt
    assert ".pi-sonar-agent-runtime/retry/issue/attempt-02-build-tail.log" in prompt
    assert "不要一次性读取整份大日志" in prompt


def test_build_user_prompt_includes_precise_sonar_location_guidance() -> None:
    issue = SonarIssue(
        key="issue-location",
        rule="csharpsquid:S3358",
        message="Extract this nested ternary operation into an independent statement.",
        line=92,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
        text_range={
            "startLine": 92,
            "endLine": 93,
            "startOffset": 8,
            "endOffset": 24,
        },
        flows=(
            {
                "locations": (
                    {
                        "component": "BI:src/Bar.cs",
                        "msg": "Related branch condition.",
                        "textRange": {"startLine": 16, "endLine": 16, "startOffset": 12, "endOffset": 28},
                    },
                ),
            },
        ),
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  92 | value = condition ? a : b;",
        "",
        "- 只允许修改当前表达式。",
        {
            "name": "Ternary operators should not be nested",
            "description": "嵌套三元表达式会降低可读性。",
            "how_to_fix": "将内层条件提取为独立语句。",
        },
        'dotnet build "src/Foo.sln"',
    )

    assert "【精确定位】" in prompt
    assert "- 主定位: src/Foo.cs:92:9-93:25" in prompt
    assert "- 关联位置 1: src/Bar.cs:16:13-29 | Related branch condition." in prompt
    assert "- 报错行号: 92" in prompt


def test_load_csharp_quality_gate_only_for_csharp_files() -> None:
    csharp_issue = SonarIssue(
        key="issue-cs",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    text_issue = SonarIssue(
        key="issue-txt",
        rule="generic:rule",
        message="文本问题",
        line=1,
        component="BI:notes.txt",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    csharp_gate = ClaudeFixAgent._load_csharp_quality_gate(csharp_issue)
    text_gate = ClaudeFixAgent._load_csharp_quality_gate(text_issue)

    assert "C# 代码质量与架构规范门禁" in csharp_gate
    assert "异步标准" in csharp_gate
    assert text_gate == ""


def test_build_agent_extra_args_uses_bare_for_third_party_provider() -> None:
    extra_args = ClaudeFixAgent._build_agent_extra_args(
        {
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_API_KEY": "token",
        }
    )

    assert extra_args == {
        "setting-sources": "project,local",
        "bare": None,
    }


def test_build_agent_extra_args_keeps_default_mode_for_first_party_provider() -> None:
    extra_args = ClaudeFixAgent._build_agent_extra_args(
        {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_API_KEY": "token",
        }
    )

    assert extra_args == {"setting-sources": "project,local"}


def test_build_sdk_child_env_strips_model_env_for_third_party_provider() -> None:
    child_env = ClaudeFixAgent._build_sdk_child_env(
        {
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_API_KEY": "token",
            "ANTHROPIC_CUSTOM_MODEL_OPTION": "glm-4.7",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
            "CLAUDE_MODEL": "glm-4.7",
        }
    )

    assert child_env["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert child_env["ANTHROPIC_API_KEY"] == "token"
    assert child_env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == ""
    assert child_env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == ""
    assert child_env["CLAUDE_MODEL"] == ""


def test_resolve_sdk_model_uses_cli_model_for_third_party_provider() -> None:
    raw_env = {
        "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
        "ANTHROPIC_API_KEY": "token",
    }
    child_env = ClaudeFixAgent._build_sdk_child_env(raw_env)

    sdk_model = ClaudeFixAgent._resolve_sdk_model(raw_env, child_env, "glm-4.7")

    assert sdk_model == "glm-4.7"
    assert child_env["ANTHROPIC_MODEL"] == "glm-4.7"
    assert child_env["CLAUDE_MODEL"] == ""


def test_build_sdk_child_env_preserves_explicit_model_key_clears() -> None:
    child_env = ClaudeFixAgent._build_sdk_child_env(
        {
            "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
            "ANTHROPIC_API_KEY": "token",
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_MODEL": "",
            "OPENAI_MODEL": "",
        }
    )

    assert child_env["ANTHROPIC_BASE_URL"] == "https://api.minimaxi.com/anthropic"
    assert child_env["ANTHROPIC_API_KEY"] == "token"
    assert child_env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert child_env["CLAUDE_MODEL"] == ""
    assert child_env["OPENAI_MODEL"] == ""


def test_load_csharp_quality_gate_uses_repo_markdown_as_single_source(
    monkeypatch,
    tmp_path,
) -> None:
    issue = SonarIssue(
        key="issue-cs-skill",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    gate_file = tmp_path / "csharp-quality-gate.md"
    gate_file.write_text(
        "\n".join(
            [
                "---",
                '{"version":1,"rules":[{"rule_id":"demo","title":"Demo","summary":"Demo","enforcement":"hard"}]}',
                "---",
                "",
                "# C# 代码质量与架构规范门禁",
                "",
                "* **[强制] XML 文档注释**：所有公开的类、方法、属性、实体都必须有完整的 XML 文档注释。",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(ClaudeFixAgent, "QUALITY_GATE_PATHS", (gate_file,))

    gate = ClaudeFixAgent._load_csharp_quality_gate(issue)

    assert '"version":1' not in gate
    assert "# C# 代码质量与架构规范门禁" in gate
    assert "所有公开的类、方法、属性、实体都必须有完整的 XML 文档注释" in gate


def test_load_csharp_quality_gate_prefers_active_rule_summary_when_contract_present() -> None:
    issue = SonarIssue(
        key="issue-cs-active-rules",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    edit_contract = EditContract(
        issue_key=issue.key,
        rule_id=issue.rule,
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        quality_gate_rules=(
            QualityGateRule(
                rule_id="cognitive_complexity",
                title="单方法认知复杂度不超过 30",
                summary="触达方法的认知复杂度不得超过 30。",
                enforcement="hard",
                prompt_hint="优先通过拆小局部逻辑或提取 private helper 降低复杂度。",
            ),
        ),
    )

    gate = ClaudeFixAgent._load_csharp_quality_gate(issue, edit_contract)

    assert "本次修复只需遵守下面这些已启用的质量门禁规则" in gate
    assert "cognitive_complexity 单方法认知复杂度不超过 30" in gate
    assert "优先通过拆小局部逻辑或提取 private helper 降低复杂度" in gate
    assert "# C# 代码质量与架构规范门禁" not in gate


def test_default_quality_gate_source_points_to_repo_file() -> None:
    assert len(ClaudeFixAgent.QUALITY_GATE_PATHS) == 1
    assert str(ClaudeFixAgent.QUALITY_GATE_PATHS[0]).replace("\\", "/").endswith(
        "data/csharp-quality-gate.md"
    )


def test_build_user_prompt_includes_rule_specific_guards() -> None:
    issue = SonarIssue(
        key="issue-guards",
        rule="csharpsquid:S3267",
        message="循环可简化为 LINQ",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | foreach (var item in items) { ... }",
        "",
        "- 只允许修改第 18-24 行。",
        {
            "name": "Loops should be simplified with LINQ expressions",
            "description": "某些循环可以被 LINQ 表达式替代。",
            "how_to_fix": "在安全时使用 LINQ。",
        },
        'dotnet build "src/Foo.sln"',
    )

    assert "【当前规则的额外约束】" not in prompt
    assert "IQueryable" not in prompt
    assert "不要为了满足规则把简单循环改成更难读" not in prompt
    assert "【SonarQube 修复建议】" in prompt
    assert "在安全时使用 LINQ。" in prompt


def test_build_user_prompt_includes_visible_tool_summary() -> None:
    issue = SonarIssue(
        key="issue-visible-tools",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        visible_tool_names=("Read", "Grep", "Glob", "Edit", "MultiEdit", "Bash", "Finish"),
    )

    assert "【当前可用工具】" in prompt
    assert "Read, Grep, Glob, Edit, MultiEdit, Bash, Finish" in prompt
    assert "【推荐构建命令】" not in prompt
    assert "外层统一执行 build 和 post-check" in prompt
    assert "先用 Glob/Grep" in prompt


def test_build_user_prompt_keeps_build_command_when_build_tool_is_visible() -> None:
    issue = SonarIssue(
        key="issue-visible-build-tool",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        visible_tool_names=("Read", "Edit", "mcp__sonar-fix__run_build", "Finish"),
    )

    assert "=== DYNAMIC_BOUNDARY ===" in prompt
    assert "【当前可用工具】" in prompt
    assert "Read, Edit, mcp__sonar-fix__run_build, Finish" in prompt
    assert 'dotnet build "src/Foo.sln"' not in prompt


def test_build_user_prompt_hides_bash_constraints_when_bash_is_not_visible() -> None:
    issue = SonarIssue(
        key="issue-no-bash",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        visible_tool_names=("Read", "Edit", "MultiEdit", "Finish"),
    )

    assert "【当前可用工具】" in prompt
    assert "Read, Edit, MultiEdit, Finish" in prompt
    assert "Bash" not in prompt


def test_build_user_prompt_keeps_simple_loop_prompt_light_for_s3776_retry() -> None:
    issue = SonarIssue(
        key="issue-s3776-guards",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    first_prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
    )
    retry_prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | if (condition) { ... }",
        "",
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        retry_context=RetryContext(source_attempt_number=1, failure_kind="quality_gate"),
    )

    assert "不要改动公开签名或新增公开成员" not in first_prompt
    assert "只有 helper 体内真实包含 await 时才允许 async" not in first_prompt
    assert "只有 helper 体内真实包含 await 时才允许 async" not in retry_prompt
    assert "一次性同步接口声明、调用点和 nameof(...)" not in retry_prompt
    assert "【上次尝试的失败信息】" not in retry_prompt
    assert "如果上一轮策略失败或已回滚" in retry_prompt


def test_build_user_prompt_externalizes_reference_when_prompt_is_large(tmp_path) -> None:
    issue = SonarIssue(
        key="issue-large-prompt",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    large_gate = "\n".join(f"- rule {index}: keep patch small" for index in range(600))

    prompt = ClaudeFixAgent._build_user_prompt(
        issue,
        "  18 | if (condition) { ... }",
        large_gate,
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。",
            "how_to_fix": "提取私有方法，减少嵌套层级。",
        },
        'dotnet build "src/Foo.sln"',
        edit_contract_section="【Edit Contract】\n" + ("- 最小修改\n" * 200),
        repair_plan_section="【Repair Plan】\n" + ("- 先在原方法内收口\n" * 200),
        workspace_path=workspace,
    )

    reference_file = workspace / ".pi-sonar-agent-runtime" / "sonar_fix_reference.md"

    assert reference_file.exists()
    assert "详细约束已写入 `.pi-sonar-agent-runtime/sonar_fix_reference.md`" in prompt
    assert "Sonar Fix Reference" in reference_file.read_text(encoding="utf-8")


def test_rule_validator_rejects_unresolved_nested_ternary() -> None:
    message = validate_rule_fix(
        validator_name="nested_ternary_removed",
        issue_line=3,
        file_content="\n".join(
            [
                "var result = foo",
                "    ? (bar ? 1 : 0)",
                "    : 2;",
            ]
        ),
    )

    assert "nested ternary expression still exists" in message
    assert "csharpsquid:S3358" in message


def test_rule_validator_accepts_single_level_ternary() -> None:
    message = validate_rule_fix(
        validator_name="nested_ternary_removed",
        issue_line=2,
        file_content="\n".join(
            [
                "var parsed = int.TryParse(value, out var temp) ? temp : 0;",
                "var result = hasValue ? parsed : 0;",
            ]
        ),
    )

    assert message == ""


def test_fix_issue_fails_when_agent_makes_no_changes(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-2",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(claude_agent_module.anyio, "run", lambda func: None)

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.error == "Agent completed without modifying any files"
    assert result.retryable_failure is True
    assert result.failure_kind == "no_change"


def test_fix_issue_uses_same_attempt_no_change_continuation_before_failing(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-no-change-cont",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    requests: list[object] = []

    def fake_runtime_run(self, request):
        requests.append(request)
        return AgentRuntimeResult(
            tool_uses=("Read",),
            last_tool_name="Read",
            runtime_events=(
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    sequence=1,
                    stage="completed",
                    payload={"success": False},
                ),
            ),
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.failure_kind == "no_change"
    assert len(requests) == 2
    assert requests[0].max_turns == 20
    assert requests[1].max_turns == 20
    assert "你还没有真正修改代码" in requests[1].user_prompt
    assert result.performance_metrics["continuation_retry_count"] == 1


def test_fix_issue_classifies_empty_edit_payload_as_tool_input_invalid(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-invalid-edit",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_runtime_run(self, request):
        return AgentRuntimeResult(
            tool_uses=("Read", "Edit"),
            last_tool_name="Edit",
            tool_call_count=2,
            read_call_count=1,
            edit_call_count=1,
            runtime_events=(
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.TOOL_CALLED,
                    sequence=1,
                    stage="tool:Edit",
                    payload={"tool_name": "Edit", "tool_payload": {}},
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.SDK_TRACE,
                    sequence=2,
                    stage="sdk_message:UserMessage",
                    payload={
                        "preview": (
                            "{\"content\": [{\"content_preview\": "
                            "\"<tool_use_error>InputValidationError: Edit failed due to the following issues:\\n"
                            "The required parameter `file_path` is missing\\n"
                            "The required parameter `old_string` is missing\\n"
                            "The required parameter `new_string` is missing</tool_use_error>\"}]}"
                        )
                    },
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    sequence=3,
                    stage="completed",
                    payload={"success": False},
                ),
            ),
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "tool_input_invalid"
    assert result.error == "Model emitted an invalid Edit/MultiEdit/Write call"
    assert "InputValidationError" in result.build_output


def test_fix_issue_routes_s107_to_roslyn_engine_when_available(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-skip",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=12,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    public void Save(string a, string b, string c, string d, string e, string f, string g, string h)",
                "    {",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    rule_detail_calls: list[str] = []
    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: rule_detail_calls.append(rule_key) or {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        "pi_sonar_agent.core.engine_router.inspect_roslyn_availability",
        lambda: (True, ()),
    )

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.skipped is True
    assert result.failure_kind == "roslyn_cannot_fix_safely"
    assert rule_detail_calls == ["csharpsquid:S107"]
    assert result.engine_routing_decision is not None
    assert result.engine_routing_decision.resolved_engine == "roslyn"


def test_fix_issue_skips_when_engine_router_rejects_rule(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-engine-routing-skip",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=12,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    from pi_sonar_agent.core.engine_router import EngineRoutingDecision

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        "pi_sonar_agent.agent.claude_agent.route_engine_for_issue",
        lambda **kwargs: EngineRoutingDecision(
            primary_engine="roslyn",
            resolved_engine="skip",
            fallback_allowed=False,
            fallback_reason="S107 agent fallback disabled until Roslyn solution engine is available.",
            skip_reason="S107 requires Roslyn engine, but it is unavailable. missing project file: fix_engine/AgentFixEngine.csproj",
            requires_roslyn=True,
        ),
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_build_user_prompt",
        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prompt should not be built"))),
    )

    result = agent.fix_issue(issue, tmp_path)

    assert result.success is False
    assert result.skipped is True
    assert result.failure_kind == "engine_router_skip"
    assert result.skip_reason.startswith("S107 requires Roslyn engine")
    assert result.engine_routing_decision is not None
    assert result.engine_routing_decision.resolved_engine == "skip"


def test_previously_skipped_rules_now_expose_prompt_guards_without_skip_reason() -> None:
    for rule_id in ("csharpsquid:S107", "csharpsquid:S1172", "csharpsquid:S4136", "csharpsquid:S6960"):
        policy = get_rule_policy(rule_id)
        assert policy.skip_reason == ""
        assert policy.prompt_guards


def test_s3776_policy_requires_preserving_nullable_types_when_extracting_helpers() -> None:
    policy = get_rule_policy("csharpsquid:S3776")

    assert any("nullable" in guard for guard in policy.prompt_guards)
    assert any("decimal?" in guard for guard in policy.prompt_guards)
    assert any("DateTime?" in guard for guard in policy.prompt_guards)


def test_collect_skipped_rule_ids_returns_empty_set_for_current_defaults() -> None:
    assert collect_skipped_rule_ids() == set()


def test_fix_issue_downgrades_complex_plan_instead_of_hard_blocking(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-plan-conflict",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    public async Task AutoPlugin(IEnumerable<int> orderIds)",
                "    {",
                "        await SaveAsync(orderIds);",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    runtime_requests: list[object] = []

    def fake_runtime_run(self, request):
        runtime_requests.append(request)
        return AgentRuntimeResult(
            agent_error=None,
            tool_uses=("Read",),
            last_tool_name="Read",
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.failure_kind == "no_change"
    assert result.plan_precheck is not None
    assert result.plan_precheck.status == "not_applicable"
    assert result.repair_plan is None
    assert result.execution_mode == "simple_loop"
    assert runtime_requests


def test_builtin_tool_policy_allows_read_edit_search_tools_without_bash() -> None:
    assert BUILTIN_FIX_TOOLS == ["Read", "Grep", "Glob", "Edit", "MultiEdit", "Write"]
    assert "Bash" not in BUILTIN_FIX_TOOLS
    assert MCP_FIX_TOOLS == []
    assert "mcp__sonar-fix__git_add" not in MCP_FIX_TOOLS
    assert "mcp__sonar-fix__git_commit" not in MCP_FIX_TOOLS
    assert "mcp__sonar-fix__git_push" not in MCP_FIX_TOOLS


def test_build_fix_role_user_prompt_prefers_relative_path_and_summarizes_invalid_edit_feedback() -> None:
    issue = SonarIssue(
        key="issue-role-fix",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=41,
        component="BI:OpenAuth.Core/OpenAuth.App/Finance/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    prompt = build_fix_role_user_prompt(
        issue=issue,
        code_context="  41 | public async Task ProcessAsync()",
        file_path_candidates=("OpenAuth.Core/OpenAuth.App/Finance/Foo.cs",),
        working_memory=None,
        fix_memory=None,
        retry_feedback=(
            "Invalid write tool input burst detected; stop this attempt and retry with a precise patch.\n\n"
            "{\"content\": [{\"content_preview\": \"<tool_use_error>InputValidationError: "
            "Edit failed due to the following issue:\\nThe required parameter `old_string` is missing"
            "</tool_use_error>\"}]}"
        ),
    )

    assert "- 主文件相对路径: OpenAuth.Core/OpenAuth.App/Finance/Foo.cs" in prompt
    assert "- 读取和编辑时只使用上面的仓库相对路径，不要先尝试带前导 / 的路径。" in prompt
    assert "先用 Glob/Grep" in prompt
    assert "Edit 必须带 file_path、old_string、new_string" in prompt
    assert "required parameter `old_string` is missing" not in prompt
    assert "\"content_preview\"" not in prompt


def test_classify_fix_role_failure_surfaces_tool_input_invalid() -> None:
    attempt_events = (
        AttemptRuntimeEvent(
            sequence=1,
            kind=AttemptRuntimeEventKind.SDK_TRACE,
            stage="sdk_message:UserMessage",
            payload={
                "preview": (
                    "{\"content\": [{\"content_preview\": \"<tool_use_error>InputValidationError: "
                    "Edit failed due to the following issue:\\nThe required parameter `old_string` is missing"
                    "</tool_use_error>\"}]}"
                )
            },
        ),
    )

    failure_kind, summary, child_summary, build_output = ClaudeFixAgent._classify_fix_role_failure(
        agent_error="Invalid write tool input burst detected; stop this attempt and retry with a precise patch.",
        attempt_events=attempt_events,
    )

    assert failure_kind == "tool_input_invalid"
    assert "无效的 Edit/MultiEdit/Write 工具调用" in summary
    assert "缺少必要参数" in child_summary
    assert "old_string" in build_output


def test_allowed_fix_tool_rules_append_controlled_bash_rules() -> None:
    allowed_tools = build_allowed_fix_tool_rules(["Read", "Edit"], include_controlled_bash=True)

    assert "Read" in allowed_tools
    assert "Edit" in allowed_tools
    assert "Bash" in allowed_tools
    assert "Finish" in allowed_tools


def test_allowed_fix_tool_rules_append_scoped_write_create_file_rules() -> None:
    allowed_tools = build_allowed_fix_tool_rules(
        ["Read", "Edit"],
        create_file_tool_roots=("src/generated",),
    )

    assert "Write" in allowed_tools
    assert "Write(create_file_under=src/generated)" in allowed_tools
    assert "Finish" in allowed_tools


def test_claude_fix_tool_policy_allows_finish_and_harmless_shell() -> None:
    policy = ClaudeFixAgent._build_fix_tool_policy()

    finish_decision = policy.classify("Finish")
    echo_decision = policy.classify("Bash", {"command": "echo 修复完成"})
    delete_decision = policy.classify("Bash", {"command": "Remove-Item Foo.cs"})

    assert finish_decision.allowed is True
    assert echo_decision.allowed is True
    assert delete_decision.allowed is False
    assert delete_decision.policy_violation is True


def test_claude_fix_tool_policy_bundle_exposes_write_for_create_file_contract(tmp_path) -> None:
    contract = EditContract(
        issue_key="ISSUE-WRITE",
        rule_id="csharpsquid:S107",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        allow_file_creation=True,
        allowed_new_file_roots=("src/generated",),
        patch_only=True,
    )

    policy, visible_toolset = ClaudeFixAgent._build_fix_tool_policy_bundle(
        contract,
        workspace_path=tmp_path,
    )

    existing_file = tmp_path / "src" / "Foo.cs"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_text("class Foo {}\n", encoding="utf-8")

    assert "Write" in visible_toolset.visible_tools
    assert "Write(create_file_under=src/generated)" not in visible_toolset.allowed_tools
    rewrite_decision = policy.classify(
        "Write",
        {"file_path": "src/Foo.cs", "content": "class Foo { }\n"},
    )
    create_decision = policy.classify(
        "Write",
        {"file_path": "src/generated/NewType.cs", "content": "class NewType {}\n"},
    )
    assert rewrite_decision.allowed is True
    assert create_decision.allowed is False


def test_resolve_runtime_builtin_tools_keeps_full_fix_toolset(tmp_path) -> None:
    agent = ClaudeFixAgent(
        sonar_host="https://sonar.example",
        sonar_token="token",
        agent_env={"ANTHROPIC_BASE_URL": "https://proxy.example/anthropic"},
        model="MiniMax-M2.7",
    )

    assert agent._resolve_runtime_builtin_tools(tmp_path) == build_fix_runtime_tools(
        include_create_file_tool=False
    )


def test_fix_issue_attaches_sonar_mcp_runtime_when_configured(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(
        sonar_host="https://sonar.example",
        sonar_token="token",
        agent_env={
            "SONAR_MCP_ENABLED": "true",
            "SONAR_MCP_MODE": "stdio",
            "SONAR_MCP_COMMAND": "sonarqube-mcp-server",
            "SONAR_MCP_ARGS": "--stdio",
            "SONAR_MCP_TOOLS": "mcp__sonarqube__search_issues,mcp__sonarqube__get_rule",
            "SONAR_MCP_READ_ONLY": "true",
            "SONAR_MCP_URL": "https://sonar.example",
            "SONAR_MCP_TOKEN": "token",
        },
    )
    issue = SonarIssue(
        key="issue-mcp-runtime",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    public void Demo()",
                "    {",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    runtime_requests: list[object] = []

    def fake_runtime_run(self, request):
        runtime_requests.append(request)
        return AgentRuntimeResult(
            agent_error=None,
            tool_uses=("Read",),
            last_tool_name="Read",
            saw_result_event=True,
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    result = agent.fix_issue(issue, tmp_path)

    assert runtime_requests
    request = runtime_requests[0]
    assert "mcp__sonarqube__search_issues" in request.allowed_tools
    assert request.mcp_servers["sonarqube"]["command"] == "sonarqube-mcp-server"
    assert request.metadata["mcp_tools_count"] == "2"
    assert request.metadata["mcp_mode"] == "stdio"
    assert "mcp__sonarqube__search_issues" in request.metadata["visible_tools"]
    assert result.visible_toolset is not None
    assert "mcp__sonarqube__search_issues" in result.visible_toolset.visible_tools


def test_fix_issue_fails_when_local_build_verification_fails(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )
    _mock_role_compile_flow(monkeypatch)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(claude_agent_module.anyio, "run", lambda func: None)

    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "compile failed"

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.error == "Issue changes failed local build verification"
    assert result.build_command == 'dotnet build "src/Foo.sln"'
    assert "compile failed" in result.build_output
    assert result.build_verification_failed is True
    assert result.retryable_failure is True
    assert result.failure_kind == "build"


def test_fix_issue_retries_when_rule_specific_validation_fails(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3d",
        rule="csharpsquid:S3358",
        message="嵌套三元运算符",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "var value = foo",
                "    ? (bar ? 1 : 0)",
                "    : 2;",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )
    _mock_role_compile_flow(monkeypatch)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_run(func) -> None:
        source_file.write_text(
            "\n".join(
                [
                    "var output = foo",
                    "    ? (bar ? 1 : 0)",
                    "    : 2;",
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(claude_agent_module.anyio, "run", fake_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "rule_validation"
    assert "nested ternary expression still exists" in result.build_output


def test_fix_issue_retries_when_run_build_tool_crashes(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-build-tool",
        rule="csharpsquid:S6580",
        message="DateTime.TryParse 应指定 format provider",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeToolUseBlock("mcp__sonar-fix__run_build")]

    async def fake_receive_response():
        yield FakeAssistantMessage()
        raise RuntimeError("Command failed with exit code 3 (exit code: 3)\nError output: build stderr")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def query(self, prompt):
            return None

        def receive_response(self):
            return fake_receive_response()

    monkeypatch.setattr(claude_agent_module, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_agent_module, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())

    class FakeCompletedProcess:
        returncode = 1
        stdout = "build stdout"
        stderr = "src/Foo.cs(4,1): error CS0103: name not found [Foo.csproj]"

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.error == "Build tool execution failed"
    assert result.retryable_failure is True
    assert result.build_verification_failed is True
    assert result.failure_kind == "build_tool"
    assert "run_build 工具执行异常" in result.build_output
    assert "本地回退构建 Exit code: 1" in result.build_output
    assert "error CS0103: name not found" in result.build_output


def test_fix_issue_retries_when_model_first_response_times_out(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-model-timeout",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(
        claude_agent_module.anyio,
        "run",
        lambda func: (_ for _ in ()).throw(TimeoutError("模型在 120 秒内没有返回首个响应")),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "model_timeout"
    assert result.error == "Model response timed out"
    assert "没有返回首个响应" in result.build_output


def test_fix_issue_retries_when_follow_up_response_times_out(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-follow-up-timeout",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    events: list[str] = []

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeTextBlock("still working")]

    async def fake_receive_response():
        yield FakeAssistantMessage()
        await asyncio.sleep(1)

    class FakeClient:
        async def __aenter__(self):
            events.append("__aenter__")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("__aexit__")
            return None

        async def query(self, prompt):
            return None

        async def interrupt(self):
            events.append("interrupt")
            return None

        def receive_response(self):
            return fake_receive_response()

    monkeypatch.setattr(claude_agent_module, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_agent_module, "TextBlock", FakeTextBlock)
    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())
    monkeypatch.setattr(claude_agent_module, "FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(claude_agent_module, "ISSUE_HARD_TIMEOUT_SECONDS", 10)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "model_timeout"
    assert result.error == "Model response timed out"
    assert "没有返回后续响应" in result.build_output
    assert "清理动作: interrupt, close_response_stream, disconnect" in result.build_output
    assert events == [
        "__aenter__",
        "interrupt",
        "__aexit__",
        "__aenter__",
        "interrupt",
        "__aexit__",
        "__aenter__",
        "interrupt",
        "__aexit__",
    ]
    assert result.performance_metrics["continuation_retry_count"] == 2


def test_fix_issue_retries_when_issue_runtime_exceeds_deadline(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-hard-timeout",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: []),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    events: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            events.append("__aenter__")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("__aexit__")
            return None

        async def query(self, prompt):
            await asyncio.sleep(1)

        async def interrupt(self):
            events.append("interrupt")
            return None

        def receive_response(self):
            raise AssertionError("receive_response should not be reached before deadline")

    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())
    monkeypatch.setattr(claude_agent_module, "FIRST_RESPONSE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(claude_agent_module, "FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(claude_agent_module, "ISSUE_HARD_TIMEOUT_SECONDS", 0.01)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "model_timeout"
    assert result.error == "Model response timed out"
    assert "单个 issue 在" in result.build_output
    assert "清理动作: interrupt, disconnect" in result.build_output
    assert events == ["__aenter__", "interrupt", "__aexit__"]


def test_fix_issue_retries_when_forbidden_tool_is_used(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-forbidden-tool",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_attempt_head_changed",
        staticmethod(lambda workspace_path: False),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    class FakeToolUseBlock:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content = [FakeToolUseBlock("mcp__sonar-fix__git_commit")]

    class FakeResultMessage:
        def __init__(self) -> None:
            self.total_cost_usd = 0.1
            self.is_error = False
            self.result = ""
            self.errors = []

    async def fake_receive_response():
        yield FakeAssistantMessage()
        yield FakeResultMessage()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def query(self, prompt):
            return None

        def receive_response(self):
            return fake_receive_response()

    monkeypatch.setattr(claude_agent_module, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_agent_module, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude_agent_module, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(claude_agent_module, "ClaudeSDKClient", lambda options: FakeClient())

    monkeypatch.setattr(
        ClaudeFixAgent,
        "_run_local_build_fallback",
        classmethod(lambda cls, workspace_path, build_command: (True, "fallback build ok")),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.retryable_failure is True
    assert result.failure_kind == "forbidden_tool"
    assert result.error == "Forbidden tool used during issue fix"
    assert "git_commit" in result.build_output
    assert "fallback build ok" in result.build_output


def test_collect_modified_files_detects_attempt_local_commit(monkeypatch, tmp_path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    tracked_file = repo / "tracked.cs"
    tracked_file.write_text("class Foo {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.cs"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    ClaudeFixAgent._capture_attempt_workspace_state(repo)
    try:
        tracked_file.write_text("class Foo { int Value => 1; }\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.cs"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "attempt"], cwd=repo, check=True)

        changed_files = ClaudeFixAgent._collect_modified_files(repo)

        assert changed_files == ["tracked.cs"]
        assert ClaudeFixAgent._attempt_head_changed(repo) is True
    finally:
        ClaudeFixAgent._cleanup_attempt_workspace_state(repo)


def test_fix_issue_runs_build_when_scope_soft_drift_is_ignored(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3b",
        rule="csharpsquid:S3358",
        message="嵌套三元运算符",
        line=3,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_validate_issue_edit_scope",
        staticmethod(
            lambda workspace_path, issue, scope, **kwargs: "Issue changes exceeded the allowed Sonar edit scope."
        ),
    )
    _mock_role_compile_flow(monkeypatch)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    monkeypatch.setattr(claude_agent_module.anyio, "run", lambda func: None)

    build_calls: list[str] = []

    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "src/Foo.cs(3,1): error CS0103: name not found [Foo.csproj]"

    def fake_run(*args, **kwargs):
        build_calls.append("build")
        return FakeCompletedProcess()

    monkeypatch.setattr(claude_agent_module.subprocess, "run", fake_run)

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is False
    assert result.error == "Issue changes failed local build verification"
    assert "error CS0103" in result.build_output
    assert result.retryable_failure is True
    assert result.failure_kind == "build"
    assert build_calls == ["build"]


def test_fix_issue_salvages_patch_after_post_edit_timeout(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-salvage",
        rule="csharpsquid:S1481",
        message="移除未使用变量",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "        var unused = 1;",
                    "    }",
                    "}",
                    "",
                ]
            ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_runtime_run(self, request):
        source_file.write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        raise AgentRuntimeError(
            TimeoutError("模型在 180 秒内没有返回后续响应\n阶段分类: post_edit_stall"),
            AgentRuntimeResult(
                tool_uses=("Read", "Edit"),
                last_tool_name="Edit",
                total_duration_seconds=12.0,
                time_to_first_model_content_seconds=1.2,
                time_after_first_edit_to_finalize_seconds=10.8,
                tool_call_count=2,
                read_call_count=1,
                edit_call_count=1,
                timeout_stage="post_edit_stall",
                last_progress_stage="tool:Edit",
            ),
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert result.patch_salvaged is True
    assert result.model_timeout_stage == "post_edit_stall"
    assert result.performance_metrics["patch_salvaged"] is True
    assert result.performance_metrics["model_timeout_stage"] == "post_edit_stall"
    assert result.performance_metrics["build_invoked"] is True


def test_fix_issue_salvages_patch_after_max_turn_agent_error(monkeypatch, tmp_path) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-max-turn-salvage",
        rule="csharpsquid:S1481",
        message="删除未使用的局部变量",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var unused = 1;",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_runtime_run(self, request):
        source_file.write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return AgentRuntimeResult(
            tool_uses=("Read", "Edit"),
            last_tool_name="Edit",
            total_duration_seconds=9.0,
            time_to_first_model_content_seconds=1.0,
            time_after_first_edit_to_finalize_seconds=8.0,
            tool_call_count=2,
            read_call_count=1,
            edit_call_count=1,
            successful_edit_count=1,
            agent_error="Reached maximum number of turns (20)",
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert result.patch_salvaged is True
    assert result.performance_metrics["patch_salvaged"] is True
    assert result.performance_metrics["agent_error_salvaged"] is True
    assert result.performance_metrics["agent_error_salvage_reason"] == "agent_error_max_turns"


def test_fix_issue_scope_validation_ignores_previous_successful_changes_in_same_file(
    monkeypatch,
    tmp_path,
) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-3c",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=10,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
                [
                    "class Foo",
                    "{",
                    "    private int _value = 1;",
                    "",
                    "    /// <summary>",
                    "    /// Demo.",
                    "    /// </summary>",
                    "    public void Demo()",
                    "    {",
                    "        var now = DateTime.Now;",
                    "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Simulate a previous issue that already modified the same file before the current issue starts.
    source_file.write_text(
        "\n".join(
                [
                    "class Foo",
                    "{",
                    "    private int _value = 2;",
                    "",
                    "    /// <summary>",
                    "    /// Demo.",
                    "    /// </summary>",
                    "    public void Demo()",
                    "    {",
                    "        var now = DateTime.Now;",
                    "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )
    _mock_role_compile_flow(monkeypatch)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_run(func) -> None:
        source_file.write_text(
            "\n".join(
                    [
                        "class Foo",
                        "{",
                        "    private int _value = 2;",
                        "",
                        "    /// <summary>",
                        "    /// Demo.",
                        "    /// </summary>",
                        "    public void Demo()",
                        "    {",
                        "        var now = DateTime.UtcNow;",
                        "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(claude_agent_module.anyio, "run", fake_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert result.build_passed is True
    assert "allowed Sonar edit scope" not in result.build_output


def test_fix_issue_continues_same_issue_after_follow_up_timeout(
    monkeypatch,
    tmp_path,
) -> None:
    agent = ClaudeFixAgent(sonar_host="https://sonar.example", sonar_token="token")
    issue = SonarIssue(
        key="issue-continuation",
        rule="csharpsquid:S1481",
        message="Remove this unused local variable.",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var unused = 1;",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    _mock_role_compile_flow(monkeypatch)
    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    seen_prompts: list[str] = []
    seen_change_probes = {"count": 0}

    def fake_collect_modified_files(workspace_path):
        seen_change_probes["count"] += 1
        if len(seen_prompts) >= 2:
            return ["src/Foo.cs"]
        return []

    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(fake_collect_modified_files),
    )

    def fake_runtime_run(self, request):
        seen_prompts.append(request.user_prompt)
        if len(seen_prompts) == 1:
            raise AgentRuntimeError(
                TimeoutError("模型在 180 秒内没有返回后续响应\n阶段分类: post_read_stall"),
                AgentRuntimeResult(
                    tool_uses=("Read",),
                    last_tool_name="Read",
                    total_duration_seconds=8.0,
                    time_to_first_model_content_seconds=1.0,
                    tool_call_count=1,
                    read_call_count=1,
                    timeout_stage="post_read_stall",
                    last_progress_stage="tool:Read",
                    runtime_events=(
                        AttemptRuntimeEvent(
                            kind=AttemptRuntimeEventKind.ATTEMPT_STARTED,
                            sequence=1,
                            stage="initializing",
                        ),
                        AttemptRuntimeEvent(
                            kind=AttemptRuntimeEventKind.TOOL_CALLED,
                            sequence=2,
                            stage="tool:Read",
                            payload={
                                "tool_name": "Read",
                                "tool_payload": {"file_path": r"C:\GIT.NEWARE.WORK\BI\src\Foo.cs"},
                                "tool_preview": '{"file_path":"C:\\\\GIT.NEWARE.WORK\\\\BI\\\\src\\\\Foo.cs"}',
                                "read_preview": "   4 |     {\n   5 |         var unused = 1;",
                            },
                        ),
                    ),
                ),
            )

        source_file.write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "    }",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return AgentRuntimeResult(
            tool_uses=("Read", "Edit"),
            last_tool_name="Edit",
            total_duration_seconds=6.0,
            time_to_first_model_content_seconds=0.8,
            time_after_first_edit_to_finalize_seconds=2.0,
            tool_call_count=2,
            read_call_count=1,
            edit_call_count=1,
            continuation_retry_count=1,
            continuation_recovered=True,
            continuation_timeout_stages=("post_read_stall",),
            runtime_events=(
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_STARTED,
                    sequence=1,
                    stage="initializing",
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.TOOL_CALLED,
                    sequence=2,
                    stage="tool:Edit",
                    payload={"tool_name": "Edit"},
                ),
                AttemptRuntimeEvent(
                    kind=AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    sequence=3,
                    stage="completed",
                    payload={"success": True},
                ),
            ),
        )

    monkeypatch.setattr(claude_agent_module.AgentRuntime, "run", fake_runtime_run)

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert len(seen_prompts) == 2
    assert "【继续上一轮修复，不要从头分析】" in seen_prompts[1]
    assert "绝对路径" in seen_prompts[1]
    assert "最近已读取的关键代码片段" in seen_prompts[1]
    assert result.performance_metrics["continuation_retry_count"] == 1
    assert result.performance_metrics["continuation_recovered"] is True
    assert "post_read_stall" in result.performance_metrics["continuation_timeout_stages"]
    assert any(
        event.kind == AttemptRuntimeEventKind.CONTINUATION_REQUESTED
        for event in result.attempt_events
    )


def test_scope_validation_rejects_out_of_scope_lines() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-4",
            rule="csharpsquid:S6562",
            message="DateTime 应显式指定 Kind",
            line=4,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "public void Demo()",
            "{",
            '    var name = "demo";',
            "    var cuffOffDate = new DateTime(",
            "        2024,",
            "        6,",
            "        1);",
            "    var another = DateTime.Now;",
            "}",
        ],
    )

    changed_lines = ClaudeFixAgent._extract_changed_line_numbers(
        "\n".join(
            [
                "@@ -4,1 +4,1 @@",
                "-    var cuffOffDate = new DateTime(",
                "+    var cuffOffDate = DateTime.SpecifyKind(",
                "@@ -8,1 +8,1 @@",
                "-    var another = DateTime.Now;",
                "+    var another = DateTime.UtcNow;",
            ]
        )
    )
    offending_lines = ClaudeFixAgent._find_out_of_scope_lines(scope, changed_lines)

    assert scope.start_line == 4
    assert scope.validation_start_line == 4
    assert scope.validation_end_line == 7
    assert offending_lines == [8]


def test_scope_validation_uses_before_coordinates_for_pure_delete() -> None:
    scope = IssueEditScope(
        start_line=2224,
        end_line=2224,
        validation_start_line=2224,
        validation_end_line=2224,
        mode="statement",
    )

    changed_lines = ClaudeFixAgent._extract_changed_line_numbers(
        "\n".join(
            [
                "@@ -2224 +2223,0 @@",
                "-        var orderNum = result.OrderNum();",
            ]
        )
    )

    offending_lines = ClaudeFixAgent._find_out_of_scope_lines(scope, changed_lines)

    assert changed_lines == {2224}
    assert offending_lines == []


def test_build_issue_edit_scope_expands_window_for_s125_adjacent_cleanup() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-s125",
            rule="csharpsquid:S125",
            message="移除被注释掉的代码段",
            line=6,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "class Foo",
            "{",
            "    void Demo()",
            "    {",
            "        var temp = 1;",
            "        // old code",
            "        Run();",
            "    }",
            "}",
        ],
    )

    assert scope.mode == "statement"
    assert scope.validation_start_line < 6
    assert scope.validation_end_line >= 6


def test_build_issue_edit_scope_supports_control_block_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-block",
            rule="csharpsquid:S2681",
            message="多行代码块必须使用大括号",
            line=2,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "public void Demo()",
            "if (enabled)",
            "    Run();",
            "return;",
        ],
    )

    assert scope.mode == CONTROL_BLOCK_SCOPE_MODE
    assert scope.start_line == 2
    assert scope.end_line == 3
    assert scope.validation_start_line == 2
    assert scope.validation_end_line == 4


def test_build_issue_edit_scope_supports_declaration_comment_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-doc",
            rule="external_roslyn:CS1591",
            message="公开成员缺少 XML 注释",
            line=4,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "namespace Demo",
            "{",
            "    [HttpGet]",
            "    public IActionResult List(",
            "        int id)",
            "    {",
            "        return Ok(id);",
            "    }",
            "}",
        ],
    )

    assert scope.mode == DECLARATION_COMMENT_SCOPE_MODE
    assert scope.start_line == 3
    assert scope.end_line == 6
    assert scope.validation_start_line == 1
    assert scope.validation_end_line == 9


def test_build_issue_edit_scope_supports_conditional_chain_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-conditional",
            rule="csharpsquid:S1871",
            message="两个分支实现相同",
            line=7,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "if (a)",
            "{",
            "    Foo();",
            "}",
            "else if (b)",
            "{",
            "    Foo();",
            "}",
            "else",
            "{",
            "    Foo();",
            "}",
        ],
    )

    assert scope.mode == CONDITIONAL_CHAIN_SCOPE_MODE
    assert scope.start_line == 1
    assert scope.end_line == 12
    assert scope.validation_start_line == 1
    assert scope.validation_end_line == 12


def test_build_issue_edit_scope_supports_expression_rewrite_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-expression",
            rule="csharpsquid:S3358",
            message="嵌套三元运算符",
            line=5,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "var result = items.Select(x => new",
            "{",
            "    Name = x.Name,",
            "    Score = foo",
            "        ? (bar ? 1 : 0)",
            "        : 2,",
            "}).ToList();",
        ],
    )

    assert scope.mode == EXPRESSION_REWRITE_SCOPE_MODE
    assert scope.start_line == 1
    assert scope.end_line == 7
    assert scope.validation_start_line == 1
    assert scope.validation_end_line == 7


def test_build_issue_edit_scope_supports_loop_rewrite_scope() -> None:
    scope = ClaudeFixAgent._build_issue_edit_scope(
        SonarIssue(
            key="issue-loop",
            rule="csharpsquid:S3267",
            message="循环可简化为 LINQ",
            line=4,
            component="BI:src/Foo.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        ),
        [
            "private static string? FindMatch(IEnumerable<Item> items, string target)",
            "{",
            "    foreach (var item in items)",
            "    {",
            "        if (item.Name == target)",
            "        {",
            "            return item.Name;",
            "        }",
            "    }",
            "",
            "    return null;",
            "}",
        ],
    )

    assert scope.mode == LOOP_REWRITE_SCOPE_MODE
    assert scope.start_line == 3
    assert scope.end_line == 11
    assert scope.validation_start_line == 3
    assert scope.validation_end_line == 12
