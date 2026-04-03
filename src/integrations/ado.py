"""Azure DevOps API Client.

Client for interacting with Azure DevOps REST API.
Compatible with Windows and Unix systems.
"""

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from pi_sonar_agent.core.git_gateway import GitRepositoryGateway


@dataclass
class PullRequest:
    """Represents an Azure DevOps pull request."""

    pr_id: int
    title: str
    description: str
    source_branch: str
    target_branch: str
    url: str
    state: str


class AzureDevOpsRequestError(RuntimeError):
    """Rich Azure DevOps request failure with response details."""

    def __init__(self, message: str, *, status_code: int = 0, response_text: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class AzureDevOpsClient:
    """Client for Azure DevOps REST API."""

    def __init__(
        self,
        base_url: str,
        project: str,
        pat: str,
        organization: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.organization = organization
        self.timeout = timeout

        # For REST API calls
        self.session = requests.Session()
        auth = base64.b64encode(f":{pat}".encode()).decode()
        self.session.headers.update({
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        })

    @property
    def _api_url(self) -> str:
        """Get the API base URL."""
        if self.organization:
            return f"{self.base_url}/{self.project}"
        return f"{self.base_url}/{self.project}"

    def get_repository(self, repository: str) -> dict[str, Any]:
        """Get repository details."""
        url = f"{self._api_url}/_apis/git/repositories/{repository}"
        response = self.session.get(url, timeout=self.timeout)
        self._raise_for_status_with_details(response, action=f"获取仓库信息失败: {repository}")
        return response.json()

    def get_remote_url(self, repository: str) -> str:
        """Get the remote URL for a repository."""
        repo = self.get_repository(repository)
        return repo.get("webUrl", repo.get("remoteUrl", ""))

    def create_pull_request(
        self,
        repository: str,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str = "develop",
        reviewer_email: str | None = None,
    ) -> PullRequest:
        """Create a new pull request."""
        url = f"{self._api_url}/_apis/git/repositories/{repository}/pullrequests"

        body = {
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target_branch}",
            "title": title,
            "description": description,
        }

        if self.organization:
            url += "?api-version=7.0"
            body["repositoryId"] = repository

        response = self.session.post(
            url, json=body, timeout=self.timeout
        )
        self._raise_for_status_with_details(
            response,
            action=(
                f"创建 Pull Request 失败: {repository} "
                f"{source_branch} -> {target_branch}"
            ),
        )
        data = response.json()
        pr_id = data.get("pullRequestId", 0)
        web_url = (
            data.get("_links", {}).get("web", {}).get("href", "")
            or data.get("links", {}).get("web", {}).get("href", "")
            or self._build_pr_web_url(repository, pr_id)
        )

        if reviewer_email and pr_id > 0:
            reviewer_id = self.resolve_identity_id(reviewer_email)
            if reviewer_id:
                self.add_reviewer(repository, pr_id, reviewer_id)

        return PullRequest(
            pr_id=pr_id,
            title=data.get("title", ""),
            description=data.get("description", ""),
            source_branch=source_branch,
            target_branch=target_branch,
            url=web_url,
            state=data.get("status", ""),
        )

    def update_pull_request_description(
        self,
        repository: str,
        pull_request_id: int,
        description: str,
    ) -> PullRequest:
        """Update an existing pull request description."""

        url = (
            f"{self._api_url}/_apis/git/repositories/{repository}/pullrequests/{pull_request_id}"
            "?api-version=7.1"
        )
        response = self.session.patch(
            url,
            json={"description": description},
            timeout=self.timeout,
        )
        self._raise_for_status_with_details(response, action=f"更新 PR 描述失败: PR {pull_request_id}")
        data = response.json()
        web_url = (
            data.get("_links", {}).get("web", {}).get("href", "")
            or data.get("links", {}).get("web", {}).get("href", "")
            or self._build_pr_web_url(repository, int(data.get("pullRequestId", 0)))
        )
        return PullRequest(
            pr_id=int(data.get("pullRequestId", 0)),
            title=data.get("title", ""),
            description=data.get("description", ""),
            source_branch=data.get("sourceRefName", ""),
            target_branch=data.get("targetRefName", ""),
            url=web_url,
            state=data.get("status", ""),
        )

    def _build_pr_web_url(self, repository: str, pr_id: int) -> str:
        """Build a browser-friendly pull request URL."""

        if pr_id <= 0:
            return ""
        return f"{self.base_url}/{self.project}/_git/{repository}/pullrequest/{pr_id}"

    def resolve_identity_id(self, identifier: str) -> str | None:
        """Resolve an Azure DevOps identity ID from email or account name."""

        normalized = str(identifier or "").strip()
        if not normalized:
            return None

        candidates = [normalized]
        if "@" in normalized:
            local_part = normalized.split("@", 1)[0].strip()
            if local_part and local_part not in candidates:
                candidates.append(local_part)

        for candidate in candidates:
            url = f"{self.base_url}/_apis/Identities"
            params = {
                "searchFilter": "General",
                "filterValue": candidate,
                "queryMembership": "None",
                "api-version": "7.1",
            }
            response = self.session.get(url, params=params, timeout=self.timeout)
            self._raise_for_status_with_details(response, action=f"解析 ADO 身份失败: {candidate}")
            identities = response.json().get("value", [])
            if not identities:
                continue

            exact_match = self._find_exact_identity_match(identities, normalized, candidate)
            if exact_match:
                return str(exact_match.get("id", "")).strip() or None

            first_id = str(identities[0].get("id", "")).strip()
            if first_id:
                return first_id

        return None

    def _find_exact_identity_match(
        self,
        identities: list[dict[str, Any]],
        original_identifier: str,
        current_candidate: str,
    ) -> dict[str, Any] | None:
        """Find the best exact identity match from a candidate list."""

        original_identifier = original_identifier.lower()
        current_candidate = current_candidate.lower()

        for identity in identities:
            properties = identity.get("properties", {}) or {}
            account = str((properties.get("Account") or {}).get("$value", "")).strip().lower()
            mail = str((properties.get("Mail") or {}).get("$value", "")).strip().lower()
            display_name = str(identity.get("providerDisplayName", "")).strip().lower()
            if original_identifier in {account, mail, display_name}:
                return identity
            if current_candidate in {account, mail, display_name}:
                return identity
        return None

    def add_reviewer(
        self,
        repository: str,
        pull_request_id: int,
        reviewer_id: str,
        is_required: bool = True,
    ) -> dict[str, Any]:
        """Add a reviewer to an Azure DevOps pull request."""

        url = (
            f"{self._api_url}/_apis/git/repositories/{repository}/pullrequests/"
            f"{pull_request_id}/reviewers/{reviewer_id}"
        )
        params = {"api-version": "7.1"}
        body = {
            "id": reviewer_id,
            "isRequired": is_required,
        }
        response = self.session.put(url, params=params, json=body, timeout=self.timeout)
        self._raise_for_status_with_details(response, action=f"添加 PR 审阅人失败: PR {pull_request_id}")
        return response.json()

    @staticmethod
    def _extract_error_text(response: requests.Response) -> str:
        """Extract a concise error message from an Azure DevOps HTTP response."""

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            candidates = [
                str(payload.get("message", "")).strip(),
                str(payload.get("error", "")).strip(),
                str(payload.get("typeName", "")).strip(),
            ]
            text = " | ".join(item for item in candidates if item)
            if text:
                return text[:1200]

        return (response.text or "").strip()[:1200]

    @classmethod
    def _raise_for_status_with_details(
        cls,
        response: requests.Response,
        *,
        action: str,
    ) -> None:
        """Raise an actionable error that includes Azure DevOps response details."""

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = cls._extract_error_text(response)
            suffix = f" | 响应: {details}" if details else ""
            raise AzureDevOpsRequestError(
                f"{action} (HTTP {response.status_code}){suffix}",
                status_code=response.status_code,
                response_text=details,
            ) from exc

    def get_pull_requests(
        self,
        repository: str,
        state: str = "active",
    ) -> list[dict[str, Any]]:
        """Get pull requests for a repository."""
        url = f"{self._api_url}/_apis/git/repositories/{repository}/pullrequests"
        params = {
            "status": state,
            "$top": 50,
        }

        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json().get("value", [])


class GitClient:
    """Legacy Git facade backed by the shared repository gateway."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def clone(self, remote_url: str, branch: str | None = None) -> Path:
        """Clone a repository."""
        repo_name = remote_url.split("/")[-1].replace(".git", "")
        target_dir = self.workspace_root / repo_name

        if target_dir.exists():
            return target_dir

        gateway = GitRepositoryGateway(remote_url=remote_url)
        gateway.clone_repository(target_dir, branch=branch)
        return target_dir

    def create_branch(self, branch_name: str, cwd: Path | None = None) -> None:
        """Create a new branch."""
        work_dir = cwd or self.workspace_root
        gateway = GitRepositoryGateway(remote_url=str(work_dir))
        gateway.create_branch(work_dir, branch_name)

    def commit_and_push(
        self,
        message: str,
        files: list[str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        """Commit and push changes."""
        work_dir = cwd or self.workspace_root
        gateway = GitRepositoryGateway(remote_url=str(work_dir))
        gateway.stage_paths(work_dir, files)
        try:
            gateway.commit_all_changes(work_dir, message)
        except RuntimeError as exc:
            if "nothing to commit" in str(exc).lower():
                return
            raise
        gateway.push_head(work_dir)
