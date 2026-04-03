from __future__ import annotations

from pathlib import Path

import pytest

from pi_sonar_agent.core.preflight import (
    ensure_remote_branch_exists,
    ensure_workspace_writable,
    load_runtime_environment,
    require_env,
)


def test_load_runtime_environment_resolves_required_values(monkeypatch) -> None:
    import pi_sonar_agent.core.preflight as preflight_module

    monkeypatch.setattr(preflight_module, "validate_agent_env", lambda: [])

    runtime_env = load_runtime_environment(
        environ={
            "SONARQUBE_HOST": "https://sonar.example",
            "SONARQUBE_TOKEN": "sonar-token",
            "SONARQUBE_ORG": "sonar-org",
            "ADO_BASE_URL": "https://dev.azure.com/acme/project",
            "ADO_ORG": "acme",
            "ADO_PROJECT": "pi",
            "ADO_PAT": "ado-token",
            "WORKSPACE_ROOT": ".custom-workspaces",
        }
    )

    assert runtime_env.sonar_host == "https://sonar.example"
    assert runtime_env.sonar_token == "sonar-token"
    assert runtime_env.sonar_org == "sonar-org"
    assert runtime_env.ado_base_url == "https://dev.azure.com/acme/project"
    assert runtime_env.ado_org == "acme"
    assert runtime_env.ado_project == "pi"
    assert runtime_env.ado_pat == "ado-token"
    assert runtime_env.workspace_root == Path(".custom-workspaces")


def test_load_runtime_environment_raises_for_invalid_model_env(monkeypatch) -> None:
    import pi_sonar_agent.core.preflight as preflight_module

    monkeypatch.setattr(
        preflight_module,
        "validate_agent_env",
        lambda: ["模型 endpoint 配置无效：https://bad.example"],
    )

    with pytest.raises(RuntimeError) as exc_info:
        load_runtime_environment(
            environ={
                "SONARQUBE_HOST": "https://sonar.example",
                "SONARQUBE_TOKEN": "sonar-token",
                "ADO_BASE_URL": "https://dev.azure.com/acme/project",
                "ADO_PROJECT": "pi",
                "ADO_PAT": "ado-token",
            }
        )

    assert "模型配置无效" in str(exc_info.value)
    assert "https://bad.example" in str(exc_info.value)


def test_require_env_raises_for_missing_value() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        require_env("ADO_PAT", {})

    assert str(exc_info.value) == "缺少环境变量: ADO_PAT"


def test_require_env_does_not_fall_back_to_os_environ_for_empty_mapping(monkeypatch) -> None:
    monkeypatch.setenv("ADO_PAT", "host-token")

    with pytest.raises(RuntimeError) as exc_info:
        require_env("ADO_PAT", {})

    assert str(exc_info.value) == "缺少环境变量: ADO_PAT"


def test_ensure_workspace_writable_creates_and_cleans_probe_file(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace-root"

    ensure_workspace_writable(workspace_root)

    assert workspace_root.exists()
    assert not (workspace_root / ".pi-sonar-agent-write-check").exists()


def test_ensure_workspace_writable_raises_when_probe_write_fails(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace-root"

    original_write_text = Path.write_text

    def fake_write_text(self, *args, **kwargs):
        if self.name == ".pi-sonar-agent-write-check":
            raise PermissionError("access denied")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake_write_text)

    with pytest.raises(RuntimeError) as exc_info:
        ensure_workspace_writable(workspace_root)

    assert "WORKSPACE_ROOT 不可写" in str(exc_info.value)
    assert "access denied" in str(exc_info.value)


def test_ensure_remote_branch_exists_uses_git_gateway() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.redacted_remote_url = "https://***:***@dev.azure.com/acme/project/_git/repo"
            self.calls: list[str] = []

        def branch_exists(self, branch: str) -> bool:
            self.calls.append(branch)
            return True

    gateway = FakeGateway()

    ensure_remote_branch_exists(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        branch="release/2026.04",
        pat="secret",
        git_gateway=gateway,  # type: ignore[arg-type]
    )

    assert gateway.calls == ["release/2026.04"]


def test_ensure_remote_branch_exists_raises_for_missing_branch() -> None:
    class FakeGateway:
        redacted_remote_url = "https://***:***@dev.azure.com/acme/project/_git/repo"

        def branch_exists(self, branch: str) -> bool:
            return False

    with pytest.raises(RuntimeError) as exc_info:
        ensure_remote_branch_exists(
            remote_url="https://dev.azure.com/acme/project/_git/repo",
            branch="missing-branch",
            pat="secret",
            git_gateway=FakeGateway(),  # type: ignore[arg-type]
        )

    assert "远端基线分支不存在: missing-branch" in str(exc_info.value)
