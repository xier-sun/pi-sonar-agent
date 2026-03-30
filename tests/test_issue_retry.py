from __future__ import annotations

import subprocess
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import FixResult, SonarIssue
from pi_sonar_agent.core.issue_retry import (
    capture_workspace_baseline,
    cleanup_workspace_baseline,
    process_issue_with_retries,
    restore_workspace_baseline,
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


def _init_git_repo(repo: Path) -> None:
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")

    (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.txt")
    _run_git(repo, "commit", "-m", "init")


def test_restore_workspace_baseline_preserves_previous_successful_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    tracked_file = repo / "tracked.txt"
    tracked_file.write_text("successful change\n", encoding="utf-8")
    baseline_untracked = repo / "keep.txt"
    baseline_untracked.write_text("keep me\n", encoding="utf-8")

    baseline = capture_workspace_baseline(
        repo,
        repository="repo",
        issue_key="ISSUE-1",
        run_label="run1",
        snapshot_root=str(tmp_path / "snapshots"),
    )
    try:
        tracked_file.write_text("bad change\n", encoding="utf-8")
        (repo / "bad.txt").write_text("remove me\n", encoding="utf-8")

        restore_workspace_baseline(repo, baseline)

        assert tracked_file.read_text(encoding="utf-8") == "successful change\n"
        assert baseline_untracked.read_text(encoding="utf-8") == "keep me\n"
        assert not (repo / "bad.txt").exists()
    finally:
        cleanup_workspace_baseline(baseline)


def test_process_issue_with_retries_skips_after_three_build_failures(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    tracked_file = repo / "tracked.cs"
    tracked_file.write_text("previous success\n", encoding="utf-8")

    issue = SonarIssue(
        key="issue-1",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=1,
        component="BI:tracked.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    class FakeAgent:
        def __init__(self) -> None:
            self.retry_feedbacks: list[str] = []

        def fix_issue(self, issue, workspace_path, build_command, retry_feedback=""):
            self.retry_feedbacks.append(retry_feedback)
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=[{"file": "tracked.cs", "action": "modified"}],
                build_passed=False,
                build_verification_failed=True,
                error="Issue changes failed local build verification",
                build_command=build_command,
                build_output=f"{tracked_file}(1,1): error CS0103: name not found [tracked.csproj]",
            )

    agent = FakeAgent()

    result = process_issue_with_retries(
        agent=agent,
        issue=issue,
        workspace_path=repo,
        build_command='dotnet build "tracked.sln"',
        repository="repo",
        run_label="run2",
        max_build_retries=3,
    )

    assert result.success is False
    assert result.skipped is True
    assert result.skip_reason == "Build verification failed after 3 attempt(s)"
    assert result.attempts == 3
    assert Path(result.issue_log_path).exists()
    assert tracked_file.read_text(encoding="utf-8") == "previous success\n"
    assert agent.retry_feedbacks[0] == ""
    assert "关键编译错误" in agent.retry_feedbacks[1]
    assert "CS0103" in agent.retry_feedbacks[1]
    assert "出错代码片段" in agent.retry_feedbacks[1]
    assert "不要引用未定义的变量" in agent.retry_feedbacks[1]
    assert "关键编译错误" in agent.retry_feedbacks[2]
