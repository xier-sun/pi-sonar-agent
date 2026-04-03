"""Claude Code SDK based Agent for fixing SonarQube issues.

This module provides the main agent class that:
1. Connects to SonarQube to get issues
2. Uses Claude Code to analyze and fix code issues
3. Runs build/test to verify fixes
4. Creates PR in Azure DevOps
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import requests
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from pi_sonar_agent.agent.rule_policies import (
    STATEMENT_SCOPE_MODE,
    get_rule_policy,
)
from pi_sonar_agent.core.agent_runtime import AgentRuntime, AgentRuntimeError, RuntimeTimeouts
from pi_sonar_agent.core.claude_adapter import ClaudeAdapter, ClaudeSDKDependencies
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.editor_policy import EditorPolicy
from pi_sonar_agent.core.fix_verifier import FixVerifier
from pi_sonar_agent.core.hooks import HookPipeline
from pi_sonar_agent.core.issue_planner import IssuePlanner
from pi_sonar_agent.core.issue_prompt import IssuePromptBuilder
from pi_sonar_agent.core.policy import ToolPolicy
from pi_sonar_agent.core.registry import build_fix_tool_registry
from pi_sonar_agent.core.resource_loader import ResourceLoader
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.scope_guard import IssueEditScope, LegacyScopeGuard

# ============== Data Classes ==============


@dataclass
class SonarIssue:
    """Represents a SonarQube issue."""

    key: str
    rule: str
    message: str
    line: int
    component: str
    severity: str
    issue_type: str
    status: str = "OPEN"

    @property
    def file_path(self) -> str:
        """Extract file path from component."""
        component = self.component.split(":", 1)[-1].replace("\\", "/")
        if not component.startswith("/"):
            component = f"/{component}"
        return component


@dataclass
class FixResult:
    """Result of a fix operation."""

    success: bool
    issue_key: str
    file_path: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    build_passed: bool = False
    build_verification_failed: bool = False
    error: str | None = None
    summary: str = ""
    build_command: str = ""
    build_output: str = ""
    attempts: int = 1
    skipped: bool = False
    skip_reason: str = ""
    issue_log_path: str = ""
    artifact_root: str = ""
    issue_state: Any | None = None
    retryable_failure: bool = False
    failure_kind: str = ""
    edit_contract: Any | None = None
    reviewer_result: Any | None = None
    follow_ups: tuple[Any, ...] = ()
    guardrail_mode: str = ""
    follow_up_log_path: str = ""

BUILTIN_FIX_TOOLS = [
    "Read",
    "Edit",
    "MultiEdit",
    "Write",
    "Grep",
    "Glob",
]


MCP_FIX_TOOLS: list[str] = []

FORBIDDEN_FIX_TOOLS = {
    "Bash",
    "mcp__sonar-fix__git_add",
    "mcp__sonar-fix__git_commit",
    "mcp__sonar-fix__git_push",
}

HEARTBEAT_INTERVAL_SECONDS = 30
CLIENT_CONNECT_TIMEOUT_SECONDS = 60
FIRST_RESPONSE_TIMEOUT_SECONDS = 120
FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS = 180
ISSUE_HARD_TIMEOUT_SECONDS = 900


# ============== Main Agent Class ==============


class ClaudeFixAgent:
    """Main agent for fixing SonarQube issues using Claude Code SDK."""

    QUALITY_GATE_PATHS = (
        Path.home() / ".claude" / "skills" / "csharp-quality-gate" / "SKILL.md",
        Path(__file__).resolve().parents[2] / "data" / "csharp-quality-gate.md",
    )
    QUALITY_GATE_SUPPLEMENT = """
## 当前执行补充约束

- 新增公开类、公开方法、公开属性、公开实体时，必须补齐完整 XML 文档注释；至少包含 `<summary>`，有参数时补 `<param>`，有返回值时补 `<returns>`，可能抛出的业务异常按需补 `<exception>`。
- 不要给 `private` 或 `internal` 的辅助方法添加残缺的 XML 文档注释；默认不写 XML 文档注释，确有必要时用简短中文注释说明业务意图。
- 严禁只写 `<summary>` 却省略与签名对应的 `<param>`、`<returns>` 等内容。
""".strip()

    def __init__(
        self,
        sonar_host: str,
        sonar_token: str,
        sonar_org: str | None = None,
        workspace_root: str = ".agent_workspaces",
        max_turns: int = 10,
        max_budget_usd: float = 5.0,
        agent_env: dict[str, str] | None = None,
        model: str | None = None,
    ):
        self.sonar_host = sonar_host.rstrip("/")
        self.sonar_token = sonar_token
        self.sonar_org = sonar_org
        self.workspace_root = Path(workspace_root)
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.agent_env = dict(agent_env or {})
        self.model = str(model).strip() if model is not None and str(model).strip() else None
        self.session = requests.Session()
        self.session.auth = (sonar_token, "")
        self.session.headers.update({"Accept": "application/json"})

    @staticmethod
    def _extract_agent_error(message: ResultMessage) -> str | None:
        """Extract a readable error from the SDK result payload."""

        if not message.is_error:
            return None

        details: list[str] = []
        if message.result and str(message.result).strip():
            details.append(str(message.result).strip())
        if message.errors:
            details.extend(str(item).strip() for item in message.errors if str(item).strip())

        if not details:
            return "Agent execution failed"

        # Preserve order while removing duplicates.
        return " | ".join(dict.fromkeys(details))

    @staticmethod
    def _collect_modified_files(workspace_path: Path) -> list[str]:
        """Collect files changed during the current attempt.

        When a per-attempt snapshot is available, compare against that baseline
        instead of using the whole workspace dirty state. This prevents earlier
        successful issue changes from being counted as changes for the current
        issue, and it still detects files that the model committed locally.
        """

        manifest = ClaudeFixAgent._load_attempt_state_manifest(workspace_path)
        if manifest is not None:
            return ClaudeFixAgent._collect_attempt_modified_files(workspace_path, manifest)

        return ClaudeFixAgent._collect_workspace_dirty_files(workspace_path)

    @staticmethod
    def _collect_workspace_dirty_files(workspace_path: Path) -> list[str]:
        """Collect dirty files from git status as a coarse fallback."""

        git_dir = workspace_path / ".git"
        if not git_dir.exists():
            return []

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except Exception:
            return []

        if result.returncode != 0:
            return []

        modified_files: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path_text = line[3:].strip()
            if not path_text:
                continue
            modified_files.append(path_text.split(" -> ", 1)[-1])

        return modified_files

    @staticmethod
    def _attempt_state_root(workspace_path: Path) -> Path:
        return workspace_path / ".git" / "pi-sonar-agent-attempt-state"

    @staticmethod
    def _read_file_bytes(file_path: Path) -> bytes:
        try:
            return file_path.read_bytes()
        except Exception:
            return b""

    @classmethod
    def _get_head_commit(cls, workspace_path: Path) -> str:
        git_dir = workspace_path / ".git"
        if not git_dir.exists():
            return ""

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except Exception:
            return ""

        if result.returncode != 0:
            return ""
        return (result.stdout or "").strip()

    @classmethod
    def _capture_attempt_workspace_state(cls, workspace_path: Path) -> None:
        """Persist the current attempt baseline under `.git`."""

        git_dir = workspace_path / ".git"
        if not git_dir.exists():
            return

        state_root = cls._attempt_state_root(workspace_path)
        if state_root.exists():
            shutil.rmtree(state_root, ignore_errors=True)

        files_root = state_root / "files"
        files_root.mkdir(parents=True, exist_ok=True)

        status_paths = cls._collect_workspace_dirty_files(workspace_path)
        existing_paths: list[str] = []
        for rel_path in status_paths:
            source = workspace_path / rel_path
            if source.is_file():
                target = files_root / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                existing_paths.append(rel_path)

        manifest = {
            "head_commit": cls._get_head_commit(workspace_path),
            "status_paths": status_paths,
            "existing_paths": existing_paths,
        }
        state_root.mkdir(parents=True, exist_ok=True)
        (state_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _cleanup_attempt_workspace_state(cls, workspace_path: Path) -> None:
        """Remove the current attempt snapshot."""

        state_root = cls._attempt_state_root(workspace_path)
        if state_root.exists():
            shutil.rmtree(state_root, ignore_errors=True)

    @classmethod
    def _load_attempt_state_manifest(cls, workspace_path: Path) -> dict[str, Any] | None:
        """Load the current attempt snapshot manifest if present."""

        manifest_path = cls._attempt_state_root(workspace_path) / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if not isinstance(data, dict):
            return None
        return data

    @classmethod
    def _attempt_head_changed(cls, workspace_path: Path) -> bool:
        """Return whether the current attempt changed HEAD."""

        manifest = cls._load_attempt_state_manifest(workspace_path)
        if manifest is None:
            return False
        baseline_head = str(manifest.get("head_commit", "")).strip()
        current_head = cls._get_head_commit(workspace_path)
        return bool(baseline_head and current_head and baseline_head != current_head)

    @classmethod
    def _collect_attempt_modified_files(
        cls,
        workspace_path: Path,
        manifest: dict[str, Any],
    ) -> list[str]:
        """Collect files changed relative to the current attempt baseline."""

        changed_files: set[str] = set()
        baseline_head = str(manifest.get("head_commit", "")).strip()
        before_paths = {
            str(path).replace("\\", "/")
            for path in manifest.get("status_paths", [])
            if str(path).strip()
        }
        existing_before = {
            str(path).replace("\\", "/")
            for path in manifest.get("existing_paths", [])
            if str(path).strip()
        }

        current_head = cls._get_head_commit(workspace_path)
        if baseline_head and current_head and baseline_head != current_head:
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", f"{baseline_head}..{current_head}"],
                    cwd=str(workspace_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            except Exception:
                result = None

            if result is not None and result.returncode == 0:
                for line in (result.stdout or "").splitlines():
                    rel_path = line.strip().replace("\\", "/")
                    if rel_path:
                        changed_files.add(rel_path)

        after_paths = {
            path.replace("\\", "/")
            for path in cls._collect_workspace_dirty_files(workspace_path)
        }
        files_root = cls._attempt_state_root(workspace_path) / "files"

        for rel_path in sorted(before_paths | after_paths):
            current_file = workspace_path / rel_path
            current_exists = current_file.is_file()
            before_exists = rel_path in existing_before

            if rel_path not in before_paths:
                changed_files.add(rel_path)
                continue

            if before_exists != current_exists:
                changed_files.add(rel_path)
                continue

            if before_exists and current_exists:
                snapshot_file = files_root / rel_path
                before_bytes = cls._read_file_bytes(snapshot_file)
                current_bytes = cls._read_file_bytes(current_file)
                if before_bytes != current_bytes:
                    changed_files.add(rel_path)

        return sorted(changed_files)

    @staticmethod
    def _display_agent_endpoint(agent_env: dict[str, str]) -> str:
        """Build a safe endpoint string for run logs."""

        return ClaudeAdapter.display_agent_endpoint(agent_env)

    @staticmethod
    def _display_agent_model(agent_env: dict[str, str], explicit_model: str | None) -> str:
        """Build a safe model string for run logs."""

        return ClaudeAdapter.display_agent_model(agent_env, explicit_model)

    @staticmethod
    def _uses_third_party_anthropic_provider(agent_env: dict[str, str]) -> bool:
        """Return True when the configured Anthropic endpoint is not first-party."""

        return ClaudeAdapter.uses_third_party_anthropic_provider(agent_env)

    @classmethod
    def _build_agent_extra_args(cls, agent_env: dict[str, str]) -> dict[str, Any]:
        """Build extra Claude CLI arguments for provider-specific compatibility."""

        return ClaudeAdapter.build_agent_extra_args(agent_env)

    @classmethod
    def _build_sdk_child_env(cls, agent_env: dict[str, str]) -> dict[str, str]:
        """Sanitize the env passed to Claude CLI for provider-specific compatibility."""

        return ClaudeAdapter.build_sdk_child_env(agent_env)

    @classmethod
    def _resolve_sdk_model(
        cls,
        agent_env: dict[str, str],
        child_env: dict[str, str],
        explicit_model: str | None,
    ) -> str | None:
        """Resolve how the Claude CLI should receive the selected model."""

        return ClaudeAdapter.resolve_sdk_model(agent_env, child_env, explicit_model)

    @staticmethod
    def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
        """Combine subprocess streams into a single safe string."""

        return FixVerifier._combine_process_output(result)

    @staticmethod
    def _normalize_exception_text(value: Any) -> str:
        """Normalize exception-related text values safely."""

        return FixVerifier._normalize_exception_text(value)

    @classmethod
    def _format_exception_details(cls, exc: BaseException) -> str:
        """Collect the most useful exception details, including stderr/stdout when present."""

        return FixVerifier.format_exception_details(exc)

    @classmethod
    def _run_local_build_fallback(
        cls,
        workspace_path: Path,
        build_command: str,
    ) -> tuple[bool, str]:
        """Run a local fallback build when the model-triggered build tool crashes."""

        return FixVerifier.run_local_build_fallback(workspace_path, build_command)

    def get_issues(
        self,
        project_key: str,
        author: str | None = None,
        severities: list[str] | None = None,
    ) -> list[SonarIssue]:
        """Get open issues from SonarQube."""
        params: dict[str, Any] = {
            "componentKeys": project_key,
            "statuses": "OPEN",
            "types": "BUG,CODE_SMELL",
            "ps": 100,
            "additionalFields": "_all",
        }
        if self.sonar_org:
            params["organization"] = self.sonar_org
        if author:
            params["authors"] = author
            params["author"] = author

        issues: list[SonarIssue] = []
        page = 1

        while True:
            params["p"] = page
            response = self.session.get(
                f"{self.sonar_host}/api/issues/search",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            for issue_data in data.get("issues", []):
                issue = SonarIssue(
                    key=issue_data.get("key", ""),
                    rule=issue_data.get("rule", ""),
                    message=issue_data.get("message", ""),
                    line=issue_data.get("line", 0),
                    component=issue_data.get("component", ""),
                    severity=issue_data.get("severity", ""),
                    issue_type=issue_data.get("type", ""),
                    status=issue_data.get("status", "OPEN"),
                )

                # Filter by severity if specified
                if severities and issue.severity not in severities:
                    continue

                issues.append(issue)

            paging = data.get("paging", {})
            total = paging.get("total", 0)
            if len(issues) >= total:
                break
            page += 1

        return issues

    def get_issue_snippet(self, issue_key: str) -> str:
        """Get the code snippet for an issue."""
        params: dict[str, Any] = {"issueKey": issue_key}
        if self.sonar_org:
            params["organization"] = self.sonar_org

        response = self.session.get(
            f"{self.sonar_host}/api/sources/issue_snippets",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return self._extract_snippet(response.json())

    def get_rule_details(self, rule_key: str) -> dict[str, str]:
        """Get details about a SonarQube rule."""
        params: dict[str, Any] = {"key": rule_key}
        if self.sonar_org:
            params["organization"] = self.sonar_org

        response = self.session.get(
            f"{self.sonar_host}/api/rules/show",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json().get("rule", {})

        return {
            "name": data.get("name", ""),
            "severity": data.get("severity", ""),
            "type": data.get("type", ""),
            "description": data.get("mdDesc", "") or data.get("htmlDesc", ""),
            "how_to_fix": data.get("mdNote", "") or data.get("htmlNote", ""),
        }

    @staticmethod
    def _normalize_prompt_text(value: str, fallback: str) -> str:
        """Normalize prompt text so the model always gets usable guidance."""

        return IssuePromptBuilder.normalize_prompt_text(value, fallback)

    @staticmethod
    def _strip_quality_gate_front_matter(text: str) -> str:
        """Strip optional YAML front matter from a quality-gate markdown file."""

        return ResourceLoader.strip_markdown_front_matter(text)

    @classmethod
    def _load_csharp_quality_gate(cls, issue: SonarIssue) -> str:
        """Load the C# quality gate for C# source files."""

        return ResourceLoader.load_csharp_quality_gate(
            issue.file_path,
            cls.QUALITY_GATE_PATHS,
            cls.QUALITY_GATE_SUPPLEMENT,
        )

    @classmethod
    def _build_issue_edit_scope(
        cls,
        issue: SonarIssue,
        lines: list[str],
    ) -> IssueEditScope:
        """Build the allowed edit scope for the issue."""

        return LegacyScopeGuard.build_issue_edit_scope(issue, lines)

    @staticmethod
    def _build_scope_guidance(issue: SonarIssue, scope: IssueEditScope | None) -> str:
        """Render edit-scope guidance for the model prompt."""

        return LegacyScopeGuard.build_scope_guidance(issue, scope)

    @staticmethod
    def _get_rule_skip_reason(issue: SonarIssue) -> str:
        """Return the default skip reason for a rule, if any."""

        return get_rule_policy(issue.rule).skip_reason

    @staticmethod
    def _run_rule_specific_validation(issue: SonarIssue, file_content: str) -> str:
        """Run post-fix local validation for rules that support it."""

        return FixVerifier.run_rule_specific_validation(issue, file_content)

    @staticmethod
    def _extract_changed_line_numbers(diff_text: str) -> set[int]:
        """Extract changed target line numbers from unified diff text."""

        return LegacyScopeGuard.extract_changed_line_numbers(diff_text)

    @staticmethod
    def _find_out_of_scope_lines(scope: IssueEditScope, changed_lines: set[int]) -> list[int]:
        """Find changed lines that exceed the allowed issue scope."""

        return LegacyScopeGuard.find_out_of_scope_lines(scope, changed_lines)

    @staticmethod
    def _build_content_diff(
        original_content: str,
        current_content: str,
        relative_path: str,
    ) -> str:
        """Build a unified diff for the current attempt only."""

        return LegacyScopeGuard.build_content_diff(original_content, current_content, relative_path)

    @classmethod
    def _validate_issue_edit_scope(
        cls,
        workspace_path: Path,
        issue: SonarIssue,
        scope: IssueEditScope | None,
        *,
        original_content: str | None = None,
        current_content: str | None = None,
    ) -> str | None:
        """Verify that the issue edit stayed inside the allowed code scope."""

        return LegacyScopeGuard.validate_issue_edit_scope(
            workspace_path,
            issue,
            scope,
            original_content=original_content,
            current_content=current_content,
        )

    @classmethod
    def _build_user_prompt(
        cls,
        issue: SonarIssue,
        code_context: str,
        quality_gate_text: str,
        scope_guidance: str,
        rule_details: dict[str, str],
        build_command: str,
        retry_feedback: str = "",
        retry_context: RetryContext | None = None,
        edit_contract_section: str = "",
    ) -> str:
        """Build the issue-specific user prompt."""

        return IssuePromptBuilder.build_user_prompt(
            issue=issue,
            code_context=code_context,
            quality_gate_text=quality_gate_text,
            scope_guidance=scope_guidance,
            rule_details=rule_details,
            build_command=build_command,
            retry_feedback=retry_feedback,
            retry_context=retry_context,
            edit_contract_section=edit_contract_section,
        )

    def _extract_snippet(self, data: dict) -> str:
        """Extract snippet text from SonarQube response."""
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

    @staticmethod
    def _sdk_dependencies() -> ClaudeSDKDependencies:
        """Build the SDK dependency bundle used by ClaudeAdapter."""

        return ClaudeSDKDependencies(
            client_cls=ClaudeSDKClient,
            options_cls=ClaudeAgentOptions,
            assistant_message_cls=AssistantMessage,
            result_message_cls=ResultMessage,
            text_block_cls=TextBlock,
            tool_use_block_cls=ToolUseBlock,
        )

    @staticmethod
    def _handle_cli_stderr(line: str) -> None:
        """Render SDK stderr lines in a stable log format."""

        text = str(line).strip()
        if not text:
            return
        print(f"  [CLI STDERR] {text}", flush=True)

    @classmethod
    def _build_system_prompt(cls, workspace_path: Path) -> str:
        """Compose the fix system prompt with optional workspace rules."""

        return IssuePromptBuilder.build_system_prompt(workspace_path)

    @staticmethod
    def _resolve_guardrail_mode(agent_env: dict[str, str] | None = None) -> str:
        """Resolve the configured issue guardrail mode."""

        raw_value = (
            (agent_env or {}).get("ISSUE_GUARDRAIL_MODE")
            or os.getenv("ISSUE_GUARDRAIL_MODE", "")
        )
        normalized = str(raw_value or "").strip().lower()
        if normalized in {"scope", "contract_review"}:
            return normalized
        return "scope"

    @classmethod
    def _build_issue_plan(
        cls,
        *,
        issue: SonarIssue,
        scope: IssueEditScope | None,
        retry_context: RetryContext | None,
        workspace_path: Path,
        agent_env: dict[str, str] | None = None,
    ):
        """Build the issue plan and edit contract for this attempt."""

        workspace_rules = ResourceLoader.load_workspace_rules(workspace_path)
        scope_mode = scope.mode if scope is not None else STATEMENT_SCOPE_MODE
        scope_start = scope.start_line if scope is not None else issue.line
        scope_end = scope.end_line if scope is not None else issue.line
        validation_start = scope.validation_start_line if scope is not None else issue.line
        validation_end = scope.validation_end_line if scope is not None else issue.line
        guardrail_mode = cls._resolve_guardrail_mode(agent_env)
        return IssuePlanner.plan_issue(
            issue_key=issue.key,
            rule_id=issue.rule,
            file_path=issue.file_path,
            issue_line=issue.line,
            guardrail_mode=guardrail_mode,
            scope_mode=scope_mode,
            scope_start_line=scope_start,
            scope_end_line=scope_end,
            validation_start_line=validation_start,
            validation_end_line=validation_end,
            retry_context=retry_context,
            workspace_rules=workspace_rules,
        )

    @classmethod
    def _build_fix_tool_policy(cls, edit_contract: Any | None = None) -> ToolPolicy:
        """Build the runtime tool policy for single-issue fix attempts."""

        registry = build_fix_tool_registry(
            BUILTIN_FIX_TOOLS,
            MCP_FIX_TOOLS,
            FORBIDDEN_FIX_TOOLS,
        )
        allowed_tools = [*BUILTIN_FIX_TOOLS, *MCP_FIX_TOOLS]
        if edit_contract is not None:
            allowed_tools = list(EditorPolicy.allowed_tool_names(allowed_tools, edit_contract))
        return ToolPolicy(registry, allowed_tools)

    @classmethod
    def _build_edit_contract_section(cls, edit_contract: Any | None) -> str:
        """Render the edit-contract summary used in the user prompt."""

        if edit_contract is None:
            return ""
        sections = [
            IssuePlanner.render_contract_guidance(edit_contract),
            EditorPolicy.render_prompt_constraints(edit_contract),
        ]
        return "\n\n".join(section for section in sections if section).strip()

    @classmethod
    def _build_attempt_file_changes(
        cls,
        workspace_path: Path,
        changed_files: tuple[str, ...] | list[str],
    ) -> tuple[ReviewedFileChange, ...]:
        """Build file-level diff facts for diff review."""

        manifest = cls._load_attempt_state_manifest(workspace_path) or {}
        files_root = cls._attempt_state_root(workspace_path) / "files"
        existing_before = {
            str(path).replace("\\", "/")
            for path in manifest.get("existing_paths", [])
            if str(path).strip()
        }
        file_changes: list[ReviewedFileChange] = []

        for rel_path in sorted(
            {
                str(path).replace("\\", "/").lstrip("/")
                for path in changed_files
                if str(path).strip()
            }
        ):
            current_file = workspace_path / rel_path
            after_exists = current_file.is_file()
            before_exists = rel_path in existing_before and (files_root / rel_path).is_file()
            before_text = (
                (files_root / rel_path).read_text(encoding="utf-8", errors="replace")
                if before_exists
                else ""
            )
            after_text = (
                current_file.read_text(encoding="utf-8", errors="replace")
                if after_exists
                else ""
            )
            diff_text = cls._build_content_diff(before_text, after_text, rel_path)
            if not diff_text and not before_exists and not after_exists:
                continue
            changed_lines = tuple(sorted(cls._extract_changed_line_numbers(diff_text)))
            hunk_count = sum(1 for line in diff_text.splitlines() if line.startswith("@@ "))
            file_changes.append(
                ReviewedFileChange(
                    file=rel_path,
                    changed_lines=changed_lines,
                    diff_text=diff_text,
                    hunk_count=hunk_count,
                    before_exists=before_exists,
                    after_exists=after_exists,
                )
            )

        return tuple(file_changes)

    def fix_issue(
        self,
        issue: SonarIssue,
        workspace_path: Path,
        build_command: str = "dotnet build",
        retry_feedback: str = "",
        retry_context: RetryContext | None = None,
    ) -> FixResult:
        """Fix a single SonarQube issue using Claude Code."""
        # Prepare workspace
        workspace_path.mkdir(parents=True, exist_ok=True)
        file_path = workspace_path / issue.file_path.lstrip("/")

        skip_reason = self._get_rule_skip_reason(issue)
        if skip_reason:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                error=skip_reason,
                summary="Skipped by rule policy",
                skipped=True,
                skip_reason=skip_reason,
                failure_kind="policy_skip",
            )

        # Get rule guidance
        rule_details = self.get_rule_details(issue.rule)

        # Get code context - try to get from local file first
        code_context = ""
        scope: IssueEditScope | None = None
        original_issue_file_content: str | None = None
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            original_issue_file_content = content
            lines = content.splitlines()
            start = max(0, issue.line - 10)
            end = min(len(lines), issue.line + 10)
            code_context = "\n".join(f"{i + 1:4d} | {lines[i]}" for i in range(start, end))
            scope = self._build_issue_edit_scope(issue, lines)
        else:
            # Fall back to SonarQube snippet
            try:
                snippet = self.get_issue_snippet(issue.key)
                code_context = snippet
            except Exception:
                code_context = f"File not found: {file_path}"

        issue_plan = self._build_issue_plan(
            issue=issue,
            scope=scope,
            retry_context=retry_context,
            workspace_path=workspace_path,
            agent_env=self.agent_env,
        )
        edit_contract = issue_plan.edit_contract
        guardrail_mode = edit_contract.guardrail_mode
        result_metadata = {
            "edit_contract": edit_contract,
            "guardrail_mode": guardrail_mode,
        }

        # Build prompts
        system_prompt = self._build_system_prompt(workspace_path)
        resolved_build_command = build_command.strip() or "dotnet build"
        user_prompt = self._build_user_prompt(
            issue,
            code_context,
            self._load_csharp_quality_gate(issue),
            self._build_scope_guidance(issue, scope),
            rule_details,
            resolved_build_command,
            retry_feedback,
            retry_context,
            edit_contract_section=self._build_edit_contract_section(edit_contract),
        )

        tool_policy = self._build_fix_tool_policy(edit_contract)
        gateway_request = ClaudeAdapter.build_request(
            agent_env=self.agent_env,
            explicit_model=self.model,
            cwd=str(workspace_path),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tuple(BUILTIN_FIX_TOOLS),
            allowed_tools=tool_policy.allowed_tool_names(),
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            stderr_handler=self._handle_cli_stderr,
            build_command=resolved_build_command,
        )
        runtime = AgentRuntime(
            gateway=ClaudeAdapter(self._sdk_dependencies()),
            tool_policy=tool_policy,
            timeouts=RuntimeTimeouts(
                client_connect_seconds=CLIENT_CONNECT_TIMEOUT_SECONDS,
                first_response_seconds=FIRST_RESPONSE_TIMEOUT_SECONDS,
                follow_up_seconds=FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS,
                issue_hard_timeout_seconds=ISSUE_HARD_TIMEOUT_SECONDS,
                heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
            ),
            hooks=HookPipeline(),
            run_sync=anyio.run,
        )

        changes: list[dict[str, Any]] = []
        runtime_result = None
        self._capture_attempt_workspace_state(workspace_path)
        try:
            runtime_result = runtime.run(gateway_request)
        except AgentRuntimeError as e:
            runtime_result = e.partial_result
            error_details = self._format_exception_details(e.cause) or str(e.cause)
            for modified_file in self._collect_modified_files(workspace_path):
                changes.append({"file": modified_file, "action": "modified"})

            model_timeout = (
                isinstance(e.cause, TimeoutError)
                or "没有返回首个响应" in error_details
                or "没有返回后续响应" in error_details
                or "单个 issue 在" in error_details
                or "未完成初始化" in error_details
            )
            if model_timeout:
                self._cleanup_attempt_workspace_state(workspace_path)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    error="Model response timed out",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=error_details,
                    retryable_failure=True,
                    failure_kind="model_timeout",
                    **result_metadata,
                )

            used_forbidden_tool = bool(runtime_result.forbidden_tool_uses) or self._attempt_head_changed(workspace_path)
            if used_forbidden_tool:
                fallback_build_passed, fallback_build_output = self._run_local_build_fallback(
                    workspace_path,
                    resolved_build_command,
                )
                output_parts = [
                    "修复阶段使用了被禁止的工具，当前尝试已作废。",
                ]
                if runtime_result.forbidden_tool_uses:
                    output_parts.append(
                        "禁止工具: " + ", ".join(dict.fromkeys(runtime_result.forbidden_tool_uses))
                    )
                if self._attempt_head_changed(workspace_path):
                    output_parts.append("检测到当前 attempt 改写了 Git HEAD/提交历史。")
                output_parts.append(error_details)
                output_parts.append(fallback_build_output)
                self._cleanup_attempt_workspace_state(workspace_path)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=fallback_build_passed,
                    build_verification_failed=not fallback_build_passed,
                    error="Forbidden tool used during issue fix",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output="\n\n".join(part for part in output_parts if part),
                    retryable_failure=True,
                    failure_kind="forbidden_tool",
                    **result_metadata,
                )

            build_tool_failed = (
                runtime_result.last_tool_name == "mcp__sonar-fix__run_build"
                or (
                    runtime_result.saw_build_tool
                    and "exit code" in error_details.lower()
                )
            )
            if build_tool_failed:
                fallback_build_passed, fallback_build_output = self._run_local_build_fallback(
                    workspace_path,
                    resolved_build_command,
                )
                output_parts = [
                    "run_build 工具执行异常。",
                    error_details,
                    fallback_build_output,
                ]
                self._cleanup_attempt_workspace_state(workspace_path)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=fallback_build_passed,
                    build_verification_failed=True,
                    error="Build tool execution failed",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output="\n\n".join(part for part in output_parts if part),
                    retryable_failure=True,
                    failure_kind="build_tool",
                    **result_metadata,
                )

            self._cleanup_attempt_workspace_state(workspace_path)
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                changes=changes,
                error=error_details,
                **result_metadata,
            )
        except Exception as e:
            error_details = self._format_exception_details(e) or str(e)
            for modified_file in self._collect_modified_files(workspace_path):
                changes.append({"file": modified_file, "action": "modified"})

            model_timeout = (
                isinstance(e, TimeoutError)
                or "没有返回首个响应" in error_details
                or "没有返回后续响应" in error_details
                or "单个 issue 在" in error_details
                or "未完成初始化" in error_details
            )
            if model_timeout:
                self._cleanup_attempt_workspace_state(workspace_path)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    error="Model response timed out",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=error_details,
                    retryable_failure=True,
                    failure_kind="model_timeout",
                    **result_metadata,
                )

            self._cleanup_attempt_workspace_state(workspace_path)
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                changes=changes,
                error=error_details,
                **result_metadata,
            )
        try:
            for modified_file in self._collect_modified_files(workspace_path):
                changes.append({"file": modified_file, "action": "modified"})

            runtime_result = runtime_result or runtime.run(gateway_request)
            used_forbidden_tool = bool(runtime_result.forbidden_tool_uses) or self._attempt_head_changed(workspace_path)

            if used_forbidden_tool:
                build_passed, build_output = self._run_local_build_fallback(
                    workspace_path,
                    resolved_build_command,
                )
                output_parts = [
                    "修复阶段使用了被禁止的工具，当前尝试已作废。",
                ]
                if runtime_result.forbidden_tool_uses:
                    output_parts.append(
                        "禁止工具: " + ", ".join(dict.fromkeys(runtime_result.forbidden_tool_uses))
                    )
                if self._attempt_head_changed(workspace_path):
                    output_parts.append("检测到当前 attempt 改写了 Git HEAD/提交历史。")
                if build_output:
                    output_parts.append(build_output)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=not build_passed,
                    error="Forbidden tool used during issue fix",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output="\n\n".join(part for part in output_parts if part),
                    retryable_failure=True,
                    failure_kind="forbidden_tool",
                    **result_metadata,
                )

            if runtime_result.agent_error:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    error=runtime_result.agent_error,
                    **result_metadata,
                )

            if not changes:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    error="Agent completed without modifying any files",
                    summary="Fixed 0 file(s)",
                    retryable_failure=True,
                    failure_kind="no_change",
                    **result_metadata,
                )

            current_issue_file_content: str | None = None
            if file_path.exists():
                current_issue_file_content = file_path.read_text(encoding="utf-8")

            changed_file_paths = tuple(
                str(change.get("file", "")).replace("\\", "/").lstrip("/")
                for change in changes
                if str(change.get("file", "")).strip()
            )
            reviewed_changes = self._build_attempt_file_changes(workspace_path, changed_file_paths)
            verification = FixVerifier.evaluate_attempt(
                issue=issue,
                workspace_path=workspace_path,
                build_command=resolved_build_command,
                edit_contract=edit_contract,
                guardrail_mode=guardrail_mode,
                scope=scope,
                reviewed_changes=reviewed_changes,
                original_issue_file_content=original_issue_file_content,
                current_issue_file_content=current_issue_file_content,
                build_runner=subprocess.run,
                scope_validator=self._validate_issue_edit_scope,
                rule_validator=self._run_rule_specific_validation,
            )
            reviewer_result = verification.reviewer_result
            build_passed = verification.build_passed
            build_output = verification.build_output
            scope_violation = verification.scope_violation

            if not build_passed:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=False,
                    build_verification_failed=True,
                    error="Issue changes failed local build verification",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=verification.combined_output,
                    retryable_failure=True,
                    failure_kind="build",
                    reviewer_result=reviewer_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    **result_metadata,
                )

            if guardrail_mode == "scope" and scope_violation:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Issue changes exceeded allowed scope",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=verification.combined_output or scope_violation,
                    retryable_failure=True,
                    failure_kind="scope",
                    reviewer_result=reviewer_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    **result_metadata,
                )

            if guardrail_mode == "contract_review" and reviewer_result.status == "retry":
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Diff reviewer rejected the patch",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=verification.reviewer_retry_message,
                    retryable_failure=True,
                    failure_kind="reviewer",
                    reviewer_result=reviewer_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    **result_metadata,
                )

            rule_validation_message = verification.rule_validation_message
            if rule_validation_message:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Rule-specific validation failed",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=rule_validation_message,
                    retryable_failure=True,
                    failure_kind="rule_validation",
                    reviewer_result=reviewer_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    **result_metadata,
                )

            return FixResult(
                success=True,
                issue_key=issue.key,
                file_path=str(file_path),
                changes=changes,
                build_passed=build_passed,
                summary=f"Fixed {len(changes)} file(s)",
                build_command=resolved_build_command,
                build_output=build_output,
                reviewer_result=reviewer_result.to_dict(),
                follow_ups=reviewer_result.follow_ups,
                **result_metadata,
            )
        finally:
            self._cleanup_attempt_workspace_state(workspace_path)


# ============== Convenience Functions ==============


def fix_issues(
    project_key: str,
    repository: str,
    author: str,
    sonar_host: str,
    sonar_token: str,
    ado_base_url: str,
    ado_project: str,
    ado_pat: str,
    build_command: str = "dotnet build",
    max_issues: int = 0,
    base_branch: str = "develop",
) -> dict[str, Any]:
    """Main entry point for fixing SonarQube issues.

    This is a synchronous wrapper around the async agent.
    """
    # Get issues
    agent = ClaudeFixAgent(
        sonar_host=sonar_host,
        sonar_token=sonar_token,
    )

    issues = agent.get_issues(project_key=project_key, author=author)
    print(f"Found {len(issues)} issues")

    if max_issues > 0:
        issues = issues[:max_issues]

    # Sort by severity
    severity_order = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}
    issues.sort(key=lambda x: severity_order.get(x.severity, 5))

    results = []
    for issue in issues:
        print(f"\nFixing: {issue.rule} at {issue.file_path}:{issue.line}")

        # Create workspace
        workspace = Path(f".agent_workspaces/{issue.key}")
        workspace.mkdir(parents=True, exist_ok=True)

        # Note: In real implementation, would clone repo here
        # For now, just show the issue
        result = agent.fix_issue(issue, workspace, build_command)
        results.append(result)

    # Summary
    success_count = sum(1 for r in results if r.success)
    return {
        "total": len(results),
        "successful": success_count,
        "failed": len(results) - success_count,
        "results": results,
    }
