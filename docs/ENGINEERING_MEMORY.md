# 工程问题记忆

这份文档记录的是“这个项目已经踩过哪些坑，现在是怎么约定的”。
它不是完整变更日志，而是给维护者的排障记忆和工程边界说明。

如果你想看当前架构全貌，请配合阅读 [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)。

## 1. `.env` 是工程内唯一可信配置源

### 旧问题

- `.env` 里明明配了一套模型
- 实际运行却吃到了机器级 `ANTHROPIC_*` 或 `OPENAI_*`

### 根因

- Claude SDK 子进程会继承当前进程环境
- 如果不主动清理 child env，就容易被系统环境污染

### 当前约定

- `.env` 由 [src/core/model_env.py](../src/core/model_env.py) 统一加载
- 发给 Claude SDK 的 child env 由 [src/core/claude_adapter.py](../src/core/claude_adapter.py) 再做一次清理
- 排障模型问题时优先查 `.env` 和 request snapshot，不要先猜系统环境

## 2. 第三方 Anthropic-compatible endpoint 和本地交互式 Claude Code 不一定等价

### 旧问题

- 本地 `claude` 用同一 URL / token 可以成功
- 自动化 run 却在日志里出现 `401 authentication_failed`

### 根因

- 当前自动化对非官方 `ANTHROPIC_BASE_URL` 会进入 `bare` 兼容模式
- 当前兼容逻辑可能把 `ANTHROPIC_AUTH_TOKEN` 桥接成 `ANTHROPIC_API_KEY`
- 本地交互式 Claude Code 和自动化 bare 链路使用的认证来源可能不同

### 当前约定

- 对第三方 endpoint，优先显式配置 `ANTHROPIC_API_KEY`
- 遇到 `401` 时，先看：
  - request snapshot 里的 `mode`
  - SDK init 里的 `apiKeySource`
  - `.env` 中到底配置的是 `ANTHROPIC_API_KEY` 还是 `ANTHROPIC_AUTH_TOKEN`
- 不要因为“本地 Claude Code 能用”就假设自动化 bare 链路一定等价

## 3. 当前 issue 执行模式只有 `simple_loop`

### 旧问题

- 文档和日志里容易把多种历史 execution mode 当成仍然可选

### 根因

- 代码已经把 execution mode 规范化为 `simple_loop`
- 但历史文档里仍保留了旧分支叙述

### 当前约定

- [src/core/simple_mode.py](../src/core/simple_mode.py) 会把 execution mode 统一收敛到 `simple_loop`
- `simple_loop` 下 fix 子 agent 不负责自行编译
- 构建、post-check、review gate 都由外层 verifier 统一负责

## 4. 共享运行骨架是事实，不要再维护“双实现”心智模型

### 旧问题

- 认为单目标入口和批量入口维护两套不同主流程

### 根因

- 历史上确实存在漂移
- 重构后已经收口为共享 `RunCoordinator`

### 当前约定

- 单目标入口和批量入口都复用 [src/core/run_coordinator.py](../src/core/run_coordinator.py)
- 行为差异主要体现在参数来源，而不是编排逻辑
- 排障时不要再分别追两套主链路

## 5. `base_branch` 现在真正控制初始 clone

### 旧问题

- 配了 `base_branch`
- 实际初始 clone 还是固定先拉 `develop`

### 根因

- 早期分支配置只是 clone 后再切换的参考值

### 当前约定

- `base_branch` 直接决定初始 clone 分支、修复基线和 PR 目标分支
- 生效顺序：
  - 单目标：CLI > `targets.json` > 默认值
  - 批量：target > 默认值
- `base_branch` 当前不从 `.env` 读取

## 6. 工具面必须和提示词、allowlist 一致

### 旧问题

- prompt 提醒模型去用 `Grep/Glob`
- 实际运行面又把它们裁掉，导致 `No such tool available`

### 根因

- 运行面、allowlist、prompt 文案曾出现过不一致

### 当前约定

- fix runtime 默认声明的内建工具面是：
  - `Read`
  - `Grep`
  - `Glob`
  - `Edit`
  - `MultiEdit`
  - `Write`
  - `Bash`
- prompt、visible toolset、runtime request 必须围绕同一套工具面生成
- 如果第三方 provider 的 SDK init trace 只回部分工具，先把它当成 provider / CLI 兼容问题排查，而不是先去删 prompt 里的工具说明

## 7. `Reached maximum number of turns` 不是编译错误

### 旧问题

- 看到这条消息就以为是 `dotnet build` 报错

### 根因

- 这条错误经常被展示在构建日志区域
- 但它本质上是 agent 会话回合数耗尽

### 当前约定

- 当前 fix agent 默认 turn floor 是 `16`
- 某些规则 profile 会继续抬高上限，例如 `S107`
- 排障时优先判断：
  - patch 是否始终没落盘
  - 是否反复小步编辑
  - 是否发生无效 Edit / MultiEdit / Write 调用

## 8. “有 patch”不等于“修复成功”

### 旧问题

- Agent 正常退出或改到了文件，就容易被误当成成功

### 根因

- 早期成功判定过于宽松
- “方向正确但规则还没消失”的 patch 容易被放行

### 当前约定

- issue 修复后的成功判定要经过：
  - `EditContract` / drift 审查
  - deterministic verifier
  - quality gate
  - review gate
  - 规则本地 post-check
- 尤其是 `S107`，即使构建通过，参数数目仍 `>7` 也不能算成功

## 9. `S107` 现在有专项护栏，不要再把它当普通文本替换问题

### 旧问题

- 修法停留在 `8` 或 `9` 个参数的半成品
- 或者只改了一部分调用点

### 根因

- `S107` 本质上是签名传播和上下文收敛问题
- 靠零碎 replace 很容易把 turn 用光

### 当前约定

- 主修复引擎仍以 Roslyn 为主
- prompt 会在复杂 case 下引导读取 [docs/s107-fix-guide.md](../docs/s107-fix-guide.md)
- 运行时会把该指南同步到工作区 `.pi-sonar-agent-runtime/s107-fix-guide.md`
- verifier 还会做 `S107` 参数计数 post-check，防止 `8/9` 参数 patch 被误记为 fixed

## 10. Review Gate 是第一公民，不是附属提示

### 旧问题

- deterministic verifier 通过后，容易把 patch 直接送去编译
- 模型 review 只被当成“可有可无”

### 根因

- 早期更偏向“先 build，再看结果”
- 对 propagation、contract drift、规则未真正消除这类问题拦截不足

### 当前约定

- review gate 默认开启
- review gate 可以使用独立 provider / key / model
- 当 review gate 返回 `retry` 时，应优先看 `review_gate_result` 和 `retry_context`
- 如果 deterministic hard blocker 已经成立，review gate 会被标记为 `not_applicable`

## 11. Recipient 解析失败不等于数据库报错

### 旧问题

- 日志里看到 `DingTalk UserId: (unresolved)`，就以为数据库查询失败了

### 根因

- 当前实现对 DB 反查是“可选能力”
- `.env` 缺少 `DB_*` 时会直接跳过建连，不会抛错

### 当前约定

- recipient 顺序是：
  - `targets.json.dingtalk_userid`
  - MySQL `author` 反查
  - `unresolved`
- 只有当 `.env` 同时存在 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME` 时，才会真正查库
- 当前 SQL 固定为：

```sql
SELECT UserId
FROM erp4.dingtalkuserdetail
WHERE Email = %s
LIMIT 1
```

- 其中 `%s` 的值是当前 target 的 `author`

## 12. 状态、事件、工件优先，MySQL 同步是可选增强

### 旧问题

- 跑完以后主要靠 console log 回看
- DB 不可用时容易怀疑主流程会不会丢状态

### 根因

- 早期 artifact 和状态模型不完整

### 当前约定

- `logs/run_artifacts/`、`logs/issue_artifacts/`、`events.jsonl` 是一等事实来源
- MySQL 不可用时会自动降级，不阻塞主流程
- 排障时先看本地 artifact，再看 DB

## 13. Reviewer / Follow-up / Drift 已经结构化，不要再只看 raw log

### 旧问题

- 排障时只盯着控制台输出
- 很容易忽略 reviewer 结论、drift audit、follow-up 队列

### 当前约定

- 关注这些结构化工件：
  - `reviewer_result.json`
  - `build_result.json`
  - `attempt_summary.json`
  - `issue_summary.json`
  - `logs/follow_ups/...`
- 先看结构化结论，再回到 raw log 对照细节

## 14. 质量门禁和仓库能力是长期约束，不是一次性 prompt 文案

### 旧问题

- 只看 Sonar 规则本身，容易修出新的命名、异步、语言版本问题

### 根因

- 仓库自己的质量门禁、语言版本和能力边界不在 Sonar issue 本体里

### 当前约定

- C# prompt 会叠加质量门禁与仓库规则
- repo capability 会影响 planner、quality gate 和 Roslyn 策略
- `S107` 这类复杂规则在当前仓库能力不满足时宁可跳过，也不要冒险做错误重构

## 15. 文档必须跟运行事实走

### 旧问题

- 文档停留在旧执行模式、旧工具面或旧入口描述

### 当前约定

- 一旦运行语义发生收口，必须同步更新：
  - [README.md](../README.md)
  - [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)
  - [docs/RUNBOOK.md](RUNBOOK.md)
  - [docs/ENGINEERING_MEMORY.md](ENGINEERING_MEMORY.md)
  - [docs/AGENT_REFACTOR_PLAN.md](AGENT_REFACTOR_PLAN.md)

这份文档本身就是为了减少“代码已经变了，但大家脑子里还是旧版本”的情况。
