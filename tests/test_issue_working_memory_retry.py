from __future__ import annotations

from pathlib import Path
import subprocess

from pi_sonar_agent.agent.claude_agent import FixResult, SonarIssue
from pi_sonar_agent.core.issue_retry import process_issue_with_retries
from pi_sonar_agent.core.lessons_store import LessonsStore


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
    (repo / "tracked.cs").write_text("class Foo { }\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.cs")
    _run_git(repo, "commit", "-m", "init")


def test_process_issue_with_retries_passes_working_memory_between_attempts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    tracked_file = repo / "tracked.cs"

    issue = SonarIssue(
        key="issue-memory",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=1,
        component="BI:tracked.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    class FakeAgent:
        def __init__(self) -> None:
            self.memories = []
            self.calls = 0

        def fix_issue(
            self,
            issue,
            workspace_path,
            build_command,
            retry_feedback="",
            retry_context=None,
            working_memory=None,
        ):
            self.calls += 1
            self.memories.append(working_memory)
            if self.calls == 1:
                tracked_file.write_text("class Foo { int Bar() => 1; }\n", encoding="utf-8")
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
                    retryable_failure=True,
                    failure_kind="build",
                )
            return FixResult(
                success=True,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=[{"file": "tracked.cs", "action": "modified"}],
                build_passed=True,
                summary="Fixed 1 file(s)",
                build_command=build_command,
                build_output="build passed",
            )

    agent = FakeAgent()
    result = process_issue_with_retries(
        agent=agent,
        issue=issue,
        workspace_path=repo,
        build_command='dotnet build "tracked.sln"',
        repository="repo",
        run_label="wm-run",
        lessons_store=LessonsStore(tmp_path / "lessons"),
        max_build_retries=2,
    )

    assert result.success is True
    assert agent.calls == 2
    assert agent.memories[0] is not None
    assert agent.memories[0].authoritative_workspace_state == "issue_baseline"
    assert agent.memories[1] is not None
    assert agent.memories[1].authoritative_workspace_state == "issue_baseline"
    assert "上一轮 patch 已撤销" in agent.memories[1].rollback_reason
    assert "Issue changes failed local build verification" in agent.memories[1].latest_retryable_failure
    assert agent.memories[1].stale_evidence
    assert "CS0103" in agent.memories[1].stale_evidence[0]

    working_memory_path = (
        repo
        / ".git"
        / "pi-sonar-agent-runtime"
        / "issues"
        / "issue-memory"
        / "working-memory.json"
    )
    assert working_memory_path.exists()
    payload = working_memory_path.read_text(encoding="utf-8")
    assert '"authoritative_workspace_state": "fixed_patch"' in payload
    evidence_index_path = (
        repo
        / ".git"
        / "pi-sonar-agent-runtime"
        / "issues"
        / "issue-memory"
        / "evidence-index.json"
    )
    assert evidence_index_path.exists()
    evidence_payload = evidence_index_path.read_text(encoding="utf-8")
    assert '"status": "stale"' in evidence_payload
    assert '"superseded_by": "restored_issue_baseline"' in evidence_payload
