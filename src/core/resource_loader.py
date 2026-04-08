"""Prompt/resource loading helpers for runtime-facing agent code."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE_RULE_FILES = ("CLAUDE.md", "AGENTS.md")
PROJECT_RULE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSHARP_QUALITY_GATE_FILE = PROJECT_RULE_ROOT / "data" / "csharp-quality-gate.md"


class ResourceLoader:
    """Load optional markdown resources used during issue fixing."""

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
        return cls.load_markdown(paths)

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
    ) -> str:
        """Append workspace-level instructions to the base system prompt when present."""

        workspace_rules = cls.load_workspace_rules(workspace_path, filenames)
        project_rules = cls.load_project_rules(filenames)
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
        return "\n\n".join(section for section in sections if section).strip()
