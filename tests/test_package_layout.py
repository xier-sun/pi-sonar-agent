from __future__ import annotations

from pathlib import Path

import pi_sonar_agent
from pi_sonar_agent.agent import ClaudeFixAgent
from pi_sonar_agent.fixers import RuleProfile
from pi_sonar_agent.integrations import AzureDevOpsClient


def test_package_path_stays_inside_standard_package_dir() -> None:
    package_paths = [Path(entry) for entry in pi_sonar_agent.__path__]

    assert package_paths
    assert all(path.name == "pi_sonar_agent" for path in package_paths)
    assert all(path.parent.name == "src" for path in package_paths)


def test_package_and_subpackages_export_public_symbols() -> None:
    assert pi_sonar_agent.ClaudeFixAgent is ClaudeFixAgent
    assert pi_sonar_agent.RuleProfile is RuleProfile
    assert pi_sonar_agent.AzureDevOpsClient is AzureDevOpsClient
