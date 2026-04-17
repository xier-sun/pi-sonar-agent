"""Unified light-check catalog and playbook metadata for headless simple-loop execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.resource_loader import ResourceLoader
from pi_sonar_agent.core.state import serialize_state

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIGHT_CHECK_CATALOG_FILE = PROJECT_ROOT / "data" / "sonar_light_check_catalog.yaml"
DEFAULT_SONAR_FIX_PLAYBOOK_FILE = PROJECT_ROOT / "docs" / "sonar-fix-playbook.md"


@dataclass(frozen=True)
class LightBlockerCategory:
    """One simple-loop blocker category with raw matcher ids."""

    name: str
    title: str
    summary: str = ""
    semantic_finding_ids: tuple[str, ...] = ()
    quality_gate_rule_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, name: str, payload: dict[str, Any]) -> "LightBlockerCategory":
        return cls(
            name=str(name or "").strip(),
            title=str(payload.get("title", "")).strip() or str(name or "").strip(),
            summary=str(payload.get("summary", "")).strip(),
            semantic_finding_ids=tuple(
                str(item).strip()
                for item in payload.get("semantic_finding_ids", ())
                if str(item).strip()
            ),
            quality_gate_rule_ids=tuple(
                str(item).strip()
                for item in payload.get("quality_gate_rule_ids", ())
                if str(item).strip()
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class LightRuleProfile:
    """Per-rule lightweight validation and blocker policy."""

    rule_id: str
    family: str
    title: str = ""
    issue_validator: str = ""
    self_check: tuple[str, ...] = ()
    blocker_checks: tuple[str, ...] = ()
    validator_config: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, rule_id: str, payload: dict[str, Any]) -> "LightRuleProfile":
        validator_config = payload.get("validator_config", {})
        return cls(
            rule_id=str(rule_id or "").strip(),
            family=str(payload.get("family", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            issue_validator=str(payload.get("issue_validator", "")).strip(),
            self_check=tuple(
                str(item).strip()
                for item in payload.get("self_check", ())
                if str(item).strip()
            ),
            blocker_checks=tuple(
                str(item).strip()
                for item in payload.get("blocker_checks", ())
                if str(item).strip()
            ),
            validator_config=dict(validator_config) if isinstance(validator_config, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class PlaybookFamily:
    """Prompt-facing playbook guidance for one rule family."""

    family: str
    title: str
    prompt_guidance: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, family: str, payload: dict[str, Any]) -> "PlaybookFamily":
        return cls(
            family=str(family or "").strip(),
            title=str(payload.get("title", "")).strip() or str(family or "").strip(),
            prompt_guidance=tuple(
                str(item).strip()
                for item in payload.get("prompt_guidance", ())
                if str(item).strip()
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class LightCheckCatalog:
    """Structured catalog shared by prompting, issue checks, and blocker checks."""

    version: int
    source_path: str
    rules: dict[str, LightRuleProfile]
    blocker_categories: dict[str, LightBlockerCategory]

    @classmethod
    def load_from_file(cls, path: Path) -> "LightCheckCatalog":
        raw_text = path.read_text(encoding="utf-8", errors="replace").strip()
        payload = json.loads(raw_text) if raw_text else {}
        rules_payload = payload.get("rules", {}) if isinstance(payload, dict) else {}
        blockers_payload = payload.get("blocker_categories", {}) if isinstance(payload, dict) else {}
        rules = {
            str(rule_id).strip(): LightRuleProfile.from_dict(str(rule_id).strip(), rule_payload)
            for rule_id, rule_payload in rules_payload.items()
            if isinstance(rule_payload, dict) and str(rule_id).strip()
        }
        blocker_categories = {
            str(name).strip(): LightBlockerCategory.from_dict(str(name).strip(), category_payload)
            for name, category_payload in blockers_payload.items()
            if isinstance(category_payload, dict) and str(name).strip()
        }
        return cls(
            version=int(payload.get("version", 1)) if isinstance(payload, dict) else 1,
            source_path=path.as_posix(),
            rules=rules,
            blocker_categories=blocker_categories,
        )

    def rule(self, rule_id: str) -> LightRuleProfile | None:
        return self.rules.get(str(rule_id or "").strip())

    def family(self, rule_id: str) -> str:
        profile = self.rule(rule_id)
        return str(profile.family).strip() if profile is not None else ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class SonarFixPlaybook:
    """Prompt-facing playbook metadata shared by simple-loop prompting."""

    version: int
    source_path: str
    families: dict[str, PlaybookFamily]
    common_prompt_guidance: tuple[str, ...] = ()
    refactor_safety_constraints: tuple[str, ...] = ()
    body_markdown: str = ""

    @classmethod
    def load_from_file(cls, path: Path) -> "SonarFixPlaybook":
        _, metadata, body = ResourceLoader.load_json_front_matter((path,))
        families_payload = metadata.get("families", {}) if isinstance(metadata, dict) else {}
        families = {
            str(name).strip(): PlaybookFamily.from_dict(str(name).strip(), family_payload)
            for name, family_payload in families_payload.items()
            if isinstance(family_payload, dict) and str(name).strip()
        }
        return cls(
            version=int(metadata.get("version", 1)) if isinstance(metadata, dict) else 1,
            source_path=path.as_posix(),
            families=families,
            common_prompt_guidance=tuple(
                str(item).strip()
                for item in (metadata.get("common_prompt_guidance", ()) if isinstance(metadata, dict) else ())
                if str(item).strip()
            ),
            refactor_safety_constraints=tuple(
                str(item).strip()
                for item in (metadata.get("refactor_safety_constraints", ()) if isinstance(metadata, dict) else ())
                if str(item).strip()
            ),
            body_markdown=body.strip(),
        )

    def family_guidance(self, family: str) -> tuple[str, ...]:
        entry = self.families.get(str(family or "").strip())
        if entry is None:
            return ()
        return tuple(entry.prompt_guidance)

    def common_guidance(self) -> tuple[str, ...]:
        return tuple(self.common_prompt_guidance)

    def refactor_safety_guidance(self) -> tuple[str, ...]:
        return tuple(self.refactor_safety_constraints)

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@lru_cache(maxsize=1)
def load_default_light_check_catalog() -> LightCheckCatalog:
    """Load the repository-default light-check catalog."""

    return LightCheckCatalog.load_from_file(DEFAULT_LIGHT_CHECK_CATALOG_FILE)


@lru_cache(maxsize=1)
def load_default_sonar_fix_playbook() -> SonarFixPlaybook:
    """Load the repository-default simple-loop playbook metadata."""

    return SonarFixPlaybook.load_from_file(DEFAULT_SONAR_FIX_PLAYBOOK_FILE)


def render_simple_loop_guidance_for_rule(rule_id: str, *, max_items: int = 4) -> tuple[str, ...]:
    """Return compact self-check guidance for simple-loop prompting."""

    catalog = load_default_light_check_catalog()
    profile = catalog.rule(rule_id)
    if profile is None:
        return ()

    playbook = load_default_sonar_fix_playbook()
    items: list[str] = []
    items.extend(profile.self_check)
    items.extend(playbook.family_guidance(profile.family))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= max(1, int(max_items)):
            break
    return tuple(deduped)


def render_simple_loop_refactor_safety_constraints(
    rule_id: str,
    *,
    max_items: int = 6,
) -> tuple[str, ...]:
    """Return compact cross-rule C# refactor safety constraints for prompt guards."""

    # `rule_id` is accepted for future rule-scoped expansion; current constraints are universal.
    _ = str(rule_id or "").strip()
    playbook = load_default_sonar_fix_playbook()
    items: list[str] = []
    items.extend(playbook.common_guidance())
    items.extend(playbook.refactor_safety_guidance())

    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= max(1, int(max_items)):
            break
    return tuple(deduped)
