# pi-sonar-agent 项目结构与执行指南

这份文档面向维护者，回答三件事：

1. 当前仓库真实结构是什么
2. 单目标和批量运行到底经过哪些阶段
3. 核心模块各自负责什么，不该负责什么

如果你只是想把环境配好并跑起来，先看 [docs/RUNBOOK.md](docs/RUNBOOK.md)。
如果你想了解这轮重构为什么这样收口，再看 [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md)。

## 1. 目录结构

### 1.1 顶层目录

- [run.py](run.py): 本地一键入口，负责把 `src/` 加入 `sys.path` 后调用 `pi_sonar_agent.main`
- [README.md](README.md): 项目总览
- [PROJECT_GUIDE.md](PROJECT_GUIDE.md): 当前文档
- [docs/RUNBOOK.md](docs/RUNBOOK.md): 运行与配置手册
- [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md): 工程问题记忆
- [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md): 重构实施记录
- [data/csharp-quality-gate.md](data/csharp-quality-gate.md): C# 质量门禁补充说明
- [.github/workflows/ci.yml](.github/workflows/ci.yml): CI 质量门禁

### 1.2 源码目录

- [src/main.py](src/main.py): 单目标正式入口
- [src/batch_runner.py](src/batch_runner.py): 批量入口
- [src/core](src/core): 当前主要实现层，编排、运行时、工件、状态、Git、prompt、reviewer 都在这里
- [src/agent](src/agent): ClaudeFixAgent 与规则策略
- [src/fixers](src/fixers): 构建、规则 profile、Roslyn 相关能力
- [src/integrations](src/integrations): SonarQube / Azure DevOps 集成
- [src/sonar_mcp](src/sonar_mcp): MCP 工具
- [src/reporting](src/reporting): 报告输出
- [src/pi_sonar_agent](src/pi_sonar_agent): 标准包入口和桥接模块

### 1.3 关于 `src/pi_sonar_agent`

当前仓库的“对外标准导入路径”已经统一为 `pi_sonar_agent.*`，但主要实现仍然在 `src/core`、`src/agent`、`src/fixers`、`src/integrations`。

例如：

- [src/pi_sonar_agent/core/run_coordinator.py](src/pi_sonar_agent/core/run_coordinator.py)
- [src/pi_sonar_agent/main.py](src/pi_sonar_agent/main.py)

这类文件当前主要是桥接模块，负责让 `pi_sonar_agent.*` 指向真实实现。后续新代码应优先使用 `pi_sonar_agent.*` 作为 import 路径，但阅读实现时仍应以 `src/core/*`、`src/agent/*` 为主。

## 2. 执行入口

### 2.1 单目标入口

以下两种方式都会走 [src/main.py](src/main.py)：

- `python run.py`
- `pi-sonar-agent`

主入口职责：

1. 加载 `.env`
2. 解析 CLI 与默认 target
3. 创建 `RunCoordinator`
4. 创建 `RunStateStore`
5. 记录 `RUN_STARTED`
6. 运行单个 target
7. 写 `run_summary.json`
8. 记录 `RUN_FINISHED`

### 2.2 批量入口

批量入口是：

- `python -m pi_sonar_agent.batch_runner data/targets.json`

[src/batch_runner.py](src/batch_runner.py) 会：

1. 加载 `.env`
2. 读取 `targets.json`
3. 创建共享 `RunCoordinator`
4. 逐个 target 执行 `run_target()`
5. 汇总所有 target 状态
6. 写整轮 `run_summary.json`

批量入口不会复制一套独立编排逻辑，而是复用 [src/core/run_coordinator.py](src/core/run_coordinator.py)。

## 3. 运行主流程

### 3.1 启动阶段

主流程从 [src/main.py](src/main.py) 或 [src/batch_runner.py](src/batch_runner.py) 进入后，会先经过：

- [src/core/model_env.py](src/core/model_env.py): 加载 `.env`，解析模型配置
- [src/core/target_config.py](src/core/target_config.py): 解析目标配置
- [src/core/preflight.py](src/core/preflight.py): 预校验模型环境、Sonar/ADO 必填配置、工作区可写、远端 `base_branch` 存在

这里的关键变化是：

- `base_branch` 现在会真正控制初始 clone
- PAT 认证通过 [src/core/git_gateway.py](src/core/git_gateway.py) 统一收口
- 分支不存在和工作区不可写会在 issue 开始前失败，而不是中途才失败

### 3.2 仓库准备

[src/core/run_coordinator.py](src/core/run_coordinator.py) 会负责：

1. 解析收件人
2. 从 SonarQube 拉取当前作者的 open issues
3. 清理历史工作区
4. 通过 `GitRepositoryGateway.clone_branch()` 直接 clone 到 `base_branch`
5. 进入 issue 循环

相关模块：

- [src/core/git_gateway.py](src/core/git_gateway.py)
- [src/core/workspace.py](src/core/workspace.py)
- [src/integrations/sonar.py](src/integrations/sonar.py)
- [src/integrations/ado.py](src/integrations/ado.py)
- [src/core/recipient_resolution.py](src/core/recipient_resolution.py)

### 3.3 单 issue 流程

单个 issue 的处理主线在：

- [src/core/issue_retry.py](src/core/issue_retry.py)
- [src/agent/claude_agent.py](src/agent/claude_agent.py)

每个 issue 的典型执行顺序如下：

1. 记录 `ISSUE_STARTED`
2. 为当前工作区建立基线快照
3. 读取 issue 文件上下文
4. 基于规则策略生成 `IssueEditScope`
5. 生成 `IssuePlan` 与 `EditContract`
6. 组装 prompt、workspace rules、quality gate、retry memory
7. 通过 `AgentRuntime + ClaudeAdapter` 执行单次 attempt
8. 运行 `FixVerifier` 做构建验证、scope/diff 审查、规则校验
9. 失败时回滚当前 issue 改动并重试
10. 成功或最终跳过后写 issue state / artifact / event

### 3.4 attempt 内部运行时

单次 attempt 的运行时分层如下：

- [src/core/model_gateway.py](src/core/model_gateway.py): 模型网关抽象
- [src/core/claude_adapter.py](src/core/claude_adapter.py): Claude SDK 适配
- [src/core/agent_runtime.py](src/core/agent_runtime.py): 事件循环、超时、取消、hook、工具策略
- [src/core/policy.py](src/core/policy.py): tool policy 与工具使用追踪
- [src/core/registry.py](src/core/registry.py): 工具注册
- [src/core/hooks.py](src/core/hooks.py): before/after tool 和 attempt finalize hook

这层只负责“如何稳定地跑一次模型会话”，不直接负责 Sonar 业务策略。

### 3.5 最终交付阶段

所有 issue 处理结束后，[src/core/run_coordinator.py](src/core/run_coordinator.py) 会：

1. 对成功保留的改动执行最终构建
2. 生成 PR 描述和本地报告
3. 创建分支并推送
4. 创建 PR
5. 添加 reviewer
6. 发送钉钉通知
7. 写 target state 与 run state

相关模块：

- [src/fixers/build_gate.py](src/fixers/build_gate.py)
- [src/core/pr_description.py](src/core/pr_description.py)
- [src/core/dingtalk.py](src/core/dingtalk.py)

## 4. 单 Issue 约束体系

当前项目不再只依赖“prompt 里说不要顺手修”，而是把约束拆成多层。

### 4.1 规划层

- [src/core/issue_planner.py](src/core/issue_planner.py): 把 issue 元信息转成 `IssuePlan`
- [src/core/issue_contract.py](src/core/issue_contract.py): 定义 `EditContract`

`EditContract` 会声明：

- 目标文件
- 目标符号
- 允许的改动类型
- 禁止的改动类型
- 校验窗口
- patch-only 策略

### 4.2 编辑层

- [src/core/editor_policy.py](src/core/editor_policy.py): 基于 `EditContract` 限制工具和 prompt
- [src/core/issue_prompt.py](src/core/issue_prompt.py): 统一拼接系统 prompt、用户 prompt、workspace rules、quality gate、retry memory
- [src/core/resource_loader.py](src/core/resource_loader.py): 加载 [CLAUDE.md](CLAUDE.md) 和仓库级 `CLAUDE.md/AGENTS.md`

### 4.3 审查层

- [src/core/diff_reviewer.py](src/core/diff_reviewer.py): 审查 patch 是否越过合同边界
- [src/core/follow_up_store.py](src/core/follow_up_store.py): 顺手发现的相邻技术债只记录，不并入当前 patch
- [src/core/scope_guard.py](src/core/scope_guard.py): legacy scope guard

### 4.4 校验层

- [src/core/fix_verifier.py](src/core/fix_verifier.py): 构建验证、diff 审查、scope 校验、规则校验
- [src/agent/rule_validators.py](src/agent/rule_validators.py): 特定规则的本地校验
- [src/agent/rule_policies.py](src/agent/rule_policies.py): scope 模式、prompt guard、validator 绑定

### 4.5 Guardrail 模式

通过 `ISSUE_GUARDRAIL_MODE` 控制：

- `scope`: 以 legacy scope guard 为主
- `contract_review`: 以 `EditContract + DiffReviewer` 为主

当前默认是 `scope`，但新设计能力已经完整落地在 `contract_review` 链路中。

## 5. 状态、事件、工件

### 5.1 状态模型

- [src/core/state.py](src/core/state.py)

包含四层状态：

- `RunState`
- `TargetState`
- `IssueState`
- `AttemptState`

### 5.2 生命周期事件

- [src/core/events.py](src/core/events.py)

当前会记录：

- `RUN_STARTED` / `RUN_FINISHED`
- `TARGET_STARTED` / `TARGET_FINISHED`
- `ISSUE_STARTED` / `ISSUE_FINISHED`
- `ATTEMPT_STARTED` / `ATTEMPT_FINISHED`

### 5.3 工件输出

- [src/core/artifact_writer.py](src/core/artifact_writer.py)

单 issue attempt 会输出：

- `issue.json`
- `edit_contract.json`
- `prompt_context.json`
- `patch.diff`
- `reviewer_result.json`
- `build_result.json`
- `attempt_summary.json`

issue / target / run 结束后会输出：

- `issue_summary.json`
- `target_summary.json`
- `run_summary.json`

### 5.4 DB 同步

- [src/core/state_store.py](src/core/state_store.py)
- [src/core/db_client.py](src/core/db_client.py)

当前策略是：

- 优先写本地 artifact 和 `events.jsonl`
- MySQL 可用时同步写 state snapshot / event
- DB 不可用时自动降级，不阻塞主流程

## 6. 配置语义

### 6.1 单目标入口配置优先级

- `project_key` / `repository` / `author`: CLI > `.env` > `targets.json`
- `build_command` / `test_command` / `solution_path`: CLI > `.env` > `targets.json`
- `max_issues`: CLI > `.env` > `targets.json`
- `base_branch`: CLI > `targets.json` > 默认值

### 6.2 批量入口配置优先级

批量入口不读 CLI 参数覆盖业务字段，直接读每个 target：

- `base_branch`: `target.base_branch` > 默认值
- `max_issues`: `target.max_issues` > 默认值
- `keep_workspace` / `skip_build_gate`: 只对该 target 生效

### 6.3 模型环境

[src/core/model_env.py](src/core/model_env.py) 的工程约定是：

- `.env` 优先
- 不偷偷继承系统环境里的隐藏模型配置
- 支持 OpenAI 风格代理映射到 Anthropic 风格变量
- 第三方 Anthropic 兼容网关会走 Claude adapter 的兼容逻辑

## 7. 日志与排障入口

关键日志和工件目录：

- `logs/runs/`: 整轮 stdout/stderr
- `logs/issue_attempts/`: 单 issue 重试日志
- `logs/run_artifacts/`: run/target summary 与 `events.jsonl`
- `logs/issue_artifacts/`: issue/attempt 结构化工件
- `logs/follow_ups/`: reviewer 识别出的 follow-up
- `logs/pr_descriptions/`: 本地 PR 说明副本
- `.agent_workspaces/`: 临时工作区

推荐排障顺序：

1. `logs/runs/run_<timestamp>.log`
2. `logs/run_artifacts/<run_label>/run_summary.json`
3. `logs/run_artifacts/<run_label>/events.jsonl`
4. `logs/issue_artifacts/<repo>/<run_label>/<issue>/attempt-xx/`
5. `logs/issue_attempts/<repo>_<issue_key>_<timestamp>.log`
6. `.agent_workspaces/` 中保留下来的最近工作区

## 8. 核心模块分工

### 编排与配置

- [src/main.py](src/main.py)
- [src/batch_runner.py](src/batch_runner.py)
- [src/core/run_coordinator.py](src/core/run_coordinator.py)
- [src/core/target_config.py](src/core/target_config.py)
- [src/core/preflight.py](src/core/preflight.py)

### Agent 运行时

- [src/agent/claude_agent.py](src/agent/claude_agent.py)
- [src/core/agent_runtime.py](src/core/agent_runtime.py)
- [src/core/model_gateway.py](src/core/model_gateway.py)
- [src/core/claude_adapter.py](src/core/claude_adapter.py)
- [src/core/resource_loader.py](src/core/resource_loader.py)

### Issue 规划与约束

- [src/core/issue_planner.py](src/core/issue_planner.py)
- [src/core/issue_contract.py](src/core/issue_contract.py)
- [src/core/issue_prompt.py](src/core/issue_prompt.py)
- [src/core/editor_policy.py](src/core/editor_policy.py)
- [src/core/diff_reviewer.py](src/core/diff_reviewer.py)
- [src/core/follow_up_store.py](src/core/follow_up_store.py)
- [src/core/fix_verifier.py](src/core/fix_verifier.py)
- [src/core/scope_guard.py](src/core/scope_guard.py)

### 状态与工件

- [src/core/state.py](src/core/state.py)
- [src/core/events.py](src/core/events.py)
- [src/core/artifact_writer.py](src/core/artifact_writer.py)
- [src/core/state_store.py](src/core/state_store.py)
- [src/core/retry_context.py](src/core/retry_context.py)

### 外部集成

- [src/integrations/sonar.py](src/integrations/sonar.py)
- [src/integrations/ado.py](src/integrations/ado.py)
- [src/core/dingtalk.py](src/core/dingtalk.py)
- [src/core/db_client.py](src/core/db_client.py)

## 9. 推荐阅读顺序

1. [README.md](README.md)
2. [docs/RUNBOOK.md](docs/RUNBOOK.md)
3. [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md)
4. [src/main.py](src/main.py)
5. [src/core/run_coordinator.py](src/core/run_coordinator.py)
6. [src/core/issue_retry.py](src/core/issue_retry.py)
7. [src/agent/claude_agent.py](src/agent/claude_agent.py)
8. [src/core/agent_runtime.py](src/core/agent_runtime.py)
