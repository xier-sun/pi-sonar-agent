"""Git repository preparation helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit


class CommandRunner(Protocol):
    """Callable protocol for shell command execution."""

    def __call__(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> Any:
        """Run a shell command."""


@dataclass(frozen=True)
class BaseBranchSelection:
    """Resolved base branch and where it came from."""

    branch: str
    source: str


def resolve_base_branch(
    *,
    cli_branch: str | None = None,
    configured_branch: str | None = None,
    default_branch: str = "develop",
    cli_source: str = "args.base_branch",
    configured_source: str = "targets.json.base_branch",
    default_source: str = "default",
) -> BaseBranchSelection:
    """Resolve the effective base branch from CLI, config, and default values."""

    normalized_cli_branch = (cli_branch or "").strip()
    if normalized_cli_branch:
        return BaseBranchSelection(branch=normalized_cli_branch, source=cli_source)

    normalized_configured_branch = (configured_branch or "").strip()
    if normalized_configured_branch:
        return BaseBranchSelection(branch=normalized_configured_branch, source=configured_source)

    return BaseBranchSelection(branch=default_branch, source=default_source)


def build_authenticated_remote_url(remote_url: str, pat: str | None = None) -> str:
    """Inject a PAT into an HTTPS remote URL when required."""

    normalized_remote = remote_url.strip()
    normalized_pat = (pat or "").strip()

    if not normalized_remote or not normalized_pat:
        return normalized_remote

    parsed = urlsplit(normalized_remote)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return normalized_remote

    if "@" in parsed.netloc:
        return normalized_remote

    encoded_pat = quote(normalized_pat, safe="")
    authenticated_netloc = f":{encoded_pat}@{parsed.netloc}"
    return urlunsplit((parsed.scheme, authenticated_netloc, parsed.path, parsed.query, parsed.fragment))


def redact_remote_url(remote_url: str) -> str:
    """Redact any embedded credentials from a remote URL."""

    normalized_remote = remote_url.strip()
    if not normalized_remote:
        return normalized_remote

    parsed = urlsplit(normalized_remote)
    if "@" not in parsed.netloc:
        return normalized_remote

    _, host = parsed.netloc.rsplit("@", 1)
    return urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment))


class GitRepositoryGateway:
    """Centralized Git gateway for repository preparation."""

    def __init__(
        self,
        *,
        remote_url: str,
        pat: str | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.remote_url = remote_url.strip()
        self.pat = (pat or "").strip() or None
        self.command_runner = command_runner or _run_command_quiet

    @property
    def authenticated_remote_url(self) -> str:
        """Return the effective remote URL used for clone operations."""

        return build_authenticated_remote_url(self.remote_url, self.pat)

    @property
    def redacted_remote_url(self) -> str:
        """Return a redacted URL safe for logs and errors."""

        return redact_remote_url(self.authenticated_remote_url or self.remote_url)

    def clone_repository(
        self,
        workspace_path: Path,
        *,
        branch: str | None = None,
        depth: int | None = None,
    ) -> None:
        """Clone a repository, optionally targeting a specific branch and depth."""

        remote_url = self.authenticated_remote_url or self.remote_url
        single_branch_command = _build_clone_command(
            remote_url=remote_url,
            workspace_path=workspace_path,
            branch=branch,
            depth=depth,
            single_branch=True,
        )
        fallback_command = _build_clone_command(
            remote_url=remote_url,
            workspace_path=workspace_path,
            branch=branch,
            depth=depth,
            single_branch=False,
        )

        try:
            self._run_git_command(
                single_branch_command,
                action=_clone_action(branch),
            )
            return
        except RuntimeError as first_error:
            if not branch:
                raise
            try:
                self._run_git_command(
                    fallback_command,
                    action=_clone_action(branch),
                )
                return
            except RuntimeError as second_error:
                raise RuntimeError(str(first_error)) from second_error

    def branch_exists(self, branch: str) -> bool:
        """Return whether a branch exists on the remote repository."""

        remote_url = self.authenticated_remote_url or self.remote_url
        ref_name = f"refs/heads/{branch}"

        try:
            result = self.command_runner(
                (
                    "git ls-remote --heads "
                    f"{_shell_quote(remote_url)} {_shell_quote(ref_name)}"
                ),
                timeout=60,
                check=False,
            )
        except subprocess.CalledProcessError as exc:
            details = _extract_process_details(exc)
            detail_suffix = f": {details}" if details else ""
            raise RuntimeError(
                f"Git check branch {branch} failed for {self.redacted_remote_url}{detail_suffix}"
            ) from exc

        if getattr(result, "returncode", 0) != 0:
            details = _extract_process_details(
                subprocess.CalledProcessError(
                    getattr(result, "returncode", 1),
                    "git ls-remote",
                    output=getattr(result, "stdout", ""),
                    stderr=getattr(result, "stderr", ""),
                )
            )
            detail_suffix = f": {details}" if details else ""
            raise RuntimeError(
                f"Git check branch {branch} failed for {self.redacted_remote_url}{detail_suffix}"
            )

        return bool(str(getattr(result, "stdout", "") or "").strip())

    def clone_branch(
        self,
        workspace_path: Path,
        branch: str,
        *,
        depth: int | None = None,
    ) -> None:
        """Clone a repository directly from the effective base branch."""

        self.clone_repository(workspace_path, branch=branch, depth=depth)

    def create_branch(self, workspace_path: Path, branch: str) -> None:
        """Create and switch to a new branch in an existing workspace."""

        self._run_git_command(
            f"git checkout -b {_shell_quote(branch)}",
            action=f"create branch {branch}",
            cwd=workspace_path,
        )

    def stage_all_changes(self, workspace_path: Path) -> None:
        """Stage all changes in a workspace."""

        self.stage_paths(workspace_path)

    def stage_paths(self, workspace_path: Path, paths: list[str] | tuple[str, ...] | None = None) -> None:
        """Stage either all changes or a selected list of paths."""

        command = "git add -A"
        if paths:
            quoted_paths = " ".join(_shell_quote(path) for path in paths)
            command = f"git add -- {quoted_paths}"

        self._run_git_command(
            command,
            action="stage changes",
            cwd=workspace_path,
        )

    def commit_all_changes(self, workspace_path: Path, message: str) -> None:
        """Commit all staged changes in a workspace."""

        self._run_git_command(
            f"git commit -m {_shell_quote(message)}",
            action="commit changes",
            cwd=workspace_path,
        )

    def push_branch(self, workspace_path: Path, branch: str) -> None:
        """Push a branch to origin and set upstream tracking."""

        self._run_git_command(
            f"git push -u origin {_shell_quote(branch)}",
            action=f"push branch {branch}",
            cwd=workspace_path,
        )

    def push_head(self, workspace_path: Path) -> None:
        """Push the current HEAD to origin and set upstream tracking."""

        self._run_git_command(
            "git push -u origin HEAD",
            action="push current HEAD",
            cwd=workspace_path,
        )

    def publish_branch(self, workspace_path: Path, branch: str, commit_message: str) -> None:
        """Create a branch, commit all changes, and push it to origin."""

        self.create_branch(workspace_path, branch)
        self.stage_all_changes(workspace_path)
        self.commit_all_changes(workspace_path, commit_message)
        self.push_branch(workspace_path, branch)

    def _run_git_command(
        self,
        command: str,
        *,
        action: str,
        cwd: Path | None = None,
    ) -> None:
        """Run a Git command and raise a sanitized error on failure."""

        try:
            result = self.command_runner(command, cwd=cwd)
            if getattr(result, "returncode", 0) != 0:
                raise subprocess.CalledProcessError(
                    getattr(result, "returncode", 1),
                    command,
                    output=getattr(result, "stdout", ""),
                    stderr=getattr(result, "stderr", ""),
                )
        except subprocess.CalledProcessError as exc:
            details = _extract_process_details(exc)
            detail_suffix = f": {details}" if details else ""
            raise RuntimeError(
                f"Git {action} failed for {self.redacted_remote_url}{detail_suffix}"
            ) from exc


def _extract_process_details(exc: subprocess.CalledProcessError) -> str:
    """Extract a concise error message from a failed subprocess."""

    stderr = str(exc.stderr or "").strip()
    stdout = str(exc.output or "").strip()
    details = stderr or stdout
    return details[:500]


def _build_clone_command(
    *,
    remote_url: str,
    workspace_path: Path,
    branch: str | None,
    depth: int | None,
    single_branch: bool,
) -> str:
    """Build a clone command with optional branch and depth flags."""

    parts = ["git clone"]
    if depth and depth > 0:
        parts.append(f"--depth {depth}")
    if branch:
        parts.append(f"-b {_shell_quote(branch)}")
        if single_branch:
            parts.append("--single-branch")
    parts.append(_shell_quote(remote_url))
    parts.append(_shell_quote(str(workspace_path)))
    return " ".join(parts)


def _clone_action(branch: str | None) -> str:
    """Describe the clone action for error reporting."""

    if branch:
        return f"clone branch {branch}"
    return "clone repository"


def _run_command_quiet(
    command: str,
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command without echoing raw process output to the console."""

    result = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )

    return result


def _shell_quote(value: str) -> str:
    """Quote a shell argument for the current command style."""

    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'
