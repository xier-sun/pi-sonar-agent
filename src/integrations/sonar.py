"""SonarQube API Client.

Simplified client for interacting with SonarQube REST API.
Compatible with Windows and Unix systems.
"""

import html
import re
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class SonarRuleDetails:
    """Details about a SonarQube rule."""

    key: str
    name: str = ""
    severity: str = ""
    rule_type: str = ""
    description: str = ""
    how_to_fix: str = ""


class SonarQubeClient:
    """Client for SonarQube Web API."""

    def __init__(
        self,
        host: str,
        token: str,
        organization: str | None = None,
        timeout: int = 30,
    ):
        self.host = host.rstrip("/")
        self.organization = organization
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (token, "")
        self.session.headers.update({"Accept": "application/json"})
        self._rule_cache: dict[str, SonarRuleDetails] = {}

    def get_open_issues(
        self,
        project_key: str,
        page_size: int = 100,
        author: str | None = None,
        severities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get open issues from a project."""
        params: dict[str, Any] = {
            "componentKeys": project_key,
            "statuses": "OPEN",
            "types": "BUG,CODE_SMELL",
            "ps": min(max(page_size, 1), 500),
            "additionalFields": "_all",
        }
        if self.organization:
            params["organization"] = self.organization
        if author:
            params["authors"] = author
        if severities:
            params["severities"] = ",".join(severities)

        issues: list[dict[str, Any]] = []
        page = 1

        while True:
            params["p"] = page
            data = self._request("GET", "/api/issues/search", params=params)
            page_issues = data.get("issues", [])
            issues.extend(page_issues)

            paging = data.get("paging", {})
            total = paging.get("total", 0)
            if not page_issues or len(issues) >= total:
                break
            page += 1

        # Filter by author if specified
        if author:
            issues = [
                i for i in issues
                if i.get("author") == author or i.get("assignee") == author
            ]

        return issues

    def get_issue_snippet(self, issue_key: str) -> str:
        """Get code snippet for an issue."""
        params: dict[str, Any] = {"issueKey": issue_key}
        if self.organization:
            params["organization"] = self.organization

        data = self._request("GET", "/api/sources/issue_snippets", params=params)
        return self._extract_snippet(data)

    def get_rule_details(self, rule_key: str) -> SonarRuleDetails:
        """Get details about a rule."""
        if rule_key in self._rule_cache:
            return self._rule_cache[rule_key]

        params: dict[str, Any] = {"key": rule_key}
        if self.organization:
            params["organization"] = self.organization

        data = self._request("GET", "/api/rules/show", params=params)
        raw_rule = data.get("rule", {})

        rule = SonarRuleDetails(
            key=rule_key,
            name=raw_rule.get("name", ""),
            severity=raw_rule.get("severity", ""),
            rule_type=raw_rule.get("type", ""),
            description=raw_rule.get("mdDesc", "") or raw_rule.get("htmlDesc", ""),
            how_to_fix=raw_rule.get("mdNote", "") or raw_rule.get("htmlNote", ""),
        )

        self._rule_cache[rule_key] = rule
        return rule

    def get_projects(self, search: str | None = None) -> list[dict[str, Any]]:
        """Get list of projects."""
        params: dict[str, Any] = {"ps": 100}
        if search:
            params["query"] = search
        if self.organization:
            params["organization"] = self.organization

        data = self._request("GET", "/api/projects/search", params=params)
        return data.get("components", [])

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make API request."""
        url = f"{self.host}{path}"
        response = self.session.request(
            method, url, params=params, timeout=self.timeout
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                f"SonarQube API error: {method} {url} -> {response.status_code}"
            ) from exc

        return response.json() if response.content else {}

    @staticmethod
    def _extract_snippet(data: dict) -> str:
        """Extract snippet from response."""
        collected: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key.lower() in ("code", "snippet", "source", "text"):
                        if isinstance(value, str) and value.strip():
                            collected.append(value.strip())
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
        return "\n".join(collected)