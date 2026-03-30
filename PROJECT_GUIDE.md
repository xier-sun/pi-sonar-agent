# pi-sonar-agent 项目说明

本文档描述当前仓库在 `2026-03` 这一轮迭代后的真实结构和行为，重点覆盖：

- 入口与运行模式
- 配置优先级
- issue 处理状态机
- 关键模块职责
- 日志与排障入口

## 1. 入口

当前完整工作流入口有两个：

- `python run.py`
- `pi-sonar-agent`

这两个入口都会进入 [src/main.py](src/main.py) 的主流程。

另外还有一个批量入口：

- `python -m pi_sonar_agent.batch_runner data/targets.json`

它会遍历 `targets.json` 中的多个目标，按目标逐个执行修复流程。

`src/cli.py` 仍然存在，但更偏向开发调试，不是默认生产入口。

## 2. 当前主流程

主流程在 [src/main.py](src/main.py)，执行顺序如下：

1. 加载 `.env`
2. 读取 `data/targets.json` 默认目标
3. 解析命令行参数和配置优先级
4. 获取 SonarQube issues
5. 克隆目标仓库到 `.agent_workspaces/`
6. 按 issue 逐个调用 `ClaudeFixAgent`
7. 每个 issue 修完后做本地构建验证
8. 构建失败时对当前 issue 最多重试 3 次
9. 三次仍失败时只回滚当前 issue，写日志，跳过后继续下一个
10. 所有成功保留的 issue 跑一次最终构建验证
11. 最终构建通过后推送分支并创建 PR
12. 尝试添加 reviewer，并发送钉钉通知

## 3. 配置优先级

### 3.1 目标参数

以下字段按这个顺序解析：

1. 命令行参数
2. `.env`
3. `data/targets.json` 第一个目标

适用字段：

- `project_key`
- `repository`
- `author`
- `base_branch`
- `build_command`
- `test_command`
- `solution_path`
- `max_issues`

### 3.2 reviewer / 钉钉目标

这两项当前主要来自 `targets.json`：

- `reviewer_email`
- `dingtalk_userid`

### 3.3 模型配置

模型配置使用 [src/core/model_env.py](src/core/model_env.py) 统一处理。

原则：

- `.env` 优先
- 不再依赖系统环境里的隐藏模型配置
- 仅在 `.env` 中解析显式模型名
- 对 OpenAI 风格代理做 Anthropic 风格兼容映射

显式模型字段：

- `ANTHROPIC_MODEL`
- `CLAUDE_MODEL`
- `OPENAI_MODEL`

转发给 SDK 的关键环境变量包括：

- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_CUSTOM_MODEL_OPTION`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

## 4. `targets.json` 当前职责

`data/targets.json` 是零参数运行的默认目标配置。

当前常用字段：

- `project_key`
- `repository`
- `author`
- `reviewer_email`
- `dingtalk_userid`
- `max_issues`
- `base_branch`
- `solution_path`
- `keep_workspace`

示例：

```json
[
  {
    "project_key": "Neware_BI_xxx",
    "repository": "BI",
    "author": "liyinglin@neware.com.cn",
    "reviewer_email": "pengxiru@neware.com.cn",
    "dingtalk_userid": "17556530801301497",
    "max_issues": 3,
    "base_branch": "325testai",
    "solution_path": "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
  }
]
```

## 5. issue 处理状态机

单个 issue 的状态机由 [src/core/issue_retry.py](src/core/issue_retry.py) 编排。

### 5.1 基本规则

- 每个 issue 开始前先做工作区基线快照
- 只允许回滚当前 issue 的改动
- 之前成功 issue 的改动必须保留

### 5.2 修复结果

一个 issue 可能进入三种最终状态：

- `FIXED`
- `SKIPPED`
- `FAILED`

### 5.3 构建失败时的行为

如果 issue 修改后本地构建失败：

1. 提取关键编译错误
2. 生成更聚焦的重试反馈
3. 恢复该 issue 开始前的工作区基线
4. 再尝试修复

最多尝试 3 次。

如果 3 次仍失败：

- 标记为 `SKIPPED`
- 当前 issue 改动被回滚
- 写入 `logs/issue_attempts/*.log`
- 继续下一个 issue

## 6. 修改范围控制

当前 Agent 已增加“只修 Sonar 指定位置”的约束，相关逻辑在 [src/agent/claude_agent.py](src/agent/claude_agent.py)。

### 6.1 prompt 约束

prompt 会显式告诉模型：

- 只允许修改指定行附近的问题点
- `S3776` 只允许修改目标方法
- 不要顺手修复同文件中其他相同规则问题

### 6.2 本地校验

修复后还会基于 `git diff --unified=0` 检查改动行号。

如果改到了范围外：

- 当前尝试直接判失败
- 作为 issue 级失败进入重试
- 不会把越界改动当成成功结果保留下来

## 7. 构建验证

构建相关逻辑在 [src/fixers/build_gate.py](src/fixers/build_gate.py)。

当前特性：

- Windows 下统一使用 `utf-8` + `errors="replace"`
- 自动把 `solution_path` 拼进 `dotnet build` / `dotnet test`
- 打印关键错误和日志尾部
- 避免 `stdout=None` / `stderr=None` 时再次抛 Python 异常

## 8. PR 生成

PR 相关逻辑涉及：

- [src/integrations/ado.py](src/integrations/ado.py)
- [src/core/pr_description.py](src/core/pr_description.py)

当前 PR 描述会包含：

- 运行概览
- 审阅提示
- 已修复 issues
- 已跳过 issues
- 失败 issues
- 每条 issue 的 `issue key`
- 改动文件
- 跳过原因
- issue 级日志路径

## 9. 日志与工作区

### 9.1 工作区

- `.agent_workspaces/fix_<repo>_<timestamp>/`

### 9.2 issue 级日志

- `logs/issue_attempts/<repo>_<issue_key>_<timestamp>.log`

### 9.3 日志适合排查的问题

- 模型是否反复引入相同编译错误
- issue 是在哪一次尝试失败的
- 是语法错误、类型错误还是范围越界

## 10. 关键模块

### 编排层

- [src/main.py](src/main.py): 单目标主流程
- [src/batch_runner.py](src/batch_runner.py): 多目标批处理

### Agent 层

- [src/agent/claude_agent.py](src/agent/claude_agent.py): Claude Code SDK 封装、prompt 组装、范围校验、issue 级构建验证

### Core 层

- [src/core/model_env.py](src/core/model_env.py): 模型环境变量加载与转发
- [src/core/issue_retry.py](src/core/issue_retry.py): issue 重试、回滚、日志
- [src/core/pr_description.py](src/core/pr_description.py): PR 说明生成
- [src/core/dingtalk.py](src/core/dingtalk.py): 钉钉通知

### Integration 层

- [src/integrations/sonar.py](src/integrations/sonar.py): SonarQube API
- [src/integrations/ado.py](src/integrations/ado.py): Azure DevOps API

### Fixer / Utility 层

- [src/fixers/build_gate.py](src/fixers/build_gate.py): 构建命令解析、日志提取、最终构建
- [src/sonar_mcp/tools.py](src/sonar_mcp/tools.py): MCP 工具

## 11. 测试

当前关键回归测试覆盖：

- 模型环境配置
- issue 重试与回滚
- PR 描述生成
- 构建日志格式化
- Agent 的工具策略、prompt 和范围校验

运行方式：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

## 12. 建议阅读顺序

如果你是第一次接手这个项目，建议按这个顺序阅读：

1. [README.md](README.md)
2. [docs/RUNBOOK.md](docs/RUNBOOK.md)
3. [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md)
4. [src/main.py](src/main.py)
5. [src/agent/claude_agent.py](src/agent/claude_agent.py)
6. [src/core/issue_retry.py](src/core/issue_retry.py)
