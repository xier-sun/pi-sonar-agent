# 运行与配置手册

本文档面向实际使用者，重点回答三个问题：

1. 怎么把环境配对
2. 怎么稳定运行
3. 出错时先看哪里

## 1. 安装

### 1.1 Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果 `.venv` 明显损坏，直接删除后重建：

```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

### 1.2 Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

## 2. 必备配置

### 2.1 `.env`

至少需要：

- `SONARQUBE_HOST`
- `SONARQUBE_TOKEN`
- `ADO_BASE_URL`
- `ADO_PROJECT`
- `ADO_PAT`

常见补充：

- `SONARQUBE_ORG`
- `BUILD_COMMAND`
- `TEST_COMMAND`
- `SOLUTION_PATH`

### 2.2 `data/targets.json`

零参数运行时会读取第一个 target。

当前推荐字段：

```json
[
  {
    "project_key": "your_sonar_project_key",
    "repository": "your_repo",
    "author": "owner@company.com",
    "reviewer_email": "reviewer@company.com",
    "dingtalk_userid": "1234567890",
    "max_issues": 3,
    "base_branch": "develop",
    "solution_path": "src/YourSolution.sln"
  }
]
```

## 3. 模型配置

当前项目优先用 `.env` 中的模型配置，不再依赖系统环境变量。

### 3.1 Anthropic 兼容网关

```env
ANTHROPIC_BASE_URL=https://your-gateway/api/anthropic
ANTHROPIC_AUTH_TOKEN=your_token
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-4.7
```

### 3.2 OpenAI 风格代理

```env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://your-gateway/api/coding/paas/v4
OPENAI_MODEL=glm-4.7
```

系统会自动把这组 `OPENAI_*` 转成当前 Claude SDK 能消费的 `ANTHROPIC_*` 兼容配置。

### 3.3 自定义模型名

如果显式模型名不是标准 `sonnet/opus/haiku` 或 Claude 官方模型名，系统会自动注册成 custom model option。

## 4. 运行方式

### 4.1 最常用

```powershell
.\.venv\Scripts\python.exe run.py
```

或：

```powershell
.\.venv\Scripts\pi-sonar-agent.exe
```

### 4.2 临时覆盖参数

```powershell
.\.venv\Scripts\python.exe run.py `
  --project-key YOUR_PROJECT `
  --repository YOUR_REPO `
  --author you@company.com `
  --max-issues 1 `
  --base-branch develop `
  --solution-path src/YourSolution.sln
```

### 4.3 多目标批量运行

```powershell
.\.venv\Scripts\python.exe -m pi_sonar_agent.batch_runner data\targets.json
```

## 5. 运行时实际行为

### 5.1 单 issue

每个 issue 都会：

1. 保存当前工作区基线
2. 调用模型修复
3. 立刻跑 issue 级构建验证
4. 构建失败则记录原因并重试
5. 三次失败后回滚当前 issue 改动并跳过

### 5.2 整体运行

整体运行只会把“最终保留下来且最终构建通过”的改动推到 PR。

### 5.3 修改范围

当前规则是：

- 普通定位型问题：只允许改报错行附近的小范围
- `S3776` 这类认知复杂度问题：只允许改 Sonar 指向的方法；必要时只能在该方法附近新增 helper
- 不允许顺手修复同文件里其他相同规则问题

## 6. 构建与测试

### 6.1 `solution_path`

如果项目不是在仓库根目录直接有 `.sln`，必须配置：

- `SOLUTION_PATH`
或
- `targets.json` 中的 `solution_path`

否则最终构建可能报：

```text
MSBUILD : error MSB1003: 请指定项目或解决方案文件
```

### 6.2 输出日志

构建失败时会打印：

- 构建命令
- 关键错误
- 构建日志尾部

如果是 issue 级失败，还会附带：

- `[ISSUE LOG] logs/issue_attempts/...`

## 7. 日志查看顺序

排障时建议按这个顺序看：

1. 终端中的 `[ISSUE BUILD LOG]`
2. `logs/issue_attempts/<repo>_<issue_key>_<timestamp>.log`
3. `.agent_workspaces/<run>/` 里保留的工作区

## 8. PR 行为

PR 创建前提：

- 至少有一个 issue 成功修复
- 最终构建通过
- 没有显式 `--skip-build`

当前 PR 描述会写明：

- issue key
- 规则
- 文件与行号
- 处理结果
- 改动文件
- 跳过原因
- issue 级日志路径

## 9. 常见命令

### 9.1 只验证文档/逻辑改动没有破坏测试

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

### 9.2 先小流量试跑

```powershell
.\.venv\Scripts\python.exe run.py --max-issues 1 --keep-workspace
```

### 9.3 保留工作区方便人工检查

```powershell
.\.venv\Scripts\python.exe run.py --keep-workspace
```

## 10. 常见误区

- 不要假设系统环境变量里的模型配置就是当前项目配置
- 不要忽略 `solution_path`
- 不要把 `--skip-build` 当成默认运行方式
- 不要把 `src/cli.py` 当成当前主入口
- 不要认为 issue 修复失败会影响之前成功 issue 的代码，当前逻辑是按 issue 维度回滚

## 11. 相关文档

- [README.md](../README.md)
- [PROJECT_GUIDE.md](../PROJECT_GUIDE.md)
- [docs/ENGINEERING_MEMORY.md](ENGINEERING_MEMORY.md)
