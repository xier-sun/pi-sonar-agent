# pi-sonar-agent

`pi-sonar-agent` 是一个面向 SonarQube 技术债修复的自动化 Agent。它会围绕“单个 issue 的最小安全修复”运行完整链路：

1. 从 SonarQube 拉取指定作者的 open issues
2. 克隆 Azure DevOps 仓库到本地工作区
3. 为单个 issue 生成编辑合同并调用 Claude Code SDK 修复
4. 对当前 issue 做本地构建校验、范围审查和规则校验
5. 失败时按 issue 粒度回滚并重试
6. 对保留下来的改动做最终构建验证
7. 推送分支、创建 PR、添加 reviewer、发送钉钉通知

## 文档导航

- [PROJECT_GUIDE.md](PROJECT_GUIDE.md): 当前项目结构、执行流程、核心模块职责
- [docs/RUNBOOK.md](docs/RUNBOOK.md): 安装、配置、运行、排障手册
- [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md): 已踩问题、根因、当前工程约定
- [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md): 已完成的重构实施记录

## 当前推荐入口

- [run.py](run.py)
- 命令行脚本 `pi-sonar-agent`
- 批量入口：`python -m pi_sonar_agent.batch_runner data/targets.json`

这三个入口都会进入当前已经收口的共享运行骨架：

- [src/main.py](src/main.py)
- [src/batch_runner.py](src/batch_runner.py)
- [src/core/run_coordinator.py](src/core/run_coordinator.py)

`src/cli.py` 仍然保留，但更适合开发调试，不是当前生产入口。

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

`python -m pip install -e ".[dev]"` 的含义是：

- `.`: 安装当前仓库这个项目
- `-e`: 以 editable 方式安装，源码改动后无需重新安装
- `.[dev]`: 同时安装开发依赖，例如 `pytest`、`ruff`

### 2. 准备配置

至少需要：

- `.env`
- `data/targets.json`

`.env` 中最少需要这些运行时配置：

- `SONARQUBE_HOST`
- `SONARQUBE_TOKEN`
- `ADO_BASE_URL`
- `ADO_PROJECT`
- `ADO_PAT`

模型配置建议统一放在 `.env`，由 [src/core/model_env.py](src/core/model_env.py) 解析。支持两种常见方式：

- Anthropic 兼容配置：`ANTHROPIC_*`
- OpenAI 风格代理：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`

如果使用 OpenAI 风格代理，系统会自动映射成 Claude SDK 需要的 `ANTHROPIC_*` 兼容变量。

`data/targets.json` 用于零参数运行或批量运行。常用字段：

- `project_key`
- `repository`
- `author`
- `reviewer_email`
- `dingtalk_userid`
- `max_issues`
- `base_branch`
- `build_command`
- `test_command`
- `solution_path`
- `keep_workspace`
- `skip_build_gate`

一个最小示例：

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

### 3. 运行

按 `data/targets.json` 第一个目标运行：

```powershell
.\.venv\Scripts\python.exe run.py
```

显式覆盖默认目标：

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

### 运行编排

- [src/core/run_coordinator.py](src/core/run_coordinator.py): 单目标共享编排器，负责 preflight、仓库准备、issue 循环、最终构建、PR、通知
- [src/core/target_config.py](src/core/target_config.py): 统一解析单目标和批量目标配置
- [src/core/preflight.py](src/core/preflight.py): 启动前校验模型环境、必填环境变量、工作区可写、远端基线分支存在
- [src/core/git_gateway.py](src/core/git_gateway.py): 统一 clone / branch / add / commit / push 语义，并对 PAT 做日志脱敏

### Agent 运行时

- [src/agent/claude_agent.py](src/agent/claude_agent.py): issue 级修复主入口，负责拼装 issue 上下文并驱动单次 attempt
- [src/core/agent_runtime.py](src/core/agent_runtime.py): 单次 attempt 的运行时循环、工具策略、超时和取消
- [src/core/model_gateway.py](src/core/model_gateway.py): 模型网关抽象
- [src/core/claude_adapter.py](src/core/claude_adapter.py): Claude Code SDK 适配层
- [src/core/resource_loader.py](src/core/resource_loader.py): 加载 [CLAUDE.md](CLAUDE.md)、仓库级 `CLAUDE.md/AGENTS.md` 和 C# 质量门禁

### 单 Issue 约束链路

- [src/core/issue_planner.py](src/core/issue_planner.py): 为单个 issue 生成 `EditContract`
- [src/core/issue_contract.py](src/core/issue_contract.py): 结构化编辑合同
- [src/core/editor_policy.py](src/core/editor_policy.py): 将编辑合同转成 patch-only 工具和 prompt 约束
- [src/core/diff_reviewer.py](src/core/diff_reviewer.py): patch 审查器，识别越界改动和顺手修
- [src/core/follow_up_store.py](src/core/follow_up_store.py): 将 incidental fix 记入 follow-up 队列，而不是混进当前 patch
- [src/core/fix_verifier.py](src/core/fix_verifier.py): issue 级构建、范围校验、diff 审查和规则校验

当前支持两种 Guardrail 模式，由 `ISSUE_GUARDRAIL_MODE` 控制：

- `scope`: 保留 legacy scope 校验
- `contract_review`: 以 `EditContract + DiffReviewer` 为主

默认值是 `scope`。

### 状态、事件与工件

- [src/core/state.py](src/core/state.py): `run / target / issue / attempt` 状态模型
- [src/core/events.py](src/core/events.py): `events.jsonl` 生命周期事件
- [src/core/artifact_writer.py](src/core/artifact_writer.py): 结构化工件输出
- [src/core/state_store.py](src/core/state_store.py): 工件优先、MySQL 可选同步的状态存储
- [src/core/retry_context.py](src/core/retry_context.py): 结构化 retry memory

核心产物目录：

- `logs/runs/`: 整轮控制台日志
- `logs/issue_attempts/`: 单 issue 重试日志
- `logs/run_artifacts/`: `run_summary.json`、`target_summary.json`、`events.jsonl`
- `logs/issue_artifacts/`: `issue.json`、`edit_contract.json`、`prompt_context.json`、`patch.diff`、`reviewer_result.json`
- `logs/follow_ups/`: reviewer 识别到的后续技术债
- `logs/pr_descriptions/`: PR 详细说明副本

## 配置优先级

### 单目标入口

- `project_key` / `repository` / `author`: CLI > `.env` > `targets.json`
- `build_command` / `test_command` / `solution_path`: CLI > `.env` > `targets.json`
- `max_issues`: CLI > `.env` > `targets.json` > 默认值
- `base_branch`: CLI `--base-branch` > `targets.json.base_branch` > 默认值 `develop`

注意：当前 `base_branch` 不从 `.env` 读取。

### 批量入口

批量运行时每个 target 直接读取 `targets.json`：

- `base_branch`: `target.base_branch` > 默认值 `develop`
- `max_issues`: `target.max_issues` > 默认值 `3`
- `keep_workspace` / `skip_build_gate`: 仅批量入口识别

### 收件人解析

- `reviewer_email`: 优先 `targets.json`
- `dingtalk_userid`: 优先 `targets.json`
- 若 `dingtalk_userid` 未配置，系统会尝试用 `.env` 里的 `DB_*` 连接 ERP4，按 `author` 反查钉钉用户

## 验证与质量门禁

本地常用验证命令：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

CI 工作流在 [.github/workflows/ci.yml](.github/workflows/ci.yml)，当前会执行：

- `python -m pip install -e ".[dev]"`
- `python -m ruff check src tests run.py`
- `python -m pytest -q`

## 当前工程约定

- 对外导入请优先使用 `pi_sonar_agent.*`
- 真实实现主要仍在 `src/core`、`src/agent`、`src/fixers`、`src/integrations`
- `src/pi_sonar_agent/*` 当前主要承担标准包入口和桥接职责
- `run.py` 已经去掉机器私有路径 fallback，可以直接作为本地入口

## 建议下一步阅读

1. [docs/RUNBOOK.md](docs/RUNBOOK.md)
2. [PROJECT_GUIDE.md](PROJECT_GUIDE.md)
3. [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md)
4. [docs/AGENT_REFACTOR_PLAN.md](docs/AGENT_REFACTOR_PLAN.md)
