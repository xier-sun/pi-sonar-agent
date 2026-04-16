"""Roslyn-based code fix engine.

This module provides integration with the C# Roslyn analyzer for
automatically fixing certain types of SonarQube issues.

The actual Roslyn engine is a separate C# process that performs
the code analysis and transformation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sonar_agent.fixers.deterministic import IssueGroup

SUPPORTED_ROSLYN_RULES = frozenset({"csharpsquid:S107"})


@dataclass(frozen=True)
class RoslynFixResult:
    """Result of a Roslyn fix operation."""

    applied: bool
    updated_content: str
    strategy: str
    summary: str
    error: str = ""
    can_fix_safely: bool = False
    safety_flags: tuple[str, ...] = ()
    changed_files: dict[str, str] | None = None


@dataclass(frozen=True)
class RoslynAvailability:
    """Availability probe result for the Roslyn fix engine."""

    available: bool
    reasons: tuple[str, ...] = ()


class RoslynFixEngine:
    """Wrapper for the C# Roslyn fix engine."""

    def __init__(
        self,
        project_path: str = "fix_engine/AgentFixEngine.csproj",
        configuration: str = "Release",
        timeout_seconds: int = 120,
    ):
        self.project_path = Path(project_path)
        self.configuration = configuration
        self.timeout_seconds = timeout_seconds
        self._built_dll_path: Path | None = None

    def apply_fix(
        self,
        *,
        file_content: str,
        issue_group: IssueGroup,
        primary_issue: dict[str, Any],
    ) -> RoslynFixResult:
        """Apply a Roslyn-based fix to a single file."""
        dll_path = self._ensure_built()
        if not dll_path:
            return RoslynFixResult(
                applied=False,
                updated_content=file_content,
                strategy="roslyn:unavailable",
                summary="",
                error="Roslyn engine not built",
            )

        request_payload = {
            "ruleId": issue_group.rule,
            "filePath": issue_group.file_path,
            "fileContent": file_content,
            "startLine": issue_group.start_line,
            "endLine": issue_group.end_line,
            "primaryMessage": str(primary_issue.get("message", "")),
            "symbolNames": list(issue_group.symbol_names),
            "issues": [
                {
                    "key": str(issue.get("key", "")),
                    "line": int(
                        issue.get("line")
                        or issue.get("textRange", {}).get("startLine")
                        or 0
                    ),
                    "message": str(issue.get("message", "")),
                }
                for issue in issue_group.issues
            ],
        }

        payload = self._invoke_engine(
            dll_path=dll_path,
            arguments=["--request"],
            request_payload=request_payload,
        )

        return RoslynFixResult(
            applied=bool(payload.get("applied", False)),
            updated_content=str(payload.get("updatedFileContent", file_content)),
            strategy=str(payload.get("strategy", "roslyn:unknown")),
            summary=str(payload.get("summary", "")),
            error=str(payload.get("error", "")),
            can_fix_safely=bool(payload.get("canFixSafely", False)),
            safety_flags=tuple(str(item).strip() for item in (payload.get("safetyFlags", []) or []) if str(item).strip()),
            changed_files=(
                {
                    str(file_path): str(content)
                    for file_path, content in (payload.get("changedFiles", {}) or {}).items()
                }
                or None
            ),
        )

    def apply_solution_fix(
        self,
        *,
        workspace_path: str,
        solution_path: str,
        issue_group: IssueGroup,
        primary_issue: dict[str, Any],
    ) -> RoslynFixResult:
        """Apply a solution-level Roslyn fix (may modify multiple files)."""
        dll_path = self._ensure_built()
        if not dll_path:
            return RoslynFixResult(
                applied=False,
                updated_content="",
                strategy="roslyn:unavailable",
                summary="",
                error="Roslyn engine not built",
            )

        request_payload = {
            "ruleId": issue_group.rule,
            "solutionPath": solution_path,
            "workspaceRoot": workspace_path,
            "filePath": issue_group.file_path,
            "startLine": issue_group.start_line,
            "endLine": issue_group.end_line,
            "primaryMessage": str(primary_issue.get("message", "")),
            "symbolNames": list(issue_group.symbol_names),
            "issues": [
                {
                    "key": str(issue.get("key", "")),
                    "line": int(
                        issue.get("line")
                        or issue.get("textRange", {}).get("startLine")
                        or 0
                    ),
                    "message": str(issue.get("message", "")),
                }
                for issue in issue_group.issues
            ],
        }

        payload = self._invoke_engine(
            dll_path=dll_path,
            arguments=["--solution-request"],
            request_payload=request_payload,
        )

        return RoslynFixResult(
            applied=bool(payload.get("applied", False)),
            updated_content="",
            strategy=str(payload.get("strategy", "roslyn:unknown")),
            summary=str(payload.get("summary", "")),
            error=str(payload.get("error", "")),
            can_fix_safely=bool(payload.get("canFixSafely", False)),
            safety_flags=tuple(str(item).strip() for item in (payload.get("safetyFlags", []) or []) if str(item).strip()),
            changed_files=(
                {
                    str(file_path): str(content)
                    for file_path, content in (payload.get("changedFiles", {}) or {}).items()
                }
                or None
            ),
        )

    def _ensure_built(self) -> Path | None:
        """Ensure the Roslyn engine is built."""
        if self._built_dll_path and self._built_dll_path.exists():
            return self._built_dll_path

        if not self.project_path.exists():
            return None

        # Build the project
        result = None
        for attempt_index in range(2):
            result = subprocess.run(
                ["dotnet", "build", str(self.project_path), "-c", self.configuration],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            if result.returncode == 0:
                break
            combined_output = f"{result.stdout}\n{result.stderr}".lower()
            if attempt_index == 0 and ("cs2012" in combined_output or "being used by another process" in combined_output):
                time.sleep(1.0)
                continue
            break

        if result is None or result.returncode != 0:
            return None

        # Find the output DLL
        dll_path = (
            self.project_path.parent
            / "bin"
            / self.configuration
            / "net8.0"
            / "AgentFixEngine.dll"
        )

        if dll_path.exists():
            self._built_dll_path = dll_path
            return dll_path

        return None

    def _invoke_engine(
        self,
        dll_path: Path,
        arguments: list[str],
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the Roslyn engine with a request."""
        input_json = json.dumps(request_payload)

        try:
            result = subprocess.run(
                ["dotnet", str(dll_path)] + arguments,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                encoding="utf-8",
            )

            if result.returncode != 0:
                return {"applied": False, "error": result.stderr or "Process failed"}

            # Parse JSON output
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"applied": False, "error": "Invalid JSON output"}

        except subprocess.TimeoutExpired:
            return {"applied": False, "error": f"Timeout after {self.timeout_seconds}s"}
        except Exception as e:
            return {"applied": False, "error": str(e)}


def is_roslyn_available() -> bool:
    """Check if Roslyn engine is available."""
    engine = RoslynFixEngine()
    return engine._ensure_built() is not None


def supports_roslyn_rule(rule_id: str) -> bool:
    """Return whether the current Roslyn engine implements the given rule."""

    return str(rule_id or "").strip() in SUPPORTED_ROSLYN_RULES


def inspect_roslyn_availability(
    project_path: str = "fix_engine/AgentFixEngine.csproj",
) -> tuple[bool, tuple[str, ...]]:
    """Perform a light-weight availability probe for the Roslyn engine."""

    reasons: list[str] = []
    project = Path(project_path)
    if not project.exists():
        reasons.append(f"missing project file: {project.as_posix()}")
    if shutil.which("dotnet") is None:
        reasons.append("dotnet SDK not found on PATH")
    return (not reasons, tuple(reasons))
