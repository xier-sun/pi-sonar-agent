"""Repository workspace management."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pi_sonar_agent.core.git_gateway import GitRepositoryGateway


@dataclass
class WorkspaceInfo:
    """Information about a workspace."""

    path: Path
    repository: str
    base_branch: str
    created_at: str


@dataclass(frozen=True)
class WorkspacePruneResult:
    """Outcome of pruning old workspace directories."""

    removed: tuple[Path, ...]
    failed: tuple[Path, ...]


class RepositoryWorkspaceSession:
    """Manages repository workspace for fixes."""

    def __init__(
        self,
        remote_url: str,
        pat: str,
        repository: str,
        base_branch: str,
        workspace_root: str = ".agent_workspaces",
        command_timeout_seconds: int = 1800,
    ):
        self.remote_url = remote_url.strip()
        self.pat = pat.strip()
        self.repository = repository
        self.base_branch = base_branch
        self.workspace_root = Path(workspace_root)
        self.command_timeout_seconds = command_timeout_seconds
        self._current_workspace: Path | None = None

    def prepare(self, run_id: str | None = None) -> Path:
        """Prepare workspace by cloning repository."""
        run_label = run_id or time.strftime("%Y%m%d%H%M%S")
        workspace_path = self.workspace_root / f"{self.repository}_{run_label}"

        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)

        self.workspace_root.mkdir(parents=True, exist_ok=True)
        git_gateway = GitRepositoryGateway(remote_url=self.remote_url, pat=self.pat)
        git_gateway.clone_branch(workspace_path, self.base_branch, depth=1)

        self._current_workspace = workspace_path
        return workspace_path

    def cleanup(self) -> None:
        """Clean up the workspace."""
        if self._current_workspace and self._current_workspace.exists():
            shutil.rmtree(self._current_workspace, ignore_errors=True)
            self._current_workspace = None

    def get_current_workspace(self) -> Path | None:
        """Get the current workspace path."""
        return self._current_workspace

    def get_file_content(self, file_path: str) -> str:
        """Get content of a file in the workspace."""
        if not self._current_workspace:
            raise RuntimeError("Workspace not prepared")

        full_path = self._current_workspace / file_path.lstrip("/")
        return full_path.read_text(encoding="utf-8")

    def write_file_content(self, file_path: str, content: str) -> None:
        """Write content to a file in the workspace."""
        if not self._current_workspace:
            raise RuntimeError("Workspace not prepared")

        full_path = self._current_workspace / file_path.lstrip("/")
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")


def clone_repository(
    remote_url: str,
    branch: str,
    target_dir: Path,
    pat: str | None = None,
    depth: int = 1,
) -> None:
    """Clone a repository to a target directory."""
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    git_gateway = GitRepositoryGateway(remote_url=remote_url, pat=pat)
    git_gateway.clone_repository(target_dir, branch=branch or None, depth=depth)


def get_git_diff(workspace_path: Path) -> str:
    """Get git diff for workspace changes."""
    result = subprocess.run(
        "git diff --no-color",
        shell=True,
        cwd=str(workspace_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def get_git_status(workspace_path: Path) -> str:
    """Get git status for workspace."""
    result = subprocess.run(
        "git status",
        shell=True,
        cwd=str(workspace_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def _remove_readonly_and_retry(func, path: str, exc_info) -> None:
    """Clear readonly bit and retry a failed filesystem delete callback."""

    os.chmod(path, stat.S_IWRITE)
    func(path)


def _delete_directory(path: Path) -> bool:
    """Best-effort delete for a workspace directory, with verification."""

    if not path.exists():
        return True

    for _ in range(3):
        try:
            shutil.rmtree(path, onerror=_remove_readonly_and_retry)
        except FileNotFoundError:
            return True
        except Exception:
            time.sleep(0.2)

        if not path.exists():
            return True

    return not path.exists()


def prune_old_workspaces(workspace_root: str | Path, keep_latest: int = 1) -> WorkspacePruneResult:
    """Delete older workspace directories while keeping the most recent ones."""

    root = Path(workspace_root)
    if keep_latest < 0:
        raise ValueError("keep_latest must be greater than or equal to 0")
    if not root.exists():
        return WorkspacePruneResult(removed=(), failed=())

    candidates = [path for path in root.iterdir() if path.is_dir()]
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)

    removed: list[Path] = []
    failed: list[Path] = []
    for path in candidates[keep_latest:]:
        if _delete_directory(path):
            removed.append(path)
        else:
            failed.append(path)

    return WorkspacePruneResult(
        removed=tuple(removed),
        failed=tuple(failed),
    )
