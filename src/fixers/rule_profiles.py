"""Rule profiles and configuration module.

Handles loading and parsing rule_profiles.json to configure
how different SonarQube rules are fixed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RULE_PROFILE_PATH = Path("rule_profiles.json")


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for Agent-based fixes."""

    max_turns: int = 0
    max_token_budget: int = 0
    max_files: int = 1
    allowed_operations: tuple[str, ...] = ()
    strategy_hint: str = ""


@dataclass(frozen=True)
class RuleProfile:
    """Configuration for a specific SonarQube rule."""

    rule_id: str
    title: str
    category: str
    risk: str
    enabled: bool
    primary_engine: str
    fallback_engines: tuple[str, ...]
    pr_mode: str
    max_changed_lines: int
    comment_only: bool = False
    allow_signature_changes: bool = False
    fix_scope: str = "file"
    classification_required: bool = False
    verification_policy: str = "build"
    requires_solution_path: bool = False
    complexity_level: str = ""
    agent_config: AgentConfig | None = None

    @property
    def engine_order(self) -> tuple[str, ...]:
        """Get the ordered list of engines to try."""
        ordered = [self.primary_engine, *self.fallback_engines]
        seen: set[str] = set()
        deduplicated: list[str] = []
        for engine_name in ordered:
            normalized = str(engine_name).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduplicated.append(normalized)
        return tuple(deduplicated)

    @property
    def is_auto(self) -> bool:
        """Whether this rule should be fixed automatically."""
        return self.enabled and self.pr_mode == "auto"

    @property
    def is_draft(self) -> bool:
        """Whether this rule should create a draft PR."""
        return self.enabled and self.pr_mode == "draft"

    @property
    def is_manual(self) -> bool:
        """Whether this rule requires manual fixing."""
        return self.pr_mode == "manual" or not self.enabled

    @property
    def is_solution_scope(self) -> bool:
        """Whether this fix requires solution-level changes."""
        return self.fix_scope == "solution"

    @property
    def max_turns(self) -> int:
        """Max turns for Agent-based fixes."""
        if self.agent_config is not None and self.agent_config.max_turns > 0:
            return self.agent_config.max_turns
        return 5 if self.primary_engine == "agent" else 0

    @property
    def max_token_budget(self) -> int:
        """Max tokens for Agent-based fixes."""
        if self.agent_config is not None and self.agent_config.max_token_budget > 0:
            return self.agent_config.max_token_budget
        return 0

    @property
    def max_files(self) -> int:
        """Max files that can be modified for this fix."""
        if self.agent_config is not None and self.agent_config.max_files > 0:
            return self.agent_config.max_files
        return 1

    @property
    def allowed_operations(self) -> tuple[str, ...]:
        """Allowed operations for Agent-based fixes."""
        if self.agent_config is not None and self.agent_config.allowed_operations:
            return self.agent_config.allowed_operations
        if self.primary_engine == "agent":
            return (
                "read_file",
                "search_in_project",
                "get_file_outline",
                "apply_edit",
                "run_build",
                "finish",
            )
        return ()

    @property
    def strategy_hint(self) -> str:
        """Strategy hint for Agent-based fixes."""
        if self.agent_config is not None:
            return self.agent_config.strategy_hint
        return ""


@dataclass(frozen=True)
class RuleCatalog:
    """Catalog of all rule profiles."""

    source_path: str
    schema_version: int
    profiles: dict[str, RuleProfile]

    def get(self, rule_id: str) -> RuleProfile | None:
        """Get a rule profile by ID."""
        return self.profiles.get(rule_id.strip())

    def require(self, rule_id: str) -> RuleProfile:
        """Get a rule profile by ID, raising if not found."""
        profile = self.get(rule_id)
        if profile is None:
            raise RuntimeError(f"rule_profiles.json 中未定义规则: {rule_id}")
        return profile

    def get_processable_rule_ids(self) -> set[str]:
        """Get all rule IDs that can be automatically processed."""
        return {
            rule_id
            for rule_id, profile in self.profiles.items()
            if profile.enabled and profile.pr_mode in {"auto", "draft"}
        }


def load_rule_catalog(path: str | None = None) -> RuleCatalog:
    """Load rule profiles from JSON file."""
    profile_path = Path(path) if path else DEFAULT_RULE_PROFILE_PATH

    # Try multiple locations
    search_paths = [
        profile_path,
        Path.cwd() / "rule_profiles.json",
        Path(__file__).parent.parent.parent / "data" / "rule_profiles.json",
    ]

    found_path = None
    for p in search_paths:
        if p.exists():
            found_path = p
            break

    if not found_path:
        # Use bundled default
        found_path = Path(__file__).parent.parent.parent / "data" / "rule_profiles.json"
        # Create default if doesn't exist
        if not found_path.exists():
            found_path.parent.mkdir(parents=True, exist_ok=True)
            found_path.write_text(DEFAULT_RULE_PROFILES_JSON, encoding="utf-8")

    try:
        payload = json.loads(found_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"规则配置文件 JSON 解析失败: {found_path} -> {exc}") from exc

    schema_version = int(payload.get("schema_version", 1))
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        raise RuntimeError("rule_profiles.json 的 `rules` 必须是数组。")

    profiles: dict[str, RuleProfile] = {}
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            raise RuntimeError("rule_profiles.json 中的每个规则项都必须是对象。")

        rule_id = str(raw_rule.get("rule_id", "")).strip()
        if not rule_id:
            raise RuntimeError("rule_profiles.json 中存在缺少 `rule_id` 的规则项。")
        if rule_id in profiles:
            raise RuntimeError(f"rule_profiles.json 中存在重复规则定义: {rule_id}")

        fallback_engines = raw_rule.get("fallback_engines", [])
        if not isinstance(fallback_engines, list):
            raise RuntimeError(f"规则 {rule_id} 的 `fallback_engines` 必须是数组。")

        raw_agent_config = raw_rule.get("agent_config")
        if raw_agent_config is not None and not isinstance(raw_agent_config, dict):
            raise RuntimeError(f"规则 {rule_id} 的 `agent_config` 必须是对象。")

        allowed_operations: tuple[str, ...] = ()
        if isinstance(raw_agent_config, dict):
            raw_allowed_operations = raw_agent_config.get("allowed_operations", [])
            if raw_allowed_operations is not None and not isinstance(raw_allowed_operations, list):
                raise RuntimeError(f"规则 {rule_id} 的 `allowed_operations` 必须是数组。")
            allowed_operations = tuple(
                str(item).strip()
                for item in (raw_allowed_operations or [])
                if str(item).strip()
            )

        profiles[rule_id] = RuleProfile(
            rule_id=rule_id,
            title=str(raw_rule.get("title", rule_id)).strip() or rule_id,
            category=str(raw_rule.get("category", "uncategorized")).strip() or "uncategorized",
            risk=str(raw_rule.get("risk", "unknown")).strip() or "unknown",
            enabled=bool(raw_rule.get("enabled", False)),
            primary_engine=str(raw_rule.get("primary_engine", "manual")).strip() or "manual",
            fallback_engines=tuple(str(item).strip() for item in fallback_engines if str(item).strip()),
            pr_mode=str(raw_rule.get("pr_mode", "manual")).strip() or "manual",
            max_changed_lines=max(int(raw_rule.get("max_changed_lines", 0)), 0),
            comment_only=bool(raw_rule.get("comment_only", False)),
            allow_signature_changes=bool(raw_rule.get("allow_signature_changes", False)),
            fix_scope=str(raw_rule.get("fix_scope", "file")).strip().lower() or "file",
            classification_required=bool(raw_rule.get("classification_required", False)),
            verification_policy=str(raw_rule.get("verification_policy", "build")).strip().lower() or "build",
            requires_solution_path=bool(raw_rule.get("requires_solution_path", False)),
            complexity_level=str(raw_rule.get("complexity_level", "")).strip().lower(),
            agent_config=(
                AgentConfig(
                    max_turns=max(int(raw_agent_config.get("max_turns", 0)), 0),
                    max_token_budget=max(int(raw_agent_config.get("max_token_budget", 0)), 0),
                    max_files=max(int(raw_agent_config.get("max_files", 1)), 1),
                    allowed_operations=allowed_operations,
                    strategy_hint=str(raw_agent_config.get("strategy_hint", "")).strip(),
                )
                if raw_agent_config
                else None
            ),
        )

    return RuleCatalog(
        source_path=str(found_path),
        schema_version=schema_version,
        profiles=profiles,
    )


# Default rule profiles (subset of common rules)
DEFAULT_RULE_PROFILES_JSON = """{
  "schema_version": 2,
  "rules": [
    {
      "rule_id": "csharpsquid:S1481",
      "title": "移除未使用的局部变量",
      "category": "cleanup",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": ["deterministic"],
      "pr_mode": "auto",
      "max_changed_lines": 4
    },
    {
      "rule_id": "csharpsquid:S1854",
      "title": "移除未使用的赋值",
      "category": "cleanup",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": ["deterministic"],
      "pr_mode": "auto",
      "max_changed_lines": 4
    },
    {
      "rule_id": "csharpsquid:S125",
      "title": "移除被注释掉的代码段",
      "category": "cleanup",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": [],
      "pr_mode": "auto",
      "max_changed_lines": 12
    },
    {
      "rule_id": "external_roslyn:CS0219",
      "title": "变量已被赋值但未使用",
      "category": "cleanup",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": ["deterministic"],
      "pr_mode": "auto",
      "max_changed_lines": 4
    },
    {
      "rule_id": "csharpsquid:S4487",
      "title": "移除未读取的 private 字段",
      "category": "cleanup",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": ["agent", "llm"],
      "pr_mode": "auto",
      "max_changed_lines": 10,
      "complexity_level": "simple",
      "agent_config": {
        "max_turns": 5,
        "max_token_budget": 128000,
        "max_files": 1,
        "strategy_hint": "私有字段不涉及跨文件引用，直接移除字段声明及其无用赋值，保持最小化修改。"
      }
    },
    {
      "rule_id": "csharpsquid:S2681",
      "title": "多行代码块必须使用大括号",
      "category": "syntax",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": [],
      "pr_mode": "auto",
      "max_changed_lines": 6
    },
    {
      "rule_id": "external_roslyn:CS1591",
      "title": "缺少公开成员的 XML 注释",
      "category": "documentation",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": [],
      "pr_mode": "auto",
      "max_changed_lines": 40,
      "comment_only": true
    },
    {
      "rule_id": "csharpsquid:S6610",
      "title": "StartsWith/EndsWith 优先使用 char",
      "category": "syntax",
      "risk": "low",
      "enabled": true,
      "primary_engine": "roslyn",
      "fallback_engines": [],
      "pr_mode": "auto",
      "max_changed_lines": 2
    },
    {
      "rule_id": "csharpsquid:S2325",
      "title": "方法应标记为 static",
      "category": "design",
      "risk": "low",
      "enabled": true,
      "primary_engine": "llm",
      "fallback_engines": [],
      "pr_mode": "auto",
      "max_changed_lines": 2
    },
    {
      "rule_id": "csharpsquid:S1871",
      "title": "两个分支实现完全一样",
      "category": "design",
      "risk": "medium",
      "enabled": true,
      "primary_engine": "llm",
      "fallback_engines": [],
      "pr_mode": "auto",
      "max_changed_lines": 20
    }
  ]
}"""
