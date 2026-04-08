"""Attempt-local diff facts used by boundary review and quality gates."""

from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange, ReviewedLineOperation

_HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
@dataclass(frozen=True)
class TouchedLineFacts:
    """Normalized patch facts in before/after coordinate spaces."""

    before_changed_lines: tuple[int, ...]
    after_changed_lines: tuple[int, ...]
    line_operations: tuple[ReviewedLineOperation, ...]


class AttemptFileChangeBuilder:
    """Build stable per-file diff facts for one issue attempt."""

    @staticmethod
    def build_content_diff(
        original_content: str,
        current_content: str,
        relative_path: str,
    ) -> str:
        return "\n".join(
            difflib.unified_diff(
                original_content.splitlines(),
                current_content.splitlines(),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                n=0,
                lineterm="",
            )
        )

    @classmethod
    def extract_touched_line_facts(cls, diff_text: str) -> TouchedLineFacts:
        """Extract normalized before/after touched lines from unified diff text."""

        before_touched_lines: set[int] = set()
        after_touched_lines: set[int] = set()
        operations: list[ReviewedLineOperation] = []
        current_old_line = 0
        current_new_line = 0
        pending_deleted_lines: list[int] = []

        for raw_line in (diff_text or "").splitlines():
            header_match = _HUNK_HEADER_PATTERN.match(raw_line)
            if header_match:
                current_old_line = int(header_match.group("old_start"))
                current_new_line = int(header_match.group("new_start"))
                pending_deleted_lines = []
                continue

            if raw_line.startswith(("--- ", "+++ ")):
                continue

            if raw_line.startswith("+"):
                before_anchor = (
                    pending_deleted_lines.pop(0)
                    if pending_deleted_lines
                    else max(current_old_line, 1)
                )
                after_line = max(current_new_line, 1)
                before_touched_lines.add(before_anchor)
                after_touched_lines.add(after_line)
                operations.append(
                    ReviewedLineOperation(
                        kind="add",
                        before_line=before_anchor,
                        after_line=after_line,
                        text=raw_line[1:],
                    )
                )
                current_new_line += 1
                continue

            if raw_line.startswith("-"):
                before_line = max(current_old_line, 1)
                before_touched_lines.add(before_line)
                pending_deleted_lines.append(before_line)
                operations.append(
                    ReviewedLineOperation(
                        kind="delete",
                        before_line=before_line,
                        after_line=max(current_new_line, 0),
                        text=raw_line[1:],
                    )
                )
                current_old_line += 1
                continue

            if raw_line.startswith(" "):
                pending_deleted_lines = []
                current_old_line += 1
                current_new_line += 1

        return TouchedLineFacts(
            before_changed_lines=tuple(sorted(before_touched_lines)),
            after_changed_lines=tuple(sorted(after_touched_lines)),
            line_operations=tuple(operations),
        )

    @classmethod
    def extract_touched_line_numbers(cls, diff_text: str) -> set[int]:
        """Extract the exact before-file line numbers touched by the patch."""

        return set(cls.extract_touched_line_facts(diff_text).before_changed_lines)

    @staticmethod
    def count_hunks(diff_text: str) -> int:
        return sum(1 for line in diff_text.splitlines() if line.startswith("@@ "))

    @staticmethod
    def _read_text_file(file_path: Path) -> str:
        return file_path.read_text(encoding="utf-8", errors="replace")

    @classmethod
    def _read_git_file_at_commit(
        cls,
        workspace_path: Path,
        commit: str,
        rel_path: str,
    ) -> str | None:
        normalized_path = str(rel_path or "").replace("\\", "/").lstrip("/")
        if not commit or not normalized_path:
            return None
        try:
            result = subprocess.run(
                ["git", "show", f"{commit}:{normalized_path}"],
                cwd=str(workspace_path),
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception:
            return None

        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace")

    @classmethod
    def _load_before_text(
        cls,
        *,
        workspace_path: Path,
        files_root: Path,
        rel_path: str,
        manifest: dict[str, object],
    ) -> tuple[bool, str]:
        existing_before = {
            str(path).replace("\\", "/")
            for path in manifest.get("existing_paths", [])
            if str(path).strip()
        }
        normalized_path = str(rel_path or "").replace("\\", "/").lstrip("/")

        if normalized_path in existing_before:
            snapshot_file = files_root / normalized_path
            if snapshot_file.is_file():
                return True, cls._read_text_file(snapshot_file)

        baseline_head = str(manifest.get("head_commit", "")).strip()
        git_text = cls._read_git_file_at_commit(workspace_path, baseline_head, normalized_path)
        if git_text is not None:
            return True, git_text

        return False, ""

    @classmethod
    def build(
        cls,
        *,
        workspace_path: Path,
        changed_files: tuple[str, ...] | list[str],
        manifest: dict[str, object] | None = None,
    ) -> tuple[ReviewedFileChange, ...]:
        """Build reviewed file changes from the attempt baseline and workspace."""

        normalized_manifest = manifest or {}
        files_root = workspace_path / ".git" / "pi-sonar-agent-attempt-state" / "files"
        file_changes: list[ReviewedFileChange] = []

        for rel_path in sorted(
            {
                str(path).replace("\\", "/").lstrip("/")
                for path in changed_files
                if str(path).strip()
            }
        ):
            current_file = workspace_path / rel_path
            after_exists = current_file.is_file()
            before_exists, before_text = cls._load_before_text(
                workspace_path=workspace_path,
                files_root=files_root,
                rel_path=rel_path,
                manifest=normalized_manifest,
            )
            after_text = cls._read_text_file(current_file) if after_exists else ""

            diff_text = cls.build_content_diff(before_text, after_text, rel_path)
            if not diff_text and not before_exists and not after_exists:
                continue

            touched_line_facts = cls.extract_touched_line_facts(diff_text)
            file_changes.append(
                ReviewedFileChange(
                    file=rel_path,
                    changed_lines=touched_line_facts.before_changed_lines,
                    diff_text=diff_text,
                    hunk_count=cls.count_hunks(diff_text),
                    before_exists=before_exists,
                    after_exists=after_exists,
                    before_changed_lines=touched_line_facts.before_changed_lines,
                    after_changed_lines=touched_line_facts.after_changed_lines,
                    line_operations=touched_line_facts.line_operations,
                )
            )

        return tuple(file_changes)
