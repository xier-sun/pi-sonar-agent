# 运行与配置手册

这份手册面向操作者，重点说明：

1. 环境怎么装
2. 配置怎么写
3. 入口怎么跑
4. 失败时先看哪里

如果你要理解内部架构，请看 [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)。

## 1. 运行前提

建议先确认本机满足这些条件：

- Python 3.10+
- `git` 可用
- `dotnet` 可用，且目标仓库能在本机正常构建
- 能访问 SonarQube
- 能访问 Azure DevOps 仓库和 PR API
- 已安装 Claude Code CLI，并且当前用户能正常运行 `claude --version`

## 2. 安装

### 2.1 Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果虚拟环境明显损坏，可以直接重建：

```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

### 2.2 Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

## 3. 必备配置

### 3.1 `.env`

最少需要这些变量：

- `SONARQUBE_HOST`
- `SONARQUBE_TOKEN`
- `ADO_BASE_URL`
- `ADO_PROJECT`
- `ADO_PAT`

常用补充项：

- `SONARQUBE_ORG`
- `ADO_ORG`
- `WORKSPACE_ROOT`
- `BUILD_COMMAND`
- `TEST_COMMAND`
- `SOLUTION_PATH`
- `MAX_ISSUES`
- `ISSUE_GUARDRAIL_MODE`
- `ISSUE_EXECUTION_MODE`

注意：

- `ISSUE_EXECUTION_MODE` 当前会被规范化为 `simple_loop`，其他值不会开启旧执行分支。
- `ISSUE_GUARDRAIL_MODE` 支持 `scope` 和 `contract_review`，默认值是 `scope`。

### 3.2 模型配置

模型配置建议统一放在 `.env`，不要依赖机器级环境变量。

#### Anthropic 兼容配置

优先推荐显式使用 `ANTHROPIC_API_KEY`：

```env
ANTHROPIC_BASE_URL=https://your-gateway/api/anthropic
ANTHROPIC_API_KEY=your_key
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-5
```

#### 只提供 `ANTHROPIC_AUTH_TOKEN` 的场景

```env
ANTHROPIC_BASE_URL=https://your-gateway/api/anthropic
ANTHROPIC_AUTH_TOKEN=your_token
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-5
```

当前实现的注意事项：

- 对第三方 `ANTHROPIC_BASE_URL`，自动化链路会走 Claude Code CLI 的 `bare` 模式。
- 当前第三方兼容逻辑可能把 `ANTHROPIC_AUTH_TOKEN` 桥接成 `ANTHROPIC_API_KEY`。
- 因此本地交互式 Claude Code 和自动化 bare 链路不一定完全等价。

如果你本地 `claude` 能用，但自动化日志里出现 `401 authentication_failed`：

1. 优先显式配置 `ANTHROPIC_API_KEY`
2. 核对 `ANTHROPIC_BASE_URL` 是否真的是 Anthropic-compatible endpoint
3. 再看 provider 是否接受 `x-api-key` 这套认证，而不是别的私有头

#### OpenAI 风格代理

```env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://your-gateway/api/coding/paas/v4
OPENAI_MODEL=glm-5
```

系统会自动把 `OPENAI_*` 转成 Claude SDK 可消费的兼容 `ANTHROPIC_*` 变量。

### 3.3 可选 Review Gate 配置

如果希望 patch 审核走独立模型链路，可配置：

- `PI_SONAR_REVIEW_GATE_ENABLED`
- `PI_SONAR_REVIEW_GATE_BASE_URL`
- `PI_SONAR_REVIEW_GATE_API_KEY`
- `PI_SONAR_REVIEW_GATE_AUTH_TOKEN`
- `PI_SONAR_REVIEW_GATE_MODEL`
- `PI_SONAR_REVIEW_GATE_TIMEOUT_SECONDS`

说明：

- Review gate 默认开启。
- 如果未显式配置 review gate 模型，会回退到主修复模型。
- Review gate 可以和主修复模型使用不同 provider / key。

### 3.4 `data/targets.json`

`targets.json` 的根节点必须是数组。每个元素代表一个 target，也就是“一个 Sonar 项目 + 一个 ADO 仓库 + 一个作者过滤条件”的组合。

- 零参数运行 `run.py` 时，只会读取数组中的第一个 target
- 批量运行 `python -m pi_sonar_agent.batch_runner data\targets.json` 时，会遍历整个数组

推荐示例：

```json
[
  {
    "project_key": "your_sonar_project_key",
    "repository": "your_repo",
    "author": "owner@company.com",
    "reviewer_email": "reviewer@company.com",
    "dingtalk_userid": "1234567890",
    "max_issues": 3,
    "base_branch": "main",
    "build_command": "dotnet build",
    "test_command": "dotnet test",
    "solution_path": "src/YourSolution.sln",
    "keep_workspace": false,
    "skip_build_gate": false
  }
]
```

#### 必填字段

- `project_key`
- `repository`
- `author`

#### 常用可选字段

- `reviewer_email`
- `dingtalk_userid`
- `issue_keys`
- `skip_issue_keys`
- `max_issues`
- `base_branch`
- `build_command`
- `test_command`
- `solution_path`

#### 仅批量入口生效的字段

- `keep_workspace`
- `skip_build_gate`

说明：

- 单目标入口要保留工作区时，请用 CLI 参数 `--keep-workspace`
- 单目标入口要跳过最终构建时，请用 CLI 参数 `--skip-build`
- `issue_keys` 用于“只处理这些 issue key”
- `skip_issue_keys` 用于“显式跳过这些 issue key，不做修复”
- 如果同时配置了 `issue_keys` 和 `skip_issue_keys`，执行顺序是：先按 `issue_keys` 选中候选 issue，再从候选集合中剔除 `skip_issue_keys`
- `skip_issue_keys` 只对当前 target 生效，不会影响其他 target

### 3.5 可选数据库配置

如果需要：

- MySQL 状态同步
- 按 `author` 回查 DingTalk userId

还可以在 `.env` 中配置：

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_CONNECT_TIMEOUT`

当前 userId 反查行为：

- 只有在 `.env` 同时存在 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME` 时，系统才会创建 MySQL client
- 查询 SQL 固定为：

```sql
SELECT UserId
FROM erp4.dingtalkuserdetail
WHERE Email = %s
LIMIT 1
```

- 其中 `Email = %s` 的入参是 `targets.json` 中当前 target 的 `author`
- 如果 `DB_*` 未配置，系统不会报错，而是直接跳过数据库反查，最终 `DingTalk UserId` 显示为 `unresolved`

### 3.6 可选钉钉配置

如果需要发送通知，可以配置其中一套：

- 企业应用私信：`DINGTALK_APPKEY`、`DINGTALK_APPSECRET`、`DINGTALK_AGENTID`
- 机器人 webhook：`DINGTALK_WEBHOOK`、`DINGTALK_SECRET`

当前收件人解析优先级：

1. `targets.json.dingtalk_userid`
2. MySQL `author` 反查
3. `unresolved`

如果企业应用能力可用并且解析出了 `dingtalk_userid`，系统会优先尝试私信；否则走 webhook。

### 3.7 常用运行时 / 性能开关

常见环境变量：

- `PI_SONAR_ENABLE_CONTROLLED_BASH`
- `PI_SONAR_GIT_CLONE_DEPTH`
- `PI_SONAR_REVIEW_GATE_ENABLED`
- `PI_SONAR_PERF_FAST_PATH`
- `PI_SONAR_PERF_FAST_PATH_MAX_TURNS`
- `PI_SONAR_PERF_CONTINUATION_RETRY`
- `PI_SONAR_PERF_CONTINUATION_RETRY_LIMIT`

当前默认值里几个容易关注的点：

- issue turn floor 是 `16`
- `fast_path_max_turns` 默认 `20`
- `git_clone_depth` 默认 `50`

## 4. 配置优先级

### 4.1 单目标入口

- `project_key` / `repository` / `author`: CLI > `.env` > `targets.json`
- `build_command` / `test_command` / `solution_path`: CLI > `.env` > `targets.json`
- `max_issues`: CLI > `.env` > `targets.json`
- `base_branch`: CLI `--base-branch` > `targets.json.base_branch` > 默认值 `develop`

注意：

- `base_branch` 当前不从 `.env` 读取
- `reviewer_email` / `dingtalk_userid` 当前优先取 `targets.json` 显式值

### 4.2 批量入口

批量运行按每个 target 自己的配置执行：

- `base_branch`: `target.base_branch` > 默认值 `develop`
- `max_issues`: `target.max_issues` > 默认值 `3`
- `keep_workspace` / `skip_build_gate`: 直接读取当前 target

## 5. 运行方式

### 5.1 单目标，按默认 target 运行

```powershell
.\.venv\Scripts\python.exe run.py
```

### 5.2 单目标，临时覆盖参数

```powershell
.\.venv\Scripts\python.exe run.py `
  --project-key YOUR_PROJECT `
  --repository YOUR_REPO `
  --author you@company.com `
  --base-branch main `
  --solution-path src/YourSolution.sln `
  --max-issues 1 `
  --keep-workspace
```

### 5.3 批量运行

```powershell
.\.venv\Scripts\python.exe -m pi_sonar_agent.batch_runner data\targets.json
```

### 5.4 常用调试参数

保留工作区：

```powershell
.\.venv\Scripts\python.exe run.py --keep-workspace
```

跳过最终构建验证：

```powershell
.\.venv\Scripts\python.exe run.py --skip-build
```

`--skip-build` 只建议在链路排障时使用，不建议作为常规运行方式。

## 6. 运行时会发生什么

### 6.1 启动前校验

启动阶段会统一校验：

- 模型环境
- Sonar / ADO 必填配置
- 工作区可写
- 远端 `base_branch` 存在

### 6.2 工作区准备

每个 target 会：

1. 解析 reviewer 和 DingTalk 收件人
2. 拉取指定作者的 Sonar issues
3. 清理旧工作区
4. 直接按生效 `base_branch` clone 仓库

### 6.3 单 issue 尝试

每个 issue 会：

1. 建立 issue baseline
2. 读取代码上下文
3. 生成 `IssuePlan` 和 `EditContract`
4. 组装 simple-loop prompt
5. 执行 fix agent
6. 外层统一运行 build / post-check / review gate
7. 按结果决定成功、重试或跳过

### 6.4 最终交付

所有 issue 结束后会：

1. 跑最终构建
2. 生成 PR 说明
3. 推送修复分支
4. 创建 PR
5. 添加 reviewer
6. 发送钉钉通知

## 7. 日志与工件

关键目录：

- `logs/runs/`: 整轮控制台日志
- `logs/issue_attempts/`: 单 issue attempt 日志
- `logs/run_artifacts/`: run / target summary 与 `events.jsonl`
- `logs/issue_artifacts/`: prompt、patch、review/build 结果
- `logs/follow_ups/`: incidental fix / technical debt follow-up
- `logs/pr_descriptions/`: PR 描述本地副本
- `.agent_workspaces/`: 当前工作区

建议排障顺序：

1. `logs/runs/<run_label>.log`
2. `logs/run_artifacts/<run_label>/run_summary.json`
3. `logs/run_artifacts/<run_label>/events.jsonl`
4. `logs/issue_artifacts/<repo>/<run_label>/<issue>/`
5. 保留下来的 `.agent_workspaces/`

## 8. 常见问题

### 8.1 `401 authentication_failed`

通常优先排查：

- `.env` 中的 `ANTHROPIC_BASE_URL`
- 当前 provider 是否要求 `ANTHROPIC_API_KEY`
- 当前运行是否在第三方 endpoint 下进入了 `bare` 模式
- 本地交互式 Claude Code 和自动化 bare 链路是否使用了不同认证方式

如果日志里出现：

- `mode=bare`
- `apiKeySource=ANTHROPIC_API_KEY`

优先显式配置 `ANTHROPIC_API_KEY`，不要只依赖 `ANTHROPIC_AUTH_TOKEN`。

### 8.2 `Reached maximum number of turns`

这表示子 agent 在当前 attempt 内超过了回合上限，不是 `dotnet build` 的编译错误。

当前默认 turn floor 为 `16`，部分规则 profile 会更高。遇到该问题时，先看：

- 当前规则的实际修法是否收敛
- 是否有无效工具调用或无效 Edit payload
- 是否在 fix prompt 里反复小步搜索、迟迟不落 patch

### 8.3 `DingTalk UserId: (unresolved)`

这不一定代表数据库查询报错，常见原因有三类：

1. `targets.json` 没有显式配置 `dingtalk_userid`
2. `.env` 没有配置 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME`，所以数据库回查被直接跳过
3. 数据库里 `erp4.dingtalkuserdetail` 没有匹配 `author` 邮箱的记录

### 8.4 Review Gate 一直 `retry`

优先看：

- `review_gate_result`
- `retry_context`
- `reviewer_result.json`

Review gate 的职责是拦“方向不对但能编译”的 patch，尤其是 propagation、contract drift、规则未真正消除这类问题。

### 8.5 SDK init 里工具比 request 少

request snapshot 里会声明完整 fix tool surface，但第三方 provider / CLI init trace 可能只回部分工具。

排障时请同时看：

- request snapshot 里的 `tools` / `allowed_tools`
- SDK `init` 里的 `tools`
- 是否实际出现 `No such tool available` 或 provider 兼容限制

## 9. 本地验证

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```
