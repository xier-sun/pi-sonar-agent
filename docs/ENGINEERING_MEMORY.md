# 工程问题记忆

这份文档记录的是“这个项目已经踩过哪些坑，现在是怎么约定的”。
它不是完整变更日志，而是给维护者的排障记忆和工程边界说明。

如果你想看当前架构全貌，请配合阅读 [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)。

## 1. 依赖与虚拟环境

### 症状

- 缺少模块
- `pytest` / `ruff` 找不到
- 同一台机器上系统 Python 和项目 Python 行为不一致

### 根因

- 虚拟环境损坏
- 依赖没有按当前仓库版本重装
- 实际运行时没有用仓库内 `.venv`

### 当前约定

- 优先使用 `python -m pip install -e ".[dev]"` 安装项目和开发依赖
- 优先使用 `.\.venv\Scripts\python.exe ...` 运行命令
- `run.py` 只负责把 `src/` 注入 `sys.path`，不会替你补装依赖

关键文件：

- [pyproject.toml](../pyproject.toml)
- [run.py](../run.py)

## 2. `.env` 必须压过系统环境

### 旧问题

- `.env` 里明明配了一套模型
- 实际运行却吃到了机器级 `ANTHROPIC_*` 或 `OPENAI_*`

### 根因

- Claude SDK 子进程会继承当前进程环境
- 如果不主动整理 child env，就容易被系统环境污染

### 当前约定

- `.env` 由 [src/core/model_env.py](../src/core/model_env.py) 统一加载
- 模型选择优先来自 `.env`
- 发给 Claude SDK 的 child env 由 [src/core/claude_adapter.py](../src/core/claude_adapter.py) 再做一次清理

这意味着：

- 问题优先查 `.env`
- 不要指望机器级环境变量默默生效

## 3. OpenAI 风格代理不是直接给 OpenAI SDK 用的

### 旧问题

- 用户配置了 `OPENAI_API_KEY` / `OPENAI_BASE_URL`
- 但项目实际走的是 Claude Code SDK

### 根因

- Claude SDK 原生吃的是 Anthropic 风格环境变量

### 当前约定

- 允许用户继续写 `OPENAI_*`
- [src/core/model_env.py](../src/core/model_env.py) 会把它们映射成 Claude SDK 需要的兼容变量
- [src/core/claude_adapter.py](../src/core/claude_adapter.py) 会处理第三方 Anthropic 兼容网关下的 CLI 参数和 child env

## 4. `base_branch` 必须真正控制初始 clone

### 旧问题

- 配了 `base_branch`
- 实际初始 clone 还是固定先拉 `develop`

### 根因

- 早期分支配置只是“clone 后再 fetch/check”的参考值，不是真正的 clone 参数

### 当前约定

- `base_branch` 现在由 [src/core/target_config.py](../src/core/target_config.py) 和 [src/core/git_gateway.py](../src/core/git_gateway.py) 统一解析和执行
- 单目标入口优先级：CLI > `targets.json` > 默认值
- 批量入口优先级：target > 默认值
- preflight 会在真正处理 issue 前通过 [src/core/preflight.py](../src/core/preflight.py) 校验远端分支是否存在

这意味着：

- 分支问题会更早失败
- 文档里不应该再写“固定 clone develop”
- 当前 `base_branch` 不从 `.env` 读取

## 5. ADO PAT 必须贯穿 Git 链路，而不是只给 REST API

### 旧问题

- ADO API 能通
- `git clone` / `git push` 却失败

### 根因

- 早期 PAT 只用于 REST API 请求头
- Git 操作依赖机器上碰巧已有的凭据缓存

### 当前约定

- 所有正式 Git 操作统一走 [src/core/git_gateway.py](../src/core/git_gateway.py)
- `clone / branch / add / commit / push` 语义已经收口
- PAT 会注入 HTTPS remote URL
- 错误日志会用 redacted URL，避免敏感信息直接落盘

仍需注意：

- 现在的 PAT 方案是“统一且可用”，但不是最理想的长期安全方案
- 如果后续要继续加强，可以再演进到 `credential helper` 或 `http.extraheader`

## 6. 启动前失败和运行中失败必须分开

### 旧问题

- 分支不存在、工作区不可写、模型配置错误，往往在运行很后面才炸

### 根因

- 早期缺少统一 preflight

### 当前约定

- [src/core/preflight.py](../src/core/preflight.py) 统一负责：
  - 模型环境校验
  - `SONARQUBE_*` / `ADO_*` 必填配置校验
  - 工作区可写校验
  - 远端基线分支存在校验
- 正式入口 [src/main.py](../src/main.py) 和 [src/batch_runner.py](../src/batch_runner.py) 都走同一套 preflight

## 7. 单个 issue 必须独立回滚，不能污染整轮运行

### 旧问题

- 一个 issue 修坏代码后，后续 issue 也被污染

### 根因

- 早期缺少 issue 粒度的 Git 基线和回滚

### 当前约定

- [src/core/issue_retry.py](../src/core/issue_retry.py) 会在每个 issue 开始前建立工作区基线
- 当前 issue 失败时只回滚本 issue 改动
- 之前成功 issue 的改动必须保留
- 单 issue 默认最多重试 3 次

## 8. “有改动”不等于“修复成功”

### 旧问题

- Agent 正常退出，或者改到了文件，就被当成成功
- 实际上可能根本没落有效修改，或者已经把代码改坏

### 根因

- 早期成功判定过于宽松

### 当前约定

- 无文件改动视为失败
- issue 修复后必须经过 [src/core/fix_verifier.py](../src/core/fix_verifier.py) 的 issue 级构建校验
- 如果 issue 级构建失败，就进入 retry，而不是计入成功

## 9. retry memory 不能只塞整段 build log

### 旧问题

- 把大段 build log 原样塞回 prompt
- 模型重试时仍然反复犯同类错误

### 根因

- 没有结构化 retry memory

### 当前约定

- [src/core/retry_context.py](../src/core/retry_context.py) 负责结构化 retry memory
- 记录的内容包括：
  - 关键编译错误
  - scope violation
  - reviewer rejection
  - forbidden tool
  - build tool failure
  - model timeout
- [src/core/issue_prompt.py](../src/core/issue_prompt.py) 会把结构化 retry memory 渲染回 prompt
- `prompt_context.json` 也会写出 `retry_context`

## 10. 超时不是“等一下就好”，必须能取消和清理

### 旧问题

- SDK 卡住时只能等
- 日志里有心跳，但不会真正中止执行

### 根因

- 早期只保护了首响应，后续工具调用或流式响应卡住时缺少显式 abort

### 当前约定

- [src/core/agent_runtime.py](../src/core/agent_runtime.py) 负责：
  - client connect timeout
  - first response timeout
  - follow-up timeout
  - issue hard timeout
- [src/core/claude_adapter.py](../src/core/claude_adapter.py) 负责显式：
  - `interrupt`
  - `close_response_stream`
  - `disconnect`

这意味着日志里看到 timeout 时，不只是“报错返回”，还伴随真正的会话清理。

## 11. 抑制“顺手修”不能只靠 prompt

### 旧问题

- 模型会顺手修同文件里其他相同规则问题
- 单纯靠 prompt 约束不稳定

### 根因

- 缺少结构化的 issue 级边界控制

### 当前约定

当前采用多层 Guardrail：

- [src/core/issue_planner.py](../src/core/issue_planner.py): 生成 `IssuePlan`
- [src/core/issue_contract.py](../src/core/issue_contract.py): 声明 `EditContract`
- [src/core/editor_policy.py](../src/core/editor_policy.py): 推导 patch-only 策略
- [src/core/diff_reviewer.py](../src/core/diff_reviewer.py): patch 审查
- [src/core/follow_up_store.py](../src/core/follow_up_store.py): incidental fix 只入队，不混入当前 patch
- [src/core/scope_guard.py](../src/core/scope_guard.py): legacy scope guard 兼容链路

当前支持两种模式：

- `scope`
- `contract_review`

默认仍是 `scope`，但新设计能力已经在 `contract_review` 链路中落地。

## 12. C# 规则需要仓库级质量门禁补充

### 旧问题

- 模型只看 Sonar 问题本身，容易修完一个问题又引入新的命名、异步或结构问题

### 根因

- 规则说明不足以覆盖仓库自己的代码规范

### 当前约定

- 对 C# 文件，prompt 会自动附带 [data/csharp-quality-gate.md](../data/csharp-quality-gate.md)
- [src/core/resource_loader.py](../src/core/resource_loader.py) 会加载仓库级 `CLAUDE.md/AGENTS.md`
- [CLAUDE.md](../CLAUDE.md) 现在也是长期工程规则的一部分

## 13. PR 说明必须结构化，不能只写一句话

### 旧问题

- reviewer 很难知道修了什么、跳过了什么、为什么失败

### 根因

- 早期 PR 描述太短

### 当前约定

- [src/core/pr_description.py](../src/core/pr_description.py) 负责生成结构化 PR 描述
- 本地会在 `logs/pr_descriptions/` 保留副本
- 仓库工作区里也会写一份 PR 详细报告，供 PR 描述引用

## 14. Azure DevOps abandoned PR 不能直接更新

### 症状

- 更新 PR 描述返回 `400`

### 根因

- 目标 PR 已经处于 `abandoned`

### 当前约定

- 不对 abandoned PR 直接打补丁
- 先恢复，或新建 PR

## 15. 钉钉通知有两套能力，不要混用接口

### 旧问题

- 企业应用私信和机器人 webhook 混着调，导致 `404`

### 当前约定

- 企业应用私信走企业应用接口
- webhook 走 `DINGTALK_WEBHOOK`
- 如果 target 已解析出 `dingtalk_userid`，优先尝试私信；失败再回退 webhook

关键文件：

- [src/core/dingtalk.py](../src/core/dingtalk.py)
- [src/core/recipient_resolution.py](../src/core/recipient_resolution.py)

## 16. 状态、事件、工件必须结构化，不能只靠日志

### 旧问题

- 跑完以后主要靠 console log 回看，很难做状态汇总、二次分析或 DB 同步

### 当前约定

- [src/core/state.py](../src/core/state.py): run/target/issue/attempt 状态模型
- [src/core/events.py](../src/core/events.py): `events.jsonl`
- [src/core/artifact_writer.py](../src/core/artifact_writer.py): 尝试工件
- [src/core/state_store.py](../src/core/state_store.py): artifact-first + DB optional sync

本地工件是第一真相源，数据库只是增强，不是单点依赖。

## 17. 包结构已经收口，但仍有桥接层

### 旧问题

- 以前依赖 `__path__` hack 和开发机本地 fallback
- 包结构不稳定，不利于安装和迁移

### 当前约定

- [run.py](../run.py) 已经去掉开发机私有 fallback
- [src/pi_sonar_agent/__init__.py](../src/pi_sonar_agent/__init__.py) 是正式包入口
- `src/pi_sonar_agent/*` 当前仍保留桥接模块，方便标准化 import 路径

这意味着：

- 对外导入请优先使用 `pi_sonar_agent.*`
- 阅读真实实现时仍以 `src/core`、`src/agent`、`src/fixers`、`src/integrations` 为主

## 18. 质量门禁必须覆盖整仓，而不是只看局部

### 旧问题

- 有测试通过，但 lint 和包布局仍可能漂移

### 当前约定

- CI 在 [.github/workflows/ci.yml](../.github/workflows/ci.yml)
- 本地推荐运行：
  - `python -m ruff check .`
  - `python -m pytest -q`
- [pyproject.toml](../pyproject.toml) 已排除 `logs/`、`.agent_workspaces/` 等生成目录

## 19. 维护时的优先级建议

后续如果继续演进，优先遵守这些边界：

1. 新增能力先挂到 `RunCoordinator / AgentRuntime / FixVerifier / StateStore` 的清晰层次里
2. 不要把新的流程编排重新塞回 `ClaudeFixAgent`
3. 不要绕开 `GitRepositoryGateway` 自己拼 clone/push 逻辑
4. 不要绕开 `ArtifactWriter` 和 `StateStore` 只写散乱日志
5. 新的 guardrail 优先加到 `EditContract + DiffReviewer` 链路，而不是继续堆 prompt 文案
