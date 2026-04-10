# Agent 工程重构实施方案

## 1. 决策结论

本项目不建议另起一个全新仓库从零重写，也不建议继续围绕现有骨架做零散补丁。

推荐路线是：

- 在当前仓库内重构
- 用 `V2` 运行骨架逐步替换旧实现
- 通过 feature flag 和渐进迁移保持主链路持续可跑

这份文档不是概念性说明，而是实施文档。目标是让重构过程能够按阶段推进，并且每个阶段都知道：

- 先解决什么
- 为什么先做这些
- 做完后交付什么
- 怎么验证是否可以进入下一阶段

## 2. 当前问题归纳

当前 `pi-sonar-agent` 已经具备“拉 issue -> 调模型修复 -> 本地构建 -> 提 PR -> 通知”的主链路，但工程实现上存在以下核心问题：

1. 分支语义错误：`base_branch` 没有真正控制初始 clone，主流程固定先拉 `develop`
2. Git 认证链路不一致：`ADO_PAT` 被强依赖，但实际 `git clone`/`fetch` 并不稳定使用该凭据
3. 超时治理不完整：只有首响应超时，没有 issue 级硬超时、空闲超时和可靠取消
4. 入口和编排重复：单目标入口与批量入口维护了两套流程，已出现行为漂移
5. 包结构脆弱：入口依赖兼容 hack，本地路径耦合明显，标准 package 边界未建立
6. Agent 职责过度集中：`ClaudeFixAgent` 同时承担模型接入、规则策略、工具约束、retry 反馈和部分 orchestration
7. `planning / memory / tools` 只有概念，没有形成正式模块边界
8. 单 Issue 约束机制不理想：当前 scope 的目标是对的，但实现过硬、过窄，且未完整覆盖跨文件副作用

## 3. 重构总目标

### 3.1 第一目标：先保稳定

- 任意 target 都能在明确分支、明确认证、明确超时的条件下稳定启动
- 任意单 issue 失败、卡死、分支不存在、认证失败时，都能快速失败并保留清晰状态
- 单目标入口和批量入口共用一套运行骨架

### 3.2 第二目标：再补边界

- `Model / Runtime / Planning / Memory / Tools / Reporting` 职责清晰
- 运行状态、尝试历史、日志、DB、报告统一到正式状态模型
- 单 Issue 修复约束从脆弱 scope 升级为可解释、可审查、可复盘的治理链路

### 3.3 第三目标：最后收口工程质量

- 回到标准 Python package 结构
- 清理重复实现和死代码
- 用 `ruff + pytest + 关键集成测试` 兜底

## 4. 重构策略

### 4.1 采用“仓内 V2 框架 + 渐进迁移”

- 在当前仓库内建立新的运行时骨架
- 旧入口和旧实现先保留，作为兼容路径
- 新老实现通过 feature flag 并行一段时间
- 每阶段只跨一个边界层，不做大爆炸式迁移

### 4.2 借鉴 `pi-mono`，但不照搬技术栈

`pi-mono` 对本项目有很强的架构参考意义，但参考的是分层思想，不是 npm monorepo、TUI 或交互外壳。

建议借鉴关系如下：

- `packages/ai` -> 本项目的 `ModelGateway`
- `packages/agent` -> 本项目的 `AgentRuntime + hooks + tool policy`
- `packages/coding-agent` -> 本项目的 `RunCoordinator / IssueRunner / state / resource loader`
- `AGENTS.md / skills / prompt templates / extensions` -> 本项目的 `CLAUDE.md / AGENTS.md / EditContract / Skill / Hook`

不建议照搬的部分：

- `pi-mono` 的 TUI、web UI、terminal 交互层
- npm monorepo 工程形态
- 大而全的 extension marketplace
- 高度通用的交互式 session tree

## 5. 实施原则

- 先收敛运行链路，再增强 Agent 能力
- 先建立单一事实来源，再拆模块
- 先让失败可控、可恢复、可观测，再提高成功率
- 先引入新骨架，再迁移旧逻辑
- 先补验收项和回归测试，再删除旧实现

## 6. 目标架构

建议在当前仓库内逐步收敛为如下结构：

```text
src/pi_sonar_agent/
  app/
    run_coordinator.py
    target_runner.py
    issue_runner.py
  agent/
    model_gateway.py
    claude_adapter.py
  runtime/
    agent_runtime.py
    hooks.py
    state.py
    events.py
    resource_loader.py
    artifact_writer.py
  planning/
    issue_planner.py
    issue_contract.py
  memory/
    run_memory.py
    issue_memory.py
    knowledge_memory.py
  tools/
    registry.py
    policy.py
    editor_policy.py
  integrations/
    sonar.py
    ado.py
    dingtalk.py
    db.py
  reporting/
    pr_description.py
    html_report.py
```

核心对象关系如下：

1. `RunCoordinator` 负责一次运行的编排
2. `TargetRunner` 负责单个目标仓库
3. `IssueRunner` 负责单个 issue 的完整处理
4. `IssuePlanner` 负责规划本次修复
5. `AgentRuntime` 负责模型对话、工具调用和 hook
6. `DiffReviewer` 负责审查变更是否仍然聚焦当前 issue
7. `ArtifactWriter` 负责产出结构化记录

## 7. 配置与边界的统一决策

### 7.1 `base_branch` 的统一语义

`base_branch` 定义为“工作基线分支”，其值必须决定：

- 初始 clone 的分支
- issue 修复的基线工作树
- PR 的目标分支

短期配置策略：

- 优先级为 `--base-branch > targets.json.base_branch > 默认值`
- 初始 clone 必须直接使用最终生效的 `base_branch`

中期配置策略：

- 若同一个 repository 会重复出现在多个 target 中，`base_branch` 应逐步上收为 repository-level config
- `targets.json` 可以暂时保留默认值，但不应让相同仓库在不同 target 中出现冲突分支定义

### 7.2 Git 认证的统一语义

Git 认证必须由单一 `GitRepositoryGateway` 承担。

要求：

- `ADO_PAT` 的用途必须明确
- REST API 认证和 Git 认证要么共用同一凭据源，要么有清晰区分
- 所有 clone / fetch / checkout / push 都走同一实现
- 脱敏日志是强制要求

建议优先级：

1. 使用安全的凭据头或 credential helper
2. 若必须拼接 URL，由 gateway 统一处理并保证日志脱敏
3. 删除散落在 `workspace.py`、`build_gate.py` 等处的第二套 clone 逻辑

### 7.3 单 Issue 修复的统一目标

本项目的单 Issue 约束目标不是“限制模型改行号”，而是：

- 一次 attempt 只解决一个 Sonar issue
- 不顺手修同文件其他问题
- 如果发现相邻技术债，记录而不是混入当前 patch
- 在不牺牲正确修复的前提下，保持 patch 最小化

## 8. 分阶段实施方案

## Phase 0：运行稳定性兜底

### 目标

先把“能稳定跑完一轮”做扎实，解决分支、认证、超时和入口漂移。

### 当前执行状态（2026-04-03）

- `Phase 0`：已完成
- `0.1 统一运行入口`：已完成。`RunCoordinator` 已接入主入口与批量入口，批量模式已改为“循环调用共享单目标执行器”，不再维护第二套 issue 编排流程
- `0.2 修正分支语义`：已完成。主入口和批量入口已改为直接按生效 `base_branch` clone，不再固定先拉 `develop`
- `0.3 统一 Git 认证`：已完成。`GitRepositoryGateway` 已统一主入口、批量入口、`workspace`、`build_gate` 和 legacy `GitClient` 的 clone/建分支/提交/推送底层实现；兼容 facade 的最终删除留待 Phase 4
- `0.4 补齐全链路超时与取消`：已完成。已补 SDK 初始化超时、首响应超时、后续响应空闲超时和单 issue 总时长超时，并通过 `_SDKSessionController` 明确接入 `interrupt / response_stream close / disconnect` 的 abort/cancel 清理链路
- `0.5 统一启动前校验`：已完成。主入口与批量入口已统一接入共享 runtime preflight，模型环境与 Sonar/ADO 必填配置、远端基线分支存在性、workspace 可写性已按同一规则校验
- `补充修复`：已修正 `preflight / target_config` 中空映射意外回退到 `os.environ` 的问题，并补充了入口层、preflight 和启动前校验回归测试

### 主要任务

#### 0.1 统一运行入口

- 引入 `RunCoordinator`
- 单目标入口和批量入口都只做参数解析和配置装配
- 批量模式变为“循环调用单目标执行器”，不再维护第二套流程

#### 0.2 修正分支语义

- 删除“固定 clone `develop` 再切换分支”的逻辑
- 初始 clone 直接使用最终生效的 `base_branch`
- 若分支不存在，在运行早期快速失败
- 日志中明确记录：
- 请求分支
- 生效分支
- 分支来源

#### 0.3 统一 Git 认证

- 引入 `GitRepositoryGateway`
- 收口 clone / fetch / checkout / push
- 统一处理 PAT、remote URL、日志脱敏和错误包装

#### 0.4 补齐全链路超时与取消

- 增加 SDK 初始化超时
- 增加模型/工具空闲超时
- 增加单 issue 硬超时
- 超时后必须：
- 停止当前 issue
- 标记失败类型
- 恢复 baseline
- 继续下一条 issue

#### 0.5 统一启动前校验

- 校验模型环境
- 校验 Sonar/ADO 基础配置
- 校验分支存在性
- 校验 workspace 可写
- 区分“配置错误”和“运行中失败”

### 交付物

- `RunCoordinator`
- `GitRepositoryGateway`
- 统一 preflight 校验
- issue 级 timeout/cancel/rollback

### 验收标准

- 指定非 `develop` 分支时，可直接从该分支开始 clone 和运行
- 单目标入口和批量入口行为一致
- 模型卡死时，单 issue 能在限定时间内退出并进入下一条
- clone / fetch / push 只存在一套正式实现
- 日志中不暴露 PAT、token 或带密 URL

## Phase 1：状态模型与执行工件

### 目标

让运行状态、重试上下文、日志和报告统一进入结构化状态模型，而不是只靠文本拼接。

### 当前执行状态（2026-04-03）

- `Phase 1`：已完成
- `1.1 建立状态模型`：已完成。`RunState / TargetState / IssueState / AttemptState`、状态推导函数和基础枚举已接入主流程
- `1.2 建立事件模型`：已完成。已新增 `events.py`、`StateEvent / AttemptEvent / EventRecorder`，并在 `main / batch_runner / RunCoordinator / issue_retry` 接入 `run -> target -> issue -> attempt` 关键事件流
- `1.3 引入结构化 artifact`：已完成。单 issue attempt、issue summary、target summary、run summary 和 `events.jsonl` 已写入稳定目录结构
- `1.4 结构化 retry memory`：已完成。已新增 `RetryContext`、结构化 compiler/scope retry memory、artifact 落盘和 prompt 渲染链路；`ClaudeFixAgent` 现在优先消费 `RetryContext`，不再要求上游直接拼散乱日志
- `1.5 DB 接入主流程`：已完成。已新增 `state_store.py`，并将 MySQL 快照、事件写入与旧 `run_record / issue_record` 兼容更新接入主流程；DB 不可用时会自动降级为本地 artifact + event log
- `补充说明`：本阶段已将 `issue_retry`、`RunCoordinator`、单目标入口、批量入口串成 `attempt -> issue -> target -> run` 的状态链，并补上 `RetryContext -> prompt -> artifact`、`EventRecorder -> events.jsonl`、`RunStateStore -> MySQL snapshot/event` 的完整链路

### 主要任务

#### 1.1 建立状态模型

- 定义 `RunState`
- 定义 `TargetState`
- 定义 `IssueState`
- 定义 `AttemptState`
- 定义 failure kind、skip reason、retry reason 枚举

#### 1.2 建立事件模型

- 定义 `AttemptEvent`
- 定义运行关键节点事件
- 用事件驱动 artifact、DB 和报告生成

#### 1.3 引入结构化 artifact

每个 issue attempt 至少产出：

- `issue.json`
- `edit_contract.json`
- `prompt_context.json`
- `patch.diff`
- `reviewer_result.json`
- `build_result.json`
- `attempt_summary.json`

#### 1.4 结构化 retry memory

- 引入 `RetryContext`
- 包含上次失败类型、关键编译错误、越界或 reviewer 拒绝信息、上次变更文件等
- prompt 仅负责渲染 `RetryContext`，不直接拼散乱日志

#### 1.5 DB 接入主流程

- 将 DB 从辅助能力升级为正式状态存储
- DB 不可用时可降级为本地 artifact，不允许成为单点

### 交付物

- `state.py`
- `events.py`
- `artifact_writer.py`
- `retry_context.py`
- `state_store.py`
- `RetryContext`
- DB 状态写入集成

### 验收标准

- 任一运行中断后，可以从 DB 或 artifact 精确回答每个 issue 的状态
- retry 输入来自结构化对象，而不是长字符串拼接
- DB 不可用时，主流程仍可运行且状态不丢失
- run/target/issue/attempt 关键节点都有结构化事件记录，且可从 `events.jsonl` 或 DB 事件表回放

## Phase 2：运行时分层与能力边界

### 目标

将当前过于集中的 `ClaudeFixAgent` 拆开，形成正式的模型接入层、运行时层、工具策略层和资源加载层。

### 当前执行状态（2026-04-03）

- `Phase 2`：已完成
- `2.1 引入 ModelGateway`：已完成。已新增 `model_gateway.py` 和 `claude_adapter.py`，Claude SDK 适配、provider 兼容参数、环境变量整理、模型选择和消息归一化已从 `ClaudeFixAgent` 中抽离
- `2.2 引入 AgentRuntime`：已完成。已新增 `agent_runtime.py`，单次 issue attempt 的 SDK 会话、首响应/后续响应/硬超时、heartbeat、取消和结果汇总都由共享 runtime 承担
- `2.3 引入 hook pipeline`：已完成。已新增 `hooks.py`，运行时已正式接入 `before_tool_call / after_tool_call / before_attempt_finalize / after_attempt_finalize`
- `2.4 引入 ToolRegistry / ToolPolicy`：已完成。已新增 `registry.py` 和 `policy.py`，内建编辑工具、受控 build 工具、禁止工具的注册与分类都已收口到独立策略层
- `2.5 引入 ResourceLoader`：已完成。已新增 `resource_loader.py`，质量门禁、`CLAUDE.md / AGENTS.md` 长期规则和 system prompt 组合逻辑已从 `ClaudeFixAgent` 中抽离
- `2.6 瘦身 ClaudeFixAgent`：已完成。`ClaudeFixAgent.fix_issue()` 已改为委托 `ClaudeAdapter + AgentRuntime + ToolPolicy + ResourceLoader` 执行单次 attempt，并在 Phase 3/4 继续将 issue contract、reviewer、prompt、legacy scope guard、build/rule verification 下沉到独立组件；当前兼容层已不再承载 runtime/orchestration 核心逻辑

### 主要任务

#### 2.1 引入 `ModelGateway`

- 把 Claude SDK 适配收口为 `ClaudeAdapter`
- 对外暴露统一能力接口，例如：
- `stream()`
- `complete()`
- `abort()`
- `supports_tools()`
- 为后续 OpenAI/Azure OpenAI 预留接入点

#### 2.2 引入 `AgentRuntime`

- 负责单次模型循环
- 负责工具调用执行
- 负责 hook 调用
- 不承载业务编排

#### 2.3 引入 hook pipeline

建议至少支持：

- `before_tool_call`
- `after_tool_call`
- `before_attempt_finalize`
- `after_attempt_finalize`

这些 hook 用于承接：

- 工具准入
- 编辑约束
- diff 审查
- build gate
- follow-up 记录
- 失败分类

#### 2.4 引入 `ToolRegistry / ToolPolicy`

- 区分只读工具、写入工具、受控工具
- 模型默认只接触只读和受限写入工具
- build/git/PR/通知由外层 orchestrator 控制

#### 2.5 引入 `ResourceLoader`

统一加载：

- `CLAUDE.md` / `AGENTS.md`
- 规则模板
- 可选 skill 配置
- prompt 片段

要求：

- 长期规则和动态上下文分离
- prompt 构造只消费已解析资源

#### 2.6 瘦身 `ClaudeFixAgent`

最终 `ClaudeFixAgent` 只保留：

- Claude 模型调用适配
- SDK 会话生命周期
- 消息收发转换

规则策略、scope、retry、工具集选择、运行编排都从中移出。

### 交付物

- `model_gateway.py`
- `claude_adapter.py`
- `agent_runtime.py`
- `hooks.py`
- `registry.py`
- `policy.py`
- `resource_loader.py`

### 验收标准

- 可以独立测试 `ModelGateway`
- 可以独立测试 `AgentRuntime + hooks`
- 可以独立测试 `ToolPolicy`
- `ClaudeFixAgent` 不再承担 orchestration 职责

## Phase 3：单 Issue 约束治理升级

### 目标

将当前硬 scope 机制升级为更灵活、更可解释的单 Issue 治理链路，在抑制 incidental fix 的同时不误伤合法修复。

### 当前执行状态（2026-04-03）

- `Phase 3`：已完成
- `3.1 引入 IssuePlanner`：已完成。已新增 `issue_planner.py`，将 issue 级修复策略、验证计划和 `EditContract` 生成从 `ClaudeFixAgent` 中抽离
- `3.2 引入 EditContract`：已完成。已新增 `issue_contract.py`，并在每次 issue attempt 中生成结构化 `edit_contract.json`
- `3.3 收敛为 patch-only 编辑`：已完成。已新增 `editor_policy.py`，并通过 `ToolPolicy + EditorPolicy` 将 contract 模式下的默认允许工具收敛为 patch-only 风格，不再允许 whole-file `Write`
- `3.4 引入 DiffReviewer`：已完成。已新增 `diff_reviewer.py`，并在 `ClaudeFixAgent.fix_issue()` 中对本次 attempt 的文件 diff 做契约审查，支持跨文件触碰和同文件越界识别
- `3.5 引入 FollowUpQueue`：已完成。已新增 `follow_up_store.py`，`DiffReviewer` 发现的 incidental fix / 邻近技术债会写入 `logs/follow_ups/...jsonl`
- `3.6 引入长期规则文件`：已完成。仓库根目录已新增 `CLAUDE.md`，`ResourceLoader` 现会把 agent-level 规则与目标仓库规则一并并入 system prompt
- `3.7 用 feature flag 渐进替换旧 scope`：已完成。已新增 `ISSUE_GUARDRAIL_MODE=scope|contract_review`；默认仍保留 legacy `scope`，`contract_review` 已可独立启用并落盘 reviewer/follow-up 工件
- `3.8 收口 touched-region 与 boundary policy`：已完成。已新增 `attempt_changes.py / boundary_policy.py / boundary_runtime.py`，将 patch touched-region、scope 越界判定和 contract line-range 判定收口为共享 runtime/policy；修复了“attempt 起始时干净文件被误判为整文件变更”的 baseline 对比缺陷，并进一步把删除前/删除后坐标分开建模，消除了纯删除 hunk 在 `scope` 模式下的行号错位
- `补充说明`：`ClaudeFixAgent` 已继续下沉一层，issue contract、editor policy、diff reviewer 和 follow-up queue 均已从单体逻辑中抽离；issue 级 prompt 装配、build 验证和规则本地校验仍保留在兼容层，后续在 Phase 4 / 包结构收口中继续下沉
- `日志驱动补充整改（2026-04-08）`：针对 `batch_20260408122214.log` 中 `S1481 / S125` 的连续失败，已补上“真实 patch touched lines 单一真源”和 `S125` 的相邻清理策略，避免单行补丁被误判成整文件修改，也避免删注释代码时被过窄 statement scope 误伤；针对 `batch_20260408133124.log` 暴露出的纯删除 hunk 错位问题，已把 before/after touched lines 分离，改为 `scope/reviewer` 使用删除前坐标、`quality gate` 使用删除后坐标

### 主要任务

#### 3.1 引入 `IssuePlanner`

输入：

- Sonar issue
- 代码上下文
- 规则策略
- `RetryContext`
- 长期工程规则

输出：

- 修复策略
- `EditContract`
- 验证计划
- 跳过决策

#### 3.2 引入 `EditContract`

`EditContract` 是单 issue 的工程边界，不是死板行号窗口。

推荐字段：

- `issue_key`
- `target_files`
- `target_symbols`
- `allowed_change_kinds`
- `forbidden_change_kinds`
- `validation_plan`
- `follow_up_policy`
- `review_hints`

建议结构：

```json
{
  "issue_key": "AXXX-123",
  "target_files": ["src/Foo/BarService.cs"],
  "target_symbols": [
    {
      "file": "src/Foo/BarService.cs",
      "symbol": "BarService.ProcessOrder",
      "reason": "Sonar issue is located in this method"
    }
  ],
  "allowed_change_kinds": [
    "condition-rewrite",
    "extract-local",
    "guard-clause-adjustment"
  ],
  "forbidden_change_kinds": [
    "drive-by-refactor",
    "whole-file-format",
    "touch-unrelated-tests"
  ],
  "validation_plan": ["build"],
  "follow_up_policy": "record_only",
  "review_hints": [
    "ignore nearby formatting-only noise",
    "flag unrelated edits in the same file"
  ]
}
```

#### 3.3 收敛为 patch-only 编辑

- 模型默认只允许统一 diff、Search/Replace 或精确 patch
- 不允许整文件重写
- `ToolPolicy` 根据 `EditContract` 限制可修改文件

#### 3.4 引入 `DiffReviewer`

`DiffReviewer` 的目标不是“判断有没有越过某几行”，而是判断：

- 这些 hunk 是否仍然是完成当前 issue 主修复所必需
- 是否出现与当前 issue 无关的顺手修
- 是否触碰未声明文件或无关符号

建议返回结构：

```json
{
  "status": "retry",
  "violations": [
    {
      "type": "incidental_fix",
      "file": "src/Foo/BarService.cs",
      "reason": "This hunk changes unrelated null-check logic not required by the current issue"
    }
  ],
  "follow_ups": [
    {
      "file": "src/Foo/BarService.cs",
      "summary": "Potential adjacent null-handling cleanup",
      "source_issue_key": "AXXX-123"
    }
  ]
}
```

#### 3.5 引入 `FollowUpQueue`

所有 incidental fix 或相邻技术债，不直接混入当前 patch，而是进入 `FollowUpQueue`。

建议字段：

- `source_issue_key`
- `file`
- `symbol`
- `summary`
- `evidence_hunk`
- `discovered_at`
- `discovered_by`

#### 3.6 引入长期规则文件

建议以 `CLAUDE.md` 为单一事实来源，必要时生成等价 `AGENTS.md`。

建议初稿：

```md
# pi-sonar-agent Working Rules

- Solve exactly one Sonar issue per attempt.
- Stay inside the edit contract.
- Use the smallest patch that can pass validation.
- Do not make drive-by fixes in the same file.
- Record incidental findings in FOLLOW_UPS instead of editing them.
- If a fix seems to require broader refactoring, explain why in the review output.
```

#### 3.7 用 feature flag 渐进替换旧 scope

- 保留旧 scope 一段时间做对照
- 推荐增加 `ISSUE_GUARDRAIL_MODE=scope|contract_review`
- 当 `contract_review` 在成功率不下降的前提下显著降低 incidental fix，再逐步替代旧逻辑

#### 3.8 收口 touched-region 与 boundary policy

针对日志中暴露出的 `S1481 / S125` 失败，补齐剩余 guardrail 收口工作：

- touched-region 必须从真实 patch hunk 提取，不能再用“起始时已脏文件快照”推测整文件范围
- `DiffReviewer`、legacy `scope` 校验、`QualityGateVerifier` 必须共用同一份 touched-region 事实
- touched-region 不能只保留一套 `changed_lines`；必须显式区分删除前/删除后坐标，避免纯删除 patch 被映射成相邻存活行
- 边界判定要从 `ClaudeFixAgent` 的局部猜测下沉到 runtime/policy，形成显式的 boundary review pipeline
- 对 `S125` 这类“移除注释代码后需要顺带清理紧邻死变量”的规则，允许受控的 adjacent cleanup，而不是继续强卡单 statement 行窗

参考 `pi-mono` 与 Claude Code 的工程思想：

- 任务是一等对象
- 边界是运行时决策，不是散落的 if 判断
- 恢复性来自显式状态、工件与可解释的失败原因

### 交付物

- `issue_planner.py`
- `issue_contract.py`
- `editor_policy.py`
- `diff_reviewer.py`
- `follow_up_store.py`
- `CLAUDE.md`

### 核心指标

- 单 issue 平均改动文件数
- 单 issue 平均 hunk 数
- reviewer 拒绝率
- follow-up 产出数
- build/test 通过率
- 最终 issue 修复成功率

### 验收标准

- 单个 issue 的修复边界可以通过 `EditContract + DiffReviewer` 解释和复盘
- 同文件 incidental fix 能被识别、拦截或转存
- 正确修复成功率不低于旧 scope 模式

## Phase 4：包结构收口与旧实现退场

### 目标

完成标准 package 化，删除兼容 hack、重复实现和半接线代码。

### 当前执行状态（2026-04-03）

- `Phase 4`：已完成
- `4.1 标准化包结构`：已完成。`src/pi_sonar_agent/` 已成为正式包入口，顶层 `__init__.py` 和子包 `__init__.py` 已提供稳定导出；`__path__` 注入兼容方案已删除
- `4.2 清理入口 hack`：已完成。`run.py` 已移除开发机私有 SDK fallback，仅保留本仓库 `src/` bootstrap 和标准 `python -m pip install -e .` 提示
- `4.3 删除旧实现`：已完成。legacy 包 `__init__.py` 已改为相对导出，消除了移除 `__path__` 后的循环导入；`ClaudeFixAgent` 中的 issue prompt、legacy scope guard、build/rule verification 已继续下沉到 `issue_prompt.py / scope_guard.py / fix_verifier.py`。保留的 `GitClient / workspace` 兼容 facade 现仅作为公共兼容 API，不再视为主链路待删除旧实现
- `4.4 补齐质量门槛`：已完成。全仓 `ruff check .` 已通过，仓库已新增 GitHub Actions CI，关键验收场景已由现有回归测试和新增质量门槛测试共同覆盖
- `4.5 继续收口边界检查职责`：已完成。边界检查已下沉为 `BoundaryRuntime + BoundaryPolicy`，不再由 `ClaudeFixAgent` 或 verifier 各自猜测 touched-region；质量门禁现与 reviewer/scope 共用统一 patch 事实，并已明确切换为 `scope/reviewer -> before-lines`、`quality gate -> after-lines`
- `补充说明`：`GitClient`、`workspace` 等公共兼容 facade 仍保留，但主运行链路已不再依赖这层兼容接口；它们现作为公共兼容 API 保留，不再阻塞本轮重构收口

### 主要任务

#### 4.1 标准化包结构

- 将新实现收敛到 `src/pi_sonar_agent/`
- 移除 `__path__` 注入的兼容方案

#### 4.2 清理入口 hack

- 移除开发机专用路径 fallback
- 保留一个正式 CLI 入口和一个开发入口

#### 4.3 删除旧实现

- 下线旧 clone helper
- 下线分散的 GitClient/Workspace clone 实现
- 删除未正式接线的分支
- 删除已被替换的旧 scope 逻辑

#### 4.4 补齐质量门槛

- 修复 `ruff` 问题
- 接入 `pytest`
- 增加关键集成测试

关键集成测试至少包括：

- 非 `develop` 分支 clone
- Git 认证失败与脱敏日志
- issue 超时回滚
- 单目标与批量入口行为一致
- `scope` 与 `contract_review` 对照
- DB 降级运行

#### 4.5 继续收口边界检查职责

- 用 `BoundaryRuntime` 编排 scope review 与 diff review
- 用 `BoundaryPolicy` 统一行窗允许策略
- 用 `AttemptFileChangeBuilder` 统一 per-attempt diff、touched lines 和 baseline 对比
- 让 `AttemptFileChangeBuilder` 同时输出 before/after touched lines 与 edit operations，供不同 verifier 使用各自正确的坐标面
- 保证 `QualityGateVerifier` 只审真实 touched region，不再把历史整文件问题误判为当前 patch 失败

### 交付物

- 标准 `src/pi_sonar_agent/` 包结构
- 新 CLI 入口
- 清理后的旧代码
- CI 脚本和关键集成测试

### 验收标准

- `pip install -e .` 后 CLI 可直接运行
- 无需路径 hack
- `ruff`、`pytest`、关键集成测试全部通过

## 8.5 后续补强：规范遵守闭环

### 目标

在现有 `CLAUDE.md + csharp-quality-gate.md + EditContract + QualityGateVerifier + RetryContext` 的基础上，继续提升“严格遵守规范”的稳定性，但避免回退到“只靠 prompt”的旧模式。

这部分不是重做已有 Phase，而是在当前重构完成后继续增强规范治理闭环。

### 当前执行状态（2026-04-09）

- `8.5.1 精准规范加载`：已完成。prompt 已改为优先注入“本次启用的 quality gate 规则摘要”，不再默认依赖整篇 `csharp-quality-gate.md` 全文；`prompt_context.json` 也已落盘 `active_quality_gate_rules`
- `8.5.2 合规审计工件`：已完成。已新增 `compliance_summary` 生成逻辑，并接入 `attempt artifact / issue artifact / build_result.json / PR 详细说明`；`attempt-xx/compliance_summary.json` 与 issue 根目录 `compliance_summary.json` 均已稳定落盘
- `8.5.3 结构化 lessons memory`：已完成。已新增 `lessons_store.py`，将 repeated failure pattern 收口为 `quality_gate_lessons.jsonl / boundary_failure_patterns.json / rule_failure_patterns.json`；`IssuePlanner` 现可按 `rule_id / failure_kind / scope_mode / guardrail_mode` 读取 lessons，并自动补充到 strategy / review_hints / prompt guidance

### 需要补强的方向

#### 8.5.1 精准规范加载

当前问题：

- prompt 里仍可能注入整段质量门禁正文
- 运行时虽然已经知道 `EditContract.quality_gate_rules`，但 prompt 消费层还没有完全切到“只注入本次适用规则”

补强目标：

- 默认只向模型注入本次 issue 真正适用的 quality gate 条目
- 整篇 `data/csharp-quality-gate.md` 作为规则真源和审计材料保留，但不再默认全文塞进每次 prompt
- 对于 `rule_id / file_path / scope_mode / retry_context` 可明显缩窄的场景，只传本次适用规则、规则摘要和 `prompt_hint`

建议落点：

- `resource_loader.py`
- `issue_planner.py`
- `issue_prompt.py`

验收标准：

- 模型 prompt 中的规范信息以“本次适用规则清单”为主，不再依赖整篇 markdown 全文注入
- `prompt_context.json` 中能看到本次实际启用的质量门禁规则列表

#### 8.5.2 合规审计工件

当前问题：

- 质量门禁结果已经结构化，但还缺一个明确的“本次规范遵守摘要”
- PR 报告与工件里还没有稳定的合规总结对象

补强目标：

- 为每个 attempt 和最终 issue 生成 `compliance_summary.json`
- 显式记录：
  - 本次启用的 hard/soft 规则
  - hard pass/fail
  - soft findings
  - waived / not_applicable / skipped checks
- 将该摘要接入 PR 详细报告，而不是依赖模型自行输出“我遵守了哪些规范”

建议落点：

- `quality_gate.py`
- `quality_gate_verifier.py`
- `artifact_writer.py`
- `pr_description.py`

验收标准：

- 每个 attempt artifact 中都能看到结构化 `compliance_summary`
- PR 详细说明中能稳定展示“启用规则 / 通过项 / 失败项 / 软提示”

#### 8.5.3 结构化 lessons memory

当前问题：

- 失败模式已经能通过 `RetryContext` 回传，但还没有沉淀为长期经验
- 不同 run 中重复出现的 reviewer / scope / quality gate 失败，尚未形成结构化经验记忆

补强目标：

- 不使用自由文本 `tasks/lessons.md` 作为主方案
- 将 repeated failure pattern 沉淀为结构化 lessons memory，例如：
  - `quality_gate_lessons.jsonl`
  - `rule_failure_patterns.json`
  - `boundary_failure_patterns.json`
- `IssuePlanner` 可以按 `rule_id / failure_kind / scope_mode` 读取最近高频失败模式，自动补充到 strategy / review_hints / retry guidance

建议落点：

- `retry_context.py`
- `issue_retry.py`
- `issue_planner.py`
- `state_store.py` 或新的 `lessons_store.py`

验收标准：

- 同一类失败重复出现时，planner 能拿到结构化 lessons，而不是只依赖上一轮 raw retry output
- lessons memory 可审计、可清理、可按规则聚合

### 对 Plan → Edit → Verify 方案的吸收原则

这套思路总体值得吸收，但不能原样照搬，建议按下面方式并入项目：

#### Plan

值得补充：

- planner 输出里显式增加“本次改动如何满足当前启用的 quality gate / contract / boundary policy”
- 将“本次适用规范”写成结构化对象，而不是只在 prompt 里自然语言描述

不建议照搬：

- 不建议再单独引入 `CODE_NORMS.md`
- 本项目继续以 `CLAUDE.md` 作为长期工作规则、`data/csharp-quality-gate.md` 作为代码规范真源

#### Edit

值得补充：

- 继续强化 patch-only / Search-Replace / MultiEdit 优先
- 对“精确 old_string -> new_string”可以作为优先策略，但应理解为“优先做确定性 patch”，不是强制所有修复都必须依赖单一唯一 old_string

不建议照搬：

- 不应把“old_string 必须全文件唯一”设为通用硬门禁
- 某些多行重写、helper 提取、注释补全和邻接清理场景仍需要结构化 patch，而不是单一字符串替换

#### Verify

值得补充：

- 保持现有 `build + boundary review + quality gate + rule validator` 闭环
- 在其上增加 `compliance_summary`，让验证结果更可审计

不建议照搬：

- 不建议每次 attempt 都强制“重新跑一次 Sonar 全量扫描”
- Sonar re-scan 更适合作为 target 级或最终验收级步骤，而不是每次 issue attempt 的必经步骤，否则成本和时延过高

#### Self-improvement loop

值得补充：

- 保留“从纠正中学习”的思路
- 但要以结构化 lessons memory 为主，而不是持续膨胀的自由文本 `tasks/lessons.md`

### 推荐实施顺序

1. 先做 `8.5.1 精准规范加载`
2. 再做 `8.5.2 合规审计工件`
3. 最后做 `8.5.3 结构化 lessons memory`

不要反过来做。先让“规范如何进入本次任务”和“结果如何被稳定审计”变清楚，再引入长期经验沉淀。

## 8.6 后续补强：全局边界框架与首批规则验证

### 目标

参考 `pi-mono` 与 Claude Code 的架构思想，把当前边界治理继续升级成“全局框架 + 规则能力映射”的形态，而不是继续走“两种极端”：

- 不是只用一个全局 statement/window 规则硬卡所有 issue
- 也不是继续为每条规则零散打补丁

这一部分与 `8.5 规范遵守闭环` 是互补关系，不是重复建设：

- `8.5` 解决的是“模型如何按规范修、如何被验证、如何被审计”
- `8.6` 解决的是“运行时如何为不同修复形状生成正确边界，不把合法修复误判为越界”

对应到系统层次：

- `8.5` 的核心是 `quality gate`
- `8.6` 的核心是 `task / contract / boundary runtime / capability model`

### 当前执行状态（2026-04-08）

- `8.6`：已完成
- `8.6.1 建立全局 Boundary Capability Model`：已完成。已新增 `boundary_capabilities.py`，并将 capability/profile 解析接入 `rule_policies.py / issue_contract.py / boundary_policy.py`
- `8.6.2 contract 升级为 symbol + capability + 多范围`：已完成。`EditContract` 已新增 `boundary_profile / allowed_capabilities / allowed_related_symbols`，`IssuePlanner` 已为 `S1481 / S125 / S1144` 生成多范围与相关 symbol 合同
- `8.6.3 BoundaryRuntime 成为唯一边界判定入口`：已完成。`BoundaryRuntime` 已统一输出主次边界失败原因，`FixVerifier / RetryContext / issue_retry / artifact_writer` 已贯通边界失败码
- `8.6.4 规则通过 profile 映射到 capability`：已完成。`rule_policies.py` 已将 `S1481 / S125 / S1144` 收口为 `boundary_profile + capability` 映射，而不是散乱特判
- `8.6.5 lessons 驱动 contract 调整`：已完成。`lessons_store.py` 已沉淀 `boundary_failure_code`，`IssuePlanner` 现可基于历史边界失败自动补 `allowed_line_ranges / allowed_related_symbols / review_hints`
- `8.6.6 用首批真实规则做回归验证`：已完成。已新增 `tests/test_boundary_regressions.py`，并补强 `test_boundary_runtime.py / test_fix_verifier.py / test_issue_planner.py / test_lessons_store.py`
- `8.6.7 Filesystem Hard Boundary + Soft Drift Audit`：已完成。边界已从 `line-window / scope hard fail` 迁移为“目录/文件系统硬边界 + 修复偏离软审计”；同文件额外修改、额外 touched files 不再作为 hard fail，而是作为 drift audit 写入 `reviewer_result` 与 PR 详细报告附件
- `日志驱动补充整改（2026-04-09 13:07 run）`：已完成。针对 `batch_20260409130720.log` 中 `S1144` 的 `method_cluster_not_declared`，`IssuePlanner` 已修正相邻 private member cluster 的扫描与合同生成逻辑，不再因为重复使用原始 `scope_end_line` 而漏掉后续 helper 范围；`BoundaryRuntime` 也已支持在“同文件 + capability 已声明”的前提下，对 `member_cluster / adjacent cleanup / declaration anchor` 做运行时合同放宽，避免合法的同文件局部扩展继续被静态行窗误拒
- `验证结果`：`.venv` 下 `pytest -q` 通过，`174 passed`；`ruff check .` 通过；`compileall src tests` 通过
- `日志驱动补充整改（2026-04-08 18:21 run）`：已完成。针对 `batch_20260408182111.log` 中 `S1481` 的假性 scope reject，legacy `scope` 校验已改为优先消费 `EditContract.allowed_line_ranges`，不再只看原始 `validation_line_range`；这使 `declaration_anchor / adjacent_cleanup` 这类多范围合同在 `guardrail_mode=scope` 下也能与新 contract 保持一致

### 设计原则

#### 8.6.0 为什么不能只做“重点规则补洞”

`batch_20260408153551.log` 暴露出的 `S1481 / S125 / S1144` 失败很典型，但它们应该被视为**首批验证样本**，不是整个方案的边界本身。

原因是：

- `S1481` 代表 `declaration_delete`
- `S125` 代表 `comment_cleanup + adjacent_cleanup`
- `S1144` 代表 `member_delete / method_cluster_delete`

这三条规则的价值在于：它们分别覆盖了三种不同的合法修复拓扑，适合用来验证全局边界框架是否成立。

所以 `8.6` 的目标不是“只修三条规则”，而是：

- 先建立全局 boundary capability model
- 再用这三条规则做第一批验证样本

这也更符合：

- `pi-mono` 的 runtime/hook/policy 思路
- Claude Code 分析文档里“任务是一等对象、边界是运行时决策、恢复性来自显式状态”的原则

### 需要补强的方向

#### 8.6.1 建立全局 Boundary Capability Model

状态：已完成

已实现：

- 新增 `src/core/boundary_capabilities.py`，定义 `statement_edit / declaration_delete / adjacent_cleanup / method_cluster_delete` 等统一 capability
- `RuleHandlingPolicy` 已支持 `boundary_profile + boundary_capabilities`
- `BoundaryPolicy` 已统一从 contract 中解析 capability，不再只依赖默认 `scope_mode`

当前问题：

- 当前边界模型仍偏向“行窗 + scope mode”
- 规则差异主要靠零散 `prompt_guards / review_hints / trailing_lines`
- 这不足以表达不同修复形状

补强目标：

- 引入一组全局的 `boundary capabilities`
- 让规则不再直接操作原始行窗，而是声明自己需要哪种编辑能力

第一批建议能力：

- `statement_edit`
- `declaration_delete`
- `adjacent_cleanup`
- `method_rewrite`
- `member_delete`
- `method_cluster_delete`
- `helper_extract`
- `signature_change`
- `new_type_add`
- `multi_file_refactor`

建议落点：

- `rule_policies.py`
- `issue_contract.py`
- `boundary_policy.py`

验收标准：

- 规则边界不再主要依赖“默认 statement + trailing lines”
- 运行时可以回答“当前规则属于哪种编辑能力”

#### 8.6.2 将 contract 从“行窗”升级为“symbol + capability + 多范围”

状态：已完成

已实现：

- `EditContract` 已新增 `allowed_related_symbols / boundary_profile / allowed_capabilities`
- `IssuePlanner` 已能为 `S1481 / S125 / S1144` 生成相关 symbol 与非连续范围
- 已补“statement 范围去空白前后缀”与“邻接声明锚点”逻辑，避免 sparse / delete 场景把相关范围放大成假窗口
- 已补 `member_cluster` 连续相邻 private member 扫描逻辑，planner 现在会持续向后扩展 cluster，而不是在首个 helper 后停止

当前问题：

- 当前 `EditContract.allowed_line_ranges` 仍主要是一个 validation window
- `target_symbols` 虽然已经存在，但还没有真正成为边界判定主轴

补强目标：

- 让 `EditContract` 成为真正的任务对象，显式表达：
  - `target_symbols`
  - `allowed_capabilities`
  - `allowed_line_ranges`
  - `allowed_related_symbols`
  - `boundary_profile`

建议策略：

- contract 默认以 symbol 为主，line range 为辅
- 支持非连续范围与相关符号声明，例如：
  - `S125`: `comment_line_range + adjacent_cleanup_range`
  - `S1144`: `method_range + dependent_helper_range`
  - `S1481`: `issue_line + declaration_anchor_range`

建议落点：

- `issue_contract.py`
- `issue_planner.py`
- `boundary_policy.py`

验收标准：

- contract 能清楚表达“本次允许改哪些符号 / 范围 / 能力、为什么允许”
- reviewer 输出可以解释命中的具体 symbol/range/capability，而不只是一个总窗口

#### 8.6.3 让 BoundaryRuntime 成为唯一边界判定入口

状态：已完成

已实现：

- `BoundaryRuntime` 已统一消费 `patch facts + contract + boundary profile`
- runtime 现可输出 `scope_symbol_anchor_miss / adjacent_cleanup_not_declared / method_cluster_not_declared / scope_line_window_reject` 等细分失败码
- `FixVerifier / ClaudeFixAgent / RetryContext / issue_retry / build_result.json / issue_summary` 已贯通 `boundary_failure_code` 与 `secondary_boundary_failure_codes`
- legacy `scope` 校验现已优先使用 `BoundaryPolicy.contract_line_ranges(edit_contract)`，`declaration_anchor / adjacent_cleanup` 等多范围合同不会再因为旧 `validation_line_range` 过窄而被假性拒绝
- 在“同文件 + 目标文件未越界 + capability 已声明”的前提下，`BoundaryRuntime` 现可进行运行时合同放宽，再次复核 reviewer/scope，避免 declaration anchor、adjacent cleanup、member cluster 这类合法局部扩展继续被静态窗口拒绝

当前问题：

- 虽然 `BoundaryRuntime` 已存在，但规则级边界能力还没有完全在这里收口
- 失败结果仍容易表现为 `build`、`scope` 混杂，真实主阻塞原因不够显式

补强目标：

- 继续把边界判定从分散逻辑收口到 runtime/policy
- 让 runtime 统一消费：
  - patch facts
  - contract
  - boundary profile
  - lessons

建议策略：

- runtime 输出更细的原因，例如：
  - `scope_line_window_reject`
  - `scope_symbol_anchor_miss`
  - `adjacent_cleanup_not_declared`
  - `member_cluster_not_declared`
  - `capability_not_allowed`
- 当 build 与 scope 同时出现时，显式记录主次原因，避免日志只剩 build 噪音

建议落点：

- `boundary_runtime.py`
- `fix_verifier.py`
- `retry_context.py`
- `issue_retry.py`

验收标准：

- 每个失败 issue 都能明确回答“模型不会修”还是“边界不允许修”
- `issue_summary.json` 中能稳定反映真实主阻塞原因

#### 8.6.4 规则通过 profile 映射到 capability，而不是手写散乱特判

状态：已完成

已实现：

- `S1481 -> declaration_anchor + declaration_delete`
- `S125 -> comment_adjacent_cleanup + statement_edit + adjacent_cleanup`
- `S1144 -> member_cluster + member_delete + method_cluster_delete`
- 首批规则已经按“profile -> capability”落地，后续规则可沿同一模型继续扩展

当前问题：

- 如果继续只为 `S1481 / S125 / S1144` 写散乱特判，后续会再次回到“规则越修越碎”的状态

补强目标：

- 将规则实现收口为“profile -> capability”映射
- 首批规则只是验证样本，不是专案式一次性补丁

首批映射建议：

- `S1481 -> declaration_delete`
- `S125 -> statement_edit + adjacent_cleanup`
- `S1144 -> member_delete + method_cluster_delete`

后续规则可继续按同一模型接入，而不是再发明新的一套 scope 逻辑。

建议落点：

- `rule_policies.py`
- `issue_planner.py`
- `boundary_policy.py`

验收标准：

- 首批规则的实现形式是“映射到 capability”，不是独立散乱 patch
- 后续规则可以沿同一模型扩展

#### 8.6.5 把 lessons 真正用于 contract 生成，而不是只做 guidance

状态：已完成

已实现：

- `LessonsStore` 已持久化 `boundary_failure_code`
- planner 读取 lessons 时已可按 `rule_id / failure_kind / scope_mode / guardrail_mode / boundary_failure_code` 过滤
- 当命中 `scope_symbol_anchor_miss / adjacent_cleanup_not_declared` 等高频模式时，`IssuePlanner` 会自动补 `allowed_line_ranges` 与 `allowed_related_symbols`

当前问题：

- `lessons_store.py` 已存在
- 但目前 lessons 更多用于 planner guidance，还没有充分改变 contract 本身

补强目标：

- 当某条规则反复因相同边界原因失败时，planner 可以主动调整 contract 生成策略

建议策略：

- 对高频模式建立结构化 lessons：
  - `S1481 + scope_symbol_anchor_miss`
  - `S125 + adjacent_cleanup_not_declared`
  - `S1144 + method_cluster_not_declared`
- planner 读取 lessons 后，可自动：
  - 放宽到更合适的 `allowed_line_ranges`
  - 增加 `allowed_capabilities`
  - 增加 `allowed_related_symbols`
  - 补充针对性的 review hints

建议落点：

- `lessons_store.py`
- `issue_planner.py`
- `retry_context.py`

验收标准：

- 同一规则、同一失败模式连续出现时，后续 attempt 的 contract 会显式体现 lessons 的影响

#### 8.6.6 用首批真实规则做回归验证

状态：已完成

已实现：

- 新增 `tests/test_boundary_regressions.py`，覆盖 `batch_20260408153551.log` 对应的 `S1481 / S125` 和同类 `S1144` contract 生成回归
- `tests/test_boundary_runtime.py` 已覆盖 declaration-anchor 边界失败分类
- `tests/test_fix_verifier.py` 已覆盖边界失败码向 verifier 结果传递
- `tests/test_lessons_store.py / tests/test_issue_planner.py` 已覆盖 lessons 反哺 contract 的回归

当前问题：

- 当前很多回归还是 unit test 层
- 但这类问题更适合用真实日志/真实 patch 做 regression fixtures

补强目标：

- 让首批验证样本成为长期回归资产

第一批样本：

- `batch_20260408153551.log` 中的 `S1481`
- `batch_20260408153551.log` 中的 `S125`
- 同类 `S1144` private method deletion 场景

验证点包括：

- contract 是否合理生成
- capability 是否正确映射
- reviewer 是否接受
- scope 是否不再误拒
- build/quality gate 是否不被边界误伤

建议落点：

- `tests/test_boundary_runtime.py`
- `tests/test_fix_verifier.py`
- 新增 capability/regression fixtures

验收标准：

- 这批真实失败样本在回归测试中可重放
- 修复后不会因为重构再次退回旧的 scope reject

#### 8.6.7 将 line-window hard fail 迁移为 filesystem hard boundary + soft drift audit

状态：已完成

已实现：

- `DiffReviewer` 现在只对真正的文件系统高风险行为做 hard fail：
  - 触达受保护路径
  - 新建文件
  - 删除文件
- 同文件额外修改、主区域外 hunk、额外 touched files 不再触发 `scope / reviewer` 硬失败，而是记为 soft drift finding
- `BoundaryRuntime / FixVerifier / ClaudeFixAgent` 已切到“只有 filesystem boundary 才阻断 attempt”，不再因为 `scope_line_window_reject / declaration_anchor / member_cluster` 这类边界问题直接判失败
- `reviewer_result.metrics` 已新增：
  - `drift_score`
  - `soft_boundary_violation_count`
  - `extra_touched_file_count`
  - `outside_primary_region_line_count`
- PR 详细报告附件现在会展示：
  - `边界审计`
  - `drift score`
  - `漂移记录`

当前问题：

- 旧边界模型里，“最小化修复”主要靠 line-window/scope 做 hard enforcement，副作用已经大于收益
- 很多合法修复会因为：
  - 相邻清理
  - helper 提取
  - 同文件额外 hunk
  被判成 `scope error`

迁移后的原则：

- 硬边界只管安全：
  - 只允许工作区内已有源文件
  - 禁止新建、删除、重命名、整文件覆盖
  - 禁止受保护目录
- 软边界只管偏离：
  - 同文件额外修改
  - 主区域外 hunk
  - 额外 touched files
  这些只审计、不直接判死

验收标准：

- `scope` 不再成为当前主链路里的常见最终失败原因
- 修复偏离信息会稳定写入：
  - `reviewer_result.json`
  - `issue_summary/compliance_summary` 相关工件
  - PR 详细报告附件
- 文件系统高风险行为仍然会被阻断

### 推荐实施顺序

1. 先做 `8.6.1 建立全局 Boundary Capability Model`
2. 再做 `8.6.2 contract 升级为 symbol + capability + 多范围`
3. 接着做 `8.6.3 BoundaryRuntime 成为唯一边界判定入口`
4. 然后做 `8.6.4 规则通过 profile 映射到 capability`
5. 再做 `8.6.5 lessons 驱动 contract 调整`
6. 最后做 `8.6.6 用首批真实规则做回归验证`
7. 在规则能力稳定后，将 `line-window hard fail` 迁移为 `filesystem hard boundary + soft drift audit`

不要反过来做。先建立全局边界框架，再让 `S1481 / S125 / S1144` 作为首批验证样本落地，而不是把它们当成整个方案本身。

## 8.7 后续补强：性能优化专项方案

### 目标

针对 `batch_20260408182111.log` 暴露出的“单 issue 耗时过长、模型后续响应超时、同一小问题因多轮 retry 被放大”的现象，补一轮专门的性能优化。

这一轮优化的目标不是单纯“更快”，而是：

- 在不降低修复能力的前提下减少平均单题耗时
- 在不降低成功率的前提下降低超时和无效重试
- 在不降低修复质量的前提下减少模型啰嗦交互、重复读取和不必要的重验证

### 硬约束

本专项必须遵守以下硬约束：

- 不降低最终修复成功率
- 不降低最终 patch 质量
- 不放松最终质量门禁、边界门禁和 build gate 的最终验收要求
- 不把“减少耗时”建立在“减少必要验证”或“缩小模型能力”之上
- 所有性能优化都必须支持 feature flag 或可回滚开关
- 每项优化都必须配套回归测试和至少一轮真实 batch 指标对比

换句话说：这是一次“无损优化”，不是用速度换质量。

### 当前问题判断

从 `batch_20260408182111.log` 来看，当前慢主要由四类因素叠加：

1. 模型后续响应偏慢，`follow_up_response_timeout` 多次触发
2. 当前 Agent 在小问题上仍有较多 `Read / Edit / 再 Read / 再总结` 往返
3. 每个成功候选 patch 后都会触发全量 `dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"`，单次 build 成本高
4. retry 会把上述成本乘上去，尤其是假性 scope reject 或重复 timeout 会显著拉长尾耗时

因此本专项不是“只换更快模型”，而是“模型交互瘦身 + 验证分层 + retry 降噪 + 指标化 rollout”。

结合 `batch_20260408184952.log` 的进一步观察，还需要补充两个更具体的问题：

5. 存在“工具返回后模型不再继续响应”的中途挂起，典型表现为停在 `tool:Read / tool:Edit / sdk_message:UserMessage` 之后，最终触发 `follow_up_response_timeout`
6. 存在“patch 已形成，但模型仍继续长篇解释/总结”的后处理拖延，导致本可进入 verifier 的 attempt 最终被 timeout 判废

因此 `8.7` 不只是“让整体更快”，还要专门解决：

- tool-after-response stall
- post-edit narration overrun
- patch 已有效但未被及时 salvage 的问题

### 需要补强的方向

#### 8.7.1 建立性能基线与验收指标

状态：已完成

目标：

- 先把性能问题量化，再做优化
- 明确区分：模型等待时间、工具往返时间、build 时间、retry 放大时间

建议指标：

- `attempt_total_duration_seconds`
- `time_to_first_model_content_seconds`
- `time_after_first_edit_to_finalize_seconds`
- `tool_call_count`
- `read_call_count`
- `edit_call_count`
- `build_duration_seconds`
- `retry_count_per_issue`
- `model_timeout_rate`
- `scope_reject_rate`

建议落点：

- `events.py`
- `artifact_writer.py`
- `run_summary / target_summary / attempt_summary`

本轮实现：

- `AgentRuntimeResult` 已补齐 `total_duration_seconds / time_to_first_model_content_seconds / time_after_first_edit_to_finalize_seconds / tool_call_count / read_call_count / edit_call_count / assistant_text_events / assistant_text_chars / timeout_stage / last_progress_stage / saw_result_event`
- `FixResult`、`AttemptState`、`IssueState`、`TargetState`、`RunState` 已贯通 `performance_metrics / performance_summary / rollout_flags`
- 新增 `summarize_issue_performance / summarize_target_performance / summarize_run_performance`
- `build_result.json / prompt_context.json / run_summary / target_summary / attempt_summary` 已能落盘性能基线与 rollout flags

验收标准：

- 能从 artifact 或 run summary 直接回答“慢在模型、慢在 build，还是慢在 retry”

#### 8.7.2 模型交互瘦身，但不削弱修复能力

状态：已完成

目标：

- 让模型把 token 和时间花在“读必要上下文并改代码”上，而不是花在长篇自然语言说明和重复读文件上

建议策略：

- 对小规则启用“short-form execution prompt”，要求：
  - 少解释
  - 少总结
  - 优先直接读必要片段并下 patch
- 增加 `post-edit narration cutoff`：
  - 一旦 patch 已完成，优先进入 `Finish` 或最短确认
  - 避免在修复完成后继续输出长篇“修复总结 / 背景分析 / 逐条解释”
- 对同一文件/同一区域的重复 `Read` 做去重缓存
- 对同一 attempt 中无效的连续 `Edit -> Read -> Edit -> Read` 循环增加 runtime 侧检测
- 对 `tool -> assistant_text -> tool -> assistant_text` 的冗长往返增加短路约束，尤其是小规则与 fast path 场景
- planner 输出继续保留，但更多转成结构化 guidance，而不是让模型在聊天文本里重复解释一遍

注意：

- 不是压缩上下文到让模型看不懂
- 不是禁止分析
- 而是去掉“对修复没有增益的啰嗦说明”

建议落点：

- `issue_prompt.py`
- `agent_runtime.py`
- `registry.py / policy.py`
- `resource_loader.py`

本轮实现：

- `IssuePlanner` 已为低风险规则生成 `execution_profile=fast_path_short_form`
- `IssuePromptBuilder` 已增加 `execution_mode_section`，将 short-form fast path 指令显式注入 prompt
- `ClaudeFixAgent` 已在 fast path 场景下收紧 `max_turns`
- `AgentRuntime` 已显式记录 `assistant_text_events / assistant_text_chars / last_progress_stage`，为后续继续压缩 narration 提供基线
- `prompt_context.json` 已落盘 `execution_profile / fast_path_enabled / rollout_flags`

验收标准：

- 小问题的 `Read/Edit` 往返次数明显下降
- 平均 prompt 体积和 assistant narration 长度下降
- `follow_up_response_timeout` 中由“长篇修后总结”触发的比例下降
- 修复成功率不下降

#### 8.7.3 建立低风险规则 fast path

状态：已完成

目标：

- 对明显属于“局部、低风险、单文件”的规则走更轻执行路径
- 避免所有问题都走同样重的完整交互流程

第一批候选：

- `S1481`
- `S125`
- `S1144`
- 其他可以稳定映射到 `declaration_delete / adjacent_cleanup / member_delete` 的规则

建议策略：

- fast path 只减少模型交互和上下文加载，不减少最终验证
- 一旦触发以下任一条件，立即回退到 full path：
  - 跨文件
  - 需要 helper extract
  - 需要 signature change
  - 出现 boundary ambiguity
  - 第一次 attempt 未成功

建议落点：

- `issue_planner.py`
- `rule_policies.py`
- `agent_runtime.py`

本轮实现：

- 已建立首批 fast path 规则集合：`S1481 / S125 / S1144`
- `IssuePlanner._should_enable_fast_path()` 已根据 `retry_context / allowed_capabilities / related_symbols` 决定是否启用 fast path
- 一旦进入 retry、需要 `signature_change / helper_extract / new_type_add / multi_file_refactor`，会自动回退到 full path
- `EditContract` 已补齐 `execution_profile / fast_path_enabled / rollout_flags`

验收标准：

- fast path 命中的 issue 平均耗时下降
- fast path 回退机制明确
- fast path 问题的成功率与 full path 持平或更高

#### 8.7.4 验证分层，但保持最终 gate 不降级

状态：已完成

目标：

- 减少每轮 attempt 都去做最重验证的浪费
- 但最终成功判定和提 PR 前，仍保留现有严格门禁

建议策略：

- 保留 `boundary review / quality gate / full build` 的最终严格要求
- 引入“分层验证顺序”：
  1. 先跑最便宜的 boundary / reviewer / quality gate
  2. 只有当前 patch 成为有效候选时，再跑全量 build
- 对“无变更 / 明显 timeout / forbidden tool / 明显 boundary reject”的 attempt，避免进入昂贵 build
- 对 target 级最终成功结果，保留一次完整全量 build 作为最终验收

注意：

- 这不是取消 full build
- 而是避免在明显无效的 attempt 上浪费 full build

建议落点：

- `fix_verifier.py`
- `run_coordinator.py`
- `build_gate.py`

本轮实现：

- `FixVerifier` 已改为 `BoundaryRuntime -> QualityGateVerifier -> Rule Validator -> Build` 的分层顺序
- 对 `scope / reviewer / quality_gate / rule_validation` 已明确失败的 attempt，会跳过昂贵 build
- build 执行事实已通过 `build_invoked / build_duration_seconds` 进入性能指标链
- 最终成功结果仍保留现有严格 gate，不降低质量门禁与边界门禁

验收标准：

- 无效 attempt 的 build 次数下降
- 最终成功 issue 仍保留严格 full build 验收
- build 相关成功率和质量不下降

#### 8.7.5 Retry 降噪与超时恢复优化

状态：已完成

目标：

- 减少“同一错误反复重试”
- 尽早识别“这次 retry 不会比上次更好”的场景

建议策略：

- 对重复 patch / 重复 boundary failure / 重复 timeout 做更强的短路
- 对 `model_timeout` 区分：
  - 首响应超时
  - follow-up 超时
  - 工具后无响应超时
- 对 `follow_up_response_timeout` 再细分：
  - `post_read_stall`
  - `post_edit_stall`
  - `post_summary_stall`
- 如果上一次已经产出有效 patch，但卡在后续解释或总结阶段，优先把 patch 进入验证，而不是整轮判废
- 引入 `patch salvage`：
  - 若 attempt 内已产生有效 diff，且边界/文件变更可解析，则优先进入 verifier
  - 不要求模型必须再补一段自然语言“我修好了”的结束语
- 对“工具后挂起但 diff 未变化”的场景，直接终止当前 attempt，避免空转到 180 秒
- 对同一 issue 的重复失败模式，直接使用 lessons 调整 contract 或切换 fast/full path

建议落点：

- `issue_retry.py`
- `retry_context.py`
- `lessons_store.py`
- `agent_runtime.py`

本轮实现：

- `AgentRuntime` 已将 follow-up timeout 细分为：`post_read_stall / post_edit_stall / post_summary_stall / post_text_stall`
- `ClaudeFixAgent` 已支持 `patch salvage`：在 timeout 但 patch 已落盘时进入 verifier，而不是整轮直接判废
- `ClaudeFixAgent` 已补充同上下文 continuation retry：对 `follow_up_response_timeout` 且未形成有效 patch 的场景，会基于最近 runtime events、工具摘要和工作区现状构造 resume prompt，在同一 issue attempt 内最多续跑 2 次
- `RetryContext` 已补齐 `model_timeout_stage / patch_salvaged`
- `issue_retry` 已将 timeout 分类与 salvage 信息写入 retry feedback、attempt artifact 和 issue state
- `lessons` 链路已可继续利用这些结构化失败原因调整后续 contract

验收标准：

- 同一 issue 的平均 attempt 数下降
- `model_timeout` 导致的无效重试下降
- `follow_up_response_timeout` 中“已形成有效 patch 但最终判废”的比例下降
- 真实成功率不下降

#### 8.7.6 通过指标对比渐进 rollout

状态：已完成

目标：

- 不凭感觉判断“变快了”
- 用真实 batch 对比验证“快了但没有变差”

建议策略：

- 每项优化都挂 feature flag
- 至少记录以下对比：
  - 平均单 issue 耗时
  - P95 单 issue 耗时
  - 平均 attempt 数
  - model timeout rate
  - scope reject rate
  - 最终修复成功率
  - 最终 PR 成功率
- 先灰度到低风险规则，再逐步放开

建议落点：

- `RunCoordinator`
- `run_summary.json`
- `target_summary.json`
- `state_store.py`

本轮实现：

- 新增 `perf_flags.py`，所有 8.7 优化均已挂到 feature flag：
  - `PI_SONAR_PERF_SHORT_FORM_PROMPT`
  - `PI_SONAR_PERF_FAST_PATH`
  - `PI_SONAR_PERF_LAYERED_VERIFICATION`
  - `PI_SONAR_PERF_PATCH_SALVAGE`
  - `PI_SONAR_PERF_CONTINUATION_RETRY`
  - `PI_SONAR_PERF_FAST_PATH_MAX_TURNS`
  - `PI_SONAR_PERF_CONTINUATION_RETRY_LIMIT`
- `EditContract / FixResult / AttemptState / IssueState / TargetState / RunState` 已贯通 `rollout_flags`
- `run_summary / target_summary / prompt_context / build_result` 已能落盘 rollout flag 与性能汇总，支持前后批次指标对比
- 当前 rollout 默认安全开启，且所有优化都可通过环境变量快速回退

验收标准：

- 每项优化都能用一轮前后对比证明“更快且不更差”
- 若成功率或质量回落，可用 flag 快速回滚

#### 8.7.7 引入 Attempt Event Stream（参考 `pi-mono` EventStream 思想）

状态：已完成

目标：

- 把 attempt 运行过程里的关键阶段统一成一条事件流
- 不再让 `logger / retry / metrics / state store / lessons` 各自猜状态
- 为 `follow_up_response_timeout`、`patch salvage`、`boundary reject` 提供单一事实来源

为什么要做：

- 当前项目虽然已有 `events.py` 和结构化 state，但更偏“结果落盘事件”
- 对 attempt 内部的 `tool -> text -> timeout -> salvage -> verifier` 过程，还缺统一运行时事件流
- `pi-mono` 的 `EventStream` 设计值得借鉴的不是 TUI，而是“生产者与消费者解耦、事件渐进消费、零等待推送”

建议事件：

- `attempt_started`
- `tool_called`
- `tool_result_received`
- `assistant_text_delta`
- `patch_detected`
- `boundary_rejected`
- `quality_gate_rejected`
- `build_started`
- `build_finished`
- `timeout_classified`
- `patch_salvaged`
- `attempt_finished`

建议落点：

- `agent_runtime.py`
- `events.py`
- `issue_retry.py`
- `state_store.py`
- `artifact_writer.py`

本轮实现：

- `events.py` 已新增 `AttemptRuntimeEventKind / AttemptRuntimeEvent / AttemptEventStream`
- `AgentRuntimeResult` 已携带 `runtime_events`
- `AgentRuntime` 已发出 `attempt_runtime_started / tool_called / tool_result_received / assistant_text_delta / timeout_classified / attempt_runtime_finished`
- `ClaudeFixAgent` 已补齐 `patch_detected / patch_salvaged / build_started / build_finished / boundary_rejected / quality_gate_rejected / attempt_finished`
- `ArtifactWriter` 已稳定落盘 `attempt_events.jsonl`
- 运行日志现在会额外展示 `user message` 摘要、`ThinkingBlock`/SDK trace 预览，以及 `Read/Edit/Write/Bash` 等工具的输入摘要；`Read` 还会输出对应文件片段预览

验收标准：

- 同一 attempt 的 timeout、salvage、boundary、build 事实只在一处生成
- 日志、state、metrics、retry feedback 使用同一份事件事实
- 能从事件流重建单次 attempt 的关键路径

#### 8.7.8 引入 Attempt-Local Read Cache（参考 `pi-mono` 文件缓存思想）

状态：已完成

目标：

- 减少同一 attempt 内对同一文件、同一区域的重复 `Read`
- 降低小规则上的无效工具往返和 follow-up stall 概率

为什么要做：

- 当前 `8.7.2` 已做 short-form prompt，但 still 可能出现重复 `Read`
- `pi-mono` 性能分析里最适合本项目借鉴的部分之一，就是基于文件变化事实做局部缓存，而不是每次都重新读
- 对本项目来说，最值得缓存的不是完整消息序列化，而是“文件片段 / issue 行上下文 / related symbol 片段”

建议策略：

- 按 `path + requested_range + file_mtime_or_hash` 做 attempt-local cache
- 缓存：
  - issue line 上下文
  - declaration anchor 片段
  - adjacent cleanup 片段
  - member cluster 片段
- 文件一旦被 `Edit / MultiEdit / Write` 触达，立即失效相关缓存
- cache 只在单 attempt 生命周期内有效，不跨 attempt 复用

建议落点：

- `agent_runtime.py`
- `issue_prompt.py`
- `issue_planner.py`
- `attempt_changes.py`

本轮实现：

- 新增 `attempt_context.py`，提供 `AttemptContextCache`
- `ClaudeFixAgent.fix_issue()` 现在通过 cache 读取 issue 文件、编号窗口和后续验证前的最新文件内容
- 发生 patch 后会对目标文件做显式失效，避免 host 侧上下文读取陈旧内容
- 当前 cache 生命周期限定在单 attempt 内，不跨 attempt 复用

验收标准：

- 小规则 attempt 的 `read_call_count` 下降
- 同一文件/同一区域重复读取显著下降
- 修复成功率与 patch 质量不下降

#### 8.7.9 引入 Planner Prefetch / Context Packing（参考 `pi-mono` 批量读取与预取思想）

状态：已完成

目标：

- 在模型开始修复前，把当前 issue 必需的上下文一次性打包好
- 减少“模型先读一小段，再回头读相邻声明/方法/注释”的往返

为什么要做：

- `pi-mono` 性能分析里“批量读取/预加载”的思想，对本项目不应直接实现成通用 BatchRead tool
- 更适合变成 planner 侧的结构化预取：先由本地逻辑收集 boundary 相关片段，再一次性注入 prompt/context

建议预取内容：

- issue line 附近上下文
- `allowed_line_ranges` 对应片段
- `allowed_related_symbols` 对应片段
- declaration anchor / adjacent cleanup / member cluster 片段
- 当前 attempt 的 active quality gate 规则摘要

建议策略：

- fast path 默认启用 prefetch
- full path 只对 boundary profile 明确的规则启用 prefetch
- prompt 中优先注入结构化片段，减少模型自己多轮 `Read`

建议落点：

- `issue_planner.py`
- `issue_prompt.py`
- `resource_loader.py`
- `artifact_writer.py`

本轮实现：

- `EditContract` 已补齐 `prefetched_context`
- `IssuePlanner` 已根据 `allowed_line_ranges / allowed_related_symbols / issue_window` 生成预取片段
- `IssuePromptBuilder` 已新增 `【预取上下文】` 区块，把 boundary 相关片段一次性打包给模型
- `prompt_context.json` 已落盘 `prefetched_context`
- fast path 和 boundary-aware 规则现在会优先拿到 declaration anchor / adjacent cleanup / member cluster 等相关片段

验收标准：

- fast path 命中的问题 `tool_call_count` 和 `read_call_count` 下降
- `time_to_first_model_content_seconds` 与 `time_after_first_edit_to_finalize_seconds` 改善
- prompt 仍保持“足够理解，不盲改”

#### 8.7.10 显式调度 Attempt 内验证与工具阶段（参考 `pi-mono` 智能调度思想）

状态：已完成

目标：

- 把“先做什么、哪些可以跳过、何时进入昂贵 build”的判断升级成显式调度层
- 避免把调度逻辑继续散落在 `FixVerifier`、`ClaudeFixAgent`、`issue_retry` 的条件分支里

为什么要做：

- `pi-mono` 值得借鉴的不是“并行一切”，而是“调度是一等能力”
- 本项目现在已经有：
  - fast path / full path
  - layered verification
  - patch salvage
  - boundary runtime
- 下一步更适合把这些提升成统一的 `AttemptScheduler` / `VerificationScheduler` 策略层

建议职责：

- 决定当前 attempt 走 `fast_path_short_form` 还是 `full_path`
- 决定 verifier 顺序与 build 是否可跳过
- 决定 timeout 后是直接失败、salvage 还是快速终止
- 决定 lessons 是否触发 contract 扩展或路径切换

建议落点：

- `agent_runtime.py`
- `fix_verifier.py`
- `issue_retry.py`
- `perf_flags.py`

本轮实现：

- 新增 `attempt_scheduler.py`
- `AttemptScheduler.build_execution_schedule()` 已统一决定：
  - `execution_profile`
  - `effective_max_turns`
  - `enable_prefetch`
  - `enable_attempt_context_cache`
  - `patch_salvage_enabled`
- `AttemptScheduler.build_verification_schedule()` 已统一决定 layered verification 行为
- `ClaudeFixAgent` 已改为通过 scheduler 决定 fast path、salvage 资格和 execution metadata
- `FixVerifier` 已改为通过 scheduler 决定是否在 precheck 失败时跳过 build
- execution / verification schedule 已进入 metadata 与 performance metrics，便于审计和回滚

验收标准：

- 运行时调度规则可测试、可解释、可回滚
- 不再依赖多个模块各自猜测“下一步该做什么”
- 新增规则/优化时只需扩展 scheduler/policy，而不是复制条件分支

#### 8.7.11 同上下文 continuation retry（有限参考 Claude Code 的恢复式续跑）

状态：已完成

目标：

- 不把所有 `follow_up_response_timeout` 都直接判成整轮失败
- 在不复制 Claude Code 整套 transcript loop 的前提下，引入适合本项目的恢复式续跑
- 让“工具后卡住但尚未形成有效 patch”的 attempt 有一次到两次低成本补救机会

为什么要做：

- 当前项目的超时主形态不是“完全没首响应”，而是 `Read / Edit / assistant_text` 之后的 follow-up stall
- 这类场景如果直接整轮判废，会把小问题放大成多次 attempt、重复 build 和重复 prompt
- Claude Code 值得借鉴的不是照搬 Teleport/Session 全量机制，而是：
  - 保留最近运行事实
  - 清理坏尾巴
  - 用短续跑指令继续，而不是整轮重头开始

建议策略：

- 只对 `follow_up_response_timeout` 相关阶段开放 continuation：
  - `post_read_stall`
  - `post_edit_stall`
  - `post_summary_stall`
  - `post_text_stall`
- 如果已经形成有效 patch，优先走 `patch salvage`，而不是 continuation
- continuation 不是重发整轮 retry，而是：
  - 保留原始 prompt
  - 基于最近 runtime events 构造 compact resume section
  - 附加最近工具摘要、assistant 摘要、read preview、变更文件信息
  - 强制提示“不要从头分析、只用仓库相对路径、修好就直接结束”
- continuation 次数严格受 rollout flag 控制，默认最多 2 次

建议落点：

- `continuation_recovery.py`
- `claude_agent.py`
- `attempt_scheduler.py`
- `perf_flags.py`
- `events.py`

本轮实现：

- 新增 `continuation_recovery.py`，将 recent tool trace / assistant preview / read preview 收敛成 compact resume context
- `AttemptScheduler` 已新增 `continuation_retry_enabled / continuation_retry_limit` 与 `should_continue_after_timeout()`
- `ClaudeFixAgent` 已新增 `_run_runtime_with_continuation()`，对 eligible follow-up stall 执行 bounded continuation
- continuation 会落 `continuation_requested` 事件，并把 `continuation_retry_count / continuation_recovered / continuation_timeout_stages` 贯通到 performance metrics
- `Read` 事件已把 `read_preview` 进入 attempt events，便于续跑复用
- 修复 prompt 已额外强调：文件访问只使用仓库相对路径，避免重走日志里反复出现的 `C:\\...` 绝对路径错误
- 已补充 `client_connect_timeout` 根因透传：当 Claude SDK 初始化超时时，会触发一次同配置最小 CLI 连接诊断，并把 `403 认证/额度错误` 等 provider 根因拼回 timeout 文本，不再只留下空泛的 `client_connect_timeout`

验收标准：

- `follow_up_response_timeout` 中“无 patch 且直接整轮判废”的比例下降
- 同一 issue 的平均 attempt 数下降
- continuation 不引入成功率和质量回退
- 事件流能够还原 continuation 触发、resume 信息和最终结果

#### 8.7.12 受控 Shell / Finish 语义收口（有限参考 Claude Code 的 shell 权限模型）

状态：已完成

目标：

- 在不放松安全边界的前提下，为修复模型补齐 shell 搜索/查看/诊断能力
- 让 prompt、运行时和底层 SDK 的 shell 语义完全一致，不再让模型在 PowerShell / CMD / Bash 之间猜测
- 只把高危文件系统变更判为策略违规，避免 harmless shell 命令和 `Finish` 收尾动作污染成功修复
- 让 issue 文件路径约束和 prompt 对齐，只允许使用 workspace 相对路径定位目标文件

为什么要做：

- 当前主链路虽然已有 `Read / Grep / Glob / Edit / MultiEdit / Write`，但复杂定位场景仍容易退化成大量 `Read -> Thinking -> Read`
- 之前的 `Bash(pattern)` 白名单对 Windows 不友好，且把 `echo`、`pwd && ls`、`Finish` 这类正常收尾/诊断动作也误伤成 `forbidden_tool`
- Claude Code 值得借的不是“无脑放开 shell”，而是：
  - shell 能力是第一类工具
  - 权限判断要按命令风险，而不是只按工具名
  - 完成信号要有正式语义，不能和违规工具混在一起

建议策略：

- 运行时 builtin tool surface 继续暴露 SDK 内建 `Bash`，但 prompt 必须明确它的真实语义就是 bash-compatible shell
- prompt 只能描述真实可用的工具面，不能继续宣称 `Grep / Glob / Finish` 必然可用
- `ToolPolicy` 改为按“高危文件系统变更”判定策略违规，而不是继续用窄白名单误伤 harmless shell 行为
- 高危 shell 操作至少包括：
  - 删除文件/目录
  - 创建文件/目录
  - 覆盖文件/通过 shell 直接改写源码
- `echo`、搜索、查看、诊断、路径定位等无害 shell 操作不再直接判废
- `Finish` 必须成为正式受控工具语义，不再因为未注册/未允许而落成 `forbidden_tool`
- issue 文件路径在 prompt 中必须一律渲染成 workspace 相对路径，不再允许 `C:\...` 或 `/repo/...` 这类误导性路径

建议落点：

- `tool_surface.py`
- `registry.py`
- `policy.py`
- `claude_agent.py`
- `issue_prompt.py`
- `retry_context.py`

本轮实现：

- `tool_surface.py` 已改为 bash-compatible shell 语义：
  - runtime builtin tools 只保留真实可用的 `Read / Edit / Bash`
  - `Finish` 继续作为受控完成语义存在于 allow rules / policy 层，但不再由 prompt 强制调用
  - prompt 约束改成“工具名为 `Bash`，且只写 bash 兼容命令；不要写 PowerShell / CMD 语法”
- `ToolRegistry` 已将 `Finish` 正式注册为受控工具，并将 `Bash` 描述切换为 bash-compatible shell
- `ToolPolicy` 已从窄白名单切换为高危命令识别：
  - 允许 harmless shell 命令，例如 `echo`、`find`、路径定位、只读搜索
  - 拒绝高危文件系统变更命令，例如 `Remove-Item`、`New-Item`、`Set-Content`
- `ClaudeFixAgent` 的运行时 tool policy 已允许 `Finish`，不再把完成信号误判为 `forbidden_tool`
- `IssuePromptBuilder` 与 `RetryContext` 已更新为 bash-compatible shell 口径，不再要求 PowerShell/CMD
- `IssuePromptBuilder` 已将 issue 文件路径统一渲染为 workspace 相对路径，并明确“唯一允许直接操作的目标文件相对路径”
- prompt 已去掉对 `Grep / Glob / finish` 的虚假承诺，只描述当前真实工具面和真实完成语义
- 已补回归测试，覆盖：
  - `Finish` 允许通过
  - `echo`/`find` 等 harmless shell 命令允许通过
  - `Remove-Item` / `Set-Content` 仍被判为策略违规

验收标准：

- 修复模型可在当前 SDK 实际 shell 语义下稳定使用 shell 工具进行搜索、查看、诊断和 harmless 收尾
- `Finish` 不再制造伪 `forbidden_tool`
- 只有高危文件系统变更型 shell 命令才会被判为策略违规
- harmless shell 命令不会再把已成功的 patch attempt 判废
- prompt 描述、运行时工具面和底层 SDK 实际能力保持一致
- issue 文件定位不再依赖绝对路径或带前导 `/` 的伪根路径

### 参考 `pi-mono` 性能分析的适配边界

以下内容适合借鉴并已转化为本项目可执行项：

- 事件流驱动的运行时观测
- 文件/片段级缓存
- planner 侧预取与上下文打包
- 显式调度与分层验证

以下内容不建议直接照搬：

- TUI 差分渲染
- 面向交互式终端的 UI 优化
- 为通用 agent 平台设计的激进并行工具执行
- 对本项目当前单 issue 修复场景没有直接收益的“完整消息序列化缓存”

### 推荐实施顺序

1. 先做 `8.7.1 性能基线与验收指标`
2. 再做 `8.7.7 Attempt Event Stream`
3. 接着做 `8.7.8 Attempt-Local Read Cache`
4. 然后做 `8.7.9 Planner Prefetch / Context Packing`
5. 再做 `8.7.10 显式调度 Attempt 内验证与工具阶段`
6. 继续收紧 `8.7.2 模型交互瘦身`
7. 复核并迭代 `8.7.4 验证分层`
8. 持续优化 `8.7.5 Retry 降噪与超时恢复`
9. 在稳定基础上扩展 `8.7.3 低风险规则 fast path`
10. 最后通过 `8.7.6 指标化 rollout` 做前后对比和灰度放开

不要反过来做。先把“慢在哪里”量化，再补事件流、缓存、预取和调度这些全局骨架，最后才扩 fast path 和 rollout。

## 8.8 后续补强：复杂规则 Plan-First 工作流

### 目标

针对 `S3776 / S1144 / S107` 这类复杂规则，引入“先 Plan、再机器检查、最后 Edit”的结构化工作流，减少以下典型浪费：

- 第 1 次 attempt 先因为 boundary/scope 被拒
- 第 2 次 attempt 再因为 quality gate 被拒
- 第 3 次 attempt 才暴露 contract 与 quality gate 的策略冲突

这部分的目标不是让所有 issue 都变慢，而是**只在高风险规则上，用一轮轻量的 Plan 来避免 2~3 次重型 attempt 的反复试错。**

### 为什么需要这层

从 `batch_20260409130720.log` 的第 5 个 `S3776` 可以看到：

- 第 1 次失败：`scope_line_window_reject`
- 第 2 次失败：`quality_gate`
- 第 3 次失败：仍是 `quality_gate`

这类问题不是“模型完全不会修”，而是 edit 前没有一次性想清楚：

- 是否需要提取 helper
- helper 是同步还是异步
- 是否会触发 `async_signature / async_requires_await`
- 是否需要改公开方法名
- 当前 contract 是否允许 `signature_change`
- 改方法头/注释会不会越过当前边界

如果这些问题在 edit 前不显式化，就很容易把一个复杂规则拆成三次失败的 attempt。

### 设计原则

- 不是全局默认开启 plan mode
- 不是把 prompt 写得更长
- 不是让模型多说一轮自由文本解释

而是：

- 只对复杂/高风险规则启用
- 让 planner 产出结构化修复形状
- 在 edit 前做一次机器检查
- 只有 plan 与 contract/quality gate 不冲突时，才进入 edit

这和 Claude Code 的思路保持一致：

- 任务是一等对象
- 边界是运行时决策
- 失败应尽可能在 edit 前显式暴露

### 当前执行状态（2026-04-09）

- `8.8`：已完成
- 已完成：在 `issue_planner.py` 中为复杂规则接入 `Plan-First`，当前默认覆盖 `S3776 / S1144 / S107` 和带 `helper_extract / signature_change / new_type_add / multi_file_refactor / method_cluster_delete` 能力的高风险规则
- 已完成：新增 `repair_plan.py`，正式定义 `RepairPlan / RepairHelperPlan / PlanPrecheckResult`，并将其挂入 `EditContract`
- 已完成：`ClaudeFixAgent.fix_issue()` 已在进入 runtime 前执行 plan precheck；如发现 `signature_change_not_allowed` 等显式冲突，会直接返回 `plan_conflict`，不再浪费 2~3 次 attempt
- 已完成：`RetryContext / issue_retry / artifact_writer` 已贯通 plan 冲突的结构化记录与提示；`prompt_context.json / build_result.json / edit_contract.json` 现均可落盘 `repair_plan / plan_precheck`
- 已完成：`perf_flags.py / attempt_scheduler.py` 已接入 `Plan-First` 的启用标记，复杂规则走 `plan_first_full_path`，小规则继续保持 fast path / normal path
- 已完成：补充回归测试，覆盖 `S3776` 的 `signature_change` 冲突、agent 级 fail-fast、retry prompt 与 artifact 落盘；全量验证已通过
- 已完成（2026-04-09）：`Plan-First` 已升级为“受控 signature_change + propagation”。当复杂规则触达公开异步方法且缺少 `Async` 后缀时，`IssuePlanner` 现会在当前 workspace 内识别接口声明、调用点与 `nameof(...)` 传播目标；若目标齐全，会自动提升 `signature_change + multi_file_refactor`，并把相关文件加入 `EditContract.target_files`。同时，`PlanPrecheck` 新增 `signature_propagation_targets_missing / signature_propagation_not_allowed`，避免“只放开改名但漏掉接口/调用点联动”的假放行。
- 已完成（2026-04-09）：已补齐“质量门禁返工闭环”。`quality_gate_verifier.py` 已修复 `Task<(...)>`、`Task<Dictionary<...>>` 等签名的误判，避免把 tuple 返回值、嵌套泛型返回值误识别成方法名或参数列表；`retry_context.py` 已把质量门禁失败升级成可执行返工单，明确提示“只修当前 gate 问题、保留已通过改动、必要时同步接口/调用点/nameof 传播”。

### 需要补强的方向

#### 8.8.1 为复杂规则引入 Plan-First 模式

状态：已完成

适用规则第一批建议：

- `S3776`
- `S1144`
- `S107`
- 其他满足以下任一条件的规则：
  - 允许 `helper_extract`
  - 可能需要 `signature_change`
  - 可能新增 type / helper cluster
  - 易与 quality gate/contract 冲突

目标：

- 在真正 edit 前，先产出结构化 plan
- plan 不要求长篇解释，只要求把“修复形状”说清楚

建议落点：

- `issue_planner.py`
- 新增 `plan_mode.py` 或 `plan_contract.py`
- `claude_agent.py / agent_runtime.py`

验收标准：

- 复杂规则在 edit 前可拿到结构化 plan
- 小规则继续直接走 fast path 或 normal path，不被额外拖慢

#### 8.8.2 结构化 Plan 对象，而不是自由文本解释

状态：已完成

Plan 至少应包含：

- `repair_shape`
- `target_symbols`
- `new_helpers`
- `helper_async_map`
- `requires_signature_change`
- `requires_new_type`
- `expected_boundary_capabilities`
- `expected_quality_gates`
- `risk_notes`

目标：

- 不依赖模型聊天式“我准备怎么改”
- 而是让 runtime 能消费 plan 并做预检

建议落点：

- `issue_contract.py`
- 新增 `repair_plan.py`
- `artifact_writer.py`

验收标准：

- 每次 plan 都能落成结构化 artifact
- 后续 runtime 可以直接读取 plan 并做兼容性检查

#### 8.8.3 Edit 前做 Contract / Quality Gate 兼容性预检

状态：已完成

目标：

- 在进入 edit 前，尽早暴露“这次 plan 根本不被当前 contract/quality gate 允许”的场景

预检示例：

- `requires_signature_change=true`，但 contract 未允许 `signature_change`
- helper 被声明为 `async`，但 plan 中没有真实 `await` 来源
- 计划新增公开方法/类型，但未覆盖 XML doc 需求
- 计划触达的 symbol/range 超过当前 boundary profile

建议落点：

- 新增 `plan_reviewer.py`
- `boundary_runtime.py`
- `quality_gate.py`

验收标准：

- 能在 edit 前就明确回答“当前 plan 会不会被 boundary / quality gate 拒绝”
- 复杂规则的无效 attempt 数下降

#### 8.8.4 将 Plan 失败显式写入 RetryContext

状态：已完成

目标：

- 如果 plan 预检失败，不要等 edit 之后再把错误反馈给模型
- 直接把 `plan_conflict`、`signature_change_not_allowed`、`async_helper_invalid` 等写入 retry context

建议落点：

- `retry_context.py`
- `issue_retry.py`
- `artifact_writer.py`

验收标准：

- 复杂规则重试时，模型拿到的是“计划层冲突”的结构化反馈，而不是一堆后置失败日志

#### 8.8.5 只对复杂规则启用，保留小规则直修

状态：已完成

目标：

- 不让 `S1481 / S125` 这类小规则也先跑一轮 plan，避免整体变慢

建议策略：

- `Plan-First` 只对复杂规则或高风险能力启用
- 小规则继续：
  - `fast_path_short_form`
  - 或现有 normal path

建议落点：

- `perf_flags.py`
- `issue_planner.py`
- `attempt_scheduler.py`

验收标准：

- 复杂规则的平均重试次数下降
- 小规则平均耗时不升高

#### 8.8.6 受控 signature_change + propagation

状态：已完成

目标：

- 不再只因为 `signature_change_not_allowed` 就直接跳过像 `S3776` 这样的复杂规则
- 对公开异步方法的命名修正，允许在**明确识别到传播目标**时放开受控签名变更
- 传播范围只限于：
  - 接口声明
  - 同仓可定位的调用点
  - `nameof(...)` 引用

设计原则：

- 不做全局放开
- 不把 `signature_change` 单独裸放开
- 只有当 planner 在当前 workspace 中识别到可信传播目标时，才提升 contract 能力
- 如果需要改名但找不到传播目标，Plan-First 继续在 edit 前阻断

本轮实施结果：

- `RepairPlan` 已新增：
  - `primary_method_name`
  - `proposed_method_name`
  - `requires_propagation`
  - `propagation_targets`
- `IssuePlanner` 现支持在 `workspace_path` 下扫描 `.cs` 文件，识别：
  - `signature_declaration`
  - `callsite`
  - `nameof_ref`
- 当 `S3776` 等复杂规则触达公开异步方法且缺少 `Async` 后缀时，planner 会：
  - 先生成带传播目标的 repair plan
  - 在传播目标齐全时自动提升 `signature_change`
  - 如果跨文件传播成立，同时提升 `multi_file_refactor`
- `EditContract` 现会把传播目标相关文件加入 `target_files`，并把相关 symbol 纳入 `allowed_related_symbols`
- `PlanPrecheck` 已新增：
  - `signature_propagation_targets_missing`
  - `signature_propagation_not_allowed`
- 2026-04-09 补充修复：`IssuePlanner._iter_workspace_cs_files()` 已改为基于 `workspace_path` 相对路径过滤受保护目录，避免真实 batch 工作区位于 `.agent_workspaces/...` 时把所有 `.cs` 文件误判成受保护目录，导致 propagation 扫描结果为空
- 2026-04-09 补充修复：signature propagation 扫描读取源码时已改成 `utf-8` + `errors="replace"`，避免个别非 UTF-8 `.cs` 文件中断扫描
- 2026-04-09 回归补强：已新增覆盖真实 `.agent_workspaces/...` 目录形态的 planner 测试，确保 `S3776` 这类公开异步方法改名场景在真实运行目录下也会自动提升 `signature_change + multi_file_refactor`

验收标准：

- 类似 `AutoPlugin -> AutoPluginAsync` 的公开异步方法改名，不再因为 contract 固定缺失 `signature_change` 而直接跳过
- 如果需要联动接口或调用点，contract/plan 必须显式识别这些目标
- 如果传播目标缺失，必须在 edit 前 fail-fast，而不是放进去等 build 再炸

### 推荐实施顺序

1. 先做 `8.8.1 Plan-First 开关与适用规则集`
2. 再做 `8.8.2 结构化 Plan 对象`
3. 然后做 `8.8.3 Edit 前 plan 预检`
4. 再做 `8.8.4 RetryContext 接 plan 冲突`
5. 最后做 `8.8.5 与 fast path / scheduler 联动`

不要反过来做。先把“计划长什么样”和“谁需要 plan”定义清楚，再去接入 retry 与调度。

## 9. 执行顺序与里程碑

### Milestone 1：稳定跑通

- 完成 Phase 0
- 目标是稳定 clone、稳定认证、稳定超时和统一入口

### Milestone 2：状态可观测

- 完成 Phase 1
- 目标是 run/issue/attempt 可追踪、可恢复、可审计

### Milestone 3：运行时解耦

- 完成 Phase 2
- 目标是从“Claude 单体文件”变成“可测试的 runtime 架构”

### Milestone 4：单 Issue 约束升级

- 完成 Phase 3
- 目标是用 `EditContract + DiffReviewer + FollowUpQueue` 取代脆弱 scope

### Milestone 5：工程收口

- 完成 Phase 4
- 目标是清理旧实现并建立 CI 门槛

## 10. 第一批可直接执行的任务单

### Task A：入口与配置收口

状态：已完成

- 已完成：`base_branch` 生效逻辑收口为共享解析函数，并在主入口和批量入口统一使用
- 已完成：引入共享 `TargetConfig` 解析，收口了 `project_key / repository / author / base_branch / build/test/solution / max_issues`
- 已完成：引入共享 runtime preflight，主入口和批量入口已统一校验模型环境与 Sonar/ADO 必填配置
- 已完成：引入 `RunCoordinator`，主入口与批量入口已共用同一套单目标执行骨架
- 已完成：补充批量入口回归测试，验证共享 runtime、run label 和入口聚合逻辑

- 建 `RunCoordinator`
- 建统一 target config 解析
- 修正 `base_branch` 生效逻辑

### Task B：Git 网关收口

状态：已完成（兼容 facade 保留到 Phase 4 删除）

- 已完成：引入 `GitRepositoryGateway`，统一了 clone 分支和 PAT 注入逻辑，并补了错误脱敏单测
- 已完成：主入口与批量入口中的 `checkout -b / add -A / commit / push` 已迁移到 `GitRepositoryGateway`
- 已完成：`workspace.py`、`build_gate.py`、`integrations/ado.py` 中的 legacy clone / add / commit / push 已改为委托 `GitRepositoryGateway`
- 后续 Phase 4：删除兼容 facade，仅保留正式网关接口

- 建 `GitRepositoryGateway`
- 合并 clone/fetch/push 实现
- 做日志脱敏

### Task C：超时与回滚

状态：已完成

- 已完成：补充 SDK 初始化超时、模型首响应超时、后续响应空闲超时和单 issue 总时长超时
- 已完成：超时结果已统一归类为 `model_timeout`，并接入 retry feedback
- 已完成：issue attempt 超时后仍沿用现有 baseline restore 机制回滚工作区，再决定是否重试
- 已完成：抽出 `_SDKSessionController`，统一 `interrupt / close_response_stream / disconnect` 的 abort/cancel 清理语义，并补充了超时清理回归测试

- issue 级硬超时
- 空闲超时
- rollback 和 failure kind 统一

### Task D：状态与工件

状态：已完成

- 已完成：新增 `state.py`，定义 `RunState / TargetState / IssueState / AttemptState`、`RunStatus / TargetStatus / IssueStatus / AttemptStatus / RetryReason`，并统一状态序列化
- 已完成：新增 `artifact_writer.py`，稳定输出 `issue.json / edit_contract.json / prompt_context.json / patch.diff / reviewer_result.json / build_result.json / attempt_summary.json`
- 已完成：`process_issue_with_retries()` 已接入结构化 attempt artifact 和 `issue_summary.json`，并把 `issue_state` 回传到主流程
- 已完成：`RunCoordinator` 已接入 `TargetState` 落盘；单目标入口与批量入口已分别接入 `run_summary.json`
- 已完成：新增 `retry_context.py`，将 compiler errors、scope violation、模型超时和 forbidden tool 等重试信息收口成 `RetryContext`，并写入 `prompt_context.json`
- 已完成：`ClaudeFixAgent` 现可直接消费 `RetryContext` 渲染 retry 提示；`issue_retry` 仍保留 `build_retry_feedback()` 兼容接口用于现有测试和调用方
- 已完成：新增 `events.py` 与 `state_store.py`，接入 `events.jsonl`、MySQL `state snapshot / event` 表，以及旧 `run_record / issue_record` 兼容更新
- 已完成：补充回归测试，覆盖 issue artifact、target/run summary、入口层 run summary、事件落盘、DB 降级和失败状态落盘
- `Task D` 收尾：已完成，本阶段不再遗留未接线的状态/工件主项

- 建 `RunState / IssueState / AttemptState`
- 建 `ArtifactWriter`
- 落结构化 attempt artifact

### Task E：运行时分层

状态：已完成

- 已完成：新增 `model_gateway.py`，定义归一化的 request / session / event / abort result 契约
- 已完成：新增 `claude_adapter.py`，将 Claude SDK options、provider 兼容环境、消息转换和 session 生命周期收口到适配层
- 已完成：新增 `agent_runtime.py`，接管单次 issue attempt 的模型循环、首响应/后续响应/硬超时、heartbeat、取消和结果汇总
- 已完成：新增 `hooks.py`，并在 `AgentRuntime` 中正式接入 hook pipeline
- 已完成：新增 `registry.py` 和 `policy.py`，统一工具注册、允许列表、build 工具识别和禁止工具识别
- 已完成：新增 `resource_loader.py`，统一加载质量门禁、`CLAUDE.md / AGENTS.md` 长期规则并组合 system prompt
- 已完成：`ClaudeFixAgent` 已改为通过 `ClaudeAdapter + AgentRuntime` 执行单次 attempt；`_build_agent_extra_args / _build_sdk_child_env / _resolve_sdk_model / _load_csharp_quality_gate` 现均委托新层实现
- 已完成：补充独立回归测试，覆盖 `ModelGateway`、`AgentRuntime + hooks`、`ToolPolicy`、`ResourceLoader`，并保留原有 `ClaudeFixAgent` 兼容测试
- 后续 Phase 3：将 issue 级 prompt/context 规划、`EditContract / DiffReviewer / FollowUpQueue` 继续从 `ClaudeFixAgent` 下沉，替代当前 scope-only guardrail

- 建 `ModelGateway`
- 建 `AgentRuntime`
- 建 hooks
- 建 `ToolRegistry / ToolPolicy`

### Task F：单 Issue 约束升级

状态：已完成

- 已完成：新增 `issue_contract.py`，定义 `EditContract / ContractTargetSymbol`
- 已完成：新增 `issue_planner.py`，按 issue 元数据、scope 窗口和 retry context 生成 `IssuePlan + EditContract`
- 已完成：新增 `editor_policy.py`，把 patch-only 允许工具和额外编辑约束从 prompt 拼接中抽离
- 已完成：新增 `diff_reviewer.py`，支持对 undeclared file、同文件越界变更和 incidental fix 做结构化审查
- 已完成：新增 `follow_up_store.py`，将 reviewer 发现的相邻技术债写入 `logs/follow_ups/...jsonl`
- 已完成：`ArtifactWriter` 现落真实的 `edit_contract.json / reviewer_result.json`，不再写 Phase 1 占位符
- 已完成：`ClaudeFixAgent.fix_issue()` 已接入 `IssuePlanner + EditorPolicy + DiffReviewer`，并支持 `ISSUE_GUARDRAIL_MODE=scope|contract_review`
- 已完成：`RetryContext / issue_retry` 已接入 reviewer rejection 与 follow-up queue，支持 reviewer 失败后的结构化重试反馈
- 已完成：新增仓库级 `CLAUDE.md` 并通过 `ResourceLoader` 接入 system prompt
- 已完成：补充回归测试，覆盖 `IssuePlanner`、`DiffReviewer`、`FollowUpStore`、artifact 落盘和 `contract_review` 主流程
- 已完成：新增 `attempt_changes.py`，将 per-attempt diff、touched lines 提取和 baseline 读取收口为共享事实来源
- 已完成：新增 `boundary_policy.py`，统一 contract/scope 的行窗判定逻辑，避免 reviewer 和 legacy scope 各自猜 touched lines
- 已完成：针对 `csharpsquid:S125` 补充 rule-specific adjacent cleanup 策略，允许删除注释代码时一并清理紧邻死变量
- 后续 Phase 4：继续将 issue 级 prompt 构造、build/rule validation 从 `ClaudeFixAgent` 兼容层下沉，完成 `2.6` 的最终瘦身

- 建 `IssuePlanner`
- 建 `EditContract`
- 建 `DiffReviewer`
- 建 `FollowUpQueue`
- 引入 `CLAUDE.md`

### Task G：包结构迁移与删除旧实现

状态：已完成（公共兼容 facade 保留但不再参与主链路）

- 已完成：删除 `src/pi_sonar_agent/__init__.py` 中的 `__path__` 注入，改为真正的标准 package 导出
- 已完成：补齐 `src/pi_sonar_agent/agent|fixers|integrations|sonar_mcp` 子包导出，并保留基于模块别名的桥接文件，保证 `pi_sonar_agent.*` monkeypatch 与导入语义稳定
- 已完成：legacy `agent/__init__.py`、`fixers/__init__.py`、`integrations/__init__.py`、`sonar_mcp/__init__.py` 已改为包内相对导出，移除对 `pi_sonar_agent.*` 的反向依赖，解决去掉路径 hack 后的循环导入
- 已完成：`run.py` 已删除开发机私有路径和安装提示，统一为标准可移植入口
- 已完成：新增 `issue_prompt.py`、`scope_guard.py`、`fix_verifier.py`，将 issue prompt 构造、legacy scope 计算/校验、build/rule verification 从 `ClaudeFixAgent` 兼容层继续下沉，完成 `2.6` 的第二轮瘦身
- 已完成：新增 `boundary_runtime.py`，将 boundary review 编排从 `ClaudeFixAgent` 继续下沉；`FixVerifier` 现通过该 runtime 统一执行 scope + reviewer 判定
- 保留说明：`GitClient` 与少量 facade 继续作为公共兼容接口存在，但主链路已经统一走正式 gateway / runtime / verifier 组件

- 收敛到 `src/pi_sonar_agent/`
- 删兼容 hack
- 删旧 clone helper
- 删旧 scope

### Task H：质量门槛

状态：已完成

- 已完成：收口全仓 `ruff` 问题，修复 legacy 模块、bridge 模块、未使用导入、尾随换行和 import 排序；`ruff check .` 已可直接通过
- 已完成：在 [pyproject.toml](D:/MyProjects/pi-sonar-agent/pyproject.toml) 中补充 `tool.ruff.extend-exclude`，忽略 `.tmp`、`.agent_workspaces`、`logs` 等生成目录，消除本地运行 `ruff check .` 时的无关噪音
- 已完成：新增 [ci.yml](D:/MyProjects/pi-sonar-agent/.github/workflows/ci.yml)，接入 `pip install -e ".[dev]"`、`ruff check src tests run.py` 和 `pytest -q`
- 已完成：新增 [test_quality_gate_matrix.py](D:/MyProjects/pi-sonar-agent/tests/test_quality_gate_matrix.py)，补充 `scope` 与 `contract_review` 的对照门槛测试
- 已完成：关键验收场景现已具备测试覆盖：
- 非 `develop` 分支 clone：`test_git_gateway.py`、`test_run_coordinator.py`
- Git 认证失败与脱敏日志：`test_git_gateway.py`、`test_preflight.py`
- issue 超时回滚：`test_claude_agent.py`、`test_issue_retry.py`
- 单目标与批量入口行为一致：`test_main.py`、`test_batch_runner.py`
- `scope` 与 `contract_review` 对照：`test_issue_guardrails.py`、`test_quality_gate_matrix.py`
- DB 降级运行：`test_state_store.py`

- 修 `ruff`
- 接 CI
- 补关键集成测试

## 11. 风险与反目标

- 不要一上来先迁移全部包结构，否则容易把运行链路和导入链路一起打断
- 不要先做 MCP 正式接入，当前更重要的是外层控制 build/git 的边界
- 不要把数据库做成单点
- 不要一口气删除旧实现，必须经过 feature flag 和对照验证
- 不要为了参考 `pi-mono` 而引入与当前项目目标无关的 TUI、插件市场或大而全交互层

## 12. 参考资料

- `pi-mono`
- https://github.com/badlogic/pi-mono
- `pi-mono AGENTS.md`
- https://github.com/badlogic/pi-mono/blob/main/AGENTS.md
- `pi-agent-core`
- https://github.com/badlogic/pi-mono/blob/main/packages/agent/README.md
- `pi-ai`
- https://github.com/badlogic/pi-mono/blob/main/packages/ai/README.md
- `pi-coding-agent`
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md
- `pi-coding-agent SDK`
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md
- Anthropic Claude Code memory / hooks
- https://code.claude.com/docs/zh-CN/memory
- https://code.claude.com/docs/en/hooks
- OpenAI Codex practices
- https://openai.com/business/guides-and-resources/how-openai-uses-codex/
- https://openai.com/codex
- https://openai.com/index/harness-engineering
- OpenAI patch tool
- https://developers.openai.com/api/docs/guides/tools-apply-patch
- Agentless
- https://lingming.cs.illinois.edu/publications/fse2025.pdf

## 13. 最终结论

实施顺序必须固定为：

1. 先收敛入口、分支、认证、超时
2. 再建立状态模型和执行工件
3. 再拆 runtime、model、tools、resource loader
4. 然后升级单 Issue 治理链路
5. 最后做包结构收口和旧实现退场

不要反过来做。当前项目最缺的不是“更多 Agent 技巧”，而是“稳定、可控、可恢复、可审计的运行骨架”。
