"""Integrations with external systems (SonarQube, Azure DevOps, etc.)

This module provides integration utilities that can be reused from the
original fix_engine project or used independently.
"""

from .ado import AzureDevOpsClient
from .sonar import SonarQubeClient

__all__ = ["SonarQubeClient", "AzureDevOpsClient"]
