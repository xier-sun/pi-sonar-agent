from __future__ import annotations

import io
import os
import sys

from pi_sonar_agent.core.run_logging import RunLogSession
from pi_sonar_agent.core.workspace import prune_old_workspaces


def test_run_log_session_tees_stdout_and_stderr(tmp_path, monkeypatch) -> None:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_buffer)
    monkeypatch.setattr(sys, "stderr", stderr_buffer)

    with RunLogSession(run_label="20260330180000", log_root=str(tmp_path), prefix="test") as session:
        print("hello stdout")
        print("hello stderr", file=sys.stderr)

    log_text = (tmp_path / "test_20260330180000.log").read_text(encoding="utf-8")

    assert "hello stdout" in stdout_buffer.getvalue()
    assert "hello stderr" in stderr_buffer.getvalue()
    assert "hello stdout" in log_text
    assert "hello stderr" in log_text
    assert session.log_path.name == "test_20260330180000.log"


def test_prune_old_workspaces_keeps_latest_directory(tmp_path) -> None:
    workspace_a = tmp_path / "workspace_a"
    workspace_b = tmp_path / "workspace_b"
    workspace_c = tmp_path / "workspace_c"

    workspace_a.mkdir()
    workspace_b.mkdir()
    workspace_c.mkdir()

    os.utime(workspace_a, (1, 1))
    os.utime(workspace_b, (2, 2))
    os.utime(workspace_c, (3, 3))

    result = prune_old_workspaces(tmp_path, keep_latest=1)

    assert [path.name for path in result.removed] == ["workspace_b", "workspace_a"]
    assert result.failed == ()
    assert not workspace_a.exists()
    assert not workspace_b.exists()
    assert workspace_c.exists()


def test_prune_old_workspaces_reports_failed_directories(tmp_path, monkeypatch) -> None:
    workspace_a = tmp_path / "workspace_a"
    workspace_b = tmp_path / "workspace_b"

    workspace_a.mkdir()
    workspace_b.mkdir()

    os.utime(workspace_a, (1, 1))
    os.utime(workspace_b, (2, 2))

    import shutil

    original_rmtree = shutil.rmtree

    def fake_rmtree(path, onerror=None):
        if str(path).endswith("workspace_a"):
            return
        return original_rmtree(path, onerror=onerror)

    monkeypatch.setattr("pi_sonar_agent.core.workspace.shutil.rmtree", fake_rmtree)

    result = prune_old_workspaces(tmp_path, keep_latest=1)

    assert result.removed == ()
    assert [path.name for path in result.failed] == ["workspace_a"]
    assert workspace_a.exists()
    assert workspace_b.exists()
