"""Minimal local skill loader for prompt-time skill injection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


@dataclass(frozen=True)
class SkillPromptSection:
    """One prompt-ready skill section."""

    skill_name: str
    title: str
    role: str
    content: str

    def render(self) -> str:
        return f"【Skill: {self.title}】\n{self.content}".strip()


@dataclass(frozen=True)
class SkillDocument:
    """Parsed local skill document."""

    name: str
    title: str
    description: str
    content: str


def _extract_frontmatter_field(frontmatter: str, field_name: str) -> str:
    pattern = rf"(?mi)^{re.escape(field_name)}\s*:\s*(.+?)\s*$"
    match = re.search(pattern, frontmatter or "")
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _extract_markdown_section(content: str, heading: str) -> str:
    pattern = rf"(?ms)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, content or "")
    if not match:
        return ""
    return str(match.group(1) or "").strip()


@lru_cache(maxsize=32)
def load_skill_document(skill_name: str) -> SkillDocument | None:
    skill_dir = SKILLS_ROOT / str(skill_name or "").strip()
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return None
    try:
        raw_text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None

    frontmatter_match = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n?(.*)$", raw_text)
    if frontmatter_match:
        frontmatter = str(frontmatter_match.group(1) or "")
        body = str(frontmatter_match.group(2) or "")
    else:
        frontmatter = ""
        body = raw_text

    return SkillDocument(
        name=str(skill_name or "").strip(),
        title=_extract_frontmatter_field(frontmatter, "title") or str(skill_name or "").strip(),
        description=_extract_frontmatter_field(frontmatter, "description"),
        content=body.strip(),
    )


def load_skill_prompt_section(skill_name: str, *, role: str) -> SkillPromptSection | None:
    document = load_skill_document(skill_name)
    if document is None:
        return None
    normalized_role = str(role or "").strip().lower()
    if not normalized_role:
        return None
    section_text = _extract_markdown_section(document.content, normalized_role.capitalize())
    if not section_text:
        return None
    return SkillPromptSection(
        skill_name=document.name,
        title=document.title,
        role=normalized_role,
        content=section_text,
    )


def load_quality_gate_skill_digest(*, role: str) -> str:
    section = load_skill_prompt_section("csharp-quality-gate", role=role)
    return section.content if section is not None else ""


def load_rule_skill_section(*, issue_rule: str, role: str) -> SkillPromptSection | None:
    normalized_rule = str(issue_rule or "").strip()
    if normalized_rule == "csharpsquid:S3776":
        return load_skill_prompt_section("s3776-fix", role=role)
    if normalized_rule == "csharpsquid:S107":
        return load_skill_prompt_section("s107-fix", role=role)
    return None
