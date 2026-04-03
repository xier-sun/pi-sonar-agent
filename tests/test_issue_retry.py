from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import FixResult, SonarIssue
from pi_sonar_agent.core.issue_retry import (
    build_retry_feedback,
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


def test_restore_workspace_baseline_removes_attempt_commit_pollution(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    baseline = capture_workspace_baseline(
        repo,
        repository="repo",
        issue_key="ISSUE-COMMIT",
        run_label="run-commit",
        snapshot_root=str(tmp_path / "snapshots"),
    )
    try:
        tracked_file = repo / "tracked.txt"
        tracked_file.write_text("attempt change\n", encoding="utf-8")
        _run_git(repo, "add", "tracked.txt")
        _run_git(repo, "commit", "-m", "attempt commit")

        restore_workspace_baseline(repo, baseline)

        assert _run_git(repo, "rev-parse", "HEAD").stdout.strip() == baseline.head_commit
        assert tracked_file.read_text(encoding="utf-8") == "original\n"
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
                retryable_failure=True,
                failure_kind="build",
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
    assert Path(result.artifact_root).exists()
    assert result.issue_state is not None
    assert result.issue_state.status.value == "skipped"
    assert tracked_file.read_text(encoding="utf-8") == "previous success\n"
    assert agent.retry_feedbacks[0] == ""
    assert "关键编译错误" in agent.retry_feedbacks[1]
    assert "CS0103" in agent.retry_feedbacks[1]
    assert "出错代码片段" in agent.retry_feedbacks[1]
    assert "不要引用未定义的变量" in agent.retry_feedbacks[1]
    assert "关键编译错误" in agent.retry_feedbacks[2]
    issue_summary = json.loads((Path(result.artifact_root) / "issue_summary.json").read_text(encoding="utf-8"))
    assert issue_summary["status"] == "skipped"
    assert len(issue_summary["attempts"]) == 3
    assert issue_summary["attempts"][0]["status"] == "retrying"
    assert issue_summary["attempts"][1]["status"] == "retrying"
    assert issue_summary["attempts"][2]["status"] == "skipped"
    second_prompt_context = json.loads(
        (Path(result.artifact_root) / "attempt-02" / "prompt_context.json").read_text(encoding="utf-8")
    )
    assert second_prompt_context["retry_context"]["failure_kind"] == "build"
    assert second_prompt_context["retry_context"]["source_attempt_number"] == 1
    assert second_prompt_context["retry_context"]["compiler_errors"][0]["code"] == "CS0103"
    assert (Path(result.artifact_root) / "attempt-01" / "issue.json").exists()
    assert (Path(result.artifact_root) / "attempt-01" / "attempt_summary.json").exists()
    assert (Path(result.artifact_root) / "attempt-01" / "build_result.json").exists()
    assert (Path(result.artifact_root) / "attempt-01" / "patch.diff").exists()


def test_process_issue_with_retries_retries_when_agent_makes_no_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    issue = SonarIssue(
        key="issue-no-change",
        rule="csharpsquid:S125",
        message="注释代码",
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
                error="Agent completed without modifying any files",
                summary="Fixed 0 file(s)",
                retryable_failure=True,
                failure_kind="no_change",
            )

    agent = FakeAgent()

    result = process_issue_with_retries(
        agent=agent,
        issue=issue,
        workspace_path=repo,
        build_command='dotnet build "tracked.sln"',
        repository="repo",
        run_label="run-no-change",
        max_build_retries=3,
    )

    assert result.success is False
    assert result.skipped is True
    assert result.skip_reason == "Agent completed without modifying any files after 3 attempt(s)"
    assert result.attempts == 3
    assert "上次尝试没有实际修改任何文件" in agent.retry_feedbacks[1]
    assert "必须对 Sonar 指向的代码真正落盘修改" in agent.retry_feedbacks[2]


def test_build_retry_feedback_preserves_scope_violation_details(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = FixResult(
        success=False,
        issue_key="issue-scope",
        file_path="tracked.cs",
        build_passed=False,
        build_verification_failed=True,
        error="Issue changes exceeded allowed scope",
        build_command='dotnet build "tracked.sln"',
        build_output=(
            "Issue changes exceeded the allowed Sonar edit scope.\n"
            "Allowed lines: 72-72\n"
            "Changed lines outside scope: 141, 186\n"
            "只允许修复 Sonar 指向的这一处代码，不要顺手修改本文件其他同类位置。"
        ),
    )

    feedback = build_retry_feedback(repo, result)

    assert "修改了 Sonar 指定范围之外的代码" in feedback
    assert "Allowed lines: 72-72" in feedback
    assert "Changed lines outside scope: 141, 186" in feedback
    assert "如果这是行级问题，只修改包含 issue 行的那条语句。" in feedback


def test_build_retry_feedback_guides_s3358_toward_local_expression_rewrite(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    issue = SonarIssue(
        key="issue-s3358",
        rule="csharpsquid:S3358",
        message="Extract this nested ternary operation into an independent statement.",
        line=92,
        component="BI:tracked.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    result = FixResult(
        success=False,
        issue_key="issue-s3358",
        file_path="tracked.cs",
        build_passed=True,
        build_verification_failed=False,
        error="Issue changes exceeded allowed scope",
        build_command='dotnet build "tracked.sln"',
        build_output=(
            "Issue changes exceeded the allowed Sonar edit scope.\n"
            "Allowed lines: 88-112\n"
            "Changed lines outside scope: 182, 183, 184\n"
            "只允许修复 Sonar 指向的这一处代码，不要顺手修改本文件其他同类位置。"
        ),
        retryable_failure=True,
        failure_kind="scope",
    )

    feedback = build_retry_feedback(repo, result, issue)

    assert "局部变量、if/else 或语句 lambda" in feedback
    assert "不要新增类级 private/helper 方法" in feedback
    assert "LINQ Select/匿名对象初始化" in feedback


def test_build_retry_feedback_combines_compiler_errors_and_scope_details(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    failing_file = repo / "tracked.cs"
    failing_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = FixResult(
        success=False,
        issue_key="issue-build-scope",
        file_path="tracked.cs",
        build_passed=False,
        build_verification_failed=True,
        error="Issue changes failed local build verification",
        build_command='dotnet build "tracked.sln"',
        build_output=(
            f"{failing_file}(2,1): error CS0103: name not found [tracked.csproj]\n"
            "Issue changes exceeded the allowed Sonar edit scope.\n"
            "Allowed lines: 72-72\n"
            "Changed lines outside scope: 141, 186\n"
            "只允许修复 Sonar 指向的这一处代码，不要顺手修改本文件其他同类位置。"
        ),
        retryable_failure=True,
        failure_kind="build",
    )

    feedback = build_retry_feedback(repo, result)

    assert "关键编译错误" in feedback
    assert "CS0103" in feedback
    assert "另外，上次修改还越过了 Sonar 允许范围" in feedback
    assert "Changed lines outside scope: 141, 186" in feedback


def test_build_retry_feedback_includes_build_tool_failure_context(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    failing_file = repo / "tracked.cs"
    failing_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = FixResult(
        success=False,
        issue_key="issue-build-tool",
        file_path="tracked.cs",
        build_passed=False,
        build_verification_failed=True,
        error="Build tool execution failed",
        build_command='dotnet build "tracked.sln"',
        build_output=(
            "run_build 工具执行异常。\n\n"
            "Command failed with exit code 3 (exit code: 3)\nError output: build stderr\n\n"
            "本地回退构建 Exit code: 1\n\n"
            f"{failing_file}(2,1): error CS0103: name not found [tracked.csproj]"
        ),
        retryable_failure=True,
        failure_kind="build_tool",
    )

    feedback = build_retry_feedback(repo, result)

    assert "运行构建工具时异常退出" in feedback
    assert "关键编译错误" in feedback
    assert "CS0103" in feedback


def test_build_retry_feedback_includes_forbidden_tool_constraints(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = FixResult(
        success=False,
        issue_key="issue-forbidden-tool",
        file_path="tracked.cs",
        build_passed=True,
        build_verification_failed=False,
        error="Forbidden tool used during issue fix",
        build_command='dotnet build "tracked.sln"',
        build_output=(
            "修复阶段使用了被禁止的工具，当前尝试已作废。\n\n"
            "禁止工具: Bash, mcp__sonar-fix__git_commit\n\n"
            "fallback build ok"
        ),
        retryable_failure=True,
        failure_kind="forbidden_tool",
    )

    feedback = build_retry_feedback(repo, result)

    assert "被禁止的工具" in feedback
    assert "严禁使用 Bash、git_add、git_commit、git_push" in feedback
    assert "提交由外层流程统一处理" in feedback


def test_build_retry_feedback_includes_model_timeout_constraints(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = FixResult(
        success=False,
        issue_key="issue-model-timeout",
        file_path="tracked.cs",
        build_passed=False,
        build_verification_failed=False,
        error="Model response timed out",
        build_command='dotnet build "tracked.sln"',
        build_output="模型在 120 秒内没有返回首个响应",
        retryable_failure=True,
        failure_kind="model_timeout",
    )

    feedback = build_retry_feedback(repo, result)

    assert "等待模型首响应时超时" in feedback
    assert ".env 中的模型 endpoint、token 和 provider 兼容性" in feedback


def test_build_retry_feedback_distinguishes_follow_up_response_timeout(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = FixResult(
        success=False,
        issue_key="issue-model-timeout-follow-up",
        file_path="tracked.cs",
        build_passed=False,
        build_verification_failed=False,
        error="Model response timed out",
        build_command='dotnet build "tracked.sln"',
        build_output="模型在 180 秒内没有返回后续响应",
        retryable_failure=True,
        failure_kind="model_timeout",
    )

    feedback = build_retry_feedback(repo, result)

    assert "等待模型后续响应时超时" in feedback
