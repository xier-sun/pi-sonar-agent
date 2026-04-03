"""Build gate module for verifying fixes.

This module handles:
- Local build verification (dotnet build)
- Test execution (dotnet test)
- Optional SonarQube rescan
- Format validation
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.git_gateway import GitRepositoryGateway


@dataclass(frozen=True)
class BuildGateResult:
    """Result of build gate execution."""

    succeeded: bool
    format_command: str | None
    build_command: str
    test_command: str | None
    build_target: str
    log_path: str
    workspace_path: str
    duration_seconds: float
    final_file_changes: dict[str, str] | None = None
    sonar_gate_enabled: bool = False
    sonar_gate_passed: bool = False
    sonar_gate_skipped: bool = False
    sonar_quality_gate_status: str = ""
    sonar_branch_name: str = ""
    sonar_summary: str = ""
    resolved_after_rescan: tuple[dict[str, str], ...] = ()
    unresolved_after_rescan: tuple[dict[str, str], ...] = ()
    new_issues_after_rescan: tuple[dict[str, str], ...] = ()
    verification_mode: str = ""
    error: str = ""


@dataclass(frozen=True)
class SonarIssueExpectation:
    """Expected issue after fix."""

    key: str
    rule: str
    file_path: str
    line: int = 0


@dataclass(frozen=True)
class SonarRescanConfig:
    """Configuration for SonarQube rescan."""

    host: str
    token: str
    project_key: str
    organization: str | None = None
    scanner_command: str = "dotnet sonarscanner"
    branch_name: str = ""
    supports_branch_analysis: bool = False
    timeout_seconds: int = 600
    poll_interval_seconds: int = 5
    strict_quality_gate: bool = True
    expected_issues: tuple[SonarIssueExpectation, ...] = ()
    changed_file_paths: tuple[str, ...] = ()


def _run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command with stable text decoding across Windows environments."""

    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _normalize_process_text(value: str | None) -> str:
    """Normalize subprocess text streams so callers can concatenate safely."""

    return value if isinstance(value, str) else ""


def _combined_process_output(result: subprocess.CompletedProcess[str]) -> str:
    """Combine stdout and stderr without failing on missing streams."""

    return f"{_normalize_process_text(result.stdout)}{_normalize_process_text(result.stderr)}"


def _tail_text(text: str, max_lines: int) -> str:
    """Return the trailing lines of text, with a marker when truncation happens."""

    normalized = _normalize_process_text(text).strip()
    if not normalized:
        return ""

    lines = normalized.splitlines()
    if len(lines) <= max_lines:
        return normalized

    tail = "\n".join(lines[-max_lines:])
    omitted = len(lines) - max_lines
    return f"... (省略前 {omitted} 行)\n{tail}"


def _extract_error_lines(text: str, max_lines: int) -> str:
    """Extract likely compiler/build error lines from command output."""

    normalized = _normalize_process_text(text).strip()
    if not normalized:
        return ""

    lines = [
        line for line in normalized.splitlines()
        if " error " in f" {line.lower()} " or ": error" in line.lower()
    ]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    tail = "\n".join(lines[-max_lines:])
    omitted = len(lines) - max_lines
    return f"... (省略前 {omitted} 条错误)\n{tail}"


def _command_has_explicit_target(command: str, solution_path: str | None = None) -> bool:
    """Check whether a command already points at a solution/project target."""

    normalized = command.lower().replace("\\", "/")
    if any(ext in normalized for ext in (".sln", ".csproj", ".fsproj", ".vbproj")):
        return True

    target = _normalize_process_text(solution_path).strip().lower().replace("\\", "/")
    return bool(target and target in normalized)


def resolve_build_command(build_command: str | None, solution_path: str | None = None) -> str:
    """Resolve the effective build command, appending solution_path when appropriate."""

    command = _normalize_process_text(build_command).strip() or "dotnet build"
    target = _normalize_process_text(solution_path).strip()
    if not target or _command_has_explicit_target(command, target):
        return command
    if command.lower().startswith("dotnet build"):
        return f'{command} "{target}"'
    return command


def resolve_test_command(test_command: str | None, solution_path: str | None = None) -> str | None:
    """Resolve the effective test command, appending solution_path when appropriate."""

    command = _normalize_process_text(test_command).strip()
    if not command:
        return None
    target = _normalize_process_text(solution_path).strip()
    if not target or _command_has_explicit_target(command, target):
        return command
    if command.lower().startswith("dotnet test"):
        return f'{command} "{target}"'
    return command


def format_build_failure_report(result: dict[str, Any], max_lines: int = 80) -> str:
    """Format a readable build/test failure report for console output."""

    sections: list[str] = []

    error_text = _normalize_process_text(result.get("error"))
    if error_text:
        sections.append(f"错误: {error_text}")

    build_command = _normalize_process_text(result.get("build_command"))
    if build_command:
        sections.append(f"构建命令: {build_command}")

    build_output = _tail_text(_normalize_process_text(result.get("build_output")), max_lines)
    build_errors = _extract_error_lines(_normalize_process_text(result.get("build_output")), max_lines)
    if build_errors:
        sections.append(f"关键错误:\n{build_errors}")
    if build_output:
        sections.append(f"构建日志:\n{build_output}")

    test_command = _normalize_process_text(result.get("test_command"))
    if test_command:
        sections.append(f"测试命令: {test_command}")

    test_output = _tail_text(_normalize_process_text(result.get("test_output")), max_lines)
    test_errors = _extract_error_lines(_normalize_process_text(result.get("test_output")), max_lines)
    if test_errors:
        sections.append(f"测试关键错误:\n{test_errors}")
    if test_output:
        sections.append(f"测试日志:\n{test_output}")

    return "\n\n".join(section for section in sections if section)


class LocalBuildGate:
    """Local build verification gate."""

    def __init__(
        self,
        remote_url: str,
        pat: str,
        workspace_root: str = ".agent_workspaces",
        log_root: str = "logs/build_gate",
        command_timeout_seconds: int = 1800,
    ):
        self.remote_url = remote_url.strip()
        self.pat = pat.strip()
        self.workspace_root = Path(workspace_root)
        self.log_root = Path(log_root)
        self.command_timeout_seconds = command_timeout_seconds

    def run(
        self,
        *,
        repository: str,
        base_branch: str,
        file_changes: dict[str, str],
        solution_path: str | None = None,
        format_command: str | None = None,
        build_command: str | None = None,
        test_command: str | None = None,
        sonar_rescan: SonarRescanConfig | None = None,
        keep_workspace: bool = False,
        run_id: str | None = None,
    ) -> BuildGateResult:
        """Run the build gate."""
        if not file_changes:
            raise RuntimeError("Build Gate 未收到任何文件变更。")

        run_label = run_id or time.strftime("%Y%m%d%H%M%S")
        workspace_path = self._build_workspace_path(repository=repository, run_label=run_label)
        log_path = self._build_log_path(repository=repository, run_label=run_label)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)

        started_at = time.time()
        resolved_format_command = None
        resolved_build_command = ""
        resolved_test_command = test_command.strip() if test_command and test_command.strip() else None
        build_target = ""
        sonar_gate_enabled = sonar_rescan is not None
        sonar_gate_passed = False
        sonar_gate_skipped = False
        sonar_quality_gate_status = ""
        sonar_branch_name = sonar_rescan.branch_name if sonar_rescan else ""
        sonar_summary = ""
        resolved_after_rescan: tuple[dict[str, str], ...] = ()
        unresolved_after_rescan: tuple[dict[str, str], ...] = ()
        new_issues_after_rescan: tuple[dict[str, str], ...] = ()
        verification_mode = "build+test" if resolved_test_command else "build"

        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                # Clone repository
                self._clone_repository(
                    workspace_path=workspace_path,
                    base_branch=base_branch,
                    log_file=log_file,
                )

                # Apply file changes
                self._apply_file_changes(
                    workspace_path=workspace_path,
                    file_changes=file_changes,
                )

                # Resolve build target
                build_target_path = self.resolve_build_target_path(
                    workspace_path=workspace_path,
                    solution_path=solution_path,
                )
                build_target = self._format_build_target(
                    workspace_path=workspace_path,
                    build_target_path=build_target_path,
                )

                # Format command
                resolved_format_command = (
                    format_command.strip()
                    if format_command and format_command.strip()
                    else None
                )
                if resolved_format_command:
                    if not self._has_editorconfig(workspace_path):
                        log_file.write("[WARN] 当前仓库未发现 .editorconfig\n")

                    log_file.write(f"\n=== Running format: {resolved_format_command} ===\n")
                    format_result = _run_shell_command(
                        resolved_format_command,
                        cwd=workspace_path,
                        timeout=120,
                    )
                    log_file.write(_normalize_process_text(format_result.stdout))
                    if format_result.stderr:
                        log_file.write(f"[STDERR] {format_result.stderr}\n")

                # Build command
                resolved_build_command = (
                    build_command.strip()
                    if build_command and build_command.strip()
                    else f"dotnet build {build_target}"
                )

                log_file.write(f"\n=== Running build: {resolved_build_command} ===\n")
                build_result = _run_shell_command(
                    resolved_build_command,
                    cwd=workspace_path,
                    timeout=self.command_timeout_seconds,
                )
                log_file.write(_normalize_process_text(build_result.stdout))
                if build_result.stderr:
                    log_file.write(f"[STDERR] {build_result.stderr}\n")

                if build_result.returncode != 0:
                    return BuildGateResult(
                        succeeded=False,
                        format_command=resolved_format_command,
                        build_command=resolved_build_command,
                        test_command=resolved_test_command,
                        build_target=build_target,
                        log_path=str(log_path),
                        workspace_path=str(workspace_path),
                        duration_seconds=time.time() - started_at,
                        verification_mode=verification_mode,
                        error=f"Build failed with exit code {build_result.returncode}",
                    )

                # Test command
                if resolved_test_command:
                    log_file.write(f"\n=== Running tests: {resolved_test_command} ===\n")
                    test_result = _run_shell_command(
                        resolved_test_command,
                        cwd=workspace_path,
                        timeout=self.command_timeout_seconds,
                    )
                    log_file.write(_normalize_process_text(test_result.stdout))
                    if test_result.stderr:
                        log_file.write(f"[STDERR] {test_result.stderr}\n")

                    if test_result.returncode != 0:
                        return BuildGateResult(
                            succeeded=False,
                            format_command=resolved_format_command,
                            build_command=resolved_build_command,
                            test_command=resolved_test_command,
                            build_target=build_target,
                            log_path=str(log_path),
                            workspace_path=str(workspace_path),
                            duration_seconds=time.time() - started_at,
                            verification_mode=verification_mode,
                            error=f"Tests failed with exit code {test_result.returncode}",
                        )

                # Get final file changes
                final_changes = self._get_file_changes(workspace_path, file_changes.keys())

                # SonarQube rescan (if configured)
                if sonar_rescan:
                    log_file.write("\n=== Running SonarQube rescan ===\n")
                    sonar_result = self._run_sonar_rescan(
                        workspace_path=workspace_path,
                        config=sonar_rescan,
                        log_file=log_file,
                    )
                    sonar_gate_passed = sonar_result["passed"]
                    sonar_gate_skipped = sonar_result["skipped"]
                    sonar_quality_gate_status = sonar_result.get("quality_gate_status", "")
                    sonar_summary = sonar_result.get("summary", "")
                    resolved_after_rescan = tuple(sonar_result.get("resolved", []))
                    unresolved_after_rescan = tuple(sonar_result.get("unresolved", []))
                    new_issues_after_rescan = tuple(sonar_result.get("new_issues", []))

                return BuildGateResult(
                    succeeded=True,
                    format_command=resolved_format_command,
                    build_command=resolved_build_command,
                    test_command=resolved_test_command,
                    build_target=build_target,
                    log_path=str(log_path),
                    workspace_path=str(workspace_path),
                    duration_seconds=time.time() - started_at,
                    final_file_changes=final_changes,
                    sonar_gate_enabled=sonar_gate_enabled,
                    sonar_gate_passed=sonar_gate_passed,
                    sonar_gate_skipped=sonar_gate_skipped,
                    sonar_quality_gate_status=sonar_quality_gate_status,
                    sonar_branch_name=sonar_branch_name,
                    sonar_summary=sonar_summary,
                    resolved_after_rescan=resolved_after_rescan,
                    unresolved_after_rescan=unresolved_after_rescan,
                    new_issues_after_rescan=new_issues_after_rescan,
                    verification_mode=verification_mode,
                )

        except subprocess.TimeoutExpired:
            return BuildGateResult(
                succeeded=False,
                format_command=resolved_format_command,
                build_command=resolved_build_command,
                test_command=resolved_test_command,
                build_target=build_target,
                log_path=str(log_path),
                workspace_path=str(workspace_path),
                duration_seconds=time.time() - started_at,
                verification_mode=verification_mode,
                error="Build/test timed out",
            )
        except Exception as e:
            return BuildGateResult(
                succeeded=False,
                format_command=resolved_format_command,
                build_command=resolved_build_command,
                test_command=resolved_test_command,
                build_target=build_target,
                log_path=str(log_path),
                workspace_path=str(workspace_path),
                duration_seconds=time.time() - started_at,
                verification_mode=verification_mode,
                error=str(e),
            )
        finally:
            if not keep_workspace and workspace_path.exists():
                shutil.rmtree(workspace_path, ignore_errors=True)

    def _build_workspace_path(self, repository: str, run_label: str) -> Path:
        return self.workspace_root / f"{repository}_{run_label}"

    def _build_log_path(self, repository: str, run_label: str) -> Path:
        return self.log_root / f"build_{repository}_{run_label}.log"

    def _clone_repository(
        self,
        workspace_path: Path,
        base_branch: str,
        log_file: Any,
    ) -> None:
        """Clone the repository."""
        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)

        log_file.write(f"Cloning {base_branch} branch...\n")
        git_gateway = GitRepositoryGateway(remote_url=self.remote_url, pat=self.pat)
        git_gateway.clone_branch(workspace_path, base_branch)

    def _apply_file_changes(
        self,
        workspace_path: Path,
        file_changes: dict[str, str],
    ) -> None:
        """Apply file changes to workspace."""
        for rel_path, content in file_changes.items():
            file_path = workspace_path / rel_path.lstrip("/")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

    def resolve_build_target_path(
        self,
        workspace_path: Path,
        solution_path: str | None = None,
    ) -> Path | None:
        """Resolve the build target path."""
        if solution_path:
            return workspace_path / solution_path

        # Try to find .sln file
        sln_files = list(workspace_path.glob("**/*.sln"))
        if sln_files:
            return sln_files[0]

        # Try to find .csproj file
        csproj_files = list(workspace_path.glob("**/*.csproj"))
        if csproj_files:
            return csproj_files[0]

        return None

    def _format_build_target(
        self,
        workspace_path: Path,
        build_target_path: Path | None,
    ) -> str:
        """Format the build command target."""
        if not build_target_path:
            return ""

        # Make path relative to workspace
        try:
            rel_path = build_target_path.relative_to(workspace_path)
            return str(rel_path)
        except ValueError:
            return str(build_target_path)

    def _has_editorconfig(self, workspace_path: Path) -> bool:
        """Check if workspace has .editorconfig."""
        return (workspace_path / ".editorconfig").exists()

    def _get_file_changes(
        self,
        workspace_path: Path,
        original_files: list[str],
    ) -> dict[str, str]:
        """Get final file contents after changes."""
        changes = {}
        for rel_path in original_files:
            file_path = workspace_path / rel_path.lstrip("/")
            if file_path.exists():
                changes[rel_path] = file_path.read_text(encoding="utf-8")
        return changes

    def _run_sonar_rescan(
        self,
        workspace_path: Path,
        config: SonarRescanConfig,
        log_file: Any,
    ) -> dict[str, Any]:
        """Run SonarQube rescan."""
        log_file.write(f"Note: Sonar rescan is a placeholder - would run: {config.scanner_command}\n")

        # In a full implementation, this would:
        # 1. Run sonar scanner
        # 2. Wait for analysis to complete
        # 3. Fetch new issues
        # 4. Compare with expected issues

        return {
            "passed": True,
            "skipped": True,
            "quality_gate_status": "SKIPPED",
            "summary": "Sonar rescan not implemented in Python",
            "resolved": [],
            "unresolved": [],
            "new_issues": [],
        }


def run_local_build(
    workspace_path: Path,
    build_command: str,
    test_command: str | None = None,
    timeout_seconds: int = 1800,
    solution_path: str | None = None,
) -> dict[str, Any]:
    """Run a local build and optionally tests."""

    resolved_build_command = resolve_build_command(build_command, solution_path)
    resolved_test_command = resolve_test_command(test_command, solution_path)

    # Run build
    build_result = _run_shell_command(
        resolved_build_command,
        cwd=workspace_path,
        timeout=timeout_seconds,
    )
    build_stdout = _normalize_process_text(build_result.stdout)
    build_stderr = _normalize_process_text(build_result.stderr)

    if build_result.returncode != 0:
        return {
            "succeeded": False,
            "build_passed": False,
            "test_passed": False,
            "build_command": resolved_build_command,
            "test_command": resolved_test_command,
            "error": f"Build failed: {build_stderr}",
            "build_output": f"{build_stdout}{build_stderr}",
        }

    # Run tests if provided
    test_passed = True
    if resolved_test_command:
        test_result = _run_shell_command(
            resolved_test_command,
            cwd=workspace_path,
            timeout=timeout_seconds,
        )
        test_passed = test_result.returncode == 0
        test_stdout = _normalize_process_text(test_result.stdout)
        test_stderr = _normalize_process_text(test_result.stderr)

        if not test_passed:
            return {
                "succeeded": False,
                "build_passed": True,
                "test_passed": False,
                "build_command": resolved_build_command,
                "test_command": resolved_test_command,
                "error": f"Tests failed: {test_stderr}",
                "build_output": build_stdout,
                "test_output": f"{test_stdout}{test_stderr}",
            }

    return {
        "succeeded": True,
        "build_passed": True,
        "test_passed": test_passed,
        "build_command": resolved_build_command,
        "test_command": resolved_test_command,
        "build_output": build_stdout,
    }
