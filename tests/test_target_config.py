from __future__ import annotations

from argparse import Namespace

from pi_sonar_agent.core.target_config import (
    missing_required_target_fields,
    resolve_batch_target_config,
    resolve_cli_target_config,
)


def test_resolve_cli_target_config_uses_cli_env_and_defaults() -> None:
    args = Namespace(
        project_key="",
        repository="",
        author="",
        max_issues=None,
        base_branch="release/2026.04",
        build_command="",
        test_command="",
        solution_path="",
    )
    target_defaults = {
        "project_key": "target-project",
        "repository": "target-repo",
        "author": "target-author",
        "reviewer_email": "reviewer@example.com",
        "dingtalk_userid": "ding-user",
        "base_branch": "main",
        "build_command": "dotnet build target.sln",
        "test_command": "dotnet test target.sln",
        "solution_path": "target.sln",
        "max_issues": "7",
    }
    environ = {
        "PROJECT_KEY": "env-project",
        "REPOSITORY": "env-repo",
        "AUTHOR": "env-author",
        "BUILD_COMMAND": "dotnet build env.sln",
        "TEST_COMMAND": "dotnet test env.sln",
        "SOLUTION_PATH": "env.sln",
        "MAX_ISSUES": "5",
    }

    config = resolve_cli_target_config(
        args,
        target_defaults,
        environ=environ,
        default_base_branch="develop",
        default_max_issues=0,
    )

    assert config.project_key == "env-project"
    assert config.repository == "env-repo"
    assert config.author == "env-author"
    assert config.reviewer_email == "reviewer@example.com"
    assert config.dingtalk_userid == "ding-user"
    assert config.base_branch == "release/2026.04"
    assert config.base_branch_source == "args.base_branch"
    assert config.build_command == "dotnet build env.sln"
    assert config.test_command == "dotnet test env.sln"
    assert config.solution_path == "env.sln"
    assert config.max_issues == 5


def test_resolve_batch_target_config_uses_target_defaults() -> None:
    config = resolve_batch_target_config(
        {
            "project_key": "project-a",
            "repository": "repo-a",
            "author": "alice@example.com",
            "base_branch": "main",
            "build_command": "dotnet build Foo.sln",
            "test_command": "dotnet test Foo.sln",
            "solution_path": "Foo.sln",
        },
        default_base_branch="develop",
        default_max_issues=3,
    )

    assert config.project_key == "project-a"
    assert config.repository == "repo-a"
    assert config.author == "alice@example.com"
    assert config.base_branch == "main"
    assert config.base_branch_source == "targets.json.base_branch"
    assert config.build_command == "dotnet build Foo.sln"
    assert config.test_command == "dotnet test Foo.sln"
    assert config.solution_path == "Foo.sln"
    assert config.max_issues == 3


def test_missing_required_target_fields_reports_empty_values() -> None:
    args = Namespace(
        project_key="",
        repository="",
        author="",
        max_issues=None,
        base_branch="",
        build_command="",
        test_command="",
        solution_path="",
    )

    config = resolve_cli_target_config(
        args,
        {},
        environ={},
        default_base_branch="develop",
        default_max_issues=0,
    )

    assert missing_required_target_fields(config) == ["project_key", "repository", "author"]


def test_resolve_cli_target_config_does_not_fall_back_to_os_environ_for_empty_mapping(
    monkeypatch,
) -> None:
    args = Namespace(
        project_key="",
        repository="",
        author="",
        max_issues=None,
        base_branch="",
        build_command="",
        test_command="",
        solution_path="",
    )
    monkeypatch.setenv("PROJECT_KEY", "host-project")
    monkeypatch.setenv("REPOSITORY", "host-repo")
    monkeypatch.setenv("AUTHOR", "host-author")

    config = resolve_cli_target_config(
        args,
        {},
        environ={},
        default_base_branch="develop",
        default_max_issues=0,
    )

    assert config.project_key == ""
    assert config.repository == ""
    assert config.author == ""
