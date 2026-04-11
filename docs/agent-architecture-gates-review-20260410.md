# pi-sonar-agent 当前架构、门禁与未解决问题梳理

更新时间：2026-04-10（基于当前仓库源码与本地运行工件）

主要证据来源：

- `src/main.py`
- `src/batch_runner.py`
- `src/core/run_coordinator.py`
- `src/core/issue_retry.py`
- `src/agent/claude_agent.py`
- `src/core/issue_planner.py`
- `src/core/issue_contract.py`
- `src/core/editor_policy.py`
- `src/core/policy.py`
- `src/core/boundary_runtime.py`
- `src/core/diff_reviewer.py`
- `src/core/quality_gate.py`
- `src/core/quality_gate_verifier.py`
- `src/core/fix_verifier.py`
- `src/fixers/build_gate.py`
- `logs/runs/batch_20260410013022.log`
- `logs/runs/batch_20260410104638.log`
- `logs/run_artifacts/20260410013022/run_summary.json`
- `logs/run_artifacts/20260410104638/events.jsonl`
- `logs/run_artifacts/20260410104638-01/events.jsonl`

## 1. 结论摘要

- 当前 agent 已经不是单体脚本，而是“入口层 -> 目标编排层 -> issue 重试层 -> 模型运行时层 -> 规划/合同层 -> 验证门禁层 -> 交付层”的分层结构。
- 当前真正的核心编排骨架是：
  `run.py / pi_sonar_agent.main / pi_sonar_agent.batch_runner -> RunCoordinator -> process_issue_with_retries -> ClaudeFixAgent.fix_issue -> FixVerifier -> RunCoordinator final build/PR`
- 当前“门禁”不是一个点，而是至少 7 层：
  启动前校验、plan/contract 预检、工具与编辑门禁、boundary reviewer、C# quality gate、rule validator、本地 build gate、目标级 final build/PR gate。
- 2026-04-10 01:30 这轮批量运行（`batch_20260410013022.log`）一共处理 40 个 issue，结果是：
  20 个修复成功，20 个跳过，0 个 failed，最终 target build 通过，并创建了 PR 22266。
- 但 `run_summary.json` 的 target/run 状态仍是 `partial`，因为还有 20 个 issue 没有被自动修掉。
- 这 20 个未完成 issue 里，13 个是策略性跳过，不是运行失败；真正暴露 agent 当前能力边界的，是剩下 7 个：
  2 个 `build`、3 个 `quality_gate`、1 个 `plan_conflict`、1 个 `no_change`。
- 2026-04-10 10:46 这轮运行（`batch_20260410104638.log`）只落下了 `run_started` 和 `target_started`，没有任何 `issue_started/attempt_started`。根据现有工件，我推断这轮是在 issue 实际处理前就被人工中断，或者进程提前退出；它没有产生新的可诊断修复失败。

## 2. 当前 agent 真实架构

### 2.1 入口与包结构

- 发布入口由 `pyproject.toml` 声明：`pi-sonar-agent = "pi_sonar_agent.main:main"`
- 本地单目标入口：
  `run.py -> src/main.py`
- 批量入口：
  `python -m pi_sonar_agent.batch_runner data/targets.json -> src/batch_runner.py`
- 标准导入路径已经统一成 `pi_sonar_agent.*`
- 但真实实现主要还在：
  `src/core`、`src/agent`、`src/fixers`、`src/integrations`
- `src/pi_sonar_agent/*` 当前主要是桥接层，不是主实现层。

### 2.2 分层视图

| 层级 | 主要文件 | 职责 |
| --- | --- | --- |
| 入口层 | `src/main.py`, `src/batch_runner.py` | 读取配置、初始化运行环境、启动单目标或批量编排 |
| 目标编排层 | `src/core/run_coordinator.py` | preflight、拉 issue、准备工作区、循环处理 issue、最终 build、PR、通知 |
| issue 重试层 | `src/core/issue_retry.py` | issue 级 baseline、3 次重试、回滚、落工件、写 state/event |
| issue 执行层 | `src/agent/claude_agent.py` | 构造 prompt、组装 contract、驱动单次模型 attempt、收集变更、触发验证 |
| 模型运行时层 | `src/core/agent_runtime.py`, `src/core/claude_adapter.py`, `src/core/model_gateway.py`, `src/core/policy.py` | Claude SDK 适配、工具策略、超时、continuation retry、patch salvage |
| 规划与合同层 | `src/core/issue_planner.py`, `src/core/issue_contract.py`, `src/core/editor_policy.py`, `src/core/issue_prompt.py`, `src/core/resource_loader.py` | 生成 EditContract、RepairPlan、执行 profile、prompt 约束、patch-only 策略 |
| 门禁验证层 | `src/core/boundary_runtime.py`, `src/core/diff_reviewer.py`, `src/core/quality_gate_verifier.py`, `src/core/fix_verifier.py`, `src/agent/rule_validators.py`, `src/fixers/build_gate.py` | boundary review、quality gate、规则校验、本地 build/test gate |
| 状态与工件层 | `src/core/artifact_writer.py`, `src/core/state.py`, `src/core/state_store.py`, `src/core/events.py` | `run/target/issue/attempt` 状态、JSON 工件、events.jsonl |
| 外部集成层 | `src/integrations/sonar.py`, `src/integrations/ado.py`, `src/core/db_client.py`, `src/core/dingtalk.py` | SonarQube、Azure DevOps、ERP4 收件人解析、钉钉通知 |

### 2.3 单个 issue 的真实调用链

1. `RunCoordinator.run_target()` 拉取 Sonar issues、clone 基线分支、逐个 issue 循环。
2. 每个 issue 进入 `process_issue_with_retries()`。
3. `process_issue_with_retries()` 先对 issue 建 baseline，再调用 `ClaudeFixAgent.fix_issue()`。
4. `ClaudeFixAgent.fix_issue()`：
   - 读取源码上下文
   - 生成 `IssuePlan + EditContract + RepairPlan`
   - 构造 system/user prompt
   - 通过 `AgentRuntime` 跑 Claude SDK
   - 收集 diff
   - 调 `FixVerifier.evaluate_attempt()` 做验证
5. 如果 attempt 失败：
   - `issue_retry.py` 会写 attempt artifacts
   - 构造 `RetryContext`
   - 记录 lessons
   - 恢复到该 issue 的 baseline
   - 再试下一次
6. issue 成功后回到 `RunCoordinator`，继续下一个 issue。
7. 全部 issue 结束后，`RunCoordinator` 做 final build。
8. 只有 final build 通过且 `successful > 0` 时，才会 publish branch + create PR + upload PR report + DingTalk。

## 3. 当前门禁体系

## 3.1 门禁总表

| 门禁层 | 主要组件 | 阻断级别 | 当前实际行为 |
| --- | --- | --- | --- |
| G0 启动前校验 | `preflight.py` | 硬门禁 | 校验模型环境、必填 env、workspace 可写、远端基线分支存在 |
| G1 Plan/Contract 预检 | `IssuePlanner.plan_issue()` | 硬门禁 | 在 edit 前检查 repair plan 是否与当前 contract capability 冲突 |
| G2 工具/编辑门禁 | `EditorPolicy`, `ToolPolicy` | 硬门禁 | patch-only 模式下禁 `Write`；shell 只允许只读/无害命令；禁止 git 污染和高风险 shell 写入 |
| G3 Boundary reviewer | `BoundaryRuntime`, `DiffReviewer` | 混合 | 受保护路径/建删文件是硬拒绝；额外文件和主区域外行目前只做 soft drift audit |
| G4 C# 质量门禁 | `QualityGateVerifier` | 硬+软 | `public_xml_docs/async_signature/async_requires_await/linq_method_syntax/cognitive_complexity` 是硬门禁；其余规则只记 soft finding |
| G5 Rule-specific validator | `rule_validators.py` | 硬门禁 | 当前只实现了 `nested_ternary_removed` 这类特定规则校验 |
| G6 attempt build gate | `FixVerifier.run_local_build()` | 硬门禁 | 只有 boundary/quality/rule 预检通过时才跑 build；build 失败会触发 retry |
| G7 target final build/PR gate | `RunCoordinator` + `LocalBuildGate` 语义 | 硬门禁 | final build 不通过则不创建 PR；只保留本轮成功 issue 的改动 |

## 3.2 各门禁详细说明

### G0. 启动前校验

位置：

- `src/core/preflight.py`
- `src/core/run_coordinator.py`

内容：

- `validate_agent_env()` 校验模型配置
- `require_env()` 校验 `SONARQUBE_*`、`ADO_*`
- `ensure_workspace_writable()` 校验工作区可写
- `ensure_remote_branch_exists()` 校验远端 `base_branch` 是否存在

效果：

- 如果基线分支不存在、工作区不可写、模型配置不完整，整个 target 在 issue 开始前就失败

### G1. Plan / EditContract / RepairPlan 预检

位置：

- `src/core/issue_planner.py`
- `src/core/issue_contract.py`

内容：

- 根据 rule policy、scope mode、boundary profile、历史 lessons 生成 `EditContract`
- 对复杂 rule 生成 `RepairPlan`
- 若 `RepairPlan` 需要的能力超出 contract，例如 `signature_change`、`multi_file_refactor` 未被放开，`plan_precheck` 直接阻断

典型产物：

- `target_files`
- `allowed_capabilities`
- `validation_plan`
- `allowed_line_ranges`
- `quality_gate_rules`
- `execution_profile`
- `repair_plan`
- `plan_precheck`

运行 profile：

- `fast_path_short_form`
- `full_path`
- `plan_first_full_path`

### G2. 工具与编辑门禁

位置：

- `src/core/editor_policy.py`
- `src/core/policy.py`
- `src/core/issue_prompt.py`

内容：

- `patch_only=True` 时，工具白名单会去掉 `Write`
- shell/Bash 允许搜索、查看、诊断，但禁止：
  删除文件、创建文件、整文件覆盖、move/copy/rename 等高风险操作
- prompt 里会显式注入：
  active quality gate、rule guards、Edit Contract、Repair Plan、prefetched context、tool surface constraints

效果：

- 模型默认只能用 `Read + Edit + 受控 Bash + Finish`
- forbidden tool 会直接把该 attempt 判废

### G3. Boundary reviewer

位置：

- `src/core/boundary_runtime.py`
- `src/core/diff_reviewer.py`
- `src/core/boundary_policy.py`

当前硬规则：

- 触碰 `.git/`、`logs/`、`.agent_workspaces/` 等受保护路径
- 新建文件
- 删除文件

当前软规则：

- `extra_touched_file`
- `outside_primary_region`

软规则的处理方式：

- 只记到 `reviewer_result` 和 `follow_ups`
- 不会直接 hard fail 当前 attempt

### G3 的关键实现现状

这是当前门禁实现里最需要向 Claude 说明的一点：

- 虽然 `guardrail_mode` 默认仍是 `scope`
- 虽然 `validation_plan` 里会写 `scope_review`
- 但当前 `BoundaryRuntime.review()` 实际只调用了 `DiffReviewer.review()`
- 它没有真正消费 `scope`
- 也没有真正调用传入的 `scope_validator`

这意味着当前的真实 hard boundary 已经更接近：

- 文件系统边界硬门禁
- 同文件额外漂移软审计

而不是早期那种“只要改出 validation window 就硬拒绝”的 line-level scope hard fail。

这个实现现状与部分旧文档里“默认是 scope guard 主链路”的描述已经不完全一致。

日志侧证据也支持这一点：

- `run_summary.json` 中 `scope_reject_rate = 0.0`
- `boundary_hard_reject_rate = 0.0`
- 本轮 20 个 skipped 里，没有一个最终失败类型是 `scope`

### G4. C# Quality Gate

位置：

- `src/core/quality_gate.py`
- `src/core/quality_gate_verifier.py`
- 规则真源：`data/csharp-quality-gate.md`

规则加载方式：

- 通过 markdown front matter 读结构化规则
- 只把当前文件适用的规则注入 prompt 和 verifier

当前硬门禁：

- `public_xml_docs`
- `async_signature`
- `async_requires_await`
- `linq_method_syntax`
- `cognitive_complexity`

当前软信号：

- `zero_redundant_code`
- `static_preferred`
- `sealed_preferred`
- `constructor_dependency_injection`
- `business_comments_chinese`
- `finance_terms_chinese`

验证范围：

- changed lines
- changed public symbols
- changed methods
- changed comments

### G5. Rule-specific validator

位置：

- `src/agent/rule_policies.py`
- `src/agent/rule_validators.py`

当前状态：

- 本地 rule validator 还比较少
- 现有明确实现的是 `nested_ternary_removed`
- 主要用于像 `S3358` 这种模型容易“表面改了，实质没改掉”的规则

### G6. attempt 级 build gate

位置：

- `src/core/fix_verifier.py`
- `src/core/attempt_scheduler.py`

当前顺序是：

1. boundary review
2. quality gate
3. rule validator
4. build

并且 `layered_verification` 打开时：

- 如果 boundary / quality gate / rule validator 先失败
- 就会直接 `skip_build_on_precheck_failure`
- 不再浪费一次本地 build

这也是为什么本轮运行里：

- `attempt_count = 58`
- 但 `build_invocation_rate = 0.3966`

很多 attempt 根本没有进入 build。

### G7. target 级 final build / PR gate

位置：

- `src/core/run_coordinator.py`
- `src/fixers/build_gate.py`

当前行为：

- 所有 issue 处理完后，对保留的成功改动做 final build
- `should_create_pr = build_passed and successful > 0 and not options.skip_build`
- 也就是说：
  final build 不通过，不会创建 PR
- 只要 final build 通过，即使还有 skipped issue，也可以创建 PR

这也是为什么：

- `20260410013022` 这轮虽然有 20 个 skipped
- 但因为 20 个 fixed 的合并结果 final build 通过
- 所以仍然成功创建了 PR 22266

## 4. 2026-04-10 运行结果解读

### 4.1 `batch_20260410013022.log`

时间：

- 日志文件名对应本地时间 2026-04-10 01:30:22

整体结果：

- 目标数：1
- issue 数：40
- attempts：58
- 修复成功：20
- 跳过：20
- failed：0
- final build：通过
- PR：已创建，ID 22266
- run/target 状态：`partial`

跳过原因分布：

- `policy_skip`：13
- `quality_gate`：3
- `build`：2
- `plan_conflict`：1
- `no_change`：1

说明：

- 这轮不是“整体失败”，而是“半自动成功”
- 成功产出了一部分修复和 PR
- 但仍有 7 个真正的自动修复失败点没有打通

### 4.2 `batch_20260410104638.log`

时间：

- 日志文件名对应本地时间 2026-04-10 10:46:38

现有证据：

- `logs/run_artifacts/20260410104638/events.jsonl` 只有 `run_started`
- `logs/run_artifacts/20260410104638-01/events.jsonl` 只有 `target_started`
- 没有任何 `issue_started`
- 没有任何 `attempt_started`
- `logs/issue_attempts` 下也没有 `*20260410104638*` 的 issue log

基于这些工件，我的推断是：

- 这轮运行没有真正进入 issue 处理
- 更像是启动后被人工中断，或进程在 issue loop 前提前终止

因此：

- `20260410104638` 不提供新的“无法修复 issue”证据
- 真正可诊断的未解决问题仍然主要来自 `20260410013022`

## 5. 当前未解决问题

下面把“策略性不处理”和“真实自动修复失败”分开写。

## 5.1 策略性不处理，不应与能力失败混淆

共 13 个：

- `csharpsquid:S107`：12 个
  含义是“方法参数过多”
  当前策略默认跳过，因为通常需要跨调用点重构
- `csharpsquid:S6960`：1 个
  含义是“Controller 职责拆分”
  当前策略默认跳过，因为属于架构级改造

这类 issue 不是“agent 修不动”，而是当前产品策略明确不自动修。

## 5.2 真正暴露能力边界的 7 个 issue

### A. Build 失败型，核心问题是“跨签名传播做了一半”

1. `0fb3e377-0955-41cb-8cae-729a28012d3c`
   规则：`csharpsquid:S3776`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs:171`
   最终失败：`build`
   关键信息：
   - repair plan 明确要把 `AutoPlugin` 改成 `AutoPluginAsync`
   - 同时需要传播到 callsite 和 `IFinanceHanlerApp`
   - 最终 build 报错是 `CS0103: AutoPlugin 不存在`
   说明：
   - 模型已经进入“改签名 + 跨文件传播”模式
   - 但传播没有做完整，导致主方法名和调用点脱节

2. `c2c11128-8890-4742-9c7b-cf2ffb04071a`
   规则：`csharpsquid:S3776`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/Reimburse/Services/ReimburseCommonService.cs:880`
   最终失败：`build`
   关键信息：
   - repair plan 要把 `GetReimburseNeedPrintLst` 改成 `GetReimburseNeedPrintLstAsync`
   - 需要传播到 `ReimburseInfoApp.cs`
   - 最终 build 报 `CS1061` 和 `CS0103`
   - 典型错误是外部仍在调用 `GetReimburseNeedPrintLstAsync`，但实现/辅助方法没有对齐
   说明：
   - 和上一个问题本质一致
   - 当前 agent 对“重构级 S3776 + 跨签名传播”的闭环稳定性不够

### B. Quality Gate 失败型，核心问题是“为解决主问题引入了新的规范债”

3. `a22e301e-b1e0-4a1f-9bec-712001f1a11a`
   规则：`csharpsquid:S3776`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:41`
   最终失败：`quality_gate`
   关键硬违规：
   - `async_requires_await`
   - 具体是：`ResolveOrderDocEntryFromReturnChainAsync` 没有实际 `await`
   说明：
   - 模型为了拆复杂度，引入了异步 helper
   - 但 helper 只是“长得像 async”，并没有真实 `await`
   - 这属于典型的 quality gate 自我击穿

4. `e0df305b-afab-45c6-bd45-641d1ca20b66`
   规则：`csharpsquid:S3776`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:457`
   最终失败：`quality_gate`
   关键硬违规：
   - `public_xml_docs`
   - `async_signature`
   - `async_requires_await`
   具体表现：
   - 新触达的 public property 缺 XML docs
   - `ProcessSingleOrderByRangeOptimized` 没有 `Async` 后缀
   - `LoadOrderPenaltyEventsAsync` 没有真实 `await`
   说明：
   - 这是“重构扩散过宽”的典型案例
   - 一旦 patch 触达 public API 和 async helper，quality gate 立刻放大

5. `bed2ec1c-8990-4b9f-aae6-4ae12fcc4fa6`
   规则：`csharpsquid:S1144`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:942`
   最终失败：`quality_gate`
   关键硬违规：
   - `public_xml_docs`
   - `async_signature`
   - `async_requires_await`
   具体表现：
   - `RemarkDic` 缺 XML docs
   - `GetCompletedOrdersWithHistoricalOverdue` 没有 `Async` 后缀
   - 同名方法没有实际 `await`
   说明：
   - 问题本身是 `S1144`，但模型为了修它，顺带把 async/public surface 搞脏了
   - 这表明当前 prompt 和 planner 对“不要引入新的公开/异步规范债”的约束还不够稳

### C. Plan 冲突型，核心问题是“planner 已知需要改签名，但 contract 没放能力”

6. `fd483c64-1218-485f-9343-30ef2a2b530f`
   规则：`csharpsquid:S3776`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:962`
   最终失败：`plan_conflict`
   具体结论：
   - `RepairPlan` 判断需要 `signature_change`
   - 但 `EditContract.allowed_capabilities` 只有 `method_rewrite + helper_extract`
   - 所以 `plan_precheck` 在 edit 前直接阻断
   说明：
   - 这不是模型失败
   - 是 planner 与 contract capability 协议不一致
   - 当前系统已经“知道修法”，但“不给修”

### D. No-change 型，核心问题是“大方法重构不稳定 + Edit 落盘脆弱”

7. `e536a516-dac6-4cc1-b3df-301e21acdc94`
   规则：`csharpsquid:S3776`
   文件：`/OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1778`
   最终失败：`no_change`
   这是当前最典型的“大方法 refactor 不稳定”案例：
   - 第 1 次 attempt：
     先被 quality gate 打回
     主要是 `public_xml_docs`、`async_signature`、`async_requires_await`
   - 第 2 次 attempt：
     使用 forbidden tool，并引入一组未定义 helper，导致大量 `CS0103/CS8130`
   - 第 3 次 attempt：
     模型长时间阅读和分析
     最后唯一一次 `Edit` 因 `String to replace not found in file` 失败
     整个 attempt 结束时没有任何落盘改动
   说明：
   - 这是“复杂 refactor 方案不稳定 + 精确 patch 命中率不稳”双重问题
   - 也是当前最值得让 Claude 审视的失败样本

## 6. 对门禁体系的重点评价

如果把这份文档交给 Claude，我建议它重点看下面几点。

### 6.1 优点

- 编排骨架已经清晰，单目标/批量入口共用 `RunCoordinator`
- issue 级 baseline、回滚、工件、事件已经完整闭环
- quality gate 是结构化的，不再只是 prompt 口头约束
- build 前置短路策略是合理的，避免无意义 build
- policy skip、plan conflict、quality gate、build、no_change 已经被分成不同 failure kind，后续分析比较容易

### 6.2 关键短板

- `scope` 名义上仍是默认 guardrail mode，但当前真实 hard gate 已不再是 strict scope；实现和文档存在认知偏差
- `S3776` 这类“大方法拆分”问题，当前 planner/contract/quality gate 之间经常互相打架
- 当 repair plan 需要 `signature_change` 时，capability 放开逻辑还不够稳定
- patch-only + 精确 `old_string` 替换在大方法上容易失败，导致 `no_change`
- quality gate 对“新引入 async helper / public member”的压制是对的，但 agent 现在缺少足够稳定的自约束来避免踩进去

### 6.3 我认为最优先的改进点

1. 明确 `scope` 的产品语义
   现在到底是“严格 scope hard fail”，还是“filesystem hard boundary + drift audit”。
   代码、README、PROJECT_GUIDE、RUNBOOK 需要统一口径。

2. 把 `signature_change` / `multi_file_refactor` 的 capability 协议彻底打通
   当前已经有 repair plan 和 propagation targets，但还会出现“planner 知道要改签名，contract 却不允许”的断层。

3. 给大方法 refactor 增加更稳的 edit 落盘策略
   尤其是避免单次超长 `old_string` 替换，降低 `String to replace not found`。

4. 让 planner 在生成复杂 refactor 方案时，提前预算 quality gate 成本
   例如：
   - 是否会新增 public symbol
   - 是否会引入 async helper
   - 是否会触达 XML docs
   - 是否会制造“async 但无 await”

5. 对 `S3776` 建立更强的“最小安全拆分模板”
   当前大部分真正失败样本都集中在这里。

## 7. 一句话判断

当前 agent 已经具备“批量修一半、能产出 PR、失败可诊断”的工程化能力，但它还没有稳定跨过“大方法重构 + 签名传播 + 质量门禁协同”这条线。真正要评估的，不是它能不能修简单 issue，而是它在复杂 `S3776` 场景下，planner、contract、quality gate、build gate 是否已经形成一致闭环。答案是：现在还没有完全闭环。
