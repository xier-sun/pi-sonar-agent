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

至少需要这些变量：

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

`ISSUE_GUARDRAIL_MODE` 支持：

- `scope`
- `contract_review`

未设置时默认是 `scope`。

### 3.2 模型配置

模型配置建议统一放在 `.env`，不要依赖机器级环境变量。

#### Anthropic 兼容配置

```env
ANTHROPIC_BASE_URL=https://your-gateway/api/anthropic
ANTHROPIC_AUTH_TOKEN=your_token
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-4.7
```

#### OpenAI 风格代理

```env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://your-gateway/api/coding/paas/v4
OPENAI_MODEL=glm-4.7
```

系统会自动把 `OPENAI_*` 转成 Claude SDK 可消费的 `ANTHROPIC_*` 兼容变量。

### 3.3 `data/targets.json`

`targets.json` 的根节点必须是一个数组。每个元素代表一个 target，也就是“一个 Sonar 项目 + 一个 ADO 仓库 + 一个作者过滤条件”的组合。

- 零参数运行 `run.py` 时，只会读取数组中的第一个 target 作为默认值
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

下面 3 个字段是 target 的最小闭环，缺任意一个都会在启动时直接失败：

- `project_key`
  含义：SonarQube 项目 Key，用来拉取该项目的 open issues。
  用法：必须和 SonarQube 中项目的真实 key 一致。
  示例：`"project_key": "Neware_BI_60e40c96-7c07-478e-a859-a65a1afd6af0"`

- `repository`
  含义：Azure DevOps 仓库名，用来查询远端地址、clone 仓库、创建分支和 PR。
  用法：填 ADO 仓库名，不是本地目录名，也不是完整 URL。
  示例：`"repository": "BI"`

- `author`
  含义：Sonar issue 的作者过滤条件，只处理该作者关联的 issues。
  用法：通常填邮箱；系统会把它作为本轮 issue 拉取条件。
  示例：`"author": "pengxiru@neware.com.cn"`

#### 可选字段

- `reviewer_email`
  含义：PR 审阅人邮箱。
  用法：如果提供，创建 PR 后会尝试解析成 ADO 身份并加为 reviewer。
  默认：空字符串，表示不强制指定 reviewer。
  说明：单目标入口和批量入口都优先读取 `targets.json` 里的这个值。

- `dingtalk_userid`
  含义：钉钉接收人 userId。
  用法：如果配置了钉钉通知能力，会优先尝试把通知发给这个 userId。
  默认：空字符串，表示由数据库映射或 webhook 回退逻辑决定。
  说明：单目标入口和批量入口都优先读取 `targets.json` 里的这个值。

- `max_issues`
  含义：本轮最多处理多少个 issue。
  用法：用于限制单个 target 的处理规模，便于小批量试跑。
  示例：`"max_issues": 30`
  默认：
  单目标入口中，未配置时默认 `0`，表示不限制。
  批量入口中，未配置时默认 `3`，表示每个 target 最多处理 3 个 issue。

- `base_branch`
  含义：仓库基线分支。
  用法：系统会直接按这个分支做初始 clone、创建修复分支，并把 PR 合并目标指向它。
  示例：`"base_branch": "develop"` 或 `"base_branch": "main"`
  默认：`develop`
  说明：当前 `base_branch` 不从 `.env` 读取；单目标入口可以被 CLI `--base-branch` 覆盖。

- `build_command`
  含义：构建命令。
  用法：用于单 issue 修复后的本地构建验证，以及最终 PR 前构建验证。
  示例：`"build_command": "dotnet build"`
  默认：未配置时会回退到 `dotnet build`

- `test_command`
  含义：测试命令。
  用法：在最终构建验证阶段，如果提供了这个字段，系统会在 build 之后继续跑测试。
  示例：`"test_command": "dotnet test"`
  默认：空，表示不额外跑测试命令。

- `solution_path`
  含义：`.sln` 或 `.csproj` 的相对路径。
  用法：用于帮助 build/test 在正确的解决方案或项目范围内运行，也会进入 PR 说明和运行报告。
  示例：`"solution_path": "OpenAuth.Core/OpenAuth.Core.WebApi.sln"`
  默认：空，表示直接在仓库工作区根目录运行构建命令。

#### 仅批量入口生效的字段

下面两个字段不会进入 `TargetConfig`，而是由批量入口 [src/batch_runner.py](../src/batch_runner.py) 直接读取：

- `keep_workspace`
  含义：是否保留该 target 的工作区。
  用法：设为 `true` 时，本轮批量运行结束后保留工作区，便于人工排查和复现。
  示例：`"keep_workspace": true`
  默认：`false`
  说明：单目标入口要保留工作区时，请用 CLI 参数 `--keep-workspace`，而不是依赖 `targets.json`。

- `skip_build_gate`
  含义：是否跳过最终 build/test gate。
  用法：设为 `true` 时，该 target 在批量模式下会跳过最终构建校验。
  示例：`"skip_build_gate": true`
  默认：`false`
  说明：单目标入口要跳过最终构建校验时，请用 CLI 参数 `--skip-build`。

#### 配置建议

- 布尔值优先用 JSON 原生 `true` / `false`
- `keep_workspace`、`skip_build_gate` 即使用字符串 `"true"`、`"1"`、`"yes"`、`"on"` 目前也会被识别，但不建议依赖这个兼容行为
- 同一个仓库如果在多个 target 中重复出现，建议保持 `base_branch`、`solution_path`、`build_command` 一致，避免运行语义漂移
- 如果只是本地单次调试，优先用 `run.py` CLI 参数覆盖，不要频繁改 `targets.json`

### 3.4 可选数据库配置

如果需要：

- MySQL 状态同步
- 按 `author` 回查钉钉用户

还可以在 `.env` 中配置：

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_CONNECT_TIMEOUT`

数据库不可用时不会阻塞主流程，系统会退回到本地 artifact 和事件日志。

### 3.5 可选钉钉配置

如果需要发送通知，可以配置其中一套：

- 企业应用私信：`DINGTALK_APPKEY`、`DINGTALK_APPSECRET`、`DINGTALK_AGENTID`
- 机器人 webhook：`DINGTALK_WEBHOOK`、`DINGTALK_SECRET`

如果 target 中已经有 `dingtalk_userid`，系统会优先尝试企业应用私信；失败后再回退到 webhook。

## 4. 配置优先级

### 4.1 单目标入口

- `project_key` / `repository` / `author`: CLI > `.env` > `targets.json`
- `build_command` / `test_command` / `solution_path`: CLI > `.env` > `targets.json`
- `max_issues`: CLI > `.env` > `targets.json`
- `base_branch`: CLI `--base-branch` > `targets.json.base_branch` > 默认值 `develop`

注意：当前 `base_branch` 不从 `.env` 读取。

### 4.2 批量入口

批量运行按每个 target 自己的配置执行：

- `base_branch`: `target.base_branch` > 默认值 `develop`
- `max_issues`: `target.max_issues` > 默认值 `3`
- `keep_workspace` / `skip_build_gate`: 直接读取 target

## 5. 运行方式

### 5.1 单目标，按默认 target 运行

```powershell
.\.venv\Scripts\python.exe run.py
```

或：

```powershell
.\.venv\Scripts\pi-sonar-agent.exe
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

正式入口会先做 preflight：

- 校验模型配置能否解析
- 校验 `SONARQUBE_*`、`ADO_*` 必填变量
- 校验工作区目录可写
- 校验远端 `base_branch` 存在

如果这里失败，说明问题还没进入 issue 修复阶段。

### 6.2 单 issue

每个 issue 都会：

1. 保存 Git 工作区基线
2. 生成 `EditContract`
3. 调用模型修复
4. 运行 issue 级构建验证
5. 运行 scope/diff reviewer
6. 失败时只回滚当前 issue 的改动
7. 最多重试 3 次

### 6.3 整轮运行

整轮运行只会把“已保留下来并且最终构建通过”的改动推到分支和 PR。

## 7. 输出内容

### 7.1 日志

- `logs/runs/`: 整轮运行日志
- `logs/issue_attempts/`: issue 级重试日志

### 7.2 结构化工件

- `logs/run_artifacts/<run_label>/run_summary.json`
- `logs/run_artifacts/<run_label>/events.jsonl`
- `logs/run_artifacts/<run_label>/targets/<repo>__<author>/target_summary.json`
- `logs/issue_artifacts/<repo>/<run_label>/<issue>/attempt-xx/`

单个 attempt 目录下重点看：

- `edit_contract.json`
- `prompt_context.json`
- `patch.diff`
- `reviewer_result.json`
- `build_result.json`
- `attempt_summary.json`

### 7.3 其他输出

- `logs/follow_ups/`: reviewer 识别到的后续技术债
- `logs/pr_descriptions/`: 本地 PR 说明副本；PR 创建成功后会作为附件上传到 Azure DevOps
- `.agent_workspaces/`: 临时工作区

## 8. 常见排障顺序

建议按下面顺序排查：

1. `logs/runs/run_<timestamp>.log`
2. `logs/run_artifacts/<run_label>/run_summary.json`
3. `logs/run_artifacts/<run_label>/events.jsonl`
4. `logs/issue_artifacts/<repo>/<run_label>/<issue>/attempt-xx/build_result.json`
5. `logs/issue_artifacts/<repo>/<run_label>/<issue>/attempt-xx/reviewer_result.json`
6. `logs/issue_attempts/<repo>_<issue_key>_<timestamp>.log`
7. `.agent_workspaces/` 中保留下来的工作区

## 9. 常见问题

### 9.1 `缺少环境变量`

先检查 `.env` 是否已加载，并确认运行的是仓库内虚拟环境：

```powershell
.\.venv\Scripts\python.exe run.py
```

### 9.2 `远端基线分支不存在`

说明 `base_branch` 配错了，或该分支在远端仓库中不存在。当前系统会在 clone 前就失败。

### 9.3 `WORKSPACE_ROOT 不可写`

检查：

- `WORKSPACE_ROOT` 是否指向了受限目录
- 当前账号是否有写权限
- 是否有杀毒软件或 IDE 占用

### 9.4 `MSBUILD : error MSB1003`

通常说明没有配置 `solution_path`，或者 `build_command` 不能直接在仓库根运行。

### 9.5 模型能连通，但迟迟没有返回

先看运行日志里的 timeout 信息；当前系统已经区分：

- SDK 初始化超时
- 首响应超时
- 后续响应空闲超时
- 单 issue 总时长超时

如果是第三方网关，请优先检查其对 Claude SDK 工具调用协议的兼容性。

### 9.6 patch 被 reviewer 拒绝

说明当前改动超出了 `EditContract` 声明的文件或校验窗口。先看：

- `reviewer_result.json`
- `edit_contract.json`
- `patch.diff`

## 10. 本地验证命令

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

如果只想先小流量试跑：

```powershell
.\.venv\Scripts\python.exe run.py --max-issues 1 --keep-workspace
```

## 11. 相关文档

- [README.md](../README.md)
- [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)
- [docs/ENGINEERING_MEMORY.md](ENGINEERING_MEMORY.md)
- [docs/AGENT_REFACTOR_PLAN.md](AGENT_REFACTOR_PLAN.md)
