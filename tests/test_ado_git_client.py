from __future__ import annotations

from pathlib import Path

import pi_sonar_agent.integrations.ado as ado_module


def test_git_client_delegates_to_git_repository_gateway(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []

    class FakeGateway:
        def __init__(self, *, remote_url: str, pat=None, command_runner=None):
            calls.append(("init", remote_url))

        def clone_repository(self, target_dir: Path, *, branch: str | None = None, depth=None) -> None:
            calls.append(("clone_repository", (target_dir, branch, depth)))

        def create_branch(self, work_dir: Path, branch_name: str) -> None:
            calls.append(("create_branch", (work_dir, branch_name)))

        def stage_paths(self, work_dir: Path, files) -> None:
            calls.append(("stage_paths", (work_dir, list(files) if files else None)))

        def commit_all_changes(self, work_dir: Path, message: str) -> None:
            calls.append(("commit_all_changes", (work_dir, message)))

        def push_head(self, work_dir: Path) -> None:
            calls.append(("push_head", work_dir))

    monkeypatch.setattr(ado_module, "GitRepositoryGateway", FakeGateway)
    client = ado_module.GitClient(tmp_path)

    cloned_path = client.clone("https://dev.azure.com/acme/project/_git/repo", branch="main")
    work_dir = tmp_path / "repo"
    client.create_branch("feature/one", cwd=work_dir)
    client.commit_and_push('fix: say "hello"', files=["src/Foo.cs"], cwd=work_dir)

    assert cloned_path == work_dir
    assert calls == [
        ("init", "https://dev.azure.com/acme/project/_git/repo"),
        ("clone_repository", (work_dir, "main", None)),
        ("init", str(work_dir)),
        ("create_branch", (work_dir, "feature/one")),
        ("init", str(work_dir)),
        ("stage_paths", (work_dir, ["src/Foo.cs"])),
        ("commit_all_changes", (work_dir, 'fix: say "hello"')),
        ("push_head", work_dir),
    ]


def test_git_client_commit_and_push_ignores_nothing_to_commit(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeGateway:
        def __init__(self, *, remote_url: str, pat=None, command_runner=None):
            calls.append(f"init:{remote_url}")

        def stage_paths(self, work_dir: Path, files) -> None:
            calls.append("stage")

        def commit_all_changes(self, work_dir: Path, message: str) -> None:
            raise RuntimeError("nothing to commit, working tree clean")

        def push_head(self, work_dir: Path) -> None:
            calls.append("push")

    monkeypatch.setattr(ado_module, "GitRepositoryGateway", FakeGateway)
    client = ado_module.GitClient(tmp_path)

    client.commit_and_push("fix: noop", cwd=tmp_path)

    assert calls == [f"init:{tmp_path}", "stage"]


def test_upload_pull_request_attachment_posts_binary_payload() -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "fileName": "report.txt",
                "url": "https://dev.azure.com/acme/project/_apis/git/repositories/repo/pullRequests/7/attachments/1",
            }

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def request(self, method: str, url: str, timeout=None, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            captured["data"] = kwargs.get("data")
            captured["headers"] = kwargs.get("headers")
            captured["timeout"] = timeout
            return FakeResponse()

        def close(self) -> None:
            return None

    client = ado_module.AzureDevOpsClient(
        "https://dev.azure.com/acme",
        "project",
        "ado-token",
        organization="acme",
        timeout=15,
    )
    client.session = FakeSession()

    attachment = client.upload_pull_request_attachment(
        repository="repo",
        pull_request_id=7,
        file_name="report.txt",
        content="# Report\n",
    )

    assert attachment.file_name == "report.txt"
    assert attachment.url.endswith("/attachments/1")
    assert captured == {
        "url": "https://dev.azure.com/acme/project/_apis/git/repositories/repo/pullRequests/7/attachments",
        "params": {"fileName": "report.txt", "api-version": "7.1"},
        "data": b"\xef\xbb\xbf# Report\n",
        "headers": {"Content-Type": "application/octet-stream"},
        "timeout": 15,
    }


def test_create_pull_request_retries_after_connection_reset(monkeypatch) -> None:
    calls: list[tuple[str, str, object]] = []

    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "pullRequestId": 42,
                "title": "Fix PR",
                "description": "summary",
                "status": "active",
                "_links": {"web": {"href": "https://dev.azure.com/acme/project/_git/repo/pullrequest/42"}},
            }

    class FakeSession:
        def __init__(self, should_fail: bool = False) -> None:
            self.headers: dict[str, str] = {}
            self.should_fail = should_fail

        def request(self, method: str, url: str, timeout=None, **kwargs):
            calls.append((method, url, timeout))
            if self.should_fail:
                raise ado_module.requests.ConnectionError(
                    ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")
                )
            return FakeResponse()

        def close(self) -> None:
            return None

    sessions = [FakeSession(should_fail=True), FakeSession(should_fail=False)]

    def fake_build_session(self):
        return sessions.pop(0)

    monkeypatch.setattr(ado_module.AzureDevOpsClient, "_build_session", fake_build_session)
    monkeypatch.setattr(ado_module.time, "sleep", lambda seconds: None)

    client = ado_module.AzureDevOpsClient(
        "https://dev.azure.com/acme",
        "project",
        "ado-token",
        organization="acme",
        timeout=15,
    )

    pr = client.create_pull_request(
        repository="repo",
        title="Fix PR",
        description="summary",
        source_branch="feature/pr-retry",
    )

    assert pr.pr_id == 42
    assert len(calls) == 2
    assert calls[0][0] == "post"
    assert calls[1][0] == "post"


def test_create_pull_request_raises_after_exhausting_transport_retries(monkeypatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def request(self, method: str, url: str, timeout=None, **kwargs):
            raise ado_module.requests.ConnectionError(
                ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(ado_module.AzureDevOpsClient, "_build_session", lambda self: FakeSession())
    monkeypatch.setattr(ado_module.time, "sleep", lambda seconds: None)

    client = ado_module.AzureDevOpsClient(
        "https://dev.azure.com/acme",
        "project",
        "ado-token",
        organization="acme",
        timeout=15,
    )

    try:
        client.create_pull_request(
            repository="repo",
            title="Fix PR",
            description="summary",
            source_branch="feature/pr-retry-fail",
        )
        raise AssertionError("expected AzureDevOpsRequestError")
    except ado_module.AzureDevOpsRequestError as exc:
        assert "3 次尝试后仍未成功" in str(exc)
