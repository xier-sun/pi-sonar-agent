# 运行问题与解决方案记忆

这份文档记录本项目在 `2026-03` 这一轮联调过程中遇到的真实问题、根因和最终处理方式。
目标不是写变更日志，而是给后续维护者一个“遇到类似症状先看这里”的记忆库。

## 1. 虚拟环境损坏或依赖不全

### 症状

- 运行时出现缺少模块
- `pytest` / `ruff` 无法直接调用
- 已有 `.venv` 行为异常

### 根因

- 旧虚拟环境状态不一致
- 依赖未按当前项目版本重新安装

### 解决方案

- 删除 `.venv` 后重建
- 使用 `pip install -e ".[dev]"` 安装项目和开发依赖
- 在 Windows 下优先用 `.\.venv\Scripts\python.exe -m pytest`

## 2. `.env` 模型配置未生效，实际跑到了系统环境变量

### 症状

- `.env` 里配的是一套 key / base url
- 实际运行却提示另外一套模型或鉴权错误
- `claude auth status` 显示的 key 来源不是当前项目

### 根因

- SDK 子进程继承了系统环境中的 `ANTHROPIC_*`
- 代码最初没有把 `.env` 配置强制覆盖到 Agent 子进程

### 解决方案

- `.env` 使用 `override=True` 加载
- 新增 `build_agent_env()` 显式构造传给 SDK 的模型环境
- 在 `.env` 使用 `ANTHROPIC_AUTH_TOKEN` 时清空继承的 `ANTHROPIC_API_KEY`
- 模型只从 `.env` 解析，不再依赖机器级隐藏模型配置

## 3. OpenAI 风格代理无法直接驱动 Claude SDK

### 症状

- 用户提供的是 `OPENAI_API_KEY` / `OPENAI_BASE_URL`
- 代码实际走的是 Claude SDK

### 根因

- Claude SDK 当前消费的是 Anthropic 风格环境变量

### 解决方案

- 如果 `.env` 只有 `OPENAI_*`，自动映射成 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`
- 保持用户只改 `.env` 就能切换网关

## 4. 自定义模型名 `glm-4.7` 不被识别

### 症状

- 提示 `selected model (glm-4.7) may not exist`

### 根因

- Claude Code 会先校验模型名
- 第三方网关模型名需要 custom option 或 alias 映射

### 解决方案

- 对非标准模型名自动注册 `ANTHROPIC_CUSTOM_MODEL_OPTION`
- 支持用 `ANTHROPIC_DEFAULT_SONNET_MODEL` 把默认 `sonnet` 映射到网关模型

## 5. `ResultMessage` 二次异常掩盖真实错误

### 症状

- 日志里先有鉴权错误
- 随后又出现 `'ResultMessage' object has no attribute 'data'`

### 根因

- 代码按旧假设访问了 SDK 当前对象不存在的 `data` 字段

### 解决方案

- 改为从 `result` / `errors` 等真实字段提取错误
- 让原始错误能直接透出

## 6. Windows 下构建输出解码失败

### 症状

- `UnicodeDecodeError: 'gbk' codec can't decode ...`
- 后续又出现 `stdout + stderr` 的 `TypeError`

### 根因

- 子进程默认按本机编码读输出
- 解码线程异常后，`stdout` 可能变成 `None`

### 解决方案

- 统一子进程输出为 `encoding="utf-8", errors="replace"`
- 拼接输出前先做空值归一化

## 7. 最终构建在仓库根目录裸跑，找不到 `.sln`

### 症状

- `MSBUILD : error MSB1003: 请指定项目或解决方案文件`

### 根因

- 最终构建只执行 `dotnet build`
- 没有把 `solution_path` 拼到命令中

### 解决方案

- `solution_path` 同时用于 issue 级构建和最终构建
- `resolve_build_command()` 会把 `.sln` / `.csproj` 自动补到命令后

## 8. 日志里显示成功，但其实一个文件都没改

### 症状

- `Done. Cost: ...`
- 结果显示 `[OK] Fixed 0 file(s)`

### 根因

- 早期成功判定只看 Agent 是否正常退出

### 解决方案

- “无文件改动”直接视为失败
- 不再允许 `Fixed 0 file(s)` 进入成功路径

## 9. issue 改坏代码后仍被当成成功

### 症状

- 某个 issue 的 agent 日志里已经出现编译错误
- 运行摘要仍计入成功

### 根因

- 成功判定只看“改到了文件”
- 没把 issue 级构建验证失败升级成失败结果

### 解决方案

- issue 修完必须通过本地构建验证
- issue 级构建失败会触发重试，不再直接计入成功

## 10. 一个 issue 修不好会拖死整轮运行

### 症状

- 某个 issue 把工作区改坏后，后续 issue 也被污染

### 根因

- 早期没有“按 issue 粒度回滚”的机制

### 解决方案

- 每个 issue 开始前保存工作区基线
- 失败时只回滚当前 issue 的改动
- 已成功 issue 的改动保留
- 当前 issue 三次失败后跳过并继续下一个

## 11. 编译报错已经回喂模型，但信息太吵，重试质量不高

### 症状

- 模型重试时反复犯同一类错误
- issue log 里 error 行重复很多次

### 根因

- 早期只是把大段 build log 原样塞回 prompt
- 缺少去重、缺少针对性约束

### 解决方案

- 提取唯一的关键编译错误
- 附带出错文件、行列号和代码片段
- 根据错误码补约束

例如：

- `CS1963`: 不要在 IQueryable / EF 表达式树里用 `dynamic`
- `CS0246`: 不要凭空引入未定义的新类型
- `CS0103`: 不要引用当前作用域中不存在的名称

## 12. 模型会顺手修同文件里其他相同规则的问题

### 症状

- Sonar 指向一处
- Agent 却把同文件其他同类写法也一起改了

### 根因

- prompt 只强调“修这个 issue”，但没有给出明确的允许修改范围
- 本地没有做越界修改校验

### 解决方案

- prompt 中加入“允许修改范围”
- 普通问题只允许修改报错行附近小范围
- `S3776` 只允许修改目标方法，必要时只允许在该方法附近新增 helper
- 修复后用 `git diff --unified=0` 校验改动行号，越界直接判失败并重试

## 13. PR 描述过于简略，不利于审阅

### 症状

- PR 里只有非常短的说明
- reviewer 很难快速理解修了什么、跳过了什么

### 根因

- 早期 PR description 基本只有标题或简单摘要

### 解决方案

- 新增结构化 PR 描述生成器
- 包含运行概览、issue 明细、issue key、改动文件、跳过原因、issue 日志

## 14. 已创建的 PR 无法再更新描述

### 症状

- 调用更新描述接口返回 `400`

### 根因

- 目标 PR 已处于 `abandoned` 状态
- Azure DevOps 不允许编辑已废弃 PR

### 解决方案

- 先恢复 PR，或重新创建新的 PR
- 不能直接对 abandoned PR 打补丁

## 15. 钉钉企业应用通知返回 404

### 症状

- 创建 PR 成功
- 钉钉通知请求 `https://api.dingtalk.com/v1.0/robot/oAuth/token` 返回 `404`

### 当前判断

- 这是外部配置/接口匹配问题，不是主修复链路核心逻辑错误

### 建议排查

- 确认当前使用的是机器人还是企业内部应用
- 核对 `DINGTALK_APPKEY` / `DINGTALK_APPSECRET` / `DINGTALK_AGENTID`
- 确认当前应用对应的 token 接口是否就是这条 URL

## 16. 当前最重要的稳定性结论

截至这轮文档整理时，项目已经具备以下稳定行为：

- `.env` 优先生效
- 模型网关可配置
- issue 级重试与回滚可用
- 最终构建会使用 `solution_path`
- PR 描述足够详细
- 越界修改会被拦住

仍建议持续观察的点：

- 钉钉通知接口配置
- 某些复杂 `S3776` 场景下模型仍可能需要更多领域约束
