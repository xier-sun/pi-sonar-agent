自动修复 SonarQube issues

## 运行概览
- 作者: liyinglin@neware.com.cn
- 基线分支: develop
- 最终构建: 通过
- 成功: 2
- 跳过: 0
- 失败: 0
- 构建命令: dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
- 解决方案: OpenAuth.Core/OpenAuth.Core.WebApi.sln
- 测试命令: None

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs, OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs

## 已修复 Issues
1. csharpsquid:S3776
   - Issue Key: a22e301e-b1e0-4a1f-9bec-712001f1a11a
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:41
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 34 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
2. csharpsquid:S3776
   - Issue Key: c8c4ea2e-fd35-40f6-a859-dc9ed80884e1
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs:195
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 32 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 5 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=5

## 已跳过 Issues
- 无跳过 issue

## 失败 Issues
- 无失败 issue
