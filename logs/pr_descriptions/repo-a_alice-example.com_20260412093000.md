自动修复 SonarQube issues

## 运行概览
- 作者: alice@example.com
- 基线分支: develop
- 最终构建: 通过
- 成功: 2
- 跳过: 0
- 失败: 0
- 构建命令: dotnet build Foo.sln
- 解决方案: Foo.sln

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: src/Foo.cs

## 已修复 Issues
1. csharpsquid:S1125
   - Issue Key: issue-2
   - 状态: FIXED
   - 位置: src/Foo.cs:20
   - Sonar 问题: second
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: src/Foo.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
2. csharpsquid:S1125
   - Issue Key: issue-3
   - 状态: FIXED
   - 位置: src/Foo.cs:30
   - Sonar 问题: third
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: src/Foo.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.

## 已跳过 Issues
- 无跳过 issue

## 失败 Issues
- 无失败 issue
