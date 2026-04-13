自动修复 SonarQube issues

## 运行概览
- 作者: liyinglin@neware.com.cn
- 基线分支: develop
- 最终构建: 通过
- 成功: 2
- 跳过: 1
- 失败: 0
- 策略排除: 0
- 构建命令: dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
- 解决方案: OpenAuth.Core/OpenAuth.Core.WebApi.sln
- 测试命令: None

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs, OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs

## 已修复 Issues
1. csharpsquid:S107
   - Issue Key: f2f04149-8a3d-4915-a52a-f0098f141d5a
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs:188
   - Sonar 问题: Method has 11 parameters, which is greater than the 7 authorized.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 5 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=5
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs 主区域外变更: 159, 166, 167, 168, 263, 264, 265, 266
2. csharpsquid:S107
   - Issue Key: 91335541-7084-45c2-a0ba-0ac021ce489d
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:137
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.

## 已跳过 Issues
1. csharpsquid:S3776
   - Issue Key: a22e301e-b1e0-4a1f-9bec-712001f1a11a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:41
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 34 to the 30 allowed.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 6 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=6
   - 跳过原因: Build verification failed after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_a22e301e-b1e0-4a1f-9bec-712001f1a11a_20260412210838-01.log

## 失败 Issues
- 无失败 issue
