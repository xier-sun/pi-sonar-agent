# Context Memory And Compaction Implementation Plan

日期: 2026-04-17
适用范围: `pi-sonar-agent` headless `simple_loop` 主链
相关文档:
- [headless-simple-loop-implementation-plan-20260416.md](/d:/MyProjects/pi-sonar-agent/docs/headless-simple-loop-implementation-plan-20260416.md)
- [sonar-fix-playbook.md](/d:/MyProjects/pi-sonar-agent/docs/sonar-fix-playbook.md)
- [sonar-agent-implementation-checklist-20260415.md](/d:/MyProjects/pi-sonar-agent/docs/sonar-agent-implementation-checklist-20260415.md)
- Claude Code 参考实现: [compact.ts](/d:/MyProjects/claude-code-main/src/commands/compact/compact.ts)

## 1. 背景

当前项目已经具备以下能力:
- 单 issue 重试链路
- `RetryContext` 失败记忆
- prompt 截断与外置参考文档
- `LessonsStore` 长期 lessons
- `simple_loop` 的 `build -> post_check` 主链

但现状仍有一个核心缺陷:

系统更擅长压缩失败日志, 不擅长维护当前 issue 的权威工作状态。

这会带来 4 类直接问题:
- 旧的 build 错误会在工作区已经恢复后继续误导模型
- 历史失败信息会压过当前真正该做的下一步
- prompt 的“截断”只能减字数, 不能保证连续修复能力
- 长期 lessons、当前 issue 状态、短期失败记忆之间边界不清

本方案的目标不是“再多加一点 retry 文本”, 而是建立一套真正可压缩、可恢复、可验证、不会误导模型的上下文记忆架构。

## 2. 总目标

本方案只服务一个目标:

让模型在每一轮 attempt 中, 优先看到“当前最真实、最权威、最可执行”的 issue 状态, 而不是被过时错误、冗长历史或无关 lessons 带偏。

最终理想闭环:

`issue -> 当前权威工作记忆 -> Claude 修复 -> build/post_check -> 更新工作记忆 -> 必要时压缩 -> 下一轮继续`

## 3. 非目标

以下事项不在本方案主线内:
- 重写 Sonar 闭环或 PR 后再分析链路
- 重新设计所有 rule-specific validator
- 引入新的模型或 provider
- 用长期 memory 取代当前 issue 的状态管理

## 4. 设计原则

### 4.1 信息分层必须明确

系统中的信息必须严格分成三层:

1. `Issue Working Memory`
- 当前 issue 的权威状态
- 下一轮 prompt 的第一信息源

2. `Retry Context`
- 最近几轮失败和修复轨迹
- 属于短期近场记忆

3. `Durable Memory`
- 跨 run 的 lessons、playbook、项目级长期约束
- 只提供策略补充, 不能替代当前状态

### 4.2 当前事实优先于历史事实

只要工作区发生以下变化, 旧证据就不能再当作当前事实:
- 恢复 baseline
- 恢复 best patch
- 文件已被后续 patch 重写
- 编译错误对应的代码形态已经变化

### 4.3 压缩不是截断

压缩的目标是保住连续性, 不是仅仅减少字符数。

因此:
- 旧细节要被新的结构化摘要替代
- compact 后必须留下清晰的 boundary
- 模型要知道“哪些是当前状态, 哪些只是历史参考”

同时必须注意:
- 字符数只能作为近似指标, 不能作为最终预算判断标准
- 最终是否 compact, 必须以与目标模型匹配的 token 预算为准

### 4.4 长期记忆不能抢主位

`LessonsStore`、`playbook`、项目规则都只能作为次级参考。

prompt 中的信息优先级必须固定:
1. 当前工作记忆
2. 最近有效修复摘要
3. 最近失败信息
4. 更早历史压缩摘要
5. lessons / playbook

## 5. 当前架构的主要缺口

### 5.1 缺少单一真相源

当前一个 issue 的状态散落在:
- `RetryContext`
- `prompt_context.json`
- `attempt_summary.json`
- `build_result.json`
- `LessonsStore`

没有一个对象负责表达“当前这个 issue 到底处于什么状态”。

### 5.2 失败摘要被当作工作记忆

`RetryContext` 目前已经支持:
- `strategy_summary`
- `patch_summary`
- `edited_symbols`
- `workspace_state_note`

但它的中心仍然是 `failure_kind / compiler_errors / fingerprints`。

这使它天然更像“失败报告”, 而不是“当前工作面”。

### 5.3 缺少 stale evidence 失效机制

系统现在会继续携带旧编译错误和旧失败信息, 即便工作区已经恢复。

这会让模型拿着旧错误去看新代码, 进入错误循环。

### 5.4 缺少真正的 compact boundary

目前有:
- prompt 截断
- retry history 压缩
- 外置 reference doc

但还没有类似 Claude Code 的:
- session / issue compact boundary
- post-compact cleanup
- compact 后的新权威摘要

### 5.5 长期 lessons 和当前状态的边界不够硬

`LessonsStore` 是对的, 但没有清楚限制它在 prompt 中的地位。

如果 lessons 注入过重, 会把模型从“当前这轮怎么修”拉回“以往通常怎么修”。

### 5.6 运行时工件缺少强结构校验

后续方案会新增:
- `working-memory.json`
- `compact-summary.md`
- `evidence-index.json`

如果这些工件没有严格 schema 校验, 上游字段微调就可能导致:
- prompt 渲染读取失败
- working memory 部分字段丢失
- compact 边界失真
- stale evidence 判断失效

## 6. 目标架构

新增一层权威对象:

`IssueWorkingMemory`

它是单 issue 的唯一权威状态, 生命周期覆盖整个 issue 的所有 attempts。

### 6.1 信息流

1. issue 开始时创建 `IssueWorkingMemory`
2. 每轮 attempt 完成后更新它
3. 如果上下文过长, 基于它生成 compact summary
4. 下一轮 prompt 优先读取它
5. `RetryContext` 只提供最近失败轨迹
6. `LessonsStore` 只提供少量长期策略补充

### 6.2 建议目录

新增:
- `src/core/memory/issue_working_memory.py`
- `src/core/memory/issue_compaction.py`
- `src/core/memory/evidence_state.py`
- `src/core/memory/working_memory_store.py`
- `src/core/memory/memory_schema.py`

运行时工件新增:
- `.git/pi-sonar-agent-runtime/issues/<issue>/working-memory.json`（git 工作区优先）
- `.git/pi-sonar-agent-runtime/issues/<issue>/compact-summary.md`（git 工作区优先）
- `.git/pi-sonar-agent-runtime/issues/<issue>/evidence-index.json`（git 工作区优先）
- 无 `.git` 时回退到 `.pi-sonar-agent-runtime/issues/<issue>/...`

### 6.3 Schema 与存储契约

所有 memory 工件必须先经过代码级 schema 校验, 再允许写入或读取。

推荐做法:
- 第一阶段使用 `dataclass + 显式验证函数` 或 `Pydantic`
- 中期补充 JSON Schema 导出, 便于工件审计与离线校验

最少要保证:
- 写入前校验
- 读取后校验
- 版本字段存在
- 缺字段时 fail fast, 不允许静默回退成空状态

## 7. 核心数据结构

### 7.1 IssueWorkingMemory

建议字段:
- `issue_key`
- `rule_id`
- `current_goal`
- `authoritative_workspace_state`
- `best_known_patch_state`
- `accepted_constraints`
- `rejected_strategies`
- `stale_evidence`
- `files_inspected`
- `symbols_touched`
- `latest_verification`
- `latest_retryable_failure`
- `next_action`
- `compaction_generation`
- `last_updated_at`

### 7.2 EvidenceState

建议字段:
- `evidence_id`
- `source_type`
- `summary`
- `related_files`
- `related_symbols`
- `status`
- `content_fingerprint`
- `superseded_by`

`status` 只允许:
- `current`
- `historical`
- `stale`
- `superseded`

### 7.3 CompactSummary

建议字段:
- `issue_key`
- `generation`
- `current_goal`
- `authoritative_state_summary`
- `accepted_constraints`
- `rejected_strategies`
- `current_best_patch`
- `recent_failures_summary`
- `next_action`

### 7.4 DurableMemorySelection

为了防止 lessons 注入过量, 需要一个明确的筛选结构。

建议字段:
- `rule_id`
- `failure_fingerprint`
- `selected_lessons`
- `selection_reason`
- `selection_mode`

`selection_mode` 只允许:
- `rule_exact`
- `fingerprint_exact`
- `rule_plus_fingerprint`
- `manual_override`

## 8. Prompt 侧的硬规则

### 8.1 Prompt 信息顺序必须固定

`simple_loop` 主 prompt 必须按如下顺序构建:

1. `【当前工作记忆】`
- 当前目标
- 当前工作区权威状态
- 当前最优 patch 状态
- 当前下一步

2. `【最近有效修复摘要】`
- 上一轮真正做了什么
- 修改了哪些符号
- 当前 patch 是否已恢复到 baseline / best patch

3. `【最近失败信息】`
- 只保留仍然与当前工作区一致的失败

4. `【历史压缩摘要】`
- 更早失败和修法的压缩版

5. `【长期参考】`
- lessons
- playbook

### 8.2 不得把旧证据放进主失败信息

以下情况必须从主 prompt 中移除旧证据:
- 文件内容已变
- workspace 已恢复
- best patch 已恢复
- 当前 diff 与旧编译错误不再对应

### 8.3 compact 后要显式告诉模型

一旦发生 compact, prompt 里必须加入:

`更早细节已压缩, 以下摘要为当前权威状态。请按当前工作记忆继续, 不要重复追旧错误。`

### 8.4 回滚后要显式告诉模型

当工作区发生以下回滚时, 下一轮 prompt 必须加入强提示:
- restore issue baseline
- restore best known patch
- reviewer reject 后回滚

提示必须明确包含:
- 发生了回滚
- 当前工作区回到了什么状态
- 上一轮哪类策略被撤销
- 本轮必须尝试不同策略, 不能机械重复

建议模板:

`注意: 由于上一轮策略失败, 代码已回滚至 {workspace_state}。上轮对 {symbol_or_region} 的修改已撤销。本轮必须更换修复策略, 不要重复上一轮的 helper 提取 / dynamic 兜底 / 签名扩散。`

## 9. Compact 触发策略

### 9.1 触发条件

满足任一条件即触发 issue-level compact:
- retry 次数达到阈值
- prompt budget 接近上限
- retry history 超过字符预算
- 估算 token 数接近模型上下文阈值
- 读取文件数超过阈值
- build/output 体积过大
- 工件引用过多

其中:
- 字符预算只作为 cheap precheck
- token 预算才是 hard gate

实现建议:
1. 先用字符预算做快速筛选
2. 命中后再用与当前模型匹配的 token estimator 做精算
3. 只有 token 预算接近阈值时才触发真正 compact

注意:
- 包含代码块、diff、非英文字符时, 字符数和 token 数偏差会非常大
- 不能只依赖 `len(text)`

### 9.2 compact 的输出

compact 不直接删除历史, 而是:
- 基于 `IssueWorkingMemory` 生成新的 compact summary
- 将更早历史标记为“已被摘要替代”
- 保留最近 1 到 2 轮的详细上下文
- 其余历史降为 compacted history

### 9.3 post-compact cleanup

compact 完成后必须做:
- 重建当前权威工作记忆
- 清理已过期的 failure detail
- 重置 prompt 中的主要失败来源
- 更新工件引用

## 10. Stale Evidence 失效机制

### 10.1 必须触发失效的场景

以下操作后必须重新评估证据状态:
- restore issue baseline
- restore best patch
- patch salvage
- reviewer reject 后回滚 patch
- 新 patch 明显覆盖旧变更区域

### 10.2 编译错误要绑定文件状态

编译错误不能只存文本, 还要记录:
- 文件路径
- 相关行附近摘要
- 当前文件内容指纹
- 对应 diff 指纹

若当前文件指纹已变化, 该错误自动降级为:
- `historical` 或 `stale`

### 10.3 Prompt 中的表现

当前仍有效的错误:
- 可以进入 `【最近失败信息】`

已失效错误:
- 只能进入 `【历史压缩摘要】`
- 或只存工件, 不再直接进 prompt

### 10.4 回滚后的状态提示

一旦触发回滚, `IssueWorkingMemory` 中必须写入:
- `authoritative_workspace_state`
- `rollback_reason`
- `rejected_strategies`

并在下一轮 prompt 中显式渲染:
- 当前已回滚到何种状态
- 上轮哪种策略已被否定
- 本轮不得重复哪些操作

## 11. 与现有模块的职责关系

### 11.1 retry_context.py

保留, 但职责调整为:
- 记录最近失败
- 记录最近修法摘要
- 记录少量近场历史

不再承担:
- 当前 issue 的权威状态总汇

### 11.2 issue_retry.py

新增职责:
- 更新 `IssueWorkingMemory`
- 判断是否触发 compact
- 触发 stale evidence invalidation
- 将最优 patch 状态同步回 working memory

### 11.3 issue_prompt.py

新增职责:
- 按固定优先级渲染 working memory
- 区分 current vs stale evidence
- 在 compact 后输出明确边界说明

减少职责:
- 不再隐式承担工作状态拼装

### 11.4 lessons_store.py

继续保留为 durable memory, 但必须限制注入策略:
- 只注入和当前 rule / failure fingerprint 强相关的少量 lessons
- 不得整包进入主 prompt

具体要求:
- 第一阶段优先采用严格映射:
  - `rule_id -> lessons`
  - `failure_fingerprint -> lessons`
  - `rule_id + failure_fingerprint -> highest priority lessons`
- 默认每轮最多注入 1 到 2 条 lesson
- 禁止注入泛化经验, 如“如何写好 C# 代码”之类的宽泛提示
- 向量检索或 RAG 化可以作为后续增强, 但不能替代严格过滤

## 12. 实施步骤

### 阶段 1: 建权威状态层
状态: `completed`

任务:
1. 新增 `IssueWorkingMemory`
2. 新增 `working_memory_store`
3. 新增 memory schema 校验
4. 每轮 attempt 后更新 working memory
5. 将当前 prompt 改为优先读取 working memory

完成定义:
- 每个 issue 都有 `working-memory.json`
- prompt 中出现 `【当前工作记忆】`
- 当前目标、当前状态、下一步来自同一个对象
- 工件写入和读入都经过 schema 校验

实施结果:
- 已新增 `src/core/memory/issue_working_memory.py`
- 已新增 `src/core/memory/working_memory_store.py`
- 已新增 `src/core/memory/memory_schema.py`
- `simple_loop` prompt 已接入 `【当前工作记忆】`
- `process_issue_with_retries(...)` 已在每轮 attempt 前后维护 `working-memory.json`

### 阶段 2: 引入 stale evidence
状态: `completed`

任务:
1. 新增 `EvidenceState`
2. 编译错误绑定文件/内容指纹
3. workspace restore / best patch restore 时标记旧证据失效
4. prompt 中剔除 stale evidence
5. 回滚后输出显式状态提示

完成定义:
- 工作区恢复后, 旧 build 错误不再进入主失败信息
- 工件中能看到 evidence 状态变化
- 回滚后的 prompt 能明确告诉模型“已回滚, 请更换策略”

实施结果:
- 已新增 `src/core/memory/evidence_state.py`
- 编译错误已绑定到 `EvidenceState`，并写入 `evidence-index.json`
- 回滚到 baseline / best patch 时，当前 compiler-error evidence 会标记为 `stale`
- `simple_loop` prompt 在回滚后会将旧失败降级为 `【历史失败线索】`
- 运行时 memory 工件已迁移到 `.git/pi-sonar-agent-runtime/issues/...`，避免被 `git clean -fd` 清掉

### 阶段 3: 实现 issue-level compact
状态: `completed`

任务:
1. 新增字符预算预筛
2. 新增 token 预算硬判断
3. 新增 compact trigger
4. 新增 compact summary 生成器
5. 新增 compact boundary 标记
6. compact 后做 post-compact cleanup

完成定义:
- 长 issue 重试时, prompt 不再无限增长
- compact 后模型仍能正确延续上次工作
- compact 决策不再只依赖字符数

实施结果:
- 已新增 `src/core/memory/issue_compaction.py`
- 已在 `IssuePromptBuilder` 中接入字符预筛 + token 预算硬判断，触发后会生成 `compact-summary.md`
- `IssueWorkingMemory` 已新增 `compacted_history_summary / compact_boundary_note / compact_summary_path`
- prompt 现在会显式输出 `【上下文压缩边界】`，并把更早历史压成 working memory 中的权威摘要
- `PromptBudgetReport` 已补充 `estimated_tokens / token_budget / compaction_applied / compaction_reason`
- compaction 工件已落到 `.git/pi-sonar-agent-runtime/issues/<issue>/compact-summary.md`

### 阶段 4: 收紧长期记忆注入
状态: `completed`

任务:
1. 限制 lessons 注入数量
2. 建立 `rule_id / failure_fingerprint` 精确映射
3. 只保留与当前 issue 直接相关的 durable memory
4. playbook 与当前状态彻底分层

完成定义:
- lessons 不再压过当前 issue 状态
- prompt 中 durable memory 始终处于末位
- 每轮 durable memory 注入可解释、可审计、可控量

实施结果:
- `LessonsStore` 已新增 `primary_failure_fingerprint / failure_fingerprints / selection_mode / selection_reason`
- `load_planner_lessons(...)` 现在优先按 `rule_id + failure_fingerprint` 精确匹配，再回退到 `rule_exact`
- lessons 默认每轮最多注入 `1-2` 条，避免长期经验压过当前状态
- `IssuePlanner` 已在加载 planner lessons 时显式传入 `failure_fingerprints`
- `simple_loop` prompt 已新增 `【长期参考】` 末位 section，只注入当前 issue 强相关 lessons

## 12.1 当前完成状态

- 阶段 1: `completed`
- 阶段 2: `completed`
- 阶段 3: `completed`
- 阶段 4: `completed`

## 12.2 本轮验证

- `tests/test_issue_compaction.py tests/test_lessons_store.py tests/test_simple_loop_prompt.py tests/test_issue_working_memory.py tests/test_issue_working_memory_retry.py`: `16 passed`
- `tests/test_issue_planner.py tests/test_artifact_writer.py tests/test_claude_agent.py -k "planner_lessons or prompt_budget or structured_retry_context or includes_repair_summary_from_retry_context or workspace_retry_references"`: `3 passed, 89 deselected`
- `py_compile`: 通过

## 13. 验收标准

### 13.1 连续性
- 第 3 轮及以后, 模型仍能清楚知道:
  - 当前最优 patch 是什么
  - 上次具体改了什么
  - 哪些旧错误已经失效
  - 本轮应继续做什么

### 13.2 正确性
- workspace 恢复后, 旧错误不会继续主导 prompt
- compact 后不会丢失当前目标和下一步

### 13.3 轻量化
- prompt 体积随着 issue 重试增长保持可控
- 重试历史只保留少量高价值信息
- compact 触发依据 token 预算, 而不是只看字符数

### 13.4 不误导模型
- 当前 prompt 中不存在与当前工作区明显不一致的主失败信息
- lessons / playbook 只做补充, 不抢占主语境
- 回滚后 prompt 明确提醒模型策略已撤销, 不会机械重复上一轮
- memory 工件结构一致, 不会因字段漂移造成状态读取失真

## 14. 红线

以下做法禁止继续扩张:
- 继续往 `RetryContext` 塞越来越多字段, 试图把它变成万能状态对象
- 只靠字符串截断解决上下文膨胀
- 不区分 current 与 stale evidence
- 把 lessons 当成当前状态
- compact 后不显式告诉模型边界变化

## 15. 总结

本项目下一阶段最重要的不是“再多做一点 retry 历史摘要”, 而是:

1. 为单 issue 建立权威工作记忆
2. 让旧证据在状态变化后自动失效
3. 把真正的 compact boundary 做出来
4. 让 prompt 永远优先呈现当前真实状态

只有这样, 上下文压缩才会真正帮助模型持续修复, 而不是把更多历史失败压成更短的误导信息。
