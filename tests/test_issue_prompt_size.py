from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.issue_prompt import IssuePromptBuilder


def test_build_system_prompt_result_stays_within_budget(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "# Repo Rules\n\n" + "\n".join(f"- rule {index}: keep patch small" for index in range(1200)),
        encoding="utf-8",
    )

    result = IssuePromptBuilder.build_system_prompt_result(tmp_path)

    assert len(result.prompt) <= IssuePromptBuilder.SYSTEM_PROMPT_TARGET_CHARS
    assert result.target_chars == IssuePromptBuilder.SYSTEM_PROMPT_TARGET_CHARS


def test_build_user_prompt_result_externalizes_large_sections_and_tracks_budget(
    tmp_path: Path,
) -> None:
    issue = SonarIssue(
        key="issue-large-budget",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=18,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )
    large_gate = "\n".join(f"- gate {index}: keep patch small" for index in range(500))
    large_contract = "【Edit Contract】\n" + ("- 最小修改\n" * 240)
    large_plan = "【Repair Plan】\n" + ("- 先在原方法内收口\n" * 240)
    large_prefetched = "【预取上下文】\n" + ("- snippet\ncontext line\n" * 120)

    result = IssuePromptBuilder.build_user_prompt_result(
        issue,
        "  18 | if (condition) { ... }\n" * 120,
        large_gate,
        "- 只允许修改当前方法。",
        {
            "name": "Cognitive Complexity of methods should not be too high",
            "description": "嵌套条件和循环会提高认知复杂度。" * 120,
            "how_to_fix": "提取私有方法，减少嵌套层级。" * 120,
        },
        'dotnet build "src/Foo.sln"',
        edit_contract_section=large_contract,
        repair_plan_section=large_plan,
        prefetched_context_section=large_prefetched,
        workspace_path=tmp_path,
    )

    reference_file = tmp_path / ".pi-sonar-agent-runtime" / "sonar_fix_reference.md"

    assert len(result.prompt) <= IssuePromptBuilder.USER_PROMPT_TARGET_CHARS
    assert reference_file.exists()
    assert "quality_gate_section" in result.externalized_sections
    assert "repair_plan_section" in result.externalized_sections
    assert result.reference_document_path == ".pi-sonar-agent-runtime/sonar_fix_reference.md"
