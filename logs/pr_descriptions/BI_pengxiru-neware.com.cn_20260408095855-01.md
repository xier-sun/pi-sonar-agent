自动修复 SonarQube issues

## 运行概览
- 作者: pengxiru@neware.com.cn
- 基线分支: develop
- 最终构建: 通过
- 成功: 1
- 跳过: 4
- 失败: 0
- 构建命令: dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
- 解决方案: OpenAuth.Core/OpenAuth.Core.WebApi.sln
- 测试命令: None

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs

## 已修复 Issues
1. csharpsquid:S1144
   - Issue Key: 23feccf9-71f5-4aa8-851f-768eeafa7b6c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:1037
   - Sonar 问题: Remove the unused private method 'CollectAllRelatedOrderIds'.
   - 尝试次数: 3
   - 处理结果: 经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。

## 已跳过 Issues
1. csharpsquid:S1144
   - Issue Key: 4b65e0d0-6a6c-4f9e-a168-917c82abeab6
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:1082
   - Sonar 问题: Remove the unused private method 'SupplementMissingMainOrders'.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_4b65e0d0-6a6c-4f9e-a168-917c82abeab6_20260408095855-01.log
2. csharpsquid:S3776
   - Issue Key: 941f6101-03f0-4509-9ff2-6281f2e2028f
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:1619
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 45 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Build verification failed after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_941f6101-03f0-4509-9ff2-6281f2e2028f_20260408095855-01.log
3. csharpsquid:S107
   - Issue Key: b682bf4f-8fc8-4745-90f1-40ebaea7e4e2
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2108
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_b682bf4f-8fc8-4745-90f1-40ebaea7e4e2_20260408095855-01.log
4. csharpsquid:S107
   - Issue Key: c95aba2e-5991-4701-857d-5ce8226591d6
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2168
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_c95aba2e-5991-4701-857d-5ce8226591d6_20260408095855-01.log

## 失败 Issues
- 无失败 issue
