"""Prompt/resource loading helpers for runtime-facing agent code."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE_RULE_FILES = ("CLAUDE.md", "AGENTS.md")
PROJECT_RULE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSHARP_QUALITY_GATE_FILE = PROJECT_RULE_ROOT / "data" / "csharp-quality-gate.md"
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(`)?([A-Za-z]:\\[^`\r\n]+)(`)?")


class ResourceLoader:
    """Load optional markdown resources used during issue fixing."""

    TRUNCATION_NOTICE = "[truncated]"

    @staticmethod
    def split_markdown_front_matter(text: str) -> tuple[str, str]:
        """Split optional markdown front matter from the body text."""

        normalized = str(text or "").strip()
        if not normalized.startswith("---"):
            return "", normalized

        lines = normalized.splitlines()
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return "\n".join(lines[1:index]).strip(), "\n".join(lines[index + 1:]).strip()
        return "", normalized

    @classmethod
    def strip_markdown_front_matter(cls, text: str) -> str:
        """Strip optional front matter from a markdown file."""

        _, body = cls.split_markdown_front_matter(text)
        return body

    @classmethod
    def load_markdown(cls, paths: Iterable[Path]) -> str:
        """Return the first non-empty markdown resource from the given paths."""

        for path in paths:
            try:
                if not path.exists():
                    continue
                content = cls.strip_markdown_front_matter(
                    path.read_text(encoding="utf-8", errors="replace")
                ).strip()
                if content:
                    return content
            except Exception:
                continue
        return ""

    @classmethod
    def load_markdown_document(cls, paths: Iterable[Path]) -> tuple[Path | None, str, str]:
        """Return the first non-empty markdown resource with metadata and body."""

        for path in paths:
            try:
                if not path.exists():
                    continue
                raw_text = path.read_text(encoding="utf-8", errors="replace")
                metadata, body = cls.split_markdown_front_matter(raw_text)
                content = body.strip()
                if content:
                    return path, metadata.strip(), content
            except Exception:
                continue
        return None, "", ""

    @classmethod
    def truncate_for_prompt(
        cls,
        text: str,
        max_chars: int,
        *,
        max_lines: int | None = None,
    ) -> str:
        """Trim prompt-facing markdown/text to a stable budget."""

        normalized = str(text or "").strip()
        if not normalized or max_chars <= 0:
            return ""

        lines = normalized.splitlines()
        if max_lines is not None and max_lines > 0 and len(lines) > max_lines:
            normalized = "\n".join(lines[:max_lines]).strip()
        else:
            normalized = "\n".join(lines).strip()

        if len(normalized) <= max_chars:
            return normalized

        suffix = "\n" + cls.TRUNCATION_NOTICE
        cutoff = max(0, max_chars - len(suffix))
        return normalized[:cutoff].rstrip() + suffix

    @classmethod
    def load_json_front_matter(cls, paths: Iterable[Path]) -> tuple[Path | None, dict[str, Any], str]:
        """Load JSON front matter and the markdown body from the first available file."""

        path, metadata, body = cls.load_markdown_document(paths)
        if not metadata:
            return path, {}, body
        try:
            payload = json.loads(metadata)
        except json.JSONDecodeError:
            return path, {}, body
        if isinstance(payload, dict):
            return path, payload, body
        return path, {}, body

    @classmethod
    def load_csharp_quality_gate(
        cls,
        issue_file_path: str,
        quality_gate_paths: Iterable[Path],
        supplement: str = "",
    ) -> str:
        """Load the C# quality gate for C# source files."""

        if not str(issue_file_path or "").lower().endswith(".cs"):
            return ""

        gate_text = cls.load_markdown(quality_gate_paths).strip()
        supplement_text = str(supplement or "").strip()

        if gate_text and supplement_text:
            return f"{gate_text}\n\n{supplement_text}".strip()
        return gate_text or supplement_text

    @classmethod
    def load_workspace_rules(
        cls,
        workspace_path: Path,
        filenames: Iterable[str] = DEFAULT_WORKSPACE_RULE_FILES,
    ) -> str:
        """Load repository-level long-term instructions from the workspace."""

        paths = tuple(workspace_path / name for name in filenames if str(name).strip())
        return cls._sanitize_workspace_rules(cls.load_markdown(paths))

    @classmethod
    def load_project_rules(
        cls,
        filenames: Iterable[str] = DEFAULT_WORKSPACE_RULE_FILES,
    ) -> str:
        """Load agent-level long-term instructions from the current project."""

        paths = tuple(PROJECT_RULE_ROOT / name for name in filenames if str(name).strip())
        return cls.load_markdown(paths)

    @classmethod
    def compose_system_prompt(
        cls,
        base_prompt: str,
        workspace_path: Path,
        filenames: Iterable[str] = DEFAULT_WORKSPACE_RULE_FILES,
        *,
        max_chars: int | None = None,
        max_project_rule_chars: int = 1400,
        max_workspace_rule_chars: int = 1400,
    ) -> str:
        """Append workspace-level instructions to the base system prompt when present."""

        workspace_rules = cls.truncate_for_prompt(
            cls.load_workspace_rules(workspace_path, filenames),
            max_workspace_rule_chars,
            max_lines=80,
        )
        project_rules = cls.truncate_for_prompt(
            cls.load_project_rules(filenames),
            max_project_rule_chars,
            max_lines=80,
        )
        sections = [str(base_prompt).strip()]
        if project_rules:
            sections.extend(
                [
                    "【Agent 长期规则】",
                    project_rules.strip(),
                ]
            )
        if workspace_rules:
            sections.extend(
                [
                    "【仓库长期规则】",
                    workspace_rules.strip(),
                ]
            )
        prompt = "\n\n".join(section for section in sections if section).strip()
        if max_chars is None or len(prompt) <= max_chars:
            return prompt
        return cls.truncate_for_prompt(prompt, max_chars, max_lines=220)

    @classmethod
    def _sanitize_workspace_rules(cls, text: str) -> str:
        """Strip volatile absolute-path hints from workspace rules before prompting."""

        normalized = str(text or "").strip()
        if not normalized:
            return ""

        replacement_count = 0

        def _replace_absolute_path(match: re.Match[str]) -> str:
            nonlocal replacement_count
            replacement_count += 1
            if match.group(1) or match.group(3):
                return "`<workspace-root>`"
            return "<workspace-root>"

        sanitized = _WINDOWS_ABSOLUTE_PATH_PATTERN.sub(_replace_absolute_path, normalized)
        if replacement_count <= 0:
            return sanitized

        guidance = (
            "运行时工作目录已经切到当前 issue 的临时工作区；读取和编辑源码时只使用仓库相对路径，"
            "不要使用 `C:\\` 等绝对路径。"
        )
        if guidance in sanitized:
            return sanitized
        return f"{sanitized}\n\n{guidance}".strip()
