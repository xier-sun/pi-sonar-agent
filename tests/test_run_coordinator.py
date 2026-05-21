from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pi_sonar_agent.core.run_coordinator as run_coordinator_module
from pi_sonar_agent.agent.claude_agent import FixResult
from pi_sonar_agent.core.model_env import ModelTierConfig
from pi_sonar_agent.core.perf_flags import PerformanceFlags
from pi_sonar_agent.core.preflight import RuntimeEnvironment
from pi_sonar_agent.core.run_coordinator import RunCoordinator, TargetRunOptions
from pi_sonar_agent.core.state import AttemptState, AttemptStatus, IssueState, IssueStatus
from pi_sonar_agent.core.state_store import RunStateStore
from pi_sonar_agent.core.target_config import TargetConfig


def test_run_coordinator_runs_workspace_and_branch_preflight(monkeypatch, tmp_path, capsys) -> None:
    calls: list[tuple[str, object]] = []
    target_states = []

    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="release/2026.04",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=0,
    )

    monkeypatch.setattr(
        run_coordinator_module,
        "ensure_workspace_writable",
        lambda workspace_root: calls.append(("workspace", workspace_root)),
    )
    monkeypatch.setattr(
        run_coordinator_module,
        "ensure_remote_branch_exists",
        lambda **kwargs: calls.append(("branch", kwargs["branch"])),
    )
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            target_states.append(target_state)
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            calls.append(("gateway", remote_url))

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            raise AssertionError("clone should not be reached when there are no issues")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return []

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260403150000", show_banner=False),
    )

    assert result.issues == 0
    assert result.ok is True
    assert result.status == "succeeded"
    assert result.target_state is not None
    assert result.target_state.status.value == "succeeded"
    assert Path(result.target_summary_path).exists()
    assert len(target_states) == 1
    assert calls == [
        ("workspace", tmp_path / "workspaces"),
        ("gateway", "https://dev.azure.com/acme/project/_git/repo-a"),
        ("branch", "release/2026.04"),
    ]
    output = capsys.readouterr().out
    assert "启动前校验通过" in output


def test_run_coordinator_skips_global_workspace_prune_when_disabled(
    monkeypatch, tmp_path, capsys
) -> None:
    calls: list[str] = []

    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=0,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)

    def fake_prune(*args, **kwargs):
        calls.append("pruned")
        return SimpleNamespace(removed=(), failed=())

    monkeypatch.setattr(run_coordinator_module, "prune_old_workspaces", fake_prune)

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            raise AssertionError("clone should not be reached when there are no issues")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return []

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260403150000", show_banner=False, prune_workspaces=False),
    )

    assert result.ok is True
    assert calls == []
    _ = capsys.readouterr().out


def test_run_coordinator_filters_issues_by_configured_issue_keys(monkeypatch, tmp_path, capsys) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=0,
        issue_keys=("issue-2", "issue-3", "issue-missing"),
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def branch_exists(self, branch: str) -> bool:
            return True

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
                {
                    "key": "issue-2",
                    "rule": "csharpsquid:S1125",
                    "message": "second",
                    "line": 20,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
                {
                    "key": "issue-3",
                    "rule": "csharpsquid:S1125",
                    "message": "third",
                    "line": 30,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    processed_issue_keys: list[str] = []

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: processed_issue_keys.append(kwargs["issue"].key) or FixResult(
            success=True,
            issue_key=kwargs["issue"].key,
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
        ),
    )

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260412093000", show_banner=False, skip_build=True),
    )

    assert result.ok is True
    assert result.issues == 2
    assert result.successful == 2
    assert processed_issue_keys == ["issue-2", "issue-3"]
    output = capsys.readouterr().out
    assert "按 issue_keys 过滤后保留 2 个" in output
    assert "issue_keys 中有 1 个未命中当前 Sonar issues" in output


def test_run_coordinator_excludes_skip_issue_keys_after_issue_key_filter(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=0,
        issue_keys=("issue-1", "issue-2", "issue-3", "issue-missing"),
        skip_issue_keys=("issue-2", "issue-3", "issue-skip-missing"),
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def branch_exists(self, branch: str) -> bool:
            return True

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
                {
                    "key": "issue-2",
                    "rule": "csharpsquid:S1125",
                    "message": "second",
                    "line": 20,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
                {
                    "key": "issue-3",
                    "rule": "csharpsquid:S1125",
                    "message": "third",
                    "line": 30,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    processed_issue_keys: list[str] = []

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: processed_issue_keys.append(kwargs["issue"].key) or FixResult(
            success=True,
            issue_key=kwargs["issue"].key,
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
        ),
    )

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260518110000", show_banner=False, skip_build=True),
    )

    assert result.ok is True
    assert result.issues == 1
    assert result.successful == 1
    assert processed_issue_keys == ["issue-1"]
    output = capsys.readouterr().out
    assert "按 issue_keys 过滤后保留 3 个" in output
    assert "issue_keys 中有 1 个未命中当前 Sonar issues" in output
    assert "按 skip_issue_keys 排除后保留 1 个" in output
    assert "skip_issue_keys 中有 1 个未命中当前待处理 issues" in output


def test_run_coordinator_passes_configured_clone_depth_to_git_gateway(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )
    monkeypatch.setattr(
        run_coordinator_module,
        "load_performance_flags",
        lambda: PerformanceFlags(git_clone_depth=25),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    clone_calls: list[tuple[Path, str, int | None]] = []
    exclude_calls: list[Path] = []

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            clone_calls.append((workspace_path, branch, depth))
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            exclude_calls.append(workspace_path)

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
        ),
    )
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260415100000", show_banner=False),
    )

    assert result.ok is True
    assert clone_calls == [
        (
            tmp_path / "workspaces" / "fix_repo-a_20260415100000",
            "develop",
            25,
        )
    ]
    assert exclude_calls == [tmp_path / "workspaces" / "fix_repo-a_20260415100000"]
    output = capsys.readouterr().out
    assert "使用浅克隆准备仓库: depth=25" in output


def test_run_coordinator_uploads_pr_report_as_attachment(monkeypatch, tmp_path, capsys) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)
            (workspace_path / "src").mkdir(parents=True, exist_ok=True)
            (workspace_path / "src" / "Foo.cs").write_text("class Foo {}", encoding="utf-8")

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    class FakeDingTalkClient:
        def send_run_notification(self, **kwargs):
            captured["notification"] = kwargs
            return {"ok": True}

    monkeypatch.setattr(
        dingtalk_module,
        "create_dingtalk_client_from_env",
        lambda: FakeDingTalkClient(),
    )
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    captured: dict[str, object] = {}

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(
            self,
            repository: str,
            title: str,
            description: str,
            source_branch: str,
            target_branch: str = "develop",
            reviewer_email: str | None = None,
        ):
            captured["create_description"] = description
            captured["create_repository"] = repository
            captured["create_source_branch"] = source_branch
            return SimpleNamespace(
                pr_id=17,
                url="https://dev.azure.com/acme/project/_git/repo-a/pullrequest/17",
            )

        def upload_pull_request_attachment(
            self,
            repository: str,
            pull_request_id: int,
            *,
            file_name: str,
            content: str | bytes,
            content_type: str = "application/octet-stream",
        ):
            captured["attachment_repository"] = repository
            captured["attachment_pr_id"] = pull_request_id
            captured["attachment_name"] = file_name
            captured["attachment_content"] = content
            captured["attachment_content_type"] = content_type
            return SimpleNamespace(
                file_name=file_name,
                url="https://dev.azure.com/acme/project/_apis/git/repositories/repo-a/pullRequests/17/attachments/1",
            )

        def update_pull_request_description(
            self,
            repository: str,
            pull_request_id: int,
            description: str,
        ):
            captured["update_repository"] = repository
            captured["update_pr_id"] = pull_request_id
            captured["update_description"] = description
            return SimpleNamespace(pr_id=pull_request_id, description=description)

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "Remove redundant boolean literal",
                    "line": 12,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
        ),
    )
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260407123000", show_banner=False),
    )

    repo_report_dir = tmp_path / "workspaces" / "fix_repo-a_20260407123000" / "docs" / "sonar-reports"
    assert result.ok is True
    assert result.pr_url.endswith("/17")
    assert not repo_report_dir.exists()
    assert "详细修复报告附件" not in str(captured["create_description"])
    assert captured["attachment_repository"] == "repo-a"
    assert captured["attachment_pr_id"] == 17
    assert captured["attachment_name"] == "repo-a_alice-example.com_20260407123000.txt"
    assert "自动修复 SonarQube issues" in str(captured["attachment_content"])
    assert captured["attachment_content_type"] == "application/octet-stream"
    assert "详细修复报告附件" in str(captured["update_description"])
    assert "pullRequests/17/attachments/1" in str(captured["update_description"])
    assert captured["notification"]["pr_url"].endswith("/17")
    assert captured["notification"]["successful"] == 1
    assert captured["notification"]["skipped"] == 0
    assert captured["notification"]["failed"] == 0
    output = capsys.readouterr().out
    assert "PR 详细说明已保存" in output
    assert "PR 详细报告附件已上传" in output
    assert "钉钉通知发送成功" in output


def test_run_coordinator_keeps_target_returning_when_publish_branch_fails(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)
            (workspace_path / "src").mkdir(parents=True, exist_ok=True)
            (workspace_path / "src" / "Foo.cs").write_text("class Foo {}", encoding="utf-8")

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="ding-user",
            dingtalk_source="targets.json.dingtalk_userid",
        ),
    )

    notification_calls: list[dict[str, object]] = []

    class FakeDingTalkClient:
        def send_run_notification(self, **kwargs):
            notification_calls.append(kwargs)
            return {"ok": True}

    monkeypatch.setattr(
        dingtalk_module,
        "create_dingtalk_client_from_env",
        lambda: FakeDingTalkClient(),
    )
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(self, *args, **kwargs):
            raise AssertionError("create_pull_request should not be called when publish_branch fails")

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "Remove redundant boolean literal",
                    "line": 12,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
        ),
    )
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260413160000", show_banner=False),
    )

    assert result.ok is True
    assert result.pr_url == ""
    assert "远程主机强迫关闭了一个现有的连接" in result.pr_error
    assert notification_calls == []
    output = capsys.readouterr().out
    assert "[WARN] PR 创建失败:" in output


def test_run_coordinator_does_not_notify_when_no_pr_created(monkeypatch, tmp_path, capsys) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def branch_exists(self, branch: str) -> bool:
            return True

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise AssertionError("publish_branch should not be called when no PR is created")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="ding-user",
            dingtalk_source="targets.json.dingtalk_userid",
        ),
    )

    notification_calls: list[dict[str, object]] = []

    class FakeDingTalkClient:
        def send_run_notification(self, **kwargs):
            notification_calls.append(kwargs)
            return {"ok": True}

    monkeypatch.setattr(
        dingtalk_module,
        "create_dingtalk_client_from_env",
        lambda: FakeDingTalkClient(),
    )
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S107",
                    "message": "Too many parameters",
                    "line": 12,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=False,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Skipped by policy",
            attempts=1,
            skipped=True,
            skip_reason="规则 csharpsquid:S107 默认跳过",
            failure_kind="policy_skip",
        ),
    )

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260408143000", show_banner=False),
    )

    assert result.pr_url == ""
    assert result.successful == 0
    assert result.skipped == 0
    assert result.policy_skipped == 1
    assert notification_calls == []
    output = capsys.readouterr().out
    assert "钉钉通知发送成功" not in output
    assert "策略排除" in output


def test_run_target_filters_policy_skipped_rules_before_issue_limits(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        workspace_root=tmp_path / ".agent_workspaces",
        sonar_host="https://sonar.example",
        sonar_token="token",
        sonar_org="org",
        ado_base_url="https://dev.azure.com/acme",
        ado_project="project",
        ado_pat="pat",
        ado_org="acme",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="main",
        base_branch_source="targets.json.base_branch",
        max_issues=1,
        build_command="",
        test_command="",
        solution_path="",
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.agent.rule_policies as rule_policies_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.model_env as model_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="unresolved",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(model_env_module, "build_agent_env", lambda: {})
    monkeypatch.setattr(model_env_module, "resolve_agent_model", lambda: "fake-model")
    monkeypatch.setattr(rule_policies_module, "collect_skipped_rule_ids", lambda: {"skip:rule"})

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "skip-me",
                    "rule": "skip:rule",
                    "message": "skip",
                    "line": 10,
                    "component": "project-a:src/Skip.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
                {
                    "key": "keep-me",
                    "rule": "keep:rule",
                    "message": "keep",
                    "line": 12,
                    "component": "project-a:src/Keep.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def branch_exists(self, branch: str) -> bool:
            return True

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise AssertionError("publish_branch should not be called")

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    seen_issue_keys: list[str] = []

    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: seen_issue_keys.append(kwargs["issue"].key) or FixResult(
            success=True,
            issue_key=kwargs["issue"].key,
            file_path=kwargs["issue"].file_path,
            summary="fixed",
            attempts=1,
            changes=[{"file": kwargs["issue"].file_path, "action": "modified"}],
            build_passed=True,
        ),
    )

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260412153000", show_banner=False, skip_build=True),
    )

    assert result.successful == 1
    assert seen_issue_keys == ["keep-me"]
    output = capsys.readouterr().out
    assert "已排除 1 个策略跳过规则的 issues" in output


def _patch_two_tier_model_route(monkeypatch, *, enable_second_pass: bool = True) -> tuple[ModelTierConfig, ModelTierConfig]:
    import pi_sonar_agent.core.model_env as model_env_module

    tier1 = ModelTierConfig(
        tier_name="tier1",
        explicit_model="tier1-model",
        agent_env={"ANTHROPIC_MODEL": "tier1-model"},
        configured=True,
        source="test",
    )
    tier2 = ModelTierConfig(
        tier_name="tier2",
        explicit_model="tier2-model",
        agent_env={"ANTHROPIC_MODEL": "tier2-model"},
        configured=True,
        source="test",
    )
    monkeypatch.setattr(
        model_env_module,
        "resolve_model_tiers",
        lambda *args, **kwargs: {"tier1": tier1, "tier2": tier2},
    )
    monkeypatch.setattr(
        model_env_module,
        "build_issue_model_route",
        lambda *args, **kwargs: (tier2, tier1) if kwargs.get("second_pass") else (tier1, tier2),
    )
    monkeypatch.setattr(
        model_env_module,
        "second_pass_enabled",
        lambda *args, **kwargs: enable_second_pass,
    )
    monkeypatch.setattr(
        model_env_module,
        "abort_publish_enabled",
        lambda *args, **kwargs: True,
    )
    return tier1, tier2


def test_run_coordinator_retries_unresolved_issues_in_second_pass_with_tier2_first(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(
        run_coordinator_module,
        "current_run_timestamp",
        lambda: "2026-04-24 10:11:12",
    )
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise AssertionError("publish_branch should not be called when skip_build is true")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    _patch_two_tier_model_route(monkeypatch, enable_second_pass=True)
    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model")

    call_records: list[tuple[str, str]] = []

    def fake_process_issue_with_retries(**kwargs):
        call_records.append(
            (
                str(kwargs["agent"].model),
                str(kwargs.get("seed_retry_feedback", "") or ""),
            )
        )
        if kwargs["agent"].model == "tier1-model":
            return FixResult(
                success=False,
                issue_key=kwargs["issue"].key,
                file_path="src/Foo.cs",
                error="Agent completed without modifying any files",
                summary="No change",
                attempts=5,
                skipped=True,
                skip_reason="No change after retries",
                failure_kind="no_change",
            )
        return FixResult(
            success=True,
            issue_key=kwargs["issue"].key,
            file_path="src/Foo.cs",
            summary="Fixed on second pass",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
        )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(issue_retry_module, "process_issue_with_retries", fake_process_issue_with_retries)

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260423190000", show_banner=False, skip_build=True),
    )

    assert result.ok is True
    assert result.successful == 1
    assert result.skipped == 0
    assert [item[0] for item in call_records] == ["tier1-model", "tier2-model"]
    assert call_records[0][1] == ""
    assert "第二轮增强修复交接" in call_records[1][1]
    assert "第一轮最终状态: no_change" in call_records[1][1]
    output = capsys.readouterr().out
    assert "第二轮增强修复" in output
    assert "[2026-04-24 10:11:12] [1/1] 开始修复:" in output
    assert "[2026-04-24 10:11:12] [1/1] 修复结束:" in output
    assert "[2026-04-24 10:11:12] [SECOND PASS 1/1] 开始修复:" in output
    assert "[2026-04-24 10:11:12] [SECOND PASS 1/1] 修复完成:" in output


def test_run_coordinator_retries_roslyn_skips_in_second_pass(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(
        run_coordinator_module,
        "current_run_timestamp",
        lambda: "2026-04-24 10:11:12",
    )
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise AssertionError("publish_branch should not be called when skip_build is true")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    _patch_two_tier_model_route(monkeypatch, enable_second_pass=True)
    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-s107",
                    "rule": "csharpsquid:S107",
                    "message": "too many parameters",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model")

    call_records: list[tuple[str, bool]] = []

    def fake_process_issue_with_retries(**kwargs):
        call_records.append((str(kwargs["agent"].model), bool(kwargs.get("second_pass"))))
        if kwargs["agent"].model == "tier1-model":
            return FixResult(
                success=False,
                issue_key=kwargs["issue"].key,
                file_path="src/Foo.cs",
                error="Roslyn rejected the S107 candidate",
                summary="Roslyn rejected the S107 candidate",
                attempts=1,
                skipped=True,
                skip_reason="Roslyn rejected the S107 candidate",
                failure_kind="roslyn_cannot_fix_safely",
            )
        return FixResult(
            success=True,
            issue_key=kwargs["issue"].key,
            file_path="src/Foo.cs",
            summary="Fixed on second pass",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
        )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(issue_retry_module, "process_issue_with_retries", fake_process_issue_with_retries)

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260423190000", show_banner=False, skip_build=True),
    )

    assert result.ok is True
    assert result.successful == 1
    assert result.skipped == 0
    assert call_records == [("tier1-model", False), ("tier2-model", True)]
    output = capsys.readouterr().out
    assert "第一轮结束后仍有 1 个 unresolved issues" in output
    assert "[2026-04-24 10:11:12] [SECOND PASS 1/1] 开始修复:" in output


def test_run_coordinator_fails_over_to_tier2_when_first_tier_times_out(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise AssertionError("publish_branch should not be called when skip_build is true")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    _patch_two_tier_model_route(monkeypatch, enable_second_pass=False)
    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model")

    call_records: list[tuple[str, str]] = []

    def fake_process_issue_with_retries(**kwargs):
        call_records.append(
            (
                str(kwargs["agent"].model),
                str(kwargs.get("seed_retry_feedback", "") or ""),
            )
        )
        if kwargs["agent"].model == "tier1-model":
            return FixResult(
                success=False,
                issue_key=kwargs["issue"].key,
                file_path="src/Foo.cs",
                error="Model response timed out",
                summary="timeout",
                attempts=5,
                skipped=True,
                skip_reason="Model response timed out",
                failure_kind="model_timeout",
                model_timeout_stage="first_response_timeout",
            )
        return FixResult(
            success=True,
            issue_key=kwargs["issue"].key,
            file_path="src/Foo.cs",
            summary="Recovered on fallback tier",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
        )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(issue_retry_module, "process_issue_with_retries", fake_process_issue_with_retries)

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260423191000", show_banner=False, skip_build=True),
    )

    assert result.ok is True
    assert result.successful == 1
    assert [item[0] for item in call_records] == ["tier1-model", "tier2-model"]
    assert call_records[0][1] == ""
    assert "模型切梯交接" in call_records[1][1]
    assert "上一梯结果: model_timeout" in call_records[1][1]
    output = capsys.readouterr().out
    assert "切换到下一梯" in output


def test_run_coordinator_publishes_partial_pr_after_model_outage_abort(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=0,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    publish_calls: list[tuple[str, str]] = []
    captured: dict[str, object] = {}

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)
            (workspace_path / "src").mkdir(parents=True, exist_ok=True)
            (workspace_path / "src" / "Foo.cs").write_text("class Foo {}", encoding="utf-8")

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            publish_calls.append((branch, commit_message))

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    _patch_two_tier_model_route(monkeypatch, enable_second_pass=False)
    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(
            self,
            repository: str,
            title: str,
            description: str,
            source_branch: str,
            target_branch: str = "develop",
            reviewer_email: str | None = None,
        ):
            captured["title"] = title
            captured["description"] = description
            return SimpleNamespace(
                pr_id=21,
                url="https://dev.azure.com/acme/project/_git/repo-a/pullrequest/21",
            )

        def upload_pull_request_attachment(self, *args, **kwargs):
            return SimpleNamespace(file_name="report.txt", url="https://dev.azure.com/report.txt")

        def update_pull_request_description(self, *args, **kwargs):
            return SimpleNamespace(pr_id=21)

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
                {
                    "key": "issue-2",
                    "rule": "csharpsquid:S1125",
                    "message": "second",
                    "line": 20,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                },
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model")

    def fake_process_issue_with_retries(**kwargs):
        issue_key = kwargs["issue"].key
        model = str(kwargs["agent"].model)
        if issue_key == "issue-1":
            return FixResult(
                success=True,
                issue_key=issue_key,
                file_path="src/Foo.cs",
                summary="Fixed the issue",
                attempts=1,
                changes=[{"file": "src/Foo.cs"}],
                build_passed=True,
            )
        return FixResult(
            success=False,
            issue_key=issue_key,
            file_path="src/Foo.cs",
            error=f"{model} timed out",
            summary="timeout",
            attempts=5,
            skipped=True,
            skip_reason=f"{model} timed out",
            failure_kind="model_timeout",
            model_timeout_stage="first_response_timeout",
        )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(issue_retry_module, "process_issue_with_retries", fake_process_issue_with_retries)

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260423192000", show_banner=False),
    )

    assert result.ok is False
    assert result.successful == 1
    assert result.pr_url.endswith("/21")
    assert publish_calls
    assert str(captured["title"]).startswith("Partial Fix:")
    output = capsys.readouterr().out
    assert "优先发布已修结果" in output


def test_run_coordinator_auto_complete_failure_does_not_block_pr(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.project_env as project_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(
        project_env_module,
        "read_project_env",
        lambda *args, **kwargs: {
            "ADO_PR_AUTO_COMPLETE_ENABLED": "true",
            "ADO_PR_DELETE_SOURCE_BRANCH_ON_COMPLETE": "true",
        },
    )
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(
            self,
            repository: str,
            title: str,
            description: str,
            source_branch: str,
            target_branch: str = "develop",
            reviewer_email: str | None = None,
        ):
            return SimpleNamespace(
                pr_id=21,
                url="https://dev.azure.com/acme/project/_git/repo-a/pullrequest/21",
                created_by_id="creator-21",
            )

        def set_pull_request_auto_complete(self, *args, **kwargs):
            raise RuntimeError("permission denied")

        def upload_pull_request_attachment(self, *args, **kwargs):
            return SimpleNamespace(file_name="report.txt", url="https://dev.azure.com/report.txt")

        def update_pull_request_description(self, *args, **kwargs):
            return SimpleNamespace(pr_id=21)

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
        ),
    )

    coordinator = RunCoordinator(runtime_env)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260427150000", show_banner=False),
    )

    assert result.ok is True
    assert result.pr_url.endswith("/21")
    assert result.pr_error == ""
    output = capsys.readouterr().out
    assert "设置 PR 自动完成失败，但不影响当前 PR" in output


def test_run_coordinator_persists_pr_business_records(monkeypatch, tmp_path) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.project_env as project_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(project_env_module, "read_project_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(self, *args, **kwargs):
            return SimpleNamespace(
                pr_id=31,
                url="https://dev.azure.com/acme/project/_git/repo-a/pullrequest/31",
                created_by_id="creator-31",
            )

        def upload_pull_request_attachment(self, *args, **kwargs):
            return SimpleNamespace(file_name="report.txt", url="https://dev.azure.com/report.txt")

        def update_pull_request_description(self, *args, **kwargs):
            return SimpleNamespace(pr_id=31)

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model")

    class FakeDbClient:
        def __init__(self) -> None:
            self.ensure_tables_calls = 0
            self.snapshots: list[dict] = []
            self.events: list[dict] = []
            self.run_records: list[tuple[str, str, str, int]] = []
            self.issue_records: list[tuple[int, str, str, str, int]] = []
            self.updated_issue_records: list[tuple[str, str, str | None, str | None]] = []
            self.updated_run_records: list[tuple[int, int | None, int | None, str | None, str | None, str | None]] = []
            self.pr_records: list[dict] = []
            self.pr_issue_rows: list[tuple[int, list[dict]]] = []
            self.pr_attempt_rows: list[tuple[int, list[dict]]] = []

        def ensure_tables(self) -> None:
            self.ensure_tables_calls += 1

        def insert_run_record(self, author: str, project_key: str, repository: str, total_issues: int) -> int:
            self.run_records.append((author, project_key, repository, total_issues))
            return 7

        def update_run_record(self, run_id: int, successful_fixes=None, failed_fixes=None, status=None, error=None, pr_url=None) -> None:
            self.updated_run_records.append((run_id, successful_fixes, failed_fixes, status, error, pr_url))

        def insert_issue_record(self, run_id: int, issue_key: str, rule_id: str, file_path: str, line_number: int) -> None:
            self.issue_records.append((run_id, issue_key, rule_id, file_path, line_number))

        def update_issue_record(self, issue_key: str, fix_status: str, fix_engine: str = None, error_message: str = None) -> None:
            self.updated_issue_records.append((issue_key, fix_status, fix_engine, error_message))

        def upsert_state_snapshot(self, **kwargs) -> None:
            self.snapshots.append(kwargs)

        def insert_event_record(self, **kwargs) -> None:
            self.events.append(kwargs)

        def insert_pull_request_record(self, **kwargs) -> int:
            self.pr_records.append(kwargs)
            return 101

        def insert_pull_request_issue_records(self, pr_record_id: int, rows: list[dict]) -> None:
            self.pr_issue_rows.append((pr_record_id, rows))

        def insert_pull_request_attempt_records(self, pr_record_id: int, rows: list[dict]) -> None:
            self.pr_attempt_rows.append((pr_record_id, rows))

        def disconnect(self) -> None:
            return None

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)

    def fake_process_issue_with_retries(**kwargs):
        attempt_state = AttemptState(
            attempt_number=1,
            status=AttemptStatus.SUCCEEDED,
            started_at="2026-04-27T15:46:03+08:00",
            finished_at="2026-04-27T15:46:03+08:00",
            duration_seconds=0.0,
            summary="Fixed the issue",
            changed_files=("src/Foo.cs",),
            artifact_dir=str(tmp_path / "issue-artifacts" / "attempt-01"),
            performance_metrics={
                "model_route_tier": "tier1",
                "model_route_model": "tier1-model",
                "model_route_pass": "first_pass",
            },
            build_passed=True,
        )
        issue_state = IssueState(
            issue_key="issue-1",
            repository="repo-a",
            run_label="20260427160000",
            rule_id="csharpsquid:S1125",
            file_path="/src/Foo.cs",
            line=10,
            status=IssueStatus.FIXED,
            attempts=(attempt_state,),
            final_summary="Fixed the issue",
            artifact_root=str(tmp_path / "issue-artifacts" / "issue-1"),
        )
        return FixResult(
            success=True,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
            performance_metrics={
                "model_route_tier": "tier1",
                "model_route_model": "tier1-model",
                "model_route_pass": "first_pass",
            },
            issue_state=issue_state,
            issue_log_path="logs/issue.log",
        )

    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        fake_process_issue_with_retries,
    )

    fake_db = FakeDbClient()
    coordinator = RunCoordinator(runtime_env)
    coordinator.state_store = RunStateStore(db_client=fake_db)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260427160000", show_banner=False),
    )

    assert result.ok is True
    assert result.pr_url.endswith("/31")
    assert len(fake_db.pr_records) == 1
    assert fake_db.pr_records[0]["pr_id"] == 31
    assert fake_db.pr_records[0]["successful_issues"] == 1
    assert fake_db.pr_records[0]["pr_status"] == "created"
    assert len(fake_db.pr_issue_rows) == 1
    assert fake_db.pr_issue_rows[0][0] == 101
    assert fake_db.pr_issue_rows[0][1][0]["issue_key"] == "issue-1"
    assert fake_db.pr_issue_rows[0][1][0]["final_status"] == "fixed"
    assert len(fake_db.pr_attempt_rows) == 1
    assert fake_db.pr_attempt_rows[0][1][0]["attempt_number"] == 1
    assert fake_db.pr_attempt_rows[0][1][0]["tier_name"] == "tier1"


def test_run_coordinator_persists_failed_pr_business_record(monkeypatch, tmp_path) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.project_env as project_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(project_env_module, "read_project_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(self, *args, **kwargs):
            raise RuntimeError("ado pr failed")

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    class FakeDbClient:
        def __init__(self) -> None:
            self.pr_records: list[dict] = []
            self.pr_issue_rows: list[tuple[int, list[dict]]] = []
            self.pr_attempt_rows: list[tuple[int, list[dict]]] = []

        def ensure_tables(self) -> None:
            return None

        def insert_run_record(self, *args, **kwargs) -> int:
            return 7

        def update_run_record(self, *args, **kwargs) -> None:
            return None

        def insert_issue_record(self, *args, **kwargs) -> None:
            return None

        def update_issue_record(self, *args, **kwargs) -> None:
            return None

        def upsert_state_snapshot(self, **kwargs) -> None:
            return None

        def insert_event_record(self, **kwargs) -> None:
            return None

        def insert_pull_request_record(self, **kwargs) -> int:
            self.pr_records.append(kwargs)
            return 101

        def insert_pull_request_issue_records(self, pr_record_id: int, rows: list[dict]) -> None:
            self.pr_issue_rows.append((pr_record_id, rows))

        def insert_pull_request_attempt_records(self, pr_record_id: int, rows: list[dict]) -> None:
            self.pr_attempt_rows.append((pr_record_id, rows))

        def disconnect(self) -> None:
            return None

    attempt_state = AttemptState(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        started_at="2026-04-27T15:46:03+08:00",
        finished_at="2026-04-27T15:46:03+08:00",
        duration_seconds=0.0,
        summary="Fixed the issue",
        changed_files=("src/Foo.cs",),
        artifact_dir=str(tmp_path / "issue-artifacts" / "attempt-01"),
        build_passed=True,
        performance_metrics={
            "model_route_tier": "tier1",
            "model_route_model": "tier1-model",
            "model_route_pass": "first_pass",
        },
    )
    issue_state = IssueState(
        issue_key="issue-1",
        repository="repo-a",
        run_label="20260427161000",
        rule_id="csharpsquid:S1125",
        file_path="/src/Foo.cs",
        line=10,
        status=IssueStatus.FIXED,
        attempts=(attempt_state,),
        final_summary="Fixed the issue",
        artifact_root=str(tmp_path / "issue-artifacts" / "issue-1"),
    )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Fixed the issue",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
            issue_state=issue_state,
        ),
    )

    fake_db = FakeDbClient()
    coordinator = RunCoordinator(runtime_env)
    coordinator.state_store = RunStateStore(db_client=fake_db)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260427161000", show_banner=False),
    )

    assert result.ok is True
    assert result.pr_url == ""
    assert len(fake_db.pr_records) == 1
    assert fake_db.pr_records[0]["pr_status"] == "failed"


def test_run_coordinator_persists_not_created_pr_business_record(monkeypatch, tmp_path) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            raise AssertionError("publish_branch should not be called")

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.project_env as project_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(project_env_module, "read_project_env", lambda *args, **kwargs: {})

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-1",
                    "rule": "csharpsquid:S1125",
                    "message": "first",
                    "line": 10,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    class FakeDbClient:
        def __init__(self) -> None:
            self.pr_records: list[dict] = []

        def ensure_tables(self) -> None:
            return None

        def insert_run_record(self, *args, **kwargs) -> int:
            return 7

        def update_run_record(self, *args, **kwargs) -> None:
            return None

        def insert_issue_record(self, *args, **kwargs) -> None:
            return None

        def update_issue_record(self, *args, **kwargs) -> None:
            return None

        def upsert_state_snapshot(self, **kwargs) -> None:
            return None

        def insert_event_record(self, **kwargs) -> None:
            return None

        def insert_pull_request_record(self, **kwargs) -> int:
            self.pr_records.append(kwargs)
            return 101

        def insert_pull_request_issue_records(self, pr_record_id: int, rows: list[dict]) -> None:
            return None

        def insert_pull_request_attempt_records(self, pr_record_id: int, rows: list[dict]) -> None:
            return None

        def disconnect(self) -> None:
            return None

    attempt_state = AttemptState(
        attempt_number=1,
        status=AttemptStatus.SKIPPED,
        started_at="2026-04-27T15:46:03+08:00",
        finished_at="2026-04-27T15:46:03+08:00",
        duration_seconds=0.0,
        skip_reason="policy skip",
        artifact_dir=str(tmp_path / "issue-artifacts" / "attempt-01"),
        performance_metrics={
            "model_route_tier": "tier1",
            "model_route_model": "tier1-model",
            "model_route_pass": "first_pass",
        },
    )
    issue_state = IssueState(
        issue_key="issue-1",
        repository="repo-a",
        run_label="20260427162000",
        rule_id="csharpsquid:S1125",
        file_path="/src/Foo.cs",
        line=10,
        status=IssueStatus.SKIPPED,
        attempts=(attempt_state,),
        final_skip_reason="policy skip",
        artifact_root=str(tmp_path / "issue-artifacts" / "issue-1"),
    )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=False,
            issue_key="issue-1",
            file_path="src/Foo.cs",
            summary="Skipped by policy",
            attempts=1,
            skipped=True,
            skip_reason="policy skip",
            failure_kind="policy_skip",
            issue_state=issue_state,
        ),
    )

    fake_db = FakeDbClient()
    coordinator = RunCoordinator(runtime_env)
    coordinator.state_store = RunStateStore(db_client=fake_db)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260427162000", show_banner=False, skip_build=True),
    )

    assert result.ok is True
    assert result.pr_url == ""
    assert len(fake_db.pr_records) == 1
    assert fake_db.pr_records[0]["pr_status"] == "not_created"


def test_run_coordinator_persists_s3776_rule_review_summary(monkeypatch, tmp_path) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.project_env as project_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(project_env_module, "read_project_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(self, *args, **kwargs):
            return SimpleNamespace(
                pr_id=32,
                url="https://dev.azure.com/acme/project/_git/repo-a/pullrequest/32",
                created_by_id="creator-32",
            )

        def upload_pull_request_attachment(self, *args, **kwargs):
            return SimpleNamespace(file_name="report.txt", url="https://dev.azure.com/report.txt")

        def update_pull_request_description(self, *args, **kwargs):
            return SimpleNamespace(pr_id=32)

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-s3776",
                    "rule": "csharpsquid:S3776",
                    "message": "Cognitive complexity too high",
                    "line": 22,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    class FakeDbClient:
        def __init__(self) -> None:
            self.pr_issue_rows: list[tuple[int, list[dict]]] = []

        def ensure_tables(self) -> None:
            return None

        def insert_run_record(self, *args, **kwargs) -> int:
            return 7

        def update_run_record(self, *args, **kwargs) -> None:
            return None

        def insert_issue_record(self, *args, **kwargs) -> None:
            return None

        def update_issue_record(self, *args, **kwargs) -> None:
            return None

        def upsert_state_snapshot(self, **kwargs) -> None:
            return None

        def insert_event_record(self, **kwargs) -> None:
            return None

        def insert_pull_request_record(self, **kwargs) -> int:
            return 101

        def insert_pull_request_issue_records(self, pr_record_id: int, rows: list[dict]) -> None:
            self.pr_issue_rows.append((pr_record_id, rows))

        def insert_pull_request_attempt_records(self, pr_record_id: int, rows: list[dict]) -> None:
            return None

        def disconnect(self) -> None:
            return None

    attempt_state = AttemptState(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        started_at="2026-04-27T15:46:03+08:00",
        finished_at="2026-04-27T15:46:03+08:00",
        duration_seconds=0.0,
        summary="Fixed S3776",
        changed_files=("src/Foo.cs",),
        artifact_dir=str(tmp_path / "issue-artifacts" / "attempt-01"),
        build_passed=True,
        performance_metrics={
            "model_route_tier": "tier2",
            "model_route_model": "tier2-model",
            "model_route_pass": "second_pass",
        },
    )
    issue_state = IssueState(
        issue_key="issue-s3776",
        repository="repo-a",
        run_label="20260427163000",
        rule_id="csharpsquid:S3776",
        file_path="/src/Foo.cs",
        line=22,
        status=IssueStatus.FIXED,
        attempts=(attempt_state,),
        final_summary="Fixed S3776",
        artifact_root=str(tmp_path / "issue-artifacts" / "issue-s3776"),
    )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-s3776",
            file_path="src/Foo.cs",
            summary="Fixed S3776",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
            issue_state=issue_state,
            repair_plan=SimpleNamespace(
                primary_method_name="CalculateFoo",
                selected_archetype="guard_clause_flatten",
                fallback_archetype="local_block_reorder",
                new_helpers=("NormalizeFoo",),
                requires_signature_change=False,
                requires_new_type=False,
                impact_summary="Reduce nested branching in the current method.",
            ),
            post_fix_check_result={
                "issue_status": "PASS",
                "issue_check": {
                    "metrics": {
                        "estimated_cognitive_complexity": 18,
                        "fail_threshold": 30,
                    }
                },
            },
            performance_metrics={
                "model_route_tier": "tier2",
                "model_route_model": "tier2-model",
                "model_route_pass": "second_pass",
            },
        ),
    )

    fake_db = FakeDbClient()
    coordinator = RunCoordinator(runtime_env)
    coordinator.state_store = RunStateStore(db_client=fake_db)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260427163000", show_banner=False),
    )

    assert result.ok is True
    assert len(fake_db.pr_issue_rows) == 1
    row = fake_db.pr_issue_rows[0][1][0]
    summary_items = json.loads(row["rule_review_summary_json"])
    assert "主要收口方法: CalculateFoo" in summary_items
    assert "采用策略: guard_clause_flatten" in summary_items
    assert "本地复杂度估计: 18/30" in summary_items
    assert any(item.startswith("最终模型梯次: ") for item in summary_items)


def test_run_coordinator_builds_s3776_review_summary_from_patch_facts_without_repair_plan(
    monkeypatch,
    tmp_path,
) -> None:
    runtime_env = RuntimeEnvironment(
        sonar_host="https://sonar.example",
        sonar_token="sonar-token",
        sonar_org="sonar-org",
        ado_base_url="https://dev.azure.com/acme",
        ado_org="acme",
        ado_project="pi",
        ado_pat="ado-token",
        workspace_root=tmp_path / "workspaces",
    )
    target_config = TargetConfig(
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        reviewer_email="",
        dingtalk_userid="",
        base_branch="develop",
        base_branch_source="targets.json.base_branch",
        build_command="dotnet build Foo.sln",
        test_command=None,
        solution_path="Foo.sln",
        max_issues=1,
    )

    monkeypatch.setattr(run_coordinator_module, "ensure_workspace_writable", lambda workspace_root: None)
    monkeypatch.setattr(run_coordinator_module, "ensure_remote_branch_exists", lambda **kwargs: None)
    monkeypatch.setattr(
        run_coordinator_module,
        "prune_old_workspaces",
        lambda workspace_root, keep_latest=1: SimpleNamespace(removed=(), failed=()),
    )

    class FakeArtifactWriter:
        def write_target_state(self, target_state):
            summary_path = tmp_path / "run-artifacts" / "target_summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text("{}", encoding="utf-8")
            return summary_path

    monkeypatch.setattr(run_coordinator_module, "ArtifactWriter", FakeArtifactWriter)

    class FakeGitRepositoryGateway:
        def __init__(self, *, remote_url: str, pat: str | None = None, command_runner=None):
            self.remote_url = remote_url

        def clone_branch(self, workspace_path: Path, branch: str, *, depth: int | None = None) -> None:
            workspace_path.mkdir(parents=True, exist_ok=True)

        def install_local_excludes(self, workspace_path: Path) -> None:
            return None

        def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
            return None

    monkeypatch.setattr(run_coordinator_module, "GitRepositoryGateway", FakeGitRepositoryGateway)

    import pi_sonar_agent.agent.claude_agent as claude_agent_module
    import pi_sonar_agent.core.db_client as db_client_module
    import pi_sonar_agent.core.dingtalk as dingtalk_module
    import pi_sonar_agent.core.issue_retry as issue_retry_module
    import pi_sonar_agent.core.project_env as project_env_module
    import pi_sonar_agent.core.recipient_resolution as recipient_module
    import pi_sonar_agent.fixers.build_gate as build_gate_module
    import pi_sonar_agent.integrations.ado as ado_module
    import pi_sonar_agent.integrations.sonar as sonar_module

    monkeypatch.setattr(db_client_module, "create_mysql_client_from_env", lambda: None)
    monkeypatch.setattr(
        recipient_module,
        "resolve_recipients",
        lambda **kwargs: SimpleNamespace(
            reviewer_email="",
            reviewer_source="author",
            dingtalk_userid="",
            dingtalk_source="author",
        ),
    )
    monkeypatch.setattr(dingtalk_module, "create_dingtalk_client_from_env", lambda: None)
    monkeypatch.setattr(project_env_module, "read_project_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        build_gate_module,
        "run_local_build",
        lambda *args, **kwargs: {
            "succeeded": True,
            "build_command": "dotnet build Foo.sln",
            "test_command": "",
        },
    )
    monkeypatch.setattr(build_gate_module, "resolve_build_command", lambda command, solution_path: command)
    monkeypatch.setattr(build_gate_module, "format_build_failure_report", lambda *args, **kwargs: "")

    class FakeAdoClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_remote_url(self, repository: str) -> str:
            return f"https://dev.azure.com/acme/project/_git/{repository}"

        def create_pull_request(self, *args, **kwargs):
            return SimpleNamespace(
                pr_id=32,
                url="https://dev.azure.com/acme/project/_git/repo-a/pullrequest/32",
                created_by_id="creator-32",
            )

        def upload_pull_request_attachment(self, *args, **kwargs):
            return SimpleNamespace(file_name="report.txt", url="https://dev.azure.com/report.txt")

        def update_pull_request_description(self, *args, **kwargs):
            return SimpleNamespace(pr_id=32)

    class FakeSonarClient:
        def __init__(self, *args, **kwargs):
            return None

        def get_open_issues(self, project_key: str, author: str) -> list[dict]:
            return [
                {
                    "key": "issue-s3776",
                    "rule": "csharpsquid:S3776",
                    "message": "Cognitive complexity too high",
                    "line": 22,
                    "component": "project-a:src/Foo.cs",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                }
            ]

    class FakeClaudeFixAgent:
        def __init__(self, *args, **kwargs):
            return None

    class FakeDbClient:
        def __init__(self) -> None:
            self.pr_issue_rows: list[tuple[int, list[dict]]] = []

        def ensure_tables(self) -> None:
            return None

        def insert_run_record(self, *args, **kwargs) -> int:
            return 7

        def update_run_record(self, *args, **kwargs) -> None:
            return None

        def insert_issue_record(self, *args, **kwargs) -> None:
            return None

        def update_issue_record(self, *args, **kwargs) -> None:
            return None

        def upsert_state_snapshot(self, **kwargs) -> None:
            return None

        def insert_event_record(self, **kwargs) -> None:
            return None

        def insert_pull_request_record(self, **kwargs) -> int:
            return 101

        def insert_pull_request_issue_records(self, pr_record_id: int, rows: list[dict]) -> None:
            self.pr_issue_rows.append((pr_record_id, rows))

        def insert_pull_request_attempt_records(self, pr_record_id: int, rows: list[dict]) -> None:
            return None

        def disconnect(self) -> None:
            return None

    attempt_artifact_dir = tmp_path / "issue-artifacts" / "issue-s3776" / "attempt-01"
    attempt_artifact_dir.mkdir(parents=True, exist_ok=True)
    (attempt_artifact_dir / "prompt_context.json").write_text(
        json.dumps(
            {
                "prefetched_context": [
                    {
                        "label": "target_method_full",
                        "content": (
                            " 455 |         /// 批量加载收款数据\n"
                            " 456 |         /// </summary>\n"
                            " 457 |         private async Task<Dictionary<int, List<ReceiptForOrder>>> LoadReceiptDataAsync(List<int> orderIds)\n"
                            " 458 |         {\n"
                            " 459 |             var receiptDict = new Dictionary<int, List<ReceiptForOrder>>();"
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (attempt_artifact_dir / "patch.diff").write_text(
        """--- a/src/Foo.cs
+++ b/src/Foo.cs
@@
-            var orderToInv1Entries = new Dictionary<int, List<int>>();
+            var orderToInv1Entries = BuildOrderToInv1Entries(orderIds, dln1EntriesDict, inv1EntriesDict);
+            AppendIndirectReceipts(orderIds, orderToInv1Entries, allRct2, directDocNums, indirectReceiptsDict, receiptDict);
+            AppendDirectReceipts(orderIds, directReceiptsByOrder, receiptDict);
+            receiptDict = SortAndDeduplicateReceipts(receiptDict);
+
+        private Dictionary<int, List<int>> BuildOrderToInv1Entries(List<int> orderIds, Dictionary<int, List<int>> dln1EntriesDict, Dictionary<int, List<int>> inv1EntriesDict)
+        {
+        }
+
+        private void AppendIndirectReceipts(List<int> orderIds, Dictionary<int, List<int>> orderToInv1Entries, List<(int DocNum, int InvDocEntry, decimal SumApplied, decimal AppliedFC)> allRct2, HashSet<int> directDocNums, Dictionary<int, ORCT> indirectReceiptsDict, Dictionary<int, List<ReceiptForOrder>> receiptDict)
+        {
+        }
+
+        private void AppendDirectReceipts(List<int> orderIds, Dictionary<int, List<ORCT>> directReceiptsByOrder, Dictionary<int, List<ReceiptForOrder>> receiptDict)
+        {
+        }
+
+        private Dictionary<int, List<ReceiptForOrder>> SortAndDeduplicateReceipts(Dictionary<int, List<ReceiptForOrder>> receiptDict)
+        {
+        }
""",
        encoding="utf-8",
    )

    attempt_state = AttemptState(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        started_at="2026-04-27T18:16:03+08:00",
        finished_at="2026-04-27T18:16:03+08:00",
        duration_seconds=0.0,
        summary="Fixed S3776",
        changed_files=("src/Foo.cs",),
        artifact_dir=str(attempt_artifact_dir),
        build_passed=True,
        performance_metrics={
            "model_route_tier": "tier1",
            "model_route_model": "tier1-model",
            "model_route_pass": "first_pass",
        },
    )
    issue_state = IssueState(
        issue_key="issue-s3776",
        repository="repo-a",
        run_label="20260427181600",
        rule_id="csharpsquid:S3776",
        file_path="/src/Foo.cs",
        line=22,
        status=IssueStatus.FIXED,
        attempts=(attempt_state,),
        final_summary="Fixed S3776",
        artifact_root=str(tmp_path / "issue-artifacts" / "issue-s3776"),
    )

    monkeypatch.setattr(ado_module, "AzureDevOpsClient", FakeAdoClient)
    monkeypatch.setattr(sonar_module, "SonarQubeClient", FakeSonarClient)
    monkeypatch.setattr(claude_agent_module, "ClaudeFixAgent", FakeClaudeFixAgent)
    monkeypatch.setattr(
        issue_retry_module,
        "process_issue_with_retries",
        lambda **kwargs: FixResult(
            success=True,
            issue_key="issue-s3776",
            file_path="src/Foo.cs",
            summary="Fixed S3776",
            attempts=1,
            changes=[{"file": "src/Foo.cs"}],
            build_passed=True,
            issue_state=issue_state,
            post_fix_check_result={
                "issue_status": "PASS",
                "issue_check": {
                    "metrics": {
                        "method_name": "LoadReceiptDataAsync",
                        "estimated_cognitive_complexity": 19,
                        "fail_threshold": 30,
                    }
                },
            },
            performance_metrics={
                "model_route_tier": "tier1",
                "model_route_model": "tier1-model",
                "model_route_pass": "first_pass",
            },
        ),
    )

    fake_db = FakeDbClient()
    coordinator = RunCoordinator(runtime_env)
    coordinator.state_store = RunStateStore(db_client=fake_db)
    result = coordinator.run_target(
        target_config,
        TargetRunOptions(run_label="20260427181600", show_banner=False),
    )

    assert result.ok is True
    assert len(fake_db.pr_issue_rows) == 1
    row = fake_db.pr_issue_rows[0][1][0]
    summary_items = json.loads(row["rule_review_summary_json"])
    assert "主要收口方法: LoadReceiptDataAsync" in summary_items
    assert "提取私有方法: BuildOrderToInv1Entries, AppendIndirectReceipts, AppendDirectReceipts, SortAndDeduplicateReceipts" in summary_items
    assert "目标方法现在通过调用: BuildOrderToInv1Entries, AppendIndirectReceipts, AppendDirectReceipts, SortAndDeduplicateReceipts" in summary_items
    assert "主要修改: 将 LoadReceiptDataAsync 中的复杂逻辑下沉到私有 helper，主方法改为编排式调用。" in summary_items
    assert "本地复杂度估计: 19/30" in summary_items
    assert any(item.startswith("重点审阅: ") for item in summary_items)
