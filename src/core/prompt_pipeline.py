"""Prompt assembly helpers for layered, pipeline-style prompt construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="
PromptLayer = Literal["core", "support", "dynamic"]
PromptChannel = Literal["plain", "reminder"]


def _normalize_prompt_text(value: str) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class PromptSection:
    """One named prompt block in a layered prompt pipeline."""

    name: str
    content: str
    layer: PromptLayer = "core"
    channel: PromptChannel = "plain"


@dataclass(frozen=True)
class PromptPipelineRender:
    """Rendered prompt plus minimal observability for callers/tests."""

    prompt: str
    static_section_names: tuple[str, ...] = ()
    dynamic_section_names: tuple[str, ...] = ()
    dynamic_boundary_inserted: bool = False


@dataclass
class PromptPipelineBuilder:
    """Collect prompt blocks and render them with a visible dynamic boundary."""

    dynamic_boundary: str = DYNAMIC_BOUNDARY
    _sections: list[PromptSection] = field(default_factory=list)

    def add_section(
        self,
        name: str,
        content: str,
        *,
        layer: PromptLayer = "core",
        channel: PromptChannel = "plain",
    ) -> None:
        text = _normalize_prompt_text(content)
        if not text:
            return
        self._sections.append(
            PromptSection(
                name=str(name or "").strip() or "section",
                content=text,
                layer=layer,
                channel=channel,
            )
        )

    def add_core(self, name: str, content: str) -> None:
        self.add_section(name, content, layer="core")

    def add_support(self, name: str, content: str) -> None:
        self.add_section(name, content, layer="support")

    def add_dynamic(self, name: str, content: str, *, reminder: bool = True) -> None:
        self.add_section(
            name,
            content,
            layer="dynamic",
            channel="reminder" if reminder else "plain",
        )

    def build(self) -> PromptPipelineRender:
        static_parts: list[str] = []
        dynamic_plain_parts: list[str] = []
        reminder_parts: list[str] = []
        static_section_names: list[str] = []
        dynamic_section_names: list[str] = []

        for section in self._sections:
            if section.layer == "dynamic":
                dynamic_section_names.append(section.name)
                if section.channel == "reminder":
                    reminder_parts.append(section.content)
                else:
                    dynamic_plain_parts.append(section.content)
                continue
            static_section_names.append(section.name)
            static_parts.append(section.content)

        rendered_parts: list[str] = []
        if static_parts:
            rendered_parts.extend(static_parts)

        dynamic_parts: list[str] = []
        if dynamic_plain_parts:
            dynamic_parts.extend(dynamic_plain_parts)
        if reminder_parts:
            dynamic_parts.append(
                "<system-reminder>\n"
                + "\n\n".join(part for part in reminder_parts if part.strip()).strip()
                + "\n</system-reminder>"
            )

        inserted_boundary = bool(dynamic_parts and rendered_parts)
        if inserted_boundary:
            rendered_parts.append(self.dynamic_boundary)
        if dynamic_parts:
            rendered_parts.extend(dynamic_parts)

        prompt = "\n\n".join(part for part in rendered_parts if str(part).strip()).strip()
        return PromptPipelineRender(
            prompt=prompt,
            static_section_names=tuple(static_section_names),
            dynamic_section_names=tuple(dynamic_section_names),
            dynamic_boundary_inserted=inserted_boundary,
        )
