"""Rule profiles and configuration module.

This module handles:
- Loading and parsing rule_profiles.json
- RuleProfile dataclass with all configuration
- RuleCatalog for looking up rules
"""

from pi_sonar_agent.fixers.rule_profiles import (
    AgentConfig,
    RuleCatalog,
    RuleProfile,
    load_rule_catalog,
)

__all__ = [
    "AgentConfig",
    "RuleCatalog",
    "RuleProfile",
    "load_rule_catalog",
]