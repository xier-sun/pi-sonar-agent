# pi-sonar-agent

`pi-sonar-agent` 是一个面向 SonarQube 技术债修复的自动化 Agent。
当前主流程是：

1. 从 SonarQube 拉取指定作者的 open issues
2. 克隆 Azure DevOps 仓库到临时工作区
3. 调用 Claude Code SDK 修复单个 issue
4. 对每个 issue 做本地构建校验
5. 失败时按 issue 维度重试并回滚
6. 对成功保留的改动做最终构建验证
7. 推送分支、创建 PR、添加 reviewer、发送钉钉通知

## 当前推荐入口

- `python run.py`
- `pi-sonar-agent`

这两个入口都走当前完整工作流。

`src/cli.py` 仍然保留，但更适合开发调试，不是当前推荐的生产入口。

## 文档导航

- [PROJECT_GUIDE.md](PROJECT_GUIDE.md): 项目结构、执行流程、核心模块说明
- [docs/RUNBOOK.md](docs/RUNBOOK.md): 运行与配置手册
- [docs/ENGINEERING_MEMORY.md](docs/ENGINEERING_MEMORY.md): 已踩问题、根因和解决方案记忆

## 核心能力

- `.env` 优先于系统环境变量，避免机器级 `ANTHROPIC_*` / `OPENAI_*` 污染当前项目配置
- 支持 Anthropic 兼容网关，以及 OpenAI 风格代理配置映射到 Claude SDK
- 自定义模型名会自动注册为 Claude Code custom model option
- `solution_path` 会参与 issue 级构建和最终构建，不再只在仓库根目录裸跑 `dotnet build`
- 单个 issue 修复失败时最多重试 3 次，只回滚当前 issue 的改动，不影响之前成功的 issue
- 构建失败日志会输出关键错误和日志尾部，便于定位
- PR 描述会带运行概览、issue 明细、issue key、跳过原因和重试日志
- Agent 会限制修改范围，尽量只修 SonarQube 指定的那一块代码

## 安装

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果虚拟环境损坏，直接删除 `.venv` 后重建即可。

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

## 基础配置

至少需要准备两份配置：

- `.env`
- `data/targets.json`

### `.env`

必需项通常包括：

- `SONARQUBE_HOST`
- `SONARQUBE_TOKEN`
- `ADO_BASE_URL`
- `ADO_PROJECT`
- `ADO_PAT`

模型相关建议优先写在 `.env`，不要依赖系统环境变量。

### `data/targets.json`

当前零参数运行会读取 `data/targets.json` 的第一个目标。常用字段：

- `project_key`
- `repository`
- `author`
- `reviewer_email`
- `dingtalk_userid`
- `max_issues`
- `base_branch`
- `solution_path`

## 配置优先级

### 运行目标参数

以下字段按这个优先级解析：

- 命令行参数
- `.env`
- `data/targets.json` 第一个目标

适用字段包括：

- `project_key`
- `repository`
- `author`
- `base_branch`
- `build_command`
- `test_command`
- `solution_path`
- `max_issues`

### 模型参数

模型选择只以 `.env` 为准，不再偷偷继承机器级模型设置。

支持的显式模型字段：

- `ANTHROPIC_MODEL`
- `CLAUDE_MODEL`
- `OPENAI_MODEL`

如果 `.env` 里只配了 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，系统会自动映射到当前 Claude SDK 实际使用的 `ANTHROPIC_*` 配置。

## 常用运行方式

### 按 `targets.json` 默认目标运行

```powershell
.\.venv\Scripts\python.exe run.py
```

### 覆盖默认目标

```powershell
.\.venv\Scripts\python.exe run.py `
  --project-key YOUR_PROJECT `
  --repository YOUR_REPO `
  --author you@company.com `
  --max-issues 3 `
  --base-branch develop `
  --solution-path src/YourApp.sln
```

### 保留工作区

```powershell
.\.venv\Scripts\python.exe run.py --keep-workspace
```

### 跳过最终构建验证

```powershell
.\.venv\Scripts\python.exe run.py --skip-build
```

只建议在链路排障时使用 `--skip-build`。

## 日志与产物

- `.agent_workspaces/`: 每次运行的临时工作区
- `logs/issue_attempts/`: 单 issue 重试日志

单 issue 连续 3 次构建失败后，会：

1. 回滚当前 issue 的改动
2. 记录 issue 级日志
3. 跳过该 issue
4. 继续处理下一个 issue

## 测试与检查

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

如果命令行里没有 `pytest`，优先使用 `python -m pytest`。

## 已知注意点

- 如果钉钉企业应用通知返回 `404`，优先检查 appkey/appsecret、应用类型和实际 token 接口是否匹配
- 已被 `abandoned` 的 Azure DevOps PR 不能直接更新描述，需要恢复或重新创建 PR
- `S3776` 这类认知复杂度问题允许在目标方法范围内重构，但仍会限制改动不要扩散到文件中其他同类位置
