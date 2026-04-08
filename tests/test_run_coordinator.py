from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pi_sonar_agent.core.run_coordinator as run_coordinator_module
from pi_sonar_agent.agent.claude_agent import FixResult
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

        def clone_branch(self, workspace_path: Path, branch: str) -> None:
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

        def clone_branch(self, workspace_path: Path, branch: str) -> None:
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
    assert result.skipped == 1
    assert notification_calls == []
    output = capsys.readouterr().out
    assert "钉钉通知发送成功" not in output
