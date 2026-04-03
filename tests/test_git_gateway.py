from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pi_sonar_agent.core.git_gateway import (
    GitRepositoryGateway,
    build_authenticated_remote_url,
    redact_remote_url,
    resolve_base_branch,
)


def test_resolve_base_branch_prefers_cli_value() -> None:
    selection = resolve_base_branch(
        cli_branch=" release/2026.04 ",
        configured_branch="main",
        default_branch="develop",
    )

    assert selection.branch == "release/2026.04"
    assert selection.source == "args.base_branch"


def test_resolve_base_branch_falls_back_to_config_then_default() -> None:
    configured_selection = resolve_base_branch(
        configured_branch="main",
        default_branch="develop",
    )
    default_selection = resolve_base_branch(default_branch="develop")

    assert configured_selection.branch == "main"
    assert configured_selection.source == "targets.json.base_branch"
    assert default_selection.branch == "develop"
    assert default_selection.source == "default"


def test_build_authenticated_remote_url_and_redaction() -> None:
    remote_url = "https://dev.azure.com/acme/project/_git/repo"

    authenticated_remote = build_authenticated_remote_url(remote_url, "pat:/?#")

    assert authenticated_remote == "https://:pat%3A%2F%3F%23@dev.azure.com/acme/project/_git/repo"
    assert redact_remote_url(authenticated_remote) == "https://***:***@dev.azure.com/acme/project/_git/repo"


def test_git_repository_gateway_clones_requested_branch_with_authenticated_remote(tmp_path: Path) -> None:
    calls: list[tuple[str, Path | None]] = []

    def fake_runner(command: str, *, cwd: Path | None = None, timeout=None, check: bool = True):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="secret",
        command_runner=fake_runner,
    )

    gateway.clone_branch(tmp_path / "repo", "release/1.0")

    assert len(calls) == 1
    assert calls[0][1] is None
    assert 'git clone -b "release/1.0" --single-branch' in calls[0][0]
    assert "https://:secret@dev.azure.com/acme/project/_git/repo" in calls[0][0]


def test_git_repository_gateway_redacts_pat_when_clone_fails(tmp_path: Path) -> None:
    def fake_runner(command: str, *, cwd: Path | None = None, timeout=None, check: bool = True):
        raise subprocess.CalledProcessError(128, command, stderr="authentication failed")

    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="super-secret-token",
        command_runner=fake_runner,
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway.clone_branch(tmp_path / "repo", "main")

    message = str(exc_info.value)
    assert "super-secret-token" not in message
    assert "***:***@dev.azure.com" in message
    assert "authentication failed" in message


def test_git_repository_gateway_publish_branch_runs_expected_command_sequence(tmp_path: Path) -> None:
    calls: list[tuple[str, Path | None]] = []

    def fake_runner(command: str, *, cwd: Path | None = None, timeout=None, check: bool = True):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="secret",
        command_runner=fake_runner,
    )

    gateway.publish_branch(tmp_path, "feature/one", 'fix: say "hello"')

    assert [command for command, _ in calls] == [
        'git checkout -b "feature/one"',
        "git add -A",
        'git commit -m "fix: say \\"hello\\""',
        'git push -u origin "feature/one"',
    ]
    assert all(cwd == tmp_path for _, cwd in calls)


def test_git_repository_gateway_supports_generic_clone_with_depth(tmp_path: Path) -> None:
    calls: list[tuple[str, Path | None]] = []

    def fake_runner(command: str, *, cwd: Path | None = None, timeout=None, check: bool = True):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="secret",
        command_runner=fake_runner,
    )

    gateway.clone_repository(tmp_path / "repo", depth=1)

    assert calls == [
        (
            'git clone --depth 1 "https://:secret@dev.azure.com/acme/project/_git/repo" '
            f'"{tmp_path / "repo"}"',
            None,
        )
    ]


def test_git_repository_gateway_stage_paths_and_push_head_use_expected_commands(tmp_path: Path) -> None:
    calls: list[tuple[str, Path | None]] = []

    def fake_runner(command: str, *, cwd: Path | None = None, timeout=None, check: bool = True):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="secret",
        command_runner=fake_runner,
    )

    gateway.stage_paths(tmp_path, ["src/Foo.cs", "tests/Foo.Tests.cs"])
    gateway.push_head(tmp_path)

    assert [command for command, _ in calls] == [
        'git add -- "src/Foo.cs" "tests/Foo.Tests.cs"',
        "git push -u origin HEAD",
    ]
    assert all(cwd == tmp_path for _, cwd in calls)


def test_git_repository_gateway_branch_exists_checks_remote_refs() -> None:
    calls: list[tuple[str, Path | None]] = []

    def fake_runner(command: str, *, cwd: Path | None = None, timeout=None, check: bool = True):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=0, stdout="abc123\trefs/heads/main\n", stderr="")

    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="secret",
        command_runner=fake_runner,
    )

    exists = gateway.branch_exists("main")

    assert exists is True
    assert calls == [
        (
            'git ls-remote --heads "https://:secret@dev.azure.com/acme/project/_git/repo" "refs/heads/main"',
            None,
        )
    ]


def test_git_repository_gateway_branch_exists_returns_false_for_missing_branch() -> None:
    gateway = GitRepositoryGateway(
        remote_url="https://dev.azure.com/acme/project/_git/repo",
        pat="secret",
        command_runner=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert gateway.branch_exists("missing-branch") is False
