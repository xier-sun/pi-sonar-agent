# pi-sonar-agent Headless 最简闭环实施方案

更新时间：2026-04-17

适用范围：

- 当前 `pi-sonar-agent`
- 运行形态为纯 headless agent
- Sonar 平台为 `SonarQube Server`
- 不依赖 `SonarQube for IDE`
- 不依赖桌面版 IDE sidecar

---

## 1. 文档目标

本方案用于把当前 `pi-sonar-agent` 从“规则和预检很多，但经常在修复中途被工程限制打断”的状态，调整为“围绕 issue 修复成功率优先的 headless 最简闭环”。

目标不是继续叠加 prompt、planner、precheck，而是把主链收敛成下面这条：

1. 从 SonarQube Server 按 `issue key` 拉问题
2. 让 Claude Code 在当前工作区中最小化修复
3. 对修复结果做轻量 issue 校验
4. 对修复结果做新阻塞检查
5. 编译通过后进入下一个 issue
6. 全部 issue 完成后再做整仓 build / PR

这份文档强调：

1. 以最简闭环为目的
2. 以轻量校验为目的
3. 以减少不必要的工程限制和循环为目的
4. 项目目录结构和方案允许按需要调整

---

## 1.1 当前实施状态

- `已完成` `P0-1` 新增执行模式骨架
  - 已新增 `ISSUE_EXECUTION_MODE`
  - 已新增 `src/core/simple_mode.py`
  - 已把 `execution_mode` 接入 `EditContract`、planner、agent、artifact
  - 当前以 `opt-in` 方式接入，默认仍保持 `strict`，避免一次性切默认行为带来大回归
- `已完成` `P0-2` build-first verifier 主链
  - `simple_loop` 下已切换为 `boundary -> build -> post_check`
  - 已关闭 `semantic_precheck / propagation / quality_gate / rule_validation / fast_compile` 的 build 前阻断
  - 已让 `simple_loop` 跳过 repeated failure planner skip，避免旧的重策略继续截断主链
- `已完成` `P0-3` 极简 prompt 首版
  - `simple_loop` 下不再默认注入质量门禁大段说明
  - `simple_loop` 下不再默认注入 `Edit Contract / Repair Plan / Prefetched Context`
  - 已新增更贴近“先修当前 issue，再 build/post-check”的执行模式提示
- `已完成` `P1-1` `simple_post_check.py` 主骨架
  - 已新增 `IssueCheckResult / NewBlockerCheckResult / PostFixCheckResult`
  - 已实现 `UNKNOWN` 合法三态
  - 已把 `post_fix_check_result` 写入 `FixResult` / artifact / 运行摘要
- `已完成` `P1-2` 高频规则轻量 issue 校验
  - 已新增 `src/core/issue_checkers.py`
  - 已补齐高频轻量校验：
    - `S3358`
    - `S3776`
    - `S107`
    - `S1172`
    - `S1481`
    - `S1144`
  - 对仍无法可靠本地判断的规则继续返回 `UNKNOWN`，不再为了“判不准”强行失败
- `已完成` `P1-3` 新阻塞规则集
  - 已新增 `src/core/blocker_checkers.py`
  - 已统一收口 simple-loop blocker 分类：
    - `compile_errors`
    - `language_feature_incompatible`
    - `async_contract_break`
    - `public_signature_drift`
    - `helper_type_shape_break`
    - `forbidden_scope_change`
  - `simple_post_check.py` 已不再硬编码 blocker 规则，而是改为走统一 catalog + checker
- `已完成` 目录骨架第一步
  - 已新增：
    - `src/core/simple_mode.py`
    - `src/core/simple_post_check.py`
    - `src/core/issue_checkers.py`
    - `src/core/blocker_checkers.py`
    - 对应 bridge 模块
- `已完成` `P2-1` 统一知识源
  - 已新增 `data/sonar_light_check_catalog.yaml`
  - `prompt / issue checker / blocker checker` 已统一读取同一份轻量规则知识源
- `已完成` `P2-2` 规则族修复手册
  - 已新增 `docs/sonar-fix-playbook.md`
  - simple-loop prompt 已接入按规则族抽取的自检 guidance
- `已完成` `P2-3` strict/simple 目录进一步隔离
  - 已新增 `src/core/strict/` 兼容入口
  - 已把 `semantic_precheck / propagation_verifier / review_gate / repair_plan / failure_fingerprint / lessons_store` 明确标记为 strict 侧能力
  - 现阶段保留旧 import 路径兼容，避免一次性大重构破坏现有调用方
- `已完成` `P2-4` helper-extract 降级闭环修复
  - `allowed_change_kinds` 已受 `allowed_capabilities` 约束；禁用 `helper_extract` 后不再向模型暴露 `extract-private-helper`
  - `simple_loop` 在命中 `helper_extraction_type_break / nullable_type_mismatch` 后，主 prompt 会追加硬约束：
    - 禁止新增 helper/private 方法
    - 禁止使用 `dynamic`
    - 禁止匿名类型或 nullable-heavy 状态跨方法边界
  - `DiffReviewer` 已新增硬检查：当 `helper_extract` 不在 capability 中时，新增 private method 直接 `retry`
  - `Review gate: not_applicable` 类 trace 已改为 `Optional patch audit skipped`，避免误判成又拉起了 reviewer agent
- `已完成` `P2-5` 通用 C# 重构安全边界知识源
  - 已把通用 C# 重构安全边界沉到 `docs/sonar-fix-playbook.md` 作为统一规则经验文档
  - 已把可注入 prompt 的硬约束摘要接入 simple-loop 主提示词
  - playbook 现在同时承载：
    - 完整参考文档
    - `execution_mode` 自检 guidance
    - `rule_guard_section` 硬约束摘要
- `已完成` `P2-6` 累计重试上下文与自动压缩
  - 已在 `src/core/retry_context.py` 新增滚动 `retry_history` 记忆模型，保留每轮失败的主失败模式、关键错误码和简要 headline
  - 已在 `src/core/issue_retry.py` 把每轮失败合并进下一轮 `retry_context`，不再只保留“上一轮失败信息”
  - 已在 prompt 渲染时新增 `【累计重试上下文】`，把最近失败明细和更早尝试的压缩摘要一起带给模型
  - 当累计上下文接近预算时，会自动把更早尝试折叠成压缩摘要，优先保留最近 1-3 轮的详细轨迹
  - 已把累计上下文一并写入 artifact 的 `prompt_context.json`
- `已完成` `P2-7` `S3776` 最优 patch 保留与运行时执行面对齐
  - 已为 `S3776` 增加“最佳 build-passing patch 基线”保留机制：当某轮已把本地复杂度压到允许阈值内且 build 通过，后续 retry 会从该最佳 patch 继续，而不是退回更差版本
  - 当后续 retry 明显退化时，issue 结束前会把工作区恢复到最佳 build-passing patch，避免把更差 patch 留在工作区
  - 已在 runtime 增加 helper-extract 前置 guard：当当前 retry 已禁用 `helper_extract` 时，模型一旦落盘新增 private helper/private method，会立即终止当前 attempt，并以可重试的 `runtime_contract_violation` 进入下一轮；不再允许在同一轮里长时间自我纠偏空转
  - 已把 Bash 的可见性、prompt 文案和 policy 对齐：Bash 不可见时主 prompt 不再继续输出 Bash 约束；Bash 可见时，`grep -n` 这类只读诊断命令明确允许，不再把整轮 attempt 作废
- `已完成` `P2-8` 连续修复上下文与 runtime contract 重试闭环
  - `RetryContext` 已新增“上次修复摘要”记忆：会携带上轮修法摘要、改动文件、触达符号和 patch 预览，而不再只保留失败码
  - 已把修复摘要物化到工作区 `.pi-sonar-agent-runtime/retry/<issue>/attempt-XX-patch-summary.txt`，供模型按需 `Read`
  - 下一轮 prompt 已明确提示当前工作区是否恢复到 `issue baseline` 或 `best patch`，避免把旧编译错误误当成当前文件的逐字状态
  - `helper_extract_runtime_guard` 已从终止型 `agent_error` 调整为可重试的 `runtime_contract_violation`，防止可修 issue 被运行时护栏直接判死
- `已完成` `P2-9` simple-loop 真正瘦身
  - 已为 `simple_loop` 拆出更精简的 system/user prompt，不再沿用 strict 风格的大一统模板
  - `simple_loop` 默认不再注入：
    - 长规则说明
    - 长期 lessons / durable memory
    - 执行模式长说明
    - 通用 C# 重构安全边界长约束
  - `simple_loop` 的规则额外约束改成“仅在 retry 已证明当前路线有毒时，再追加硬约束”
  - `simple_loop` 的工具说明改成最小可执行说明：仅保留当前可用工具和 Bash 只读/不 build 提示
  - `S3776` 的本地轻量 issue-check 已从硬失败收紧为启发式 `UNKNOWN`，不再因为粗估算误杀已可编译 patch
- `已完成` `P3-1` 去除 strict 模式并收口到 simple-loop 单模式
  - `execution_mode` 已统一归一到 `simple_loop`
  - `strict` 不再作为有效执行路径参与 issue 修复主链
  - `IssueContract / IssuePlanner / runtime` 默认执行模式已统一为 `simple_loop`
- `已完成` `P3-2` 主Agent/子Agent 角色拆分
  - 主路径已切为 `Fix 子Agent -> Review 子Agent -> Main 裁决 -> Compile`
  - `Fix 子Agent` 负责直接编辑代码
  - `Review 子Agent` 负责按 C# 质量门禁审查 patch
  - `Main 裁决 Agent` 负责最终决定是否进入编译阶段
  - Review/Main 已改为 prompt-only 子会话，不再复用旧的本地 review_gate
- `已完成` `P3-3` 子Agent 记忆机制
  - 已新增 `src/core/memory/child_agent_memory.py`
  - 已支持 `fix/review/main` 三种角色的独立记忆
  - 已支持最近轮次详细记忆 + 更早历史压缩摘要
  - 已把子记忆落盘到 `.git/pi-sonar-agent-runtime/issues/<issue>/child-memory-<role>.json`
- `已完成` `P3-4` 本地约束极限收缩
  - `DiffReviewer` 现在只保留三类硬拒绝：
    - protected path
    - file created
    - file deleted
  - 已去掉 helper/scope/同文件漂移等本地 contract 审核
  - 本地 `FixVerifier` 已降为“filesystem boundary + build”，不再承担本地 semantic/quality/review_gate 判决
- `已完成` `P3-5` 编译重试上限收紧
  - `DEFAULT_MAX_BUILD_RETRIES` 已从 `5` 收紧为 `3`
  - 超过 3 轮编译仍失败时，外层继续按 issue baseline 回滚并跳过

本轮新增验证：

- `8 passed, 102 deselected`
  - `tests/test_issue_guardrails.py`
  - `tests/test_issue_working_memory.py`
  - `tests/test_fix_verifier.py`
  - `tests/test_claude_agent.py`
- `1 passed, 38 deselected`
  - `tests/test_issue_retry.py -k "three_build_failures"`
- `2 passed, 8 deselected`
  - `tests/test_issue_guardrails.py -k "disables_file_creation_in_simple_loop or drops_write_when_file_creation_is_disabled"`
- `py_compile` 已通过

本轮已完成验证：

- `16 passed`
  - `tests/test_light_check_catalog.py`
  - `tests/test_issue_checkers.py`
  - `tests/test_blocker_checkers.py`
  - `tests/test_simple_post_check.py`
  - `tests/test_simple_loop_prompt.py`
- `2 passed, 34 deselected`
  - `tests/test_attempt_scheduler.py`
  - `tests/test_fix_verifier.py -k "simple_loop or post_check or execution_mode"`
- `66 passed`
  - `tests/test_issue_planner.py`
  - `tests/test_artifact_writer.py`
  - `tests/test_issue_retry.py`
- `81 passed`
  - `tests/test_issue_planner.py`
  - `tests/test_issue_guardrails.py`
  - `tests/test_simple_loop_prompt.py`
  - `tests/test_boundary_runtime.py`
  - `tests/test_fix_verifier.py`
- `10 passed`
  - `tests/test_light_check_catalog.py`
  - `tests/test_simple_loop_prompt.py`
- `36 passed`
  - `tests/test_issue_retry.py`
- `5 passed, 88 deselected`
  - `tests/test_issue_retry.py`
  - `tests/test_claude_agent.py -k "structured_retry_context or workspace_retry_references or merge_retry_context_history or skips_after_three_build_failures"`
- `2 passed`
  - `tests/test_artifact_writer.py`
- `5 passed`
  - `tests/test_issue_checkers.py -q`
  - `tests/test_issue_retry.py -k "preserves_best_build_passing_s3776_patch"`
  - `tests/test_runtime_layers.py -k "grep_allowed_when_bash_is_visible or helper_extract_guard_detects_new_private_method"`
  - `tests/test_claude_agent.py -k "hides_bash_constraints_when_bash_is_not_visible"`
- `7 passed`
  - `tests/test_issue_retry.py -k "captures_patch_summary_and_symbols or retries_runtime_contract_violation or merge_retry_context_history_carries_attempt_history_into_next_prompt or merge_retry_context_history_compacts_older_attempts_when_budget_is_small"`
  - `tests/test_claude_agent.py -k "structured_retry_context or includes_repair_summary_from_retry_context or classify_runtime_contract_agent_error_helper_extract_guard"`
- `13 passed`
  - `tests/test_simple_loop_prompt.py`
  - `tests/test_issue_checkers.py`
- `py_compile` 已通过

---

## 2. 当前问题总结

结合最近多轮真实日志和代码路径分析，当前项目的主要问题不是“没有能力”，而是“过度工程化后主链被稀释了”。

### 2.1 当前主链太重

当前单 issue 处理链路在实践中接近于：

1. `IssuePlanner` 预设修法
2. prompt 注入多层 guard
3. `semantic_precheck`
4. `propagation_check`
5. `quality_gate`
6. `review_gate`
7. `fast_compile`
8. `build`
9. `retry`

这条链的风险是：

1. 模型还没把 issue 修完，就先被大量前置门禁打断
2. 许多拦截来自 heuristic，不是 compile 级硬事实
3. 一旦 heuristic 误判，就会把本来可修的 patch 打回
4. retry 信息被大量工程元信息污染，模型难以聚焦当前 issue

### 2.2 当前“修 issue”目标被“防失控”目标压过

最近多次失败说明：

1. 有些 patch 已接近正确，甚至可编译，但被过粗 precheck 误拦
2. 有些失败并不是 issue 本身复杂，而是 verifier / method window / tool protocol 等工程问题
3. 失败记忆和策略切换虽然越来越复杂，但主目标仍然不是“尽快修掉 issue”

### 2.3 对 headless 场景的判断方式过于理想化

在纯 headless 条件下：

1. 没有 `SonarQube for IDE`
2. 没有桌面侧增量分析
3. 不能把 Sonar 本地再扫做成 IDE 式同步反馈

因此本地闭环应接受一个现实：

1. 不可能在每轮 patch 后都得到与 Sonar Server 完全一致的裁决
2. 必须使用“build + 轻量 issue 校验 + 新阻塞检查”的组合判定
3. 要允许一部分规则进入 `UNKNOWN`，而不是强行 `PASS/FAIL`

---

## 3. 目标闭环

### 3.1 目标主链

新的 headless 最简闭环定义为：

1. `Issue Fetch`
   - 通过 SonarQube Server API 拉取 issue、rule、snippet、必要上下文
2. `Fix`
   - Claude 直接修改代码
3. `Build`
   - 运行当前 issue 的本地构建验证
4. `Light Check`
   - 判断当前 issue 是否大概率已修
5. `New Blocker Check`
   - 判断本轮 patch 是否引入新的硬阻塞
6. `Retry or Next`
   - 未通过则继续修当前 issue
   - 通过则进入下一个 issue

### 3.2 目标判定

单 issue 的本地闭环不再依赖一堆前置拦截，而是固定按以下结果判断：

- `PASS`
  - build 通过
  - 当前 issue 的轻量校验明确通过
  - 没有新的硬阻塞
- `FAIL`
  - build 失败
  - 或当前 issue 的轻量校验明确失败
  - 或出现新的硬阻塞
- `UNKNOWN`
  - build 通过
  - 当前 issue 暂时无法可靠本地判断
  - 没有新的硬阻塞

其中 `UNKNOWN` 在 headless 模式下是合法状态，不能一律继续死循环。

### 3.3 单 issue 的完成条件

推荐定义：

1. `PASS`
   - 进入下一个 issue
2. `UNKNOWN but clean`
   - 也允许进入下一个 issue
   - 但要在 artifact 中明确标记“待最终 Sonar 回查”
3. `FAIL`
   - 继续修当前 issue

---

## 4. 设计原则

### 4.1 build-first，而不是 precheck-first

对纯 headless 修复链路，build 才是首要硬门禁。

以下内容不能再默认作为 build 前 hard stop：

1. `semantic_precheck`
2. `quality_gate`
3. `propagation_check`
4. `review_gate`
5. 复杂 planner archetype 约束

这些能力如果继续保留，只能以以下方式存在：

1. soft warning
2. build 后 post-check
3. retry 提示素材
4. 规则族 guidance，而不是强阻断

### 4.2 issue 优先，而不是模板优先

当前系统很多规则是在引导模型“按工程模板修”，而不是“把当前 issue 修掉”。

调整后应统一到：

1. 当前 issue 是唯一主目标
2. 只要 patch 没越界、可编译、没引入新的硬阻塞，就优先保留
3. 不要因为风格型、启发式、偏好型规则提前打回

### 4.3 让判断更 deterministic

真正作为“继续修 / 进入下一个 issue”依据的检查，应该尽量 deterministic。

优先级应为：

1. 编译器错误
2. 语言兼容性
3. 公开签名破坏
4. 明确的 async/type 破坏
5. 当前 rule 的本地校验

不应作为主判定依据的内容：

1. prompt 偏好
2. soft reviewer finding
3. 启发式审美检查
4. 复杂 repair archetype 推理

### 4.4 把“常见 Sonar 规则经验”收敛成统一知识源

建议保留一份轻量规则知识源，但它的用途应分离：

1. 给模型做修复前后自检
2. 给代码做轻量 issue validator / blocker checker 配置

不能再出现：

1. prompt 一套
2. checker 一套
3. retry guidance 一套
4. planner lesson 再来一套

---

## 5. 最小能力边界

### 5.1 本轮保留的能力

以下能力保留，并成为 headless 最简闭环的主骨架：

1. Sonar issue / rule / snippet 拉取
   - `src/integrations/sonar.py`
2. 工作区准备、逐 issue 串行处理
   - `src/core/run_coordinator.py`
3. 单 issue retry 框架
   - `src/core/issue_retry.py`
4. Claude Code 工具调用与 patch 获取
   - `src/agent/claude_agent.py`
5. diff/boundary 审计
   - `src/core/boundary_runtime.py`
   - `src/core/diff_reviewer.py`
6. 本地 build
   - `src/core/fix_verifier.py`
7. 已有 rule-specific validator
   - `src/agent/rule_validators.py`

### 5.2 本轮下沉或关闭的能力

以下能力不再作为最简闭环的主驱动：

1. `IssuePlanner` 中过重的修法规划
2. build 前 `semantic_precheck` hard stop
3. build 前 `quality_gate` hard stop
4. `propagation_verifier`
5. `review_gate`
6. `fast_compile`
7. 大量基于 archetype 的 prompt 注入

这些能力保留但调整定位：

1. 作为 `strict mode` 的附加功能
2. 作为 `simple_loop` 的 soft diagnostics
3. 作为调试和审查工件

### 5.3 本轮完全不作为 headless 主链能力的内容

以下能力不应成为当前 headless agent 的主判定基础：

1. `SonarQube for IDE + analyze_file_list`
2. IDE sidecar
3. 按代码位置的精确 Sonar 再扫描
4. PR 后 Sonar 回查

这些能力未来可以是增强链路，但不是最简闭环的前提。

---

## 6. 新的执行模式

建议新增统一模式：

- `ISSUE_EXECUTION_MODE=simple_loop`

目标：

1. 不推翻现有复杂模式
2. 默认走最简闭环时用 `simple_loop`
3. 保守验证或批量审计场景再回到 `strict` 模式

建议同时保留：

- `ISSUE_EXECUTION_MODE=strict`

### 6.1 simple_loop 的行为

在 `simple_loop` 下：

1. prompt 极简化
2. planner 只保留最小 scope/boundary 信息
3. verifier 变为 build-first
4. 轻量 issue 校验和新阻塞检查取代大量 build 前门禁
5. retry feedback 只保留“修当前 issue 所需的关键信息”

### 6.2 strict 的行为

`strict` 保留现有工程能力，但不作为默认主链。

适用场景：

1. 高风险规则
2. 多文件传播
3. 大范围签名重构
4. 离线审查/回放

---

## 7. 新的判定模型

### 7.1 轻量 issue 校验

新增统一结果结构：

```python
@dataclass(frozen=True)
class IssueCheckResult:
    status: str          # PASS / FAIL / UNKNOWN
    summary: str
    findings: tuple[str, ...] = ()
```

作用：

1. 判断当前 issue 是否大概率已修
2. 不要求完全复刻 Sonar
3. 明确允许 `UNKNOWN`

推荐优先实现的规则：

1. `S3358`
2. `S3776`
3. `S107`
4. `S1172`
5. `S1066`
6. `S1481`
7. `S1144`

实现原则：

1. 能 deterministic 的，返回 `PASS/FAIL`
2. 无法可靠判断的，返回 `UNKNOWN`

### 7.2 新阻塞检查

新增统一结果结构：

```python
@dataclass(frozen=True)
class NewBlockerCheckResult:
    status: str          # PASS / FAIL
    blockers: tuple[str, ...] = ()
    summary: str = ""
```

新阻塞只检查真正会破坏闭环的硬问题：

1. 编译错误
2. 语言特性不兼容
3. async 合同破坏
4. 公开签名/接口实现破坏
5. helper 类型形状破坏
6. 越界修改

不应纳入新阻塞的内容：

1. soft quality gate
2. 偏好型命名提醒
3. 注释语言建议
4. reviewer 风格建议

### 7.3 最终 post-check 结果

新增统一结果结构：

```python
@dataclass(frozen=True)
class PostFixCheckResult:
    issue_status: str            # PASS / FAIL / UNKNOWN
    issue_check: IssueCheckResult
    blocker_check: NewBlockerCheckResult
    retry_message: str
```

最终决策：

1. `PASS + no blockers` -> 当前 issue 完成
2. `UNKNOWN + no blockers` -> 当前 issue 可通过，但标记待 Sonar 最终回查
3. `FAIL` -> 继续修当前 issue

---

## 8. 提示词与知识源的调整

### 8.1 prompt 要变成真正的 issue-fix prompt

当前 prompt 层注入了太多运行时和策略型信息。`simple_loop` 下应收敛为：

1. issue key
2. rule id / message
3. 文件路径 / 行号
4. Sonar snippet
5. rule fix guidance
6. 允许修改范围
7. 如果引入新的编译错误或硬阻塞，继续修到通过为止

不再默认注入：

1. 大量 repair archetype
2. propagation 预算
3. review gate 术语
4. 复杂 precheck taxonomy

### 8.2 规则经验收敛为单一知识源

建议新增：

- `data/sonar_light_check_catalog.yaml`

用途：

1. 为模型提供 rule-family 自检提示
2. 为代码提供 issue validator / blocker checker 配置

建议字段：

```yaml
csharpsquid:S3776:
  family: complexity
  self_check:
    - 优先原方法内收口
    - 不要把匿名类型跨 helper 边界传递
    - helper 默认 private/sync
  issue_validator: heuristic_complexity_reduced
  blocker_checks:
    - helper_type_shape_break
    - async_contract_break
```

### 8.3 规则经验文档

建议新增：

- `docs/sonar-fix-playbook.md`

内容按规则族组织，不按所有 rule 平铺：

1. `Complexity`
2. `Signature`
3. `Async`
4. `Type Safety`
5. `Compatibility`
6. `Minimal Patch`

定位：

1. 模型自检手册
2. 审查人员阅读材料
3. 不作为硬门禁本身

---

## 9. 目录结构调整建议

当前 `src/core` 中承载了过多同时服务 strict 和 simple 的逻辑。为避免后续继续交叉污染，建议按“主闭环 vs 扩展能力”重新组织。

### 9.1 建议新增目录与文件

新增：

- `src/core/simple_mode.py`
  - simple mode 开关和配置解析
- `src/core/simple_post_check.py`
  - build 后 issue 校验 + 新阻塞检查
- `src/core/issue_checkers.py`
  - 各规则轻量校验实现
- `src/core/blocker_checkers.py`
  - 通用新阻塞检查实现
- `src/core/simple_retry.py`
  - simple loop 专用 retry message 组装
- `data/sonar_light_check_catalog.yaml`
  - 统一知识源
- `docs/sonar-fix-playbook.md`
  - 模型/人工共用修复手册

### 9.2 建议调整现有文件职责

#### `src/core/fix_verifier.py`

调整前：

1. build 前门禁和 build 后验证混在一起

调整后：

1. strict mode：
   - 保留现有 layered verification
2. simple_loop：
   - 只做 `boundary -> build -> simple_post_check`

#### `src/core/attempt_scheduler.py`

新增执行模式分支：

1. `strict`
2. `simple_loop`

不要只通过一堆 flag 拼出行为。

#### `src/core/issue_prompt.py`

拆成两条 builder：

1. `build_strict_prompt(...)`
2. `build_simple_loop_prompt(...)`

#### `src/core/issue_retry.py`

增加 simple loop 分支：

1. 不再拼接大量 verifier taxonomy
2. 只保留当前 issue 未修原因和新阻塞信息

#### `src/core/issue_planner.py`

在 simple mode 下退化为：

1. 只生成最小 `EditContract`
2. 只保留 scope/boundary/target file
3. 不再主动注入复杂 archetype / propagation / fallback chain

### 9.3 建议下沉到 `strict` 子域的能力

后续可以逐步考虑把这些移到单独命名空间，例如 `src/core/strict/`：

1. `semantic_precheck.py`
2. `propagation_verifier.py`
3. `review_gate.py`
4. `repair_plan.py`
5. `failure_fingerprint.py`
6. `lessons_store.py`

目的不是现在就大重构，而是明确：

1. 它们不是 headless 最简闭环的主链模块
2. 后续维护时不会再误加到 simple loop 主链里

---

## 10. 模块职责重划分

### 10.1 建议的最简主链模块

最终建议把主链收敛为以下 5 个模块：

1. `IssueFetcher`
   - 来源：`integrations/sonar.py`
   - 职责：拉 issue / rule / snippet
2. `FixLoop`
   - 来源：`agent/claude_agent.py`
   - 职责：生成 patch
3. `BuildVerifier`
   - 来源：`core/fix_verifier.py`
   - 职责：运行编译
4. `PostFixChecker`
   - 来源：新增 `core/simple_post_check.py`
   - 职责：轻量 issue 校验 + 新阻塞检查
5. `RetryOrNext`
   - 来源：`core/issue_retry.py`
   - 职责：决定继续修当前 issue 还是处理下一个

### 10.2 非主链模块的定位

以下模块视为增强链路或 strict mode：

1. `IssuePlanner`
2. `SemanticPrecheck`
3. `PropagationVerifier`
4. `QualityGateVerifier`
5. `ReviewGate`
6. `FailureFingerprint`

这些模块不能再默认参与每一轮 headless 修复闭环。

---

## 11. 实施步骤

### P0：把最简闭环模式跑起来

#### P0-1 新增执行模式

实现项：

1. 新增 `ISSUE_EXECUTION_MODE`
2. 默认支持：
   - `simple_loop`
   - `strict`
3. 在 `claude_agent.py`、`attempt_scheduler.py` 接线

完成标准：

1. 日志中明确输出当前 execution mode
2. artifact 中记录当前 execution mode

#### P0-2 build-first verifier

实现项：

1. `simple_loop` 下改为 `boundary -> build -> post_check`
2. `semantic_precheck`、`quality_gate`、`review_gate`、`propagation_check` 不再 build 前阻断

完成标准：

1. real attempt 不再因为启发式 precheck 在 build 前被挡住
2. build 成为首要硬门禁

#### P0-3 极简 prompt

实现项：

1. 为 `simple_loop` 单独生成 prompt
2. 去掉大部分 repair archetype / retry taxonomy

完成标准：

1. prompt 中只保留修当前 issue 所必需的信息
2. 模型不会再被大量工程术语带偏

### P1：补齐轻量 issue 校验和新阻塞检查

#### P1-1 新增 `simple_post_check.py`

实现项：

1. 统一 `IssueCheckResult`
2. 统一 `NewBlockerCheckResult`
3. 统一 `PostFixCheckResult`

完成标准：

1. 单 issue 重试只围绕这三个结果运转

#### P1-2 先补高频规则校验

优先规则：

1. `S3358`
2. `S3776`
3. `S107`
4. `S1172`
5. `S1481`
6. `S1144`

完成标准：

1. 至少覆盖当前批次中高频、价值最高的规则
2. 不能可靠判断的规则明确返回 `UNKNOWN`

#### P1-3 新阻塞规则集

实现项：

1. `compile_errors`
2. `language_feature_incompatible`
3. `async_contract_break`
4. `public_signature_drift`
5. `helper_type_shape_break`
6. `forbidden_scope_change`

完成标准：

1. 新阻塞只保留真正影响 issue 闭环的硬问题

### P2：统一知识源和目录收口

#### P2-1 新增轻量规则目录

实现项：

1. 新增 `data/sonar_light_check_catalog.yaml`
2. prompt / issue checker / blocker checker 统一读取

完成标准：

1. 文档和代码不再各写一套规则经验

#### P2-2 新增修复手册

实现项：

1. 新增 `docs/sonar-fix-playbook.md`
2. 只按规则族组织

完成标准：

1. 既能给模型用，也能给人工审查用

#### P2-3 strict 能力边界收口

实现项：

1. 明确 strict / simple 的职责边界
2. 逐步将 strict 侧重模块隔离

完成标准：

1. 后续开发不会再把 strict 功能默认回灌到 simple loop 主链

---

## 12. 测试策略

### 12.1 单元测试

新增测试建议：

- `tests/test_simple_post_check.py`
- `tests/test_issue_checkers.py`
- `tests/test_blocker_checkers.py`
- `tests/test_simple_loop_prompt.py`
- `tests/test_execution_mode.py`

### 12.2 回归测试

重点覆盖：

1. build 通过但 rule 校验 `FAIL`
2. build 通过且 rule 校验 `UNKNOWN`
3. build 通过但出现新阻塞
4. build 失败后 retry message 是否简洁聚焦
5. `strict` 与 `simple_loop` 行为隔离

### 12.3 真实日志回放

优先使用真实失败 case 回放：

1. `S3776` 复杂度问题
2. helper 提取引发 async/type/signature 风险的问题
3. 当前已知容易被 precheck 误伤的 case

---

## 13. 完成定义

本方案落地完成后，至少应满足：

1. 纯 headless 下可以不依赖 IDE/MCP 桌面分析完成逐 issue 修复
2. 单 issue 主链收敛为 `fix -> build -> light issue check -> new blocker check -> retry/next`
3. build 前 heuristic 拦截显著减少
4. retry feedback 明显更短、更聚焦当前 issue
5. 出现 `UNKNOWN` 时系统不会陷入无意义循环
6. strict 模式与 simple loop 模式职责清晰
7. 目录结构中能明显看出“主闭环模块”和“增强/严格模块”的边界

---

## 14. 不在本方案内的内容

以下内容明确不纳入本轮 headless 最简闭环主实施范围：

1. PR 后 Sonar 正式闭环回查
2. SonarQube for IDE sidecar
3. 指定代码位置的精确 Sonar 再扫描
4. 再新增一轮比当前更重的 planner/precheck 体系

---

## 15. 一句话总结

`pi-sonar-agent` 的下一个阶段，不应该继续往“更复杂的限制系统”演化，而应该回到 issue 修复 agent 的本质：

1. 让模型先把 issue 修出来
2. 用 build 和轻量校验做闭环
3. 只对真正的硬问题进行拦截
4. 不让大量启发式限制抢走主链控制权

这就是纯 headless 场景下最现实、最稳、也最容易持续提升成功率的最简闭环方案。
