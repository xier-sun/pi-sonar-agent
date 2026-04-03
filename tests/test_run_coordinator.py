from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pi_sonar_agent.core.run_coordinator as run_coordinator_module
from pi_sonar_agent.core.preflight import RuntimeEnvironment
from pi_sonar_agent.core.run_coordinator import RunCoordinator, TargetRunOptions
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

        def clone_branch(self, workspace_path: Path, branch: str) -> None:
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
