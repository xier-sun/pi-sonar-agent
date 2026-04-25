"""Claude Code SDK based Agent for fixing SonarQube issues.

This module provides the main agent class that:
1. Connects to SonarQube to get issues
2. Uses Claude Code to analyze and fix code issues
3. Runs build/test to verify fixes
4. Creates PR in Azure DevOps
"""

import asyncio
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
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
from pi_sonar_agent.core.agent_runtime import (
    AgentRuntime,
    AgentRuntimeError,
    AgentRuntimeResult,
    RuntimeTimeouts,
)
from pi_sonar_agent.core.agent_role_prompts import (
    build_fix_role_system_prompt,
    build_fix_role_user_prompt,
    build_main_role_system_prompt,
    build_main_role_user_prompt,
    build_review_role_system_prompt,
    build_review_role_user_prompt,
)
from pi_sonar_agent.core.attempt_changes import AttemptFileChangeBuilder
from pi_sonar_agent.core.attempt_context import AttemptContextCache
from pi_sonar_agent.core.attempt_scheduler import AttemptScheduler
from pi_sonar_agent.core.attempt_todo import AttemptTodoStore, build_attempt_todo_runtime
from pi_sonar_agent.core.claude_adapter import ClaudeAdapter, ClaudeSDKDependencies
from pi_sonar_agent.core.continuation_recovery import ContinuationRecovery
from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.diff_reviewer import DiffReviewer
from pi_sonar_agent.core.editor_policy import EditorPolicy
from pi_sonar_agent.core.engine_router import route_engine_for_issue
from pi_sonar_agent.core.events import AttemptRuntimeEvent, AttemptRuntimeEventKind
from pi_sonar_agent.core.fix_verifier import FixVerifier
from pi_sonar_agent.core.hooks import HookPipeline
from pi_sonar_agent.core.issue_planner import IssuePlanner
from pi_sonar_agent.core.issue_prompt import IssuePromptBuilder
from pi_sonar_agent.core.memory.child_agent_memory import (
    ChildAgentMemory,
    append_child_agent_memory_turn,
    create_initial_child_agent_memory,
)
from pi_sonar_agent.core.memory.issue_working_memory import IssueWorkingMemory
from pi_sonar_agent.core.memory.issue_working_memory import merge_issue_working_memory
from pi_sonar_agent.core.memory.working_memory_store import WorkingMemoryStore
from pi_sonar_agent.core.mcp_servers import build_sonar_mcp_runtime
from pi_sonar_agent.core.model_gateway import ResultEvent, TextEvent, ToolCallEvent, TraceEvent
from pi_sonar_agent.core.perf_flags import load_performance_flags
from pi_sonar_agent.core.policy import ToolPolicy
from pi_sonar_agent.core.project_env import read_project_env
from pi_sonar_agent.core.quality_gate import render_quality_gate_prompt
from pi_sonar_agent.core.quality_gate_verifier import QualityGateVerifier
from pi_sonar_agent.core.registry import build_fix_tool_registry, build_visible_toolset
from pi_sonar_agent.core.resource_loader import DEFAULT_CSHARP_QUALITY_GATE_FILE, ResourceLoader
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.simple_mode import (
    is_simple_loop_execution_mode,
    resolve_execution_mode,
)
from pi_sonar_agent.core.scope_guard import IssueEditScope, LegacyScopeGuard
from pi_sonar_agent.core.tool_surface import (
    BASE_BUILTIN_FIX_TOOLS,
    CONTROLLED_BASH_TOOL,
    build_fix_runtime_tools,
    controlled_bash_enabled,
)
from pi_sonar_agent.fixers.deterministic import IssueGroup
from pi_sonar_agent.fixers.roslyn import RoslynFixEngine
from pi_sonar_agent.fixers.rule_profiles import load_rule_catalog
from pi_sonar_agent.fixers.s107_parameter_object import generate_s107_parameter_object_patch
from pi_sonar_agent.integrations.sonar import extract_rule_detail_texts

# ============== Data Classes ==============


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_text_range(raw_range: Any) -> dict[str, int]:
    if not isinstance(raw_range, dict):
        return {}

    normalized: dict[str, int] = {}
    for key in ("startLine", "endLine", "startOffset", "endOffset"):
        value = raw_range.get(key)
        if value in (None, ""):
            continue
        normalized[key] = _safe_int(value)
    return normalized


def _normalize_issue_flows(raw_flows: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw_flows, list):
        return ()

    normalized_flows: list[dict[str, Any]] = []
    for flow in raw_flows:
        if not isinstance(flow, dict):
            continue
        locations: list[dict[str, Any]] = []
        for location in flow.get("locations", []):
            if not isinstance(location, dict):
                continue
            component = str(location.get("component", "")).strip()
            message = str(location.get("msg", "")).strip()
            text_range = _normalize_text_range(location.get("textRange"))
            if not component and not message and not text_range:
                continue
            locations.append(
                {
                    "component": component,
                    "msg": message,
                    "textRange": text_range,
                }
            )
        if locations:
            normalized_flows.append({"locations": tuple(locations)})
    return tuple(normalized_flows)


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
    text_range: dict[str, int] = field(default_factory=dict)
    flows: tuple[dict[str, Any], ...] = ()

    @property
    def file_path(self) -> str:
        """Extract file path from component."""
        component = self.component.split(":", 1)[-1].replace("\\", "/")
        if not component.startswith("/"):
            component = f"/{component}"
        return component

    @property
    def start_line(self) -> int:
        return _safe_int(self.text_range.get("startLine")) or self.line

    @property
    def end_line(self) -> int:
        return _safe_int(self.text_range.get("endLine")) or self.start_line

    @classmethod
    def from_api_payload(cls, issue_data: dict[str, Any]) -> "SonarIssue":
        text_range = _normalize_text_range(issue_data.get("textRange"))
        line = _safe_int(issue_data.get("line")) or _safe_int(text_range.get("startLine"))
        return cls(
            key=str(issue_data.get("key", "")).strip(),
            rule=str(issue_data.get("rule", "")).strip(),
            message=str(issue_data.get("message", "")).strip(),
            line=line,
            component=str(issue_data.get("component", "")).strip(),
            severity=str(issue_data.get("severity", "")).strip(),
            issue_type=str(issue_data.get("type", "")).strip(),
            status=str(issue_data.get("status", "OPEN")).strip() or "OPEN",
            text_range=text_range,
            flows=_normalize_issue_flows(issue_data.get("flows")),
        )


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
    repair_plan: Any | None = None
    plan_precheck: Any | None = None
    reviewer_result: Any | None = None
    semantic_precheck_result: Any | None = None
    quality_gate_result: Any | None = None
    review_gate_result: Any | None = None
    follow_ups: tuple[Any, ...] = ()
    guardrail_mode: str = ""
    follow_up_log_path: str = ""
    boundary_failure_code: str = ""
    boundary_failure_summary: str = ""
    secondary_boundary_failure_codes: tuple[str, ...] = ()
    performance_metrics: dict[str, Any] = field(default_factory=dict)
    execution_profile: str = "full_path"
    fast_path_enabled: bool = False
    rollout_flags: tuple[str, ...] = ()
    model_timeout_stage: str = ""
    patch_salvaged: bool = False
    attempt_events: tuple[Any, ...] = ()
    engine_routing_decision: Any | None = None
    prompt_budget_report: Any | None = None
    visible_toolset: Any | None = None
    execution_mode: str = ""
    post_fix_check_result: Any | None = None
    issue_working_memory: Any | None = None
    attempt_todo_state: Any | None = None


@dataclass(frozen=True)
class RoleAgentRunResult:
    """Result of one fix/review/main child-agent session."""

    role: str
    response_text: str
    total_cost_usd: float = 0.0
    agent_error: str | None = None
    tool_uses: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleDecision:
    """Parsed structured decision returned by review/main child agents."""

    decision: str
    summary: str
    findings: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    raw_text: str = ""

BUILTIN_FIX_TOOLS = list(BASE_BUILTIN_FIX_TOOLS)
MCP_FIX_TOOLS: list[str] = []

FORBIDDEN_FIX_TOOLS = {
    "mcp__sonar-fix__git_add",
    "mcp__sonar-fix__git_commit",
    "mcp__sonar-fix__git_push",
}

HEARTBEAT_INTERVAL_SECONDS = 30
CLIENT_CONNECT_TIMEOUT_SECONDS = 60
FIRST_RESPONSE_TIMEOUT_SECONDS = 120
FOLLOW_UP_RESPONSE_TIMEOUT_SECONDS = 180
ISSUE_HARD_TIMEOUT_SECONDS = 900
S107_FIX_GUIDE_SOURCE_PATH = Path(__file__).resolve().parents[2] / "docs" / "s107-fix-guide.md"
S107_FIX_GUIDE_WORKSPACE_RELATIVE_PATH = ".pi-sonar-agent-runtime/s107-fix-guide.md"


# ============== Main Agent Class ==============


class ClaudeFixAgent:
    """Main agent for fixing SonarQube issues using Claude Code SDK."""

    QUALITY_GATE_PATHS = (DEFAULT_CSHARP_QUALITY_GATE_FILE,)

    def __init__(
        self,
        sonar_host: str,
        sonar_token: str,
        sonar_org: str | None = None,
        workspace_root: str = ".agent_workspaces",
        max_turns: int = 16,
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
    def _resolve_continuation_max_turns(request_max_turns: int) -> int:
        """Keep same-attempt continuations within the current turn budget instead of shrinking them."""

        return max(2, _safe_int(request_max_turns) or 2)

    def _resolve_issue_max_turns(self, issue: SonarIssue) -> int:
        """Use the higher per-rule budget when available without lowering the instance default."""

        resolved = max(2, _safe_int(self.max_turns) or 2)
        try:
            profile = load_rule_catalog().get(issue.rule)
        except Exception:
            return resolved
        if profile is None or profile.max_turns <= 0:
            return resolved
        return max(resolved, profile.max_turns)

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

    @classmethod
    def _sync_s107_fix_guide(cls, workspace_path: Path) -> str:
        """Ensure the S107 guide is readable from the active fix workspace."""

        source_path = S107_FIX_GUIDE_SOURCE_PATH
        if not source_path.exists():
            return ""
        target_path = workspace_path / S107_FIX_GUIDE_WORKSPACE_RELATIVE_PATH
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            return ""
        return S107_FIX_GUIDE_WORKSPACE_RELATIVE_PATH

    def _resolve_runtime_builtin_tools(self, workspace_path: Path) -> tuple[str, ...]:
        del workspace_path
        return build_fix_runtime_tools(include_create_file_tool=False)

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
                issue = SonarIssue.from_api_payload(issue_data)

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
        description, how_to_fix = extract_rule_detail_texts(data)

        return {
            "name": data.get("name", ""),
            "severity": data.get("severity", ""),
            "type": data.get("type", ""),
            "description": description,
            "how_to_fix": how_to_fix,
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
    def _load_csharp_quality_gate(cls, issue: SonarIssue, edit_contract: Any | None = None) -> str:
        """Load prompt-facing quality-gate guidance for the current issue."""

        if not str(issue.file_path or "").lower().endswith(".cs"):
            return ""

        active_rules = tuple(getattr(edit_contract, "quality_gate_rules", ()) or ())
        source_path, _, markdown_body = ResourceLoader.load_markdown_document(cls.QUALITY_GATE_PATHS)
        if active_rules:
            rendered = render_quality_gate_prompt(
                active_rules,
                source_path=source_path.as_posix() if source_path is not None else "",
            )
            if rendered.strip():
                return rendered
        return markdown_body.strip()

    @classmethod
    def _build_issue_edit_scope(
        cls,
        issue: SonarIssue,
        lines: list[str],
    ) -> IssueEditScope:
        """Build the allowed edit scope for the issue."""

        return LegacyScopeGuard.build_issue_edit_scope(issue, lines)

    @staticmethod
    def _build_scope_guidance(
        issue: SonarIssue,
        scope: IssueEditScope | None,
        edit_contract: EditContract | None = None,
    ) -> str:
        """Render edit-scope guidance for the model prompt."""

        allow_helper_extract = True
        if edit_contract is not None:
            allow_helper_extract = "helper_extract" in tuple(
                getattr(edit_contract, "allowed_capabilities", ()) or ()
            )
        return LegacyScopeGuard.build_scope_guidance(
            issue,
            scope,
            allow_helper_extract=allow_helper_extract,
        )

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

        return AttemptFileChangeBuilder.extract_touched_line_numbers(diff_text)

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

        return AttemptFileChangeBuilder.build_content_diff(original_content, current_content, relative_path)

    @classmethod
    def _validate_issue_edit_scope(
        cls,
        workspace_path: Path,
        issue: SonarIssue,
        scope: IssueEditScope | None,
        *,
        edit_contract: Any | None = None,
        original_content: str | None = None,
        current_content: str | None = None,
    ) -> str | None:
        """Verify that the issue edit stayed inside the allowed code scope."""

        return LegacyScopeGuard.validate_issue_edit_scope(
            workspace_path,
            issue,
            scope,
            edit_contract=edit_contract,
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
        repair_plan_section: str = "",
        prefetched_context_section: str = "",
        execution_mode_section: str = "",
        workspace_path: Path | None = None,
        edit_contract: Any | None = None,
        visible_tool_names: tuple[str, ...] | list[str] = (),
        working_memory: IssueWorkingMemory | None = None,
        model_hint: str = "",
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
            repair_plan_section=repair_plan_section,
            prefetched_context_section=prefetched_context_section,
            execution_mode_section=execution_mode_section,
            workspace_path=workspace_path,
            edit_contract=edit_contract,
            visible_tool_names=visible_tool_names,
            working_memory=working_memory,
            model_hint=model_hint,
        )

    @classmethod
    def _build_user_prompt_result(
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
        repair_plan_section: str = "",
        prefetched_context_section: str = "",
        execution_mode_section: str = "",
        workspace_path: Path | None = None,
        edit_contract: Any | None = None,
        visible_tool_names: tuple[str, ...] | list[str] = (),
        working_memory: IssueWorkingMemory | None = None,
        model_hint: str = "",
    ) -> Any:
        """Build the issue-specific user prompt plus budget metadata."""

        return IssuePromptBuilder.build_user_prompt_result(
            issue=issue,
            code_context=code_context,
            quality_gate_text=quality_gate_text,
            scope_guidance=scope_guidance,
            rule_details=rule_details,
            build_command=build_command,
            retry_feedback=retry_feedback,
            retry_context=retry_context,
            edit_contract_section=edit_contract_section,
            repair_plan_section=repair_plan_section,
            prefetched_context_section=prefetched_context_section,
            execution_mode_section=execution_mode_section,
            workspace_path=workspace_path,
            edit_contract=edit_contract,
            visible_tool_names=visible_tool_names,
            working_memory=working_memory,
            model_hint=model_hint,
        )

    @staticmethod
    def _build_prefetched_context_section(edit_contract: Any | None) -> str:
        """Render prefetched related snippets for fast path / boundary-aware fixes."""

        snippets = tuple(getattr(edit_contract, "prefetched_context", ()) or ())
        if not snippets:
            return ""
        lines = ["【预取上下文】", "- 以下片段由外层 planner 预先打包，请优先使用，避免重复 Read 同一片段。"]
        for snippet in snippets:
            content = str(getattr(snippet, "content", "") or "").strip()
            if not content:
                continue
            lines.extend(
                [
                    f"- {snippet.label} [{snippet.start_line}-{snippet.end_line}] ({snippet.reason})",
                    content,
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _build_execution_mode_section(edit_contract: Any | None) -> str:
        """Render short-form execution instructions when enabled."""

        if edit_contract is not None and is_simple_loop_execution_mode(
            getattr(edit_contract, "execution_mode", "")
        ):
            lines = [
                "【执行模式】",
                "- 当前 issue 使用 headless simple-loop execution。",
                "- 目标是先完成当前 issue 的最小修复，再交给外层做 build 和 post-check。",
                "- 优先提交可编译、最小、聚焦当前 issue 的 patch；不要展开长篇推理。",
                "- 如果上一轮方案失败或已回滚，请直接换一种更小的修法。",
            ]
            return "\n".join(lines)
        if edit_contract is None or not bool(getattr(edit_contract, "fast_path_enabled", False)):
            return ""
        return "\n".join(
            [
                "【执行模式】",
                "- 当前 issue 进入 fast-path short-form execution。",
                "- 只读取完成本次修复所需的最小上下文，避免重复 Read 同一片段。",
                "- 一旦 patch 已落盘，立即结束，不要输出长篇修复总结或背景分析。",
                "- 优先直接完成精确 patch，而不是先写大量自然语言解释。",
            ]
        )

    @staticmethod
    def _build_repair_plan_section(edit_contract: Any | None) -> str:
        """Render structured plan-first guidance for complex rules."""

        if edit_contract is None:
            return ""
        return IssuePlanner.render_repair_plan_guidance(edit_contract)

    @staticmethod
    def _build_runtime_performance_metrics(runtime_result: AgentRuntimeResult | None) -> dict[str, Any]:
        """Normalize runtime metrics into a stable artifact payload."""

        if runtime_result is None:
            return {}
        return {
            "runtime_total_duration_seconds": round(float(getattr(runtime_result, "total_duration_seconds", 0.0) or 0.0), 3),
            "time_to_first_model_content_seconds": round(float(getattr(runtime_result, "time_to_first_model_content_seconds", 0.0) or 0.0), 3),
            "time_after_first_edit_to_finalize_seconds": round(float(getattr(runtime_result, "time_after_first_edit_to_finalize_seconds", 0.0) or 0.0), 3),
            "tool_call_count": int(getattr(runtime_result, "tool_call_count", 0) or 0),
            "read_call_count": int(getattr(runtime_result, "read_call_count", 0) or 0),
            "edit_call_count": int(getattr(runtime_result, "edit_call_count", 0) or 0),
            "assistant_text_events": int(getattr(runtime_result, "assistant_text_events", 0) or 0),
            "assistant_text_chars": int(getattr(runtime_result, "assistant_text_chars", 0) or 0),
            "model_timeout_stage": str(getattr(runtime_result, "timeout_stage", "") or "").strip(),
            "last_progress_stage": str(getattr(runtime_result, "last_progress_stage", "") or "").strip(),
            "saw_result_event": bool(getattr(runtime_result, "saw_result_event", False)),
            "continuation_retry_count": int(getattr(runtime_result, "continuation_retry_count", 0) or 0),
            "continuation_recovered": bool(getattr(runtime_result, "continuation_recovered", False)),
            "continuation_timeout_stages": list(
                getattr(runtime_result, "continuation_timeout_stages", ()) or ()
            ),
            "warning_tool_uses": list(getattr(runtime_result, "warning_tool_uses", ()) or ()),
            "edit_nudge_count": int(getattr(runtime_result, "edit_nudge_count", 0) or 0),
            "successful_edit_count": int(getattr(runtime_result, "successful_edit_count", 0) or 0),
            "invalid_write_tool_input_count": int(
                getattr(runtime_result, "invalid_write_tool_input_count", 0) or 0
            ),
            "todo_write_count": int(getattr(runtime_result, "todo_write_count", 0) or 0),
            "todo_reminder_count": int(getattr(runtime_result, "todo_reminder_count", 0) or 0),
        }

    @staticmethod
    def _merge_runtime_results(
        first_result: AgentRuntimeResult,
        second_result: AgentRuntimeResult,
        *,
        merged_events: list[AttemptRuntimeEvent],
        continuation_retry_count: int,
        continuation_recovered: bool,
        continuation_timeout_stages: tuple[str, ...] = (),
    ) -> AgentRuntimeResult:
        """Merge two runtime results produced inside the same logical attempt."""

        first_tool_uses = tuple(getattr(first_result, "tool_uses", ()) or ())
        second_tool_uses = tuple(getattr(second_result, "tool_uses", ()) or ())
        first_forbidden = tuple(getattr(first_result, "forbidden_tool_uses", ()) or ())
        second_forbidden = tuple(getattr(second_result, "forbidden_tool_uses", ()) or ())
        first_warnings = tuple(getattr(first_result, "warning_tool_uses", ()) or ())
        second_warnings = tuple(getattr(second_result, "warning_tool_uses", ()) or ())
        first_first_response = float(getattr(first_result, "time_to_first_model_content_seconds", 0.0) or 0.0)
        second_first_response = float(getattr(second_result, "time_to_first_model_content_seconds", 0.0) or 0.0)
        return AgentRuntimeResult(
            agent_error=str(getattr(second_result, "agent_error", "") or "").strip()
            or str(getattr(first_result, "agent_error", "") or "").strip()
            or None,
            tool_uses=first_tool_uses + second_tool_uses,
            forbidden_tool_uses=first_forbidden + second_forbidden,
            warning_tool_uses=first_warnings + second_warnings,
            last_tool_name=str(getattr(second_result, "last_tool_name", "") or "").strip()
            or str(getattr(first_result, "last_tool_name", "") or "").strip()
            or None,
            saw_build_tool=bool(getattr(first_result, "saw_build_tool", False))
            or bool(getattr(second_result, "saw_build_tool", False)),
            total_duration_seconds=round(
                float(getattr(first_result, "total_duration_seconds", 0.0) or 0.0)
                + float(getattr(second_result, "total_duration_seconds", 0.0) or 0.0),
                3,
            ),
            time_to_first_model_content_seconds=round(first_first_response or second_first_response, 3),
            time_after_first_edit_to_finalize_seconds=round(
                float(getattr(first_result, "time_after_first_edit_to_finalize_seconds", 0.0) or 0.0)
                + float(getattr(second_result, "time_after_first_edit_to_finalize_seconds", 0.0) or 0.0),
                3,
            ),
            tool_call_count=int(getattr(first_result, "tool_call_count", 0) or 0)
            + int(getattr(second_result, "tool_call_count", 0) or 0),
            read_call_count=int(getattr(first_result, "read_call_count", 0) or 0)
            + int(getattr(second_result, "read_call_count", 0) or 0),
            edit_call_count=int(getattr(first_result, "edit_call_count", 0) or 0)
            + int(getattr(second_result, "edit_call_count", 0) or 0),
            assistant_text_events=int(getattr(first_result, "assistant_text_events", 0) or 0)
            + int(getattr(second_result, "assistant_text_events", 0) or 0),
            assistant_text_chars=int(getattr(first_result, "assistant_text_chars", 0) or 0)
            + int(getattr(second_result, "assistant_text_chars", 0) or 0),
            timeout_stage=str(getattr(second_result, "timeout_stage", "") or "").strip()
            or str(getattr(first_result, "timeout_stage", "") or "").strip(),
            last_progress_stage=str(getattr(second_result, "last_progress_stage", "") or "").strip()
            or str(getattr(first_result, "last_progress_stage", "") or "").strip(),
            saw_result_event=bool(getattr(first_result, "saw_result_event", False))
            or bool(getattr(second_result, "saw_result_event", False)),
            continuation_retry_count=continuation_retry_count,
            continuation_recovered=continuation_recovered,
            continuation_timeout_stages=continuation_timeout_stages,
            edit_nudge_count=int(getattr(first_result, "edit_nudge_count", 0) or 0)
            + int(getattr(second_result, "edit_nudge_count", 0) or 0),
            successful_edit_count=int(getattr(first_result, "successful_edit_count", 0) or 0)
            + int(getattr(second_result, "successful_edit_count", 0) or 0),
            invalid_write_tool_input_count=int(
                getattr(first_result, "invalid_write_tool_input_count", 0) or 0
            )
            + int(getattr(second_result, "invalid_write_tool_input_count", 0) or 0),
            todo_write_count=int(getattr(first_result, "todo_write_count", 0) or 0)
            + int(getattr(second_result, "todo_write_count", 0) or 0),
            todo_reminder_count=int(getattr(first_result, "todo_reminder_count", 0) or 0)
            + int(getattr(second_result, "todo_reminder_count", 0) or 0),
            runtime_events=tuple(merged_events),
        )

    @staticmethod
    def _merge_attempt_events(
        existing_events: list[AttemptRuntimeEvent],
        new_events: tuple[Any, ...] | list[Any],
    ) -> list[AttemptRuntimeEvent]:
        """Merge runtime-event batches while keeping sequence ordering stable."""

        merged = list(existing_events)
        for raw_event in tuple(new_events or ()):
            if not isinstance(raw_event, AttemptRuntimeEvent):
                continue
            merged.append(
                AttemptRuntimeEvent(
                    kind=raw_event.kind,
                    sequence=len(merged) + 1,
                    run_label=raw_event.run_label,
                    issue_key=raw_event.issue_key,
                    attempt_number=raw_event.attempt_number,
                    stage=raw_event.stage,
                    timestamp=raw_event.timestamp,
                    payload=dict(raw_event.payload or {}),
                )
            )
        return merged

    @staticmethod
    def _append_attempt_event(
        events: list[AttemptRuntimeEvent],
        kind: AttemptRuntimeEventKind,
        *,
        stage: str = "",
        payload: dict[str, Any] | None = None,
        runtime_result: AgentRuntimeResult | None = None,
    ) -> None:
        """Append one post-runtime attempt event while keeping sequence ordering stable."""

        base_events = tuple(getattr(runtime_result, "runtime_events", ()) or ())
        run_label = str(base_events[0].run_label) if base_events else ""
        issue_key = str(base_events[0].issue_key) if base_events else ""
        attempt_number = int(base_events[0].attempt_number) if base_events else 0
        event = AttemptRuntimeEvent(
            kind=kind,
            sequence=len(events) + 1,
            run_label=run_label,
            issue_key=issue_key,
            attempt_number=attempt_number,
            stage=str(stage or ""),
            payload=dict(payload or {}),
        )
        events.append(event)

    @classmethod
    def _run_runtime_with_continuation(
        cls,
        *,
        runtime: AgentRuntime,
        gateway_request,
        execution_schedule,
        workspace_path: Path,
    ) -> AgentRuntimeResult:
        """Run the model runtime with bounded same-context continuation recovery."""

        base_request = gateway_request
        current_request = gateway_request
        merged_events: list[AttemptRuntimeEvent] = []
        continuation_count = 0
        continuation_timeout_stages: list[str] = []

        while True:
            try:
                runtime_result = runtime.run(current_request)
                combined_events = cls._merge_attempt_events(
                    merged_events,
                    tuple(getattr(runtime_result, "runtime_events", ()) or ()),
                )
                return replace(
                    runtime_result,
                    runtime_events=tuple(combined_events),
                    continuation_retry_count=continuation_count,
                    continuation_recovered=continuation_count > 0,
                    continuation_timeout_stages=tuple(continuation_timeout_stages),
                )
            except AgentRuntimeError as exc:
                partial_result = exc.partial_result or AgentRuntimeResult()
                error_details = cls._format_exception_details(exc.cause) or str(exc.cause)
                timeout_stage = (
                    str(getattr(partial_result, "timeout_stage", "") or "").strip()
                    or cls._infer_timeout_stage(error_details)
                )
                merged_events = cls._merge_attempt_events(
                    merged_events,
                    tuple(getattr(partial_result, "runtime_events", ()) or ()),
                )
                changed_files = tuple(dict.fromkeys(cls._collect_modified_files(workspace_path)))
                used_forbidden_tool = bool(partial_result.forbidden_tool_uses) or cls._attempt_head_changed(workspace_path)
                build_tool_failed = (
                    partial_result.last_tool_name == "mcp__sonar-fix__run_build"
                    or (partial_result.saw_build_tool and "exit code" in error_details.lower())
                )
                if AttemptScheduler.should_continue_after_timeout(
                    schedule=execution_schedule,
                    timeout_stage=timeout_stage,
                    continuation_count=continuation_count,
                    changes_detected=bool(changed_files),
                    used_forbidden_tool=used_forbidden_tool,
                    build_tool_failed=build_tool_failed,
                ):
                    continuation_count += 1
                    continuation_timeout_stages.append(timeout_stage or "follow_up_response_timeout")
                    context = ContinuationRecovery.build_context(
                        events=tuple(merged_events),
                        timeout_stage=timeout_stage or "follow_up_response_timeout",
                        continuation_index=continuation_count,
                        last_progress_stage=str(getattr(partial_result, "last_progress_stage", "") or "").strip(),
                        last_tool_name=str(getattr(partial_result, "last_tool_name", "") or "").strip(),
                        changed_files=changed_files,
                    )
                    cls._append_attempt_event(
                        merged_events,
                        AttemptRuntimeEventKind.CONTINUATION_REQUESTED,
                        stage=context.timeout_stage,
                        payload=context.to_dict(),
                        runtime_result=partial_result,
                    )
                    print(
                        "  [TRACE] follow-up 超时，进入同上下文 continuation: "
                        f"index={continuation_count}, stage={context.timeout_stage}",
                        flush=True,
                    )
                    current_request = replace(
                        base_request,
                        user_prompt=ContinuationRecovery.build_prompt(
                            base_request.user_prompt,
                            context,
                        ),
                        max_turns=cls._resolve_continuation_max_turns(base_request.max_turns),
                        metadata={
                            **dict(base_request.metadata),
                            "continuation_index": str(continuation_count),
                            "continuation_stage": context.timeout_stage,
                        },
                    )
                    continue

                final_partial = replace(
                    partial_result,
                    timeout_stage=timeout_stage or partial_result.timeout_stage,
                    runtime_events=tuple(merged_events),
                    continuation_retry_count=continuation_count,
                    continuation_recovered=False,
                    continuation_timeout_stages=tuple(continuation_timeout_stages),
                )
                raise AgentRuntimeError(exc.cause, final_partial) from exc

    @classmethod
    def _run_no_change_continuation(
        cls,
        *,
        runtime: AgentRuntime,
        gateway_request,
        initial_result: AgentRuntimeResult,
        workspace_path: Path,
    ) -> AgentRuntimeResult:
        """Give no-change attempts one same-context push before falling back to a full retry."""

        merged_events = cls._merge_attempt_events(
            [],
            tuple(getattr(initial_result, "runtime_events", ()) or ()),
        )
        changed_files = tuple(dict.fromkeys(cls._collect_modified_files(workspace_path)))
        context = ContinuationRecovery.build_context(
            events=tuple(merged_events),
            timeout_stage="no_change",
            continuation_index=1,
            last_progress_stage=str(getattr(initial_result, "last_progress_stage", "") or "").strip(),
            last_tool_name=str(getattr(initial_result, "last_tool_name", "") or "").strip(),
            changed_files=changed_files,
        )
        cls._append_attempt_event(
            merged_events,
            AttemptRuntimeEventKind.CONTINUATION_REQUESTED,
            stage="no_change",
            payload={**context.to_dict(), "reason": "no_change"},
            runtime_result=initial_result,
        )
        print("  [TRACE] no_change continuation: reuse current context and force an edit decision", flush=True)
        continuation_request = replace(
            gateway_request,
            user_prompt=ContinuationRecovery.build_no_change_prompt(
                gateway_request.user_prompt,
                context,
            ),
            max_turns=cls._resolve_continuation_max_turns(gateway_request.max_turns),
            metadata={
                **dict(gateway_request.metadata),
                "continuation_index": "1",
                "continuation_stage": "no_change",
            },
        )
        continuation_result = runtime.run(continuation_request)
        merged_events = cls._merge_attempt_events(
            merged_events,
            tuple(getattr(continuation_result, "runtime_events", ()) or ()),
        )
        return cls._merge_runtime_results(
            initial_result,
            continuation_result,
            merged_events=merged_events,
            continuation_retry_count=1,
            continuation_recovered=bool(getattr(continuation_result, "tool_uses", ()) or ())
            and (
                int(getattr(continuation_result, "edit_call_count", 0) or 0) > 0
                or bool(getattr(continuation_result, "saw_result_event", False))
            ),
        )

    @staticmethod
    def _merge_performance_metrics(
        base_metrics: dict[str, Any] | None,
        **updates: Any,
    ) -> dict[str, Any]:
        """Merge runtime/build performance facts into one stable payload."""

        merged = dict(base_metrics or {})
        for key, value in updates.items():
            if value is None:
                continue
            merged[key] = value
        return merged

    @staticmethod
    def _infer_timeout_stage(error_text: str) -> str:
        """Infer a stable timeout stage from runtime error text."""

        normalized = str(error_text or "")
        if "阶段分类:" in normalized:
            return normalized.split("阶段分类:", 1)[1].splitlines()[0].strip()
        if "没有返回后续响应" in normalized:
            return "follow_up_response_timeout"
        if "没有返回首个响应" in normalized:
            return "first_response_timeout"
        if "未完成初始化" in normalized:
            return "client_connect_timeout"
        if "单个 issue 在" in normalized:
            return "issue_hard_timeout"
        return ""

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

    @classmethod
    def _build_system_prompt_result(
        cls,
        workspace_path: Path,
        *,
        edit_contract: Any | None = None,
    ) -> Any:
        """Compose the fix system prompt plus budget metadata."""

        return IssuePromptBuilder.build_system_prompt_result(
            workspace_path,
            execution_mode=str(getattr(edit_contract, "execution_mode", "") or ""),
        )

    @staticmethod
    def _normalize_edit_contract_for_child_agents(edit_contract: Any) -> Any:
        """Drop legacy local constraints for the child-agent flow."""

        return replace(
            edit_contract,
            execution_mode="simple_loop",
            allow_file_creation=False,
            allowed_new_file_roots=(),
        )

    @staticmethod
    def _build_patch_summary(
        issue: SonarIssue,
        edit_contract: Any,
        current_issue_file_content: str,
        reviewed_changes: tuple[ReviewedFileChange, ...],
    ) -> str:
        """Build a target-aware patch summary for review/main agents."""

        normalized_issue_path = str(issue.file_path or "").replace("\\", "/").lstrip("/")
        target_change = next(
            (change for change in reviewed_changes if str(change.file or "").replace("\\", "/").lstrip("/") == normalized_issue_path),
            None,
        )
        target_method_name, target_window = ClaudeFixAgent._resolve_target_method_window(
            issue=issue,
            edit_contract=edit_contract,
            file_content=current_issue_file_content,
        )
        changed_files = tuple(
            dict.fromkeys(
                str(change.file or "").replace("\\", "/").lstrip("/")
                for change in reviewed_changes
                if str(change.file or "").strip()
            )
        )
        changed_methods = ClaudeFixAgent._collect_changed_method_names(
            target_change=target_change,
            current_file_content=current_issue_file_content,
        )
        touched_target_method = ClaudeFixAgent._target_method_was_touched(
            target_change=target_change,
            target_method_name=target_method_name,
            target_window=target_window,
        )
        helper_added = ClaudeFixAgent._patch_added_private_helper(reviewed_changes)
        scope = ClaudeFixAgent._classify_patch_scope(
            normalized_issue_path=normalized_issue_path,
            changed_files=changed_files,
            changed_methods=changed_methods,
            target_method_name=target_method_name,
            touched_target_method=touched_target_method,
        )
        risk_flags: list[str] = []
        if not touched_target_method:
            risk_flags.append("target_method_not_touched")
        if helper_added:
            risk_flags.append("helper_added")
        if any(item for item in changed_methods if item and item != target_method_name):
            risk_flags.append("sibling_method_touched")
        if any(path for path in changed_files if path != normalized_issue_path):
            risk_flags.append("cross_file_change")
        preview = ClaudeFixAgent._build_target_patch_preview(
            target_change=target_change,
            target_window=target_window,
        )

        sections = [
            f"target_file={normalized_issue_path or 'unknown'}",
            f"target_method={target_method_name or 'unknown'}",
            f"touched_target_method={'yes' if touched_target_method else 'no'}",
        ]
        if changed_files:
            sections.append("changed_files=" + ", ".join(changed_files[:4]))
        if changed_methods:
            sections.append("changed_methods=" + ", ".join(changed_methods[:6]))
        sections.append(f"scope={scope}")
        if risk_flags:
            sections.append("risk_flags=" + ", ".join(dict.fromkeys(risk_flags)))
        if preview:
            sections.append("target_preview=" + preview)
        return "\n".join(section for section in sections if section).strip()

    @staticmethod
    def _resolve_target_method_window(
        *,
        issue: SonarIssue,
        edit_contract: Any,
        file_content: str,
    ) -> tuple[str, Any | None]:
        repair_plan = getattr(edit_contract, "repair_plan", None)
        primary_method_name = str(getattr(repair_plan, "primary_method_name", "") or "").strip()
        lines = str(file_content or "").splitlines()
        target_line = int(getattr(issue, "start_line", 0) or getattr(issue, "line", 0) or 0)
        window = None
        if lines and target_line > 0:
            window = QualityGateVerifier._find_enclosing_method(lines, target_line)
            if window is None:
                total_lines = len(lines)
                start = max(1, target_line - 5)
                end = min(total_lines, target_line + 5)
                for candidate_line in range(start, end + 1):
                    window = QualityGateVerifier._build_method_window(lines, candidate_line)
                    if window is not None:
                        break
        method_name = primary_method_name or str(getattr(window, "name", "") or "").strip()
        return method_name, window

    @staticmethod
    def _collect_changed_method_names(
        *,
        target_change: ReviewedFileChange | None,
        current_file_content: str,
    ) -> tuple[str, ...]:
        if target_change is None or not str(current_file_content or "").strip():
            return ()
        lines = str(current_file_content or "").splitlines()
        candidate_lines = sorted(
            {
                int(line)
                for line in (
                    *(target_change.after_changed_lines or ()),
                    *(
                        int(getattr(operation, "after_line", 0) or 0)
                        for operation in (target_change.line_operations or ())
                    ),
                )
                if int(line) > 0
            }
        )
        method_names: list[str] = []
        for line_number in candidate_lines:
            if line_number <= 0 or line_number > len(lines):
                continue
            method = QualityGateVerifier._find_enclosing_method(lines, line_number)
            if method is None:
                method = QualityGateVerifier._build_method_window(lines, line_number)
            name = str(getattr(method, "name", "") or "").strip()
            if name and name not in method_names:
                method_names.append(name)
        return tuple(method_names)

    @staticmethod
    def _target_method_was_touched(
        *,
        target_change: ReviewedFileChange | None,
        target_method_name: str,
        target_window: Any | None,
    ) -> bool:
        if target_change is None:
            return False
        normalized_target_method = str(target_method_name or "").strip()
        if normalized_target_method:
            for line_number in target_change.after_changed_lines or ():
                if not int(line_number or 0):
                    continue
                method = None
                # method lookup requires current file content and is handled earlier via changed_methods;
                # fall back to target window intersection below when the summary cannot resolve by name.
            for operation in target_change.line_operations or ():
                if normalized_target_method and normalized_target_method in str(getattr(operation, "text", "") or ""):
                    return True
        if target_window is None:
            return False
        start_line = int(getattr(target_window, "start_line", 0) or 0)
        end_line = int(getattr(target_window, "end_line", 0) or 0)
        if start_line <= 0 or end_line <= 0:
            return False
        for line_number in target_change.after_changed_lines or ():
            if start_line <= int(line_number or 0) <= end_line:
                return True
        for operation in target_change.line_operations or ():
            after_line = int(getattr(operation, "after_line", 0) or 0)
            before_line = int(getattr(operation, "before_line", 0) or 0)
            if (after_line and start_line <= after_line <= end_line) or (
                before_line and start_line <= before_line <= end_line
            ):
                return True
        return False

    @staticmethod
    def _patch_added_private_helper(
        reviewed_changes: tuple[ReviewedFileChange, ...],
    ) -> bool:
        for change in reviewed_changes:
            for operation in change.line_operations or ():
                if str(getattr(operation, "kind", "")).strip() != "add":
                    continue
                if DiffReviewer._is_private_method_declaration(str(getattr(operation, "text", "") or "")):
                    return True
        return False

    @staticmethod
    def _classify_patch_scope(
        *,
        normalized_issue_path: str,
        changed_files: tuple[str, ...],
        changed_methods: tuple[str, ...],
        target_method_name: str,
        touched_target_method: bool,
    ) -> str:
        if not changed_files:
            return "no_change"
        if any(path for path in changed_files if path != normalized_issue_path):
            return "cross_file"
        normalized_target_method = str(target_method_name or "").strip()
        if changed_methods and normalized_target_method:
            if all(name == normalized_target_method for name in changed_methods):
                return "target_method_only"
            return "target_file_expanded"
        if touched_target_method:
            return "target_method_only"
        return "target_file_unclear"

    @staticmethod
    def _build_target_patch_preview(
        *,
        target_change: ReviewedFileChange | None,
        target_window: Any | None,
    ) -> str:
        if target_change is None:
            return ""
        preview_lines: list[str] = []
        start_line = int(getattr(target_window, "start_line", 0) or 0)
        end_line = int(getattr(target_window, "end_line", 0) or 0)
        for operation in target_change.line_operations or ():
            text = " ".join(str(getattr(operation, "text", "") or "").split())
            if not text:
                continue
            before_line = int(getattr(operation, "before_line", 0) or 0)
            after_line = int(getattr(operation, "after_line", 0) or 0)
            in_target_window = bool(
                start_line and end_line and (
                    (after_line and start_line <= after_line <= end_line)
                    or (before_line and start_line <= before_line <= end_line)
                )
            )
            if start_line and end_line and not in_target_window:
                continue
            marker = "+" if str(getattr(operation, "kind", "")).strip() == "add" else "-"
            preview_lines.append(f"{marker} {text[:140]}")
            if len(preview_lines) >= 6:
                break
        if preview_lines:
            return " | ".join(preview_lines)
        for raw_line in str(target_change.diff_text or "").splitlines():
            stripped = raw_line.rstrip()
            if stripped.startswith(("+++", "---", "@@")):
                continue
            if stripped.startswith(("+", "-")):
                preview_lines.append(stripped[:140])
            if len(preview_lines) >= 6:
                break
        return " | ".join(preview_lines)

    @staticmethod
    def _extract_json_payload(raw_text: str) -> dict[str, Any]:
        text = str(raw_text or "").strip()
        if not text:
            return {}
        candidates = [text]
        if "```json" in text:
            start = text.find("```json") + len("```json")
            end = text.find("```", start)
            if end > start:
                candidates.append(text[start:end].strip())
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidates.append(text[first_brace : last_brace + 1].strip())
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return {}

    @classmethod
    def _parse_role_decision(
        cls,
        *,
        raw_text: str,
        allowed_decisions: tuple[str, ...],
        fallback_decision: str,
        fallback_summary: str,
    ) -> RoleDecision:
        payload = cls._extract_json_payload(raw_text)
        decision = str(payload.get("decision", "")).strip().lower()
        if decision not in set(allowed_decisions):
            decision = fallback_decision
        findings = tuple(
            str(item).strip()
            for item in payload.get("findings", ()) or ()
            if str(item).strip()
        )
        constraints = tuple(
            str(item).strip()
            for item in payload.get("constraints", ()) or ()
            if str(item).strip()
        )
        summary = str(payload.get("summary", "")).strip() or fallback_summary
        return RoleDecision(
            decision=decision,
            summary=summary,
            findings=findings,
            constraints=constraints,
            raw_text=str(raw_text or "").strip(),
        )

    @staticmethod
    def _extract_actionable_plaintext_lines(raw_text: str, *, max_items: int = 3) -> tuple[str, ...]:
        lines: list[str] = []
        for raw_line in str(raw_text or "").splitlines():
            line = str(raw_line or "").strip()
            if not line or line.startswith("```"):
                continue
            line = re.sub(r"^[\-\*\d\.\)\s]+", "", line).strip()
            line = line.strip('",')
            if not line:
                continue
            lowered = line.lower()
            if lowered in {"approve", "retry", "compile"}:
                continue
            if lowered.endswith("{") or lowered.endswith("}") or lowered in {
                "decision",
                "summary",
                "findings",
                "constraints",
            }:
                continue
            if line not in lines:
                lines.append(line)
            if len(lines) >= max_items:
                break
        return tuple(lines)

    @classmethod
    def _build_review_retry_constraints(
        cls,
        *,
        issue: SonarIssue,
        raw_text: str,
        patch_summary: str,
        fallback_constraints: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        constraints: list[str] = [
            "先 Read 当前目标文件和目标方法，确认当前代码状态，再提交更小、更直接的 patch。",
            "只修改已有文件；不要创建、删除、移动或重命名文件。",
        ]
        normalized = " ".join(str(raw_text or "").split()).lower()
        if patch_summary:
            constraints.append("先核对 patch 摘要与当前代码是否一致；如果不一致，以当前文件内容为准重新编辑。")
        if any(marker in normalized for marker in ("mismatch", "不一致", "摘要", "patch")):
            constraints.append("重新读取已修改文件，确保下一轮修改真正落在当前目标方法，而不是停留在过期 patch 上。")
        if issue.rule == "csharpsquid:S3776" or any(marker in normalized for marker in ("复杂度", "认知复杂度")):
            constraints.append("继续围绕目标方法本体降复杂度，优先扁平化条件、早返回和局部重排，不要只改周边调用。")
        if any(marker in normalized for marker in ("build", "编译", "cs0", "cs1", "cs2", "cs3", "cs4")):
            constraints.append("如果上一轮已经暴露编译错误，本轮先消掉这些编译错误，再继续 issue 修复。")
        if any(marker in normalized for marker in ("helper", "private method", "private helper")):
            constraints.append("如果 review 指出 helper 路线不稳，下一轮回到现有方法体内收口，不要继续提取新 helper。")
        for item in fallback_constraints:
            text = str(item).strip()
            if text and text not in constraints:
                constraints.append(text)
        return tuple(dict.fromkeys(item for item in constraints if item))[:3]

    @staticmethod
    def _parse_patch_summary_facts(patch_summary: str) -> dict[str, str]:
        facts: dict[str, str] = {}
        for raw_line in str(patch_summary or "").splitlines():
            line = str(raw_line or "").strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = str(key or "").strip().lower()
            normalized_value = str(value or "").strip()
            if normalized_key and normalized_value:
                facts[normalized_key] = normalized_value
        return facts

    @classmethod
    def _review_retry_is_missing_s3776_proof(
        cls,
        *,
        issue: SonarIssue,
        decision: RoleDecision,
        patch_summary: str,
    ) -> bool:
        if str(getattr(issue, "rule", "") or "").strip() != "csharpsquid:S3776":
            return False
        combined = "\n".join(
            part
            for part in (
                str(decision.summary or "").strip(),
                str(decision.raw_text or "").strip(),
                *tuple(str(item).strip() for item in (decision.findings or ()) if str(item).strip()),
                *tuple(str(item).strip() for item in (decision.constraints or ()) if str(item).strip()),
            )
            if part
        )
        normalized = " ".join(combined.split()).lower()
        proof_markers = (
            "复杂度数值",
            "认知复杂度度量值",
            "修复前32",
            "修复前后",
            "≤30",
            "<=30",
            "无法确认",
            "无法验证",
            "完整方法",
            "完整代码",
            "target_preview",
            "patch 摘要未提供",
            "provide",
            "proof",
        )
        if not any(marker.lower() in normalized for marker in proof_markers):
            return False
        hard_risk_markers = (
            "语法错误",
            "编译错误",
            "cs0",
            "cs1",
            "cs2",
            "cs3",
            "cs4",
            "类型风险",
            "签名",
            "scope",
            "作用域",
            "nullable",
            "匿名类型",
            "dynamic",
            "不兼容",
            "类型不匹配",
            "无法编译",
            "hard blocker",
        )
        if any(marker.lower() in normalized for marker in hard_risk_markers):
            return False
        facts = cls._parse_patch_summary_facts(patch_summary)
        if facts.get("touched_target_method", "").strip().lower() != "yes":
            return False
        scope = str(facts.get("scope", "") or "").strip().lower()
        if scope == "cross_file":
            return False
        return True

    @classmethod
    def _review_retry_is_non_patch_flow_control(
        cls,
        *,
        decision: RoleDecision,
        patch_summary: str,
    ) -> bool:
        combined = "\n".join(
            part
            for part in (
                str(decision.summary or "").strip(),
                str(decision.raw_text or "").strip(),
                *tuple(str(item).strip() for item in (decision.findings or ()) if str(item).strip()),
                *tuple(str(item).strip() for item in (decision.constraints or ()) if str(item).strip()),
            )
            if part
        )
        normalized = " ".join(combined.split()).lower()
        flow_control_markers = (
            "build=fail",
            "issue_baseline",
            "baseline",
            "无实质 patch",
            "无 patch 需要审查",
            "当前工作区已回滚至 baseline",
            "上一轮 patch 已撤销",
            "确认当前 baseline 代码的完整性",
            "baseline 代码问题",
            "baseline 问题",
            "前次 review 摘要显示已同意进入编译",
            "nu1301",
            "nuget.org",
            "nuget.azure.cn",
            "无法加载源",
            "不知道这样的主机",
        )
        if not any(marker in normalized for marker in flow_control_markers):
            return False
        hard_risk_markers = (
            "语法错误",
            "类型错误",
            "类型风险",
            "签名",
            "nullable",
            "匿名类型",
            "dynamic",
            "不兼容",
            "类型不匹配",
            "cs0",
            "cs1",
            "cs2",
            "cs3",
            "cs4",
        )
        if any(marker in normalized for marker in hard_risk_markers):
            return False
        facts = cls._parse_patch_summary_facts(patch_summary)
        if facts.get("touched_target_method", "").strip().lower() != "yes":
            return False
        scope = str(facts.get("scope", "") or "").strip().lower()
        if scope == "cross_file":
            return False
        return True

    @classmethod
    def _stabilize_review_decision(
        cls,
        *,
        issue: SonarIssue,
        patch_summary: str,
        decision: RoleDecision,
    ) -> RoleDecision:
        findings = decision.findings or cls._extract_actionable_plaintext_lines(
            decision.raw_text,
            max_items=3,
        )
        if (
            decision.decision == "retry"
            and cls._review_retry_is_missing_s3776_proof(
                issue=issue,
                decision=decision,
                patch_summary=patch_summary,
            )
        ):
            return RoleDecision(
                decision="approve",
                summary="Review 子Agent未发现编译前硬风险；S3776 是否 <=30 将在编译后的 post-check 再确认。",
                findings=tuple(
                    item
                    for item in findings
                    if not any(
                        marker in str(item or "").lower()
                        for marker in (
                            "复杂度数值",
                            "认知复杂度度量值",
                            "完整方法",
                            "完整代码",
                            "target_preview",
                            "修复前",
                            "修复后",
                            "≤30",
                            "<=30",
                        )
                    )
                ),
                constraints=(),
                raw_text=decision.raw_text,
            )
        if (
            decision.decision == "retry"
            and cls._review_retry_is_non_patch_flow_control(
                decision=decision,
                patch_summary=patch_summary,
            )
        ):
            return RoleDecision(
                decision="approve",
                summary="Review 子Agent未发现当前 patch 的编译前硬风险；上一轮外部构建/回滚状态不作为当前 patch 的拒绝理由。",
                findings=tuple(
                    item
                    for item in findings
                    if not any(
                        marker in str(item or "").lower()
                        for marker in (
                            "build=fail",
                            "baseline",
                            "issue_baseline",
                            "回滚",
                            "无 patch",
                            "无实质 patch",
                            "nu1301",
                            "nuget.org",
                            "nuget.azure.cn",
                            "无法加载源",
                        )
                    )
                ),
                constraints=(),
                raw_text=decision.raw_text,
            )
        constraints = decision.constraints
        if decision.decision == "retry" and not constraints:
            constraints = cls._build_review_retry_constraints(
                issue=issue,
                raw_text=decision.raw_text or decision.summary,
                patch_summary=patch_summary,
                fallback_constraints=findings,
            )
        summary = str(decision.summary or "").strip()
        if not summary or summary == "Review 子Agent 未给出可用结论。":
            summary = (
                "Review 子Agent认为当前 patch 可以进入编译。"
                if decision.decision == "approve"
                else "Review 子Agent要求继续修复，并已生成下一轮可执行约束。"
            )
        return RoleDecision(
            decision=decision.decision,
            summary=summary,
            findings=findings,
            constraints=constraints,
            raw_text=decision.raw_text,
        )

    @classmethod
    def _stabilize_main_decision(
        cls,
        *,
        review_decision: RoleDecision,
        decision: RoleDecision,
    ) -> RoleDecision:
        summary = str(decision.summary or "").strip()
        constraints = decision.constraints or review_decision.constraints
        if (
            decision.decision == "retry"
            and review_decision.decision == "approve"
            and summary == "Main 裁决未批准进入编译阶段。"
        ):
            return RoleDecision(
                decision="compile",
                summary=review_decision.summary or "Review 已批准，主裁决回退为进入编译阶段。",
                findings=review_decision.findings,
                constraints=(),
                raw_text=decision.raw_text,
            )
        if not summary or summary == "Main 裁决未批准进入编译阶段。":
            summary = (
                "Main 裁决允许进入编译阶段。"
                if decision.decision == "compile"
                else "Main 裁决要求继续修复，并保留了下一轮约束。"
            )
        return RoleDecision(
            decision=decision.decision,
            summary=summary,
            findings=decision.findings,
            constraints=constraints,
            raw_text=decision.raw_text,
        )

    @classmethod
    async def _run_prompt_only_role_session_async(
        cls,
        role: str,
        workspace_path: Path,
        system_prompt: str,
        user_prompt: str,
        max_turns: int,
        agent_env: dict[str, str] | None,
        explicit_model: str | None,
    ) -> RoleAgentRunResult:
        """Run a prompt-only child-agent session and capture raw text output."""

        gateway = ClaudeAdapter(cls._sdk_dependencies())
        request = ClaudeAdapter.build_request(
            agent_env=dict(agent_env or read_project_env()),
            explicit_model=explicit_model,
            cwd=str(workspace_path),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=(),
            allowed_tools=(),
            max_turns=max_turns,
            max_budget_usd=2.0,
            stderr_handler=cls._handle_cli_stderr,
            build_command="",
            mcp_servers={},
        )
        session = gateway.create_session(request)
        texts: list[str] = []
        tool_uses: list[str] = []
        total_cost = 0.0
        agent_error = ""
        start_at = time.monotonic()
        connected = False
        try:
            await session.connect(timeout_seconds=CLIENT_CONNECT_TIMEOUT_SECONDS)
            connected = True
            await session.send(request.user_prompt)
            stream = session.stream_events()
            while True:
                remaining = max(10.0, 180.0 - (time.monotonic() - start_at))
                try:
                    event = await asyncio.wait_for(anext(stream), timeout=remaining)
                except StopAsyncIteration:
                    break
                if isinstance(event, TextEvent):
                    texts.append(str(event.text or ""))
                elif isinstance(event, ToolCallEvent):
                    tool_uses.append(str(getattr(event, "name", "") or "").strip())
                elif isinstance(event, ResultEvent):
                    total_cost = float(getattr(event, "total_cost_usd", 0.0) or 0.0)
                    agent_error = str(getattr(event, "agent_error", "") or "").strip()
                elif isinstance(event, TraceEvent):
                    continue
        except Exception as exc:
            agent_error = str(exc)
        finally:
            if connected:
                try:
                    await session.close()
                except Exception:
                    pass
        return RoleAgentRunResult(
            role=role,
            response_text="".join(texts).strip(),
            total_cost_usd=total_cost,
            agent_error=agent_error or None,
            tool_uses=tuple(dict.fromkeys(item for item in tool_uses if item)),
        )

    @classmethod
    def _run_prompt_only_role_session(
        cls,
        *,
        role: str,
        workspace_path: Path,
        system_prompt: str,
        user_prompt: str,
        max_turns: int = 4,
        agent_env: dict[str, str] | None = None,
        explicit_model: str | None = None,
    ) -> RoleAgentRunResult:
        return anyio.run(
            cls._run_prompt_only_role_session_async,
            role,
            workspace_path,
            system_prompt,
            user_prompt,
            max_turns,
            agent_env,
            explicit_model,
        )

    @classmethod
    def _load_child_memory(
        cls,
        *,
        store: WorkingMemoryStore,
        issue_key: str,
        role: str,
        focus: str,
    ) -> ChildAgentMemory:
        existing = store.load_child_memory(issue_key, role)
        if existing is not None:
            return existing
        memory = create_initial_child_agent_memory(issue_key=issue_key, role=role, focus=focus)
        store.save_child_memory(memory)
        return memory

    @classmethod
    def _run_role_orchestrated_flow(
        cls,
        *,
        agent: "ClaudeFixAgent",
        issue: SonarIssue,
        workspace_path: Path,
        build_command: str,
        code_context: str,
        rule_details: dict[str, str],
        scope: IssueEditScope | None,
        original_issue_file_content: str | None,
        retry_feedback: str,
        working_memory: IssueWorkingMemory | None,
        edit_contract: Any,
        guardrail_mode: str,
        visible_toolset: Any,
        tool_policy: ToolPolicy,
        sonar_mcp_runtime: Any,
        result_metadata: dict[str, Any],
        execution_schedule: Any,
        runtime_builtin_tools: tuple[str, ...],
    ) -> FixResult:
        """Run the new main -> fix -> review -> compile orchestration flow."""

        store = WorkingMemoryStore(workspace_path)
        attempt_todo_store = AttemptTodoStore(workspace_path, issue.key, role="fix")
        attempt_todo_runtime = build_attempt_todo_runtime(
            attempt_todo_store,
            agent_env=agent.agent_env,
        )
        attempt_todo_state = (
            attempt_todo_store.reset() if attempt_todo_runtime.enabled else None
        )
        fix_memory = cls._load_child_memory(
            store=store,
            issue_key=issue.key,
            role="fix",
            focus="直接修改代码，完成当前 issue 的最小修复。",
        )
        review_memory = cls._load_child_memory(
            store=store,
            issue_key=issue.key,
            role="review",
            focus="审查当前 patch 是否符合 C# 质量门禁和 issue 修复目标。",
        )
        main_memory = cls._load_child_memory(
            store=store,
            issue_key=issue.key,
            role="main",
            focus="决定当前 patch 是否进入编译阶段。",
        )

        def current_result_metadata() -> dict[str, Any]:
            payload = dict(result_metadata)
            if attempt_todo_runtime.enabled:
                try:
                    payload["attempt_todo_state"] = attempt_todo_store.load()
                except Exception:
                    payload["attempt_todo_state"] = None
            return payload

        system_prompt = build_fix_role_system_prompt(
            todo_write_tool_name=attempt_todo_runtime.visible_tool_name,
        )
        file_path_candidates = IssuePromptBuilder.build_workspace_relative_candidates(
            issue.file_path,
            workspace_path,
        )
        user_prompt = build_fix_role_user_prompt(
            issue=issue,
            code_context=code_context,
            file_path_candidates=file_path_candidates,
            planner_lessons=tuple(getattr(edit_contract, "planner_lessons", ()) or ()),
            working_memory=working_memory,
            attempt_todo_state=attempt_todo_state,
            todo_write_tool_name=attempt_todo_runtime.visible_tool_name,
            fix_memory=fix_memory,
            retry_feedback=retry_feedback,
        )
        gateway_request = ClaudeAdapter.build_request(
            agent_env=agent.agent_env,
            explicit_model=agent.model,
            cwd=str(workspace_path),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=runtime_builtin_tools,
            allowed_tools=tool_policy.allowed_tool_names(),
            max_turns=int(getattr(execution_schedule, "effective_max_turns", 8) or 8),
            max_budget_usd=agent.max_budget_usd,
            stderr_handler=agent._handle_cli_stderr,
            build_command=build_command,
            mcp_servers={
                **dict(getattr(sonar_mcp_runtime, "server_configs", {}) or {}),
                **attempt_todo_runtime.server_configs,
            },
        )
        gateway_request.metadata.update(
            {
                "issue_key": issue.key,
                "role": "fix",
                "helper_extract_runtime_guard": "false",
                "mcp_servers": ",".join(
                    sorted(tuple(getattr(sonar_mcp_runtime, "server_configs", {}) or {}))
                ),
                "mcp_tools_count": str(
                    len(tuple(getattr(sonar_mcp_runtime, "tool_names", ()) or ()))
                ),
                "mcp_mode": str(getattr(sonar_mcp_runtime, "mode", "") or ""),
                "mcp_read_only": (
                    "true" if bool(getattr(sonar_mcp_runtime, "read_only", False)) else "false"
                ),
                "mcp_warning": str(getattr(sonar_mcp_runtime, "warning", "") or ""),
                "visible_tools": ",".join(tuple(getattr(visible_toolset, "visible_tools", ()) or ())),
                "todo_write_tool_name": attempt_todo_runtime.visible_tool_name,
                "todo_write_display_name": attempt_todo_runtime.display_name,
                "todo_write_nag_threshold": str(attempt_todo_runtime.nag_threshold),
                "todo_write_max_reminders": str(attempt_todo_runtime.max_reminders),
                "todo_role": "fix",
            }
        )
        runtime = AgentRuntime(
            gateway=ClaudeAdapter(cls._sdk_dependencies()),
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
        runtime_result = AgentRuntimeResult()
        runtime_metrics: dict[str, Any] = {}
        attempt_events: list[AttemptRuntimeEvent] = []
        patch_salvaged = False
        model_timeout_stage = ""
        invalid_write_tool_input = ""
        cls._capture_attempt_workspace_state(workspace_path)
        try:
            try:
                runtime_result = agent._run_runtime_with_continuation(
                    runtime=runtime,
                    gateway_request=gateway_request,
                    execution_schedule=execution_schedule,
                    workspace_path=workspace_path,
                )
                runtime_metrics = agent._build_runtime_performance_metrics(runtime_result)
                attempt_events = list(getattr(runtime_result, "runtime_events", ()) or ())
            except AgentRuntimeError as exc:
                runtime_result = exc.partial_result or AgentRuntimeResult()
                runtime_metrics = agent._build_runtime_performance_metrics(runtime_result)
                attempt_events = list(getattr(runtime_result, "runtime_events", ()) or ())
                error_details = cls._format_exception_details(exc.cause) or str(exc.cause)
                changed_files = tuple(dict.fromkeys(agent._collect_modified_files(workspace_path)))
                changes = [{"file": changed_file, "action": "modified"} for changed_file in changed_files]
                model_timeout = (
                    isinstance(exc.cause, TimeoutError)
                    or "没有返回首个响应" in error_details
                    or "没有返回后续响应" in error_details
                    or "单个 issue 在" in error_details
                    or "未完成初始化" in error_details
                )
                model_timeout_stage = (
                    str(runtime_metrics.get("model_timeout_stage", "")).strip()
                    or cls._infer_timeout_stage(error_details)
                )
                invalid_write_tool_input = cls._extract_invalid_write_tool_input_message(attempt_events)
                attempt_head_changed = cls._attempt_head_changed(workspace_path)
                used_forbidden_tool = bool(runtime_result.forbidden_tool_uses) or attempt_head_changed
                build_tool_failed = (
                    runtime_result.last_tool_name == "mcp__sonar-fix__run_build"
                    or (runtime_result.saw_build_tool and "exit code" in error_details.lower())
                )
                if model_timeout and AttemptScheduler.should_salvage_timeout(
                    schedule=execution_schedule,
                    changes_detected=bool(changes),
                    used_forbidden_tool=used_forbidden_tool,
                    build_tool_failed=build_tool_failed,
                ):
                    patch_salvaged = True
                    runtime_metrics = cls._merge_performance_metrics(
                        runtime_metrics,
                        patch_salvaged=True,
                        model_timeout_stage=model_timeout_stage,
                    )
                elif model_timeout:
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        error="Model response timed out",
                        summary="Fix 子Agent 执行超时。",
                        build_command=build_command,
                        build_output=error_details,
                        retryable_failure=True,
                        failure_kind="model_timeout",
                        performance_metrics=cls._merge_performance_metrics(
                            runtime_metrics,
                            patch_salvaged=False,
                            model_timeout_stage=model_timeout_stage,
                            build_invoked=False,
                            build_duration_seconds=0.0,
                        ),
                        model_timeout_stage=model_timeout_stage,
                        patch_salvaged=False,
                        attempt_events=tuple(attempt_events),
                        **current_result_metadata(),
                    )
                if used_forbidden_tool:
                    fallback_build_passed, fallback_build_output = agent._run_local_build_fallback(
                        workspace_path,
                        build_command,
                    )
                    output_parts = ["修复阶段使用了被禁止的工具，当前尝试已作废。"]
                    if runtime_result.forbidden_tool_uses:
                        output_parts.append(
                            "禁止工具: " + ", ".join(dict.fromkeys(runtime_result.forbidden_tool_uses))
                        )
                    if attempt_head_changed:
                        output_parts.append("检测到当前 attempt 改写了 Git HEAD/提交历史。")
                    if error_details:
                        output_parts.append(error_details)
                    if fallback_build_output:
                        output_parts.append(fallback_build_output)
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=fallback_build_passed,
                        build_verification_failed=not fallback_build_passed,
                        error="Forbidden tool used during issue fix",
                        summary="Fix 子Agent 使用了被禁止的工具，当前 attempt 已作废。",
                        build_command=build_command,
                        build_output="\n\n".join(part for part in output_parts if part),
                        retryable_failure=True,
                        failure_kind="forbidden_tool",
                        performance_metrics=cls._merge_performance_metrics(
                            runtime_metrics,
                            build_invoked=False,
                            build_duration_seconds=0.0,
                        ),
                        attempt_events=tuple(attempt_events),
                        **current_result_metadata(),
                    )
                if build_tool_failed:
                    fallback_build_passed, fallback_build_output = agent._run_local_build_fallback(
                        workspace_path,
                        build_command,
                    )
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=fallback_build_passed,
                        build_verification_failed=True,
                        error="Build tool execution failed",
                        summary="Fix 子Agent 调用了失败的 build 工具。",
                        build_command=build_command,
                        build_output="\n\n".join(
                            part for part in ("run_build 工具执行异常。", error_details, fallback_build_output) if part
                        ),
                        retryable_failure=True,
                        failure_kind="build_tool",
                        performance_metrics=cls._merge_performance_metrics(
                            runtime_metrics,
                            build_invoked=False,
                            build_duration_seconds=0.0,
                        ),
                        attempt_events=tuple(attempt_events),
                        **current_result_metadata(),
                    )
                if not patch_salvaged:
                    failure_kind, summary, child_summary, build_output = cls._classify_fix_role_failure(
                        agent_error=error_details,
                        attempt_events=attempt_events,
                    )
                    fix_memory = append_child_agent_memory_turn(
                        fix_memory,
                        attempt_number=len(fix_memory.turns) + 1,
                        decision="retry",
                        summary=child_summary,
                        workspace_state="issue_baseline",
                        next_action=(
                            "先 Read 当前目标文件，再用完整参数重新提交更精确的 patch。"
                            if failure_kind == "tool_input_invalid"
                            else "重新读取问题文件，改用更小的修法。"
                        ),
                    )
                    store.save_child_memory(fix_memory)
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        error=error_details,
                        summary=summary,
                        build_command=build_command,
                        build_output=build_output,
                        retryable_failure=True,
                        failure_kind=failure_kind,
                        performance_metrics=runtime_metrics,
                        attempt_events=tuple(attempt_events),
                        **current_result_metadata(),
                    )
            except Exception as exc:
                error_details = cls._format_exception_details(exc) or str(exc)
                changed_files = tuple(dict.fromkeys(agent._collect_modified_files(workspace_path)))
                changes = [{"file": changed_file, "action": "modified"} for changed_file in changed_files]
                model_timeout_stage = cls._infer_timeout_stage(error_details)
                if model_timeout_stage:
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        error="Model response timed out",
                        summary="Fix 子Agent 执行超时。",
                        build_command=build_command,
                        build_output=error_details,
                        retryable_failure=True,
                        failure_kind="model_timeout",
                        performance_metrics=cls._merge_performance_metrics(
                            runtime_metrics,
                            patch_salvaged=False,
                            model_timeout_stage=model_timeout_stage,
                            build_invoked=False,
                            build_duration_seconds=0.0,
                        ),
                        model_timeout_stage=model_timeout_stage,
                        patch_salvaged=False,
                        attempt_events=tuple(attempt_events),
                        **current_result_metadata(),
                    )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=issue.file_path,
                    changes=changes,
                    error=error_details,
                    summary="Fix 子Agent 执行异常。",
                    build_command=build_command,
                    build_output=error_details,
                    retryable_failure=True,
                    failure_kind="fix_agent",
                    performance_metrics=cls._merge_performance_metrics(
                        runtime_metrics,
                        build_invoked=False,
                        build_duration_seconds=0.0,
                    ),
                    attempt_events=tuple(attempt_events),
                    **current_result_metadata(),
                )

            invalid_write_tool_input = invalid_write_tool_input or cls._extract_invalid_write_tool_input_message(
                attempt_events
            )
            changed_files = tuple(dict.fromkeys(agent._collect_modified_files(workspace_path)))
            changes = [{"file": changed_file, "action": "modified"} for changed_file in changed_files]
            if (
                not changes
                and not patch_salvaged
                and not model_timeout_stage
                and not runtime_result.agent_error
                and int(getattr(runtime_result, "continuation_retry_count", 0) or 0) == 0
                and bool(getattr(execution_schedule, "continuation_retry_enabled", False))
                and not invalid_write_tool_input
            ):
                runtime_result = cls._run_no_change_continuation(
                    runtime=runtime,
                    gateway_request=gateway_request,
                    initial_result=runtime_result,
                    workspace_path=workspace_path,
                )
                attempt_events = list(getattr(runtime_result, "runtime_events", ()) or ())
                runtime_metrics = cls._merge_performance_metrics(
                    cls._build_runtime_performance_metrics(runtime_result),
                    patch_salvaged=patch_salvaged,
                    model_timeout_stage=model_timeout_stage,
                )
                invalid_write_tool_input = cls._extract_invalid_write_tool_input_message(attempt_events)
                changed_files = tuple(dict.fromkeys(agent._collect_modified_files(workspace_path)))
                changes = [{"file": changed_file, "action": "modified"} for changed_file in changed_files]

            attempt_head_changed = cls._attempt_head_changed(workspace_path)
            used_forbidden_tool = bool(runtime_result.forbidden_tool_uses) or attempt_head_changed
            if used_forbidden_tool:
                fallback_build_passed, fallback_build_output = agent._run_local_build_fallback(
                    workspace_path,
                    build_command,
                )
                output_parts = ["修复阶段使用了被禁止的工具，当前尝试已作废。"]
                if runtime_result.forbidden_tool_uses:
                    output_parts.append(
                        "禁止工具: " + ", ".join(dict.fromkeys(runtime_result.forbidden_tool_uses))
                    )
                if attempt_head_changed:
                    output_parts.append("检测到当前 attempt 改写了 Git HEAD/提交历史。")
                if fallback_build_output:
                    output_parts.append(fallback_build_output)
                fix_memory = append_child_agent_memory_turn(
                    fix_memory,
                    attempt_number=len(fix_memory.turns) + 1,
                    decision="retry",
                    summary="Fix 子Agent 使用了被禁止的工具。",
                    workspace_state="issue_baseline",
                    next_action="重新读取问题文件，改用允许的 Read/Edit/Write 工具完成最小修复。",
                )
                store.save_child_memory(fix_memory)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=issue.file_path,
                    changes=changes,
                    build_passed=fallback_build_passed,
                    build_verification_failed=not fallback_build_passed,
                    error="Forbidden tool used during issue fix",
                    summary="Fix 子Agent 使用了被禁止的工具，当前 attempt 已作废。",
                    build_command=build_command,
                    build_output="\n\n".join(part for part in output_parts if part),
                    retryable_failure=True,
                    failure_kind="forbidden_tool",
                    performance_metrics=runtime_metrics,
                    attempt_events=tuple(attempt_events),
                    **current_result_metadata(),
                )
            if (
                runtime_result.agent_error
                and not patch_salvaged
                and cls._should_salvage_agent_error(
                    agent_error=runtime_result.agent_error,
                    changes_detected=bool(changes),
                    used_forbidden_tool=used_forbidden_tool,
                    invalid_write_tool_input=invalid_write_tool_input,
                )
            ):
                patch_salvaged = True
                salvage_reason = (
                    "agent_error_max_turns"
                    if cls._is_max_turn_agent_error(runtime_result.agent_error)
                    else "agent_error_invalid_write_burst"
                )
                runtime_metrics = cls._merge_performance_metrics(
                    runtime_metrics,
                    patch_salvaged=True,
                    agent_error_salvaged=True,
                    agent_error_salvage_reason=salvage_reason,
                )
            if runtime_result.agent_error and not patch_salvaged:
                if invalid_write_tool_input and not changes:
                    runtime_result = replace(runtime_result, agent_error=None)
                else:
                    runtime_contract = cls._classify_runtime_contract_agent_error(runtime_result.agent_error)
                    if runtime_contract is not None:
                        boundary_code, boundary_summary = runtime_contract
                        return FixResult(
                            success=False,
                            issue_key=issue.key,
                            file_path=issue.file_path,
                            changes=changes,
                            error=runtime_result.agent_error,
                            summary="Runtime contract violation requires a narrower retry",
                            build_command=build_command,
                            build_output=runtime_result.agent_error,
                            retryable_failure=True,
                            failure_kind="runtime_contract_violation",
                            boundary_failure_code=boundary_code,
                            boundary_failure_summary=boundary_summary,
                            performance_metrics=cls._merge_performance_metrics(
                                runtime_metrics,
                                build_invoked=False,
                                build_duration_seconds=0.0,
                            ),
                            attempt_events=tuple(attempt_events),
                            **current_result_metadata(),
                        )
                    failure_kind, summary, child_summary, build_output = cls._classify_fix_role_failure(
                        agent_error=runtime_result.agent_error,
                        attempt_events=attempt_events,
                    )
                    fix_memory = append_child_agent_memory_turn(
                        fix_memory,
                        attempt_number=len(fix_memory.turns) + 1,
                        decision="retry",
                        summary=child_summary,
                        workspace_state="issue_baseline",
                        next_action=(
                            "先 Read 当前目标文件，再用完整参数重新提交更精确的 patch。"
                            if failure_kind == "tool_input_invalid"
                            else "重新读取问题文件，改用更小的修法。"
                        ),
                    )
                    store.save_child_memory(fix_memory)
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        error=runtime_result.agent_error,
                        summary=summary,
                        build_command=build_command,
                        build_output=build_output,
                        retryable_failure=True,
                        failure_kind=failure_kind,
                        performance_metrics=runtime_metrics,
                        attempt_events=tuple(attempt_events),
                        **current_result_metadata(),
                    )
            if not changes:
                failure_kind = "tool_input_invalid" if invalid_write_tool_input else (
                    "model_timeout" if model_timeout_stage else "no_change"
                )
                error = (
                    "Model emitted an invalid Edit/MultiEdit/Write call"
                    if invalid_write_tool_input
                    else ("Model response timed out" if model_timeout_stage else "Agent completed without modifying any files")
                )
                child_summary = (
                    "Edit/MultiEdit/Write 调用缺少必要参数；先 Read 精确片段，再提交完整 patch。"
                    if failure_kind == "tool_input_invalid"
                    else ("Fix 子Agent 执行超时。" if failure_kind == "model_timeout" else "Fix 子Agent没有产生代码修改。")
                )
                fix_memory = append_child_agent_memory_turn(
                    fix_memory,
                    attempt_number=len(fix_memory.turns) + 1,
                    decision="retry",
                    summary=child_summary,
                    workspace_state="issue_baseline",
                    next_action=(
                        "先 Read 当前目标文件，再用完整参数重新提交更精确的 patch。"
                        if failure_kind == "tool_input_invalid"
                        else "重新读取问题文件，改用更小的修法。"
                    ),
                )
                store.save_child_memory(fix_memory)
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=issue.file_path,
                    error=error,
                    summary=child_summary,
                    build_command=build_command,
                    build_output=invalid_write_tool_input or "",
                    retryable_failure=True,
                    failure_kind=failure_kind,
                    performance_metrics=cls._merge_performance_metrics(
                        runtime_metrics,
                        build_invoked=False,
                        build_duration_seconds=0.0,
                        patch_salvaged=patch_salvaged,
                        model_timeout_stage=model_timeout_stage,
                    ),
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **current_result_metadata(),
                )
            fallback_before_texts: dict[str, str] = {}
            normalized_issue_path = str(issue.file_path or "").replace("\\", "/").lstrip("/")
            if normalized_issue_path and original_issue_file_content is not None:
                fallback_before_texts[normalized_issue_path] = original_issue_file_content
            reviewed_changes = agent._build_attempt_file_changes(
                workspace_path,
                changed_files,
                fallback_before_texts=(fallback_before_texts or None),
            )
        finally:
            cls._cleanup_attempt_workspace_state(workspace_path)
        current_issue_path = workspace_path / issue.file_path.lstrip("/")
        current_issue_file_content = (
            current_issue_path.read_text(encoding="utf-8", errors="replace")
            if current_issue_path.exists()
            else ""
        )
        reviewer_result = DiffReviewer.review(edit_contract=edit_contract, file_changes=reviewed_changes)
        patch_summary = cls._build_patch_summary(
            issue=issue,
            edit_contract=edit_contract,
            current_issue_file_content=current_issue_file_content,
            reviewed_changes=reviewed_changes,
        )
        review_working_memory = (
            merge_issue_working_memory(
                working_memory,
                authoritative_workspace_state="attempt_patch",
                latest_patch_summary=patch_summary,
                latest_verification="当前 patch 已生成，等待 Review 子Agent 基于当前代码审查。",
                latest_retryable_failure="",
                rollback_reason="",
                next_action="先审查当前 patch 是否值得进入编译，不要把上一轮外部构建/回滚状态当作当前 patch 风险。",
            )
            if working_memory is not None
            else None
        )
        fix_memory = append_child_agent_memory_turn(
            fix_memory,
            attempt_number=len(fix_memory.turns) + 1,
            decision="patched",
            summary=patch_summary or "已生成 patch。",
            workspace_state="attempt_patch",
            next_action="等待 Review 子Agent 审查。",
        )
        store.save_child_memory(fix_memory)

        if reviewer_result.status == "retry":
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=changes,
                build_passed=False,
                error="Filesystem boundary rejected the patch",
                summary=reviewer_result.summary,
                build_command=build_command,
                build_output=reviewer_result.to_retry_message(),
                retryable_failure=True,
                failure_kind="reviewer",
                reviewer_result=reviewer_result.to_dict(),
                performance_metrics=runtime_metrics,
                attempt_events=tuple(attempt_events),
                **current_result_metadata(),
            )

        if patch_salvaged:
            verification = FixVerifier.evaluate_attempt(
                issue=issue,
                workspace_path=workspace_path,
                build_command=build_command,
                edit_contract=edit_contract,
                guardrail_mode=guardrail_mode,
                scope=scope,
                reviewed_changes=reviewed_changes,
                original_issue_file_content=original_issue_file_content,
                current_issue_file_content=current_issue_file_content,
                build_runner=subprocess.run,
                scope_validator=cls._validate_issue_edit_scope,
                rule_validator=cls._run_rule_specific_validation,
            )
            if verification.rule_validation_message:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=issue.file_path,
                    changes=changes,
                    build_passed=verification.build_passed,
                    error="Rule-specific validation failed",
                    summary="Salvaged patch failed rule-specific validation.",
                    build_command=build_command,
                    build_output=verification.rule_validation_message,
                    retryable_failure=True,
                    failure_kind="rule_validation",
                    reviewer_result=reviewer_result.to_dict(),
                    performance_metrics=cls._merge_performance_metrics(
                        runtime_metrics,
                        build_invoked=verification.build_invoked,
                        build_duration_seconds=verification.build_duration_seconds,
                        patch_salvaged=True,
                        model_timeout_stage=model_timeout_stage,
                    ),
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=True,
                    attempt_events=tuple(attempt_events),
                    **current_result_metadata(),
                )
            if not verification.build_passed:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=issue.file_path,
                    changes=changes,
                    build_passed=False,
                    build_verification_failed=True,
                    error="Issue changes failed local build verification",
                    summary="Salvaged patch failed local build verification.",
                    build_command=build_command,
                    build_output=verification.combined_output,
                    retryable_failure=True,
                    failure_kind="build",
                    reviewer_result=reviewer_result.to_dict(),
                    performance_metrics=cls._merge_performance_metrics(
                        runtime_metrics,
                        build_invoked=verification.build_invoked,
                        build_duration_seconds=verification.build_duration_seconds,
                        patch_salvaged=True,
                        model_timeout_stage=model_timeout_stage,
                    ),
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=True,
                    attempt_events=tuple(attempt_events),
                    **current_result_metadata(),
                )
            return FixResult(
                success=True,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=changes,
                build_passed=True,
                summary="Patch salvage passed local verification.",
                build_command=build_command,
                build_output=verification.build_output,
                reviewer_result=reviewer_result.to_dict(),
                performance_metrics=cls._merge_performance_metrics(
                    runtime_metrics,
                    build_invoked=verification.build_invoked,
                    build_duration_seconds=verification.build_duration_seconds,
                    patch_salvaged=True,
                    model_timeout_stage=model_timeout_stage,
                ),
                model_timeout_stage=model_timeout_stage,
                patch_salvaged=True,
                attempt_events=tuple(attempt_events),
                **current_result_metadata(),
            )

        review_run = cls._run_prompt_only_role_session(
            role="review",
            workspace_path=workspace_path,
            system_prompt=build_review_role_system_prompt(),
            user_prompt=build_review_role_user_prompt(
                issue=issue,
                code_context=code_context,
                patch_summary=patch_summary,
                current_file_content=current_issue_file_content,
                working_memory=review_working_memory,
                review_memory=review_memory,
            ),
            max_turns=4,
            agent_env=agent.agent_env,
            explicit_model=agent.model,
        )
        review_decision = cls._parse_role_decision(
            raw_text=review_run.response_text or review_run.agent_error or "",
            allowed_decisions=("approve", "retry"),
            fallback_decision="retry",
            fallback_summary="Review 子Agent 未给出可用结论。",
        )
        review_decision = cls._stabilize_review_decision(
            issue=issue,
            patch_summary=patch_summary,
            decision=review_decision,
        )
        review_memory = append_child_agent_memory_turn(
            review_memory,
            attempt_number=len(review_memory.turns) + 1,
            decision=review_decision.decision,
            summary=review_decision.summary,
            findings=review_decision.findings,
            constraints=review_decision.constraints,
            workspace_state="attempt_patch",
            next_action=(
                "等待 Main 裁决。"
                if review_decision.decision == "approve"
                else "根据 review 约束换一种修法。"
            ),
        )
        store.save_child_memory(review_memory)
        if review_decision.decision != "approve":
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=changes,
                error=review_decision.summary,
                summary="Review 子Agent拒绝当前 patch。",
                build_command=build_command,
                build_output=review_decision.raw_text or review_decision.summary,
                retryable_failure=True,
                failure_kind="review_agent",
                reviewer_result=reviewer_result.to_dict(),
                performance_metrics=runtime_metrics,
                attempt_events=tuple(attempt_events),
                **current_result_metadata(),
            )

        main_run = cls._run_prompt_only_role_session(
            role="main",
            workspace_path=workspace_path,
            system_prompt=build_main_role_system_prompt(),
            user_prompt=build_main_role_user_prompt(
                issue=issue,
                patch_summary=patch_summary,
                review_result={
                    "decision": review_decision.decision,
                    "summary": review_decision.summary,
                    "findings": list(review_decision.findings),
                    "constraints": list(review_decision.constraints),
                },
                working_memory=review_working_memory,
                main_memory=main_memory,
            ),
            max_turns=3,
            agent_env=agent.agent_env,
            explicit_model=agent.model,
        )
        main_decision = cls._parse_role_decision(
            raw_text=main_run.response_text or main_run.agent_error or "",
            allowed_decisions=("compile", "retry"),
            fallback_decision="retry",
            fallback_summary="Main 裁决未批准进入编译阶段。",
        )
        main_decision = cls._stabilize_main_decision(
            review_decision=review_decision,
            decision=main_decision,
        )
        main_memory = append_child_agent_memory_turn(
            main_memory,
            attempt_number=len(main_memory.turns) + 1,
            decision=main_decision.decision,
            summary=main_decision.summary,
            constraints=main_decision.constraints,
            workspace_state="attempt_patch",
            next_action=(
                "进入编译阶段。"
                if main_decision.decision == "compile"
                else "回到 Fix 子Agent 继续修复。"
            ),
        )
        store.save_child_memory(main_memory)
        if main_decision.decision != "compile":
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=changes,
                error=main_decision.summary,
                summary="Main 裁决要求继续修复后再编译。",
                build_command=build_command,
                build_output=main_decision.raw_text or main_decision.summary,
                retryable_failure=True,
                failure_kind="main_decision",
                reviewer_result=reviewer_result.to_dict(),
                performance_metrics=runtime_metrics,
                attempt_events=tuple(attempt_events),
                **current_result_metadata(),
            )

        verification = FixVerifier.evaluate_attempt(
            issue=issue,
            workspace_path=workspace_path,
            build_command=build_command,
            edit_contract=edit_contract,
            guardrail_mode=guardrail_mode,
            scope=scope,
            reviewed_changes=reviewed_changes,
            original_issue_file_content=original_issue_file_content,
            current_issue_file_content=current_issue_file_content,
            build_runner=subprocess.run,
            scope_validator=cls._validate_issue_edit_scope,
            rule_validator=cls._run_rule_specific_validation,
        )
        if verification.rule_validation_message:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=changes,
                build_passed=verification.build_passed,
                error="Rule-specific validation failed",
                summary=main_decision.summary or "规则级校验未通过。",
                build_command=build_command,
                build_output=verification.rule_validation_message,
                retryable_failure=True,
                failure_kind="rule_validation",
                reviewer_result=reviewer_result.to_dict(),
                performance_metrics=cls._merge_performance_metrics(
                    runtime_metrics,
                    build_invoked=verification.build_invoked,
                    build_duration_seconds=verification.build_duration_seconds,
                ),
                attempt_events=tuple(attempt_events),
                **current_result_metadata(),
            )
        if not verification.build_passed:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=issue.file_path,
                changes=changes,
                build_passed=False,
                build_verification_failed=True,
                error="Issue changes failed local build verification",
                summary=main_decision.summary or "编译未通过。",
                build_command=build_command,
                build_output=verification.combined_output,
                retryable_failure=True,
                failure_kind="build",
                reviewer_result=reviewer_result.to_dict(),
                performance_metrics=cls._merge_performance_metrics(
                    runtime_metrics,
                    build_invoked=verification.build_invoked,
                    build_duration_seconds=verification.build_duration_seconds,
                ),
                attempt_events=tuple(attempt_events),
                **current_result_metadata(),
            )

        return FixResult(
            success=True,
            issue_key=issue.key,
            file_path=issue.file_path,
            changes=changes,
            build_passed=True,
            summary=main_decision.summary or "Patch 已通过子Agent审查并完成编译。",
            build_command=build_command,
            build_output=verification.build_output,
            reviewer_result=reviewer_result.to_dict(),
            performance_metrics=cls._merge_performance_metrics(
                runtime_metrics,
                build_invoked=verification.build_invoked,
                build_duration_seconds=verification.build_duration_seconds,
            ),
            attempt_events=tuple(attempt_events),
            **current_result_metadata(),
        )

    @staticmethod
    def _resolve_guardrail_mode(agent_env: dict[str, str] | None = None) -> str:
        """Resolve the configured issue guardrail mode."""

        raw_value = (
            (agent_env or {}).get("ISSUE_GUARDRAIL_MODE")
            or read_project_env().get("ISSUE_GUARDRAIL_MODE", "")
        )
        normalized = str(raw_value or "").strip().lower()
        if normalized in {"scope", "contract_review"}:
            return normalized
        return "scope"

    @staticmethod
    def _resolve_execution_mode(agent_env: dict[str, str] | None = None) -> str:
        """Resolve the configured issue execution mode."""

        return resolve_execution_mode(agent_env)

    @classmethod
    def _build_issue_plan(
        cls,
        *,
        issue: SonarIssue,
        scope: IssueEditScope | None,
        retry_context: RetryContext | None,
        workspace_path: Path,
        source_lines: tuple[str, ...] | None = None,
        agent_env: dict[str, str] | None = None,
    ):
        """Build the issue plan and edit contract for this attempt."""

        workspace_rules = ResourceLoader.load_workspace_rules(workspace_path)
        performance_flags = load_performance_flags()
        scope_mode = scope.mode if scope is not None else STATEMENT_SCOPE_MODE
        scope_start = scope.start_line if scope is not None else issue.line
        scope_end = scope.end_line if scope is not None else issue.line
        validation_start = scope.validation_start_line if scope is not None else issue.line
        validation_end = scope.validation_end_line if scope is not None else issue.line
        guardrail_mode = cls._resolve_guardrail_mode(agent_env)
        execution_mode = cls._resolve_execution_mode(agent_env)
        return IssuePlanner.plan_issue(
            issue_key=issue.key,
            rule_id=issue.rule,
            file_path=issue.file_path,
            issue_line=issue.line,
            guardrail_mode=guardrail_mode,
            execution_mode=execution_mode,
            scope_mode=scope_mode,
            scope_start_line=scope_start,
            scope_end_line=scope_end,
            validation_start_line=validation_start,
            validation_end_line=validation_end,
            source_lines=source_lines,
            workspace_path=workspace_path,
            retry_context=retry_context,
            workspace_rules=workspace_rules,
            performance_flags=performance_flags,
        )

    @classmethod
    def _build_fix_tool_policy(
        cls,
        edit_contract: Any | None = None,
        *,
        mcp_tool_names: tuple[str, ...] | list[str] = (),
        workspace_path: Path | None = None,
        runtime_builtin_tools: tuple[str, ...] | None = None,
    ) -> ToolPolicy:
        """Build the runtime tool policy for single-issue fix attempts."""

        policy, _ = cls._build_fix_tool_policy_bundle(
            edit_contract,
            mcp_tool_names=mcp_tool_names,
            workspace_path=workspace_path,
            runtime_builtin_tools=runtime_builtin_tools,
        )
        return policy

    @classmethod
    def _build_fix_tool_policy_bundle(
        cls,
        edit_contract: Any | None = None,
        *,
        mcp_tool_names: tuple[str, ...] | list[str] = (),
        workspace_path: Path | None = None,
        runtime_builtin_tools: tuple[str, ...] | None = None,
    ) -> tuple[ToolPolicy, Any]:
        """Build the runtime tool policy plus the canonical visible toolset snapshot."""

        allow_file_creation = False
        allowed_new_file_roots: tuple[str, ...] = ()
        runtime_builtin_tools = runtime_builtin_tools or build_fix_runtime_tools(
            include_create_file_tool=False
        )
        registry = build_fix_tool_registry(
            runtime_builtin_tools,
            mcp_tool_names,
            FORBIDDEN_FIX_TOOLS,
        )
        allowed_tools = [tool_name for tool_name in runtime_builtin_tools if tool_name != CONTROLLED_BASH_TOOL]
        allowed_tools.extend(mcp_tool_names)
        if edit_contract is not None:
            allowed_tools = list(EditorPolicy.allowed_tool_names(allowed_tools, edit_contract))
        visible_toolset = build_visible_toolset(
            registry,
            allowed_tools,
            include_controlled_bash=controlled_bash_enabled(),
            bash_file_creation_roots=allowed_new_file_roots,
            create_file_tool_roots=allowed_new_file_roots,
        )
        return ToolPolicy(
            registry,
            visible_toolset.allowed_tools,
            workspace_root=workspace_path,
        ), visible_toolset

    @staticmethod
    def _resolve_solution_path(workspace_path: Path) -> str:
        """Find the first solution file under the workspace for Roslyn solution-scope rules."""

        protected_dirs = {".git", "logs", "bin", "obj", "__pycache__"}
        candidates: list[Path] = []
        for path in workspace_path.rglob("*.sln"):
            try:
                relative_parts = path.relative_to(workspace_path).parts
            except ValueError:
                relative_parts = path.parts
            if any(part in protected_dirs for part in relative_parts):
                continue
            candidates.append(path)
        if not candidates:
            return ""
        return str(sorted(candidates)[0])

    @classmethod
    def _build_roslyn_issue_group(cls, issue: SonarIssue) -> IssueGroup:
        """Build the deterministic issue-group shape consumed by the Roslyn engine."""

        return IssueGroup(
            group_key=issue.key,
            file_path=IssuePromptBuilder.render_workspace_relative_path(issue.file_path),
            rule=issue.rule,
            issues=(
                {
                    "key": issue.key,
                    "line": issue.line,
                    "textRange": issue.text_range,
                    "message": issue.message,
                },
            ),
            start_line=issue.start_line or issue.line,
            end_line=issue.end_line or issue.start_line or issue.line,
            symbol_names=(),
        )

    @classmethod
    def _align_edit_contract_for_roslyn_patch(
        cls,
        *,
        issue: SonarIssue,
        edit_contract: Any,
        roslyn_strategy: str,
    ) -> Any:
        """Align repair-plan expectations with deterministic Roslyn patch shapes."""

        repair_plan = getattr(edit_contract, "repair_plan", None)
        if repair_plan is None:
            return edit_contract

        normalized_strategy = str(roslyn_strategy or "").strip()
        if issue.rule == "csharpsquid:S107" and "parameter_object" in normalized_strategy:
            adjusted_repair_plan = replace(
                repair_plan,
                requires_new_type=True,
                requires_signature_change=True,
                strategy_preferences=tuple(
                    dict.fromkeys(
                        (
                            *tuple(getattr(repair_plan, "strategy_preferences", ()) or ()),
                            "roslyn_parameter_object_signature_change",
                            "roslyn_parameter_object_new_type",
                        )
                    )
                ),
                risk_notes=tuple(
                    dict.fromkeys(
                        (
                            *tuple(getattr(repair_plan, "risk_notes", ()) or ()),
                            "Roslyn S107 parameter-object patch intentionally introduces a local parameter carrier type and rewires the target method signature.",
                        )
                    )
                ),
            )
            return replace(edit_contract, repair_plan=adjusted_repair_plan)

        return edit_contract

    @classmethod
    def _run_roslyn_fix_path(
        cls,
        *,
        issue: SonarIssue,
        workspace_path: Path,
        build_command: str,
        edit_contract: Any,
        guardrail_mode: str,
        scope: IssueEditScope | None,
        original_issue_file_content: str | None,
        result_metadata: dict[str, Any],
    ) -> FixResult:
        """Run the Roslyn engine for routed rules and return a structured terminal result."""

        issue_file_path = workspace_path / issue.file_path.lstrip("/")
        engine = RoslynFixEngine()
        issue_group = cls._build_roslyn_issue_group(issue)
        roslyn_result = engine.apply_solution_fix(
            workspace_path=str(workspace_path),
            solution_path=cls._resolve_solution_path(workspace_path),
            issue_group=issue_group,
            primary_issue={
                "key": issue.key,
                "line": issue.line,
                "textRange": issue.text_range,
                "message": issue.message,
            },
        )
        if not roslyn_result.applied and issue.rule == "csharpsquid:S107":
            deterministic_result = generate_s107_parameter_object_patch(
                workspace_path,
                issue_group,
            )
            if deterministic_result.applied:
                roslyn_result = replace(
                    roslyn_result,
                    applied=True,
                    strategy=deterministic_result.strategy,
                    summary=deterministic_result.summary,
                    error=deterministic_result.error,
                    changed_files=dict(deterministic_result.changed_files),
                )
            else:
                roslyn_result = replace(
                    roslyn_result,
                    strategy=(
                        roslyn_result.strategy
                        if not bool(getattr(roslyn_result, "can_fix_safely", False))
                        else deterministic_result.strategy
                    ),
                    summary=roslyn_result.summary or deterministic_result.summary,
                    error=deterministic_result.error or roslyn_result.error,
                )
        summary = str(roslyn_result.summary or roslyn_result.error).strip()
        if roslyn_result.applied and roslyn_result.changed_files:
            fallback_before_texts: dict[str, str] = {}
            cls._capture_attempt_workspace_state(workspace_path)
            try:
                changed_files: list[str] = []
                changes: list[dict[str, Any]] = []
                for relative_path, updated_content in roslyn_result.changed_files.items():
                    normalized_path = str(relative_path).replace("\\", "/")
                    target_path = workspace_path / normalized_path
                    if target_path.exists():
                        fallback_before_texts[normalized_path] = target_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_text(updated_content, encoding="utf-8")
                    changed_files.append(normalized_path)
                    changes.append(
                        {
                            "file": normalized_path,
                            "action": "modified" if normalized_path in fallback_before_texts else "created",
                        }
                    )

                reviewed_changes = cls._build_attempt_file_changes(
                    workspace_path,
                    changed_files,
                    fallback_before_texts=fallback_before_texts or None,
                )
                current_issue_file_content = (
                    issue_file_path.read_text(encoding="utf-8", errors="replace")
                    if issue_file_path.exists()
                    else None
                )
                effective_edit_contract = cls._align_edit_contract_for_roslyn_patch(
                    issue=issue,
                    edit_contract=edit_contract,
                    roslyn_strategy=roslyn_result.strategy,
                )
                effective_result_metadata = {
                    **result_metadata,
                    "edit_contract": effective_edit_contract,
                    "repair_plan": getattr(effective_edit_contract, "repair_plan", None),
                }
                verification = FixVerifier.evaluate_attempt(
                    issue=issue,
                    workspace_path=workspace_path,
                    build_command=build_command,
                    edit_contract=effective_edit_contract,
                    guardrail_mode=guardrail_mode,
                    scope=scope,
                    reviewed_changes=reviewed_changes,
                    original_issue_file_content=original_issue_file_content,
                    current_issue_file_content=current_issue_file_content,
                    build_runner=subprocess.run,
                    scope_validator=cls._validate_issue_edit_scope,
                    rule_validator=cls._run_rule_specific_validation,
                )
                reviewer_result = verification.reviewer_result
                semantic_precheck_result = verification.semantic_precheck_result
                quality_gate_result = verification.quality_gate_result
                review_gate_result = verification.review_gate_result
                performance_metrics = cls._merge_performance_metrics(
                    {
                        "execution_profile": str(getattr(effective_edit_contract, "execution_profile", "full_path")),
                        "fast_path_enabled": bool(getattr(effective_edit_contract, "fast_path_enabled", False)),
                        "engine_routing_decision": getattr(
                            result_metadata.get("engine_routing_decision"),
                            "to_dict",
                            lambda: result_metadata.get("engine_routing_decision"),
                        )(),
                        "roslyn_strategy": roslyn_result.strategy,
                        "build_invoked": verification.build_invoked,
                    },
                    fast_compile_invoked=verification.fast_compile_invoked,
                    fast_compile_passed=verification.fast_compile_passed,
                    fast_compile_duration_seconds=verification.fast_compile_duration_seconds,
                    fast_compile_command=verification.fast_compile_command,
                    build_duration_seconds=verification.build_duration_seconds,
                )

                if reviewer_result.status == "retry":
                    failure_stage = (
                        "filesystem_boundary"
                        if str(verification.boundary_failure_code).startswith("filesystem_")
                        else "reviewer"
                    )
                    failure_error = (
                        "Filesystem boundary rejected the Roslyn patch"
                        if failure_stage == "filesystem_boundary"
                        else "Diff reviewer rejected the Roslyn patch"
                    )
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=verification.build_passed,
                        error=failure_error,
                        summary=summary or "Roslyn engine produced a candidate patch.",
                        build_command=build_command,
                        build_output=verification.reviewer_retry_message or verification.combined_output,
                        retryable_failure=True,
                        failure_kind="reviewer",
                        reviewer_result=reviewer_result.to_dict(),
                        semantic_precheck_result=semantic_precheck_result.to_dict(),
                        quality_gate_result=quality_gate_result.to_dict(),
                        review_gate_result=review_gate_result.to_dict(),
                        follow_ups=reviewer_result.follow_ups,
                        boundary_failure_code=verification.boundary_failure_code,
                        boundary_failure_summary=verification.boundary_failure_summary,
                        secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                        performance_metrics=performance_metrics,
                        **effective_result_metadata,
                    )

                if semantic_precheck_result.status == "retry":
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=verification.build_passed,
                        error="Semantic precheck failed",
                        summary=summary or "Roslyn engine produced a candidate patch.",
                        build_command=build_command,
                        build_output=semantic_precheck_result.to_retry_message(),
                        retryable_failure=True,
                        failure_kind="semantic_precheck",
                        reviewer_result=reviewer_result.to_dict(),
                        semantic_precheck_result=semantic_precheck_result.to_dict(),
                        quality_gate_result=quality_gate_result.to_dict(),
                        review_gate_result=review_gate_result.to_dict(),
                        follow_ups=reviewer_result.follow_ups,
                        boundary_failure_code=verification.boundary_failure_code,
                        boundary_failure_summary=verification.boundary_failure_summary,
                        secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                        performance_metrics=performance_metrics,
                        **effective_result_metadata,
                    )

                if review_gate_result.status == "retry":
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=verification.build_passed,
                        error="Review gate verification failed",
                        summary=summary or "Roslyn engine produced a candidate patch.",
                        build_command=build_command,
                        build_output=review_gate_result.to_retry_message(),
                        retryable_failure=True,
                        failure_kind="review_gate",
                        reviewer_result=reviewer_result.to_dict(),
                        semantic_precheck_result=semantic_precheck_result.to_dict(),
                        quality_gate_result=quality_gate_result.to_dict(),
                        review_gate_result=review_gate_result.to_dict(),
                        follow_ups=reviewer_result.follow_ups,
                        boundary_failure_code=verification.boundary_failure_code,
                        boundary_failure_summary=verification.boundary_failure_summary,
                        secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                        performance_metrics=performance_metrics,
                        **effective_result_metadata,
                    )

                if quality_gate_result.status == "retry":
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=verification.build_passed,
                        error="Quality gate verification failed",
                        summary=summary or "Roslyn engine produced a candidate patch.",
                        build_command=build_command,
                        build_output=quality_gate_result.to_retry_message(),
                        retryable_failure=True,
                        failure_kind="quality_gate",
                        reviewer_result=reviewer_result.to_dict(),
                        semantic_precheck_result=semantic_precheck_result.to_dict(),
                        quality_gate_result=quality_gate_result.to_dict(),
                        review_gate_result=review_gate_result.to_dict(),
                        follow_ups=reviewer_result.follow_ups,
                        boundary_failure_code=verification.boundary_failure_code,
                        boundary_failure_summary=verification.boundary_failure_summary,
                        secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                        performance_metrics=performance_metrics,
                        **effective_result_metadata,
                    )

                if verification.rule_validation_message:
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=verification.build_passed,
                        error="Rule-specific validation failed",
                        summary=summary or "Roslyn engine produced a candidate patch.",
                        build_command=build_command,
                        build_output=verification.rule_validation_message,
                        retryable_failure=True,
                        failure_kind="rule_validation",
                        reviewer_result=reviewer_result.to_dict(),
                        semantic_precheck_result=semantic_precheck_result.to_dict(),
                        quality_gate_result=quality_gate_result.to_dict(),
                        review_gate_result=review_gate_result.to_dict(),
                        follow_ups=reviewer_result.follow_ups,
                        boundary_failure_code=verification.boundary_failure_code,
                        boundary_failure_summary=verification.boundary_failure_summary,
                        secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                        performance_metrics=performance_metrics,
                        **effective_result_metadata,
                    )

                if not verification.build_passed:
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=issue.file_path,
                        changes=changes,
                        build_passed=False,
                        build_verification_failed=True,
                        error="Issue changes failed local build verification",
                        summary=summary or "Roslyn engine produced a candidate patch.",
                        build_command=build_command,
                        build_output=verification.combined_output,
                        retryable_failure=True,
                        failure_kind="build",
                        reviewer_result=reviewer_result.to_dict(),
                        semantic_precheck_result=semantic_precheck_result.to_dict(),
                        quality_gate_result=quality_gate_result.to_dict(),
                        review_gate_result=review_gate_result.to_dict(),
                        follow_ups=reviewer_result.follow_ups,
                        boundary_failure_code=verification.boundary_failure_code,
                        boundary_failure_summary=verification.boundary_failure_summary,
                        secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                        performance_metrics=performance_metrics,
                        **effective_result_metadata,
                    )

                return FixResult(
                    success=True,
                    issue_key=issue.key,
                    file_path=issue.file_path,
                    changes=changes,
                    build_passed=verification.build_passed,
                    summary=summary or "Roslyn engine applied a candidate patch.",
                    build_command=build_command,
                    build_output=verification.build_output,
                    reviewer_result=reviewer_result.to_dict(),
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=verification.boundary_failure_code,
                    boundary_failure_summary=verification.boundary_failure_summary,
                    secondary_boundary_failure_codes=verification.secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    **effective_result_metadata,
                )
            finally:
                cls._cleanup_attempt_workspace_state(workspace_path)

        failure_kind = (
            "roslyn_candidate_not_applied"
            if bool(getattr(roslyn_result, "can_fix_safely", False))
            else "roslyn_cannot_fix_safely"
        )
        return FixResult(
            success=False,
            skipped=True,
            issue_key=issue.key,
            file_path=issue.file_path,
            error=summary or "Roslyn engine did not apply a fix.",
            summary=summary or "Roslyn engine did not apply a fix.",
            build_command=build_command,
            build_output=summary or "Roslyn engine did not apply a fix.",
            failure_kind=failure_kind,
            skip_reason=summary or "Roslyn engine did not apply a fix.",
            **result_metadata,
        )

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
        *,
        fallback_before_texts: dict[str, str] | None = None,
    ) -> tuple[ReviewedFileChange, ...]:
        """Build file-level diff facts for diff review."""

        return AttemptFileChangeBuilder.build(
            workspace_path=workspace_path,
            changed_files=changed_files,
            manifest=cls._load_attempt_state_manifest(workspace_path),
            fallback_before_texts=fallback_before_texts,
        )

    @staticmethod
    def _capture_non_git_workspace_snapshot(workspace_path: Path) -> dict[str, str]:
        """Capture a lightweight text snapshot when git baseline state is unavailable."""

        if (workspace_path / ".git").exists():
            return {}

        snapshot: dict[str, str] = {}
        for file_path in workspace_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(workspace_path).as_posix()
            if rel_path.startswith((".git/", "logs/", ".agent_workspaces/")):
                continue
            try:
                snapshot[rel_path] = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        return snapshot

    @staticmethod
    def _extract_invalid_write_tool_input_message(
        attempt_events: tuple[AttemptRuntimeEvent, ...] | list[AttemptRuntimeEvent],
    ) -> str:
        """Detect malformed Edit/MultiEdit/Write tool calls that ended with empty or invalid input."""

        saw_empty_write_payload = False
        for event in reversed(tuple(attempt_events or ())):
            if event.kind == AttemptRuntimeEventKind.SDK_TRACE and str(event.stage or "") == "sdk_message:UserMessage":
                preview = str(getattr(event, "payload", {}).get("preview", "") or "")
                if "InputValidationError" in preview and any(
                    marker in preview for marker in ("file_path", "old_string", "new_string", "edits", "content")
                ):
                    return preview
            if event.kind != AttemptRuntimeEventKind.TOOL_CALLED:
                continue
            payload = getattr(event, "payload", {}) or {}
            tool_name = str(payload.get("tool_name", "") or "")
            tool_payload = payload.get("tool_payload")
            if tool_name in {"Edit", "MultiEdit", "Write"} and isinstance(tool_payload, dict) and not tool_payload:
                saw_empty_write_payload = True
        if saw_empty_write_payload:
            return (
                "Invalid write tool input: Edit/MultiEdit/Write was called with an empty payload. "
                "Required parameters such as file_path, old_string/new_string, edits, or content were missing."
            )
        return ""

    @staticmethod
    def _is_max_turn_agent_error(agent_error: str) -> bool:
        text = str(agent_error or "").strip().lower()
        return "maximum number of turns" in text

    @classmethod
    def _classify_runtime_contract_agent_error(
        cls,
        agent_error: str,
    ) -> tuple[str, str] | None:
        text = str(agent_error or "").strip()
        normalized = text.lower()
        if "当前 retry 已禁用 helper_extract" in text or "helper_extract" in normalized:
            return "helper_extract_runtime_guard", text
        return None

    @classmethod
    def _classify_fix_role_failure(
        cls,
        *,
        agent_error: str,
        attempt_events: tuple[AttemptRuntimeEvent, ...] | list[AttemptRuntimeEvent],
    ) -> tuple[str, str, str, str]:
        """Normalize fix child-agent failures into retry-meaningful categories."""

        error_text = str(agent_error or "").strip()
        invalid_write_tool_input = cls._extract_invalid_write_tool_input_message(attempt_events)
        if invalid_write_tool_input:
            return (
                "tool_input_invalid",
                "Fix 子Agent发出了无效的 Edit/MultiEdit/Write 工具调用。",
                "Edit/MultiEdit/Write 调用缺少必要参数；先 Read 精确片段，再提交完整 patch。",
                invalid_write_tool_input,
            )
        runtime_contract = cls._classify_runtime_contract_agent_error(error_text)
        if runtime_contract is not None:
            _code, detail = runtime_contract
            return (
                "runtime_contract_violation",
                "Fix 子Agent触发了运行时 contract 护栏。",
                "运行时 contract 护栏拒绝了当前修法，下一轮必须换一种更小的修法。",
                detail or error_text,
            )
        return (
            "fix_agent",
            "Fix 子Agent执行失败。",
            "Fix 子Agent执行失败。",
            error_text,
        )

    @classmethod
    def _should_salvage_agent_error(
        cls,
        *,
        agent_error: str,
        changes_detected: bool,
        used_forbidden_tool: bool,
        invalid_write_tool_input: str = "",
    ) -> bool:
        if not changes_detected or used_forbidden_tool:
            return False
        if cls._is_max_turn_agent_error(agent_error):
            return True
        return bool(invalid_write_tool_input and "invalid write tool input burst" in str(agent_error or "").lower())

    def fix_issue(
        self,
        issue: SonarIssue,
        workspace_path: Path,
        build_command: str = "dotnet build",
        retry_feedback: str = "",
        retry_context: RetryContext | None = None,
        working_memory: IssueWorkingMemory | None = None,
    ) -> FixResult:
        """Fix a single SonarQube issue using Claude Code."""
        # Prepare workspace
        workspace_path.mkdir(parents=True, exist_ok=True)
        file_path = workspace_path / issue.file_path.lstrip("/")
        context_cache = AttemptContextCache()

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
        non_git_workspace_snapshot = self._capture_non_git_workspace_snapshot(workspace_path)
        source_lines: tuple[str, ...] | None = None
        if file_path.exists():
            content = context_cache.read_text(file_path, encoding="utf-8")
            original_issue_file_content = content
            lines = list(context_cache.read_lines(file_path, encoding="utf-8"))
            source_lines = tuple(lines)
            start_line = max(1, issue.start_line - 10)
            end_line = min(len(lines), issue.end_line + 10)
            code_context = context_cache.render_numbered_window(
                file_path,
                start_line,
                end_line,
                encoding="utf-8",
            )
            scope = self._build_issue_edit_scope(issue, lines)
        else:
            # Fall back to SonarQube snippet
            try:
                snippet = self.get_issue_snippet(issue.key)
                code_context = snippet
            except Exception:
                code_context = f"File not found: {file_path}"

        performance_flags = load_performance_flags()
        issue_plan = self._build_issue_plan(
            issue=issue,
            scope=scope,
            retry_context=retry_context,
            workspace_path=workspace_path,
            source_lines=source_lines,
            agent_env=self.agent_env,
        )
        if str(getattr(issue_plan, "skip_reason", "")).strip():
            planner_skip_reason = str(issue_plan.skip_reason).strip()
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                error=planner_skip_reason,
                summary="Planner skipped the issue before any model attempt.",
                build_command=build_command.strip() or "dotnet build",
                build_output=planner_skip_reason,
                skipped=True,
                skip_reason=planner_skip_reason,
                failure_kind="planner_skip",
                performance_metrics={"build_invoked": False},
        )
        edit_contract = self._normalize_edit_contract_for_child_agents(issue_plan.edit_contract)
        execution_mode = str(getattr(edit_contract, "execution_mode", "")).strip()
        engine_routing_decision = route_engine_for_issue(
            rule_id=issue.rule,
            edit_contract=edit_contract,
        )
        guardrail_mode = edit_contract.guardrail_mode
        default_max_turns = self._resolve_issue_max_turns(issue)
        execution_schedule = AttemptScheduler.build_execution_schedule(
            edit_contract=edit_contract,
            performance_flags=performance_flags,
            default_max_turns=default_max_turns,
        )
        result_metadata = {
            "edit_contract": edit_contract,
            "repair_plan": getattr(edit_contract, "repair_plan", None),
            "plan_precheck": getattr(edit_contract, "plan_precheck", None),
            "guardrail_mode": guardrail_mode,
            "execution_mode": execution_mode,
            "execution_profile": str(getattr(edit_contract, "execution_profile", "full_path")),
            "fast_path_enabled": bool(getattr(edit_contract, "fast_path_enabled", False)),
            "rollout_flags": tuple(getattr(edit_contract, "rollout_flags", ()) or ()),
            "engine_routing_decision": engine_routing_decision,
            "issue_working_memory": working_memory,
        }
        sonar_mcp_runtime = build_sonar_mcp_runtime(self.agent_env)
        attempt_todo_store = AttemptTodoStore(workspace_path, issue.key, role="fix")
        attempt_todo_runtime = build_attempt_todo_runtime(
            attempt_todo_store,
            agent_env=self.agent_env,
        )
        runtime_builtin_tools = self._resolve_runtime_builtin_tools(workspace_path)
        tool_policy, visible_toolset = self._build_fix_tool_policy_bundle(
            edit_contract,
            mcp_tool_names=sonar_mcp_runtime.tool_names + attempt_todo_runtime.tool_names,
            workspace_path=workspace_path,
            runtime_builtin_tools=runtime_builtin_tools,
        )
        result_metadata["visible_toolset"] = visible_toolset
        if bool(getattr(engine_routing_decision, "should_skip", False)):
            skip_reason = str(getattr(engine_routing_decision, "skip_reason", "") or "").strip()
            return FixResult(
                success=False,
                skipped=True,
                issue_key=issue.key,
                file_path=str(file_path),
                error=skip_reason or "Engine router skipped this issue.",
                summary="Skipped by engine router",
                build_command=build_command.strip() or "dotnet build",
                build_output=skip_reason or "Engine router skipped this issue.",
                skip_reason=skip_reason,
                failure_kind="engine_router_skip",
                **result_metadata,
            )
        if str(getattr(engine_routing_decision, "resolved_engine", "") or "").strip() == "roslyn":
            return self._run_roslyn_fix_path(
                issue=issue,
                workspace_path=workspace_path,
                build_command=build_command.strip() or "dotnet build",
                edit_contract=edit_contract,
                guardrail_mode=guardrail_mode,
                scope=scope,
                original_issue_file_content=original_issue_file_content,
                result_metadata=result_metadata,
            )
        if str(getattr(issue, "rule", "") or "").strip() == "csharpsquid:S107":
            self._sync_s107_fix_guide(workspace_path)

        return self._run_role_orchestrated_flow(
            agent=self,
            issue=issue,
            workspace_path=workspace_path,
            build_command=build_command.strip() or "dotnet build",
            code_context=code_context,
            rule_details=rule_details,
            scope=scope,
            original_issue_file_content=original_issue_file_content,
            retry_feedback=retry_feedback,
            working_memory=working_memory,
            edit_contract=edit_contract,
            guardrail_mode=guardrail_mode,
            visible_toolset=visible_toolset,
            tool_policy=tool_policy,
            sonar_mcp_runtime=sonar_mcp_runtime,
            result_metadata=result_metadata,
            execution_schedule=execution_schedule,
            runtime_builtin_tools=runtime_builtin_tools,
        )

        # Build prompts
        system_prompt_result = self._build_system_prompt_result(
            workspace_path,
            edit_contract=edit_contract,
        )
        system_prompt = system_prompt_result.prompt
        resolved_build_command = build_command.strip() or "dotnet build"
        effective_working_memory = working_memory
        user_prompt_result = self._build_user_prompt_result(
            issue,
            code_context,
            (
                ""
                if is_simple_loop_execution_mode(execution_mode)
                else self._load_csharp_quality_gate(issue, edit_contract)
            ),
            self._build_scope_guidance(issue, scope, edit_contract),
            rule_details,
            resolved_build_command,
            retry_feedback,
            retry_context,
            edit_contract_section=(
                ""
                if is_simple_loop_execution_mode(execution_mode)
                else self._build_edit_contract_section(edit_contract)
            ),
            repair_plan_section=(
                ""
                if is_simple_loop_execution_mode(execution_mode)
                else self._build_repair_plan_section(edit_contract)
            ),
            prefetched_context_section=(
                ""
                if is_simple_loop_execution_mode(execution_mode)
                else self._build_prefetched_context_section(edit_contract)
            ),
            execution_mode_section=self._build_execution_mode_section(edit_contract),
            workspace_path=workspace_path,
            edit_contract=edit_contract,
            visible_tool_names=visible_toolset.visible_tools,
            working_memory=effective_working_memory,
            model_hint=(self.model or ""),
        )
        if getattr(user_prompt_result, "issue_working_memory", None) is not None:
            effective_working_memory = user_prompt_result.issue_working_memory
            result_metadata["issue_working_memory"] = effective_working_memory
        user_prompt = user_prompt_result.prompt
        prompt_budget_report = IssuePromptBuilder.build_prompt_budget_report(
            system_prompt_result,
            user_prompt_result,
        )
        result_metadata["prompt_budget_report"] = prompt_budget_report
        effective_max_turns = execution_schedule.effective_max_turns
        gateway_request = ClaudeAdapter.build_request(
            agent_env=self.agent_env,
            explicit_model=self.model,
            cwd=str(workspace_path),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=build_fix_runtime_tools(
                include_create_file_tool=bool(getattr(edit_contract, "allow_file_creation", False))
            ),
            allowed_tools=tool_policy.allowed_tool_names(),
            max_turns=effective_max_turns,
            max_budget_usd=self.max_budget_usd,
            stderr_handler=self._handle_cli_stderr,
            build_command=resolved_build_command,
            mcp_servers=sonar_mcp_runtime.server_configs,
        )
        gateway_request.metadata.update(
            {
                "issue_key": issue.key,
                "execution_profile": str(getattr(edit_contract, "execution_profile", "full_path")),
                "fast_path_enabled": "true" if bool(getattr(edit_contract, "fast_path_enabled", False)) else "false",
                "execution_schedule": execution_schedule.to_dict(),
                "mcp_servers": ",".join(sorted(sonar_mcp_runtime.server_configs)),
                "mcp_tools_count": str(len(sonar_mcp_runtime.tool_names)),
                "mcp_mode": sonar_mcp_runtime.mode,
                "mcp_read_only": "true" if sonar_mcp_runtime.read_only else "false",
                "mcp_warning": sonar_mcp_runtime.warning,
                "system_prompt_chars": str(len(system_prompt)),
                "user_prompt_chars": str(len(user_prompt)),
                "prompt_reference_document": prompt_budget_report.reference_document_path,
                "visible_tools": ",".join(visible_toolset.visible_tools),
                "hidden_tools_count": str(len(visible_toolset.hidden_tools)),
                "helper_extract_runtime_guard": (
                    "true"
                    if "helper_extract" not in tuple(getattr(edit_contract, "allowed_capabilities", ()) or ())
                    else "false"
                ),
            }
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
        attempt_events: list[AttemptRuntimeEvent] = []
        runtime_result: AgentRuntimeResult | None = None
        runtime_performance_metrics: dict[str, Any] = {}
        model_timeout_stage = ""
        patch_salvaged = False
        self._capture_attempt_workspace_state(workspace_path)
        try:
            try:
                runtime_result = self._run_runtime_with_continuation(
                    runtime=runtime,
                    gateway_request=gateway_request,
                    execution_schedule=execution_schedule,
                    workspace_path=workspace_path,
                )
                runtime_performance_metrics = self._build_runtime_performance_metrics(runtime_result)
                attempt_events = list(getattr(runtime_result, "runtime_events", ()) or ())
            except AgentRuntimeError as e:
                runtime_result = e.partial_result
                runtime_performance_metrics = self._build_runtime_performance_metrics(runtime_result)
                attempt_events = list(getattr(runtime_result, "runtime_events", ()) or ())
                error_details = self._format_exception_details(e.cause) or str(e.cause)
                changed_files = tuple(dict.fromkeys(self._collect_modified_files(workspace_path)))
                changes = [{"file": modified_file, "action": "modified"} for modified_file in changed_files]
                model_timeout = (
                    isinstance(e.cause, TimeoutError)
                    or "没有返回首个响应" in error_details
                    or "没有返回后续响应" in error_details
                    or "单个 issue 在" in error_details
                    or "未完成初始化" in error_details
                )
                model_timeout_stage = (
                    str(runtime_performance_metrics.get("model_timeout_stage", "")).strip()
                    or self._infer_timeout_stage(error_details)
                )
                used_forbidden_tool = bool(runtime_result.forbidden_tool_uses) or self._attempt_head_changed(workspace_path)
                build_tool_failed = (
                    runtime_result.last_tool_name == "mcp__sonar-fix__run_build"
                    or (runtime_result.saw_build_tool and "exit code" in error_details.lower())
                )

                if model_timeout and AttemptScheduler.should_salvage_timeout(
                    schedule=execution_schedule,
                    changes_detected=bool(changes),
                    used_forbidden_tool=used_forbidden_tool,
                    build_tool_failed=build_tool_failed,
                ):
                    patch_salvaged = True
                    runtime_performance_metrics = self._merge_performance_metrics(
                        runtime_performance_metrics,
                        patch_salvaged=True,
                        model_timeout_stage=model_timeout_stage,
                    )
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.PATCH_SALVAGED,
                        stage=model_timeout_stage or "follow_up_response_timeout",
                        payload={"reason": "agent_runtime_timeout", "changes_detected": True},
                        runtime_result=runtime_result,
                    )
                    print(
                        "  [TRACE] 检测到 timeout 但 patch 已落盘，进入 salvage 验证流程: "
                        f"stage={model_timeout_stage or 'follow_up_response_timeout'}",
                        flush=True,
                    )
                elif model_timeout:
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="model_timeout",
                        payload={"success": False, "failure_kind": "model_timeout"},
                        runtime_result=runtime_result,
                    )
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
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            patch_salvaged=False,
                            model_timeout_stage=model_timeout_stage,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                            fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                            effective_max_turns=effective_max_turns,
                        ),
                        model_timeout_stage=model_timeout_stage,
                        patch_salvaged=False,
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
                    )

                if used_forbidden_tool:
                    fallback_build_passed, fallback_build_output = self._run_local_build_fallback(
                        workspace_path,
                        resolved_build_command,
                    )
                    output_parts = ["修复阶段使用了被禁止的工具，当前尝试已作废。"]
                    if runtime_result.forbidden_tool_uses:
                        output_parts.append(
                            "禁止工具: " + ", ".join(dict.fromkeys(runtime_result.forbidden_tool_uses))
                        )
                    if self._attempt_head_changed(workspace_path):
                        output_parts.append("检测到当前 attempt 改写了 Git HEAD/提交历史。")
                    output_parts.append(error_details)
                    output_parts.append(fallback_build_output)
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="forbidden_tool",
                        payload={"success": False, "failure_kind": "forbidden_tool"},
                        runtime_result=runtime_result,
                    )
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
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                            fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                            effective_max_turns=effective_max_turns,
                        ),
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
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
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="build_tool",
                        payload={"success": False, "failure_kind": "build_tool"},
                        runtime_result=runtime_result,
                    )
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
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                            fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                            effective_max_turns=effective_max_turns,
                        ),
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
                    )

                if not patch_salvaged:
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="runtime_error",
                        payload={"success": False, "failure_kind": "runtime_error"},
                        runtime_result=runtime_result,
                    )
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=str(file_path),
                        changes=changes,
                        error=error_details,
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                            fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                            effective_max_turns=effective_max_turns,
                        ),
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
                    )
            except Exception as e:
                error_details = self._format_exception_details(e) or str(e)
                changed_files = tuple(dict.fromkeys(self._collect_modified_files(workspace_path)))
                changes = [{"file": modified_file, "action": "modified"} for modified_file in changed_files]
                model_timeout = (
                    isinstance(e, TimeoutError)
                    or "没有返回首个响应" in error_details
                    or "没有返回后续响应" in error_details
                    or "单个 issue 在" in error_details
                    or "未完成初始化" in error_details
                )
                model_timeout_stage = self._infer_timeout_stage(error_details)
                if model_timeout and execution_schedule.patch_salvage_enabled and changes:
                    patch_salvaged = True
                    runtime_result = runtime_result or AgentRuntimeResult(timeout_stage=model_timeout_stage)
                    runtime_performance_metrics = self._merge_performance_metrics(
                        runtime_performance_metrics,
                        patch_salvaged=True,
                        model_timeout_stage=model_timeout_stage,
                    )
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.PATCH_SALVAGED,
                        stage=model_timeout_stage or "follow_up_response_timeout",
                        payload={"reason": "generic_timeout", "changes_detected": True},
                        runtime_result=runtime_result,
                    )
                    print(
                        "  [TRACE] 检测到异常型 timeout 但 patch 已落盘，进入 salvage 验证流程: "
                        f"stage={model_timeout_stage or 'follow_up_response_timeout'}",
                        flush=True,
                    )
                elif model_timeout:
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="model_timeout",
                        payload={"success": False, "failure_kind": "model_timeout"},
                        runtime_result=runtime_result,
                    )
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
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            patch_salvaged=False,
                            model_timeout_stage=model_timeout_stage,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                            fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                            effective_max_turns=effective_max_turns,
                        ),
                        model_timeout_stage=model_timeout_stage,
                        patch_salvaged=False,
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
                    )

                if not patch_salvaged:
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="runtime_error",
                        payload={"success": False, "failure_kind": "runtime_error"},
                        runtime_result=runtime_result,
                    )
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=str(file_path),
                        changes=changes,
                        error=error_details,
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                            execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                            fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                            effective_max_turns=effective_max_turns,
                        ),
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
                    )

            if not changes:
                changed_files = tuple(dict.fromkeys(self._collect_modified_files(workspace_path)))
                changes = [{"file": modified_file, "action": "modified"} for modified_file in changed_files]
            if changes:
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.PATCH_DETECTED,
                    stage="post_runtime_diff",
                    payload={"changed_files": changed_files if 'changed_files' in locals() else [item["file"] for item in changes]},
                    runtime_result=runtime_result,
                )
            invalid_write_tool_input = self._extract_invalid_write_tool_input_message(attempt_events)

            runtime_result = runtime_result or self._run_runtime_with_continuation(
                runtime=runtime,
                gateway_request=gateway_request,
                execution_schedule=execution_schedule,
                workspace_path=workspace_path,
            )
            runtime_performance_metrics = self._merge_performance_metrics(
                runtime_performance_metrics or self._build_runtime_performance_metrics(runtime_result),
                execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                effective_max_turns=effective_max_turns,
                patch_salvaged=patch_salvaged,
                model_timeout_stage=model_timeout_stage,
            )
            if (
                not changes
                and not patch_salvaged
                and not model_timeout_stage
                and not runtime_result.agent_error
                and int(getattr(runtime_result, "continuation_retry_count", 0) or 0) == 0
                and bool(getattr(execution_schedule, "continuation_retry_enabled", False))
            ):
                invalid_write_tool_input = self._extract_invalid_write_tool_input_message(attempt_events)
                if not invalid_write_tool_input:
                    runtime_result = self._run_no_change_continuation(
                        runtime=runtime,
                        gateway_request=gateway_request,
                        initial_result=runtime_result,
                        workspace_path=workspace_path,
                    )
                    attempt_events = list(getattr(runtime_result, "runtime_events", ()) or ())
                    runtime_performance_metrics = self._merge_performance_metrics(
                        self._build_runtime_performance_metrics(runtime_result),
                        execution_profile=str(getattr(edit_contract, "execution_profile", "full_path")),
                        fast_path_enabled=bool(getattr(edit_contract, "fast_path_enabled", False)),
                        effective_max_turns=effective_max_turns,
                        patch_salvaged=patch_salvaged,
                        model_timeout_stage=model_timeout_stage,
                    )
                    invalid_write_tool_input = self._extract_invalid_write_tool_input_message(attempt_events)
                    changed_files = tuple(dict.fromkeys(self._collect_modified_files(workspace_path)))
                    changes = [{"file": modified_file, "action": "modified"} for modified_file in changed_files]
                    if changes:
                        self._append_attempt_event(
                            attempt_events,
                            AttemptRuntimeEventKind.PATCH_DETECTED,
                            stage="post_no_change_continuation_diff",
                            payload={"changed_files": list(changed_files)},
                            runtime_result=runtime_result,
                        )
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
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="forbidden_tool",
                    payload={"success": False, "failure_kind": "forbidden_tool"},
                    runtime_result=runtime_result,
                )
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
                    performance_metrics=self._merge_performance_metrics(
                        runtime_performance_metrics,
                        build_duration_seconds=0.0,
                        build_invoked=False,
                    ),
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            if (
                runtime_result.agent_error
                and not patch_salvaged
                and self._should_salvage_agent_error(
                    agent_error=runtime_result.agent_error,
                    changes_detected=bool(changes),
                    used_forbidden_tool=used_forbidden_tool,
                    invalid_write_tool_input=invalid_write_tool_input,
                )
            ):
                patch_salvaged = True
                salvage_reason = (
                    "agent_error_max_turns"
                    if self._is_max_turn_agent_error(runtime_result.agent_error)
                    else "agent_error_invalid_write_burst"
                )
                runtime_performance_metrics = self._merge_performance_metrics(
                    runtime_performance_metrics,
                    patch_salvaged=True,
                    agent_error_salvaged=True,
                    agent_error_salvage_reason=salvage_reason,
                )
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.PATCH_SALVAGED,
                    stage="agent_error_salvage",
                    payload={"reason": salvage_reason, "changes_detected": True},
                    runtime_result=runtime_result,
                )
                print(
                    "  [TRACE] 检测到可回收的 agent_error，进入 salvage 验证流程: "
                    f"reason={salvage_reason}",
                    flush=True,
                )

            if runtime_result.agent_error and not patch_salvaged:
                if invalid_write_tool_input and not changes:
                    runtime_result = replace(runtime_result, agent_error=None)
                else:
                    runtime_contract_violation = self._classify_runtime_contract_agent_error(
                        runtime_result.agent_error
                    )
                    if runtime_contract_violation is not None:
                        boundary_code, boundary_summary = runtime_contract_violation
                        self._append_attempt_event(
                            attempt_events,
                            AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                            stage="runtime_contract_violation",
                            payload={
                                "success": False,
                                "failure_kind": "runtime_contract_violation",
                                "code": boundary_code,
                            },
                            runtime_result=runtime_result,
                        )
                        return FixResult(
                            success=False,
                            issue_key=issue.key,
                            file_path=str(file_path),
                            changes=changes,
                            error=runtime_result.agent_error,
                            summary="Runtime contract violation requires a narrower retry",
                            build_command=resolved_build_command,
                            build_output=runtime_result.agent_error,
                            failure_kind="runtime_contract_violation",
                            retryable_failure=True,
                            boundary_failure_code=boundary_code,
                            boundary_failure_summary=boundary_summary,
                            performance_metrics=self._merge_performance_metrics(
                                runtime_performance_metrics,
                                build_duration_seconds=0.0,
                                build_invoked=False,
                            ),
                            attempt_events=tuple(attempt_events),
                            **result_metadata,
                        )
                    self._append_attempt_event(
                        attempt_events,
                        AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                        stage="agent_error",
                        payload={"success": False, "failure_kind": "agent_error"},
                        runtime_result=runtime_result,
                    )
                    return FixResult(
                        success=False,
                        issue_key=issue.key,
                        file_path=str(file_path),
                        changes=changes,
                        error=runtime_result.agent_error,
                        build_command=resolved_build_command,
                        build_output=runtime_result.agent_error,
                        failure_kind="agent_error",
                        performance_metrics=self._merge_performance_metrics(
                            runtime_performance_metrics,
                            build_duration_seconds=0.0,
                            build_invoked=False,
                        ),
                        attempt_events=tuple(attempt_events),
                        **result_metadata,
                    )

            if not changes:
                if invalid_write_tool_input:
                    failure_kind = "tool_input_invalid"
                    error = "Model emitted an invalid Edit/MultiEdit/Write call"
                else:
                    failure_kind = "model_timeout" if model_timeout_stage else "no_change"
                    error = "Model response timed out" if model_timeout_stage else "Agent completed without modifying any files"
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage=failure_kind,
                    payload={"success": False, "failure_kind": failure_kind},
                    runtime_result=runtime_result,
                )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    error=error,
                    summary="Fixed 0 file(s)",
                    build_command=resolved_build_command,
                    build_output=invalid_write_tool_input,
                    retryable_failure=True,
                    failure_kind=failure_kind,
                    performance_metrics=self._merge_performance_metrics(
                        runtime_performance_metrics,
                        build_duration_seconds=0.0,
                        build_invoked=False,
                        patch_salvaged=patch_salvaged,
                        model_timeout_stage=model_timeout_stage,
                    ),
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            current_issue_file_content: str | None = None
            if file_path.exists():
                context_cache.invalidate(file_path)
                current_issue_file_content = context_cache.read_text(file_path, encoding="utf-8")

            changed_file_paths = tuple(
                str(change.get("file", "")).replace("\\", "/").lstrip("/")
                for change in changes
                if str(change.get("file", "")).strip()
            )
            fallback_before_texts = dict(non_git_workspace_snapshot)
            issue_rel_path = str(issue.file_path or "").replace("\\", "/").lstrip("/")
            if issue_rel_path and original_issue_file_content is not None:
                fallback_before_texts[issue_rel_path] = original_issue_file_content
            reviewed_changes = self._build_attempt_file_changes(
                workspace_path,
                changed_file_paths,
                fallback_before_texts=(fallback_before_texts or None),
            )
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
            semantic_precheck_result = verification.semantic_precheck_result
            quality_gate_result = verification.quality_gate_result
            review_gate_result = verification.review_gate_result
            post_fix_check_result = verification.post_fix_check_result
            boundary_failure_code = verification.boundary_failure_code
            boundary_failure_summary = verification.boundary_failure_summary
            secondary_boundary_failure_codes = verification.secondary_boundary_failure_codes
            if getattr(performance_flags, "review_gate", True):
                review_gate_summary = str(getattr(review_gate_result, "summary", "")).strip()
                review_gate_invoked = bool(getattr(review_gate_result, "invoked", False))
                if review_gate_invoked:
                    print(
                        "  [TRACE] Review gate: "
                        f"status={getattr(review_gate_result, 'status', '')}, "
                        "invoked=True, "
                        f"findings={len(getattr(review_gate_result, 'findings', ()) or ())}, "
                        f"decisions={len(getattr(review_gate_result, 'decisions', ()) or ())}",
                        flush=True,
                    )
                    if review_gate_summary:
                        print(f"  [TRACE] Review gate summary: {review_gate_summary}", flush=True)
                else:
                    print(
                        "  [TRACE] Optional patch audit skipped: "
                        f"status={getattr(review_gate_result, 'status', '')}, "
                        f"findings={len(getattr(review_gate_result, 'findings', ()) or ())}, "
                        f"decisions={len(getattr(review_gate_result, 'decisions', ()) or ())}",
                        flush=True,
                    )
                    if review_gate_summary:
                        print(
                            f"  [TRACE] Optional patch audit reason: {review_gate_summary}",
                            flush=True,
                        )
                review_gate_model = str(getattr(review_gate_result, "model_display", "")).strip()
                if review_gate_invoked and review_gate_model:
                    print(f"  [TRACE] Review gate model: {review_gate_model}", flush=True)
                if review_gate_invoked:
                    waived_count = sum(
                        1
                        for item in (getattr(review_gate_result, "decisions", ()) or ())
                        if str(getattr(item, "decision", "")).strip().lower() == "waive"
                    )
                    confirmed_count = sum(
                        1
                        for item in (getattr(review_gate_result, "decisions", ()) or ())
                        if str(getattr(item, "decision", "")).strip().lower() != "waive"
                    )
                    print(
                        "  [TRACE] Review gate verdict: "
                        f"waived={waived_count}, confirmed={confirmed_count}",
                        flush=True,
                    )
                    for item in tuple(getattr(review_gate_result, "feedback", ()) or ())[:3]:
                        text = str(item).strip()
                        if text:
                            print(f"  [TRACE] Review gate feedback: {text}", flush=True)
            performance_metrics = self._merge_performance_metrics(
                runtime_performance_metrics,
                execution_mode=execution_mode,
                fast_compile_invoked=verification.fast_compile_invoked,
                fast_compile_passed=verification.fast_compile_passed,
                fast_compile_duration_seconds=verification.fast_compile_duration_seconds,
                fast_compile_command=verification.fast_compile_command,
                build_duration_seconds=verification.build_duration_seconds,
                build_invoked=verification.build_invoked,
                validation_pipeline=tuple(edit_contract.validation_plan),
                verification_schedule=AttemptScheduler.build_verification_schedule(
                    edit_contract=edit_contract,
                    performance_flags=performance_flags,
                ).to_dict(),
                patch_salvaged=patch_salvaged,
                model_timeout_stage=model_timeout_stage,
            )
            if verification.build_invoked:
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.BUILD_STARTED,
                    stage="verification_build",
                    payload={"build_command": resolved_build_command},
                    runtime_result=runtime_result,
                )
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.BUILD_FINISHED,
                    stage="verification_build",
                    payload={
                        "build_passed": build_passed,
                        "duration_seconds": verification.build_duration_seconds,
                    },
                    runtime_result=runtime_result,
                )

            if reviewer_result.status == "retry":
                failure_stage = (
                    "filesystem_boundary"
                    if str(boundary_failure_code).startswith("filesystem_")
                    else "reviewer"
                )
                failure_error = (
                    "Filesystem boundary rejected the patch"
                    if failure_stage == "filesystem_boundary"
                    else "Diff reviewer rejected the patch"
                )
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.BOUNDARY_REJECTED,
                    stage=failure_stage,
                    payload={"code": boundary_failure_code, "summary": boundary_failure_summary},
                    runtime_result=runtime_result,
                )
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage=failure_stage,
                    payload={"success": False, "failure_kind": "reviewer"},
                    runtime_result=runtime_result,
                )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error=failure_error,
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=verification.reviewer_retry_message or verification.combined_output,
                    retryable_failure=True,
                    failure_kind="reviewer",
                    reviewer_result=reviewer_result.to_dict(),
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            if semantic_precheck_result.status == "retry":
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="semantic_precheck",
                    payload={"success": False, "failure_kind": "semantic_precheck"},
                    runtime_result=runtime_result,
                )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Semantic precheck failed",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=semantic_precheck_result.to_retry_message(),
                    retryable_failure=True,
                    failure_kind="semantic_precheck",
                    reviewer_result=reviewer_result.to_dict(),
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            if review_gate_result.status == "retry":
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="review_gate",
                    payload={"success": False, "failure_kind": "review_gate"},
                    runtime_result=runtime_result,
                )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Review gate verification failed",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=review_gate_result.to_retry_message(),
                    retryable_failure=True,
                    failure_kind="review_gate",
                    reviewer_result=reviewer_result.to_dict(),
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            if quality_gate_result.status == "retry":
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.QUALITY_GATE_REJECTED,
                    stage="quality_gate",
                    payload={"summary": quality_gate_result.summary, "violations": len(quality_gate_result.violations)},
                    runtime_result=runtime_result,
                )
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="quality_gate",
                    payload={"success": False, "failure_kind": "quality_gate"},
                    runtime_result=runtime_result,
                )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Quality gate verification failed",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=quality_gate_result.to_retry_message(),
                    retryable_failure=True,
                    failure_kind="quality_gate",
                    reviewer_result=reviewer_result.to_dict(),
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            if (
                is_simple_loop_execution_mode(execution_mode)
                and str(getattr(post_fix_check_result, "issue_status", "")).strip() == "FAIL"
            ):
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="post_fix_check",
                    payload={"success": False, "failure_kind": "post_fix_check"},
                    runtime_result=runtime_result,
                )
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    build_passed=build_passed,
                    build_verification_failed=False,
                    error="Simple-loop post-fix check failed",
                    summary=f"Fixed {len(changes)} file(s)",
                    build_command=resolved_build_command,
                    build_output=verification.combined_output,
                    retryable_failure=True,
                    failure_kind="post_fix_check",
                    reviewer_result=reviewer_result.to_dict(),
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    post_fix_check_result=post_fix_check_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            rule_validation_message = verification.rule_validation_message
            if rule_validation_message:
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="rule_validation",
                    payload={"success": False, "failure_kind": "rule_validation"},
                    runtime_result=runtime_result,
                )
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
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    post_fix_check_result=post_fix_check_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            if not build_passed:
                self._append_attempt_event(
                    attempt_events,
                    AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                    stage="build",
                    payload={"success": False, "failure_kind": "build"},
                    runtime_result=runtime_result,
                )
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
                    semantic_precheck_result=semantic_precheck_result.to_dict(),
                    quality_gate_result=quality_gate_result.to_dict(),
                    review_gate_result=review_gate_result.to_dict(),
                    post_fix_check_result=post_fix_check_result.to_dict(),
                    follow_ups=reviewer_result.follow_ups,
                    boundary_failure_code=boundary_failure_code,
                    boundary_failure_summary=boundary_failure_summary,
                    secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                    performance_metrics=performance_metrics,
                    model_timeout_stage=model_timeout_stage,
                    patch_salvaged=patch_salvaged,
                    attempt_events=tuple(attempt_events),
                    **result_metadata,
                )

            self._append_attempt_event(
                attempt_events,
                AttemptRuntimeEventKind.ATTEMPT_FINISHED,
                stage="succeeded",
                payload={"success": True, "failure_kind": ""},
                runtime_result=runtime_result,
            )
            return FixResult(
                success=True,
                issue_key=issue.key,
                file_path=str(file_path),
                changes=changes,
                build_passed=build_passed,
                summary=(
                    f"Fixed {len(changes)} file(s)"
                    if str(getattr(post_fix_check_result, "issue_status", "")).strip() != "UNKNOWN"
                    else f"Fixed {len(changes)} file(s); local issue status is UNKNOWN and awaits final Sonar confirmation"
                ),
                build_command=resolved_build_command,
                build_output=build_output,
                reviewer_result=reviewer_result.to_dict(),
                semantic_precheck_result=semantic_precheck_result.to_dict(),
                quality_gate_result=quality_gate_result.to_dict(),
                review_gate_result=review_gate_result.to_dict(),
                post_fix_check_result=post_fix_check_result.to_dict(),
                follow_ups=reviewer_result.follow_ups,
                boundary_failure_code=boundary_failure_code,
                boundary_failure_summary=boundary_failure_summary,
                secondary_boundary_failure_codes=secondary_boundary_failure_codes,
                performance_metrics=performance_metrics,
                model_timeout_stage=model_timeout_stage,
                patch_salvaged=patch_salvaged,
                attempt_events=tuple(attempt_events),
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
