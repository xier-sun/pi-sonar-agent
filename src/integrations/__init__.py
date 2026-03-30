"""Integrations with external systems (SonarQube, Azure DevOps, etc.)

This module provides integration utilities that can be reused from the
original fix_engine project or used independently.
"""

from pi_sonar_agent.integrations.sonar import SonarQubeClient
from pi_sonar_agent.integrations.ado import AzureDevOpsClient

__all__ = ["SonarQubeClient", "AzureDevOpsClient"]