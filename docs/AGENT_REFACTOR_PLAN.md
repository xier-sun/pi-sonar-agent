# Agent 工程重构与优化升级状态

这份文档不再是“准备从零开工的未来计划”，而是当前仓库已经完成的重构收口和最近几轮优化升级的状态快照。

如果你想看当前代码结构，请先读 [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)。
如果你想直接运行或排障，请看 [RUNBOOK.md](RUNBOOK.md)。

## 1. 当前结论

本项目已经完成“在当前仓库内重构、共享运行骨架替换旧实现”的主目标，不建议再按“另起一套 V2 新仓库”去理解它。

当前更准确的表述是：

- 基础重构阶段已经完成
- 运行时、状态模型、issue 治理、包结构都已收口
- 后续工作重点转为“真实批次驱动的优化升级”和“特定规则/特定 provider 的可靠性增强”

## 2. 当前状态总览（2026-04-20）

### 已完成的基础重构

- 单目标入口与批量入口已经统一到共享 `RunCoordinator`
- `base_branch`、Git 认证、preflight、超时与取消链路已经收口
- `ModelGateway + ClaudeAdapter + AgentRuntime + ToolPolicy + ResourceLoader` 已成为正式运行时分层
- `IssuePlanner + EditContract + EditorPolicy + DiffReviewer + FixVerifier` 已成为单 issue 约束主链
- `RunState / TargetState / IssueState / AttemptState`、artifact 和 `events.jsonl` 已稳定落盘
- `pi_sonar_agent.*` 已成为标准导入路径，legacy bridge 主要承担兼容职责

### 已完成的优化升级

- 当前 issue 执行模式已统一收敛为 `simple_loop`
- 默认 issue turn floor 已提升到 `16`
- runtime 工具面已稳定为 `Read/Grep/Glob/Edit/MultiEdit/Write/Bash`
- patch 验证已分层为 deterministic verifier + quality gate + review gate + post-check
- `S107` 已补齐专项提示、workspace 内指南同步和本地参数计数 post-check
- recipient 解析已统一为 `targets.json` 优先、MySQL fallback、缺省 `unresolved`
- DB 状态同步仍保持可选，不再作为主流程单点

## 3. 已完成阶段摘要

## Phase 0：运行稳定性兜底

状态：已完成

交付结果：

- 统一入口和共享 `RunCoordinator`
- `base_branch` 真正控制初始 clone
- `GitRepositoryGateway` 统一 clone / branch / push
- SDK 初始化、首响应、后续响应、issue 硬超时与取消清理链路
- 统一 preflight

## Phase 1：状态模型与执行工件

状态：已完成

交付结果：

- `state.py`
- `events.py`
- `artifact_writer.py`
- `retry_context.py`
- `state_store.py`
- run / target / issue / attempt 结构化工件

## Phase 2：运行时分层与能力边界

状态：已完成

交付结果：

- `model_gateway.py`
- `claude_adapter.py`
- `agent_runtime.py`
- `hooks.py`
- `registry.py`
- `policy.py`
- `resource_loader.py`

## Phase 3：单 issue 治理升级

状态：已完成

交付结果：

- `issue_planner.py`
- `issue_contract.py`
- `editor_policy.py`
- `diff_reviewer.py`
- `follow_up_store.py`
- `boundary_runtime.py`
- `scope_guard.py`
- `fix_verifier.py`

## Phase 4：包结构与质量门槛收口

状态：已完成

交付结果：

- 标准 `pi_sonar_agent.*` 包结构
- `run.py` 入口清理
- `ruff` / `pytest` / CI 收口
- 兼容 facade 只保留桥接职责，不再参与主链路设计

## 4. 最近几轮优化升级的重点

### 4.1 性能与执行策略

当前默认启用的方向包括：

- short-form prompt
- fast path
- complex-rule plan-first
- layered verification
- review gate
- fast compile
- patch salvage
- continuation retry

这些能力由 [src/core/perf_flags.py](../src/core/perf_flags.py) 统一管理，文档和日志中应优先使用 flag 名称和结构化 summary 说话，而不是只描述“好像变快了”。

### 4.2 Review Gate 成为正式 verifier 组成部分

当前 review gate 已不再是附属实验能力，而是 verifier 正式组成部分：

- 默认开启
- 支持独立 provider / key / model / timeout
- deterministic hard blocker 存在时可自动降级为 `not_applicable`
- 结果会稳定落入 `review_gate_result`

### 4.3 `S107` 的专项增强

最近围绕 `S107` 已补齐以下能力：

- prompt 硬约束：只有最终参数数 `<=7` 才算成功
- workspace 内指南同步：`.pi-sonar-agent-runtime/s107-fix-guide.md`
- simple-loop prompt guard
- verifier post-check，防止 `8/9` 参数半成品被误记为 fixed
- turn floor 提升与 rule profile 协同

注意：

- `S107` 当前 rule profile 仍以 Roslyn 为主修复引擎
- agent 侧护栏更多是为了 fallback、运行时审计和结果判定

### 4.4 工具面与提示词对齐

当前默认 fix tool surface 已重新锁定为：

- `Read`
- `Grep`
- `Glob`
- `Edit`
- `MultiEdit`
- `Write`
- `Bash`

当前约定是：

- 提示词、allowlist、request snapshot、visible toolset 必须围绕同一套工具面生成
- 如果第三方 provider 的 SDK init trace 只返回部分工具，这属于 provider / CLI 兼容问题，不应先去弱化 prompt 的工具提醒

### 4.5 Recipient 解析收口

当前 `reviewer_email` / `dingtalk_userid` 解析已经统一为：

1. `targets.json` 显式值优先
2. reviewer 缺省回退到 `author`
3. DingTalk userId 缺省走 MySQL `author` 反查
4. 未配置 `DB_*` 时直接跳过 DB fallback，最终返回 `unresolved`

这条逻辑已经在真实 batch 中多次触发，相关文档必须同步说明“未配置 DB 不等于数据库报错”。

## 5. 当前仍需持续优化的事项

这些事项不意味着主链路未完成，而是当前最值得继续打磨的地方。

### 5.1 第三方模型 provider 的认证兼容

现状：

- 非官方 `ANTHROPIC_BASE_URL` 会进入 `bare`
- 当前兼容逻辑可能把 `ANTHROPIC_AUTH_TOKEN` 桥接成 `ANTHROPIC_API_KEY`
- 本地交互式 Claude Code 和自动化 bare 链路不一定等价

后续方向：

- 更清晰地区分 `AUTH_TOKEN` 与 `API_KEY` 的 provider 语义
- 在日志里更直接暴露“当前实际采用了哪条认证路径”

### 5.2 真实 provider 返回的工具子集

现状：

- request 侧会声明完整工具面
- 但第三方 provider 的 SDK init trace 可能只回部分工具

后续方向：

- 增强日志可读性，明确区分“声明工具面”和“provider 实际回传工具面”
- 减少 operator 把 provider 兼容问题误判成 prompt / allowlist 配置问题

### 5.3 Recipient / DB fallback 的可观测性

现状：

- 未配置 `DB_*` 时当前只会最终显示 `unresolved`
- 对操作者而言，容易误解成“数据库查询失败”

后续方向：

- 增加更直白的运行日志，例如“未配置 DB_*，跳过 DingTalk userId 数据库查询”

### 5.4 复杂规则成功率继续提升

现状：

- 复杂规则已经有 plan-first、review gate、lessons memory、boundary runtime
- 但 `S107`、复杂 `S3776`、多文件传播类规则仍最容易暴露 provider、turn、prompt 和 planner 的真实瓶颈

后续方向：

- 继续用真实 batch 日志反推策略
- 优先做“不降低其他规则修复率”的专项增强

## 6. 现在不要再做的事

当前明确不推荐：

- 再按“另起新仓库重写 V2”组织后续工作
- 重新引入第二套单目标/批量编排逻辑
- 在文档里继续描述历史 execution mode 或历史 tool surface
- 把 review gate、artifact、retry context 当成可有可无的附属能力
- 让 DB、DingTalk 或 provider 兼容问题继续停留在“只有看源码才知道”的状态

## 7. 当前验收标准

文档、实现和排障应共同满足这些标准：

- 单目标入口和批量入口使用同一条共享运行骨架
- issue 生命周期、artifact 和事件可以稳定回答“这次到底发生了什么”
- 复杂规则失败时能区分：
  - provider 认证问题
  - turn 耗尽
  - patch drift
  - review gate 拒绝
  - deterministic build / rule / quality gate blocker
- 运行语义一旦收口，`README / PROJECT_GUIDE / RUNBOOK / ENGINEERING_MEMORY` 必须同步更新

## 8. 后续维护建议

每次做真实运行链路优化时，建议按这个顺序收尾：

1. 先确认代码路径和真实日志一致
2. 再补针对性回归测试
3. 再更新本文件的“当前状态”和“持续优化事项”
4. 最后同步 `README`、`RUNBOOK`、`ENGINEERING_MEMORY`

这份文档的目标不是保留所有历史细节，而是让维护者随时知道：

- 哪些大阶段已经完成
- 当前系统的真实形态是什么
- 下一步最该继续磨哪几处
