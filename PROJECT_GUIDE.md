# pi-sonar-agent 项目结构与执行指南

这份文档面向维护者，回答四件事：

1. 当前仓库真实结构是什么
2. 单目标和批量运行到底共用哪条骨架
3. 单 issue 修复经过哪些阶段
4. 哪些运行语义已经固定，文档和代码都应以它们为准

如果你只是想把环境配好并跑起来，先看 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## 1. 目录结构

### 1.1 顶层目录

- [run.py](run.py): 本地一键入口，负责把 `src/` 加入 `sys.path` 后调用 `pi_sonar_agent.main`
- [README.md](README.md): 项目总览
- [PROJECT_GUIDE.md](PROJECT_GUIDE.md): 当前文档
- [docs/RUNBOOK.md](docs/RUNBOOK.md): 安装、配置、运行、排障
- [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md): 已踩问题、当前约定、常见误区
- [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md): 重构与升级状态快照
- [docs/s107-fix-guide.md](docs/s107-fix-guide.md): `csharpsquid:S107` 专项修复指南
- [data/targets.json](data/targets.json): 默认 target / 批量 target 配置
- [data/rule_profiles.json](data/rule_profiles.json): 规则 profile、engine 路由、turn 预算
- [.github/workflows/ci.yml](.github/workflows/ci.yml): CI 质量门禁

### 1.2 源码目录

- [src/main.py](src/main.py): 单目标正式入口
- [src/batch_runner.py](src/batch_runner.py): 批量入口
- [src/core](src/core): 当前主要实现层，编排、运行时、工件、状态、prompt、verifier 都在这里
- [src/agent](src/agent): `ClaudeFixAgent`、规则策略和兼容层
- [src/fixers](src/fixers): build gate、rule profiles、Roslyn 相关能力
- [src/integrations](src/integrations): SonarQube / Azure DevOps 集成
- [src/sonar_mcp](src/sonar_mcp): MCP 工具与 server
- [src/reporting](src/reporting): 本地报告输出
- [src/pi_sonar_agent](src/pi_sonar_agent): 标准包入口和桥接模块

### 1.3 关于 `src/pi_sonar_agent`

仓库对外导入路径已经统一为 `pi_sonar_agent.*`，但主要实现仍然在 `src/core`、`src/agent`、`src/fixers`、`src/integrations`。

阅读和修改实现时：

- 真实逻辑优先看 `src/core/*`、`src/agent/*`
- 对外 import 和测试 monkeypatch 优先使用 `pi_sonar_agent.*`

## 2. 执行入口

### 2.1 单目标入口

以下两种方式都会走 [src/main.py](src/main.py)：

- `python run.py`
- 安装后的 `pi-sonar-agent`

单目标入口职责：

1. 加载 `.env`
2. 解析 CLI 参数和默认 target
3. 创建 `RunCoordinator`
4. 记录 run 级状态和事件
5. 执行单个 target
6. 写 `run_summary.json`

### 2.2 批量入口

批量入口是：

- `python -m pi_sonar_agent.batch_runner data/targets.json`

[src/batch_runner.py](src/batch_runner.py) 会：

1. 加载 `.env`
2. 读取 `targets.json`
3. 创建共享 `RunCoordinator`
4. 逐个 target 调用 `run_target()`
5. 汇总所有 target 状态
6. 写整轮 `run_summary.json`

当前不再维护第二套批量编排逻辑，单目标和批量模式共享同一条运行骨架。

## 3. 当前固定运行语义

这些语义已经在代码中收口，文档和排障都应以此为准：

- 当前唯一支持的 issue 执行模式是 `simple_loop`。[src/core/simple_mode.py](src/core/simple_mode.py)
- `ISSUE_GUARDRAIL_MODE` 支持 `scope` 和 `contract_review`，默认值是 `scope`。[src/agent/claude_agent.py](src/agent/claude_agent.py)
- fix runtime 默认声明的内建工具面是 `Read/Grep/Glob/Edit/MultiEdit/Write/Bash`，`Finish` 只出现在 allowlist 中，不作为普通读写工具展示。[src/core/tool_surface.py](src/core/tool_surface.py)
- Claude fix agent 默认 turn floor 是 `16`，规则 profile 可以继续抬高，例如 `S107` 当前 profile 为 `20`。[src/agent/claude_agent.py](src/agent/claude_agent.py) [data/rule_profiles.json](data/rule_profiles.json)
- 对非官方 Anthropic endpoint，Claude adapter 当前会进入 `bare` 兼容模式。[src/core/claude_adapter.py](src/core/claude_adapter.py)
- `reviewer_email` 默认回退到 `author`；`dingtalk_userid` 的顺序是 `targets.json` 显式值 > MySQL 反查 > `unresolved`。[src/core/recipient_resolution.py](src/core/recipient_resolution.py)
- `DB_*` 缺失时不会报数据库错误，而是直接跳过 userId 反查。[src/core/db_client.py](src/core/db_client.py)

## 4. 端到端流程

### 4.1 启动阶段

主流程从 [src/main.py](src/main.py) 或 [src/batch_runner.py](src/batch_runner.py) 进入后，会先经过：

- [src/core/model_env.py](src/core/model_env.py): 加载 `.env`，解析模型配置，构建 child env
- [src/core/target_config.py](src/core/target_config.py): 解析 target 配置与优先级
- [src/core/preflight.py](src/core/preflight.py): 校验模型环境、Sonar/ADO 配置、工作区可写、远端 `base_branch` 存在

这里的关键点是：

- `base_branch` 现在真正控制初始 clone
- PAT / clone / push 已统一走 `GitRepositoryGateway`
- 启动前失败和运行中失败会被明确区分

### 4.2 仓库准备与 target 级编排

[src/core/run_coordinator.py](src/core/run_coordinator.py) 负责：

1. 解析 reviewer / 钉钉收件人
2. 从 SonarQube 拉取当前作者的 open issues
3. 清理历史工作区
4. clone 到生效 `base_branch`
5. 进入 issue 循环
6. 最终 build / test、PR、通知、target state 汇总

相关模块：

- [src/core/git_gateway.py](src/core/git_gateway.py)
- [src/core/recipient_resolution.py](src/core/recipient_resolution.py)
- [src/integrations/sonar.py](src/integrations/sonar.py)
- [src/integrations/ado.py](src/integrations/ado.py)
- [src/core/dingtalk.py](src/core/dingtalk.py)

### 4.3 单 issue 生命周期

单个 issue 的处理主线在：

- [src/core/issue_retry.py](src/core/issue_retry.py)
- [src/agent/claude_agent.py](src/agent/claude_agent.py)

典型顺序如下：

1. 记录 `ISSUE_STARTED`
2. 为当前 issue 建立工作区基线
3. 读取代码上下文和规则详情
4. 生成 `IssuePlan` 与 `EditContract`
5. 计算本轮运行计划、guardrail 模式和 retry memory
6. 进入 fix runtime
7. 输出 patch、tool trace、attempt artifact
8. 运行 `FixVerifier`
9. 失败时回滚当前 issue 改动并重试
10. 成功或最终跳过后写 issue artifact / state / event

### 4.4 attempt 运行时

单次 attempt 的核心分层如下：

- [src/core/model_gateway.py](src/core/model_gateway.py): 归一化 request / event / abort 契约
- [src/core/claude_adapter.py](src/core/claude_adapter.py): Claude SDK 适配、provider 兼容、CLI 参数、child env
- [src/core/agent_runtime.py](src/core/agent_runtime.py): 会话生命周期、超时、取消、heartbeat、hook
- [src/core/registry.py](src/core/registry.py): 内建工具和 MCP 工具注册
- [src/core/policy.py](src/core/policy.py): allowlist、scoped rules、Bash / Write 创建约束
- [src/core/tool_surface.py](src/core/tool_surface.py): 内建工具面、shell prompt 约束

这层只负责“如何稳定地跑一次模型会话”，不负责 Sonar 业务策略。

### 4.5 单 issue 约束与验证链

当前项目不再只靠 prompt 说“不要顺手修”，而是分成多层：

- [src/core/issue_planner.py](src/core/issue_planner.py): issue 策略、计划、`EditContract`
- [src/core/issue_contract.py](src/core/issue_contract.py): 结构化工程边界
- [src/core/editor_policy.py](src/core/editor_policy.py): patch-only 工具策略
- [src/core/diff_reviewer.py](src/core/diff_reviewer.py): patch 审查与 drift 分类
- [src/core/follow_up_store.py](src/core/follow_up_store.py): incidental fix 和 follow-up
- [src/core/fix_verifier.py](src/core/fix_verifier.py): deterministic build / scope / quality / review gate
- [src/core/review_gate.py](src/core/review_gate.py): 模型化 patch audit
- [src/core/retry_context.py](src/core/retry_context.py): 结构化重试上下文

补充说明：

- `simple_loop` 模式下，fix 子 agent 不负责自行编译；构建、post-check 和 review gate 由外层统一执行。
- `review_gate` 默认开启，可单独指定 provider / model / timeout。
- `S107` 有额外硬约束、专项指南和 post-check，即使主路径仍是 Roslyn，也保留了运行时护栏。

### 4.6 最终交付阶段

所有 issue 处理结束后，[src/core/run_coordinator.py](src/core/run_coordinator.py) 会：

1. 对保留下来的改动执行最终构建
2. 生成 PR 说明和本地报告
3. 创建修复分支并推送
4. 创建 PR
5. 添加 reviewer
6. 发送钉钉通知
7. 写 target / run 状态与工件

## 5. 配置语义

### 5.1 单目标入口配置优先级

- `project_key` / `repository` / `author`: CLI > `.env` > `targets.json`
- `build_command` / `test_command` / `solution_path`: CLI > `.env` > `targets.json`
- `max_issues`: CLI > `.env` > `targets.json`
- `base_branch`: CLI `--base-branch` > `targets.json.base_branch` > 默认值 `develop`

注意：

- `base_branch` 不从 `.env` 读取
- `reviewer_email` 和 `dingtalk_userid` 当前只从 `targets.json` 读取显式值

### 5.2 批量入口配置优先级

批量运行时每个 target 直接读取 `targets.json`：

- `base_branch`: `target.base_branch` > 默认值 `develop`
- `max_issues`: `target.max_issues` > 默认值 `3`
- `keep_workspace` / `skip_build_gate`: 仅批量入口识别
- `issue_keys`: 可用于把一个 target 收窄成指定 issue 集合

### 5.3 模型环境

[src/core/model_env.py](src/core/model_env.py) 和 [src/core/claude_adapter.py](src/core/claude_adapter.py) 的当前约定：

- `.env` 是工程内模型配置的单一事实来源
- 不偷偷继承机器级隐藏模型配置
- 支持 `OPENAI_*` 到 `ANTHROPIC_*` 的兼容映射
- 非官方 Anthropic endpoint 会走 `bare` 模式
- 当前第三方兼容逻辑可能把 `ANTHROPIC_AUTH_TOKEN` 桥接成 `ANTHROPIC_API_KEY`

这意味着：

- 调试模型问题优先查 `.env` 和 request snapshot
- “本地交互式 Claude Code 能用”不代表自动化 bare 链路就等价

### 5.4 收件人解析

[src/core/recipient_resolution.py](src/core/recipient_resolution.py) 的顺序是：

1. `reviewer_email`: `targets.json.reviewer_email` > `author`
2. `dingtalk_userid`: `targets.json.dingtalk_userid`
3. 若未配置，尝试 `mysql_client.lookup_dingtalk_userid_by_email(author)`
4. 查不到则返回 `unresolved`

MySQL 查询语义在 [src/core/db_client.py](src/core/db_client.py) 中固定为：

```sql
SELECT UserId
FROM erp4.dingtalkuserdetail
WHERE Email = %s
LIMIT 1
```

如果 `.env` 未配置 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME`，MySQL client 不会创建，反查会被直接跳过。

## 6. 状态、事件、工件

### 6.1 状态模型

- [src/core/state.py](src/core/state.py)

包含四层状态：

- `RunState`
- `TargetState`
- `IssueState`
- `AttemptState`

### 6.2 生命周期事件

- [src/core/events.py](src/core/events.py)

当前稳定记录：

- `RUN_STARTED` / `RUN_FINISHED`
- `TARGET_STARTED` / `TARGET_FINISHED`
- `ISSUE_STARTED` / `ISSUE_FINISHED`
- `ATTEMPT_STARTED` / `ATTEMPT_FINISHED`

### 6.3 工件输出

- [src/core/artifact_writer.py](src/core/artifact_writer.py)

当前常见工件包括：

- `edit_contract.json`
- `prompt_context.json`
- `patch.diff`
- `reviewer_result.json`
- `build_result.json`
- `attempt_summary.json`
- `issue_summary.json`
- `target_summary.json`
- `run_summary.json`
- `events.jsonl`

## 7. 排障入口

推荐排障顺序：

1. `logs/runs/<run_label>.log`
2. `logs/run_artifacts/<run_label>/run_summary.json`
3. `logs/run_artifacts/<run_label>/events.jsonl`
4. `logs/issue_artifacts/<repo>/<run_label>/<issue>/`
5. `logs/issue_attempts/`
6. 保留的 `.agent_workspaces/` 工作区

常见判断信号：

- `authentication_failed`: 先查 `.env`、`ANTHROPIC_*`、`bare` 模式和 provider 认证要求
- `Reached maximum number of turns`: 这是 agent 回合数耗尽，不是 `dotnet build` 编译错误
- `DingTalk UserId: (unresolved)`: 先区分是 `targets.json` 未配，还是 `.env` 未配 `DB_*`
- `Review gate status=retry`: 优先看 `review_gate_result` 和 `retry_context`

## 8. 建议阅读顺序

1. [README.md](README.md)
2. [docs/RUNBOOK.md](docs/RUNBOOK.md)
3. [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md)
4. [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md)
