# pi-sonar-agent 工程修复与门禁闭环实施计划

更新时间：2026-04-10

输入材料：

- `docs/Claude1.md`
- `docs/Claude2.md`
- `docs/agent-architecture-gates-review-20260410.md`
- `logs/runs/batch_20260410013022.log`
- `logs/runs/batch_20260410104638.log`
- `logs/runs/batch_20260410175347.log`
- `logs/runs/batch_20260410185014.log`

## 1. 目标

本轮计划不是继续“调 prompt 碰运气”，而是把当前 agent 补成一条闭环修复链路：

- 让 planner 在 edit 前就知道签名传播、质量门禁、编辑风险，而不是生成补丁后才被 verifier 打回。
- 让 contract 能对“有限、可控”的复杂修复自动放权，避免 `plan_conflict` 死锁。
- 让 verifier 在 build 前先识别传播不完整和质量门禁高风险，减少无意义重试。
- 让高复杂度重构类规则具备稳定的最小安全拆分策略，降低 `no_change` 和补丁落盘失败。
- 明确坚持“软门禁 scope”策略，不引入 line-level `scope` 硬门禁，避免把模型修复能力锁死在过窄窗口内。
- 让日志和状态工件能明确区分“真正修复失败”和“运行未进入 issue 阶段就退出”。

本轮计划的验收基线是：先打掉 2026-04-10 01:30 这轮里暴露出来的 7 个真实失败点，再补齐 10:46 这轮早退场景的可观测性。

## 1.1 实施进度

- 阶段 A.1：核对 boundary/scope 现有实现与状态工件结构
  状态：`已完成`
- 阶段 A.2：实现 scope 软审计字段与文案对齐
  状态：`已完成`
- 阶段 A.3：实现 early-abort 事件/状态落盘
  状态：`已完成`
- 阶段 A.4：补充阶段 A 回归测试并验证
  状态：`已完成`
  说明：`py_compile` 和脚本级 smoke check 已通过；`pytest` 在当前环境下受到 `tmp_path` 目录权限限制，未能作为唯一验证手段。
- 阶段 B.1：设计并接入 impact/propagation 闭环数据结构
  状态：`已完成`
- 阶段 B.2：实现 propagation completeness check 并接入 verifier
  状态：`已完成`
- 阶段 B.3：实现 fast compile 短路层
  状态：`已完成`
- 阶段 C.1：为 `RepairPlan` 引入 `selected_archetype` / `fallback_archetype` / `archetype_chain`
  状态：`已完成`
- 阶段 C.2：把 archetype 约束和 `constraint_hints` 注入 planner/prompt
  状态：`已完成`
- 阶段 D.1：实现 Edit 失败后的最近匹配片段回传
  状态：`已完成`
- 阶段 E.1：实现 `failure_detail_key` / `strategy_fingerprint` / `diff_fingerprint` 与提前终止
  状态：`已完成`
- 阶段 F.1：细化 rollout flag，并把 verifier/runtime 分层开关接入调度
  状态：`已完成`
- 阶段 F.2：补充阶段 C-F 验证
  状态：`已完成`
  说明：本轮 `py_compile` 已通过；`stage-cdef-smoke-ok` 与 `verifier-fast-compile-smoke-ok` 已通过。`.venv` 下的 `pytest` 仍被当前环境的 `tmp_path` / `basetemp` 权限异常干扰，已通过 `junitxml` 确认首个报错发生在 fixture setup/cleanup，不是本轮代码语法错误。
- 热修 G.1：归一化模型输出的包装工具名（如 `<tool_call>Bash`）
  状态：`已完成`
- 热修 G.2：在 issue 级 build / fallback build / 最终 build 中，对 NuGet restore/source 故障增加 `--no-restore` 离线重试
  状态：`已完成`
- 热修 G.3：基于 `batch_20260410175347` 与 `batch_20260410185014` 的定向回归验证
  状态：`已完成`
  说明：`.venv` 下定向 pytest 已通过；并已使用保留 workspace 对 `FixVerifier.run_local_build()` 与 `fixers.build_gate.run_local_build()` 做真实工程验证。
- 热修 G.4：修正 fresh clone 工作区上 `fast compile --no-restore` 被 `project.assets.json` 缺失误伤的问题
  状态：`已完成`
  说明：当 `fast compile` 命中 `NETSDK1004 / project.assets.json` 缺失时，不再短路 full build，而是放行到 full build 执行 restore+build。
- 热修 G.5：补回 SonarQube `descriptionSections` / `textRange` / `flows` 到主 prompt 链路
  状态：`已完成`
  说明：规则说明不再只依赖旧版 `mdDesc/mdNote` 字段；当 Sonar 仅返回 `root_cause/default` 等 section 时，也会生成可用 guidance。同时把 `textRange/flows` 注入 issue 与 prompt，给模型精确主定位和关联位置。
- 热修 G.6：默认 issue 重试上限从 `3` 提升到 `5`，并把下一轮实际喂给模型的 retry feedback 写入 issue log
  状态：`已完成`
  说明：默认运行现在会在门禁/构建可恢复失败上最多重试 5 次；连续同类失败的提前终止不再抢在前 5 次之前触发。每次被 quality gate / reviewer / build 等门禁打回后，结构化失败原因都会继续回灌给下一轮模型，并在 `logs/issue_attempts/*.log` 中显式记录。

## 1.2 热修补充说明（2026-04-10 17:53 / 18:50 两轮）

- `batch_20260410175347` 的前半段失败主因不是“没走 endpoint”，而是 `client_connect_timeout`；同时旧版超时诊断探针会把第三方 provider 的 env 驱动模型错误改写成 CLI `--model` 探针，放大误诊信息。
- `batch_20260410185014` 的真实回归点有两个：
  - 模型偶发输出 `<tool_call>Bash` 这类包装工具名，被 runtime 当成未知/禁用工具，直接触发 `forbidden_tool`。
  - issue 级和最终级 build 仍可能走会触发 NuGet 远程恢复的路径；网络/DNS 抖动时，即使 workspace 已具备本地构建条件，也会被误判为 patch 失败。
- `batch_20260410212416` 额外暴露了一个新的 verifier 回归：
  - fresh clone 工作区在尚未生成 `obj/project.assets.json` 时就执行 `fast compile --no-restore`，导致每个 issue 都被 `NETSDK1004` 短路，full build 根本没有机会执行 restore。
- 对应热修策略已经落地：
  - tool name normalization 前移到 gateway/runtime/policy 三层兜底；
  - build verifier 与最终 build 在命中典型 NuGet source/restore 故障时，自动切换 `--no-restore` 做离线重试。
  - `fast compile` 命中 `NETSDK1004 / project.assets.json` 缺失时，不再把 precheck 结果当作 patch 失败，而是继续进入 full build。

## 2. 当前问题归因

### 2.1 主矛盾

当前 agent 的核心矛盾不是“模型不会修”，而是以下四层没有闭环：

1. `IssuePlanner` 已经能识别部分复杂修复形状，例如 `signature_change`、`helper_extract`、`propagation_targets`。
2. `EditContract` 仍然以最小补丁、patch-only、低扩散为主，复杂能力放开不稳定。
3. `QualityGateVerifier` 和 build gate 主要是事后拦截，约束没有充分前移到 prompt 和 repair plan。
4. retry 机制会记录 lessons，但还没有形成可靠的“失败类型 -> 下一次策略降级/切换”的自动闭环。

结果就是：

- planner 知道某些高复杂度修复天然要拆方法、可能要改异步命名、甚至要跨文件传播；
- contract 有时不给能力，出现 `plan_conflict`；
- 给了能力以后，prompt 仍然缺少足够强的质量约束；
- verifier 最后才发现传播不完整、async helper 不合法或者补丁根本没落下来。

### 2.2 门禁现状中的关键偏差

根据当前源码，`BoundaryRuntime.review()` 真实只调用 `DiffReviewer.review()`，并没有真正消费 `scope` 或执行 `scope_validator`。因此当前门禁的真实形态是：

- 文件系统边界：硬门禁
- 新建/删除文件：硬门禁
- 额外文件触达、主区域外行漂移：以 reviewer 记录和 follow-up 为主

这与“默认 scope line-level hard fail”这类旧表述已经不一致。计划里必须先把语义对齐，否则后续修复逻辑会继续建立在错误认知上。

### 2.3 关于 `scope` 的明确策略

本项目不应引入 line-level `scope` 硬门禁。

原因很直接：

- 复杂度下降、方法拆分、命名修正、签名传播、编译补尾，本来就经常需要越过 issue 原始行窗口。
- 如果把 `scope` 收紧成逐行硬拒绝，模型会优先保守、不敢收尾，最终直接拉低修复率。
- 当前项目真正需要硬卡住的是“破坏性修改”和“客观失败”，不是“超出初始行窗口的合理联动改动”。

因此本项目的边界策略应固定为：

- `scope` 只作为 planner 引导、prompt 提示、diff 审计和人工 review 辅助信息。
- line-level `scope` 不作为 attempt 阻断条件。
- 真正的硬门禁只保留在以下客观风险点：
  - 文件系统受保护路径
  - 新建/删除文件
  - forbidden tool
  - quality gate
  - 传播完整性预检
  - build / final build

### 2.4 用什么替代 line-level `scope` 硬门禁

既然不走 strict scope hard fail，就必须用别的机制兜住风险，而不是完全放飞。

替代控制策略如下：

1. `scope` 改成“软预算 + 软审计”
   - `allowed_line_ranges`、`target_line_range`、`validation_line_range` 继续保留，但只作为首选编辑区域和审计基线。
   - 如果发生范围外改动，只记录 `outside_primary_region`、`extra_touched_file`、`propagation_expansion` 等结构化原因，不直接拒绝。

2. 用“扩散原因”代替“越界即失败”
   - 对额外改动要求归因到有限原因集合：
     - `helper_extraction`
     - `signature_propagation`
     - `compile_fixup`
     - `naming_sync`
     - `quality_gate_cleanup`
   - 无法归因的漂移记为高风险审计项，但不走 line-level hard fail。

3. 用“客观验证”代替“位置验证”
   - 是否可接受，最终看：
     - 单文件/单项目快速编译是否通过
     - quality gate 是否通过
     - propagation completeness 是否通过
     - build 是否通过
     - rule-specific validator 是否通过
   - 也就是“改到了哪里”是软信息，“改完是否正确”才是硬标准。

4. 用“工件可审计”代替“预先限制模型发挥”
   - 将范围外改动写入 attempt artifacts、review summary、PR 报告。
   - 对高扩散补丁增加 reviewer follow-up，而不是在运行时直接掐死。

5. 用“planner 预算”代替“runtime 锁死”
   - planner 可以声明目标文件预算、传播预算、推荐主区域，但默认不把这些预算变成 line-level 阻断。
   - 预算主要用于策略选择、prompt 约束和人工判断，不用于 scope 硬拒绝。

### 2.5 7 个真实失败点与对应改造方向

| 失败 issue | failure kind | 当前根因 | 首要改造方向 |
| --- | --- | --- | --- |
| `0fb3e377-0955-41cb-8cae-729a28012d3c` | `build` | `AutoPlugin -> AutoPluginAsync` 传播做了一半，旧调用仍残留 | 影响面分析 + 传播完整性预检 |
| `c2c11128-8890-4742-9c7b-cf2ffb04071a` | `build` | `GetReimburseNeedPrintLst` 改名/签名传播不完整 | 影响面分析 + 传播完整性预检 |
| `a22e301e-b1e0-4a1f-9bec-712001f1a11a` | `quality_gate` | 修复杂度时引入新的质量债 | 质量门禁前移 + 通用重构模板 |
| `e0df305b-afab-45c6-bd45-641d1ca20b66` | `quality_gate` | async helper / 命名 / public surface 约束未前置 | 质量门禁前移 + 失败后策略降级 |
| `bed2ec1c-8990-4b9f-aae6-4ae12fcc4fa6` | `quality_gate` | 同上，属于“修主问题时自击穿质量门禁” | 质量门禁前移 + 通用重构模板 |
| `fd483c64-1218-485f-9343-30ef2a2b530f` | `plan_conflict` | planner 识别出需要 `signature_change`，contract 没放开 | capability 协商与有限自动升级 |
| `e536a516-dac6-4cc1-b3df-301e21acdc94` | `no_change` | 大方法重构时 `old_string` 无法命中，补丁落盘失败 | 分段编辑策略 + 大方法专用 profile |

补充说明：

- `logs/runs/batch_20260410104638.log` 没有新的修复失败样本，暴露的是“早退出但缺少明确终态事件”的观测问题。
- `policy_skip` 的 13 个 issue 目前不是第一优先级，因为它们是显式策略跳过，不是 agent 失控失败。

## 3. 目标态架构

目标态不是推翻现有架构，而是在现有主链路上补三件事：

1. `Analyze Before Edit`
2. `Constraint First`
3. `Incremental Verify`

目标态主链路如下：

`RunCoordinator`
-> `process_issue_with_retries`
-> `IssuePlanner`
  - 生成 `RepairPlan`
  - 分析签名传播影响面
  - 编译 quality gate 约束
  - 计算能力预算与自动升级
-> `ClaudeFixAgent`
  - 将 repair plan、传播清单、质量约束注入 prompt
  - 根据编辑风险切换执行 profile
-> `FixVerifier`
  - boundary review
  - propagation completeness check
  - quality gate
  - rule validator
  - fast compile
  - local build
-> `RetryContext`
  - 记录失败类型和 lessons
  - 影响下一次 planner 策略

这里要强调两点：

- 第一，不优先重写 Claude SDK 或整套 runtime。先复用现有 `IssuePlanner`、`FixVerifier`、`RetryContext`、`IssuePrompt` 完成闭环。
- 第二，scope 语义先对齐，并明确保持为软门禁；不能再把 line-level scope 恢复成默认硬门禁。

## 4. 实施原则

### 4.1 先修闭环，再做增强

优先处理能直接消灭 7 个真实失败点的改造：

- capability 协商
- 传播分析和预检
- quality gate 前移
- 大方法编辑稳定性

不在第一阶段重做以下高成本项目：

- 自定义完整 `VerifiedEditTool`
- 全规则通用 AST 级重写器
- 全量 rule-specific validator 扩容
- 任何形式的 line-level `scope` 硬门禁回归

### 4.2 所有增强都必须落到工件和测试

每个阶段都必须同时补：

- 结构化状态字段
- 单元测试/回归测试
- 日志与事件

否则后面无法判断“问题真的被修掉了”，还是只是 failure kind 改名。

### 4.3 `scope` 只做软门禁，不做运行时硬拒绝

这是本项目的明确设计约束：

- `scope` 可以用于提示“优先改哪里”
- `scope` 可以用于记录“实际多改了哪里”
- `scope` 可以用于生成 follow-up 和审计摘要
- `scope` 不用于 line-level attempt hard fail

如果后续需要压缩扩散，也应优先通过以下方式实现：

- planner 的修复形态选择更保守
- propagation budget 更清晰
- prompt 约束更明确
- verifier 的客观检查更前置

而不是恢复 strict scope hard gate。

## 5. 分阶段实施计划

## 5.1 阶段 A：统一门禁语义与运行终态观测

优先级：`P0`

目标：

- 把当前 boundary/scope 的真实行为和系统输出对齐。
- 明确将 `scope` 固定为软门禁语义，不再为 line-level 硬阻断预留模糊空间。
- 把“运行未进入 issue 阶段就退出”的情况显式写进 events/state。

建议改动文件：

- `src/core/boundary_runtime.py`
- `src/core/diff_reviewer.py`
- `src/core/issue_planner.py`
- `src/core/events.py`
- `src/core/state.py`
- `src/core/run_coordinator.py`
- `tests/test_boundary_runtime.py`
- `tests/test_boundary_regressions.py`
- `tests/test_run_logging.py`

具体任务：

- 明确 `BoundaryRuntime.review()` 的当前模式名称，区分：
  - `filesystem_boundary_hard_fail`
  - `diff_drift_audit`
  - `scope_soft_audit`
- 给 `BoundaryReviewOutcome` 增加明确字段，避免继续把未执行的 `scope` 检查包装成已生效门禁。
- 清理当前误导性的 scope 文案，使 prompt、state、summary、文档一致，明确 line-level `scope` 不阻断 attempt。
- 把 `scope` 相关字段重新定义为：
  - planner guidance
  - review audit
  - expansion reason tagging
  - follow-up generation
- 为范围扩散增加结构化审计字段，例如：
  - `scope_expansion_count`
  - `scope_expansion_reasons`
  - `extra_file_touch_count`
  - `high_drift_warning`
- 保留文件系统边界、新建/删除文件为硬门禁，但不新增任何 line-level scope hard fail。
- 在 `RunCoordinator`/事件流中增加早退出终态，例如：
  - `run_aborted`
  - `target_aborted_before_first_issue`
  - `startup_failure`
- 保证 `batch_20260410104638` 这类场景以后能通过工件直接判断是人工中断、启动失败还是空跑。

验收标准：

- boundary 相关测试明确覆盖“当前默认不执行 strict scope validator，且后续也不以 line-level scope 作为硬拒绝”。
- 审计工件中能看见范围扩散，但不会因为 line-level 越界直接导致 attempt 失败。
- `run_summary`/`events.jsonl` 能明确区分 early abort 与 issue-level failure。
- 旧文档和新实现语义一致，不再出现“scope 是默认主硬门禁”的歧义。

## 5.2 阶段 B：能力协商、影响面分析与传播验证闭环

优先级：`P0`

目标：

- 消灭 `plan_conflict` 死锁。
- 让签名变更类修复形成完整闭环：分析 -> 注入 -> 验证。
- 让签名变更类修复在 edit 前就知道传播范围，在 build 前就知道是否漏改。
- 在 full build 前增加更快的编译/解析短路层，提前拦截大部分符号残留问题。

建议改动文件：

- `src/core/repair_plan.py`
- `src/core/issue_contract.py`
- `src/core/issue_planner.py`
- `src/agent/claude_agent.py`
- `src/core/fix_verifier.py`
- `src/core/attempt_scheduler.py`
- `src/core/issue_retry.py`
- `tests/test_issue_planner.py`
- `tests/test_claude_agent.py`
- `tests/test_fix_verifier.py`
- `tests/test_attempt_scheduler.py`

必要时新增：

- `src/core/impact_analyzer.py`
- `src/core/propagation_verifier.py`

具体任务：

- 将当前 `IssuePlanner` 内部的传播扫描能力整理成可复用的影响面分析逻辑，形成统一 `ImpactAnalyzer` 输出。
- 在 `RepairPlan` 中补充以下字段：
  - `auto_upgraded_capabilities`
  - `propagation_budget`
  - `impact_summary`
  - `strategy_preferences`
  - `verification_targets`
- 设计“有限自动升级协议”，仅在以下条件满足时自动放开 `signature_change` / `multi_file_refactor`：
  - 传播目标已识别
  - 传播文件数/目标数在预算内
  - 没有触碰受保护路径
  - 改动仍可保持 patch-only
- 对预算外场景直接在 `plan_precheck` 阶段拒绝，不进入 edit。
- 在 prompt 中增加传播清单，要求模型一次性同步更新：
  - 定义点
  - 接口声明
  - override/virtual 链
  - 所有 callsite / `nameof`
- 在 verifier 中消费同一份 impact/propagation 数据，而不是重新发明一套检查模型。
- 在 boundary review 后、full build 前增加 `propagation_completeness_check`，至少检查：
  - 旧符号名是否仍残留在传播目标内
  - `proposed_method_name` 是否已在定义点和传播目标中一致出现
  - 预期接口声明/调用点是否已更新
- 检查失败时直接返回结构化 retry 结果，并把残留位置写入 `RetryContext.lessons`。
- 在验证链中加入快速编译层，顺序调整为：
  - `boundary -> propagation check -> quality gate -> rule validator -> fast compile -> full build`
- `fast compile` 优先采用成本更低的验证方式：
  - 被修改项目或最近项目的局部编译
  - 或 Roslyn 语法/语义快速解析
  - 目标是先拦住大部分 `CS0103`、`CS1061` 一类错误，再决定是否进入 full build

验收标准：

- `fd483c64-1218-485f-9343-30ef2a2b530f` 这类 bounded case 不再因 capability 缺失直接卡死。
- `0fb3e377-0955-41cb-8cae-729a28012d3c`、`c2c11128-8890-4742-9c7b-cf2ffb04071a` 这类问题在 full build 前就能给出更精确失败原因。
- `RepairPlan` 和 `EditContract` 中可以审计到“为什么自动升级、升级了什么、传播预算是多少，以及 verifier 实际检查了哪些目标”。
- 如果传播目标缺失或超预算，agent 在 edit 前就给出可解释的拒绝结果。
- planner 和 verifier 复用同一份 impact 数据结构，不再出现“前面分析一套、后面验证一套”的重复建模。
- fast compile 能在 full build 前拦住主要的符号残留/调用残留错误。

## 5.3 阶段 C：把质量门禁编译进 planner 和 prompt，并建立通用修复模板体系

优先级：`P0`

目标：

- 把当前 `quality_gate` 的事后拒绝，前移为 planner 阶段的显式修复约束。
- 建立“按修复形态复用”的通用模板体系，而不是给单条 Sonar 规则硬编码模板。
- 让 `S3776` 只是第一批验证样本，而不是模板系统本身。

建议改动文件：

- `src/core/issue_planner.py`
- `src/core/issue_prompt.py`
- `src/core/repair_plan.py`
- `src/agent/claude_agent.py`
- `tests/test_issue_planner.py`
- `tests/test_quality_gate_matrix.py`
- `tests/test_claude_agent.py`

必要时新增：

- `data/rule_templates/repair_archetypes/`
- `data/rule_templates/rule_family_map.yaml`

具体任务：

- 扩展当前 `expected_quality_gates` 和 `_format_quality_gate_hints()`，把“风险提示”升级成“生成约束”。
- 建立“修复形态模板”而不是“单规则模板”，至少先定义以下 archetype：
  - `method_decomposition`
    - 面向复杂度、深层嵌套、长方法、分支膨胀类问题
  - `signature_preserving_refactor`
    - 优先保持公开签名不变，通过私有 helper、局部变量、条件扁平化完成修复
  - `bounded_signature_propagation`
    - 在预算内允许有限签名变更和传播
  - `declaration_hygiene`
    - 约束 async 命名、XML docs、public surface、新增成员可见性
  - `expression_simplification`
    - 面向嵌套表达式、复杂条件、可提前返回的场景
- 在 archetype 定义中直接内置降级链，而不是把降级关系留到 retry 阶段临时拼装，例如：
  - `method_decomposition -> expression_simplification`
  - `bounded_signature_propagation -> signature_preserving_refactor`
  - `signature_preserving_refactor -> expression_simplification`
  - `expression_simplification -> null`
- 规则映射改成“rule -> archetype 集合”，而不是 `if rule == S3776`：
  - `S3776`、其他复杂度/长方法/深嵌套类规则优先映射到 `method_decomposition + signature_preserving_refactor`
  - async 命名和 public surface 风险映射到 `declaration_hygiene`
  - 需要改名且传播范围可控时再叠加 `bounded_signature_propagation`
- 第一批通用模板必须固化以下约束：
  - 优先提取 `private` 同步 helper
  - 只有 helper 内部确实包含 `await` 时才允许保留 `async`
  - 新增 async 方法必须带 `Async` 后缀
  - 禁止无必要引入新的 `public/protected` 成员
  - 默认优先保持公开方法签名不变
  - 优先使用 early return / guard clause / 条件扁平化，而不是先改签名
- 把 archetype 约束写入 `RepairPlan`，再由 `IssuePrompt` 注入 user prompt。
- 在 `RepairPlan` 中显式记录：
  - `selected_archetype`
  - `fallback_archetype`
  - `archetype_chain`
- 对于当前触发频率最高的质量门禁，至少前移以下规则：
  - `async_requires_await`
  - `async_signature`
  - `public_xml_docs`
  - `cognitive_complexity`

验收标准：

- 三个 `quality_gate` 失败样本可以在 planner/prompt 层看到对应的前置约束。
- 模板选择依据是“修复形态”和“风险组合”，不是仅靠某一个固定 rule id。
- `S3776` 之外的同类规则后续接入时，不需要再复制一份专属模板，只需要扩展映射关系。
- archetype 的降级链在 plan 工件中可见，retry 阶段直接消费，不需要重新发明 fallback 关系。
- 单元测试覆盖“提取 helper 但不应变成 async”“新 public 成员应被显式禁止”这类关键分支。
- 失败时的 retry message 不再只有 verifier 报错，而是能追溯到 plan 中的约束违反点。

## 5.4 阶段 D：稳定大方法和高复杂度重构场景的编辑落盘

优先级：`P1`

目标：

- 降低 `String to replace not found`、`no_change`、大块替换失配。
- 在 Edit 失败时给模型返回足够的实际上下文，而不是只返回一句匹配失败。

建议改动文件：

- `src/core/editor_policy.py`
- `src/core/issue_planner.py`
- `src/core/issue_contract.py`
- `src/agent/claude_agent.py`
- `src/core/agent_runtime.py`
- `src/core/claude_adapter.py`
- `tests/test_issue_planner.py`
- `tests/test_claude_agent.py`

具体任务：

- 为大方法引入专用编辑 profile，例如：
  - `chunked_edit`
  - `anchor_context_required`
  - `max_old_string_lines`
  - `prefer_small_edits_first`
- 触发条件建议使用“方法行数阈值 + 修复形态”，不要只绑定 `S3776`。
- 在 prompt 中明确要求：
  - 不要一次替换整个超长方法
  - 单次 Edit 的 `old_string` 不能过大
  - 必须包含稳定锚点上下文
- 当 Edit tool 返回 `String to replace not found` 或同类失败时，在 runtime/tool result handler 中回传：
  - 最接近的实际代码片段
  - 邻近上下文行
  - 可用锚点提示
  - 当前文件中的实际缩进/空白差异
- 最近匹配片段的生成优先采用轻量方式，例如：
  - 行级相似度匹配
  - `difflib.get_close_matches`
  - 失败片段附近窗口抽样
- 明确该机制是“增强 Edit 失败反馈”，不是重写整套 SDK tool 层。
- 对 `no_change` / `old_string` 失配的 retry，下一次自动切换到 chunked profile。
- 先不重写 runtime tool；如现有 prompt + profile 仍无法收敛，再评估是否做更重的 edit 包装层。

验收标准：

- `e536a516-dac6-4cc1-b3df-301e21acdc94` 这类场景能从“大块替换失败”切换为“多次小 edit”。
- Edit 失败时，模型能收到实际最接近片段，而不是只有空泛错误消息。
- 同类 `no_change` 失败率下降。
- planner/contract 工件里能看到本次 attempt 使用的编辑 profile。

## 5.5 阶段 E：把 retry lessons 变成真正的策略降级

优先级：`P1`

目标：

- 让 retry 不是“同样思路重试三次”，而是按失败类型切换修复策略。

建议改动文件：

- `src/core/retry_context.py`
- `src/core/issue_retry.py`
- `src/core/issue_planner.py`
- `src/core/repair_plan.py`
- `tests/test_issue_retry.py`
- `tests/test_issue_planner.py`

具体任务：

- retry 优先消费 archetype 自带的 fallback 链，而不是在 retry 层重新发明降级关系。
- 为以下 failure kind 增加显式 lesson -> strategy 映射：
  - `quality_gate`
    - 若命中 `async_requires_await`，下一次禁止创建 async helper
    - 若命中 `public_xml_docs`，下一次禁止新增 public/protected 成员
  - `build`
    - 若属于签名传播残留，下一次必须附带完整 propagation checklist
    - 若超预算，降级为“不改签名”策略
  - `no_change`
    - 下一次切换为 chunked edit profile
  - `plan_conflict`
    - 若在自动升级预算内，下一次直接放权
    - 若超预算，立即终止并给出可解释原因，不浪费剩余重试
- 明确“连续同类失败提前终止”的判定标准：
  - `failure_kind` 相同
  - `failure_detail_key` 相同
  - `strategy_fingerprint` 相同
  - 新旧 diff 指纹没有产生有效变化
- `failure_detail_key` 至少按以下维度归一化：
  - quality gate：具体 rule id
  - build / fast compile：编译错误码或 propagation residual 指纹
  - no_change：edit miss 类型
  - plan_conflict：precheck code
- `strategy_fingerprint` 至少包含：
  - `selected_archetype`
  - `allowed_capabilities`
  - `edit_profile`
  - 是否启用 propagation / fast compile / constraint injection
- 当 `failure_kind + failure_detail_key + strategy_fingerprint` 连续重复且 diff 无有效变化时，立即终止，不消耗剩余重试。

验收标准：

- 同一个 issue 的 attempt 之间能看到策略发生明确变化。
- 提前终止的判定标准是结构化且可测试的，不依赖模糊字符串比较。
- retry 次数下降，但每次 retry 的信息密度和有效性上升。

## 5.6 阶段 F：回归测试、回放样本与 rollout

优先级：`P0-P1` 贯穿执行

目标：

- 所有关键改造都必须有稳定回归测试，不依赖真人盯日志。

建议改动文件：

- `tests/test_issue_planner.py`
- `tests/test_fix_verifier.py`
- `tests/test_quality_gate_matrix.py`
- `tests/test_boundary_runtime.py`
- `tests/test_run_logging.py`
- `tests/test_issue_retry.py`
- 视情况新增：
  - `tests/test_repair_archetype_planner_regressions.py`
  - `tests/test_propagation_verifier.py`

具体任务：

- 以 `20260410013022` 这轮的 7 个失败点构建回归 fixture。
- 至少补以下测试矩阵：
  - bounded `signature_change` 自动升级
  - 传播目标缺失时的 precheck 拒绝
  - impact 数据在 planner 和 verifier 之间复用
  - 通用重构模板优先私有同步 helper
  - archetype fallback 链选择
  - `async_requires_await` 前移约束
  - build 前传播残留检测
  - fast compile 短路 full build
  - 大方法 chunked edit profile 选择
  - Edit 失败时的最近匹配片段回传
  - 连续同类失败提前终止
  - early abort 事件写入
- 首轮 rollout 建议挂在 feature flag 或 rollout flag 下，逐步启用：
  - `planner.capability_auto_upgrade`
  - `planner.repair_archetypes.constraint_injection`
  - `planner.repair_archetypes.strategy_selection`
  - `verifier.propagation_lifecycle`
  - `verifier.fast_compile`
  - `planner.chunked_edit_profile`
  - `runtime.edit_failure_context_feedback`

验收标准：

- 本地测试可稳定覆盖新增分支。
- rollout 可以按功能逐步开关，而不是一次性把所有新行为推上主链路。

## 6. 实施顺序

建议按以下顺序推进：

1. 阶段 A：先统一 boundary/scope 语义，并补 early abort 事件。
2. 阶段 B：一次做完 capability 协商、impact analysis、propagation verify 和 fast compile，先把 `plan_conflict` 与签名残留链路闭环。
3. 阶段 C：把质量门禁约束前移，并建立 archetype + fallback 链。
4. 阶段 D：稳定大方法编辑，并补 Edit 失败的上下文回传。
5. 阶段 E：让 retry 真正消费 archetype fallback 和 failure fingerprints。
6. 阶段 F：回归测试和分阶段 rollout 贯穿全程，但每个阶段完成后都要落测试。

这个顺序的原因很简单：

- 不先统一语义，后续改出来的东西会继续建立在错误门禁认知上。
- 不先把 B 阶段闭环做完，planner 和 verifier 仍会各自维护一套传播逻辑。
- 不先把质量门禁前移并定义 fallback，retry 仍然会在错误 archetype 上机械重复。
- 不先稳定大方法编辑和失败反馈，就算 planner 更聪明，也会死在落盘阶段。

## 7. 不建议首轮做的事情

以下项先列为第二阶段增强，不建议首轮就做：

- 直接重写 Claude SDK 的工具层和自定义完整 `VerifiedEditTool`
- 无上限放开 `multi_file_refactor`
- 为所有 Sonar 规则铺开 rule-specific validator

原因不是这些方向不对，而是首轮目标是先打掉当前真实失败点，不能把项目拖进“大重构 + 大不确定性”。

补充约束：

- line-level `scope` 硬门禁不只是“不建议首轮做”，而是当前项目方向上明确不做。
- 如果后续修复率下降，需要优先调 planner、prompt、quality gate 前移和传播预检，不允许把问题简单归因到“scope 还不够严”。

## 8. 完成定义

当满足以下条件时，可认为本轮工程修复基本完成：

- `plan_conflict` 仅在超预算或高风险场景出现，不再拦住本该可修的 bounded case。
- 当前两类 build 失败被前移为传播不完整的结构化失败，或直接修复成功。
- 当前三类 `quality_gate` 失败在 planner/prompt 阶段已有明确约束，失败率明显下降。
- 大方法和高复杂度重构场景默认采用安全拆分策略，`no_change` 显著下降。
- `batch_20260410104638` 这类早退出场景能够通过工件直接识别原因。
- 新增行为有测试覆盖，并能通过 rollout flag 渐进启用。

## 9. 交付物清单

按本计划推进后，第一批应交付的不是“某几个零散 patch”，而是以下成套能力：

- 对齐后的 boundary/scope 语义与事件模型
- 一套明确替代 strict scope hard gate 的软门禁审计策略
- 带自动升级审计的 `RepairPlan` / `EditContract`
- 可前移质量约束的 planner/prompt 链路
- impact analysis 到 propagation verify 的完整闭环
- fast compile 短路层
- 大方法 chunked edit profile
- Edit 失败时的最近匹配上下文回传
- failure lessons 驱动的 retry 策略降级
- 对应测试与回归样本

这套东西补齐以后，当前 agent 才算真正从“能修一部分简单问题”进入“复杂修复有一致性门禁和失败闭环”的状态。
