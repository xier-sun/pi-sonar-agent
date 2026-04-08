"""Structured retry context for issue attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.state import serialize_state


@dataclass(frozen=True)
class CompilerErrorContext:
    """Structured compiler error with optional source snippet."""

    file_path: str
    line: int
    column: int
    code: str
    message: str
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the compiler error to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class ScopeViolationContext:
    """Structured scope violation details for retry analysis."""

    raw_output: str = ""
    allowed_lines: str = ""
    changed_lines_outside_scope: str = ""
    constraints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the scope violation context to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class ReviewViolationContext:
    """Structured diff-review violation details for retry analysis."""

    violation_type: str
    file: str
    reason: str
    changed_lines: tuple[int, ...] = ()
    evidence_hunk: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the reviewer violation to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class ReviewFailureContext:
    """Structured diff-review rejection details for retry analysis."""

    raw_output: str = ""
    summary: str = ""
    constraints: tuple[str, ...] = ()
    violations: tuple[ReviewViolationContext, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the review failure context to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateViolationContext:
    """Structured hard quality-gate violation for retry analysis."""

    rule_id: str
    title: str
    message: str
    file: str = ""
    line: int = 0
    symbol: str = ""
    evidence: str = ""
    retry_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class QualityGateFailureContext:
    """Structured quality-gate rejection details for retry analysis."""

    summary: str = ""
    violations: tuple[QualityGateViolationContext, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class RetryContext:
    """Structured retry memory for the next issue attempt."""

    source_attempt_number: int = 0
    failure_kind: str = ""
    error: str = ""
    summary: str = ""
    build_command: str = ""
    raw_output: str = ""
    retryable_failure: bool = False
    build_verification_failed: bool = False
    changed_files: tuple[str, ...] = ()
    compiler_errors: tuple[CompilerErrorContext, ...] = ()
    guidance: tuple[str, ...] = ()
    scope_violation: ScopeViolationContext | None = None
    review_failure: ReviewFailureContext | None = None
    quality_gate_failure: QualityGateFailureContext | None = None
    model_timeout_summary: str = ""
    build_tool_failed: bool = False
    forbidden_tool_failed: bool = False
    model_timeout_failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the retry context to a JSON-ready dictionary."""

        return serialize_state(self)


def render_retry_context(retry_context: RetryContext | None) -> str:
    """Render a structured retry context into the prompt text used by the model."""

    if retry_context is None:
        return ""

    raw_output = str(retry_context.raw_output or "").strip()
    scope_violation = retry_context.scope_violation
    review_failure = retry_context.review_failure
    quality_gate_failure = retry_context.quality_gate_failure
    has_scope_violation = scope_violation is not None
    has_review_failure = review_failure is not None
    has_quality_gate_failure = quality_gate_failure is not None
    compiler_errors = list(retry_context.compiler_errors)

    if not compiler_errors:
        if raw_output:
            if retry_context.model_timeout_failed:
                return "\n".join(
                    [
                        f"上次尝试{retry_context.model_timeout_summary or '在等待模型首响应时超时'}，请先确认当前模型网关或 provider 可正常返回工具调用响应：",
                        raw_output,
                        "重试约束:",
                        "- 不要更换构建命令，先确认模型能稳定返回工具调用响应。",
                        "- 如果连续超时，优先检查 .env 中的模型 endpoint、token 和 provider 兼容性。",
                    ]
                )
            if retry_context.forbidden_tool_failed:
                sections = [
                    "上次尝试使用了被禁止的工具，或污染了当前 issue 的 Git 基线；本次必须严格按下面约束重试：",
                    raw_output,
                    "重试约束:",
                    "- 严禁使用 Bash、git_add、git_commit、git_push 或任何自行提交/推送动作。",
                    "- 修复阶段只能直接编辑代码并运行推荐构建命令，提交由外层流程统一处理。",
                    "- 先根据下面的本地构建输出修复问题，再重新运行推荐构建命令验证。",
                ]
                if has_scope_violation:
                    sections.extend(
                        [
                            "范围约束:",
                            *(scope_violation.constraints if scope_violation else ()),
                        ]
                    )
                return "\n".join(sections)
            if retry_context.build_tool_failed:
                sections = [
                    "上次尝试在运行构建工具时异常退出，请先处理下面的异常信息和本地回退构建输出：",
                    raw_output,
                    "重试约束:",
                    "- 先修复 stderr 或回退构建输出中暴露的问题，再重新运行推荐构建命令。",
                    "- 如果回退构建已经通过，也要再次运行构建，确认修改后的代码仍然稳定。",
                ]
                if has_scope_violation:
                    sections.extend(
                        [
                            "范围约束:",
                            *(scope_violation.constraints if scope_violation else ()),
                        ]
                    )
                return "\n".join(sections)
            if has_scope_violation:
                return "\n".join(
                    [
                        "上次尝试修改了 Sonar 指定范围之外的代码，请严格缩小修改范围：",
                        raw_output,
                        "重试约束:",
                        *(scope_violation.constraints if scope_violation else ()),
                    ]
                )
            if has_review_failure:
                return "\n".join(
                    [
                        "上次尝试被变更审查拒绝，请严格缩小当前 patch：",
                        review_failure.raw_output if review_failure else raw_output,
                        "重试约束:",
                        *(review_failure.constraints if review_failure else ()),
                    ]
                )
            if has_quality_gate_failure:
                sections = [
                    "上次尝试通过了 build 和范围审查，但没有通过 C# 质量门禁：",
                    quality_gate_failure.summary if quality_gate_failure else raw_output,
                ]
                for index, item in enumerate(quality_gate_failure.violations if quality_gate_failure else (), start=1):
                    sections.append(f"{index}. [{item.rule_id}] {item.title}: {item.message}")
                    if item.retry_hint:
                        sections.append(f"   重试提示: {item.retry_hint}")
                return "\n".join(sections)
            return raw_output
        if retry_context.failure_kind == "no_change" or retry_context.error == "Agent completed without modifying any files":
            return "\n".join(
                [
                    "上次尝试没有实际修改任何文件。",
                    "这次必须对 Sonar 指向的代码真正落盘修改，然后再运行构建验证。",
                    "重试约束:",
                    "- 不要只做分析或解释，必须提交实际代码修改。",
                    "- 修改后立即使用推荐构建命令验证。",
                    "- 如果这是行级问题，只修改 Sonar 指向的那条语句。",
                ]
            )
        return retry_context.error or ""

    sections: list[str] = []
    if retry_context.model_timeout_failed:
        sections.append(f"上次尝试{retry_context.model_timeout_summary or '在等待模型首响应时超时'}。")
        sections.append("先确认当前模型 provider/网关与 Claude SDK 工具调用协议兼容，再继续重试。")
    if retry_context.forbidden_tool_failed:
        sections.append("上次尝试使用了被禁止的工具，或污染了当前 issue 的 Git 基线。")
        sections.append("这次严禁使用 Bash、git_add、git_commit、git_push；只允许直接编辑代码并运行推荐构建命令。")
    if retry_context.build_tool_failed:
        sections.append("上次尝试在运行构建工具时异常退出；已附加本地回退构建结果。")
    sections.append("上次尝试引入了以下关键编译错误，请先修复这些错误：")
    for index, item in enumerate(compiler_errors[:12], start=1):
        sections.append(
            f"{index}. {item.code} at {item.file_path}:{item.line}:{item.column}\n"
            f"   错误信息: {item.message}"
        )
        if item.snippet:
            sections.append(f"   出错代码片段:\n{item.snippet}")

    if retry_context.guidance:
        sections.append("重试约束:")
        sections.extend(f"- {item}" for item in retry_context.guidance)

    if has_scope_violation:
        sections.extend(
            [
                "另外，上次修改还越过了 Sonar 允许范围：",
                scope_violation.raw_output if scope_violation else raw_output,
                "范围约束:",
                *(scope_violation.constraints if scope_violation else ()),
            ]
        )
    if has_review_failure:
        sections.extend(
            [
                "另外，Diff reviewer 也拒绝了上次 patch：",
                review_failure.raw_output if review_failure else raw_output,
                "审查约束:",
                *(review_failure.constraints if review_failure else ()),
            ]
        )
    if has_quality_gate_failure:
        sections.append("另外，上次 patch 还没有通过 C# 质量门禁：")
        sections.append(quality_gate_failure.summary if quality_gate_failure else "")
        for index, item in enumerate(quality_gate_failure.violations if quality_gate_failure else (), start=1):
            sections.append(f"{index}. [{item.rule_id}] {item.title}: {item.message}")
            if item.retry_hint:
                sections.append(f"   重试提示: {item.retry_hint}")

    return "\n".join(sections)
