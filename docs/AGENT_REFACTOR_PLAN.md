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
- `4.3 删除旧实现`：已完成第一批。legacy 包 `__init__.py` 已改为相对导出，消除了移除 `__path__` 后的循环导入；`ClaudeFixAgent` 中的 issue prompt、legacy scope guard、build/rule verification 已继续下沉到 `issue_prompt.py / scope_guard.py / fix_verifier.py`
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
