"""pi-sonar-agent: SonarQube Code Issue Fix Agent based on Claude Code SDK.

This package provides automated fixing of SonarQube code issues using:
- Claude Code Agent for intelligent code understanding and fixing
- Multiple fix engines: deterministic, roslyn, agent, llm
- Build gate verification
- Azure DevOps PR creation
- DingTalk notifications
"""

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, FixResult, SonarIssue
from pi_sonar_agent.core.classifier import ComplexityLevel, IssueClassifier
from pi_sonar_agent.core.db_client import MySQLClient
from pi_sonar_agent.core.dingtalk import DingTalkCorpClient
from pi_sonar_agent.fixers.build_gate import LocalBuildGate, run_local_build
from pi_sonar_agent.fixers.deterministic import IssueGroup, build_issue_groups
from pi_sonar_agent.fixers.roslyn import RoslynFixEngine
from pi_sonar_agent.fixers.rule_profiles import RuleCatalog, RuleProfile, load_rule_catalog
from pi_sonar_agent.integrations.ado import AzureDevOpsClient, GitClient
from pi_sonar_agent.integrations.sonar import SonarQubeClient

__version__ = "1.0.0"

__all__ = [
    # Agent
    "ClaudeFixAgent",
    "SonarIssue",
    "FixResult",
    # Rules
    "RuleProfile",
    "RuleCatalog",
    "load_rule_catalog",
    # Fixers
    "IssueGroup",
    "build_issue_groups",
    "RoslynFixEngine",
    "LocalBuildGate",
    "run_local_build",
    # Integrations
    "SonarQubeClient",
    "AzureDevOpsClient",
    "GitClient",
    # Core
    "MySQLClient",
    "DingTalkCorpClient",
    "IssueClassifier",
    "ComplexityLevel",
]
