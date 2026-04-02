"""Claude Code SDK based Agent for fixing SonarQube issues.

This module provides the main agent class that:
1. Connects to SonarQube to get issues
2. Uses Claude Code to analyze and fix code issues
3. Runs build/test to verify fixes
4. Creates PR in Azure DevOps
"""

import asyncio
import difflib
import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    CONDITIONAL_CHAIN_SCOPE_MODE,
    CONTROL_BLOCK_SCOPE_MODE,
    DECLARATION_COMMENT_SCOPE_MODE,
    EXPRESSION_REWRITE_SCOPE_MODE,
    LOOP_REWRITE_SCOPE_MODE,
    METHOD_SCOPE_MODE,
    STATEMENT_SCOPE_MODE,
    get_rule_policy,
)
from pi_sonar_agent.agent.rule_validators import validate_rule_fix

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
    retryable_failure: bool = False
    failure_kind: str = ""


@dataclass(frozen=True)
class IssueEditScope:
    """Allowed edit scope for a single Sonar issue."""

    start_line: int
    end_line: int
    validation_start_line: int
    validation_end_line: int
    mode: str


# ============== System Prompts ==============

SONAR_FIX_SYSTEM_PROMPT = """你是一个极其严格的 .NET/C# 架构师，专门负责修复 SonarQube 代码质量问题。

你的任务：
1. 仔细分析 SonarQube 报告的代码问题
2. 理解问题的根本原因
3. 应用最小化、精确的修复
4. 确保修复不会破坏现有功能

修复原则：
1. 最小改动：只修改必须修改的代码，不要做无关的格式化或重构
2. 安全第一：确保修复后代码能编译通过
3. 保持语义：不改变方法的业务逻辑
4. 使用工具：先读取文件，理解上下文，再进行修复
5. 禁止污染：绝对不要使用 Bash、git add、git commit、git push 或任何自行提交/推送操作
6. 构建由外层流程统一执行：你只负责代码修改，不要自行尝试运行构建或测试

重要约束：
- 绝对不要使用省略号(...)或注释掉代码
- 绝对不要删除整个方法或类
- 修复后如果可能，运行 build 验证
- 不要在修复阶段执行任何 git 提交、push 或 shell 命令；提交由外层流程统一处理
- 外层流程会在你完成修改后自动运行推荐构建命令并根据错误信息重试；你不需要自己运行构建工具
- 如果不确定如何修复，调用 finish 并说明原因

工作流程：
1. 使用 read_file 工具读取有问题 的源文件
2. 使用 apply_edit 工具进行精确修复
3. 完成必要修改后直接结束，外层流程会自动进行构建验证
4. 使用 finish 工具标记完成
"""


SONAR_FIX_USER_PROMPT_TEMPLATE = """请修复以下 SonarQube 代码问题：

【问题详情】
- Issue Key: {issue_key}
- 规则ID: {rule_id}
- 规则名称: {rule_name}
- 问题描述: {message}
- 严重程度: {severity}
- 文件路径: {file_path}
- 报错行号: {line}

【SonarQube 规则说明（问题原因/风险）】
{rule_description}

【SonarQube 修复建议】
{rule_fix_guidance}

【代码上下文】（包含问题行及前后代码）
{code_context}

{quality_gate_section}
{rule_guard_section}

【允许修改范围】
{scope_guidance}

【推荐构建命令】
{build_command}

{retry_feedback_section}

请按照以下步骤操作：
1. 读取源文件 {file_path} 确认问题
2. 分析问题的根本原因
3. 应用精确修复
4. 完成后直接结束；外层流程会自动使用上述推荐构建命令验证修复
5. 调用 finish 标记完成

注意：
- 当前只处理这个 Issue Key，不要扩展修复同文件、同方法里的其他 issue
- 只修复本问题，不要做无关改动
- 不要顺手修复本文件中其他位置的相同规则问题
- 严禁使用 Bash、git_add、git_commit、git_push 或任何自行提交/推送动作
- 不要调用 git 工具污染当前工作区基线；只允许直接修改文件
- 外层流程会负责 build/test/retry，不要自行尝试运行构建或测试
- 保持代码风格一致
- 确保修复后能编译通过
"""


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

THIRD_PARTY_MODEL_ENV_KEYS = (
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "CLAUDE_MODEL",
    "OPENAI_MODEL",
)


CONTROL_FLOW_PREFIXES = (
    "if",
    "else",
    "for",
    "foreach",
    "while",
    "switch",
    "case",
    "catch",
    "using",
    "lock",
    "return",
    "do",
    "try",
)


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

        endpoint = (
            agent_env.get("ANTHROPIC_BASE_URL")
            or agent_env.get("OPENAI_BASE_URL")
            or ""
        ).strip()
        return endpoint or "(sdk default)"

    @staticmethod
    def _display_agent_model(agent_env: dict[str, str], explicit_model: str | None) -> str:
        """Build a safe model string for run logs."""

        candidates = [
            explicit_model,
            agent_env.get("ANTHROPIC_CUSTOM_MODEL_OPTION"),
            agent_env.get("CLAUDE_MODEL"),
            agent_env.get("OPENAI_MODEL"),
            agent_env.get("ANTHROPIC_DEFAULT_SONNET_MODEL"),
        ]
        for item in candidates:
            value = str(item or "").strip()
            if value:
                return value
        return "(sdk default)"

    @staticmethod
    def _uses_third_party_anthropic_provider(agent_env: dict[str, str]) -> bool:
        """Return True when the configured Anthropic endpoint is not first-party."""

        base_url = (agent_env.get("ANTHROPIC_BASE_URL") or "").strip()
        if not base_url:
            return False

        parsed = urlparse(base_url)
        host = (parsed.netloc or "").lower()
        if not host:
            return False

        return not (host.endswith("anthropic.com") or host.endswith("claude.ai"))

    @classmethod
    def _build_agent_extra_args(cls, agent_env: dict[str, str]) -> dict[str, Any]:
        """Build extra Claude CLI arguments for provider-specific compatibility."""

        if cls._uses_third_party_anthropic_provider(agent_env):
            # Third-party Anthropic-compatible providers often conflict with the user's
            # local Claude settings/plugins/hooks. `--bare` forces the CLI to rely only
            # on the env/settings we pass in for this run.
            return {"bare": None}
        return {}

    @classmethod
    def _build_sdk_child_env(cls, agent_env: dict[str, str]) -> dict[str, str]:
        """Sanitize the env passed to Claude CLI for provider-specific compatibility."""

        child_env = {key: value for key, value in agent_env.items() if str(value).strip()}
        if cls._uses_third_party_anthropic_provider(agent_env):
            for key in THIRD_PARTY_MODEL_ENV_KEYS:
                child_env.pop(key, None)
        return child_env

    @classmethod
    def _resolve_sdk_model(
        cls,
        agent_env: dict[str, str],
        child_env: dict[str, str],
        explicit_model: str | None,
    ) -> str | None:
        """Resolve how the Claude CLI should receive the selected model."""

        if cls._uses_third_party_anthropic_provider(agent_env):
            model_value = str(explicit_model or "").strip()
            if model_value:
                child_env["CLAUDE_MODEL"] = model_value
            return None
        return explicit_model

    @staticmethod
    def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
        """Combine subprocess streams into a single safe string."""

        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        return f"{stdout}{stderr}"

    @staticmethod
    def _normalize_exception_text(value: Any) -> str:
        """Normalize exception-related text values safely."""

        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace").strip()
        return str(value).strip()

    @classmethod
    def _format_exception_details(cls, exc: BaseException) -> str:
        """Collect the most useful exception details, including stderr/stdout when present."""

        parts: list[str] = []
        primary = cls._normalize_exception_text(exc)
        if primary:
            parts.append(primary)

        for attr_name, label in (
            ("stderr", "STDERR"),
            ("stdout", "STDOUT"),
            ("output", "OUTPUT"),
        ):
            value = cls._normalize_exception_text(getattr(exc, attr_name, ""))
            if value:
                parts.append(f"{label}:\n{value}")

        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            cause_text = cls._normalize_exception_text(cause)
            if cause_text and cause_text not in parts:
                parts.append(f"CAUSE:\n{cause_text}")

        return "\n\n".join(dict.fromkeys(item for item in parts if item))

    @classmethod
    def _run_local_build_fallback(
        cls,
        workspace_path: Path,
        build_command: str,
    ) -> tuple[bool, str]:
        """Run a local fallback build when the model-triggered build tool crashes."""

        try:
            result = subprocess.run(
                build_command,
                shell=True,
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except Exception as exc:
            return False, f"本地回退构建也失败：\n{cls._format_exception_details(exc)}"

        output = cls._combine_process_output(result).strip()
        header = f"本地回退构建 Exit code: {result.returncode}"
        if output:
            return result.returncode == 0, f"{header}\n\n{output}"
        return result.returncode == 0, header

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

        text = str(value or "").strip()
        return text if text else fallback

    @staticmethod
    def _strip_quality_gate_front_matter(text: str) -> str:
        """Strip optional YAML front matter from a quality-gate markdown file."""

        normalized = str(text or "").strip()
        if not normalized.startswith("---"):
            return normalized

        lines = normalized.splitlines()
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return "\n".join(lines[index + 1:]).strip()
        return normalized

    @classmethod
    def _load_csharp_quality_gate(cls, issue: SonarIssue) -> str:
        """Load the C# quality gate for C# source files."""

        if not issue.file_path.lower().endswith(".cs"):
            return ""
        for path in cls.QUALITY_GATE_PATHS:
            try:
                if not path.exists():
                    continue
                gate_text = cls._strip_quality_gate_front_matter(
                    path.read_text(encoding="utf-8", errors="replace")
                )
                gate_text = gate_text.strip()
                if not gate_text:
                    continue
                return f"{gate_text}\n\n{cls.QUALITY_GATE_SUPPLEMENT}".strip()
            except Exception:
                continue
        return ""

    @staticmethod
    def _looks_like_method_signature(header_text: str) -> bool:
        """Best-effort detection for a C# method signature."""

        normalized = " ".join(str(header_text or "").split())
        if "(" not in normalized or ")" not in normalized:
            return False

        lower = normalized.lower()
        if any(lower.startswith(f"{prefix} ") for prefix in CONTROL_FLOW_PREFIXES):
            return False
        if normalized.endswith(";"):
            return False
        return "=" not in normalized.split("(", 1)[0] or any(
            token in lower
            for token in ("public ", "private ", "protected ", "internal ", "async ", "static ")
        )

    @classmethod
    def _find_enclosing_method_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        """Find the containing method range for the issue line."""

        if not lines:
            return None

        search_start = max(1, issue_line - 80)
        search_end = min(len(lines), issue_line + 5)

        for candidate in range(issue_line, search_start - 1, -1):
            header_end = min(len(lines), candidate + 3)
            header_text = " ".join(line.strip() for line in lines[candidate - 1:header_end] if line.strip())
            if not cls._looks_like_method_signature(header_text):
                continue

            brace_line = None
            for line_number in range(candidate, min(len(lines), candidate + 5) + 1):
                if "{" in lines[line_number - 1]:
                    brace_line = line_number
                    break
            if brace_line is None or brace_line > search_end:
                continue

            depth = 0
            started = False
            for line_number in range(brace_line, len(lines) + 1):
                current = lines[line_number - 1]
                depth += current.count("{")
                if current.count("{"):
                    started = True
                depth -= current.count("}")
                if started and depth <= 0:
                    return candidate, line_number

        return None

    @staticmethod
    def _find_next_non_empty_line(lines: list[str], start_line: int) -> int | None:
        """Find the next non-empty line at or after the given line number."""

        for line_number in range(max(start_line, 1), len(lines) + 1):
            if lines[line_number - 1].strip():
                return line_number
        return None

    @staticmethod
    def _line_starts_with_keyword(line_text: str, *keywords: str) -> bool:
        """Return True when the stripped line starts with one of the keywords."""

        stripped = str(line_text or "").strip()
        return any(
            stripped == keyword
            or stripped.startswith(f"{keyword} ")
            or stripped.startswith(f"{keyword}(")
            for keyword in keywords
        )

    @classmethod
    def _looks_like_control_statement_header(cls, line_text: str) -> bool:
        """Best-effort detection for control-statement headers."""

        return cls._line_starts_with_keyword(
            line_text,
            "if",
            "else if",
            "else",
            "for",
            "foreach",
            "while",
            "using",
        )

    @classmethod
    def _find_control_header_end(cls, lines: list[str], start_line: int) -> int:
        """Find the ending line of a possibly multi-line control header."""

        total_lines = len(lines)
        paren_depth = 0
        saw_paren = False

        for line_number in range(start_line, min(total_lines, start_line + 12) + 1):
            current = lines[line_number - 1]
            stripped = current.strip()

            if stripped.startswith("else") and "if" not in stripped:
                return line_number

            paren_depth += current.count("(")
            if current.count("("):
                saw_paren = True
            paren_depth -= current.count(")")

            if saw_paren and paren_depth <= 0 and ")" in current:
                return line_number

        return start_line

    @staticmethod
    def _find_matching_brace_end(lines: list[str], brace_line: int) -> int | None:
        """Find the closing brace line for a block that starts at brace_line."""

        depth = 0
        started = False
        for line_number in range(brace_line, len(lines) + 1):
            current = lines[line_number - 1]
            depth += current.count("{")
            if current.count("{"):
                started = True
            depth -= current.count("}")
            if started and depth <= 0:
                return line_number
        return None

    @classmethod
    def _find_statement_end_from_line(cls, lines: list[str], start_line: int) -> int:
        """Find the end line for a single statement starting at start_line."""

        end_line = min(max(start_line, 1), len(lines))
        while end_line < len(lines):
            current = lines[end_line - 1]
            if cls._is_statement_boundary(current):
                break
            end_line += 1
        return end_line

    @classmethod
    def _find_control_statement_range_from_header(
        cls,
        lines: list[str],
        header_start: int,
    ) -> tuple[int, int]:
        """Find the full logical range of a control statement from its header."""

        total_lines = len(lines)
        header_end = cls._find_control_header_end(lines, header_start)
        brace_start = None

        for line_number in range(header_start, min(total_lines, header_end + 2) + 1):
            if "{" in lines[line_number - 1]:
                brace_start = line_number
                break

        if brace_start is not None:
            brace_end = cls._find_matching_brace_end(lines, brace_start)
            if brace_end is not None:
                return header_start, brace_end

        body_start = cls._find_next_non_empty_line(lines, header_end + 1)
        if body_start is None:
            return header_start, header_end

        if lines[body_start - 1].strip().startswith("{"):
            brace_end = cls._find_matching_brace_end(lines, body_start)
            if brace_end is not None:
                return header_start, brace_end

        return header_start, cls._find_statement_end_from_line(lines, body_start)

    @classmethod
    def _find_control_statement_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        """Find the surrounding control statement range for the issue line."""

        search_start = max(1, issue_line - 8)
        for candidate in range(issue_line, search_start - 1, -1):
            if not cls._looks_like_control_statement_header(lines[candidate - 1]):
                continue
            return cls._find_control_statement_range_from_header(lines, candidate)
        return None

    @staticmethod
    def _looks_like_attribute_line(line_text: str) -> bool:
        """Return True when the line looks like a C# attribute line."""

        stripped = str(line_text or "").strip()
        return stripped.startswith("[") or stripped.endswith("]")

    @classmethod
    def _find_declaration_comment_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int]:
        """Find the declaration range for XML comment related fixes."""

        total_lines = len(lines)
        declaration_start = min(max(issue_line, 1), total_lines)

        while declaration_start > 1 and cls._looks_like_attribute_line(lines[declaration_start - 2]):
            declaration_start -= 1

        declaration_end = declaration_start
        while declaration_end < total_lines:
            current = lines[declaration_end - 1].strip()
            if current.endswith("{") or current.endswith(";") or current.endswith("=>"):
                break
            declaration_end += 1

        return declaration_start, declaration_end

    @classmethod
    def _find_conditional_chain_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        """Find the full if/else chain range around the issue line."""

        search_start = max(1, issue_line - 12)
        candidate = None
        for line_number in range(issue_line, search_start - 1, -1):
            if cls._line_starts_with_keyword(lines[line_number - 1], "if", "else if"):
                candidate = line_number
                break

        if candidate is None:
            return None

        if cls._line_starts_with_keyword(lines[candidate - 1], "else", "else if"):
            for line_number in range(candidate - 1, search_start - 1, -1):
                if not cls._line_starts_with_keyword(lines[line_number - 1], "if"):
                    continue
                _, branch_end = cls._find_control_statement_range_from_header(lines, line_number)
                next_line = cls._find_next_non_empty_line(lines, branch_end + 1)
                if next_line == candidate:
                    candidate = line_number
                    break

        for line_number in range(candidate - 1, search_start - 1, -1):
            if not cls._line_starts_with_keyword(lines[line_number - 1], "if"):
                continue
            outer_start, outer_end = cls._find_control_statement_range_from_header(lines, line_number)
            if outer_start <= candidate <= outer_end:
                candidate = line_number

        chain_start = candidate
        _, chain_end = cls._find_control_statement_range_from_header(lines, chain_start)
        cursor = chain_end + 1

        while True:
            next_line = cls._find_next_non_empty_line(lines, cursor)
            if next_line is None or not cls._line_starts_with_keyword(lines[next_line - 1], "else", "else if"):
                break
            _, chain_end = cls._find_control_statement_range_from_header(lines, next_line)
            cursor = chain_end + 1

        return chain_start, chain_end

    @staticmethod
    def _looks_like_expression_rewrite_anchor(line_text: str) -> bool:
        """Return True when the line is a good anchor for a local expression rewrite."""

        stripped = str(line_text or "").strip()
        if not stripped:
            return False
        return (
            "=>" in stripped
            or stripped in {"{", "("}
            or stripped.endswith("=")
            or stripped.endswith("=>")
            or stripped.endswith("return")
        )

    @classmethod
    def _find_expression_rewrite_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int]:
        """Find a nearby expression range that can be safely rewritten into statements."""

        start_line, end_line = cls._find_enclosing_statement_range(lines, issue_line)
        search_start = max(1, start_line - 8)
        rewrite_start = start_line

        for candidate in range(start_line - 1, search_start - 1, -1):
            current = lines[candidate - 1].strip()
            if not current:
                rewrite_start = candidate
                continue
            if cls._looks_like_expression_rewrite_anchor(current):
                rewrite_start = candidate
                continue
            break

        return rewrite_start, end_line

    @classmethod
    def _find_loop_rewrite_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int] | None:
        """Find a loop range that may also absorb the immediately-following return/throw."""

        search_start = max(1, issue_line - 8)
        for candidate in range(issue_line, search_start - 1, -1):
            if not cls._line_starts_with_keyword(lines[candidate - 1], "for", "foreach", "while"):
                continue

            loop_start, loop_end = cls._find_control_statement_range_from_header(lines, candidate)
            rewrite_end = loop_end
            next_line = cls._find_next_non_empty_line(lines, loop_end + 1)
            if next_line is not None and cls._line_starts_with_keyword(
                lines[next_line - 1],
                "return",
                "throw",
            ):
                rewrite_end = cls._find_statement_end_from_line(lines, next_line)
            return loop_start, rewrite_end

        return None

    @staticmethod
    def _is_statement_boundary(line_text: str) -> bool:
        """Return True when the line looks like a C# statement boundary."""

        stripped = str(line_text or "").strip()
        if not stripped:
            return False
        return (
            stripped.endswith(";")
            or stripped.endswith("{")
            or stripped.endswith("}")
            or stripped.startswith("#")
        )

    @classmethod
    def _find_enclosing_statement_range(
        cls,
        lines: list[str],
        issue_line: int,
    ) -> tuple[int, int]:
        """Find a narrow statement-level edit range around the issue line."""

        total_lines = max(len(lines), 1)
        start_line = min(max(issue_line, 1), total_lines)
        end_line = start_line

        while start_line > 1:
            previous = lines[start_line - 2]
            if cls._is_statement_boundary(previous):
                break
            start_line -= 1

        while end_line < total_lines:
            current = lines[end_line - 1]
            if cls._is_statement_boundary(current):
                break
            end_line += 1

        return start_line, end_line

    @classmethod
    def _build_issue_edit_scope(
        cls,
        issue: SonarIssue,
        lines: list[str],
    ) -> IssueEditScope:
        """Build the allowed edit scope for the issue."""

        total_lines = max(len(lines), 1)
        policy = get_rule_policy(issue.rule)
        scope_mode = policy.scope_mode
        logical_range: tuple[int, int] | None = None

        if scope_mode == METHOD_SCOPE_MODE:
            logical_range = cls._find_enclosing_method_range(lines, issue.line)
        elif scope_mode == CONTROL_BLOCK_SCOPE_MODE:
            logical_range = cls._find_control_statement_range(lines, issue.line)
        elif scope_mode == DECLARATION_COMMENT_SCOPE_MODE:
            logical_range = cls._find_declaration_comment_range(lines, issue.line)
        elif scope_mode == CONDITIONAL_CHAIN_SCOPE_MODE:
            logical_range = cls._find_conditional_chain_range(lines, issue.line)
        elif scope_mode == EXPRESSION_REWRITE_SCOPE_MODE:
            logical_range = cls._find_expression_rewrite_range(lines, issue.line)
        elif scope_mode == LOOP_REWRITE_SCOPE_MODE:
            logical_range = cls._find_loop_rewrite_range(lines, issue.line)

        if logical_range is None:
            logical_range = cls._find_enclosing_statement_range(lines, issue.line)
            scope_mode = STATEMENT_SCOPE_MODE
            validation_start_line = logical_range[0]
            validation_end_line = logical_range[1]
        else:
            validation_start_line = max(1, logical_range[0] - policy.validation_leading_lines)
            validation_end_line = min(total_lines, logical_range[1] + policy.validation_trailing_lines)

        start_line, end_line = logical_range
        return IssueEditScope(
            start_line=start_line,
            end_line=end_line,
            validation_start_line=validation_start_line,
            validation_end_line=validation_end_line,
            mode=scope_mode,
        )

    @staticmethod
    def _build_scope_guidance(issue: SonarIssue, scope: IssueEditScope | None) -> str:
        """Render edit-scope guidance for the model prompt."""

        if scope is None:
            return (
                "- 只允许修改 SonarQube 指向的那一处问题。\n"
                "- 不要顺手修复本文件中其他相同规则或相同写法的问题。"
            )

        if scope.mode == METHOD_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的目标方法。\n"
                f"- 如果必须提取 private 辅助方法，只能新增在该方法后方紧邻区域，且不要超过第 {scope.validation_end_line} 行。\n"
                "- 新增的辅助方法只能服务当前 issue 对应的方法，不要改动本文件其他方法中的同类问题。"
            )

        if scope.mode == CONTROL_BLOCK_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的当前控制语句及其直接代码块。\n"
                "- 如果需要补大括号，只能在这段控制语句周围新增必要的 { }，不要扩展到旁边的分支、循环或其他语句。"
            )

        if scope.mode == DECLARATION_COMMENT_SCOPE_MODE:
            return (
                f"- 只允许在第 {scope.start_line}-{scope.end_line} 行对应的公开成员声明前添加或调整 XML 注释。\n"
                "- 注释必须紧贴当前声明或其 attribute，不要顺手修改其他成员的注释内容。"
            )

        if scope.mode == CONDITIONAL_CHAIN_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的当前 if/else 条件链。\n"
                "- 只调整这条条件链本身，不要顺手改方法里的其他条件分支。"
            )

        if scope.mode == EXPRESSION_REWRITE_SCOPE_MODE:
            return (
                f"- 只允许在第 {scope.validation_start_line}-{scope.validation_end_line} 行附近重写当前表达式，核心问题位于第 {scope.start_line}-{scope.end_line} 行。\n"
                "- 优先把当前嵌套 ?: 改成局部变量、if/else 或语句 lambda，然后在原位置回填结果。\n"
                "- 不要新增类级 private/helper 方法，不要把辅助逻辑提到类尾、其他方法或同文件其他位置。"
            )

        if scope.mode == LOOP_REWRITE_SCOPE_MODE:
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的当前循环改写范围。\n"
                "- 可以把当前 foreach/for/while 重写成 LINQ、Any、FirstOrDefault 等等价表达式。\n"
                "- 如果循环后紧跟着与该查找/过滤逻辑配套的 return 或 throw，也可以一并改写，但不要扩展到后续无关语句。"
            )

        return (
            f"- 只允许修改包含 issue 行的这条语句，当前允许范围是第 {scope.start_line}-{scope.end_line} 行。\n"
            "- 如果本文件其他地方也有相同写法或相同规则问题，不要顺手修改。"
        )

    @staticmethod
    def _build_rule_guard_section(issue: SonarIssue) -> str:
        """Render rule-specific prompt guards when configured."""

        guards = get_rule_policy(issue.rule).prompt_guards
        if not guards:
            return ""

        lines = ["【当前规则的额外约束】"]
        lines.extend(f"- {item}" for item in guards)
        return "\n".join(lines)

    @staticmethod
    def _get_rule_skip_reason(issue: SonarIssue) -> str:
        """Return the default skip reason for a rule, if any."""

        return get_rule_policy(issue.rule).skip_reason

    @staticmethod
    def _run_rule_specific_validation(issue: SonarIssue, file_content: str) -> str:
        """Run post-fix local validation for rules that support it."""

        policy = get_rule_policy(issue.rule)
        return validate_rule_fix(
            validator_name=policy.local_validator,
            issue_line=issue.line,
            file_content=file_content,
        )

    @staticmethod
    def _extract_changed_line_numbers(diff_text: str) -> set[int]:
        """Extract changed target line numbers from unified diff text."""

        pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
        changed_lines: set[int] = set()
        for raw_line in (diff_text or "").splitlines():
            match = pattern.match(raw_line.strip())
            if not match:
                continue
            start = int(match.group("start"))
            count = int(match.group("count") or "1")
            if count <= 0:
                changed_lines.add(start)
                continue
            changed_lines.update(range(start, start + count))
        return changed_lines

    @staticmethod
    def _find_out_of_scope_lines(scope: IssueEditScope, changed_lines: set[int]) -> list[int]:
        """Find changed lines that exceed the allowed issue scope."""

        return sorted(
            line
            for line in changed_lines
            if line < scope.validation_start_line or line > scope.validation_end_line
        )

    @staticmethod
    def _build_content_diff(
        original_content: str,
        current_content: str,
        relative_path: str,
    ) -> str:
        """Build a unified diff for the current attempt only."""

        return "\n".join(
            difflib.unified_diff(
                original_content.splitlines(),
                current_content.splitlines(),
                fromfile=relative_path,
                tofile=relative_path,
                n=0,
                lineterm="",
            )
        )

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

        if scope is None:
            return None

        relative_path = issue.file_path.lstrip("/").replace("\\", "/")
        diff_text = ""
        if original_content is not None and current_content is not None:
            diff_text = cls._build_content_diff(original_content, current_content, relative_path)
        else:
            try:
                result = subprocess.run(
                    ["git", "diff", "--unified=0", "--", relative_path],
                    cwd=str(workspace_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            except Exception:
                return None

            if result.returncode != 0:
                return None
            diff_text = result.stdout

        changed_lines = cls._extract_changed_line_numbers(diff_text)
        if not changed_lines:
            return None

        offending_lines = cls._find_out_of_scope_lines(scope, changed_lines)
        if not offending_lines:
            return None

        offending_text = ", ".join(str(line) for line in offending_lines[:12])
        return (
            "Issue changes exceeded the allowed Sonar edit scope.\n"
            f"Allowed lines: {scope.validation_start_line}-{scope.validation_end_line}\n"
            f"Changed lines outside scope: {offending_text}\n"
            "只允许修复 Sonar 指向的这一处代码，不要顺手修改本文件其他同类位置。"
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
    ) -> str:
        """Build the issue-specific user prompt."""

        retry_feedback_text = cls._normalize_prompt_text(retry_feedback, "").strip()
        retry_feedback_section = ""
        if retry_feedback_text:
            retry_feedback_section = (
                "【上次尝试的构建失败信息】\n"
                f"{retry_feedback_text}\n\n"
                "请基于这些失败原因重新修复，避免再次引入相同的编译错误。"
            )

        quality_gate_section = ""
        quality_gate = cls._normalize_prompt_text(quality_gate_text, "").strip()
        if quality_gate:
            quality_gate_section = f"【C# 代码质量门禁】\n{quality_gate}"

        rule_guard_section = cls._build_rule_guard_section(issue)

        return SONAR_FIX_USER_PROMPT_TEMPLATE.format(
            issue_key=issue.key,
            rule_id=issue.rule,
            rule_name=cls._normalize_prompt_text(rule_details.get("name", ""), "未提供"),
            message=issue.message,
            severity=issue.severity,
            file_path=issue.file_path,
            line=issue.line,
            rule_description=cls._normalize_prompt_text(
                rule_details.get("description", ""),
                "SonarQube 未返回规则说明，请结合问题描述和代码上下文分析根因。",
            ),
            rule_fix_guidance=cls._normalize_prompt_text(
                rule_details.get("how_to_fix", ""),
                "SonarQube 未返回修复建议，请基于规则说明进行最小化修复。",
            ),
            code_context=code_context,
            quality_gate_section=quality_gate_section,
            rule_guard_section=rule_guard_section,
            scope_guidance=cls._normalize_prompt_text(
                scope_guidance,
                "- 只允许修改 SonarQube 指向的那一处问题，不要修改本文件其他同类位置。",
            ),
            build_command=cls._normalize_prompt_text(build_command, "dotnet build"),
            retry_feedback_section=retry_feedback_section,
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

    def fix_issue(
        self,
        issue: SonarIssue,
        workspace_path: Path,
        build_command: str = "dotnet build",
        retry_feedback: str = "",
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

        # Build prompts
        system_prompt = SONAR_FIX_SYSTEM_PROMPT
        resolved_build_command = build_command.strip() or "dotnet build"
        user_prompt = self._build_user_prompt(
            issue,
            code_context,
            self._load_csharp_quality_gate(issue),
            self._build_scope_guidance(issue, scope),
            rule_details,
            resolved_build_command,
            retry_feedback,
        )

        # Only allow built-in file navigation/editing tools during fix attempts.
        # Build/test/git are controlled by the outer Python workflow so provider-specific
        # SDK MCP initialization cannot block the issue-fix loop.
        allowed_tool_names = [*BUILTIN_FIX_TOOLS, *MCP_FIX_TOOLS]

        def handle_cli_stderr(line: str) -> None:
            text = str(line).strip()
            if not text:
                return
            print(f"  [CLI STDERR] {text}", flush=True)

        extra_args = self._build_agent_extra_args(self.agent_env)
        sdk_env = self._build_sdk_child_env(self.agent_env)
        sdk_model = self._resolve_sdk_model(self.agent_env, sdk_env, self.model)

        # Configure options
        options = ClaudeAgentOptions(
            tools=BUILTIN_FIX_TOOLS,
            system_prompt=system_prompt,
            allowed_tools=allowed_tool_names,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            model=sdk_model,
            cwd=str(workspace_path),
            env=sdk_env,
            stderr=handle_cli_stderr,
            extra_args=extra_args,
        )

        # Run the agent
        changes: list[dict[str, Any]] = []
        agent_error: str | None = None
        last_tool_name: str | None = None
        saw_run_build_tool = False
        forbidden_tool_uses: list[str] = []
        run_started_at = time.monotonic()
        status_lock = threading.Lock()
        status_state: dict[str, Any] = {
            "phase": "initializing",
            "last_activity_at": run_started_at,
            "phase_started_at": run_started_at,
            "first_response_received": False,
            "message_count": 0,
        }
        heartbeat_stop = threading.Event()
        self._capture_attempt_workspace_state(workspace_path)

        print(
            "  [TRACE] Agent 启动: "
            f"endpoint={self._display_agent_endpoint(self.agent_env)}, "
            f"model={self._display_agent_model(self.agent_env, self.model)}, "
            f"mode={'bare' if 'bare' in extra_args else 'default'}, "
            f"build={resolved_build_command}",
            flush=True,
        )

        def update_status(phase: str, *, first_response: bool = False) -> None:
            now = time.monotonic()
            with status_lock:
                status_state["phase"] = phase
                status_state["last_activity_at"] = now
                status_state["phase_started_at"] = now
                if first_response:
                    status_state["first_response_received"] = True
                status_state["message_count"] = int(status_state.get("message_count", 0)) + 1

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                with status_lock:
                    phase = str(status_state["phase"])
                    last_activity_at = float(status_state["last_activity_at"])
                    first_response_received = bool(status_state["first_response_received"])
                now = time.monotonic()
                total_elapsed = int(now - run_started_at)
                idle_elapsed = int(now - last_activity_at)
                if first_response_received:
                    print(
                        "  [WAIT] "
                        f"当前阶段={phase}，距上次 SDK 消息 {idle_elapsed}s，总耗时 {total_elapsed}s",
                        flush=True,
                    )
                else:
                    if phase == "client_connecting":
                        waiting_text = "仍在等待 Claude SDK Client 初始化完成"
                    elif phase == "sending_query":
                        waiting_text = "请求发送中，仍在等待模型首响应"
                    elif phase == "waiting_for_first_response":
                        waiting_text = "仍在等待模型首响应"
                    else:
                        waiting_text = "仍在等待 SDK 进入可响应状态"
                    print(
                        "  [WAIT] "
                        f"{waiting_text}，当前阶段={phase}，已等待 {total_elapsed}s",
                        flush=True,
                    )

        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name="claude-fix-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

        async def run_fix():
            nonlocal agent_error, last_tool_name, saw_run_build_tool, forbidden_tool_uses

            print("  [TRACE] 正在初始化 Claude SDK Client...", flush=True)
            update_status("client_connecting")
            client_manager = ClaudeSDKClient(options=options)
            try:
                client = await asyncio.wait_for(
                    client_manager.__aenter__(),
                    timeout=CLIENT_CONNECT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Claude SDK Client 在 {CLIENT_CONNECT_TIMEOUT_SECONDS} 秒内未完成初始化"
                ) from exc

            try:
                # Send the fix request
                print("  [TRACE] 已创建 Claude SDK Client，准备发送请求...", flush=True)
                update_status("sending_query")
                await client.query(user_prompt)
                print("  [TRACE] 请求已发送，等待模型首响应...", flush=True)
                update_status("waiting_for_first_response")

                # Process responses
                response_stream = client.receive_response()
                while True:
                    try:
                        if not status_state["first_response_received"]:
                            message = await asyncio.wait_for(
                                anext(response_stream),
                                timeout=FIRST_RESPONSE_TIMEOUT_SECONDS,
                            )
                        else:
                            message = await anext(response_stream)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError(
                            f"模型在 {FIRST_RESPONSE_TIMEOUT_SECONDS} 秒内没有返回首个响应"
                        ) from exc

                    if isinstance(message, AssistantMessage):
                        update_status("assistant_message", first_response=True)
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                last_tool_name = block.name
                                if block.name == "mcp__sonar-fix__run_build":
                                    saw_run_build_tool = True
                                if block.name in FORBIDDEN_FIX_TOOLS:
                                    forbidden_tool_uses.append(block.name)
                                update_status(f"tool:{block.name}", first_response=True)
                                print(f"  Using tool: {block.name}", flush=True)
                            elif isinstance(block, TextBlock) and block.text.strip():
                                update_status("assistant_text", first_response=True)
                                print(f"  Claude: {block.text[:200]}...", flush=True)
                            else:
                                block_type = type(block).__name__
                                update_status(f"assistant_block:{block_type}", first_response=True)
                                print(f"  [TRACE] Assistant block: {block_type}", flush=True)
                    elif isinstance(message, ResultMessage):
                        update_status("result_message", first_response=True)
                        total_cost = message.total_cost_usd or 0.0
                        print(f"  Done. Cost: ${total_cost:.4f}", flush=True)
                        agent_error = self._extract_agent_error(message)
                    else:
                        message_type = type(message).__name__
                        update_status(f"sdk_message:{message_type}", first_response=True)
                        print(f"  [TRACE] 收到 SDK 消息类型: {message_type}", flush=True)
            finally:
                await client_manager.__aexit__(None, None, None)

        # Run the async function
        try:
            anyio.run(run_fix)
        except Exception as e:
            error_details = self._format_exception_details(e) or str(e)
            for modified_file in self._collect_modified_files(workspace_path):
                changes.append({"file": modified_file, "action": "modified"})

            model_timeout = (
                isinstance(e, TimeoutError)
                or "没有返回首个响应" in error_details
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
                )

            used_forbidden_tool = bool(forbidden_tool_uses) or self._attempt_head_changed(workspace_path)
            if used_forbidden_tool:
                fallback_build_passed, fallback_build_output = self._run_local_build_fallback(
                    workspace_path,
                    resolved_build_command,
                )
                output_parts = [
                    "修复阶段使用了被禁止的工具，当前尝试已作废。",
                ]
                if forbidden_tool_uses:
                    output_parts.append(
                        "禁止工具: " + ", ".join(dict.fromkeys(forbidden_tool_uses))
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
                )

            build_tool_failed = (
                last_tool_name == "mcp__sonar-fix__run_build"
                or (
                    saw_run_build_tool
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
                )

            self._cleanup_attempt_workspace_state(workspace_path)
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                changes=changes,
                error=error_details,
            )
        try:
            for modified_file in self._collect_modified_files(workspace_path):
                changes.append({"file": modified_file, "action": "modified"})

            used_forbidden_tool = bool(forbidden_tool_uses) or self._attempt_head_changed(workspace_path)

            if used_forbidden_tool:
                build_passed, build_output = self._run_local_build_fallback(
                    workspace_path,
                    resolved_build_command,
                )
                output_parts = [
                    "修复阶段使用了被禁止的工具，当前尝试已作废。",
                ]
                if forbidden_tool_uses:
                    output_parts.append(
                        "禁止工具: " + ", ".join(dict.fromkeys(forbidden_tool_uses))
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
                )

            if agent_error:
                return FixResult(
                    success=False,
                    issue_key=issue.key,
                    file_path=str(file_path),
                    changes=changes,
                    error=agent_error,
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
                )

            # Verify build
            build_passed = False
            build_output = ""
            if workspace_path.exists():
                try:
                    result = subprocess.run(
                        resolved_build_command,
                        shell=True,
                        cwd=str(workspace_path),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=300,
                    )
                    build_passed = result.returncode == 0
                    build_output = self._combine_process_output(result)
                except Exception as e:
                    print(f"  Build verification failed: {e}")

            current_issue_file_content: str | None = None
            if file_path.exists():
                current_issue_file_content = file_path.read_text(encoding="utf-8")

            scope_violation = self._validate_issue_edit_scope(
                workspace_path,
                issue,
                scope,
                original_content=original_issue_file_content,
                current_content=current_issue_file_content,
            )
            combined_output_parts = [part for part in [build_output.strip(), scope_violation] if part]
            combined_output = "\n\n".join(combined_output_parts)

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
                    build_output=combined_output,
                    retryable_failure=True,
                    failure_kind="build",
                )

            if scope_violation:
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
                    build_output=combined_output or scope_violation,
                    retryable_failure=True,
                    failure_kind="scope",
                )

            rule_validation_message = ""
            if current_issue_file_content is not None:
                rule_validation_message = self._run_rule_specific_validation(issue, current_issue_file_content)
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
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
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
