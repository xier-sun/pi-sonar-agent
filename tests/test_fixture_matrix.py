from __future__ import annotations

from pathlib import Path

from pi_sonar_agent.core.issue_planner import IssuePlanner
from pi_sonar_agent.core.repo_capability import detect_repo_capability
from pi_sonar_agent.fixers.deterministic import IssueGroup
from pi_sonar_agent.fixers.roslyn import RoslynFixEngine

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "repos"


def _issue_group(file_path: str, line: int) -> IssueGroup:
    return IssueGroup(
        group_key="fixture-issue",
        file_path=file_path,
        rule="csharpsquid:S107",
        issues=(
            {
                "key": "fixture-issue",
                "line": line,
                "textRange": {"startLine": line, "endLine": line},
                "message": "Methods should not have too many parameters",
            },
        ),
        start_line=line,
        end_line=line,
        symbol_names=(),
    )


def test_fixture_repo_capability_matrix_detects_legacy_netcoreapp31() -> None:
    workspace = FIXTURE_ROOT / "netcoreapp31_legacy"

    profile = detect_repo_capability(workspace)

    assert profile.supports_record is False
    assert profile.supports_init_only is False
    assert "netcoreapp3.1" in profile.summary()


def test_fixture_matrix_roslyn_identifies_internal_s107_candidate() -> None:
    workspace = FIXTURE_ROOT / "s107_internal_candidate"
    engine = RoslynFixEngine()

    result = engine.apply_solution_fix(
        workspace_path=str(workspace),
        solution_path="",
        issue_group=_issue_group("InternalApp/InternalService.cs", 7),
        primary_issue={"message": "Methods should not have too many parameters"},
    )

    assert result.can_fix_safely is True
    assert result.strategy == "roslyn:s107_candidate_identified"


def test_fixture_matrix_planner_and_roslyn_keep_public_surface_stable() -> None:
    workspace = FIXTURE_ROOT / "s107_public_interface_risk"
    source_file = workspace / "PublicApp" / "PublicService.cs"
    source_lines = tuple(source_file.read_text(encoding="utf-8").splitlines())

    plan = IssuePlanner.plan_issue(
        issue_key="fixture-s3776-public-risk",
        rule_id="csharpsquid:S3776",
        file_path="PublicApp/PublicService.cs",
        issue_line=7,
        guardrail_mode="scope",
        scope_mode="method",
        scope_start_line=7,
        scope_end_line=10,
        validation_start_line=7,
        validation_end_line=10,
        source_lines=source_lines,
        workspace_path=workspace,
    )
    engine = RoslynFixEngine()
    result = engine.apply_solution_fix(
        workspace_path=str(workspace),
        solution_path="",
        issue_group=_issue_group("PublicApp/PublicService.cs", 7),
        primary_issue={"message": "Methods should not have too many parameters"},
    )

    assert plan.edit_contract.repair_plan is not None
    assert plan.edit_contract.repair_plan.requires_signature_change is False
    assert "avoid_interface_controller_propagation" in plan.edit_contract.repair_plan.strategy_preferences
    assert result.can_fix_safely is False
    assert "interface_propagation_target" in result.safety_flags
