# pi-sonar-agent

`pi-sonar-agent` 是一个面向 SonarQube 技术债修复的自动化 Agent。它围绕“单个 issue 的最小安全修复”组织整条交付链路：

1. 从 SonarQube 拉取指定作者的 open issues
2. 按 target 配置 clone Azure DevOps 仓库并准备工作区
3. 为单个 issue 生成规划、边界合同和 prompt 上下文
4. 通过 Claude Code SDK / Roslyn / 规则专用引擎执行修复
5. 对 patch 做 deterministic verifier、review gate、构建与 post-check 校验
6. 按 issue 粒度回滚、重试、汇总工件
7. 最终构建、创建 PR、添加 reviewer、发送钉钉通知

## 当前运行快照

- 单目标入口和批量入口都共用 [src/core/run_coordinator.py](src/core/run_coordinator.py)。
- 当前唯一支持的 issue 执行模式是 `simple_loop`，构建与 post-check 由外层 verifier 统一负责。
- 当前 fix runtime 默认声明的内建工具面是 `Read/Grep/Glob/Edit/MultiEdit/Write/Bash`，并由 `ToolPolicy` 和 `EditorPolicy` 进一步收敛。
- 默认 issue turn floor 已提升到 `16`；规则 profile 可以继续抬高单规则上限。
- 校验链路已经分层为 `EditContract / DiffReviewer / FixVerifier / Quality Gate / Review Gate`，不再只靠 prompt 约束。
- `reviewer_email` 与 `dingtalk_userid` 支持从 `targets.json` 显式配置；未显式配置时，钉钉 userId 会尝试按 `author` 走 MySQL 反查。
- `csharpsquid:S107` 当前仍以 Roslyn 为主修复引擎；同时保留了专项提示、workspace 内指南同步和本地 post-check。

## 文档导航

- [PROJECT_GUIDE.md](PROJECT_GUIDE.md): 当前项目结构、共享运行骨架、模块职责
- [docs/RUNBOOK.md](docs/RUNBOOK.md): 安装、配置、运行、排障手册
- [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md): 已踩问题、当前工程约定、常见误区
- [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md): 重构与优化升级的当前状态快照
- [docs/s107-fix-guide.md](docs/s107-fix-guide.md): `csharpsquid:S107` 专项修复指南

## 推荐入口

生产和日常运行优先使用：

- [run.py](run.py)
- `python -m pi_sonar_agent.batch_runner data/targets.json`

以下入口仍保留，但更适合开发调试：

- [src/cli.py](src/cli.py)
- 安装后的命令行脚本 `pi-sonar-agent`

## 快速开始

### 1. 安装依赖

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

### 2. 准备配置

最少需要：

- `.env`
- `data/targets.json`

`.env` 至少需要这些核心配置：

- `SONARQUBE_HOST`
- `SONARQUBE_TOKEN`
- `ADO_BASE_URL`
- `ADO_PROJECT`
- `ADO_PAT`

常见补充项：

- `SONARQUBE_ORG`
- `ADO_ORG`
- `WORKSPACE_ROOT`
- `BUILD_COMMAND`
- `TEST_COMMAND`
- `SOLUTION_PATH`
- `MAX_ISSUES`
- `ISSUE_GUARDRAIL_MODE`
- `PI_SONAR_REVIEW_GATE_*`
- `DB_*`
- `DINGTALK_*`

模型配置建议统一写在 `.env`，由 [src/core/model_env.py](src/core/model_env.py) 解析。常见方式有两种：

Anthropic 兼容网关：

```env
ANTHROPIC_BASE_URL=https://your-gateway/api/anthropic
ANTHROPIC_API_KEY=your_key
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-5
```

OpenAI 风格代理：

```env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://your-gateway/api/coding/paas/v4
OPENAI_MODEL=glm-5
```

注意：

- 对第三方 `ANTHROPIC_BASE_URL`，当前自动化链路会走 Claude Code CLI 的 `bare` 兼容模式。
- 如果只配置了 `ANTHROPIC_AUTH_TOKEN` 而没有 `ANTHROPIC_API_KEY`，当前实现可能会为第三方网关桥接成 `ANTHROPIC_API_KEY`。
- 因此“本地交互式 Claude Code 能用”不等于“自动化 bare 链路一定能用”；如遇 `401 authentication_failed`，优先显式配置 `ANTHROPIC_API_KEY` 并核对 provider 的真实认证要求。

`data/targets.json` 是默认 target 和批量 target 的配置来源。最小示例：

```json
[
  {
    "project_key": "your_sonar_project_key",
    "repository": "your_repo",
    "author": "owner@company.com",
    "base_branch": "main",
    "solution_path": "src/YourSolution.sln",
    "max_issues": 3
  }
]
```

常用 target 字段：

- `project_key`
- `repository`
- `author`
- `reviewer_email`
- `dingtalk_userid`
- `issue_keys`
- `max_issues`
- `base_branch`
- `build_command`
- `test_command`
- `solution_path`
- `keep_workspace`
- `skip_build_gate`

### 3. 运行

按 `data/targets.json` 第一个 target 运行：

```powershell
.\.venv\Scripts\python.exe run.py
```

显式覆盖默认 target：

```powershell
.\.venv\Scripts\python.exe run.py `
  --project-key YOUR_PROJECT `
  --repository YOUR_REPO `
  --author you@company.com `
  --base-branch main `
  --solution-path src/YourSolution.sln `
  --max-issues 1
```

批量运行：

```powershell
.\.venv\Scripts\python.exe -m pi_sonar_agent.batch_runner data\targets.json
```

## 当前架构要点

### 共享运行骨架

- [src/main.py](src/main.py): 单目标正式入口
- [src/batch_runner.py](src/batch_runner.py): 批量入口
- [src/core/run_coordinator.py](src/core/run_coordinator.py): 单目标/批量共享编排器
- [src/core/target_config.py](src/core/target_config.py): target 配置解析与优先级收口
- [src/core/preflight.py](src/core/preflight.py): 启动前校验
- [src/core/git_gateway.py](src/core/git_gateway.py): clone / branch / push / URL 脱敏

### 单 issue 修复链路

- [src/core/issue_retry.py](src/core/issue_retry.py): issue 级基线、重试、artifact、状态汇总
- [src/core/issue_planner.py](src/core/issue_planner.py): issue 策略与 `EditContract`
- [src/core/issue_prompt.py](src/core/issue_prompt.py): simple-loop prompt 装配
- [src/core/editor_policy.py](src/core/editor_policy.py): patch-only 工具与编辑约束
- [src/core/diff_reviewer.py](src/core/diff_reviewer.py): patch 审查与 drift 识别
- [src/core/fix_verifier.py](src/core/fix_verifier.py): 构建、rule check、quality gate、review gate
- [src/core/review_gate.py](src/core/review_gate.py): 模型化 patch 审核
- [src/agent/claude_agent.py](src/agent/claude_agent.py): issue 级运行总入口

### 工具与运行时

- [src/core/model_gateway.py](src/core/model_gateway.py): 模型网关抽象
- [src/core/claude_adapter.py](src/core/claude_adapter.py): Claude Code SDK 适配与 provider 兼容
- [src/core/agent_runtime.py](src/core/agent_runtime.py): 单次 attempt 生命周期、超时、取消、hook
- [src/core/tool_surface.py](src/core/tool_surface.py): fix runtime 内建工具面与 shell 约束
- [src/core/registry.py](src/core/registry.py): Tool registry
- [src/core/policy.py](src/core/policy.py): Tool policy 与 scoped allow rules

### 状态、事件与工件

- [src/core/state.py](src/core/state.py): `run / target / issue / attempt` 状态模型
- [src/core/events.py](src/core/events.py): `events.jsonl`
- [src/core/artifact_writer.py](src/core/artifact_writer.py): 结构化工件输出
- [src/core/state_store.py](src/core/state_store.py): artifact 优先、MySQL 可选同步

常见输出目录：

- `logs/runs/`: 整轮控制台日志
- `logs/issue_attempts/`: 单 issue attempt 日志
- `logs/run_artifacts/`: `run_summary.json`、`target_summary.json`、`events.jsonl`
- `logs/issue_artifacts/`: prompt、patch、review/build 结果、attempt/issue summary
- `logs/follow_ups/`: reviewer 识别出的后续技术债
- `logs/pr_descriptions/`: PR 详细说明本地副本

## 运行时约定

- `ISSUE_EXECUTION_MODE` 当前会被规范化为 `simple_loop`；其他值不会开启旧分支。
- `ISSUE_GUARDRAIL_MODE` 仍支持 `scope` 和 `contract_review`，默认值是 `scope`。
- 对非官方 Anthropic endpoint，当前 provider 兼容逻辑会自动走 `bare`。
- `reviewer_email` 默认回退到 `author`。
- `dingtalk_userid` 的优先级是：`targets.json.dingtalk_userid` > MySQL `author` 反查 > `unresolved`。
- 若 `.env` 未配置 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME`，钉钉 userId 数据库反查会被直接跳过，不会报错。

## 本地验证

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

## 建议下一步阅读

1. [docs/RUNBOOK.md](docs/RUNBOOK.md)
2. [PROJECT_GUIDE.md](PROJECT_GUIDE.md)
3. [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md)
4. [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md)
