"""Claude Code SDK based Agent for fixing SonarQube issues.

This module provides the main agent class that:
1. Connects to SonarQube to get issues
2. Uses Claude Code to analyze and fix code issues
3. Runs build/test to verify fixes
4. Creates PR in Azure DevOps
"""

import re
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
from pi_sonar_agent.sonar_mcp.tools import create_sonar_mcp_server

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


@dataclass(frozen=True)
class IssueEditScope:
    """Allowed edit scope for a single Sonar issue."""

    start_line: int
    end_line: int
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

重要约束：
- 绝对不要使用省略号(...)或注释掉代码
- 绝对不要删除整个方法或类
- 修复后如果可能，运行 build 验证
- 如果不确定如何修复，调用 finish 并说明原因

工作流程：
1. 使用 read_file 工具读取有问题 的源文件
2. 使用 apply_edit 工具进行精确修复
3. 使用 run_build 工具验证编译通过
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

【允许修改范围】
{scope_guidance}

【推荐构建命令】
{build_command}

{retry_feedback_section}

请按照以下步骤操作：
1. 读取源文件 {file_path} 确认问题
2. 分析问题的根本原因
3. 应用精确修复
4. 使用上述推荐构建命令验证修复
5. 调用 finish 标记完成

注意：
- 只修复本问题，不要做无关改动
- 不要顺手修复本文件中其他位置的相同规则问题
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


MCP_FIX_TOOLS = [
    "mcp__sonar-fix__read_file",
    "mcp__sonar-fix__apply_edit",
    "mcp__sonar-fix__create_file",
    "mcp__sonar-fix__run_build",
    "mcp__sonar-fix__run_tests",
    "mcp__sonar-fix__git_status",
    "mcp__sonar-fix__git_add",
    "mcp__sonar-fix__git_commit",
    "mcp__sonar-fix__git_push",
    "mcp__sonar-fix__grep",
    "mcp__sonar-fix__list_files",
    "mcp__sonar-fix__get_file_outline",
]


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
        """Collect modified files after an agent run."""

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
    def _combine_process_output(result: subprocess.CompletedProcess[str]) -> str:
        """Combine subprocess streams into a single safe string."""

        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        return f"{stdout}{stderr}"

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

    @classmethod
    def _build_issue_edit_scope(
        cls,
        issue: SonarIssue,
        lines: list[str],
    ) -> IssueEditScope:
        """Build the allowed edit scope for the issue."""

        total_lines = max(len(lines), 1)
        if issue.rule == "csharpsquid:S3776":
            method_range = cls._find_enclosing_method_range(lines, issue.line)
            if method_range:
                start_line, end_line = method_range
                validation_end_line = min(total_lines, end_line + 60)
                return IssueEditScope(
                    start_line=start_line,
                    end_line=end_line,
                    validation_end_line=validation_end_line,
                    mode="method",
                )

        start_line = max(1, issue.line - 8)
        end_line = min(total_lines, issue.line + 8)
        return IssueEditScope(
            start_line=start_line,
            end_line=end_line,
            validation_end_line=end_line,
            mode="line",
        )

    @staticmethod
    def _build_scope_guidance(issue: SonarIssue, scope: IssueEditScope | None) -> str:
        """Render edit-scope guidance for the model prompt."""

        if scope is None:
            return (
                "- 只允许修改 SonarQube 指向的那一处问题。\n"
                "- 不要顺手修复本文件中其他相同规则或相同写法的问题。"
            )

        if scope.mode == "method":
            return (
                f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行的目标方法。\n"
                f"- 如果必须提取 private 辅助方法，只能新增在该方法后方紧邻区域，且不要超过第 {scope.validation_end_line} 行。\n"
                "- 新增的辅助方法只能服务当前 issue 对应的方法，不要改动本文件其他方法中的同类问题。"
            )

        return (
            f"- 只允许修改第 {scope.start_line}-{scope.end_line} 行附近这一个问题点。\n"
            "- 不要继续搜索并修改本文件其他位置的相同规则问题。"
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
            if line < scope.start_line or line > scope.validation_end_line
        )

    @classmethod
    def _validate_issue_edit_scope(
        cls,
        workspace_path: Path,
        issue: SonarIssue,
        scope: IssueEditScope | None,
    ) -> str | None:
        """Verify that the issue edit stayed inside the allowed code scope."""

        if scope is None:
            return None

        relative_path = issue.file_path.lstrip("/").replace("\\", "/")
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

        changed_lines = cls._extract_changed_line_numbers(result.stdout)
        if not changed_lines:
            return None

        offending_lines = cls._find_out_of_scope_lines(scope, changed_lines)
        if not offending_lines:
            return None

        offending_text = ", ".join(str(line) for line in offending_lines[:12])
        return (
            "Issue changes exceeded the allowed Sonar edit scope.\n"
            f"Allowed lines: {scope.start_line}-{scope.validation_end_line}\n"
            f"Changed lines outside scope: {offending_text}\n"
            "只允许修复 Sonar 指向的这一处代码，不要顺手修改本文件其他同类位置。"
        )

    @classmethod
    def _build_user_prompt(
        cls,
        issue: SonarIssue,
        code_context: str,
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

        # Get rule guidance
        rule_details = self.get_rule_details(issue.rule)

        # Get code context - try to get from local file first
        code_context = ""
        scope: IssueEditScope | None = None
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
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
        user_prompt = self._build_user_prompt(
            issue,
            code_context,
            self._build_scope_guidance(issue, scope),
            rule_details,
            build_command,
            retry_feedback,
        )

        # Create MCP server
        mcp_server = create_sonar_mcp_server()

        # Allow built-in file navigation/editing tools, plus project-specific MCP tools.
        allowed_tool_names = [*BUILTIN_FIX_TOOLS, *MCP_FIX_TOOLS]

        # Configure options
        options = ClaudeAgentOptions(
            tools=BUILTIN_FIX_TOOLS,
            system_prompt=system_prompt,
            mcp_servers={"sonar-fix": mcp_server},
            allowed_tools=allowed_tool_names,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            model=self.model,
            cwd=str(workspace_path),
            env=self.agent_env.copy(),
        )

        # Run the agent
        changes: list[dict[str, Any]] = []
        agent_error: str | None = None

        async def run_fix():
            nonlocal agent_error

            async with ClaudeSDKClient(options=options) as client:
                # Send the fix request
                await client.query(user_prompt)

                # Process responses
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                print(f"  Using tool: {block.name}")
                            elif isinstance(block, TextBlock) and block.text.strip():
                                print(f"  Claude: {block.text[:200]}...")
                    elif isinstance(message, ResultMessage):
                        total_cost = message.total_cost_usd or 0.0
                        print(f"  Done. Cost: ${total_cost:.4f}")
                        agent_error = self._extract_agent_error(message)

        # Run the async function
        try:
            anyio.run(run_fix)
        except Exception as e:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                error=str(e),
            )

        if agent_error:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                error=agent_error,
            )

        for modified_file in self._collect_modified_files(workspace_path):
            changes.append({"file": modified_file, "action": "modified"})

        if not changes:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                error="Agent completed without modifying any files",
                summary="Fixed 0 file(s)",
            )

        resolved_build_command = build_command.strip() or "dotnet build"
        scope_violation = self._validate_issue_edit_scope(workspace_path, issue, scope)
        if scope_violation:
            return FixResult(
                success=False,
                issue_key=issue.key,
                file_path=str(file_path),
                changes=changes,
                build_passed=False,
                build_verification_failed=True,
                error="Issue changes exceeded allowed scope",
                summary=f"Fixed {len(changes)} file(s)",
                build_command=resolved_build_command,
                build_output=scope_violation,
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
                build_output=build_output,
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
