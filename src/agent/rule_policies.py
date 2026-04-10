"""Rule handling policies for Sonar issue fixing."""

from __future__ import annotations

from dataclasses import dataclass

from pi_sonar_agent.core.boundary_capabilities import (
    ADJACENT_CLEANUP_CAPABILITY,
    BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
    BOUNDARY_PROFILE_DECLARATION_ANCHOR,
    BOUNDARY_PROFILE_MEMBER_CLUSTER,
    DECLARATION_DELETE_CAPABILITY,
    MEMBER_DELETE_CAPABILITY,
    METHOD_CLUSTER_DELETE_CAPABILITY,
)

STATEMENT_SCOPE_MODE = "statement"
METHOD_SCOPE_MODE = "method"
CONTROL_BLOCK_SCOPE_MODE = "control_block"
DECLARATION_COMMENT_SCOPE_MODE = "declaration_comment"
CONDITIONAL_CHAIN_SCOPE_MODE = "conditional_chain"
EXPRESSION_REWRITE_SCOPE_MODE = "expression_rewrite"
LOOP_REWRITE_SCOPE_MODE = "loop_rewrite"


@dataclass(frozen=True)
class RuleHandlingPolicy:
    """Policy for handling a specific Sonar rule."""

    scope_mode: str = STATEMENT_SCOPE_MODE
    boundary_profile: str = ""
    boundary_capabilities: tuple[str, ...] = ()
    validation_leading_lines: int = 0
    validation_trailing_lines: int = 0
    prompt_guards: tuple[str, ...] = ()
    local_validator: str = ""
    skip_reason: str = ""


DEFAULT_RULE_POLICY = RuleHandlingPolicy()


RULE_HANDLING_POLICIES: dict[str, RuleHandlingPolicy] = {
    "csharpsquid:S3776": RuleHandlingPolicy(
        scope_mode=METHOD_SCOPE_MODE,
        validation_trailing_lines=120,
    ),
    "csharpsquid:S3358": RuleHandlingPolicy(
        scope_mode=EXPRESSION_REWRITE_SCOPE_MODE,
        validation_leading_lines=2,
        validation_trailing_lines=16,
        prompt_guards=(
            "修复后，当前 issue 对应语句中不能再保留嵌套的 ?: 条件运算符。",
            "优先在当前表达式附近改成局部变量、if/else 或语句 lambda，不要新增类级 private/helper 方法。",
            "如果 issue 位于 LINQ Select/匿名对象初始化中，优先改写成语句 lambda，并在 lambda 内 return 原对象。",
            "如果使用中间变量或辅助方法，原位置只能保留一层简单条件，或直接调用提取后的结果，不要把嵌套三元原样保留。",
        ),
        local_validator="nested_ternary_removed",
    ),
    "csharpsquid:S2681": RuleHandlingPolicy(
        scope_mode=CONTROL_BLOCK_SCOPE_MODE,
        validation_trailing_lines=4,
    ),
    "external_roslyn:CS1591": RuleHandlingPolicy(
        scope_mode=DECLARATION_COMMENT_SCOPE_MODE,
        validation_leading_lines=8,
        validation_trailing_lines=8,
    ),
    "csharpsquid:S1066": RuleHandlingPolicy(
        scope_mode=CONDITIONAL_CHAIN_SCOPE_MODE,
        validation_trailing_lines=4,
    ),
    "csharpsquid:S1871": RuleHandlingPolicy(
        scope_mode=CONDITIONAL_CHAIN_SCOPE_MODE,
        validation_trailing_lines=4,
    ),
    "csharpsquid:S3267": RuleHandlingPolicy(
        scope_mode=LOOP_REWRITE_SCOPE_MODE,
        validation_trailing_lines=4,
        prompt_guards=(
            "只有在当前循环是纯内存集合遍历、没有副作用且不会改变执行时序时，才可以改成 LINQ。",
            "如果循环体里出现 IQueryable、Entity Framework 查询、await、break、continue、日志、异常控制或其他副作用，宁可停止修改并说明原因。",
            "不要为了满足规则把简单循环改成更难读、更难调试的 LINQ 写法。",
            "当前规则允许重写整个当前 foreach/for/while 语句块；如果循环后紧跟着与该查找逻辑配套的 return/throw，也可以一并改写。",
        ),
    ),
    "csharpsquid:S6562": RuleHandlingPolicy(
        prompt_guards=(
            "保持原有业务时区语义，不要擅自把 Local 或 Unspecified 改成 Utc。",
            "优先通过 DateTime.SpecifyKind(...) 或显式带 DateTimeKind 的构造方式修复，而不是改写业务含义。",
        ),
    ),
    "csharpsquid:S6561": RuleHandlingPolicy(
        prompt_guards=(
            "只有在当前代码确实用于性能计时或 benchmark 时，才改成 Stopwatch。",
            "如果这里记录的是业务时间戳、审计时间或用户可见时间，不要改写时间来源。",
        ),
    ),
    "csharpsquid:S4487": RuleHandlingPolicy(
        prompt_guards=(
            "移除 private 字段前，先确认它不是被 attribute、序列化、反射、source generator 或 partial class 约定使用。",
            "如果无法确认字段是否有隐式用途，宁可停止修改并说明原因。",
        ),
    ),
    "csharpsquid:S2325": RuleHandlingPolicy(
        prompt_guards=(
            "只有在成员是 private，且不是 virtual、override、abstract、interface implementation 时，才考虑改成 static。",
            "不要因为这个规则改动 public 或 protected API 形态，也不要破坏多态行为。",
        ),
    ),
    "csharpsquid:S125": RuleHandlingPolicy(
        boundary_profile=BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP,
        boundary_capabilities=(ADJACENT_CLEANUP_CAPABILITY,),
        validation_leading_lines=4,
        validation_trailing_lines=1,
        prompt_guards=(
            "如果移除被注释掉的代码后，紧邻的局部变量立即变成未使用，可以一并删除。",
            "只允许清理与该注释代码直接耦合的相邻冗余，不要继续扩展到更远的同类清理。",
        ),
    ),
    "csharpsquid:S1481": RuleHandlingPolicy(
        boundary_profile=BOUNDARY_PROFILE_DECLARATION_ANCHOR,
        boundary_capabilities=(DECLARATION_DELETE_CAPABILITY,),
        prompt_guards=(
            "只删除当前 issue 对应的未使用局部变量，不要顺手改同一方法里的其他清理项。",
        ),
    ),
    "csharpsquid:S1144": RuleHandlingPolicy(
        scope_mode=METHOD_SCOPE_MODE,
        boundary_profile=BOUNDARY_PROFILE_MEMBER_CLUSTER,
        boundary_capabilities=(
            MEMBER_DELETE_CAPABILITY,
            METHOD_CLUSTER_DELETE_CAPABILITY,
        ),
        prompt_guards=(
            "优先删除当前未使用的 private 成员本身；只有当紧邻 private helper 也因此变成未使用时，才允许一并删除。",
            "不要顺手删除同文件中更远位置的其他 private 成员。",
        ),
    ),
    "csharpsquid:S107": RuleHandlingPolicy(
        skip_reason="规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。",
    ),
    "csharpsquid:S1172": RuleHandlingPolicy(
        skip_reason="规则 csharpsquid:S1172 默认跳过：未使用参数可能涉及公共签名或接口契约，建议人工处理。",
    ),
    "csharpsquid:S4136": RuleHandlingPolicy(
        skip_reason="规则 csharpsquid:S4136 默认跳过：重排重载成员会产生大范围 diff，建议人工处理。",
    ),
    "csharpsquid:S6960": RuleHandlingPolicy(
        skip_reason="规则 csharpsquid:S6960 默认跳过：Controller 职责拆分属于架构调整，建议人工处理。",
    ),
}


def get_rule_policy(rule_id: str | None) -> RuleHandlingPolicy:
    """Return the handling policy for a Sonar rule."""

    normalized = str(rule_id or "").strip()
    return RULE_HANDLING_POLICIES.get(normalized, DEFAULT_RULE_POLICY)
