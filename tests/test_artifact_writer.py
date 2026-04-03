from __future__ import annotations

import subprocess
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import FixResult, SonarIssue
from pi_sonar_agent.core.artifact_writer import ArtifactWriter
from pi_sonar_agent.core.diff_reviewer import ReviewerResult
from pi_sonar_agent.core.issue_contract import ContractTargetSymbol, EditContract
from pi_sonar_agent.core.issue_retry import capture_workspace_baseline, cleanup_workspace_baseline
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.state import (
    AttemptState,
    AttemptStatus,
    IssueState,
    IssueStatus,
    RetryReason,
    RunState,
    RunStatus,
    TargetState,
    TargetStatus,
)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )


def test_artifact_writer_writes_attempt_bundle_and_issue_summary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")

    tracked_file = repo / "tracked.cs"
    tracked_file.write_text("class Foo {}\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.cs")
    _run_git(repo, "commit", "-m", "init")

    baseline = capture_workspace_baseline(
        repo,
        repository="repo",
        issue_key="ISSUE-1",
        run_label="run1",
        snapshot_root=str(tmp_path / "snapshots"),
    )
    try:
        tracked_file.write_text("class Foo { int Value => 1; }\n", encoding="utf-8")

        issue = SonarIssue(
            key="ISSUE-1",
            rule="csharpsquid:S1481",
            message="Remove this unused local variable.",
            line=1,
            component="BI:tracked.cs",
            severity="MAJOR",
            issue_type="CODE_SMELL",
        )
        result = FixResult(
            success=False,
            issue_key=issue.key,
            file_path=issue.file_path,
            changes=[{"file": "tracked.cs", "action": "modified"}],
            build_passed=False,
            build_verification_failed=True,
            error="Issue changes failed local build verification",
            summary="Fixed 1 file(s)",
            build_command='dotnet build "tracked.sln"',
            build_output="build failed",
            attempts=1,
            retryable_failure=True,
            failure_kind="build",
            edit_contract=EditContract(
                issue_key=issue.key,
                rule_id=issue.rule,
                guardrail_mode="contract_review",
                target_files=("tracked.cs",),
                target_symbols=(
                    ContractTargetSymbol(
                        file="tracked.cs",
                        symbol="statement@1-1",
                        reason="Sonar issue is located near line 1",
                        start_line=1,
                        end_line=1,
                    ),
                ),
                allowed_change_kinds=("direct-fix",),
                forbidden_change_kinds=("drive-by-refactor",),
                validation_plan=("build", "diff_review"),
                review_hints=("flag unrelated edits in the same file",),
                scope_mode="statement",
                target_line_range=(1, 1),
                validation_line_range=(1, 1),
            ),
            reviewer_result=ReviewerResult(
                status="pass",
                guardrail_mode="contract_review",
                summary="Patch stays inside the declared issue contract.",
                metrics={"changed_file_count": 1},
            ).to_dict(),
            guardrail_mode="contract_review",
        )
        attempt_state = AttemptState(
            attempt_number=1,
            status=AttemptStatus.RETRYING,
            started_at="2026-04-03T10:00:00+00:00",
            finished_at="2026-04-03T10:00:10+00:00",
            duration_seconds=10.0,
            failure_kind="build",
            retry_reason=RetryReason.BUILD_VERIFICATION_FAILED,
            retryable_failure=True,
            build_passed=False,
            build_verification_failed=True,
            error=result.error or "",
            summary=result.summary,
            changed_files=("tracked.cs",),
            artifact_dir=(tmp_path / "artifacts" / "repo" / "run1" / "ISSUE-1" / "attempt-01").as_posix(),
        )

        writer = ArtifactWriter(root=tmp_path / "artifacts")
        retry_context = RetryContext(
            source_attempt_number=1,
            failure_kind="build",
            error=result.error or "",
            raw_output="build failed",
            changed_files=("tracked.cs",),
        )
        bundle = writer.write_attempt_artifacts(
            repository="repo",
            run_label="run1",
            issue=issue,
            attempt_state=attempt_state,
            result=result,
            workspace_path=repo,
            baseline=baseline,
            build_command='dotnet build "tracked.sln"',
            retry_feedback="fix the build error first",
            retry_context=retry_context,
        )

        issue_state = IssueState(
            issue_key=issue.key,
            repository="repo",
            run_label="run1",
            rule_id=issue.rule,
            file_path=issue.file_path,
            line=issue.line,
            status=IssueStatus.SKIPPED,
            attempts=(attempt_state,),
            final_failure_kind="build",
            final_error=result.error or "",
            final_skip_reason="Build verification failed after 1 attempt(s)",
            final_summary=result.summary,
            artifact_root=bundle.issue_root.as_posix(),
        )
        summary_path = writer.write_issue_state(issue_state)

        assert bundle.issue_json.exists()
        assert bundle.edit_contract_json.exists()
        assert bundle.prompt_context_json.exists()
        assert bundle.patch_diff.exists()
        assert bundle.reviewer_result_json.exists()
        assert bundle.build_result_json.exists()
        assert bundle.attempt_summary_json.exists()
        assert summary_path.exists()
        prompt_context = bundle.prompt_context_json.read_text(encoding="utf-8")
        assert '"failure_kind": "build"' in prompt_context
        assert '"source_attempt_number": 1' in prompt_context
        assert '"guardrail_mode": "contract_review"' in prompt_context
        edit_contract = bundle.edit_contract_json.read_text(encoding="utf-8")
        assert '"issue_key": "ISSUE-1"' in edit_contract
        assert '"target_files": [' in edit_contract
        reviewer_result = bundle.reviewer_result_json.read_text(encoding="utf-8")
        assert '"status": "pass"' in reviewer_result
        patch_text = bundle.patch_diff.read_text(encoding="utf-8")
        assert "--- a/tracked.cs" in patch_text
        assert "+++ b/tracked.cs" in patch_text
        assert "class Foo { int Value => 1; }" in patch_text
    finally:
        cleanup_workspace_baseline(baseline)


def test_artifact_writer_writes_target_and_run_summary(tmp_path: Path) -> None:
    writer = ArtifactWriter(
        root=tmp_path / "issue-artifacts",
        run_root=tmp_path / "run-artifacts",
    )
    issue_state = IssueState(
        issue_key="ISSUE-1",
        repository="repo",
        run_label="run1",
        rule_id="csharpsquid:S1481",
        file_path="/tracked.cs",
        line=10,
        status=IssueStatus.FIXED,
        artifact_root=(tmp_path / "issue-artifacts" / "repo" / "run1" / "ISSUE-1").as_posix(),
    )
    target_state = TargetState(
        run_label="run1",
        project_key="project-a",
        repository="repo",
        author="alice@example.com",
        base_branch="main",
        status=TargetStatus.PARTIAL,
        issues=(issue_state,),
        started_at="2026-04-03T10:00:00+00:00",
        finished_at="2026-04-03T10:05:00+00:00",
    )
    run_state = RunState(
        run_label="run1",
        status=RunStatus.PARTIAL,
        targets=(target_state,),
        started_at="2026-04-03T10:00:00+00:00",
        finished_at="2026-04-03T10:05:00+00:00",
    )

    target_summary_path = writer.write_target_state(target_state)
    run_summary_path = writer.write_run_state(run_state)

    assert target_summary_path.exists()
    assert run_summary_path.exists()
    assert "alice@example.com" in target_summary_path.read_text(encoding="utf-8")
    assert '"status": "partial"' in run_summary_path.read_text(encoding="utf-8")
